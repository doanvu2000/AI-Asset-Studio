"""Image → RGBA layers (Qwen-Image-Layered): `op.layers_prep` fits the image onto the model grid, `op.image_layers`
maps the model's layers back onto the original canvas (background plate detected and dropped, edges cleaned,
back-to-front order, coverage, recomposition error), `op.layers_export` names them and writes PNGs, a layered PSD,
layers.json and a contact sheet."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png
from .keyart import grid_size
from .psd import write_psd

LAYERS_FILE = "layers.json"
LAYERS_TYPE = "asset-studio/image-layers"
INFO_FILE = "layers_info.json"
CROP_FILE = "layers_crop.json"
PSD_MIME = "application/x-photoshop"
RESOLUTIONS = (640, 1024)
GRID = 32
MAX_ASPECT = 2.0
BG_MIN_COVER = 0.95
BG_PLAIN_STD = 0.05
OCCLUSION_MIN_PX = 64
OCCLUSION_MARGIN = 0.02
BACKGROUND_MODES = ("auto", "drop", "keep")
ORDER_MODES = ("auto", "model")


def _f(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"), np.float32) / 255


def _u8(a: np.ndarray) -> np.ndarray:
    return np.round(np.clip(a, 0, 1) * 255).astype(np.uint8)


def over_white(a: np.ndarray) -> np.ndarray:
    al = a[..., 3:4]
    return a[..., :3] * al + (1 - al)


def fit_to_grid(img: Image.Image, resolution: int) -> tuple[Image.Image, dict[str, Any]]:
    """Area ≈ resolution², sides on the 32 px grid, aspect clamped to 1:2..2:1; the image is scaled inside it
    unchanged in aspect and padded with white (the model's own plate colour)."""
    w, h = img.size
    aspect = min(max(w / h, 1 / MAX_ASPECT), MAX_ASPECT)
    gw, gh = grid_size(round(aspect * 1024), 1024, resolution * resolution, GRID)
    s = min(gw / w, gh / h)
    cw, ch = max(1, min(gw, round(w * s))), max(1, min(gh, round(h * s)))
    x, y = (gw - cw) // 2, (gh - ch) // 2
    flat = Image.new("RGBA", img.size, (255, 255, 255, 255))
    flat.alpha_composite(img.convert("RGBA"))
    canvas = Image.new("RGB", (gw, gh), (255, 255, 255))
    canvas.paste(flat.convert("RGB").resize((cw, ch), Image.Resampling.LANCZOS), (x, y))
    return canvas, {"grid": [gw, gh], "box": [x, y, cw, ch], "source": [w, h], "resolution": resolution}


def crop_back(a: np.ndarray, crop: dict[str, Any]) -> np.ndarray:
    """Model-grid RGBA → original canvas. Resampled premultiplied so transparent pixels' colour cannot bleed into
    the edges."""
    gw, gh = crop["grid"]
    if a.shape[:2] != (gh, gw):
        a = _resize_rgba(a, (gw, gh))
    x, y, cw, ch = crop["box"]
    return _resize_rgba(a[y:y + ch, x:x + cw], tuple(crop["source"]))


def _resize_rgba(a: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    if (a.shape[1], a.shape[0]) == tuple(size):
        return a.copy()
    pre = np.concatenate([a[..., :3] * a[..., 3:4], a[..., 3:4]], -1)
    chans = [np.asarray(Image.fromarray(pre[..., c], "F").resize(size, Image.Resampling.LANCZOS)) for c in range(4)]
    out = np.clip(np.stack(chans, -1), 0, 1)
    al = out[..., 3:4]
    out[..., :3] = np.where(al > 1e-4, out[..., :3] / np.maximum(al, 1e-4), 0)
    return np.clip(out, 0, 1)


def detect_background(layers: list[np.ndarray], box: tuple[int, int, int, int] | None = None
                      ) -> tuple[int | None, bool]:
    """(index, plain) of the layer that covers (almost) the whole content box; plain = one flat colour."""
    best, best_cov = None, BG_MIN_COVER
    for i, a in enumerate(layers):
        al = a[..., 3] if box is None else a[box[1]:box[1] + box[3], box[0]:box[0] + box[2], 3]
        cov = float((al > 0.5).mean()) if al.size else 0.0
        if cov >= best_cov + (1e-6 if best is not None else 0):
            best, best_cov = i, cov
    if best is None:
        return None, False
    a = layers[best]
    rgb = a[..., :3][a[..., 3] > 0.5]
    return best, bool(rgb.size and float(rgb.std(0).max()) < BG_PLAIN_STD)


def alpha_clean(a: np.ndarray, floor: float = 6 / 255, bleed: int = 4) -> np.ndarray:
    """Drops near-zero alpha noise and spreads edge colour into the transparent pixels around it, so engines that
    filter or mip the texture do not pull a white/black fringe into the silhouette."""
    out = a.copy()
    al = out[..., 3]
    al[al <= floor] = 0
    known = al > 0
    rgb = out[..., :3] * known[..., None]
    weight = known.astype(np.float32)
    for _ in range(bleed):
        if known.all():
            break
        acc, cnt = np.zeros_like(rgb), np.zeros_like(weight)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            acc += np.roll(rgb * weight[..., None], (dy, dx), (0, 1))
            cnt += np.roll(weight, (dy, dx), (0, 1))
        grow = ~known & (cnt > 0)
        rgb[grow] = acc[grow] / cnt[grow][:, None]
        weight[grow] = 1
        known = known | grow
    if known.any() and not known.all():
        rgb[~known] = rgb[known].mean(0)
    out[..., :3] = np.where(al[..., None] > 0, out[..., :3], rgb)
    return out


def composite(layers: list[np.ndarray]) -> np.ndarray:
    """Straight-alpha 'over', bottom first."""
    h, w = layers[0].shape[:2]
    rgb, al = np.zeros((h, w, 3), np.float32), np.zeros((h, w, 1), np.float32)
    for a in layers:
        la = a[..., 3:4]
        rgb = a[..., :3] * la + rgb * (1 - la)
        al = la + al * (1 - la)
    return np.concatenate([np.where(al > 0, rgb / np.maximum(al, 1e-6), 0), al], -1)


def recompose_error(layers: list[np.ndarray], source: np.ndarray) -> float:
    """Mean absolute RGB difference (0..255) between the layers flattened on white and the input on white."""
    if not layers:
        return round(float(np.abs(1 - over_white(source)).mean() * 255), 2)
    return round(float(np.abs(over_white(composite(layers)) - over_white(source)).mean() * 255), 2)


def occlusion_order(layers: list[np.ndarray], source: np.ndarray) -> list[int]:
    """Back-to-front order. Where two layers both cover a pixel, the one that matches the input there is in front;
    the model's own order breaks ties and cycles."""
    n = len(layers)
    src = source[..., :3]
    behind: dict[int, set[int]] = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            both = (layers[i][..., 3] > 0.5) & (layers[j][..., 3] > 0.5)
            if int(both.sum()) < OCCLUSION_MIN_PX:
                continue
            ei = float(np.abs(layers[i][..., :3][both] - src[both]).mean())
            ej = float(np.abs(layers[j][..., :3][both] - src[both]).mean())
            if ei + OCCLUSION_MARGIN < ej:
                behind[i].add(j)
            elif ej + OCCLUSION_MARGIN < ei:
                behind[j].add(i)
    order: list[int] = []
    left = list(range(n))
    while left:
        ready = [k for k in left if not (behind[k] & set(left))]
        pick = ready[0] if ready else left[0]
        order.append(pick)
        left.remove(pick)
    return order


def alpha_bbox(a: np.ndarray) -> list[int]:
    ys, xs = np.nonzero(a[..., 3] > 1 / 255)
    if not len(ys):
        return [0, 0, 0, 0]
    return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]


def coverage(a: np.ndarray) -> float:
    return round(float((a[..., 3] > 0.5).mean()), 4)


def _checker(size: tuple[int, int], cell: int = 12) -> Image.Image:
    w, h = size
    y, x = np.mgrid[0:h, 0:w]
    v = np.where(((x // cell) + (y // cell)) % 2 == 0, 236, 204).astype(np.uint8)
    return Image.fromarray(np.stack([v, v, v, np.full_like(v, 255)], -1), "RGBA")


def contact_sheet(tiles: list[tuple[str, np.ndarray]], tile: int = 320, footer: str = "") -> Image.Image:
    """Each RGBA tile on a checkerboard with its caption under it, one row."""
    if not tiles:
        raise StepError("op_no_input", "no layers to show")
    h0, w0 = tiles[0][1].shape[:2]
    s = tile / max(w0, h0)
    tw, th = max(1, round(w0 * s)), max(1, round(h0 * s))
    cap = 22
    sheet = Image.new("RGB", (len(tiles) * (tw + 8) + 8, th + cap + 16 + (18 if footer else 0)), (32, 34, 38))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(15)
    for k, (label, a) in enumerate(tiles):
        im = _checker((tw, th))
        im.alpha_composite(Image.fromarray(_u8(a), "RGBA").resize((tw, th), Image.Resampling.LANCZOS))
        x = 8 + k * (tw + 8)
        sheet.paste(im.convert("RGB"), (x, 8))
        draw.text((x + 2, th + 12), label[:40], fill=(235, 235, 235), font=font)
    if footer:
        draw.text((8, th + cap + 12), footer, fill=(190, 200, 120), font=font)
    return sheet


def _choice(params: dict[str, Any], key: str, options: tuple[str, ...]) -> str:
    v = str(params.get(key) or options[0]).lower()
    if v not in options:
        raise StepError("op_bad_params", f"{key} must be one of {'/'.join(options)}, got {v!r}")
    return v


def _flag(v: Any, default: bool = True) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() not in ("false", "0", "no", "off", "")


async def _json_in(ctx: StepContext, inputs: Outputs, name: str, required: bool = True) -> dict[str, Any] | None:
    refs = inputs.get(name) or []
    if not refs:
        if required:
            raise StepError("op_no_input", f"input '{name}' is empty")
        return None
    try:
        doc = json.loads(await asyncio.to_thread(ctx.blobs.path(refs[0].hash).read_bytes))
    except (OSError, ValueError) as e:
        raise StepError("op_bad_input", f"input '{name}' is not JSON: {e}") from e
    if not isinstance(doc, dict):
        raise StepError("op_bad_input", f"input '{name}' must be a JSON object")
    return doc


async def _put_json(ctx: StepContext, doc: Any, name: str) -> BlobRef:
    return await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode("utf-8"), name=name,
                                     mime="application/json")


class LayersPrep(OpExecutor):
    kind = "op.layers_prep"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        try:
            res = int(params.get("resolution") or 1024)
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", f"bad resolution: {e}") from e
        if res not in RESOLUTIONS:
            raise StepError("op_bad_params", f"resolution must be one of {RESOLUTIONS}, got {res}")
        img = await _load(ctx, _images_in(inputs)[0])
        canvas, crop = await asyncio.to_thread(fit_to_grid, img, res)
        ref = await _put_png(ctx, canvas, "layers_input.png")
        doc = {**crop, "max_side": max(crop["grid"]), "hash": ref.hash}
        return StepResult(outputs={"image": [ref], "json": [await _put_json(ctx, doc, CROP_FILE)]},
                          meta={"grid": crop["grid"], "box": crop["box"], "source": crop["source"]})


class ImageLayers(OpExecutor):
    kind = "op.image_layers"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        bg_mode = _choice(params, "background", BACKGROUND_MODES)
        order_mode = _choice(params, "order", ORDER_MODES)
        try:
            min_cov = float(params.get("min_coverage", 0.002))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", f"bad min_coverage: {e}") from e
        clean = _flag(params.get("clean"))
        refs = _images_in(inputs)
        source = _f(await _load(ctx, _images_in(inputs, "source")[0]))
        crop = await _json_in(ctx, inputs, "crop")
        assert crop is not None
        try:
            if list(crop["source"]) != [source.shape[1], source.shape[0]]:
                raise StepError("op_bad_input", f"crop was made for {crop['source']}, source is "
                                                f"{source.shape[1]}x{source.shape[0]}")
            box = tuple(int(v) for v in crop["box"])
        except (KeyError, TypeError, ValueError) as e:
            raise StepError("op_bad_input", f"bad crop json: {e}") from e
        total = len(refs) + 3
        grid = []
        for i, r in enumerate(refs):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.progress(i, total, label="load")
            grid.append(_f(await _load(ctx, r)))
        bg, plain = detect_background(grid, box)  # type: ignore[arg-type]
        full = [await asyncio.to_thread(crop_back, a, crop) for a in grid]
        drop_bg = bg is not None and (bg_mode == "drop" or (bg_mode == "auto" and plain))
        dropped: list[dict[str, Any]] = []
        kept: list[int] = []
        for i, a in enumerate(full):
            if i == bg and drop_bg:
                dropped.append({"source_index": i, "reason": "background"})
                continue
            cov = coverage(a)
            if i != bg and cov < min_cov:
                dropped.append({"source_index": i, "reason": "empty", "coverage": cov})
                continue
            kept.append(i)
        if not kept:
            raise StepError("op_no_layers", "every layer was background or empty", detail={"dropped": dropped})
        if clean:
            for i in kept:
                if i != bg:
                    full[i] = await asyncio.to_thread(alpha_clean, full[i])
        if order_mode == "auto":
            movable = [i for i in kept if i != bg]
            order = [movable[k] for k in await asyncio.to_thread(occlusion_order, [full[i] for i in movable], source)]
            order = ([bg] if bg in kept else []) + order
        else:
            order = kept
        ctx.progress(len(refs) + 1, total, label="metrics")
        plate = [full[bg]] if bg is not None else []
        err_all = await asyncio.to_thread(recompose_error, plate + [full[i] for i in order if i != bg], source)
        err_kept = await asyncio.to_thread(recompose_error, [full[i] for i in order], source)
        layer_refs: list[BlobRef] = []
        entries: list[dict[str, Any]] = []
        n = 0
        for i in order:
            n += i != bg
            name = "background" if i == bg else f"layer_{n:02d}"
            r = await _put_png(ctx, Image.fromarray(_u8(full[i]), "RGBA"), f"{name}.png")
            layer_refs.append(r)
            entries.append({"name": name, "hash": r.hash, "source_index": i, "bbox": alpha_bbox(full[i]),
                            "coverage": coverage(full[i]), "background": i == bg})
        tiles = [(f"{k + 1}", full[i]) for k, i in enumerate(order)]
        sheet = await asyncio.to_thread(contact_sheet, tiles)
        sheet_ref = await _put_png(ctx, sheet, "layers_numbered.png")
        metrics = {"recompose_mae": err_all, "recompose_mae_kept": err_kept,
                   "unit": "mean |RGB| difference 0-255, flattened on white vs the input on white"}
        doc = {"type": f"{LAYERS_TYPE}/info", "size": crop["source"], "grid": crop["grid"],
               "resolution": crop.get("resolution"), "background": {"index": bg, "plain": plain, "dropped": drop_bg},
               "model_layers": len(refs), "dropped": dropped, "order": order_mode, "metrics": metrics,
               "layers": entries}
        if err_all > 12:
            ctx.log(f"layers recompose poorly ({err_all}/255 mean difference to the input)", "warning")
        if bg is None:
            ctx.log("no background plate found; every layer kept", "warning")
        ctx.progress(total, total)
        return StepResult(outputs={"images": layer_refs, "sheet": [sheet_ref],
                                   "info": [await _put_json(ctx, doc, INFO_FILE)]},
                          meta={"layers": len(entries), "background": bg, "plain_background": plain,
                                "dropped": len(dropped), **{k: v for k, v in metrics.items() if k != "unit"}})


def layer_names(raw: Any, count: int) -> list[str]:
    """Names from Claude (or the user), as file-safe snake_case, unique; anything missing falls back to layer_NN."""
    items = raw if isinstance(raw, list) else []
    out: list[str] = []
    for k in range(count):
        base = re.sub(r"[^a-z0-9]+", "_", str(items[k]).lower()).strip("_")[:32] if k < len(items) else ""
        base = base or f"layer_{k + 1:02d}"
        name, n = base, 2
        while name in out:
            name, n = f"{base}_{n}", n + 1
        out.append(name)
    return out


class LayersExport(OpExecutor):
    kind = "op.layers_export"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        info = await _json_in(ctx, inputs, "info")
        assert info is not None
        named = await _json_in(ctx, inputs, "names", required=False) or {}
        entries = list(info.get("layers") or [])
        if len(entries) != len(refs):
            raise StepError("op_bad_input", f"info lists {len(entries)} layers, got {len(refs)} images")
        given = named.get("names") if isinstance(named.get("names"), list) else []
        names = layer_names([given[k] if k < len(given) and str(given[k]).strip() else e.get("name")
                             for k, e in enumerate(entries)], len(refs))
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", str(params.get("asset") or "layers"))[:40].strip("_") or "layers"
        total = len(refs) + 2
        arrays: list[np.ndarray] = []
        out_refs: list[BlobRef] = []
        doc_layers: list[dict[str, Any]] = []
        for k, (r, e, name) in enumerate(zip(refs, entries, names)):
            ctx.progress(k, total, label=name)
            img = await _load(ctx, r)
            a = np.asarray(img.convert("RGBA"), np.uint8)
            arrays.append(a)
            fname = f"{stem}_{k + 1:02d}_{name}.png"
            ref = await _put_png(ctx, img, fname)
            out_refs.append(ref)
            doc_layers.append({"name": name, "file": fname, "hash": ref.hash, "order": k,
                               "bbox": e.get("bbox"), "coverage": e.get("coverage"),
                               "background": bool(e.get("background")), "source_index": e.get("source_index")})
        ctx.progress(len(refs), total, label="psd")
        psd_bytes = await asyncio.to_thread(write_psd, list(zip(names, arrays)))
        psd_ref = await ctx.blobs.put_bytes(psd_bytes, name=f"{stem}.psd", mime=PSD_MIME)
        metrics = info.get("metrics") or {}
        comp = composite([a.astype(np.float32) / 255 for a in arrays])
        tiles = [(f"{k + 1} {n}", a.astype(np.float32) / 255) for k, (n, a) in enumerate(zip(names, arrays))]
        tiles.append(("= composite", comp))
        footer = (f"recompose error {metrics.get('recompose_mae', '?')}/255 (all model layers), "
                  f"{metrics.get('recompose_mae_kept', '?')}/255 (kept, on white)")
        sheet = await asyncio.to_thread(contact_sheet, tiles, 320, footer)
        prev_ref = await _put_png(ctx, sheet, f"{stem}_layers_preview.png")
        h, w = arrays[0].shape[:2]
        doc = {"type": LAYERS_TYPE, "size": [w, h], "order": "back_to_front", "psd": psd_ref.name,
               "background": info.get("background"), "resolution": info.get("resolution"),
               "model_layers": info.get("model_layers"), "dropped": info.get("dropped"), "metrics": metrics,
               "names_source": named.get("source") or "default", "layers": doc_layers}
        ctx.progress(total, total)
        return StepResult(outputs={"images": out_refs, "psd": [psd_ref], "layers": [await _put_json(ctx, doc, LAYERS_FILE)],
                                   "preview": [prev_ref]},
                          meta={"names": names, "psd_bytes": len(psd_bytes),
                                **{k: v for k, v in metrics.items() if k != "unit"}})


IMAGE_LAYERS_OPS: tuple[type[OpExecutor], ...] = (LayersPrep, ImageLayers, LayersExport)
