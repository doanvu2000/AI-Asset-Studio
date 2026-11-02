"""Removes the patch of grass / ground a matting model keeps at a character's feet, plus loose specks."""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from ..contracts import Outputs, StepContext, StepResult
from .executors import OpExecutor, _images_in, _load, _out_name, _put_png

ALPHA_MIN = 128
# A character that is itself mostly green (orc, slime) must keep its body.
MAX_GREEN_SHARE = 0.2
# Grass sits at the very feet; a wider band ate green-tinted chain mail around the belt.
FEET_BAND = 0.35
MAX_PATCH_SHARE = 0.2
SPECK_SHARE = 0.01
RIM_PX = 4
# Opening width as a share of sprite height: the grass outline is a thin ring, a shoe sole is not.
THICK_SHARE = 0.03
RIM_DARK = 90
_N8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def _label(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    h, w = mask.shape
    seen = np.zeros_like(mask)
    comps = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        seen[y, x] = True
        q, pts = deque([(int(y), int(x))]), []
        while q:
            cy, cx = q.popleft()
            pts.append((cy, cx))
            for dy, dx in _N8:
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        comps.append(pts)
    return comps


def _greenish(rgb: np.ndarray, margin: int) -> np.ndarray:
    r, g, b = (rgb[..., i].astype(np.int16) for i in range(3))
    return (g > r + margin) & (g > b + margin)


def _thick(mask: np.ndarray, height: int) -> np.ndarray:
    k = 2 * max(1, round(height * THICK_SHARE / 2)) + 1
    im = Image.fromarray(mask.astype(np.uint8) * 255)
    return np.asarray(im.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))) > 127


def strip_ground(img: Image.Image, margin: int = 12) -> tuple[Image.Image, dict[str, int]]:
    arr = np.array(img.convert("RGBA"))
    opaque = arr[..., 3] >= ALPHA_MIN
    total = int(opaque.sum())
    stats = {"ground_px": 0, "specks_px": 0}
    if not total:
        return Image.fromarray(arr, "RGBA"), stats
    green = _greenish(arr[..., :3], margin) & opaque
    drop = np.zeros_like(opaque)
    floor = 0.0
    ys = np.nonzero(opaque.any(axis=1))[0]
    top, bottom = int(ys[0]), int(ys[-1])
    if green.sum() <= MAX_GREEN_SHARE * total:
        floor = top + (bottom - top + 1) * (1 - FEET_BAND)
        for pts in _label(green):
            py = [p[0] for p in pts]
            if min(py) >= floor and len(pts) <= MAX_PATCH_SHARE * total:
                drop[tuple(zip(*pts))] = True
        # The patch's own dark outline and anti-aliased rim are not green enough to seed; they sit within a few px of it.
        # Only pixels outside the thick body go: a dark shoe sole touching the grass must stay.
        thick = _thick(opaque & ~drop, bottom - top + 1)
        near = drop.copy()
        for _ in range(RIM_PX):
            grown = near.copy()
            for dy, dx in _N8:
                grown |= np.roll(np.roll(near, dy, 0), dx, 1)
            near = grown
        dark = arr[..., :3].max(axis=2) < RIM_DARK
        faint = (arr[..., 3] > 0) & ~opaque
        drop |= near & ((opaque & (dark | _greenish(arr[..., :3], 0)) & ~thick) | faint)
    stats["ground_px"] = int(drop.sum())
    arr[drop, 3] = 0
    keep = opaque & ~drop
    comps = _label(keep)
    if drop.any():
        thick = _thick(keep, bottom - top + 1)
    if comps:
        biggest = max(len(c) for c in comps)
        for pts in comps:
            low = drop.any() and min(py for py, _ in pts) >= floor and len(pts) < MAX_PATCH_SHARE * total and not thick[tuple(zip(*pts))].any()
            if len(pts) < SPECK_SHARE * biggest or (low and len(pts) < biggest):
                arr[tuple(zip(*pts))[0], tuple(zip(*pts))[1], 3] = 0
                stats["specks_px"] += len(pts)
    return Image.fromarray(arr, "RGBA"), stats


class GroundStrip(OpExecutor):
    kind = "op.ground_strip"
    version = "3"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        margin = int(params.get("margin") or 12)
        out, stats = [], []
        for i, ref in enumerate(_images_in(inputs)):
            res, st = await asyncio.to_thread(strip_ground, await _load(ctx, ref), margin)
            out.append(await _put_png(ctx, res, _out_name(ref, i, "clean")))
            stats.append(st)
        return StepResult(outputs={"images": out}, meta={"stripped": stats})


GROUND_STRIP_OPS: tuple[type[OpExecutor], ...] = (GroundStrip,)
