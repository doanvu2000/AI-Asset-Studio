"""`op.hue_variants`: one image -> N colour variants by rotating hue; alpha and shading stay untouched."""
from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import Outputs, StepContext, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png

MAX_VARIANTS = 12


def rotate_hue(img: Image.Image, degrees: float) -> Image.Image:
    rgba = img.convert("RGBA")
    hsv = np.array(rgba.convert("RGB").convert("HSV"))
    hsv[..., 0] = (hsv[..., 0].astype(np.int16) + round(degrees / 360 * 256)) % 256
    out = Image.fromarray(hsv, "HSV").convert("RGB").convert("RGBA")
    out.putalpha(rgba.getchannel("A"))
    return out


class HueVariants(OpExecutor):
    kind = "op.hue_variants"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        n = max(2, min(MAX_VARIANTS, int(params.get("count") or 6)))
        ref = _images_in(inputs)[0]
        src = await _load(ctx, ref)
        stem = (ref.name or "icon").rsplit(".", 1)[0]
        out = []
        for i in range(n):
            deg = 360 * i / n
            img = await asyncio.to_thread(rotate_hue, src, deg)
            out.append(await _put_png(ctx, img, f"{stem}_hue{round(deg):03d}.png"))
        return StepResult(outputs={"images": out}, meta={"count": n})


HUE_VARIANT_OPS: tuple[type[OpExecutor], ...] = (HueVariants,)
