"""Pure cleanup parameter logic, shared by the core executor and the Blender worker (stdlib only)."""
from __future__ import annotations

import re
from typing import Any

UPRIGHT_RE = re.compile(r"^([xyz])(-?(?:90|180|270))$")
ORIGINS = ("feet", "center", "keep")
DEFAULT_MERGE = 1e-5


def parse_upright(spec: Any) -> tuple[str, float] | None:
    """'none'/'auto' -> None (auto has no detector yet, so it never rotates); 'x90', 'y-90', 'z180' -> (axis, deg)."""
    s = str(spec if spec is not None else "none").strip().lower()
    if s in ("", "none", "auto"):
        return None
    m = UPRIGHT_RE.match(s)
    if not m:
        raise ValueError(f"upright must be none|auto|<x|y|z><90|-90|180|270>, got {spec!r}")
    return m.group(1), float(m.group(2))


def decimate_ratio(current_faces: int, target_faces: Any) -> float | None:
    if not target_faces or int(target_faces) <= 0 or current_faces <= int(target_faces):
        return None
    return int(target_faces) / current_faces


def height_scale(current_height: float, target_height: Any) -> float:
    if not target_height or float(target_height) <= 0 or current_height <= 0:
        return 1.0
    return float(target_height) / current_height


def merge_distance(value: Any) -> float:
    if value is True or value is None:
        return DEFAULT_MERGE
    if value is False:
        return 0.0
    return max(0.0, float(value))


def normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    origin = str(params.get("origin", "feet")).lower()
    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")
    upright = str(params.get("upright", "none")).strip().lower()
    parse_upright(upright)
    target = params.get("target_faces")
    height = params.get("height", 1.0)
    return {"target_faces": int(target) if target else 0, "height": float(height) if height else 0.0,
            "origin": origin, "upright": upright, "merge_by_distance": merge_distance(params.get("merge_by_distance")),
            "smooth_angle": float(params.get("smooth_angle", 40))}
