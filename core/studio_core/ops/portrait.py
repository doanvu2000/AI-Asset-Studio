"""Emotion portrait set: `op.portrait_frame` (crop the head/bust square from a character image) and `op.portrait_set`
(Klein expression edits with the background removed, aligned onto the base frame -> same-size cut-outs, atlas sheet and
emotion -> file map for dialogue / visual-novel UIs)."""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import numpy as np
from PIL import Image

from ..adapters.npc import parse_expressions, slug
from ..contracts import Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png
from .npc import ALIGN_SIDE, _gray, fit_to, grid, phase_shift, shift_image, similarity

MAP_FILE = "emotion_portraits.json"
MAP_TYPE = "emotion_portraits"
FRAMES = ("bust", "head")
SOURCES = ("auto", "portrait", "full_body")
REF_BG = (128, 128, 128)
FULL_BODY_RATIO = 1.7
ALPHA_MIN = 16
# Share of the subject's height that the frame covers, by (source, frame).
SPAN = {("portrait", "bust"): 1.0, ("portrait", "head"): 0.7, ("full_body", "bust"): 0.42,
        ("full_body", "head"): 0.2}


def subject_box(img: Image.Image) -> tuple[int, int, int, int]:
    a = np.asarray(img.convert("RGBA").getchannel("A"))
    ys, xs = np.nonzero(a > ALPHA_MIN)
    if not len(xs):
        return 0, 0, img.width, img.height
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def frame_crop(img: Image.Image, frame: str, source: str, pad: float, size: int) -> tuple[Image.Image, dict[str, Any]]:
    img = img.convert("RGBA")
    x0, y0, x1, y1 = subject_box(img)
    w, h = x1 - x0, y1 - y0
    if source == "auto":
        source = "full_body" if h / max(w, 1) >= FULL_BODY_RATIO else "portrait"
    rh = max(1, round(h * SPAN[(source, frame)]))
    a = np.asarray(img.getchannel("A"))[y0:y0 + rh, x0:x1] > ALPHA_MIN
    cols = np.nonzero(a.any(axis=0))[0]
    rx0, rx1 = (x0 + int(cols.min()), x0 + int(cols.max()) + 1) if len(cols) else (x0, x1)
    side = max(8, round(max(rh, rx1 - rx0) * (1 + 2 * pad)))
    left, top = round((rx0 + rx1) / 2 - side / 2), round(y0 - side * pad)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(img.crop((left, top, left + side, top + side)), (0, 0))
    out = out.resize((size, size), Image.Resampling.LANCZOS)
    return out, {"source": source, "frame": frame, "box": [left, top, left + side, top + side]}


def flat(img: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", img.size, (*REF_BG, 255))
    bg.alpha_composite(img.convert("RGBA"))
    return bg.convert("RGB")


class PortraitFrame(OpExecutor):
    kind = "op.portrait_frame"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        ref = _images_in(inputs)[0]
        frame = str(params.get("frame") or "bust").lower()
        source = str(params.get("source") or "auto").lower()
        if frame not in FRAMES or source not in SOURCES:
            raise StepError("op_bad_param", f"frame must be one of {FRAMES}, source one of {SOURCES}")
        size = int(params.get("size", 512))
        pad = float(params.get("pad", 0.08))
        src = await _load(ctx, ref)
        base, info = await asyncio.to_thread(frame_crop, src, frame, source, pad, size)
        # Klein reads only RGB, so it gets the cut-out on neutral grey (like op.lut_prep); the alpha comes back via rmbg.
        ref_img = await asyncio.to_thread(flat, base)
        return StepResult(outputs={"images": [await _put_png(ctx, ref_img, "portrait_ref.png")],
                                   "base": [await _put_png(ctx, base, "portrait_base.png")]},
                          meta={"size": size, **info})


def align_cutout(base: Image.Image, edit: Image.Image, max_shift: float) -> tuple[Image.Image, tuple[int, int]]:
    edit = fit_to(edit.convert("RGBA"), base.size)
    ref, mov = _gray(flat(base), ALIGN_SIDE), _gray(flat(edit), ALIGN_SIDE)
    dy, dx = phase_shift(ref, mov)
    scale = base.width / ref.shape[1]
    sx, sy = round(dx * scale), round(dy * scale)
    if abs(sx) > max_shift * base.width or abs(sy) > max_shift * base.height:
        sx = sy = 0
    return shift_image(edit, sx, sy), (sx, sy)


def atlas(images: list[Image.Image]) -> tuple[Image.Image, int, int]:
    cols = min(4, len(images))
    rows = math.ceil(len(images) / cols)
    w, h = images[0].size
    sheet = Image.new("RGBA", (cols * w, rows * h), (0, 0, 0, 0))
    for i, img in enumerate(images):
        sheet.paste(img, ((i % cols) * w, (i // cols) * h))
    return sheet, cols, rows


class PortraitSet(OpExecutor):
    kind = "op.portrait_set"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        base_ref = _images_in(inputs, "base")[0]
        edits = _images_in(inputs, "edits")
        names = parse_expressions(params.get("emotions"))
        asset = slug(params.get("asset"), "portrait")
        edit_names = [n for n in names if n != "neutral"]
        if len(edit_names) != len(edits):
            raise StepError("op_bad_input", f"{len(edit_names)} emotions but {len(edits)} edited images")
        max_shift = float(params.get("max_shift", 0.06))
        min_sim = float(params.get("min_similarity", 0.55))
        base = (await _load(ctx, base_ref)).convert("RGBA")
        items: list[tuple[str, Image.Image, float | None, tuple[int, int]]] = [("neutral", base, None, (0, 0))]
        for name, ref in zip(edit_names, edits):
            img, shift = await asyncio.to_thread(align_cutout, base, await _load(ctx, ref), max_shift)
            items.append((name, img, await asyncio.to_thread(similarity, flat(base), flat(img)), shift))
        refs, rows = [], []
        for name, img, sim, shift in items:
            ref = await _put_png(ctx, img, f"{asset}_{name}.png")
            refs.append(ref)
            rows.append({"emotion": name, "file": ref.name, "hash": ref.hash, "similarity": sim, "shift": list(shift)})
        imgs = [it[1] for it in items]
        sheet, cols, nrows = await asyncio.to_thread(atlas, imgs)
        side = base.width
        for i, r in enumerate(rows):
            r["cell"] = {"col": i % cols, "row": i // cols, "x": (i % cols) * side, "y": (i // cols) * side}
        doc = {"type": MAP_TYPE, "version": 1, "asset": asset, "size": [side, side], "default": "neutral",
               "sheet": {"file": f"{asset}_sheet.png", "cols": cols, "rows": nrows, "cell": [side, side]},
               "portraits": {r["emotion"]: {k: v for k, v in r.items() if k != "emotion"} for r in rows}}
        doc_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=MAP_FILE, mime="application/json")
        prev = await _put_png(ctx, await asyncio.to_thread(grid, [it[0] for it in items], imgs,
                                                           [it[2] for it in items], min_sim),
                              f"{asset}_preview.png")
        low = [f"{r['emotion']} {r['similarity']:.2f}" for r in rows
               if r["similarity"] is not None and r["similarity"] < min_sim]
        meta: dict[str, Any] = {"portraits": len(rows), "similarity": {r["emotion"]: r["similarity"] for r in rows}}
        if low:
            meta["flagged"] = [f"portrait drifted from the base face/crop: {', '.join(low)}"]
            ctx.log(meta["flagged"][0], "warning")
        return StepResult(outputs={"images": refs, "sheet": [await _put_png(ctx, sheet, f"{asset}_sheet.png")],
                                   "preview": [prev], "map": [doc_ref]}, meta=meta)


PORTRAIT_OPS: tuple[type[OpExecutor], ...] = (PortraitFrame, PortraitSet)
