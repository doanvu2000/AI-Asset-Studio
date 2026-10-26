"""Clip block layout for op.sheet: one row per (clip, direction), one column per frame (meta contract §4.2)."""
from __future__ import annotations

from typing import Any


def clip_layout(clips: list[dict[str, Any]], angles: list[float], count: int) -> tuple[
        list[int | None], int, list[dict[str, Any]], list[float]]:
    """Frames arrive clip-major, then direction, then frame. Returns (cell -> frame index or None for padding,
    cols, clips meta, row_angles)."""
    if not clips or not angles:
        raise ValueError("clips and angles must be non-empty")
    dirs = len(angles)
    need = sum(int(c["frames"]) for c in clips) * dirs
    if need != count:
        raise ValueError(f"clips expect {need} frames ({dirs} directions), got {count}")
    cols = max(int(c["frames"]) for c in clips)
    slots: list[int | None] = []
    meta: list[dict[str, Any]] = []
    row_angles: list[float] = []
    src = 0
    for c in clips:
        n = int(c["frames"])
        meta.append({"name": c["name"], "row0": len(row_angles), "rows": dirs, "frames": n,
                     "fps": c.get("fps", 10), "loop": bool(c.get("loop", True))})
        for a in angles:
            slots.extend(range(src, src + n))
            slots.extend([None] * (cols - n))
            src += n
            row_angles.append(a)
    return slots, cols, meta, row_angles
