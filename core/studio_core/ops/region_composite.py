"""Region edit composite: the Klein edit is kept only inside the painted mask, everything else stays the original pixels."""
from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from ..contracts import Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png


def mask_alpha(mask: Image.Image, size: tuple[int, int]) -> np.ndarray:
    """0..1 weight: painted white (or opaque-white strokes on transparent) = edit; luminance times alpha."""
    m = mask.convert("RGBA")
    if m.size != size:
        m = m.resize(size, Image.BILINEAR)
    a = np.asarray(m, np.float32) / 255.0
    lum = a[..., :3].mean(axis=2)
    return lum * a[..., 3]


def region_composite(orig: Image.Image, edit: Image.Image, mask: Image.Image, feather: float = 3.0,
                     keep_alpha: bool = True) -> Image.Image:
    if feather < 0:
        raise ValueError("feather must be >= 0")
    base = orig.convert("RGBA")
    # Klein may return a slightly different size; fit it to the original instead of moving the original.
    new = edit.convert("RGBA")
    if new.size != base.size:
        new = new.resize(base.size, Image.LANCZOS)
    w = mask_alpha(mask, base.size)
    if not (w > 0.5).any():
        raise ValueError("mask is empty")
    if feather > 0:
        soft = Image.fromarray((w * 255).round().astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(feather))
        w = np.asarray(soft, np.float32) / 255.0
    o = np.asarray(base, np.float32)
    e = np.asarray(new, np.float32)
    out = o * (1 - w[..., None]) + e * w[..., None]
    if keep_alpha:
        out[..., 3] = o[..., 3]
    return Image.fromarray(np.clip(out.round(), 0, 255).astype(np.uint8), "RGBA")


def changed_fraction(orig: Image.Image, out: Image.Image) -> float:
    a = np.asarray(orig.convert("RGBA"), np.int16)
    b = np.asarray(out.convert("RGBA"), np.int16)
    return float((np.abs(a - b).max(axis=2) > 0).mean())


class RegionComposite(OpExecutor):
    kind = "op.region_composite"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        for name in ("image", "edit", "mask"):
            if len(inputs.get(name) or []) != 1:
                raise StepError("op_no_input", f"needs exactly one '{name}' image")
        orig = await _load(ctx, inputs["image"][0])
        edit = await _load(ctx, inputs["edit"][0])
        mask = await _load(ctx, inputs["mask"][0])
        try:
            feather = float(params.get("feather") or 0)
            out = await asyncio.to_thread(region_composite, orig, edit, mask, feather,
                                          bool(params.get("keep_alpha", True)))
        except (ValueError, TypeError) as e:
            raise StepError("op_bad_params", str(e)) from e
        name = (inputs["image"][0].name or "image").rsplit(".", 1)[0] + "_edit.png"
        ref = await _put_png(ctx, out, name)
        ctx.progress(1, 1, label="done")
        return StepResult(outputs={"images": [ref]},
                          meta={"changed": round(changed_fraction(orig, out), 4), "feather": feather})


REGION_COMPOSITE_OPS: tuple[type[OpExecutor], ...] = (RegionComposite,)
