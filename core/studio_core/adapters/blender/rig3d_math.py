"""Humanoid rig tables for blender.rig_export, stdlib only: shared by the Blender worker, the executor and tests."""
from __future__ import annotations

from typing import Any

SIDES = (("l", "Left"), ("r", "Right"))

# Internal names keep the sprite rig's arm/leg names because skin_math finds arm chains by those prefixes and
# clip_pose keys its poses by them; HUMANOID renames them once the mesh is bound.
RIG = (
    ("hips", None, "pelvis", "spine_a"),
    ("spine", "hips", "spine_a", "spine_b"),
    ("chest", "spine", "spine_b", "neck"),
    ("neck", "chest", "neck", "neck_top"),
    ("head", "neck", "neck_top", "head_top"),
    *((f"thigh_{s}", "hips", f"hip_{s}", f"knee_{s}") for s, _ in SIDES),
    *((f"shin_{s}", f"thigh_{s}", f"knee_{s}", f"ankle_{s}") for s, _ in SIDES),
    *((f"foot_{s}", f"shin_{s}", f"ankle_{s}", f"foot_tip_{s}") for s, _ in SIDES),
    *((f"collar_{s}", "chest", "neck", f"shoulder_{s}") for s, _ in SIDES),
    *((f"upper_arm_{s}", f"collar_{s}", f"shoulder_{s}", f"elbow_{s}") for s, _ in SIDES),
    *((f"forearm_{s}", f"upper_arm_{s}", f"elbow_{s}", f"wrist_{s}") for s, _ in SIDES),
    *((f"hand_{s}", f"forearm_{s}", f"wrist_{s}", f"hand_tip_{s}") for s, _ in SIDES),
)

HUMANOID = {
    "hips": "Hips", "spine": "Spine", "chest": "Chest", "neck": "Neck", "head": "Head",
    **{k: v for s, side in SIDES for k, v in (
        (f"thigh_{s}", f"{side}UpperLeg"), (f"shin_{s}", f"{side}LowerLeg"), (f"foot_{s}", f"{side}Foot"),
        (f"collar_{s}", f"{side}Shoulder"), (f"upper_arm_{s}", f"{side}UpperArm"),
        (f"forearm_{s}", f"{side}LowerArm"), (f"hand_{s}", f"{side}Hand"))},
}

UNITY_REQUIRED = ("Hips", "Spine", "Head", *(f"{side}{b}" for _, side in SIDES for b in (
    "UpperLeg", "LowerLeg", "Foot", "UpperArm", "LowerArm", "Hand")))


def _lerp(a: Any, b: Any, t: float) -> Any:
    return a + (b - a) * t


def humanoid_points(pts: dict[str, Any]) -> dict[str, Any]:
    """Adds the in-between joints the 19-joint schema lacks (spine split, top of the neck)."""
    out = dict(pts)
    out["spine_a"] = _lerp(pts["pelvis"], pts["neck"], 0.12)
    out["spine_b"] = _lerp(pts["pelvis"], pts["neck"], 0.5)
    out["neck_top"] = _lerp(pts["neck"], pts["head_top"], 0.3)
    return out


def renamed(bones: tuple = RIG) -> tuple:
    return tuple((HUMANOID[n], HUMANOID.get(p) if p else None, a, b) for n, p, a, b in bones)


def rename_pose(pose: dict[str, Any]) -> dict[str, Any]:
    return {HUMANOID[k]: v for k, v in pose.items() if k in HUMANOID}


def clip_samples(clip: dict[str, Any], fps: int) -> list[float]:
    """Normalised times keyed at scene frames 0..K: a loop's last key repeats t=0 (t=1) so engines close the cycle,
    a one-shot spans its sprite frames' duration."""
    frames, rate = int(clip["frames"]), float(clip["fps"])
    dur = (frames if clip.get("loop", True) else max(1, frames - 1)) / rate
    k = max(2, round(dur * fps))
    return [i / k for i in range(k + 1)]


def preview_keys(n_keys: int, loop: bool, count: int = 4) -> list[int]:
    last = n_keys - 1
    span = count if loop else count - 1
    return sorted({round(i * last / span) for i in range(count)})
