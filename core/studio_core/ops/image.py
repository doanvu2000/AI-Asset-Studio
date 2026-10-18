"""Pure, synchronous image ops (Pillow + numpy). Executors call these via asyncio.to_thread."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Sequence

import numpy as np
from PIL import Image, ImageFilter, UnidentifiedImageError

Resample = Literal["lanczos", "nearest", "box", "bilinear", "bicubic"]
BBox = tuple[int, int, int, int]

_RESAMPLE = {
    "lanczos": Image.Resampling.LANCZOS,
    "nearest": Image.Resampling.NEAREST,
    "box": Image.Resampling.BOX,
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
}
WHITE_CUTOFF = 245


def _resample(name: str) -> Image.Resampling:
    try:
        return _RESAMPLE[name]
    except KeyError:
        raise ValueError(f"unknown resample: {name}") from None


def to_rgba(img: Image.Image) -> Image.Image:
    return img if img.mode == "RGBA" else img.convert("RGBA")


def open_image(src: Path) -> Image.Image:
    try:
        with Image.open(src) as im:
            im.load()
            return to_rgba(im)
    except UnidentifiedImageError as e:
        raise ValueError(f"not an image: {src.name}") from e


def make_thumbnail(src: Path, dst: Path, size: int) -> None:
    img = open_image(Path(src))
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "WEBP", quality=80, method=4)


def content_bbox(img: Image.Image, alpha_threshold: int = 8) -> BBox | None:
    """Bbox (l, t, r, b) of alpha > threshold; for images without usable alpha, of non-near-white pixels."""
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    arr = np.asarray(to_rgba(img))
    alpha = arr[..., 3]
    if has_alpha and alpha.min() <= alpha_threshold:
        mask = alpha > alpha_threshold
    else:
        mask = (arr[..., :3] < WHITE_CUTOFF).any(axis=2)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def trim(img: Image.Image, alpha_threshold: int = 8) -> Image.Image:
    rgba = to_rgba(img)
    bbox = content_bbox(img, alpha_threshold)
    return rgba.crop(bbox) if bbox else rgba


def _fit_scale(w: int, h: int, box_w: float, box_h: float) -> float:
    return min(box_w / w, box_h / h)


def _scaled(img: Image.Image, scale: float, resample: str) -> Image.Image:
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    if (w, h) == img.size:
        return img
    return img.resize((w, h), _resample(resample))


def trim_pad_resize(img: Image.Image, size: int, pad: float = 0.06, square: bool = True,
                    resample: Resample = "lanczos", alpha_threshold: int = 8) -> Image.Image:
    """Trim to content, fit inside size*(1-2*pad) keeping aspect, center on a transparent canvas.

    square=False makes the canvas hug the scaled content (plus padding) instead of size×size.
    """
    content = trim(img, alpha_threshold)
    margin = round(size * pad)
    inner = max(1, size - 2 * margin)
    scaled = _scaled(content, _fit_scale(content.width, content.height, inner, inner), resample)
    cw, ch = (size, size) if square else (scaled.width + 2 * margin, scaled.height + 2 * margin)
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    canvas.alpha_composite(scaled, ((cw - scaled.width) // 2, (ch - scaled.height) // 2))
    return canvas


def normalize_frames(frames: Sequence[Image.Image], size: int, pad: float = 0.06,
                     anchor: Literal["bottom", "center"] = "bottom", resample: Resample = "lanczos",
                     alpha_threshold: int = 8) -> tuple[list[Image.Image], tuple[int, int]]:
    """One shared scale + baseline for a direction set; returns (frames, pivot px).

    Same-sized frames (renders from a fixed camera) are cropped by their union bbox so relative
    placement survives; mixed sizes fall back to per-frame bboxes centered horizontally.
    """
    if not frames:
        return [], (size // 2, size // 2)
    rgba = [to_rgba(f) for f in frames]
    boxes = [content_bbox(f, alpha_threshold) or (0, 0, f.width, f.height) for f in rgba]
    if len({f.size for f in rgba}) == 1:
        union = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                 max(b[2] for b in boxes), max(b[3] for b in boxes))
        boxes = [union] * len(rgba)
    max_w = max(b[2] - b[0] for b in boxes)
    max_h = max(b[3] - b[1] for b in boxes)
    margin = round(size * pad)
    inner = max(1, size - 2 * margin)
    scale = _fit_scale(max_w, max_h, inner, inner)
    baseline = size - margin if anchor == "bottom" else (size + round(max_h * scale)) // 2
    out = []
    for f, b in zip(rgba, boxes):
        part = _scaled(f.crop(b), scale, resample)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(part, ((size - part.width) // 2, baseline - part.height))
        out.append(canvas)
    return out, (size // 2, baseline)


def mirror_dirs(frames_0_to_180: Sequence[Image.Image], count: int = 8) -> list[Image.Image]:
    """Angles step 360/count, 0 = facing camera, increasing turns the model; angle a mirrors to 360-a by h-flip."""
    if count < 2 or count % 2:
        raise ValueError("count must be even and >= 2")
    expected = count // 2 + 1
    if len(frames_0_to_180) != expected:
        raise ValueError(f"need {expected} frames for angles 0..180, got {len(frames_0_to_180)}")
    half = list(frames_0_to_180)
    mirrored = [half[count - i].transpose(Image.Transpose.FLIP_LEFT_RIGHT) for i in range(expected, count)]
    return half + mirrored


def pack_sheet(frames: Sequence[Image.Image], cols: int, cell: tuple[int, int] | int | None = None,
               padding: int = 0, pivot: tuple[int, int] | None = None,
               order: Sequence[Any] | None = None) -> tuple[Image.Image, dict[str, Any]]:
    """Row-major grid: rows = directions, cols = animation frames. Frames are centered in their cell."""
    if not frames:
        raise ValueError("no frames")
    cols = max(1, min(cols, len(frames)))
    rows = -(-len(frames) // cols)
    if cell is None:
        cw, ch = max(f.width for f in frames), max(f.height for f in frames)
    elif isinstance(cell, int):
        cw = ch = cell
    else:
        cw, ch = cell
    sheet = Image.new("RGBA", (cols * cw + (cols - 1) * padding, rows * ch + (rows - 1) * padding), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        f = to_rgba(f)
        if f.width > cw or f.height > ch:
            f = _scaled(f, _fit_scale(f.width, f.height, cw, ch), "lanczos")
        r, c = divmod(i, cols)
        x = c * (cw + padding) + (cw - f.width) // 2
        y = r * (ch + padding) + (ch - f.height) // 2
        sheet.alpha_composite(f, (x, y))
    meta: dict[str, Any] = {
        "frame_w": cw, "frame_h": ch, "cols": cols, "rows": rows, "count": len(frames),
        "padding": padding, "order": list(order) if order is not None else list(range(len(frames))),
        "width": sheet.width, "height": sheet.height,
    }
    if pivot is not None:
        meta["pivot"] = [int(pivot[0]), int(pivot[1])]
    return sheet, meta


def downscale(img: Image.Image, factor: float, resample: Resample = "box") -> Image.Image:
    if factor <= 0:
        raise ValueError("factor must be > 0")
    return _scaled(to_rgba(img), 1 / factor, resample)


def _parse_color(c: str | Sequence[int]) -> tuple[int, int, int, int]:
    if isinstance(c, str):
        h = c.lstrip("#")
        vals = [int(h[i:i + 2], 16) for i in range(0, len(h), 2)]
    else:
        vals = list(c)
    if len(vals) == 3:
        vals.append(255)
    if len(vals) != 4:
        raise ValueError(f"bad color: {c!r}")
    return vals[0], vals[1], vals[2], vals[3]


def pixelate(img: Image.Image, size: int, palette_colors: int | None = None,
             outline: str | Sequence[int] | None = None, alpha_threshold: int = 127) -> Image.Image:
    """Fit inside size×size with nearest sampling, binary alpha, optional palette quantize and 1px outline."""
    rgba = to_rgba(img)
    small = _scaled(rgba, _fit_scale(rgba.width, rgba.height, size, size), "nearest")
    arr = np.array(small)
    solid = arr[..., 3] > alpha_threshold
    if palette_colors:
        q = Image.fromarray(arr[..., :3]).quantize(colors=palette_colors, method=Image.Quantize.MEDIANCUT)
        arr[..., :3] = np.asarray(q.convert("RGB"))
    arr[..., 3] = np.where(solid, 255, 0)
    arr[~solid, :3] = 0
    if outline is not None:
        mask = Image.fromarray((solid * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))
        ring = (np.asarray(mask) > 0) & ~solid
        arr[ring] = _parse_color(outline)
    return Image.fromarray(arr, "RGBA")
