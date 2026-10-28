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


GAME_READY_MODES = ("decimate", "remesh")
BAKE_MAPS = ("normal", "ao")


def game_ready_params(params: dict[str, Any]) -> dict[str, Any]:
    mode = str(params.get("mode", "decimate")).lower()
    if mode not in GAME_READY_MODES:
        raise ValueError(f"mode must be one of {GAME_READY_MODES}, got {mode!r}")
    target = int(params.get("target_faces") or 5000)
    if target < 100:
        raise ValueError(f"target_faces must be >= 100, got {target}")
    raw = params.get("lods", [1.0, 0.5, 0.25])
    lods = [float(x) for x in (raw.split(",") if isinstance(raw, str) else raw)]
    if not lods or lods[0] != 1.0 or any(not 0 < b < a for a, b in zip(lods, lods[1:])):
        raise ValueError(f"lods must start at 1 and strictly decrease within (0, 1], got {raw!r}")
    bake = params.get("bake", list(BAKE_MAPS))
    bake = [b.strip() for b in (bake.split(",") if isinstance(bake, str) else bake) if b and b.strip()]
    if any(b not in BAKE_MAPS for b in bake):
        raise ValueError(f"bake entries must be in {BAKE_MAPS}, got {bake!r}")
    size = int(params.get("texture_size", 1024))
    if size not in (512, 1024, 2048, 4096):
        raise ValueError(f"texture_size must be 512|1024|2048|4096, got {size}")
    return {"mode": mode, "target_faces": target, "lods": lods, "bake": bake, "texture_size": size,
            "smooth_angle": float(params.get("smooth_angle", 40)), "samples": int(params.get("samples", 16))}


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
