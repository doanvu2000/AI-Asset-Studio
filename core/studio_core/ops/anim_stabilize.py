"""Animation stabiliser: feet pinned to one pivot, clip scale matched to a reference, horizontal jitter smoothed, odd frames flagged."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from ..contracts import Outputs, StepContext, StepError, StepResult
from .engine_pack import _body_height, _match_scale
from .executors import OpExecutor, _images_in, _load, _out_name, _put_png

ALPHA_MIN = 16
# Only the lowest slice of the body decides where the feet are; a swung arm or weapon must not drag the pivot.
FOOT_BAND = 0.1
# Parts narrower than this share of the figure height (blades, staffs) are not feet; small sprites skip the opening.
THIN_WIDTH = 0.04
THIN_MIN_HEIGHT = 64


@dataclass
class FrameInfo:
    index: int
    clip: str
    scale: float = 1.0
    foot: tuple[float, float] | None = None
    flagged: str | None = None
    jitter_x: float = 0.0
    offset: tuple[int, int] = (0, 0)


@dataclass
class StabilizeResult:
    images: list[Image.Image]
    kept: list[int]
    canvas: tuple[int, int]
    pivot: tuple[int, int]
    frames: list[FrameInfo] = field(default_factory=list)


def clip_name(name: str | None) -> str:
    stem = (name or "").rsplit(".", 1)[0]
    return re.sub(r"[\s_.\-]*\d+$", "", stem) or "clip"


def body_foot(img: Image.Image) -> tuple[float, float] | None:
    """Ground contact (x centre of the feet, bottom y) from the thick body only: a blade or staff pointing down is
    thinner than the legs, so a horizontal-width opening drops it before the lowest rows are read."""
    a = np.asarray(img.getchannel("A")) >= ALPHA_MIN
    ys = np.nonzero(a.any(axis=1))[0]
    if not len(ys):
        return None
    y0, y1 = int(ys[0]), int(ys[-1]) + 1
    k = 2 * round((y1 - y0) * THIN_WIDTH / 2) + 1 if y1 - y0 >= THIN_MIN_HEIGHT else 1
    if k > 1:
        im = Image.fromarray(a.astype(np.uint8) * 255)
        thick = np.asarray(im.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))) > 127
        if thick.any():
            a = thick
            ys = np.nonzero(a.any(axis=1))[0]
            y0, y1 = int(ys[0]), int(ys[-1]) + 1
    band = np.nonzero(a[max(y0, y1 - max(1, round((y1 - y0) * FOOT_BAND))):y1].any(axis=0))[0]
    return float(band.mean() + 0.5), float(y1)


def _stats(img: Image.Image) -> tuple[float, float, float, float, float] | None:
    a = np.asarray(img.getchannel("A")) >= ALPHA_MIN
    if not a.any():
        return None
    ys, xs = np.nonzero(a)
    foot = body_foot(img)
    return float(a.sum()), float(xs.max() + 1 - xs.min()), float(ys.max() + 1 - ys.min()), foot[0], foot[1]


def smooth(values: np.ndarray, window: int, method: str) -> np.ndarray:
    values = values.astype(float)
    if window <= 1 or len(values) < 2:
        return values
    if method == "ema":
        k = 2.0 / (window + 1)
        fwd, bwd = values.copy(), values.copy()
        for i in range(1, len(values)):
            fwd[i] = fwd[i - 1] + k * (values[i] - fwd[i - 1])
        for i in range(len(values) - 2, -1, -1):
            bwd[i] = bwd[i + 1] + k * (values[i] - bwd[i + 1])
        return (fwd + bwd) / 2
    r = window // 2
    padded = np.pad(values, r, mode="edge")
    return np.array([np.median(padded[i:i + 2 * r + 1]) for i in range(len(values))])


def anim_stabilize(frames: list[Image.Image], names: list[str | None] | None = None, ref_height: int = 0,
                   smooth_window: int = 5, method: str = "median", outlier: float = 0.35,
                   drop_outliers: bool = False, pixel: bool = False) -> StabilizeResult:
    if not frames:
        raise ValueError("no frames")
    if method not in ("median", "ema"):
        raise ValueError("method must be median or ema")
    if smooth_window < 0 or outlier <= 0:
        raise ValueError("smooth must be >= 0 and outlier > 0")
    names = names or [None] * len(frames)
    clips: dict[str, list[int]] = {}
    for i, n in enumerate(names):
        clips.setdefault(clip_name(n), []).append(i)
    infos = [FrameInfo(i, clip_name(names[i])) for i in range(len(frames))]
    scaled = [f.convert("RGBA") for f in frames]
    stats: list[Any] = [None] * len(frames)
    resample = Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS
    for idx in clips.values():
        k = _match_scale([scaled[i] for i in idx], ref_height) if ref_height else None
        for i in idx:
            if k and abs(k - 1) > 1e-3:
                infos[i].scale = k
                scaled[i] = scaled[i].resize((max(1, round(scaled[i].width * k)), max(1, round(scaled[i].height * k))),
                                             resample)
            stats[i] = _stats(scaled[i])
        good = [i for i in idx if stats[i]]
        med = np.median([stats[i][:3] for i in good], axis=0) if good else None
        for i in idx:
            if not stats[i]:
                infos[i].flagged = "empty"
                continue
            infos[i].foot = (stats[i][3], stats[i][4])
            dev = np.abs(np.array(stats[i][:3]) / med - 1)
            if dev.max() > outlier:
                infos[i].flagged = ("area", "width", "height")[int(dev.argmax())]
        inl = [i for i in idx if infos[i].foot and not infos[i].flagged]
        if not inl:
            continue
        fx = np.array([infos[i].foot[0] for i in inl])
        c = float(np.median(fx))
        sm = smooth(fx, smooth_window, method) if smooth_window else np.full(len(fx), c)
        for i in idx:
            if not infos[i].foot:
                continue
            drift = float(np.interp(i, inl, sm - c)) if smooth_window else 0.0
            infos[i].jitter_x = round(float(np.interp(i, inl, fx - sm)) if smooth_window else infos[i].foot[0] - c, 2)
            infos[i].offset = (round(drift - infos[i].foot[0]), 0)
    keep = [i for i in range(len(frames)) if not (drop_outliers and infos[i].flagged)]
    placed = [i for i in keep if infos[i].foot]
    if not placed:
        raise ValueError("no frame has visible pixels")
    minx = min(infos[i].offset[0] for i in placed)
    maxx = max(infos[i].offset[0] + scaled[i].width for i in placed)
    top = max(round(infos[i].foot[1]) for i in placed)
    bottom = max(scaled[i].height - round(infos[i].foot[1]) for i in placed)
    size = (maxx - minx, top + bottom)
    out = []
    for i in keep:
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        if infos[i].foot:
            infos[i].offset = (infos[i].offset[0] - minx, top - round(infos[i].foot[1]))
            canvas.paste(scaled[i], infos[i].offset)
        out.append(canvas)
    return StabilizeResult(out, keep, size, (-minx, top), infos)


def _flag(v: Any) -> bool:
    return v not in (False, "false", "0", 0, None)


class AnimStabilize(OpExecutor):
    kind = "op.anim_stabilize"
    version = "2"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        try:
            smooth_window = int(params.get("smooth", 5))
            method = str(params.get("method") or "median")
            outlier = float(params.get("outlier") or 0.35)
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        ref_h = 0
        if inputs.get("reference"):
            ref_h = _body_height(await _load(ctx, inputs["reference"][0]))
        imgs = []
        for i, ref in enumerate(refs):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            imgs.append(await _load(ctx, ref))
            ctx.progress(i + 1, len(refs) * 2, label="load")
        try:
            res = await asyncio.to_thread(anim_stabilize, imgs, [r.name for r in refs], ref_h, smooth_window, method,
                                          outlier, _flag(params.get("drop_outliers")), _flag(params.get("pixel")))
        except ValueError as e:
            raise StepError("op_bad_params", str(e)) from e
        out = []
        for n, (i, img) in enumerate(zip(res.kept, res.images)):
            out.append(await _put_png(ctx, img, _out_name(refs[i], i, "stab")))
            ctx.progress(len(refs) + n + 1, len(refs) * 2, label="save")
        flagged = [f for f in res.frames if f.flagged]
        info = {
            "width": res.canvas[0], "height": res.canvas[1], "pivot": list(res.pivot),
            "reference_height": ref_h or None, "smooth": smooth_window, "method": method, "outlier": outlier,
            "dropped": [refs[f.index].name for f in flagged if f.index not in res.kept],
            "frames": [{"name": refs[f.index].name, "clip": f.clip, "scale": round(f.scale, 4),
                        "foot": None if f.foot is None else [round(f.foot[0], 2), round(f.foot[1], 2)],
                        "jitter_x": f.jitter_x, "offset": list(f.offset), "flagged": f.flagged,
                        "kept": f.index in res.kept} for f in res.frames]}
        meta_ref = await ctx.blobs.put_bytes(json.dumps(info, indent=1).encode(), name="anim_stabilize.json",
                                             mime="application/json")
        ctx.progress(1, 1, label="done")
        return StepResult(outputs={"images": out, "meta": [meta_ref]},
                          meta={"flagged": len(flagged), "dropped": len(info["dropped"]), "pivot": info["pivot"],
                                "clips": len({f.clip for f in res.frames})})


ANIM_STABILIZE_OPS: tuple[type[OpExecutor], ...] = (AnimStabilize,)
