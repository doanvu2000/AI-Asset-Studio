"""2D sprite normal maps from albedo + alpha: a rounded bevel from the distance to the silhouette edge plus luminance
detail, Sobel'd into a tangent-space map (OpenGL, +G up: what Godot CanvasTexture and Unity URP 2D lights read)."""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from PIL import Image

    from studio_core.export.sheet import Cell, Layout

    from studio_core.export.source import Files, Source

FLAT = (0.5, 0.5, 1.0)
MAX_BEVEL = 64.0
DEFAULT_STRENGTH = 1.0
DEFAULT_DETAIL = 0.5


@dataclass(frozen=True)
class Normal2DOptions:
    strength: float = DEFAULT_STRENGTH
    # None = auto from the frame size.
    bevel: float | None = None
    detail: float = DEFAULT_DETAIL


def auto_bevel(w: int, h: int) -> float:
    return float(min(24, max(1, round(min(w, h) * 0.08))))


def _erode(m: np.ndarray, diag: bool) -> np.ndarray:
    p = np.pad(m, 1)
    out = m & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    if diag:
        out &= p[:-2, :-2] & p[:-2, 2:] & p[2:, :-2] & p[2:, 2:]
    return out


def edge_distance(mask: np.ndarray, limit: int) -> np.ndarray:
    """Steps to the nearest transparent pixel (or crop edge), capped at `limit`; alternating cross / square erosion
    gives an octagonal metric, close enough to Euclidean once blurred."""
    dist = mask.astype(np.float32)
    cur = mask
    for i in range(limit):
        cur = _erode(cur, diag=i % 2 == 1)
        if not cur.any():
            break
        dist += cur
    return dist


def height_field(rgba: np.ndarray, bevel: float, detail: float) -> np.ndarray:
    """Height in pixel units: dome of radius `bevel` along the silhouette + high-passed luminance."""
    from studio_core.ops.tile import _lum, soft_blur

    alpha = rgba[..., 3] / 255.0
    mask = alpha >= 0.5
    if not mask.any():
        return np.zeros(alpha.shape, np.float32)
    b = max(float(bevel), 0.5)
    dist = edge_distance(mask, int(math.ceil(b)) + 1)
    dist = np.where(mask, soft_blur(dist, 1, wrap=False), 0)
    t = np.clip((dist - 0.5) / b, 0, 1)
    h = np.sqrt(1 - (1 - t) ** 2) * b
    if detail > 0:
        lum = _lum(rgba[..., :3] / 255.0)
        r = max(2, round(b))
        # Mask-weighted low-pass: transparent pixels are often black and would read as a trench along the edge.
        w = soft_blur(mask.astype(np.float32), r, wrap=False)
        low = soft_blur(lum * mask, r, wrap=False) / np.maximum(w, 1e-4)
        h = h + (lum - low) * detail * b
    return (h * alpha).astype(np.float32)


def normal_from_height(h: np.ndarray, mask: np.ndarray, strength: float) -> np.ndarray:
    from studio_core.ops.pbr import PbrParams, normal_map

    n = normal_map(h, PbrParams(normal_strength=strength, seamless=False))
    n[~mask] = FLAT
    return n


def normal_rgba(rgba: np.ndarray, opts: Normal2DOptions, bevel: float | None = None) -> np.ndarray:
    """Normals in [0, 1] (H, W, 3) for one sprite / frame."""
    b = opts.bevel if opts.bevel is not None else (bevel or auto_bevel(rgba.shape[1], rgba.shape[0]))
    h = height_field(rgba.astype(np.float32), b, opts.detail)
    return normal_from_height(h, rgba[..., 3] > 0, opts.strength)


def normal_image(rgba: np.ndarray, opts: Normal2DOptions, cells: list[Cell] | None = None) -> np.ndarray:
    """uint8 RGB normal map; with `cells` each frame is filtered on its own so bevels never bleed across frames."""
    hgt, wid = rgba.shape[:2]
    out = np.empty((hgt, wid, 3), np.float32)
    out[...] = FLAT
    if not cells:
        out[...] = normal_rgba(rgba, opts)
    else:
        bevel = auto_bevel(cells[0].w, cells[0].h)
        for c in cells:
            crop = rgba[c.y:c.y + c.h, c.x:c.x + c.w]
            if crop.size:
                out[c.y:c.y + crop.shape[0], c.x:c.x + crop.shape[1]] = normal_rgba(crop, opts, bevel)
    return np.clip(np.round(out * 255), 0, 255).astype(np.uint8)


def to_png(arr: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def sheet_normal(sheet: Path, lay: Layout | None, opts: Normal2DOptions) -> bytes:
    from PIL import Image

    with Image.open(sheet) as im:
        rgba = np.asarray(im.convert("RGBA"))
    return to_png(normal_image(rgba, opts, list(lay.cells) if lay is not None else None))


def lit_preview(img: Image.Image, normal: np.ndarray) -> Image.Image:
    """Albedo | normal | albedo lit from the top-left, for the review gate."""
    from PIL import Image

    rgba = np.asarray(img.convert("RGBA")).astype(np.float32)
    n = normal.astype(np.float32) / 127.5 - 1
    light = np.array([-0.55, 0.55, 0.63], np.float32)
    light /= np.linalg.norm(light)
    shade = np.clip((n * light).sum(-1), 0, 1)[..., None] * 0.85 + 0.25
    lit = rgba.copy()
    lit[..., :3] = np.clip(rgba[..., :3] * shade, 0, 255)
    w, h = img.size
    out = Image.new("RGBA", (w * 3, h), (40, 40, 46, 255))
    out.alpha_composite(img.convert("RGBA"), (0, 0))
    out.paste(Image.fromarray(normal, "RGB"), (w, 0))
    out.alpha_composite(Image.fromarray(lit.astype(np.uint8), "RGBA"), (2 * w, 0))
    return out


def apply(src: Source, opts: Normal2DOptions | None) -> str | None:
    """Fills src.normal from the sheet when asked; a normal sheet rendered from 3D (sibling step) wins. Returns a note
    for the README, or None when nothing applies."""
    if opts is None or src.sheet is None or src.meta is None:
        return None
    if src.normal is not None:
        return "- Normal map 2D: bỏ qua vì đã có normal map render từ 3D (chính xác hơn)."
    from studio_core.export.sheet import layout

    lay = layout(src.meta, src.clip_hint, directional_rows=src.directional_rows)
    src.normal = sheet_normal(Path(src.sheet), lay, opts)
    b = "tự động" if opts.bevel is None else f"{opts.bevel:g}px"
    return (f"- Normal map 2D: sinh trên CPU từ alpha (vát viền {b}) + độ sáng, từng khung riêng; strength "
            f"{opts.strength:g}. Kênh G hướng lên (OpenGL) — đúng cho Godot và Unity URP 2D.")


def append_readme(files: Files, note: str) -> Files:
    return [(n, (d.decode().rstrip("\n") + "\n" + note + "\n").encode() if n == "README.txt" and isinstance(d, bytes)
             else d) for n, d in files]


def parse(p: dict[str, Any]) -> Normal2DOptions:
    bevel = p.get("bevel")
    b = float(bevel) if bevel not in (None, "", "auto") else 0.0
    return Normal2DOptions(strength=float(p.get("strength", DEFAULT_STRENGTH)), bevel=b if b > 0 else None,
                           detail=float(p.get("detail", DEFAULT_DETAIL)))
