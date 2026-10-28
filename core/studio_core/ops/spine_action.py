"""One-shot combat presets for an existing rig: melee attack, hit reaction, death collapse. Same rig facts as the
walk/run cycle (spine_cycle.cycle_plan); feet stay planted through their IK targets while the hip, trunk and arms
move. Attack and hit return to setup; death holds its collapsed last pose."""
from __future__ import annotations

from typing import Any

from .spine_anim import SpecError
from .spine_cycle import JUMP_STEP, _lin, _local_vec, _smooth

# Seconds, degrees, leg lengths. `at` <= 0 is placed by the clip duration.
ACTION_DEFAULTS: dict[str, dict[str, float]] = {
    "attack": {"at": 0.0, "strike": 0.12, "swing": 110.0, "forearm": 0.5, "lean": 10.0, "step": 0.12},
    "hit": {"at": 0.0, "recoil": 25.0, "push": 0.08, "flail": 25.0},
    "death": {"at": 0.0, "fall": 0.6, "drop": 0.5, "slump": 35.0, "recoil": 12.0},
}
AT_SHARE = {"attack": 0.45, "hit": 0.15, "death": 0.15}
FRONT_TILT = 0.35


def _times(end: float, extra: list[float]) -> list[float]:
    steps = max(int(round(end / JUMP_STEP)), 8)
    return sorted({round(end * i / steps, 4) for i in range(steps + 1)} | {round(min(max(t, 0.0), end), 4)
                                                                           for t in extra})


def _clamp(v: float, bone: str, limits: dict[str, dict[str, Any]] | None) -> float:
    if limits and bone in limits:
        return min(max(v, limits[bone]["lo"]), limits[bone]["hi"])
    return v


def action_keys(plan: dict[str, Any], end: float, limits: dict[str, dict[str, Any]] | None = None
                ) -> dict[str, dict[str, list]]:
    cfg, L, kind = plan["cfg"], plan["L"], plan["kind"]
    at = cfg["at"] if cfg["at"] > 0 else AT_SHARE[kind] * end
    side = cfg["facing"] != "front"
    d = cfg["direction"]
    if kind == "attack":
        t_w = max(at - cfg["strike"], 0.05)
        tail = [at + 0.06, at + 0.35]
        if not 0.05 < t_w < at < tail[1] < end:
            raise SpecError(f"action attack: need strike {cfg['strike']:g}s < at ({at:g}s) < duration - 0.35")
        S, F, lean, step = cfg["swing"], cfg["forearm"], cfg["lean"], cfg["step"] * L
        arm = [(0.0, 0.0), (t_w, -0.55 * S), (at, 0.45 * S), (tail[0], 0.55 * S), (tail[1], 0.35 * S), (end, 0.0)]
        fore = [(0.0, 0.0), (t_w, 0.6 * F * S), (at, -0.1 * F * S), (tail[1], 0.2 * F * S), (end, 0.0)]
        bend = [(0.0, 0.0), (t_w, -0.5 * lean), (at, lean), (tail[1], 0.6 * lean), (end, 0.0)]
        hip_x = [(0.0, 0.0), (t_w, -0.4 * step), (at, step), (tail[1], 0.8 * step), (end, 0.0)]
        hip_y = [(0.0, 0.0), (t_w, -0.04 * L), (at, -0.08 * L), (end, 0.0)]
        times = _times(end, [t_w, at, *tail])
    elif kind == "hit":
        if not 0 < at < end - 0.6:
            raise SpecError(f"action hit: need 0 < at ({at:g}s) < duration - 0.6")
        r = [(0.0, 0.0), (at, 0.0), (at + 0.07, -1.0), (at + 0.3, 0.25), (at + 0.55, -0.08), (end, 0.0)]
        scale = lambda k: [(t, k * v) for t, v in r]  # noqa: E731
        arm, fore = scale(-cfg["flail"]), scale(-0.5 * cfg["flail"])
        bend, hip_x = scale(cfg["recoil"]), scale(cfg["push"] * L)
        hip_y = [(0.0, 0.0), (at, 0.0), (at + 0.07, -0.04 * L), (at + 0.3, 0.0), (end, 0.0)]
        times = _times(end, [t for t, _ in r])
    elif kind == "death":
        land = at + 0.1 + cfg["fall"]
        if not 0 < at < land + 0.25 <= end:
            raise SpecError(f"action death: need at ({at:g}s) + 0.1 + fall ({cfg['fall']:g}s) + 0.25 <= duration")
        drop, slump = cfg["drop"] * L, cfg["slump"]
        # Recoil from the blow, knees give way (gravity: slow start), small bounce on landing, then hold.
        hip_y = [(0.0, 0.0), (at + 0.1, 0.0), (land, -drop), (land + 0.1, -0.94 * drop), (land + 0.25, -drop),
                 (end, -drop)]
        hip_x = [(0.0, 0.0), (at + 0.07, -0.05 * L), (land, -0.1 * L), (end, -0.1 * L)]
        bend = [(0.0, 0.0), (at, 0.0), (at + 0.07, -cfg["recoil"]), (land, slump), (land + 0.1, 1.08 * slump),
                (land + 0.25, slump), (end, slump)]
        arm = [(0.0, 0.0), (at, 0.0), (at + 0.07, 15.0), (land, -10.0), (end, -10.0)]
        fore = [(0.0, 0.0), (at + 0.07, 10.0), (land, 0.0), (end, 0.0)]
        times = _times(end, [at, at + 0.07, at + 0.1, land, land + 0.1, land + 0.25])
    else:
        raise SpecError(f"unknown action {kind!r}")

    def gravity(pts: list[tuple[float, float]], t: float) -> float:
        # The collapse accelerates like a fall instead of easing in and out.
        if kind == "death" and at + 0.1 < t < land:
            u = (t - at - 0.1) / (land - at - 0.1)
            a, b = _smooth(pts, at + 0.1), _smooth(pts, land)
            return a + (b - a) * u * u
        return _smooth(pts, t)

    out: dict[str, dict[str, list]] = {}
    if plan["hip"]:
        keys = [(t, *_local_vec(plan, plan["hip_parent"], d * gravity(hip_x, t) if side else 0.0,
                                gravity(hip_y, t))) for t in times]
        out[plan["hip"]] = {"translate": _lin(keys, ("x", "y"))}
    arms = plan["arms"]
    lead = max(range(len(arms)), key=lambda i: arms[i]["held"]) if arms else -1
    for i, a in enumerate(arms):
        # Attack: the arm holding something (most bones under the hand) swings, the other counters it.
        share = (1.0 if i == lead else -0.3) if kind == "attack" else 1.0
        up = [(t, a["sign"] * d * share * _smooth(arm, t)) for t in times]
        lo = [(t, _clamp(a["lower_sign"] * d * (share if kind != "attack" or i == lead else 0.0)
                         * _smooth(fore, t), a["lower"], limits)) for t in times]
        out[a["upper"]] = {"rotate": _lin(up, ("value",))}
        out[a["lower"]] = {"rotate": _lin(lo, ("value",))}
    for role, share in (("spine", 0.6), ("chest", 0.4), ("head", 0.5 if kind != "attack" else -0.4)):
        tr = plan["trunk"].get(role)
        if tr:
            # Front view: a 2D rotation reads as a sideways tilt, not a forward bend, so keep it small and turn by
            # screen direction (the trunk's forward sign is meaningless there).
            k = tr["sign"] * d if side else FRONT_TILT * d
            keys = [(t, k * share * gravity(bend, t)) for t in times]
            out[tr["bone"]] = {"rotate": _lin(keys, ("value",))}
    return out
