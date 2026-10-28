"""`op.vfx_frames`: RGBA video frames (Wan Alpha) -> flipbook cells. One-shot effects drop the empty lead-in/out and
fade their last frames; loops take the frame span whose last frame best matches its first. All cells share one
square crop around the union of visible pixels, so the effect does not jitter inside the sheet."""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from ..contracts import Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _put_png

VISIBLE = 8
MIN_LOOP = 8
# The alpha VAE leaves a faint line (alpha up to ~140) on the outer rows/cols of every frame, empty ones too; left in,
# it shows as streaks in each cell and stretches the shared crop to the full frame.
BORDER = 2


def clear_border(img: Image.Image, px: int = BORDER) -> Image.Image:
    a = np.asarray(img.convert("RGBA")).copy()
    a[:px, :, 3] = a[-px:, :, 3] = a[:, :px, 3] = a[:, -px:, 3] = 0
    return Image.fromarray(a, "RGBA")


def _small(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGBA").resize((64, 64), Image.Resampling.BILINEAR), np.float32) / 255
    return np.concatenate([a[..., :3] * a[..., 3:], a[..., 3:]], 2)


def best_loop(frames: list[Image.Image]) -> tuple[int, int, float]:
    """(start, end) with frame `end` closest to frame `start`: playing start..end-1 then wrapping is smoothest."""
    small = [_small(f) for f in frames]
    n = len(frames)
    best = (0, n, float("inf"))
    for s in range(0, max(1, n // 3)):
        for e in range(s + MIN_LOOP, n):
            d = float(np.abs(small[e] - small[s]).mean())
            # Prefer longer loops among near-equal seams.
            score = d - 1e-4 * (e - s)
            if score < best[2]:
                best = (s, e, score)
    return best[0], best[1], round(best[2], 5)


def active_span(frames: list[Image.Image]) -> tuple[int, int]:
    cover = [float((np.asarray(f.convert("RGBA"))[..., 3] > VISIBLE).mean()) for f in frames]
    peak = max(cover) if cover else 0.0
    on = [i for i, c in enumerate(cover) if c > 0.01 * peak]
    return (on[0], on[-1] + 1) if on else (0, len(frames))


def square_crop(frames: list[Image.Image], pad: float = 0.04) -> tuple[int, int, int, int]:
    box = None
    for f in frames:
        b = f.convert("RGBA").getchannel("A").point(lambda v: 255 if v > VISIBLE else 0).getbbox()
        if b:
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    w, h = frames[0].size
    if box is None:
        return 0, 0, w, h
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    side = max(box[2] - box[0], box[3] - box[1]) * (1 + 2 * pad)
    return (round(cx - side / 2), round(cy - side / 2), round(cx + side / 2), round(cy + side / 2))


def fade_tail(frames: list[Image.Image], n: int) -> list[Image.Image]:
    out = list(frames)
    n = min(n, len(out) - 1)
    for k in range(n):
        i = len(out) - n + k
        a = np.asarray(out[i].convert("RGBA")).copy()
        a[..., 3] = (a[..., 3] * (1 - (k + 1) / (n + 1))).astype(np.uint8)
        out[i] = Image.fromarray(a, "RGBA")
    return out


def flipbook(frames: list[Image.Image], mode: str, size: int, fade: int = 3) -> tuple[list[Image.Image], dict[str, Any]]:
    if len(frames) < 2:
        raise StepError("op_bad_input", "need at least 2 frames")
    meta: dict[str, Any] = {"source_frames": len(frames), "mode": mode}
    frames = [clear_border(f) for f in frames]
    if mode == "loop":
        s, e, seam = best_loop(frames)
        frames = frames[s:e]
        meta |= {"loop_start": s, "loop_end": e, "loop_seam": seam}
    else:
        s, e = active_span(frames)
        frames = fade_tail(frames[s:e], fade)
        meta |= {"start": s, "end": e}
    box = square_crop(frames)
    cells = [f.convert("RGBA").crop(box).resize((size, size), Image.Resampling.LANCZOS) for f in frames]
    meta |= {"frames": len(cells), "crop": list(box), "size": size}
    return cells, meta


class VfxFrames(OpExecutor):
    kind = "op.vfx_frames"
    version = "2"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        mode = str(params.get("mode", "oneshot")).lower()
        if mode not in ("oneshot", "loop"):
            raise StepError("op_bad_param", f"mode must be oneshot or loop, got {mode!r}")
        frames = [Image.open(ctx.blobs.path(r.hash)) for r in refs]
        cells, meta = flipbook(frames, mode, int(params.get("size", 256)), int(params.get("fade", 3)))
        out = [await _put_png(ctx, c, f"vfx_{i:03d}.png") for i, c in enumerate(cells)]
        return StepResult(outputs={"images": out}, meta=meta)
