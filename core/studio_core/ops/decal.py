"""`op.decal_pack`: decals drawn on a plain light background -> RGBA albedo + normal map per decal and decal.json.

Alpha comes from keying against the background colour (thin cracks, dirt, scorch and moss survive it, where a
salient-object cutter keeps only blobs) or from the BiRefNet cutout for solid shapes like logos and stickers. The
colour is un-premultiplied against the key, and the alpha fades out near the image border so a projected decal
never shows the square edge of its texture."""
from __future__ import annotations

import asyncio
import json
import zlib
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png
from .pbr import PbrParams, normal_map
from .tile import _enabled, _lum, soft_blur
from .trim import slug

DECAL_FILE = "decal.json"
DECAL_TYPE = "asset-studio/decal-pack"
ALPHA_MODES = ("key", "birefnet")
SIZES = (256, 512, 1024)


def _smooth(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0, 1)
    return (t * t * (3 - 2 * t)).astype(np.float32)


def _ring_bg(rgb: np.ndarray) -> np.ndarray:
    """Median of the outer 3% ring."""
    h, w = rgb.shape[:2]
    b = max(2, round(min(h, w) * 0.03))
    ring = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3), rgb[:, :b].reshape(-1, 3),
                           rgb[:, -b:].reshape(-1, 3)])
    return np.median(ring, 0).astype(np.float32)


def key_alpha(rgb: np.ndarray, lo: float = 0.04, hi: float = 0.22) -> tuple[np.ndarray, np.ndarray]:
    """-> (alpha, background colour)."""
    bg = _ring_bg(rgb)
    return _smooth((np.abs(rgb - bg).max(-1) - lo) / (hi - lo)), bg


SPECK_MASS = 0.006


def ink_key(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Cracks and scratches: -> (alpha, colour, background) or None when nothing is darker than the wall.
    Only what is darker than the wall counts (the lit lip drawn beside a gap would otherwise come out as a white
    line), mid-grey branches are matted against the darkest ink instead of the key ramp, which left them light grey
    over a dark wall, and the alpha ramp is steep so faint branches still read."""
    bg = _ring_bg(rgb)
    dark = (bg - rgb).max(-1)
    marks = dark[dark > 0.08]
    if marks.size < 16:
        return None
    ink = max(float(np.percentile(marks, 95)), 0.15)
    lin = np.clip(dark / ink, 0, 1)
    alpha = _smooth((lin - 0.06) / 0.3)
    # Lone specks (grain, dust the model sprinkles) have little mark mass around them; a crack runs on through.
    mass = soft_blur(alpha, max(2, round(min(alpha.shape) * 0.02)), wrap=False)
    return alpha * _smooth((mass - SPECK_MASS) / SPECK_MASS), unpremultiply(rgb, lin, bg), bg


ORGANIC = ("dirt", "stain", "scorch", "burn", "soot", "mud", "grime", "moss", "splat", "blood", "rust", "leak",
           "puddle", "oil", "ash", "char", "smudge", "spill", "mold", "mould", "lichen", "dust", "wet")
IRREGULAR_AUTO = 0.6
# 4πA/P² on the pixel outline: a filled disc scores ~0.9 here, organic splotches stay under ~0.5.
ROUND_LIMIT = 0.7


def _noise(shape: tuple[int, int], freq: int, rng: np.random.Generator) -> np.ndarray:
    h, w = shape
    lat = rng.random((freq + 1, freq + 1)).astype(np.float32)
    return np.asarray(Image.fromarray(lat, "F").resize((w, h), Image.Resampling.BICUBIC), np.float32)


def _outline(alpha: np.ndarray) -> tuple[int, int]:
    """-> (area, boundary pixel count) of alpha > 0.5."""
    m = alpha > 0.5
    inner = m.copy()
    inner[1:] &= m[:-1]
    inner[:-1] &= m[1:]
    inner[:, 1:] &= m[:, :-1]
    inner[:, :-1] &= m[:, 1:]
    return int(m.sum()), int((m & ~inner).sum())


def roundness(alpha: np.ndarray) -> float:
    area, edge = _outline(alpha)
    if area < 16:
        return 0.0
    perim = edge * 1.12
    return float(min(1.0, 4 * np.pi * area / max(perim * perim, 1.0)))


LINEAR = ("crack", "fissure", "fracture", "scratch", "shatter", "split", "claw", "slash", "rift", "seam")
# Stroke width (output px) a linear mark is grown to, as a fraction of the decal size: 1/96 is ~5 px at 512, enough
# for a hairline crack to survive mip-mapping on a 1-2 m projector.
MIN_STROKE = 1 / 96


def is_linear(name: str) -> bool:
    return any(k in name.lower() for k in LINEAR)


def stroke_width(alpha: np.ndarray) -> float:
    """Mean stroke width in px: 2·area / boundary is the width of a long band (and ~2 for a 1 px line)."""
    area, edge = _outline(alpha)
    return 2.0 * area / edge if area >= 16 else 0.0


def _max_box(a: np.ndarray, r: int) -> np.ndarray:
    out = a.copy()
    for axis in (0, 1):
        src = out.copy()
        for d in range(1, r + 1):
            sl_a = [slice(None)] * 2
            sl_b = [slice(None)] * 2
            sl_a[axis], sl_b[axis] = slice(d, None), slice(None, -d)
            np.maximum(out[tuple(sl_a)], src[tuple(sl_b)], out=out[tuple(sl_a)])
            np.maximum(out[tuple(sl_b)], src[tuple(sl_a)], out=out[tuple(sl_b)])
    return out


def thicken(color: np.ndarray, alpha: np.ndarray, radius: int) -> tuple[np.ndarray, np.ndarray]:
    """Grows the mark by `radius` px; the new rim takes the alpha-weighted colour of the mark next to it, so a dark
    crack widens dark instead of picking up the light key colour."""
    if radius <= 0:
        return color, alpha
    grown = soft_blur(_max_box(alpha, radius), 1, wrap=False)
    grown = np.maximum(alpha, np.clip(grown, 0, 1))
    r = radius + 1
    den = soft_blur(alpha, r, wrap=False)[..., None]
    fill = soft_blur(color * alpha[..., None], r, wrap=False) / np.maximum(den, 1e-4)
    keep = alpha[..., None]
    out = np.where(den > 1e-4, color * keep + np.clip(fill, 0, 1) * (1 - keep), color)
    return out.astype(np.float32), grown.astype(np.float32)


def stroke_radius(alpha: np.ndarray, name: str, size: int) -> tuple[float, int]:
    """-> (stroke width in output px, source px to grow by); only linear marks (by name) are grown, to MIN_STROKE."""
    ys, xs = np.nonzero(alpha > 0.02)
    w = stroke_width(alpha)
    if len(xs) == 0 or w <= 0:
        return 0.0, 0
    extent = max(int(ys.max() - ys.min()), int(xs.max() - xs.min())) + 1
    scale = size / (extent * 1.08)
    out_w = w * scale
    if not is_linear(name) or out_w >= MIN_STROKE * size:
        return round(out_w, 2), 0
    grow = max(1, int(np.ceil((MIN_STROKE * size / scale - w) / 2)))
    return round(out_w, 2), min(grow, max(2, round(min(alpha.shape) * 0.02)))


def irregular_edge(color: np.ndarray, alpha: np.ndarray, amount: float, seed: int
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Bends the outline with a low-frequency warp and eats into it with noise (big lobes + grainy fringe), so a
    stain the model drew as a disc gets a ragged, uncertain border; the solid middle stays."""
    if amount <= 0:
        return color, alpha
    rng = np.random.default_rng(seed)
    h, w = alpha.shape
    # Domain warp first: low-frequency displacement bends a circle into a blob; the erosion below adds the fringe.
    amp = min(amount, 0.8) * 0.12 * min(h, w)
    dy = (_noise(alpha.shape, 3, rng) - 0.5) * 2 * amp
    dx = (_noise(alpha.shape, 3, rng) - 0.5) * 2 * amp
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sy = np.clip(np.round(yy + dy), 0, h - 1).astype(np.int32)
    sx = np.clip(np.round(xx + dx), 0, w - 1).astype(np.int32)
    alpha, color = alpha[sy, sx], color[sy, sx]
    soft = soft_blur(alpha, max(2, round(min(h, w) * 0.08)), wrap=False)
    n = 0.6 * _noise(alpha.shape, 4, rng) + 0.3 * _noise(alpha.shape, 11, rng) + 0.1 * _noise(alpha.shape, 48, rng)
    n = (n - n.min()) / max(float(n.max() - n.min()), 1e-6)
    t = soft + min(amount, 0.8) * (n - 0.5) * 1.6
    keep = np.clip((t - 0.35) / 0.25, 0, 1)
    return color, (alpha * keep * keep * (3 - 2 * keep)).astype(np.float32)


def unpremultiply(rgb: np.ndarray, alpha: np.ndarray, bg: np.ndarray) -> np.ndarray:
    a = np.maximum(alpha, 0.05)[..., None]
    return np.clip(bg + (rgb - bg) / a, 0, 1).astype(np.float32)


def edge_fade(shape: tuple[int, int], frac: float) -> np.ndarray:
    h, w = shape
    if frac <= 0:
        return np.ones(shape, np.float32)
    y = np.minimum(np.arange(h), np.arange(h)[::-1]) / max(1.0, h * frac)
    x = np.minimum(np.arange(w), np.arange(w)[::-1]) / max(1.0, w * frac)
    f = np.clip(np.minimum(y[:, None], x[None, :]), 0, 1)
    return (f * f * (3 - 2 * f)).astype(np.float32)


def fit_square(rgba: np.ndarray, size: int, pad: float = 0.04) -> tuple[Image.Image, tuple[float, float]]:
    """Crops to the alpha bounds (+pad), letterboxes into a size×size canvas -> (image, (w, h) aspect, max 1)."""
    a = rgba[..., 3]
    ys, xs = np.nonzero(a > 0.02)
    h, w = a.shape
    if len(xs) == 0:
        y0, y1, x0, x1 = 0, h, 0, w
    else:
        p = round(max(h, w) * pad)
        y0, y1 = max(0, ys.min() - p), min(h, ys.max() + 1 + p)
        x0, x1 = max(0, xs.min() - p), min(w, xs.max() + 1 + p)
    crop = rgba[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    s = size / max(ch, cw)
    nw, nh = max(1, round(cw * s)), max(1, round(ch * s))
    img = Image.fromarray(np.clip(np.round(crop * 255), 0, 255).astype(np.uint8), "RGBA")
    # Premultiplied resize, so the transparent pixels' colour does not bleed into the edge.
    pm = np.asarray(img, np.float32) / 255
    pm[..., :3] *= pm[..., 3:4]
    small = np.asarray(Image.fromarray(np.round(pm * 255).astype(np.uint8), "RGBA").resize(
        (nw, nh), Image.Resampling.LANCZOS), np.float32) / 255
    small[..., :3] = np.where(small[..., 3:4] > 1e-3, small[..., :3] / np.maximum(small[..., 3:4], 1e-3), 0)
    out = np.zeros((size, size, 4), np.float32)
    oy, ox = (size - nh) // 2, (size - nw) // 2
    out[oy:oy + nh, ox:ox + nw] = np.clip(small, 0, 1)
    return (Image.fromarray(np.round(out * 255).astype(np.uint8), "RGBA"),
            (round(cw / max(ch, cw), 4), round(ch / max(ch, cw), 4)))


def decal_normal(rgba: Image.Image, strength: float, invert: bool) -> Image.Image:
    """Dark marks read as recessed (cracks, scorch); height is luminance inside the decal, flat outside it."""
    a = np.asarray(rgba, np.float32) / 255
    alpha = a[..., 3]
    lum = _lum(a[..., :3])
    h = (lum if invert else 1 - lum) * alpha
    h = soft_blur(h, max(1, rgba.width // 256), wrap=False)
    n = normal_map(1 - h, PbrParams(normal_strength=strength, seamless=False))
    rgb = np.clip(np.round(n * 255), 0, 255).astype(np.uint8)
    return Image.fromarray(np.concatenate([rgb, np.round(alpha * 255).astype(np.uint8)[..., None]], -1), "RGBA")


def irregular_amount(setting: Any, name: str, mode: str, round_score: float) -> float:
    """'auto': organic marks (by name) or anything keyed out as a near-disc get a ragged edge; logos and stickers cut
    by BiRefNet keep theirs. A number forces that amount."""
    if setting in (None, "", "auto"):
        if mode == "birefnet":
            return 0.0
        organic = any(k in name.lower() for k in ORGANIC)
        return IRREGULAR_AUTO if organic or round_score > ROUND_LIMIT else 0.0
    return min(0.8, max(0.0, float(setting)))


def make_decal(src: Image.Image, cut: Image.Image | None, mode: str, size: int, fade: float, strength: float,
               invert: bool, name: str = "", irregular: Any = "auto", seed: int = 0
               ) -> tuple[Image.Image, Image.Image, dict[str, Any]]:
    rgb = np.asarray(src.convert("RGB"), np.float32) / 255
    alpha, bg = key_alpha(rgb)
    inked = ink_key(rgb) if mode != "birefnet" and is_linear(name) else None
    if mode == "birefnet" and cut is not None:
        c = cut.convert("RGBA")
        if c.size != src.size:
            c = c.resize(src.size, Image.Resampling.LANCZOS)
        alpha = np.asarray(c, np.float32)[..., 3] / 255
        color = rgb
        stroke = None
    else:
        if inked is not None:
            alpha, color, bg = inked
        else:
            color = unpremultiply(rgb, alpha, bg)
        out_w, grow = stroke_radius(alpha, name, size)
        color, alpha = thicken(color, alpha, grow)
        stroke = [out_w, stroke_radius(alpha, name, size)[0] if grow else out_w]
    before = roundness(alpha)
    amount = irregular_amount(irregular, name, mode, before)
    color, alpha = irregular_edge(color, alpha, amount, seed)
    alpha = alpha * edge_fade(alpha.shape, fade)
    albedo, (aw, ah) = fit_square(np.concatenate([color, alpha[..., None]], -1), size)
    normal = decal_normal(albedo, strength, invert)
    a8 = np.asarray(albedo)[..., 3]
    cov = float((a8 > 128).mean())
    return albedo, normal, {"aspect": [aw, ah], "coverage": round(cov, 4),
                            "background": [round(float(v), 3) for v in bg], "irregular": amount, "stroke_px": stroke,
                            "roundness": [round(before, 3), round(roundness(a8 / 255), 3)]}


def sheet(decals: list[tuple[str, Image.Image]], cell: int = 256) -> Image.Image:
    """Every decal over a grey concrete-ish checker so the alpha edges and the un-premultiplied colour show."""
    cols = min(4, len(decals))
    rows = (len(decals) + cols - 1) // cols
    out = Image.new("RGB", (cols * cell, rows * (cell + 16)), (120, 120, 116))
    d = ImageDraw.Draw(out)
    for i, (name, img) in enumerate(decals):
        x, y = (i % cols) * cell, (i // cols) * (cell + 16)
        bgc = (150, 148, 140) if (i + i // cols) % 2 else (105, 104, 100)
        tile = Image.new("RGBA", (cell, cell), bgc + (255,))
        tile.alpha_composite(img.resize((cell, cell), Image.Resampling.LANCZOS))
        out.paste(tile.convert("RGB"), (x, y + 16))
        d.text((x + 3, y + 2), name, fill=(255, 255, 255))
    return out


class DecalPack(OpExecutor):
    kind = "op.decal_pack"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        cuts = inputs.get("cutout") or []
        mode = str(params.get("alpha") or "key")
        try:
            size = int(params.get("size", 512))
            fade = float(params.get("fade", 0.06))
            strength = float(params.get("normal_strength", 4.0))
            width_m = float(params.get("width_m", 1.0))
            irregular = params.get("irregular", "auto")
            if irregular not in (None, "", "auto"):
                irregular = float(irregular)
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        if mode not in ALPHA_MODES or size not in SIZES or not 0 <= fade <= 0.3 or width_m <= 0:
            raise StepError("op_bad_params", f"alpha must be {'/'.join(ALPHA_MODES)}, size {SIZES}, fade 0..0.3")
        if mode == "birefnet" and len(cuts) != len(refs):
            raise StepError("op_bad_input", f"alpha=birefnet needs one cutout per image ({len(cuts)} for {len(refs)})")
        invert = _enabled(params.get("invert_height", False))
        names_raw = [ln.strip() for ln in str(params.get("names") or "").splitlines() if ln.strip()]
        out: list[BlobRef] = []
        normals: list[BlobRef] = []
        entries: list[dict[str, Any]] = []
        shown: list[tuple[str, Image.Image]] = []
        taken: set[str] = set()
        for i, ref in enumerate(refs):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.progress(i, len(refs), label="decal")
            name = slug(names_raw[i] if i < len(names_raw) else (ref.name or "").rsplit(".", 1)[0], f"decal_{i + 1}")
            base, k = name, 2
            while name in taken:
                name, k = f"{base}_{k}", k + 1
            taken.add(name)
            src = await _load(ctx, ref)
            cut = await _load(ctx, cuts[i]) if mode == "birefnet" else None
            albedo, normal, meta = await asyncio.to_thread(make_decal, src, cut, mode, size, fade, strength, invert,
                                                           name, irregular, zlib.crc32(name.encode()))
            a_ref = await _put_png(ctx, albedo, f"{name}.png")
            n_ref = await _put_png(ctx, normal, f"{name}_normal.png")
            out.append(a_ref)
            normals.append(n_ref)
            aw, ah = meta["aspect"]
            entries.append({"name": name, "albedo": {"name": a_ref.name, "hash": a_ref.hash},
                            "normal": {"name": n_ref.name, "hash": n_ref.hash},
                            "width_m": round(width_m * aw, 4), "height_m": round(width_m * ah, 4), **meta})
            shown.append((name, albedo))
        if all(e["coverage"] < 0.002 for e in entries):
            raise StepError("op_bad_input", "every decal came out empty: the key found no marks on the background")
        doc = {"type": DECAL_TYPE, "size": size, "alpha": mode, "decals": entries}
        doc_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=DECAL_FILE,
                                            mime="application/json")
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": out, "normals": normals, "decals": [doc_ref],
                                   "preview": [await _put_png(ctx, await asyncio.to_thread(sheet, shown),
                                                              "decal_preview.png")]},
                          meta={"decals": len(entries), "alpha": mode,
                                "coverage": {e["name"]: e["coverage"] for e in entries},
                                "roundness": {e["name"]: e["roundness"] for e in entries},
                                "stroke_px": {e["name"]: e["stroke_px"] for e in entries}})


DECAL_OPS = (DecalPack,)
