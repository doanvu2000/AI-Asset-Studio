"""`op.decal_pack`: decals drawn on a plain light background -> RGBA albedo + normal map per decal and decal.json.

Alpha comes from keying against the background colour (thin cracks, dirt, scorch and moss survive it, where a
salient-object cutter keeps only blobs) or from the BiRefNet cutout for solid shapes like logos and stickers. The
colour is un-premultiplied against the key, and the alpha fades out near the image border so a projected decal
never shows the square edge of its texture."""
from __future__ import annotations

import asyncio
import json
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


def key_alpha(rgb: np.ndarray, lo: float = 0.04, hi: float = 0.22) -> tuple[np.ndarray, np.ndarray]:
    """-> (alpha, background colour). The background is the median of the outer 3% ring."""
    h, w = rgb.shape[:2]
    b = max(2, round(min(h, w) * 0.03))
    ring = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3), rgb[:, :b].reshape(-1, 3),
                           rgb[:, -b:].reshape(-1, 3)])
    bg = np.median(ring, 0)
    dist = np.abs(rgb - bg).max(-1)
    t = np.clip((dist - lo) / (hi - lo), 0, 1)
    return (t * t * (3 - 2 * t)).astype(np.float32), bg.astype(np.float32)


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


def make_decal(src: Image.Image, cut: Image.Image | None, mode: str, size: int, fade: float, strength: float,
               invert: bool) -> tuple[Image.Image, Image.Image, dict[str, Any]]:
    rgb = np.asarray(src.convert("RGB"), np.float32) / 255
    alpha, bg = key_alpha(rgb)
    if mode == "birefnet" and cut is not None:
        c = cut.convert("RGBA")
        if c.size != src.size:
            c = c.resize(src.size, Image.Resampling.LANCZOS)
        alpha = np.asarray(c, np.float32)[..., 3] / 255
        color = rgb
    else:
        color = unpremultiply(rgb, alpha, bg)
    alpha = alpha * edge_fade(alpha.shape, fade)
    albedo, (aw, ah) = fit_square(np.concatenate([color, alpha[..., None]], -1), size)
    normal = decal_normal(albedo, strength, invert)
    cov = float((np.asarray(albedo)[..., 3] > 128).mean())
    return albedo, normal, {"aspect": [aw, ah], "coverage": round(cov, 4),
                            "background": [round(float(v), 3) for v in bg]}


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
            albedo, normal, meta = await asyncio.to_thread(make_decal, src, cut, mode, size, fade, strength, invert)
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
                                "coverage": {e["name"]: e["coverage"] for e in entries}})


DECAL_OPS = (DecalPack,)
