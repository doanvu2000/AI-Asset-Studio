"""Shadow + outline for a whole sprite set on one grown canvas: nothing clipped, same size everywhere, shared pivot."""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _out_name, _put_png, _read_json
from .fx import FxParams, apply_fx, blob_geometry, fx_colors
from .pixel_cleanup import _outline_color, palette_from_image

# Shadow tails below this alpha are invisible; counting them would grow the canvas for nothing.
VISIBLE_ALPHA = 4


@dataclass
class ShadowOutlineResult:
    images: list[Image.Image]
    canvas: tuple[int, int]
    pad: tuple[int, int, int, int]
    pivot: tuple[float, float]
    offsets: list[tuple[int, int]]
    sources: list[tuple[int, int]]


def _unify(frames: list[Image.Image]) -> tuple[list[Image.Image], list[tuple[int, int]]]:
    """Frames of different sizes share one canvas, bottom-centred so feet stay put."""
    rgba = [f.convert("RGBA") for f in frames]
    w, h = max(f.width for f in rgba), max(f.height for f in rgba)
    out, offsets = [], []
    for f in rgba:
        off = ((w - f.width) // 2, h - f.height)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(f, off)
        out.append(canvas)
        offsets.append(off)
    return out, offsets


def _bbox(img: Image.Image, thr: int) -> tuple[int, int, int, int] | None:
    return img.getchannel("A").point(lambda v: 255 if v >= thr else 0).getbbox()


def _reach(fx: FxParams, geo: tuple[float, float, float, float] | None, size: tuple[int, int]) -> int:
    reach = fx.width if fx.outline is not None else 0
    if fx.shadow == "drop":
        reach += max(abs(fx.offset[0]), abs(fx.offset[1])) + (0 if fx.pixel else math.ceil(fx.blur * 3))
    elif fx.shadow == "blob" and geo is not None:
        reach += math.ceil(max(geo[2], geo[3]) + max(abs(geo[0] - size[0] / 2), abs(geo[1] - size[1] / 2)))
    return reach + 2


def shadow_outline(frames: list[Image.Image], fx: FxParams, pivot: tuple[float, float] | None = None,
                   margin: int = 0) -> ShadowOutlineResult:
    if not frames:
        raise ValueError("no frames")
    if margin < 0:
        raise ValueError("margin must be >= 0")
    srcs = [f.size for f in frames]
    base, offsets = _unify(frames)
    w, h = base[0].size
    if pivot is not None:
        pivot = (pivot[0] + offsets[0][0], pivot[1] + offsets[0][1])
    geo = blob_geometry(base, pivot, fx) if fx.shadow == "blob" else None
    if pivot is None:
        boxes = [b for b in (_bbox(f, 16) for f in base) if b]
        pivot = ((float(np.median([(b[0] + b[2]) / 2 for b in boxes])), float(np.median([b[3] for b in boxes])))
                 if boxes else (w / 2, float(h)))
    big = _reach(fx, geo, (w, h))
    grown = []
    for f in base:
        canvas = Image.new("RGBA", (w + 2 * big, h + 2 * big), (0, 0, 0, 0))
        canvas.paste(f, (big, big))
        grown.append(canvas)
    big_geo = (geo[0] + big, geo[1] + big, geo[2], geo[3]) if geo is not None else None
    done = [apply_fx(f, fx, big_geo) for f in grown]
    x0, y0, x1, y1 = big, big, big + w, big + h
    for img in done:
        box = _bbox(img, VISIBLE_ALPHA)
        if box:
            x0, y0, x1, y1 = min(x0, box[0]), min(y0, box[1]), max(x1, box[2]), max(y1, box[3])
    x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
    out = [img.crop((x0, y0, x1, y1)) for img in done]
    pad = (big - x0, big - y0, x1 - (big + w), y1 - (big + h))
    return ShadowOutlineResult(out, out[0].size, pad, (pivot[0] + pad[0], pivot[1] + pad[1]), offsets, srcs)


class ShadowOutline(OpExecutor):
    kind = "op.shadow_outline"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        try:
            palette = palette_from_image(await _load(ctx, inputs["palette_image"][0])) \
                if inputs.get("palette_image") else None
            fx = FxParams.from_params({**params, "outline": "none"})
            fx = replace(fx, outline=_outline_color(str(params.get("outline") or "none"), palette))
            margin = int(params.get("margin") or 0)
        except (ValueError, TypeError) as e:
            raise StepError("op_bad_params", str(e)) from e
        if not fx.active:
            raise StepError("op_bad_params", "choose a shadow or an outline")
        imgs = []
        for i, ref in enumerate(refs):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            imgs.append(await _load(ctx, ref))
            ctx.progress(i + 1, len(refs) * 2, label="load")
        pivot = None
        if inputs.get("pivot"):
            info = await _read_json(ctx, inputs["pivot"][0])
            try:
                x, y = (float(v) for v in info["pivot"])
                w0, h0 = imgs[0].size
                pivot = (x * w0 / float(info.get("width") or w0), y * h0 / float(info.get("height") or h0))
            except (KeyError, TypeError, ValueError):
                pivot = None
        try:
            res = await asyncio.to_thread(shadow_outline, imgs, fx, pivot, margin)
        except ValueError as e:
            raise StepError("op_bad_params", str(e)) from e
        out = []
        for i, (ref, img) in enumerate(zip(refs, res.images)):
            out.append(await _put_png(ctx, img, _out_name(ref, i, "fx")))
            ctx.progress(len(refs) + i + 1, len(refs) * 2, label="save")
        info_out = {
            "width": res.canvas[0], "height": res.canvas[1], "pad": list(res.pad),
            "pivot": [round(res.pivot[0], 2), round(res.pivot[1], 2)],
            "shadow": fx.shadow, "outline": None if fx.outline is None else "#{:02x}{:02x}{:02x}".format(*fx.outline),
            "outline_width": fx.width, "colors_added": fx_colors(fx),
            "frames": [{"name": ref.name, "source": list(s), "offset": [o[0] + res.pad[0], o[1] + res.pad[1]]}
                       for ref, s, o in zip(refs, res.sources, res.offsets)]}
        meta_ref = await ctx.blobs.put_bytes(json.dumps(info_out, indent=1).encode(), name="shadow_outline.json",
                                             mime="application/json")
        ctx.progress(1, 1, label="done")
        return StepResult(outputs={"images": out, "meta": [meta_ref]},
                          meta={"fx": {k: info_out[k] for k in ("shadow", "outline", "colors_added")},
                                "pad": info_out["pad"], "pivot": info_out["pivot"], "pixel": fx.pixel})


SHADOW_OUTLINE_OPS: tuple[type[OpExecutor], ...] = (ShadowOutline,)
