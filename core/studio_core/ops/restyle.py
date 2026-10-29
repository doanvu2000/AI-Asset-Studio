"""Restyle a project folder in place (recipe restyle-folder). op.scan_project reads the folder's PNGs and their
importer data, op.restyle_plan turns each image (or block of sprite rows) into a Flux.2 Klein edit with its own
prompt, op.restyle_fit maps every edit back onto the exact original pixels (size, alpha, sprite rects, 9-slice
stretch strips, tile seams) and scores the set, op.apply_restyle writes the PNGs (dry run by default)."""
from __future__ import annotations

import asyncio
import io
import json
import math
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult, VersionRow
from ..export import restyle as rs
from .enemy_variants import _font
from .executors import OpExecutor, _load, _put_png, _read_json

SCAN_FILE = "restyle_scan.json"
LAYOUT_FILE = "restyle_layout.json"
REPORT_FILE = "restyle.json"
APPLY_FILE = "restyle_apply.json"
EDIT_PIXELS = 1024 * 1024
THUMB = 160
THUMB_COLS, THUMB_ROWS = 6, 5
MAX_DESCRIBE = 240
PREVIEW_ROWS = 12
WHITE = 235
UNIFORM_STD = 4.0
SEAMLESS = 0.04

KIND_HINT = {
    "sprite": "game sprite",
    "sheet": "sprite sheet",
    "ui": "game UI element",
    "tile": "tileable game texture",
    "texture": "game texture",
}


def _humanize(rel: str) -> str:
    stem = Path(rel).stem
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    words = re.sub(r"[_\-.]+|\d+", " ", words).strip().lower()
    return words or "object"


def is_pixel(img: Image.Image, frame: int) -> bool:
    a = np.asarray(img.convert("RGBA"))
    vis = a[..., 3] > 0
    if not vis.any():
        return False
    px = a[vis][:, :3].astype(np.int32)
    colors = len(np.unique((px[:, 0] << 16) | (px[:, 1] << 8) | px[:, 2]))
    return colors <= 64 or (frame <= 64 and colors <= 256)


def on_white(img: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img.convert("RGBA"))
    return bg.convert("RGB")


def edit_canvas(unit: Image.Image, pixel: bool, opaque: bool, ref: Image.Image | None = None
                ) -> tuple[Image.Image, list[int]]:
    """The unit scaled to ~1 MP (integer nearest for pixel art) on white with a margin, or beside the style
    reference as a diptych; returns the canvas and the unit's box in it."""
    w, h = unit.size
    s = math.sqrt(EDIT_PIXELS / (w * h))
    if pixel:
        s = max(1, math.floor(s))
    bw, bh = max(1, round(w * s)), max(1, round(h * s))
    body = on_white(unit).resize((bw, bh), Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS)
    m = 0 if opaque else round(0.06 * max(bw, bh))
    if ref is None:
        out = Image.new("RGB", (bw + 2 * m, bh + 2 * m), (255, 255, 255))
        out.paste(body, (m, m))
        return out, [m, m, bw, bh]
    H = bh + 2 * m
    r = on_white(ref)
    rw = max(1, round(r.width * H / r.height))
    gap = max(16, H // 32)
    out = Image.new("RGB", (rw + gap + bw + 2 * m, H), (255, 255, 255))
    out.paste(r.resize((rw, H), Image.Resampling.LANCZOS), (0, 0))
    out.paste(body, (rw + gap + m, m))
    return out, [rw + gap + m, m, bw, bh]


def unbox(edit: Image.Image, canvas: tuple[int, int], box: list[int], size: tuple[int, int], pixel: bool,
          mode: str = "RGB") -> Image.Image:
    img = edit.convert(mode)
    if img.size != tuple(canvas):
        img = img.resize(tuple(canvas), Image.Resampling.LANCZOS)
    x, y, bw, bh = box
    part = img.crop((x, y, x + bw, y + bh))
    return part.resize(size, Image.Resampling.BOX if pixel else Image.Resampling.LANCZOS)


def prompt_for(item: dict[str, Any], what: str, style: str, n_frames: int, ref: bool) -> str:
    kind = item["kind"]
    look = style.strip().rstrip(".") or "a clean, consistent hand-painted game art style"
    subject = f"{what} ({KIND_HINT.get(kind, 'game art')})"
    if ref:
        head = (f"The picture has two panels. The left panel is only a style sample. Redraw the {subject} in the right "
                f"panel in exactly the art style of the left panel: the same line work, outline, shading, palette and "
                f"rendering, {look}. Do not copy anything from the left panel into the right one.")
    else:
        head = f"Redraw this {subject} in this art style: {look}."
    keep = ("Keep exactly the same subject, pose, silhouette, size, position, framing and composition; only the "
            "rendering, colours, line work and shading change.")
    if kind == "sheet":
        keep = (f"It is a sprite sheet of {n_frames} animation frames on a grid. Keep every frame exactly in its cell "
                "with the same pose, size and position, the same grid and spacing, and draw every frame consistently "
                "with the others (same colours, same details).")
    elif kind == "ui":
        keep += (" It is a 9-slice element: keep the ornaments in the four corners and the straight edges between "
                 "the corners plain and even, so it can still be stretched; no text, no letters, no icons.")
    elif kind == "tile":
        keep += " It is a seamless tile: the pattern continues across all four borders."
    tail = " Flat front view, no new objects." if kind in ("tile", "texture") else \
        " Plain pure white background, nothing added around it."
    return f"{head} {keep}{tail}"


def thumbs_sheet(items: list[tuple[int, str, Image.Image]]) -> Image.Image:
    """Numbered tiles '#index name' for one Claude vision call over many images."""
    cols = min(THUMB_COLS, max(1, len(items)))
    rows = math.ceil(len(items) / cols)
    head = 18
    out = Image.new("RGB", (cols * THUMB, rows * (THUMB + head)), (40, 41, 46))
    d = ImageDraw.Draw(out)
    f = _font(12)
    for k, (i, name, img) in enumerate(items):
        x, y = (k % cols) * THUMB, (k // cols) * (THUMB + head)
        tile = Image.new("RGBA", (THUMB - 4, THUMB - 4), (128, 128, 132, 255))
        t = img.convert("RGBA")
        t.thumbnail((THUMB - 8, THUMB - 8), Image.Resampling.LANCZOS)
        tile.alpha_composite(t, ((tile.width - t.width) // 2, (tile.height - t.height) // 2))
        out.paste(tile.convert("RGB"), (x + 2, y + head + 2))
        d.text((x + 3, y + 2), f"#{i} {name}"[:26], fill=(235, 235, 240), font=f)
    return out


def alpha_iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a > 127, b > 127
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 1.0


def defringe(rgb: np.ndarray, bad: np.ndarray, iters: int = 4) -> np.ndarray:
    """Fills `bad` pixels (editor background showing through at the silhouette) from their good neighbours."""
    rgb = rgb.astype(np.float32)
    good = ~bad
    for _ in range(iters):
        if good.all():
            break
        acc = np.zeros_like(rgb)
        cnt = np.zeros(rgb.shape[:2], np.float32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            g = np.roll(good, (dy, dx), (0, 1))
            acc += np.roll(rgb, (dy, dx), (0, 1)) * g[..., None]
            cnt += g
        fill = ~good & (cnt > 0)
        rgb[fill] = acc[fill] / cnt[fill][:, None]
        good = good | fill
    return np.clip(np.round(rgb), 0, 255).astype(np.uint8)


def _uniform(a: np.ndarray, axis: int) -> bool:
    return a.size > 0 and a.shape[axis] > 1 and float(a.astype(np.float32).std(axis=axis).mean()) < UNIFORM_STD


def keep_slices(orig: np.ndarray, new: np.ndarray, border: tuple[int, int, int, int],
                box: tuple[int, int, int, int]) -> list[str]:
    """Where the original's 9-slice stretch strips were flat along the stretch direction, flattens the restyled
    ones the same way (median profile), so stretching stays clean. Edits `new` in place; returns fixed strips."""
    l, t, r, b = border
    x0, y0, x1, y1 = box
    if not any(border) or l + r >= x1 - x0 or t + b >= y1 - y0:
        return []
    cx0, cx1, cy0, cy1 = x0 + l, x1 - r, y0 + t, y1 - b
    strips = {"top": ((y0, cy0), (cx0, cx1), 1), "bottom": ((cy1, y1), (cx0, cx1), 1),
              "left": ((cy0, cy1), (x0, cx0), 0), "right": ((cy0, cy1), (cx1, x1), 0),
              "center_x": ((cy0, cy1), (cx0, cx1), 1), "center_y": ((cy0, cy1), (cx0, cx1), 0)}
    fixed = []
    for name, ((ya, yb), (xa, xb), axis) in strips.items():
        o, n = orig[ya:yb, xa:xb], new[ya:yb, xa:xb]
        if n.size == 0 or not _uniform(o, axis):
            continue
        med = np.median(n.astype(np.float32), axis=axis, keepdims=True)
        new[ya:yb, xa:xb] = np.broadcast_to(np.round(med), n.shape).astype(new.dtype)
        fixed.append(name)
    return fixed


def seam(a: np.ndarray) -> float:
    f = a[..., :3].astype(np.float32) / 255
    return float((np.abs(f[:, 0] - f[:, -1]).mean() + np.abs(f[0] - f[-1]).mean()) / 2)


def make_seamless(a: np.ndarray) -> np.ndarray:
    """Cross-fade with the half-rolled copy: its borders are the image centre, which wraps continuously."""
    h, w = a.shape[:2]
    rolled = np.roll(a, (h // 2, w // 2), (0, 1)).astype(np.float32)
    wy = 1 - np.abs(np.linspace(-1, 1, h))
    wx = 1 - np.abs(np.linspace(-1, 1, w))
    wt = np.minimum(wy[:, None], wx[None, :])[..., None]
    wt = np.clip(wt * 3, 0, 1)
    return np.clip(np.round(a.astype(np.float32) * wt + rolled * (1 - wt)), 0, 255).astype(np.uint8)


def _bbox(m: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(m)
    return None if not len(ys) else (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def anchor_shift(a: np.ndarray, b: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> float:
    """Largest move (px) of the visible bbox's bottom-centre between two alpha masks, over the sprite rects: the
    point a pivot usually sits on."""
    worst = 0.0
    for x0, y0, x1, y1 in boxes:
        ba, bb = _bbox(a[y0:y1, x0:x1] > 127), _bbox(b[y0:y1, x0:x1] > 127)
        if ba and bb:
            worst = max(worst, math.hypot((ba[0] + ba[2] - bb[0] - bb[2]) / 2, ba[3] - bb[3]))
    return round(worst, 2)


def quantize_like(rgb: np.ndarray, colors: int) -> np.ndarray:
    img = Image.fromarray(rgb, "RGB").quantize(colors=max(16, min(256, colors)), method=Image.Quantize.MEDIANCUT,
                                                dither=Image.Dither.NONE)
    return np.asarray(img.convert("RGB"))


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()


Part = tuple[list[int], Image.Image, "Image.Image | None"]


def fit_item(src: Image.Image, src_mode: str, item: dict[str, Any], parts: list[Part], matte: str, iou_min: float,
             pixel: bool) -> tuple[Image.Image, dict[str, Any]]:
    """One restyled image: `parts` = (unit box, restyled unit RGB, matte or None) mapped back; the result has the
    original size, the original alpha unless the silhouette changed (matte), original pixels outside sprite rects,
    flattened 9-slice strips where the original's were flat and a seamless wrap for tiles that were seamless."""
    orig = np.asarray(src.convert("RGBA")).copy()
    h, w = orig.shape[:2]
    rgb = orig[..., :3].copy()
    mat = np.zeros((h, w), np.uint8)
    have_matte = False
    for (x0, y0, x1, y1), unit, mt in parts:
        rgb[y0:y1, x0:x1] = np.asarray(unit.convert("RGB"))
        if mt is not None:
            mat[y0:y1, x0:x1] = np.asarray(mt.convert("L"))
            have_matte = True
    rects = [rs._rect_of(r) for r in item.get("rects") or []]
    boxes = [(x, y, x + rw, y + rh) for x, y, rw, rh in rects] or [(0, 0, w, h)]
    inside = np.zeros((h, w), bool)
    for x0, y0, x1, y1 in boxes:
        inside[y0:y1, x0:x1] = True
    a0 = orig[..., 3]
    has_alpha = bool(item.get("alpha"))
    iou = alpha_iou(a0, mat) if have_matte and has_alpha else 1.0
    use = "original"
    if has_alpha and have_matte and (matte == "birefnet" or (matte == "auto" and iou < iou_min)):
        use = "birefnet"
    alpha = np.where(inside, mat, a0) if use == "birefnet" else a0.copy()
    if pixel:
        rgb = np.where(inside[..., None], quantize_like(rgb, color_count(orig)), orig[..., :3])
        if use == "birefnet":
            alpha = np.where(alpha > 127, 255, 0).astype(np.uint8)
    if has_alpha:
        bg = (rgb.min(-1) > WHITE) & (alpha > 0) & ~(orig[..., :3].min(-1) > WHITE) & inside
        if bg.any():
            rgb = defringe(rgb, bg)
    out = np.dstack([np.where(inside[..., None], rgb, orig[..., :3]), alpha]).astype(np.uint8)
    # Fully transparent pixels keep their original colour: engines bleed it into filtered edges.
    clear = alpha == 0
    out[clear, :3] = orig[clear, :3]
    fixed: list[str] = []
    if item["kind"] == "ui":
        borders = [(tuple(r.get("border") or (0, 0, 0, 0)), rs._rect_of(r)) for r in item.get("rects") or []
                   if any(r.get("border") or ())]
        if not borders and any(item.get("border") or ()):
            borders = [(tuple(item["border"]), (0, 0, w, h))]
        for border, (x, y, rw, rh) in borders:
            fixed += keep_slices(orig, out, border, (x, y, x + rw, y + rh))  # type: ignore[arg-type]
    seam0, seam1 = seam(orig), seam(out)
    if item["kind"] in ("tile", "texture") and not has_alpha and seam0 < SEAMLESS and seam1 > seam0 + 0.02:
        out = make_seamless(out)
        out[..., 3] = 255
        fixed.append("seamless")
    img = Image.fromarray(out, "RGBA")
    if src_mode in ("RGB", "L", "I", "I;16") and not has_alpha:
        img = img.convert("RGB")
    info = {"matte": use, "alpha_iou": round(iou, 4), "slice_fixed": fixed, "seam": [round(seam0, 4),
            round(seam(np.asarray(img.convert("RGBA"))), 4)],
            "anchor_shift": anchor_shift(a0, alpha, boxes) if use == "birefnet" else 0.0,
            "size_ok": img.size == src.size}
    return img, info


def color_count(arr: np.ndarray) -> int:
    px = arr[arr[..., 3] > 0][:, :3].astype(np.int32)
    return len(np.unique((px[:, 0] << 16) | (px[:, 1] << 8) | px[:, 2])) if len(px) else 2


def before_after(rows: list[tuple[str, Image.Image, Image.Image, str]]) -> Image.Image:
    cw = ch = 112
    gap, label = 6, 260
    out = Image.new("RGB", (label + 2 * (cw + gap) + gap, gap + len(rows) * (ch + gap)), (30, 31, 36))
    d = ImageDraw.Draw(out)
    f, fs = _font(13), _font(11)
    for i, (name, a, b, note) in enumerate(rows):
        y = gap + i * (ch + gap)
        d.text((8, y + 6), name[-34:], fill=(235, 235, 240), font=f)
        d.text((8, y + 28), note[:40], fill=(160, 160, 170), font=fs)
        for j, im in enumerate((a, b)):
            tile = Image.new("RGBA", (cw, ch), (52, 54, 62, 255))
            t = im.convert("RGBA")
            s = min(cw / t.width, ch / t.height)
            near = t.width <= cw // 2 and t.height <= ch // 2
            t = t.resize((max(1, round(t.width * s)), max(1, round(t.height * s))),
                         Image.Resampling.NEAREST if near else Image.Resampling.LANCZOS)
            tile.alpha_composite(t, ((cw - t.width) // 2, (ch - t.height) // 2))
            out.paste(tile.convert("RGB"), (label + gap + j * (cw + gap), y))
    return out


def _version(rel: str, ref: BlobRef) -> VersionRow:
    return VersionRow(id=rel, asset_name=rel, run_id="restyle", step_id="fit", blob=ref, created_at=time.time())


def set_qa(items: list[tuple[str, Image.Image, BlobRef]], ref: Image.Image | None) -> dict[str, Any]:
    """Project QA (#5) over the restyled set: palette drift against the style reference, framing, outline, light."""
    from ..engine.project_qa import evaluate, measure, palette_from_image

    small = []
    for rel, img, _ in items:
        t = img.convert("RGBA")
        t.thumbnail((384, 384), Image.Resampling.LANCZOS)
        small.append(measure(t, rel))
    palette = palette_from_image(ref, 12) if ref is not None else []
    return evaluate([_version(rel, b) for rel, _, b in items], small, palette,
                    "Ảnh style tham chiếu" if palette else "")


async def _json_in(ctx: StepContext, inputs: Outputs, port: str, required: bool = True) -> Any:
    refs = inputs.get(port) or []
    if not refs:
        if required:
            raise StepError("op_bad_input", f"missing '{port}'")
        return None
    return await _read_json(ctx, refs[0])


class ScanProject(OpExecutor):
    """Reads the folder (never writes); not cacheable since the files on disk change between runs."""
    kind = "op.scan_project"
    version = "1"

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        try:
            scan = await asyncio.to_thread(rs.scan_folder, params.get("folder"), params.get("include"),
                                           params.get("exclude"))
        except rs.RestyleError as e:
            raise StepError("op_bad_params", str(e)) from e
        items = scan["items"]
        if not items:
            raise StepError("op_no_input", "no restylable PNG in the folder", detail={"skipped": scan["skipped"][:20]})
        try:
            sample = int(params.get("sample") or 0)
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", f"sample: {e}") from e
        picked = rs.sample_items(items, sample)
        root = Path(scan["root"])
        sources: list[BlobRef] = []
        chosen = []
        for k, i in enumerate(picked):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            it = items[i]
            data = await asyncio.to_thread((root / it["rel"]).read_bytes)
            if rs.blake3(data).hexdigest() != it["png_hash"]:
                raise StepError("op_bad_input", f"{it['rel']} changed during the scan", retryable=True)
            ref = await ctx.blobs.put_bytes(data, name=Path(it["rel"]).name, mime="image/png")
            sources.append(ref)
            chosen.append({**it, "index": k, "src": ref.hash, "units": rs.units_for(it)})
            ctx.progress(k + 1, len(picked) * 2, label=it["rel"])
        thumbs: list[BlobRef] = []
        per = THUMB_COLS * THUMB_ROWS
        described = chosen[:MAX_DESCRIBE]
        for s in range(0, len(described), per):
            tiles = []
            for it in described[s:s + per]:
                img = await _load(ctx, sources[it["index"]])
                tiles.append((it["index"], Path(it["rel"]).name, img))
            sheet = await asyncio.to_thread(thumbs_sheet, tiles)
            thumbs.append(await _put_png(ctx, sheet, f"restyle_thumbs_{s // per + 1:02d}.png"))
            ctx.progress(len(picked) + s + len(tiles), len(picked) * 2, label="thumbs")
        doc = {**scan, "items": chosen, "total": len(items), "sample": sample,
               "units": sum(len(c["units"]) for c in chosen)}
        scan_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode(), name=SCAN_FILE,
                                             mime="application/json")
        ctx.progress(1.0)
        return StepResult(outputs={"sources": sources, "thumbs": thumbs, "scan": [scan_ref]},
                          meta={"engine": scan["engine"], "items": len(chosen), "total": len(items),
                                "units": doc["units"], "counts": scan["counts"], "skipped": len(scan["skipped"])})


class RestylePlan(OpExecutor):
    kind = "op.restyle_plan"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        scan = await _json_in(ctx, inputs, "scan")
        desc = await _json_in(ctx, inputs, "describe", required=False) or {}
        sources = inputs.get("sources") or []
        items = scan.get("items") or []
        if len(sources) != len(items):
            raise StepError("op_bad_input", f"{len(sources)} sources for {len(items)} scanned images")
        ref = await _load(ctx, inputs["style_ref"][0]) if inputs.get("style_ref") else None
        style = str(params.get("style") or "").strip()
        pixel_mode = str(params.get("pixel") or "auto")
        if pixel_mode not in ("auto", "on", "off"):
            raise StepError("op_bad_params", f"pixel must be auto | on | off, got {pixel_mode}")
        said = {}
        for d in (desc.get("items") if isinstance(desc, dict) else None) or []:
            try:
                said[int(d.get("id"))] = d
            except (TypeError, ValueError, AttributeError):
                continue
        images: list[BlobRef] = []
        prompts: list[str] = []
        units: list[dict[str, Any]] = []
        total = sum(len(it["units"]) for it in items)
        for it, src_ref in zip(items, sources):
            src = await _load(ctx, src_ref)
            d = said.get(int(it["index"])) or {}
            what = str(d.get("what") or "").strip() or _humanize(it["rel"])
            group = str(d.get("group") or "").strip() or what
            frame = min([min(r["w"], r["h"]) for r in it.get("rects") or []] or [min(src.size)])
            # Few colours also describes flat placeholder art: only keep it pixel art when no new style is asked for.
            pixel = pixel_mode == "on" or (pixel_mode == "auto" and ("pixel" in style.lower() or (
                not style and await asyncio.to_thread(is_pixel, src, frame))))
            it.update(what=what, group=group, pixel=pixel)
            n_frames = len(it.get("rects") or []) or 1
            for k, box in enumerate(it["units"]):
                x0, y0, x1, y1 = box
                inside = sum(1 for r in it.get("rects") or [] if x0 <= r["x"] and r["x"] + r["w"] <= x1
                             and y0 <= r["y"] and r["y"] + r["h"] <= y1) or n_frames
                canvas, tbox = await asyncio.to_thread(edit_canvas, src.crop(tuple(box)), pixel,
                                                       not it.get("alpha"), ref)
                images.append(await _put_png(ctx, canvas, f"edit_{it['index']:04d}_{k:02d}.png"))
                prompts.append(prompt_for(it, what, style, inside, ref is not None))
                units.append({"item": it["index"], "box": box, "canvas": list(canvas.size), "target": tbox,
                              "pixel": pixel})
                ctx.progress(len(units), total, label=it["rel"])
        doc = {"style": style, "style_ref": bool(ref), "prompts": prompts, "units": units,
               "items": [{k: it.get(k) for k in ("index", "rel", "what", "group", "pixel")} for it in items]}
        lay = await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode(), name=LAYOUT_FILE,
                                        mime="application/json")
        ctx.progress(1.0)
        return StepResult(outputs={"images": images, "layout": [lay]},
                          meta={"units": len(units), "groups": len({i["group"] for i in items}),
                                "style_ref": ref is not None})


class RestyleFit(OpExecutor):
    kind = "op.restyle_fit"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        scan = await _json_in(ctx, inputs, "scan")
        lay = await _json_in(ctx, inputs, "layout")
        edits, mattes = inputs.get("edits") or [], inputs.get("mattes") or []
        sources = inputs.get("sources") or []
        units = lay.get("units") or []
        if len(edits) != len(units):
            raise StepError("op_bad_input", f"{len(edits)} edits for {len(units)} edit units")
        if mattes and len(mattes) != len(units):
            raise StepError("op_bad_input", f"{len(mattes)} mattes for {len(units)} edit units")
        matte = str(params.get("matte") or "auto")
        if matte not in ("original", "auto", "birefnet"):
            raise StepError("op_bad_params", f"matte must be original | auto | birefnet, got {matte}")
        try:
            iou_min = float(params.get("iou_min", 0.9))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        ref = await _load(ctx, inputs["style_ref"][0]) if inputs.get("style_ref") else None
        info_by = {i["index"]: i for i in lay.get("items") or []}
        by_item: dict[int, list[int]] = {}
        for k, u in enumerate(units):
            by_item.setdefault(int(u["item"]), []).append(k)
        items = scan.get("items") or []
        out_imgs: list[BlobRef] = []
        report_items: list[dict[str, Any]] = []
        made: list[tuple[str, Image.Image, BlobRef]] = []
        rows: list[tuple[str, Image.Image, Image.Image, str]] = []
        for n, (it, src_ref) in enumerate(zip(items, sources)):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            src_path = ctx.blobs.path(src_ref.hash)
            with Image.open(src_path) as im:
                src_mode = im.mode
            src = await _load(ctx, src_ref)
            extra = info_by.get(it["index"]) or {}
            pixel = bool(extra.get("pixel"))
            parts = []
            for k in by_item.get(int(it["index"]), []):
                u = units[k]
                x0, y0, x1, y1 = u["box"]
                size = (x1 - x0, y1 - y0)
                e = await _load(ctx, edits[k])
                unit = await asyncio.to_thread(unbox, e, u["canvas"], u["target"], size, bool(u.get("pixel")))
                mt = None
                if mattes:
                    m = await _load(ctx, mattes[k])
                    mt = await asyncio.to_thread(unbox, m.getchannel("A"), u["canvas"], u["target"], size, False, "L")
                parts.append((u["box"], unit, mt))
            if not parts:
                raise StepError("op_bad_input", f"no edit for {it['rel']}")
            img, info = await asyncio.to_thread(fit_item, src, src_mode, it, parts, matte, iou_min, pixel)
            data = await asyncio.to_thread(_png_bytes, img)
            ref_out = await ctx.blobs.put_bytes(data, name=Path(it["rel"]).name, mime="image/png")
            out_imgs.append(ref_out)
            made.append((it["rel"], img, ref_out))
            report_items.append({**{k: v for k, v in it.items() if k not in ("units",)}, **info,
                                 "what": extra.get("what"), "group": extra.get("group"), "pixel": pixel,
                                 "new_hash": ref_out.hash, "units": len(parts)})
            note = info["matte"] + (f" · {', '.join(info['slice_fixed'])}" if info["slice_fixed"] else "")
            rows.append((it["rel"], src, img, note))
            ctx.progress(n + 1, len(items) + 1, label=it["rel"])
        qa = await asyncio.to_thread(set_qa, made, ref)
        by_rel = {a["version_id"]: a for a in qa["assets"]}
        for r in report_items:
            a = by_rel.get(r["rel"]) or {}
            r["qa"] = {"score": a.get("score"), "grade": a.get("grade"), "issues": a.get("issues") or []}
        previews = []
        for s in range(0, len(rows), PREVIEW_ROWS):
            chunk = [(n, a, b, f"{note} · QA {by_rel.get(n, {}).get('score', '-')}") for n, a, b, note in
                     rows[s:s + PREVIEW_ROWS]]
            sheet = await asyncio.to_thread(before_after, chunk)
            previews.append(await _put_png(ctx, sheet, f"restyle_before_after_{s // PREVIEW_ROWS + 1:02d}.png"))
        doc = {"type": rs.REPORT_TYPE, "version": 1, "root": scan["root"], "engine": scan["engine"],
               "project": scan["project"], "total": scan.get("total"), "sample": scan.get("sample"),
               "style": lay.get("style"), "style_ref": lay.get("style_ref"), "items": report_items,
               "qa": {"summary": qa["summary"], "reference": qa["reference"]},
               "flags": {"silhouette": [r["rel"] for r in report_items if r["matte"] == "birefnet"],
                         "low_qa": [r["rel"] for r in report_items if r["qa"]["grade"] == "bad"],
                         "size": [r["rel"] for r in report_items if not r["size_ok"]]}}
        rep = await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode(), name=REPORT_FILE,
                                        mime="application/json")
        ctx.progress(1.0)
        return StepResult(outputs={"images": out_imgs, "preview": previews, "restyle": [rep]},
                          meta={"images": len(out_imgs), "qa": qa["summary"].get("score"),
                                "silhouette": len(doc["flags"]["silhouette"])})


async def restyled_files(blobs: Any, report: dict[str, Any], refs: list[BlobRef]) -> dict[str, bytes]:
    """rel path -> PNG bytes of the restyled images that made it through the gate (matched by content hash)."""
    have = {r.hash for r in refs}
    out = {}
    for it in report.get("items") or []:
        h = it.get("new_hash")
        if h in have:
            out[it["rel"]] = await asyncio.to_thread(blobs.path(h).read_bytes)
    return out


class ApplyRestyle(OpExecutor):
    """dry_run (default) reports what would be written; mirror writes a sibling copy of the folder; apply backs up
    and overwrites in place. Side effects, so never cached."""
    kind = "op.apply_restyle"
    version = "1"

    def __init__(self, backups: Path | None = None):
        self.backups = backups

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        report = await _json_in(ctx, inputs, "restyle")
        if not isinstance(report, dict) or report.get("type") != rs.REPORT_TYPE:
            raise StepError("op_bad_input", "restyle input is not a restyle report")
        mode = str(params.get("output") or "dry_run")
        if mode == "apply" and self.backups is None:
            raise StepError("op_bad_params", "no backup folder configured; refusing to write in place")
        files = await restyled_files(ctx.blobs, report, inputs.get("images") or [])
        try:
            doc = await asyncio.to_thread(rs.apply_files, report, files, self.backups or ctx.job_dir, mode,
                                          {"run_id": ctx.run_id, "step_id": ctx.step_id, "style": report.get("style")})
        except rs.RestyleError as e:
            raise StepError("op_bad_params", str(e)) from e
        ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode(), name=APPLY_FILE,
                                        mime="application/json")
        ctx.progress(1.0)
        return StepResult(outputs={"apply": [ref]},
                          meta={"mode": mode, "written": doc["written"], "skipped": doc["skipped"],
                                "backup_id": doc.get("backup_id")})


RESTYLE_OPS: tuple[type[OpExecutor], ...] = (ScanProject, RestylePlan, RestyleFit)


def register_restyle(registry: Any, settings: Any) -> None:
    for cls in RESTYLE_OPS:
        registry.register(cls())
    data = getattr(settings, "data_dir", None)
    registry.register(ApplyRestyle(rs.backup_dir(Path(data)) if data else None))
