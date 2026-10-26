"""Procedural clip plan for blender.animate_dirs (core side, stdlib only)."""
from __future__ import annotations

import re
from typing import Any

# name -> (frames, fps, loop)
CLIP_DEFAULTS: dict[str, tuple[int, int, bool]] = {
    "walk": (8, 10, True),
    "idle": (8, 6, True),
    "run": (8, 14, True),
    "attack": (8, 12, False),
}
MIN_FRAMES, MAX_FRAMES = 2, 24


def parse_anims(value: Any) -> list[str]:
    """None/'' -> ['walk'] (the pre-clip behaviour); accepts a list or a comma/space separated string."""
    if value is None:
        items: list[Any] = []
    elif isinstance(value, str):
        items = [x for x in re.split(r"[\s,;]+", value) if x]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError(f"anims must be a list or comma string, got {value!r}")
    out: list[str] = []
    for x in items:
        name = str(x).strip().lower()
        if name not in CLIP_DEFAULTS:
            raise ValueError(f"unknown anim {x!r} (known: {', '.join(CLIP_DEFAULTS)})")
        if name not in out:
            out.append(name)
    return out or ["walk"]


def _int_param(params: dict[str, Any], key: str, default: int) -> int:
    v = params.get(key)
    if v in (None, ""):
        return default
    return int(v)


def plan_clips(params: dict[str, Any]) -> list[dict[str, Any]]:
    """`frames` keeps meaning the walk length (old recipes); `<clip>_frames` / `<clip>_fps` override per clip."""
    clips = []
    for name in parse_anims(params.get("anims")):
        frames, fps, loop = CLIP_DEFAULTS[name]
        if name == "walk":
            frames = _int_param(params, "frames", frames)
        frames = _int_param(params, f"{name}_frames", frames)
        frames = max(MIN_FRAMES, min(MAX_FRAMES, frames))
        clips.append({"name": name, "frames": frames, "fps": _int_param(params, f"{name}_fps", fps), "loop": loop})
    return clips


def expected_frames(clips: list[dict[str, Any]], n_angles: int) -> int:
    return sum(int(c["frames"]) for c in clips) * n_angles


def clip_order_key(clips: list[dict[str, Any]]):
    rank = {c["name"]: i for i, c in enumerate(clips)}
    return lambda f: (rank.get(f.get("clip", "walk"), len(rank)), f["angle"], f["frame"])
