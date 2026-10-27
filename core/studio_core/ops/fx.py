"""Shadow + outline post-process for whole sprite sets: one set of params, one shadow size, same canvas."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _normal_name, _out_name, _put_png, _read_json, _skip_empty
from .pixel import parse_hex

SHADOWS = ("none", "drop", "blob")
FLAT_NORMAL = (128, 128, 255)
_PLUS = ((-1, 0), (1, 0), (0, -1), (0, 1))
_DIAG = ((-1, -1), (-1, 1), (1, -1), (1, 1))
_BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16.0 + 1 / 32


@dataclass(frozen=True)
class FxParams:
    shadow: str = "none"
    shadow_color: tuple[int, int, int] = (0, 0, 0)
    shadow_opacity: float = 0.45
    offset: tuple[int, int] = (3, 3)
    blur: float = 1.5
    blob_scale: float = 0.8
    blob_squash: float = 0.3
    outline: tuple[int, int, int] | None = None
    width: int = 1
    pixel: bool = False
    normal: bool = False

    @property
    def active(self) -> bool:
        return (self.shadow != "none" and not self.normal) or self.outline is not None

    @classmethod
    def from_params(cls, p: dict[str, Any]) -> FxParams:
        shadow = str(p.get("shadow") or "none")
        if shadow not in SHADOWS:
            raise ValueError(f"shadow must be one of {SHADOWS}")
        raw_line = str(p.get("outline") or "none").strip()
        outline = None if raw_line.lower() in ("none", "") else parse_hex(raw_line)
        raw_w = p.get("width", p.get("outline_width", 1))
        width = int(1 if raw_w in (None, "") else raw_w)
        if not 1 <= width <= 4:
            raise ValueError("outline width must be within 1..4")
        opacity = float(p.get("shadow_opacity", 0.45))
        if not 0 <= opacity <= 1:
            raise ValueError("shadow_opacity must be within 0..1")
        off = p.get("offset", (3, 3))
        if not isinstance(off, (list, tuple)) or len(off) != 2:
            raise ValueError("offset must be [dx, dy]")
        return cls(shadow=shadow, shadow_color=parse_hex(str(p.get("shadow_color") or "#000000")),
                   shadow_opacity=opacity, offset=(int(off[0]), int(off[1])),
                   blur=max(0.0, float(p.get("blur", 1.5))), blob_scale=float(p.get("blob_scale", 0.8)),
                   blob_squash=float(p.get("blob_squash", 0.3)), outline=outline, width=width,
                   pixel=bool(p.get("pixel")), normal=bool(p.get("normal")))


def _shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    out[max(dy, 0):h + min(dy, 0), max(dx, 0):w + min(dx, 0)] = a[max(-dy, 0):h - max(dy, 0), max(-dx, 0):w - max(dx, 0)]
    return out


def dilate(a: np.ndarray, width: int, plus_first: bool) -> np.ndarray:
    """Max filter alternating plus/square steps, which approximates a disc instead of a square."""
    out = a
    for i in range(width):
        steps = _PLUS if (i % 2 == 0) == plus_first else _PLUS + _DIAG
        out = np.maximum.reduce([out, *(_shift(out, dy, dx) for dy, dx in steps)])
    return out


def _bbox(alpha: np.ndarray, thr: int = 16) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(alpha >= thr)
    if not len(ys):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def blob_geometry(frames: Sequence[Image.Image], pivot: tuple[float, float] | None,
                  fx: FxParams) -> tuple[float, float, float, float] | None:
    """(cx, cy, rx, ry) shared by every frame: medians over the set, so the shadow does not breathe between frames."""
    boxes = [b for b in (_bbox(np.array(f.getchannel("A"))) for f in frames) if b is not None]
    if not boxes:
        return None
    widths = np.array([b[2] - b[0] for b in boxes], float)
    rx = max(1.0, float(np.median(widths)) * fx.blob_scale / 2)
    ry = max(1.0, rx * fx.blob_squash)
    if pivot is not None:
        cx, cy = pivot
    else:
        cx = float(np.median([(b[0] + b[2]) / 2 for b in boxes]))
        cy = float(np.median([b[3] for b in boxes])) - 1
    return cx, cy, rx, ry


def _blob_alpha(size: tuple[int, int], geo: tuple[float, float, float, float], fx: FxParams) -> np.ndarray:
    w, h = size
    cx, cy, rx, ry = geo
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64) + 0.5
    d = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
    if fx.pixel:
        return np.where(d <= 1.0, fx.shadow_opacity, 0.0)
    # Soft falloff toward the rim reads as contact shadow rather than a flat disc.
    return np.clip(1.0 - d, 0.0, 1.0) ** 0.6 * fx.shadow_opacity


def apply_fx(img: Image.Image, fx: FxParams, geo: tuple[float, float, float, float] | None = None) -> Image.Image:
    rgba = np.array(img.convert("RGBA"))
    h, w = rgba.shape[:2]
    alpha = rgba[..., 3]
    if fx.pixel:
        alpha = np.where(alpha >= 128, 255, 0).astype(np.uint8)
        rgba[..., 3] = alpha
    base = Image.fromarray(rgba, "RGBA")
    body = alpha
    layers: list[Image.Image] = []
    if fx.outline is not None:
        grown = dilate(alpha, fx.width, plus_first=True)
        if fx.pixel:
            grown = np.where(grown >= 128, 255, 0).astype(np.uint8)
        color = FLAT_NORMAL if fx.normal else fx.outline
        line = np.zeros((h, w, 4), np.uint8)
        line[..., :3] = color
        line[..., 3] = grown
        layers.append(Image.fromarray(line, "RGBA"))
        body = np.maximum(body, grown)
    if fx.shadow != "none" and not fx.normal:
        if fx.shadow == "blob":
            sa = _blob_alpha((w, h), geo, fx) if geo is not None else np.zeros((h, w))
        else:
            sa = _shift(body, fx.offset[1], fx.offset[0]).astype(np.float64) / 255.0 * fx.shadow_opacity
            if fx.blur > 0 and not fx.pixel:
                sa = np.array(Image.fromarray(np.round(sa * 255).astype(np.uint8), "L")
                              .filter(ImageFilter.GaussianBlur(fx.blur)), np.float64) / 255.0
        if fx.pixel:
            # Ordered dither keeps pixel art at hard alpha; the pattern is tied to canvas position, so it holds still.
            yy, xx = np.mgrid[0:h, 0:w]
            sa = np.where((sa > 0) & (_BAYER4[yy % 4, xx % 4] < sa), 1.0, 0.0)
        shadow = np.zeros((h, w, 4), np.uint8)
        shadow[..., :3] = fx.shadow_color
        shadow[..., 3] = np.round(np.clip(sa, 0, 1) * 255).astype(np.uint8)
        layers.insert(0, Image.fromarray(shadow, "RGBA"))
    if not layers:
        return base
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for layer in layers:
        out.alpha_composite(layer)
    out.alpha_composite(base)
    return out


def fx_colors(fx: FxParams) -> list[str]:
    """Colors this fx paints on top of the input (for pixel art these are outside its palette)."""
    out = []
    if fx.outline is not None and not fx.normal:
        out.append(fx.outline)
    if fx.shadow != "none" and not fx.normal:
        out.append(fx.shadow_color)
    return ["#{:02x}{:02x}{:02x}".format(*c) for c in dict.fromkeys(out)]


def sprite_fx(frames: Sequence[Image.Image], fx: FxParams,
              pivot: tuple[float, float] | None = None) -> list[Image.Image]:
    if not frames:
        raise ValueError("no frames")
    geo = blob_geometry(frames, pivot, fx) if fx.shadow == "blob" and not fx.normal else None
    return [apply_fx(f, fx, geo) for f in frames]


class SpriteFx(OpExecutor):
    """Keeps every frame's canvas, so meta pivots stay exact; anything drawn past the edge is clipped. Normal frames
    get no shadow and a flat-normal (128,128,255) outline so they stay aligned with the color sheet."""
    kind = "op.sprite_fx"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        if _skip_empty(params, inputs):
            return StepResult(outputs={"images": []})
        refs = _images_in(inputs)
        try:
            fx = FxParams.from_params(params)
        except (ValueError, TypeError) as e:
            raise StepError("op_bad_params", str(e)) from e
        meta: dict[str, Any] = {"normal": True} if fx.normal else {}
        if not fx.active:
            # Same blobs out, so downstream cache keys match a recipe without this step.
            return StepResult(outputs={"images": list(refs)}, meta={**meta, "fx": False})
        imgs = []
        for i, ref in enumerate(refs):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            imgs.append(await _load(ctx, ref))
            ctx.progress(i + 1, len(refs) * 2, label="load")
        pivot = None
        if inputs.get("pivot") and fx.shadow == "blob":
            info = await _read_json(ctx, inputs["pivot"][0])
            try:
                x, y = (float(v) for v in info["pivot"])
                w0, h0 = imgs[0].size
                pivot = (x * w0 / float(info.get("width") or w0), y * h0 / float(info.get("height") or h0))
            except (KeyError, TypeError, ValueError):
                pivot = None
        res = await asyncio.to_thread(sprite_fx, imgs, fx, pivot)
        out: list[BlobRef] = []
        for i, (ref, img) in enumerate(zip(refs, res)):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            out.append(await _put_png(ctx, img, _normal_name(ref, i, "fx") if fx.normal else _out_name(ref, i, "fx")))
            ctx.progress(len(refs) + i + 1, len(refs) * 2, label="save")
        info_out = {"shadow": "none" if fx.normal else fx.shadow,
                    "outline": None if fx.outline is None else "#{:02x}{:02x}{:02x}".format(*fx.outline),
                    "width": fx.width, "pixel": fx.pixel, "colors_added": fx_colors(fx)}
        return StepResult(outputs={"images": out}, meta={**meta, "fx": info_out})


FX_OPS: tuple[type[OpExecutor], ...] = (SpriteFx,)
