"""Looping locomotion (walk/run) for an existing rig: feet planted through the leg IK targets, hip bob, arms swinging
against the legs, and a check that planted feet do not slide or sink. Defaults are measured on the official
spineboy-pro and mix-and-match-pro walk/run clips, in leg lengths so they carry over to any rig size."""
from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

from .spine_anim import SpecError, _hands, hip_bone, leg_chains
from .spine_runtime import Pose

CYCLE_DEFAULTS: dict[str, dict[str, float]] = {
    "walk": {"period": 1.0, "stride": 0.8, "lift": 0.3, "contact": 0.55, "bob": 0.06, "arm_swing": 30.0,
             "forearm": 0.35, "lean": 2.0, "hip_sway": 0.0},
    "run": {"period": 0.66, "stride": 1.2, "lift": 0.6, "contact": 0.35, "bob": 0.1, "arm_swing": 50.0,
            "forearm": 0.5, "lean": 8.0, "hip_sway": 0.0},
}
KEYS_PER_PERIOD = 24
MAX_DROP = 0.2
# The official walks stretch the leg chain to 1.04x its length at the stride ends; an ankle a hair short reads fine.
OVERREACH = 1.03
# Jump: seconds and leg lengths; takeoff/land <= 0 are placed by the clip duration.
JUMP_DEFAULTS: dict[str, float] = {"takeoff": 0.0, "land": 0.0, "height": 0.6, "crouch": 0.25, "tuck": 0.15,
                                   "arm_swing": 50.0, "forearm": 0.4, "lean": 12.0}
JUMP_STEP = 1 / 30
SLIDE_FAULT = 0.06
SINK_FAULT = 0.05


def _ancestors(parent: dict[str, str | None], n: str | None) -> list[str]:
    out = []
    while n:
        out.append(n)
        n = parent.get(n)
    return out


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[-_\s\d]+", name.lower()) if t} - {"ik", "leg", "arm", "upper", "up", "target"}


def foot_drivers(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Per leg (leg_chains order): the bone to translate so the foot plants, i.e. the leg IK target climbed up to the
    last ancestor that is neither the root nor above the hip (spineboy: front-leg-target -> front-foot-target)."""
    parent = {b["name"]: b.get("parent") for b in doc["bones"]}
    hip = hip_bone(doc)
    above_hip = set(_ancestors(parent, hip))
    target = {c["name"]: c["target"] for c in doc.get("ik") or []}
    out = []
    for leg in leg_chains(doc):
        d = target[leg["ik"]]
        while parent.get(d) and parent.get(parent[d]) and parent[d] not in above_hip:
            d = parent[d]
        if hip and hip in _ancestors(parent, d):
            continue
        out.append({**leg, "target": target[leg["ik"]], "driver": d, "parent": parent.get(d)})
    return out


def arm_chains(doc: dict[str, Any]) -> list[dict[str, str]]:
    parent = {b["name"]: b.get("parent") for b in doc["bones"]}
    out = []
    for h in sorted(_hands(doc)):
        lower = parent.get(h)
        upper = parent.get(lower) if lower else None
        if lower and upper:
            out.append({"upper": upper, "lower": lower, "hand": h})
    return out


def _forward_sign(pose: Pose, parent: dict[str, str | None], bone: str) -> float:
    # +1 when a CCW local rotation moves the bone's tip toward +x in the world (mirrored parents flip it).
    m, _ = pose.world[bone]
    p = parent.get(bone)
    det = float(np.linalg.det(pose.world[p][0])) if p else 1.0
    tip = m @ np.array([1.0, 0.0])
    return 1.0 if -tip[1] * (1 if det >= 0 else -1) >= 0 else -1.0


def _to_local(pose: Pose, parent: str | None, v: np.ndarray) -> np.ndarray:
    return np.linalg.solve(pose.world[parent][0], v) if parent else v


def cycle_plan(doc: dict[str, Any], spec: dict[str, Any], torso: dict[str, Any] | None = None
               ) -> dict[str, Any] | None:
    """Rig facts the preset needs (feet, arms paired to legs, hip, trunk), or None without a `cycle`, `jump` or
    `action` in the spec. A cycle loops; a jump or action is one-shot (death holds its last pose)."""
    from .spine_action import ACTION_DEFAULTS

    raw, key = spec.get("cycle"), "cycle"
    if isinstance(raw, dict):
        kind = str(raw.get("type", "walk")).lower()
        if kind not in CYCLE_DEFAULTS:
            raise SpecError(f"cycle.type must be one of {', '.join(CYCLE_DEFAULTS)}")
    elif isinstance(spec.get("jump"), dict):
        raw, key, kind = spec["jump"], "jump", "jump"
    elif isinstance(spec.get("action"), dict):
        raw, key = spec["action"], "action"
        kind = str(raw.get("type", "")).lower()
        if kind not in ACTION_DEFAULTS:
            raise SpecError(f"action.type must be one of {', '.join(ACTION_DEFAULTS)}")
    else:
        return None
    feet = foot_drivers(doc)
    if len(feet) < 2:
        raise SpecError(f"{key} needs two leg IK chains whose targets do not hang under the hip; key the legs "
                        "with 'keys' instead")
    defaults = (JUMP_DEFAULTS if kind == "jump" else ACTION_DEFAULTS[kind] if key == "action"
                else CYCLE_DEFAULTS[kind])
    cfg: dict[str, Any] = {**defaults, **{k: float(v) for k, v in raw.items() if k in defaults}}
    cfg["facing"] = str(raw.get("facing", "side")).lower()
    cfg["direction"] = -1.0 if float(raw.get("direction", 1)) < 0 else 1.0
    pose = Pose(doc)
    parent = {b["name"]: b.get("parent") for b in doc["bones"]}
    L = sum(f["length"] for f in feet) / len(feet)
    arms = arm_chains(doc)
    # Same-side arm swings against its leg: pair by shared name tokens (front/rear, l/r), else by setup x.
    legs_by_x = sorted(range(len(feet)), key=lambda i: feet[i]["x"])
    arms_by_x = sorted(arms, key=lambda a: float(pose.world[a["upper"]][1][0]))
    paired = []
    for j, a in enumerate(arms_by_x[:2]):
        score = [len(_tokens(a["upper"]) & (_tokens(feet[i]["ik"]) | _tokens(feet[i]["bones"][0])))
                 for i in range(len(feet))]
        leg = max(range(len(feet)), key=lambda i: score[i]) if max(score) else legs_by_x[min(j, len(feet) - 1)]
        paired.append({**a, "leg": leg, "sign": _forward_sign(pose, parent, a["upper"]),
                       "lower_sign": _forward_sign(pose, parent, a["lower"]),
                       "held": sum(1 for n in parent if a["hand"] in _ancestors(parent, parent.get(n)))})
    hip = (torso or {}).get("hip") or hip_bone(doc)
    trunk = {r: (torso or {}).get(r) for r in ("spine", "chest", "head")}
    return {"kind": kind, "loop": key == "cycle", "hold": kind == "death", "cfg": cfg, "L": L, "hip": hip,
            "legs": [{"L": f["length"], "d": (pose.world[f["target"]][1] - pose.world[f["bones"][0]][1]).tolist()}
                     for f in feet],
            "hip_parent": parent.get(hip) if hip else None,
            "feet": [{"driver": f["driver"], "parent": f["parent"], "ik": f["ik"]} for f in feet],
            "arms": paired,
            "trunk": {r: {"bone": b, "sign": _forward_sign(pose, parent, b)} for r, b in trunk.items() if b},
            "inv": {n: np.linalg.inv(pose.world[n][0]).tolist()
                    for n in {f["parent"] for f in feet} | {parent.get(hip) if hip else None} if n}}


def _local_vec(plan: dict[str, Any], parent: str | None, x: float, y: float) -> tuple[float, float]:
    if not parent or parent not in plan["inv"]:
        return x, y
    v = np.array(plan["inv"][parent]) @ np.array([x, y])
    return float(v[0]), float(v[1])


def _reach(legs: list[dict[str, Any]], half: float, L: float) -> tuple[float, float]:
    """(half stride, hip drop): rigs drawn with straight legs reach a stride only with the hip lowered so that the
    thigh root stays within one leg length of both stride ends; past MAX_DROP the stride shrinks instead."""
    def need(h: float) -> float:
        return max(0.0, *(abs(g["d"][1]) - math.sqrt(max((OVERREACH * g["L"]) ** 2 - (g["d"][0] + e) ** 2, 0.0))
                          for g in legs for e in (h, -h)))

    lo, hi = 0.0, half
    if need(half) > MAX_DROP * L:
        for _ in range(30):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if need(mid) <= MAX_DROP * L else (lo, mid)
        half = lo
    return half, need(half)


def _foot(phase: float, c: float, stride: float, lift: float) -> tuple[float, float]:
    """World offset of a foot at cycle phase [0, 1): planted from 0 to `c` sliding back at constant speed (the
    ground moves under an in-place cycle), then swung forward on an eased arc."""
    p = phase % 1.0
    if p < c:
        return stride * (0.5 - p / c), 0.0
    u = (p - c) / (1 - c)
    e = u * u * (3 - 2 * u)
    return stride * (e - 0.5), lift * math.sin(math.pi * u) ** 1.2


def _lin(keys: list[tuple[float, ...]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    # Dense keys with linear interpolation: the planted foot moves at exactly constant speed.
    return [{"time": round(k[0], 4), **{f: round(v, 3) for f, v in zip(fields, k[1:])}} for k in keys]


def cycle_keys(plan: dict[str, Any], end: float, limits: dict[str, dict[str, Any]] | None = None
               ) -> dict[str, dict[str, list]]:
    if plan["kind"] == "jump":
        return jump_keys(plan, end, limits)
    if not plan["loop"]:
        from .spine_action import action_keys

        return action_keys(plan, end, limits)
    cfg, L = plan["cfg"], plan["L"]
    period = cfg["period"]
    n = end / period
    if period <= 0 or abs(n - round(n)) > 1e-3 or round(n) < 1:
        raise SpecError(f"cycle: duration {end:g}s must be a whole number of periods ({period:g}s)")
    side = cfg["facing"] != "front"
    # Side view: each stride is centred under its thigh root (as in the official walks), not on the setup foot.
    center = [-g["d"][0] if side else 0.0 for g in plan["legs"]]
    legs = [{**g, "d": [g["d"][0] + c0, g["d"][1]]} for g, c0 in zip(plan["legs"], center)]
    half, drop = _reach(legs, cfg["stride"] * L / 2 if side else 0.0, L)
    stride = 2 * half * cfg["direction"]
    lift, c, bob = cfg["lift"] * L, min(max(cfg["contact"], 0.2), 0.8), cfg["bob"] * L
    steps = int(round(n)) * KEYS_PER_PERIOD
    times = [end * i / steps for i in range(steps + 1)]
    out: dict[str, dict[str, list]] = {}

    for i, f in enumerate(plan["feet"][:2]):
        keys = []
        for t in times:
            x, y = _foot(t / period + 0.5 * i, c, stride, lift)
            keys.append((t, *_local_vec(plan, f["parent"], x + center[i], y)))
        out.setdefault(f["driver"], {})["translate"] = _lin(keys, ("x", "y"))

    if plan["hip"]:
        run = plan["kind"] == "run"
        low = c * (0.5 if run else 0.2)
        keys = []
        for t in times:
            w = math.cos(4 * math.pi * (t / period - low))
            y = -drop + (-bob * w if run else -bob * (1 + w) / 2)
            # Front-facing: the weight shifts over the planted foot.
            x = 0.0 if side else cfg["hip_sway"] * L * math.cos(2 * math.pi * (t / period - c / 2))
            keys.append((t, *_local_vec(plan, plan["hip_parent"], x, y)))
        out.setdefault(plan["hip"], {})["translate"] = _lin(keys, ("x", "y"))

    for a in plan["arms"]:
        lag = 0.04
        swing = cfg["arm_swing"] * (1 if side else 0.5)
        up, lo = [], []
        for t in times:
            ph = 2 * math.pi * (t / period + 0.5 * a["leg"] - lag)
            up.append((t, -a["sign"] * cfg["direction"] * swing * math.cos(ph)))
            v = -a["lower_sign"] * cfg["direction"] * swing * cfg["forearm"] * math.cos(ph - 2 * math.pi * 0.08)
            if limits and a["lower"] in limits:
                v = min(max(v, limits[a["lower"]]["lo"]), limits[a["lower"]]["hi"])
            lo.append((t, v))
        out.setdefault(a["upper"], {})["rotate"] = _lin(up, ("value",))
        out.setdefault(a["lower"], {})["rotate"] = _lin(lo, ("value",))

    lean = cfg["lean"] * cfg["direction"] if side else 0.0
    for role, share in (("spine", 0.6), ("chest", 0.4), ("head", -0.5)):
        tr = plan["trunk"].get(role)
        if tr and abs(lean) > 1e-6:
            # Leaning forward means the trunk's tip moves toward the facing side; the head levels the gaze again.
            v = tr["sign"] * lean * share
            out.setdefault(tr["bone"], {})["rotate"] = [{"time": 0.0, "value": round(v, 3)},
                                                        {"time": round(end, 4), "value": round(v, 3)}]
    return out


def _smooth(pts: list[tuple[float, float]], t: float) -> float:
    """Value at `t` through (time, value) keys, eased in and out between neighbours."""
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
            return v0 + (v1 - v0) * u * u * (3 - 2 * u)
    return pts[-1][1]


def jump_times(cfg: dict[str, Any], end: float) -> tuple[float, float, float, float]:
    """(deepest crouch, takeoff, landing, deepest absorb) in seconds."""
    off = cfg["takeoff"] if cfg["takeoff"] > 0 else 0.35 * end
    land = cfg["land"] if cfg["land"] > off else off + 0.3 * end
    if not 0 < off < land < end - 0.2:
        raise SpecError(f"jump: need 0 < takeoff ({off:g}s) < land ({land:g}s) < duration - 0.2 ({end - 0.2:g}s)")
    return off - min(0.15, 0.45 * off), off, land, land + min(0.12, 0.3 * (end - land))


def jump_keys(plan: dict[str, Any], end: float, limits: dict[str, dict[str, Any]] | None = None
              ) -> dict[str, dict[str, list]]:
    """In-place jump: crouch with the feet planted, hip launches to full leg reach, ballistic arc with the knees
    tucked, landing absorbed by a second crouch, settle to setup."""
    cfg, L = plan["cfg"], plan["L"]
    side = cfg["facing"] != "front"
    t_c, t_off, t_land, t_abs = jump_times(cfg, end)
    crouch = min(max(cfg["crouch"], 0.0), 0.4) * L
    # Takeoff extends the legs only as far as they reach from setup (no detached feet), at most a tenth of a leg.
    reach = min(math.sqrt(max((OVERREACH * g["L"]) ** 2 - g["d"][0] ** 2, 0.0)) - abs(g["d"][1])
                for g in plan["legs"])
    ext = min(max(reach, 0.0), 0.1 * L)
    height, tuck = max(cfg["height"], 0.0) * L, max(cfg["tuck"], 0.0) * L
    before = [(0.0, 0.0), (t_c, -crouch), (t_off, ext)]
    after = [(t_land, ext), (t_abs, -0.8 * crouch), (end, 0.0)]

    def arc(t: float) -> float:
        u = (t - t_off) / (t_land - t_off)
        return 4 * height * u * (1 - u)

    def hip_y(t: float) -> float:
        # The push accelerates into takeoff and the landing decelerates out of it: no stop at either contact.
        if t_c < t <= t_off:
            u = (t - t_c) / (t_off - t_c)
            return -crouch + (ext + crouch) * u * u
        if t_land <= t < t_abs:
            u = (t - t_land) / (t_abs - t_land)
            return ext - (ext + 0.8 * crouch) * (1 - (1 - u) ** 2)
        if t_off < t < t_land:
            return ext + arc(t)
        return _smooth(before if t <= t_off else after, t)

    def feet_y(t: float) -> float:
        if not t_off < t < t_land:
            return 0.0
        # Tucked knees pull the feet up toward the hip, so the feet rise more than the hip does.
        return arc(t) + tuck * math.sin(math.pi * (t - t_off) / (t_land - t_off))

    steps = max(int(round(end / JUMP_STEP)), 8)
    times = sorted({round(end * i / steps, 4) for i in range(steps + 1)} | {round(x, 4) for x in (t_c, t_off,
                                                                                                    t_land, t_abs)})
    out: dict[str, dict[str, list]] = {}
    for f in plan["feet"][:2]:
        keys = [(t, *_local_vec(plan, f["parent"], 0.0, feet_y(t))) for t in times]
        out.setdefault(f["driver"], {})["translate"] = _lin(keys, ("x", "y"))
    if plan["hip"]:
        keys = [(t, *_local_vec(plan, plan["hip_parent"], 0.0, hip_y(t))) for t in times]
        out.setdefault(plan["hip"], {})["translate"] = _lin(keys, ("x", "y"))

    swing = cfg["arm_swing"] * (1 if side else 0.5)
    # Arms load back in the crouch, throw forward-up through takeoff, drift down in the air, brace on landing.
    arm = [(0.0, 0.0), (t_c, -swing), (t_off + 0.25 * (t_land - t_off), 1.2 * swing), (t_land, 0.5 * swing),
           (t_abs, 0.3 * swing), (end, 0.0)]
    trail = [arm[0], *((t + 0.06, v) for t, v in arm[1:-1]), arm[-1]]
    for a in plan["arms"]:
        up = [(t, a["sign"] * cfg["direction"] * _smooth(arm, t)) for t in times]
        lo = []
        for t in times:
            v = a["lower_sign"] * cfg["direction"] * cfg["forearm"] * _smooth(trail, t)
            if limits and a["lower"] in limits:
                v = min(max(v, limits[a["lower"]]["lo"]), limits[a["lower"]]["hi"])
            lo.append((t, v))
        out.setdefault(a["upper"], {})["rotate"] = _lin(up, ("value",))
        out.setdefault(a["lower"], {})["rotate"] = _lin(lo, ("value",))

    lean = cfg["lean"] * cfg["direction"] if side else 0.0
    bend = [(0.0, 0.0), (t_c, lean), (t_off, -0.3 * lean), (t_land, 0.0), (t_abs, 0.7 * lean), (end, 0.0)]
    for role, share in (("spine", 0.6), ("chest", 0.4), ("head", -0.5)):
        tr = plan["trunk"].get(role)
        if tr and abs(lean) > 1e-6:
            keys = [(t, tr["sign"] * share * _smooth(bend, t)) for t in times]
            out.setdefault(tr["bone"], {})["rotate"] = _lin(keys, ("value",))
    return out


def foot_contact(doc: dict[str, Any], anim: str, step: float = 1 / 30) -> dict[str, Any] | None:
    """Planted-foot check over a clip, per leg IK target: `slide` = how far a planted foot strays from moving in a
    straight line at constant speed (0 for a foot that stays put or a cycle's ground sliding back), `sink` = how far
    it goes below its setup height; both in px and as a fraction of the leg length."""
    a = (doc.get("animations") or {}).get(anim)
    target = {c["name"]: c["target"] for c in doc.get("ik") or []}
    feet = [{**leg, "target": target[leg["ik"]]} for leg in leg_chains(doc)]
    if a is None or not feet:
        return None
    end = max((float(k.get("time", 0)) for tl in (a.get("bones") or {}).values() for tr in tl.values() for k in tr),
              default=0.0)
    if end <= 0:
        return None
    setup = Pose(doc)
    count = int(end / step + 1e-6) + 1
    poses = [Pose(doc, anim, min(i * step, end)) for i in range(count)]
    worst: dict[str, Any] = {"slide": 0.0, "sink": 0.0}
    rows = []
    for f in feet:
        b = f["target"]
        L = max(f["length"], 1e-6)
        pts = np.array([p.world[b][1] for p in poses])
        y0 = float(setup.world[b][1][1])
        ground = pts[:, 1].min()
        planted = pts[:, 1] <= ground + 0.02 * L
        slide, at = 0.0, 0.0
        i = 0
        while i < count:
            if not planted[i]:
                i += 1
                continue
            j = i
            while j + 1 < count and planted[j + 1]:
                j += 1
            if j - i >= 2:
                ts = np.arange(i, j + 1) * step
                xs = pts[i:j + 1, 0]
                fit = np.polyval(np.polyfit(ts, xs, 1), ts)
                k = int(np.abs(xs - fit).argmax())
                if abs(xs[k] - fit[k]) > slide:
                    slide, at = float(abs(xs[k] - fit[k])), round(float(ts[k]), 3)
            i = j + 1
        sink = max(0.0, y0 - float(ground))
        rows.append({"bone": b, "slide": round(slide, 2), "slide_ratio": round(slide / L, 3), "time": at,
                     "sink": round(sink, 2), "sink_ratio": round(sink / L, 3)})
        worst["slide"] = max(worst["slide"], slide / L)
        worst["sink"] = max(worst["sink"], sink / L)
    return {"feet": rows, "slide_ratio": round(worst["slide"], 3), "sink_ratio": round(worst["sink"], 3),
            "fault": worst["slide"] > SLIDE_FAULT or worst["sink"] > SINK_FAULT}

