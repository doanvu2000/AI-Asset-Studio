"""Side-scrolling parallax background: `op.parallax_layers` turns one opaque sky plus one generation per depth layer
(cut out by BiRefNet, or keyed off the flat background) into layers of one size, each seamless on X only, hazed
toward the sky by depth, with scroll factors, a stacked + wrap-seam preview and parallax.json."""
from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png
from .tile import _closed_path, _enabled, _overlap, _quilt_x, _seam_ratio, box_blur, soft_blur

PARALLAX_FILE = "parallax.json"
PARALLAX_TYPE = "asset-studio/parallax"
GEN_SIZE = (1536, 640)
WIDTHS = (1024, 1536, 2048)
CUTOUT_MODES = ("auto", "birefnet", "key")
OVERLAP = 0.1
FG_NAMES = ("fg", "foreground", "front", "near_fg")
SKY_SCROLL = 0.05
DEFAULT_LAYERS = ("far", "mid", "near", "fg")


def layer_names(raw: Any) -> list[str]:
    lines = [ln.strip() for ln in str(raw or "").splitlines() if ln.strip()] or list(DEFAULT_LAYERS)
    out: list[str] = []
    for i, ln in enumerate(lines):
        base = re.sub(r"[^a-z0-9]+", "_", re.split(r"[,:]", ln, maxsplit=1)[0].lower()).strip("_")[:20]
        base = base if base and base != "sky" else f"layer_{i + 1:02d}"
        name, k = base, 2
        while name in out:
            name, k = f"{base}_{k}", k + 1
        out.append(name)
    return out


def scroll_factors(names: list[str], raw: Any = "auto") -> list[float]:
    """Godot scroll_scale.x per layer, sky first: 0 = glued to the camera, 1 = moves with the world, > 1 in front."""
    n = len(names)
    text = str(raw or "auto").strip().lower()
    if text != "auto":
        try:
            vals = [float(v) for v in re.split(r"[,\s;]+", text) if v]
        except ValueError as e:
            raise StepError("op_bad_params", f"scroll must be 'auto' or {n + 1} numbers: {e}") from e
        if len(vals) != n + 1 or any(not 0 <= v <= 4 for v in vals):
            raise StepError("op_bad_params", f"scroll needs {n + 1} numbers in 0..4 (sky first), got {len(vals)}")
        return [round(v, 3) for v in vals]
    fg = n >= 2 and names[-1] in FG_NAMES
    back = n - 1 if fg else n
    steps = [0.2 + 0.6 * i / max(1, back - 1) for i in range(back)] if back > 1 else [0.5] * back
    return [SKY_SCROLL, *(round(v, 3) for v in steps), *([1.25] if fg else [])]


def _f(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"), np.float32) / 255.0


def _u8(a: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(np.round(a * 255), 0, 255).astype(np.uint8), "RGBA")


def border_color(raw: np.ndarray) -> np.ndarray:
    """Flat background the layer was generated on: median of the top rows, which the prompt keeps empty."""
    h = raw.shape[0]
    return np.median(raw[: max(2, h // 20), :, :3].reshape(-1, 3), 0)


def key_alpha(raw: np.ndarray, bg: np.ndarray, lo: float = 0.06, hi: float = 0.18) -> np.ndarray:
    d = np.sqrt(((raw[..., :3] - bg) ** 2).sum(-1))
    return np.clip((d - lo) / (hi - lo), 0, 1).astype(np.float32)


def decontaminate(rgb: np.ndarray, alpha: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """Removes the background colour mixed into soft edge pixels (white fringe over a dark sky)."""
    a = alpha[..., None]
    safe = np.maximum(a, 0.05)
    out = np.where(a < 0.999, (rgb - (1 - a) * bg) / safe, rgb)
    return np.clip(out, 0, 1)


def cutout(raw: Image.Image, cut: Image.Image | None, mode: str) -> tuple[np.ndarray, str]:
    r = _f(raw)
    bg = border_color(r)
    alpha, used = None, "key"
    if cut is not None and mode != "key":
        a = _f(cut.resize(raw.size, Image.Resampling.LANCZOS))[..., 3]
        cover = float(a.mean())
        # A wide landscape band is not a "salient object": when BiRefNet keeps almost nothing or the whole frame,
        # the flat background the prompt asked for is the better matte.
        if mode == "birefnet" or 0.03 < cover < 0.97:
            alpha, used = a, "birefnet"
    if alpha is not None and mode == "auto":
        # BiRefNet keeps painted mist and backdrop pockets between trees as opaque near-white; near the backdrop
        # colour they become see-through instead.
        alpha = np.minimum(alpha, key_alpha(r, bg, 0.04, 0.16))
    if alpha is None:
        alpha = key_alpha(r, bg)
    rgb = decontaminate(r[..., :3], alpha, bg)
    return np.concatenate([rgb, alpha[..., None]], -1), used


def palette_match(a: np.ndarray, ref: np.ndarray, strength: float = 0.5) -> np.ndarray:
    """Per-channel mean/std pulled toward the reference image over the layer's visible pixels."""
    m = a[..., 3] > 0.5
    if m.sum() < 16:
        return a
    src = a[..., :3][m]
    rm, rs = ref[..., :3].reshape(-1, 3).mean(0), ref[..., :3].reshape(-1, 3).std(0) + 1e-4
    sm, ss = src.mean(0), src.std(0) + 1e-4
    moved = (a[..., :3] - sm) * (rs / ss) + rm
    out = a.copy()
    out[..., :3] = np.clip(a[..., :3] * (1 - strength) + moved * strength, 0, 1)
    return out


def ground_fill(a: np.ndarray, min_alpha: float = 0.5) -> np.ndarray:
    """Extends each column's lowest solid pixel down to the bottom edge, so a band the generator ended above the
    frame bottom does not let the layers behind (or the sky) show through underneath it."""
    solid = a[..., 3] >= min_alpha
    h = a.shape[0]
    rows = np.where(solid.any(0), h - 1 - np.argmax(solid[::-1], 0), -1)
    out = a.copy()
    y = np.arange(h)[:, None]
    below = (y > rows[None, :]) & (rows[None, :] >= 0)
    if not below.any():
        return out
    last = a[np.clip(rows, 0, h - 1), np.arange(a.shape[1])]
    out[below] = np.broadcast_to(last[None, :, :], a.shape)[below]
    out[..., 3][below] = 1.0
    return out


def clamp_height(a: np.ndarray, frac: float) -> np.ndarray:
    """Fades out everything above the lowest `frac` of the frame (soft over 8% of the height)."""
    h = a.shape[0]
    top, soft = h * (1 - frac), max(1.0, h * 0.08)
    ramp = np.clip((np.arange(h, dtype=np.float32) - (top - soft)) / soft, 0, 1)
    out = a.copy()
    out[..., 3] *= (ramp * ramp * (3 - 2 * ramp))[:, None]
    return out


def haze_amounts(n: int, haze: float, fg_last: bool) -> list[float]:
    """Farthest layer gets the most haze (atmospheric perspective), the nearest none."""
    back = n - 1 if fg_last else n
    out = [haze * 0.6 * (1 - i / max(1, back - 1)) ** 1.3 if back > 1 else 0.0 for i in range(back)]
    return out + ([0.0] if fg_last else [])


def cut_quilt_x(pm: np.ndarray, o: int, feather: int = 2) -> np.ndarray:
    """tile._quilt_x with a plain min-error cut: its low-band ramp cross-fades alpha too, which leaves a ghost
    (half-transparent mesa top, smoky trunk) wherever the two edges hold different silhouettes."""
    w = pm.shape[1]
    x, y = pm[:, w - o:], pm[:, :o]
    diff = (x - y) ** 2
    cost = box_blur(diff[..., :3].sum(-1) + 4 * diff[..., 3], 1, wrap=(False, False)) + 1e-6
    path = _closed_path(cost)
    m = (np.arange(o)[None, :] >= path[:, None]).astype(np.float32)
    if feather > 0:
        m = soft_blur(m, feather, (1,), wrap=False)
    m[:, 0], m[:, -1] = 0.0, 1.0
    return np.concatenate([m[..., None] * y + (1 - m[..., None]) * x, pm[:, o:w - o]], 1)


def seamless_x(a: np.ndarray, width: int, height: int) -> np.ndarray:
    """X-only wrap quilt on premultiplied RGBA (so transparent pixels carry no colour into the cut), resized back
    with X wrap-padding and Y edge-padding. Opaque layers keep tile.py's quilt, whose low-band ramp hides a
    brightness step in a sky gradient."""
    pm = a.copy()
    pm[..., :3] *= a[..., 3:]
    o = _overlap(a.shape[1], OVERLAP)
    q = _quilt_x(pm, OVERLAP, 3, o) if a[..., 3].min() >= 0.999 else cut_quilt_x(pm, o)
    sw, sh = q.shape[1], q.shape[0]
    mx = min(sw, math.ceil(3 * max(1.0, sw / width)) + 2)
    pad = np.pad(q, ((0, 0), (mx, mx), (0, 0)), mode="wrap")
    chans = [np.asarray(Image.fromarray(pad[..., c].astype(np.float32), "F").resize(
        (width, height), Image.Resampling.LANCZOS, box=(mx, 0, mx + sw, sh)), np.float32) for c in range(4)]
    out = np.clip(np.stack(chans, -1), 0, 1)
    # join() blurs its low band with a vertical wrap; that leaks a trace of the opaque bottom into the empty top.
    out[..., 3][out[..., 3] < 2 / 255] = 0
    al = out[..., 3:]
    out[..., :3] = np.where(al > 1e-3, out[..., :3] / np.maximum(al, 1e-3), 0)
    return np.clip(out, 0, 1)


def seam_x(a: np.ndarray) -> float:
    pm = a[..., :3] * a[..., 3:]
    # 90th percentile, not tile.py's 99th: a silhouette edge is sharp in only a few columns, unlike grout lines.
    return round(_seam_ratio(np.concatenate([pm, a[..., 3:]], -1), 90)["x"], 3)


def composite(layers: list[np.ndarray], shift: list[int] | None = None) -> Image.Image:
    out = Image.new("RGBA", (layers[0].shape[1], layers[0].shape[0]), (0, 0, 0, 255))
    for i, a in enumerate(layers):
        out.alpha_composite(_u8(np.roll(a, shift[i], 1) if shift else a))
    return out


def _checker(size: tuple[int, int], cell: int = 12) -> Image.Image:
    y, x = np.mgrid[0:size[1], 0:size[0]]
    v = np.where(((x // cell) + (y // cell)) % 2 == 0, 70, 50).astype(np.uint8)
    return Image.fromarray(np.stack([v, v, v, np.full_like(v, 255)], -1), "RGBA")


def preview(layers: list[np.ndarray], names: list[str], scrolls: list[float], seams: list[float],
            width: int = 960) -> Image.Image:
    """Stacked composite; the stack rolled by half its width so every layer's wrap seam sits under the centre mark;
    then each layer alone on a checkerboard."""
    h0, w0 = layers[0].shape[:2]
    ch = round(width * h0 / w0)
    gap, label = 8, 18
    tw = (width - gap * (len(layers) - 1)) // len(layers)
    th = round(tw * h0 / w0)
    out = Image.new("RGB", (width + 2 * gap, gap + 2 * (ch + label + gap) + th + label + gap), (24, 24, 28))
    d = ImageDraw.Draw(out)
    stacked = composite(layers).resize((width, ch), Image.Resampling.LANCZOS)
    out.paste(stacked.convert("RGB"), (gap, gap))
    d.text((gap + 2, gap + ch + 3), "stacked", fill=(230, 230, 230))
    y = gap + ch + label + gap
    seam_img = composite(layers, [w0 // 2] * len(layers))
    out.paste(seam_img.resize((width, ch), Image.Resampling.LANCZOS).convert("RGB"), (gap, y))
    d.line([(gap + width // 2, y + ch), (gap + width // 2, y + ch + 6)], fill=(255, 200, 60), width=2)
    d.text((gap + 2, y + ch + 3), "rolled 1/2: wrap seams at the centre mark", fill=(230, 230, 230))
    y += ch + label + gap
    for i, (a, n, s, sm) in enumerate(zip(layers, names, scrolls, seams)):
        x = gap + i * (tw + gap)
        cell = _checker((tw, th))
        cell.alpha_composite(_u8(a).resize((tw, th), Image.Resampling.LANCZOS))
        out.paste(cell.convert("RGB"), (x, y))
        d.text((x + 2, y + th + 3), f"{n} x{s:g} seam {sm:.2f}", fill=(200, 200, 200))
    return out


class ParallaxLayers(OpExecutor):
    kind = "op.parallax_layers"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        sky_ref = _images_in(inputs, "sky")[0]
        cuts = _images_in(inputs)
        raws = inputs.get("raw") or []
        ref_in = inputs.get("ref") or []
        names = layer_names(params.get("names"))
        if len(names) != len(cuts):
            names = [f"layer_{i + 1:02d}" for i in range(len(cuts))]
        mode = str(params.get("cutout") or "auto").lower()
        try:
            width = int(params.get("width") or GEN_SIZE[0])
            haze = float(params.get("haze", 0.5))
            pal = float(params.get("palette_strength", 0.5))
            fill = _enabled(params.get("ground_fill", True))
            fg_height = float(params.get("fg_height", 0.35))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", f"bad width/haze: {e}") from e
        if (mode not in CUTOUT_MODES or not 256 <= width <= 4096 or not 0 <= haze <= 1 or not 0 <= pal <= 1
                or not 0 <= fg_height <= 1):
            raise StepError("op_bad_params", f"cutout must be {'/'.join(CUTOUT_MODES)}, width 256..4096, "
                                             "haze, palette_strength and fg_height 0..1")
        scrolls = scroll_factors(names, params.get("scroll"))
        sky_img = await _load(ctx, sky_ref)
        height = max(16, round(width * sky_img.height / sky_img.width))
        ref = _f(await _load(ctx, ref_in[0])) if ref_in else None
        total = len(cuts) + 2
        ctx.progress(0, total, label="sky")
        sky = _f(sky_img)
        sky[..., 3] = 1.0
        if ref is not None:
            sky = palette_match(sky, ref, pal)
        sky = await asyncio.to_thread(seamless_x, sky, width, height)
        haze_rgb = sky[int(height * 0.6):, :, :3].reshape(-1, 3).mean(0)
        fg_last = len(names) >= 2 and names[-1] in FG_NAMES
        hazes = haze_amounts(len(names), haze, fg_last)
        layers, cut_used = [sky], []
        for i, cref in enumerate(cuts):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.progress(i + 1, total, label=names[i])
            cut = await _load(ctx, cref)
            raw = await _load(ctx, raws[i]) if i < len(raws) else cut.convert("RGB")
            a, used = await asyncio.to_thread(cutout, raw, cut, mode)
            if ref is not None:
                a = palette_match(a, ref, pal)
            is_fg = fg_last and i == len(cuts) - 1
            if fill and not is_fg:
                a = ground_fill(a)
            if is_fg and 0 < fg_height < 1:
                a = clamp_height(a, fg_height)
            a[..., :3] = a[..., :3] * (1 - hazes[i]) + haze_rgb * hazes[i]
            try:
                a = await asyncio.to_thread(seamless_x, a, width, height)
            except ValueError as e:
                raise StepError("op_bad_image", str(e)) from e
            cover = float(a[..., 3].mean())
            if cover < 0.02:
                ctx.log(f"layer {names[i]} is almost empty after the cutout ({cover:.1%})", "warning")
            elif is_fg and cover > 0.4:
                ctx.log(f"foreground {names[i]} covers {cover:.0%} of the frame and scrolls over gameplay", "warning")
            layers.append(a)
            cut_used.append(used)
        all_names = ["sky", *names]
        seams = [seam_x(a) for a in layers]
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", str(params.get("asset") or "parallax"))[:40] or "parallax"
        refs: list[BlobRef] = []
        entries: list[dict[str, Any]] = []
        for i, (n, a) in enumerate(zip(all_names, layers)):
            r = await _put_png(ctx, _u8(a), f"{stem}_{i}_{n}.png")
            refs.append(r)
            entries.append({"name": n, "file": r.name, "hash": r.hash, "scroll": scrolls[i], "depth": i,
                            "opaque": i == 0, "foreground": fg_last and i == len(layers) - 1,
                            "coverage": round(float(a[..., 3].mean()), 3), "seam_x": seams[i],
                            **({"cutout": cut_used[i - 1], "haze": round(hazes[i - 1], 3)} if i else {})})
        ctx.progress(total - 1, total, label="preview")
        prev = await asyncio.to_thread(preview, layers, all_names, scrolls, seams)
        p_ref = await _put_png(ctx, prev, f"{stem}_parallax_preview.png")
        doc = {"type": PARALLAX_TYPE, "size": [width, height], "axis": "x", "layers": entries,
               "scroll_note": "Godot Parallax2D.scroll_scale.x: 0 = fixed to camera, 1 = world speed, >1 in front"}
        pj = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=PARALLAX_FILE,
                                       mime="application/json")
        bad = [e["name"] for e in entries if e["seam_x"] > 1.2]
        if bad:
            ctx.log(f"visible wrap seam on: {', '.join(bad)}", "warning")
        ctx.progress(total, total)
        return StepResult(outputs={"images": refs, "preview": [p_ref], "parallax": [pj]},
                          meta={"layers": all_names, "size": [width, height], "scroll": scrolls,
                                "seam_x": dict(zip(all_names, seams)), "cutout": cut_used})


PARALLAX_OPS: tuple[type[OpExecutor], ...] = (ParallaxLayers,)
