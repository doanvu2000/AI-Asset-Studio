"""Add a procedural animation to an existing Spine 4.2 skeleton from a motion spec, and summarise a rig for prompts.

Motion spec (port of spine-test tools/gen_sway.py): a normalised driver `sway_signal` [[t, -1..1]] moves hip
(shift/dip/tilt) and leans spine/chest/neck/head with lag; `arm_keys` are explicit rotate offsets; `secondary`
chains follow the driver with growing gain and delay; `blinks` scale the eye bones. Every track starts and ends
at setup pose.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

import numpy as np

from .spine_runtime import Pose

DEFAULT_BONES = {"hip": "hip", "spine": "spine", "chest": "chest", "neck": "neck", "head": "head",
                 "eyes": ["eye_L", "eye_R"]}

MOTION_SPEC_SEMANTICS = (
    "Spine is y-up, positive rotation = CCW; rotate/translate keys are offsets from setup pose, scale keys are "
    "absolute. Spec: sway_signal = normalized driver keys [time, -1..1]; hip translate x = hip.shift_x*s, "
    "y = -hip.dip_y*|s|; hip rotate = hip.tilt*s; spine/chest/neck/head follow s with upper_body gains and lag; "
    "arm_keys = {bone: [[time, rotate offset], ...]} for any bone; secondary chains: rotate = "
    "gain*(1+falloff*i)*s delayed lag*(i+1); blinks = times of eye close (eye scaleX -> blink_closed_scale). "
    "Every key list starts at time 0 with value 0 and ends at or before duration-0.3 with value 0. "
    "arm_lag = {bone: seconds} delays that bone's arm_keys (follow-through). Joints marked 'hard seam' in the rig "
    "(separate images or single-bone meshes, e.g. elbow, wrist) are clamped to their max bend (default 20 deg, see "
    "joint_limits / elbow_max): bending further only shows a sharp seam, so stay within it; a hard seam covered by "
    "clothing (sleeve, bangle) may bend to 40 deg, and a welded joint (2-bone weighted meshes) bends naturally to 40. bounce = [[time, depth_px], ...] lists only the dips (no 0 anchors) and lowers the hip at each "
    "beat "
    "(own translatey track, separate from the sway's translatex; leg IK bends the knees), rebounds 15% between "
    "beats and settles 0.5s after the last one; with bounce, hip.dip_y is ignored. Bones holding a hand-carried "
    "accessory (bag) get their own counter-rotation so it hangs by gravity (hang = [{bone, amount, lag}] overrides). "
    "graph 'smooth' (default) gives every timeline monotone tangents, so motion flows through inner keys and only "
    "stops at turns; 'ease' stops at every key. offset = {arms: s, hip_tilt: s} delays all arm keys / the hip tilt "
    "behind the body (overlap). Skirt chains swing as a damped spring driven by the hip's sideways acceleration and "
    "hang from the hip: skirt = {amp deg, freq Hz, freq_falloff, damping, grow, chain_delay s, enabled}.")

HARD_SEAM_LIMIT = 20.0
COVERED_LIMIT = 40.0
NATURAL_LIMIT = 40.0
KNEE_OVERLAP = 0.35
HYPEREXTEND = 3.0

_PAIRS = {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}}
MOTION_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "duration": {"type": "number"},
        "ease": {"type": "number"},
        "sway_signal": _PAIRS,
        "hip": {"type": "object", "properties": {"shift_x": {"type": "number"}, "dip_y": {"type": "number"},
                                                 "tilt": {"type": "number"}},
                "required": ["shift_x", "dip_y", "tilt"]},
        "upper_body": {"type": "object",
                       "properties": {k: {"type": "number"} for k in ("spine", "chest", "neck", "head", "lag")},
                       "required": ["spine", "chest", "neck", "head", "lag"]},
        "arm_keys": {"type": "object", "additionalProperties": _PAIRS},
        "secondary": {"type": "array", "items": {
            "type": "object",
            "properties": {"bones": {"type": "array", "items": {"type": "string"}}, "gain": {"type": "number"},
                           "falloff": {"type": "number"}, "lag": {"type": "number"}},
            "required": ["bones", "gain", "falloff", "lag"]}},
        "blinks": {"type": "array", "items": {"type": "number"}},
        "blink_closed_scale": {"type": "number"},
        "arm_lag": {"type": "object", "additionalProperties": {"type": "number"}},
        "elbow_max": {"type": "number"},
        "joint_limits": {"type": "object", "additionalProperties": {"type": "number"}},
        "bounce": _PAIRS,
        "graph": {"type": "string", "enum": ["smooth", "ease"]},
        "offset": {"type": "object", "properties": {"arms": {"type": "number"}, "hip_tilt": {"type": "number"}}},
        "skirt": {"type": "object", "properties": {k: {"type": "number"} for k in (
            "amp", "freq", "freq_falloff", "damping", "grow", "chain_delay")} | {"enabled": {"type": "boolean"}}},
        "follow": {"type": "array", "items": {"type": "object", "properties": {
            "bone": {"type": "string"}, "src": {"type": "array", "items": {"type": "string"}},
            **{k: {"type": "number"} for k in ("drag", "lag", "wave_amp", "wave_period", "wave_phase", "limit")}},
            "required": ["bone", "src"]}},
        "hang": {"type": "array", "items": {
            "type": "object", "properties": {"bone": {"type": "string"}, "amount": {"type": "number"},
                                             "lag": {"type": "number"}}, "required": ["bone"]}},
    },
    "required": ["duration", "ease", "sway_signal", "hip", "upper_body", "arm_keys", "secondary", "blinks",
                 "blink_closed_scale"],
}
REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"score": {"type": "number"}, "critique": {"type": "string"}, "seam_visible": {"type": "boolean"},
                   "spec": MOTION_SPEC_SCHEMA},
    "required": ["score", "critique", "seam_visible", "spec"],
}


class SpecError(ValueError):
    pass


def _ease(t0: float, v0: float, t1: float, v1: float, k: float) -> list[float]:
    # Flat bezier handles at both ends: sine-like motion between extremes, no snap at the turn.
    dt = t1 - t0
    return [round(t0 + dt * k, 4), v0, round(t1 - dt * k, 4), v1]


def _slopes(keys: list[tuple[float, float]]) -> list[float]:
    """Monotone (Fritsch-Butland) tangents: motion runs through inner keys, stops only at turns and the ends, and
    never overshoots a key value."""
    m = [0.0] * len(keys)
    for i in range(1, len(keys) - 1):
        (t0, v0), (t1, v1), (t2, v2) = keys[i - 1], keys[i], keys[i + 1]
        h0, h1 = t1 - t0, t2 - t1
        if h0 <= 0 or h1 <= 0:
            continue
        d0, d1 = (v1 - v0) / h0, (v2 - v1) / h1
        if d0 * d1 > 0:
            w0, w1 = 2 * h1 + h0, h1 + 2 * h0
            m[i] = (w0 + w1) / (w0 / d0 + w1 / d1)
    return m


def _rotate_track(keys: list[tuple[float, float]], k: float, smooth: bool = False) -> list[dict[str, Any]]:
    out, m = [], _slopes(keys) if smooth else []
    for i, (t, v) in enumerate(keys):
        key: dict[str, Any] = {"time": round(t, 4), "value": round(v, 3)}
        if i + 1 < len(keys):
            t1, v1 = keys[i + 1]
            if smooth:
                dt = (t1 - t) / 3
                key["curve"] = [round(t + dt, 4), round(v + m[i] * dt, 3), round(t1 - dt, 4),
                                round(v1 - m[i + 1] * dt, 3)]
            else:
                key["curve"] = _ease(t, round(v, 3), t1, round(v1, 3), k)
        out.append(key)
    return out


def _drift_holds(keys: list[tuple[float, float]], cap: float = 3.0) -> list[tuple[float, float]]:
    """Two equal keys in a row read as a dead stop: the second drifts a little toward the next key."""
    out = list(keys)
    for i in range(1, len(out) - 1):
        (_, a), (t, b), (_, c) = out[i - 1], out[i], out[i + 1]
        if abs(a - b) < 1e-6 and abs(b) > 1e-6:
            out[i] = (t, b + max(-cap, min(cap, 0.35 * (c - b))))
    return out


def _xy_track(keys: list[tuple[float, float, float]], k: float) -> list[dict[str, Any]]:
    out = []
    for i, (t, x, y) in enumerate(keys):
        key: dict[str, Any] = {"time": round(t, 4), "x": round(x, 3), "y": round(y, 3)}
        if i + 1 < len(keys):
            t1, x1, y1 = keys[i + 1]
            key["curve"] = _ease(t, round(x, 3), t1, round(x1, 3), k) + _ease(t, round(y, 3), t1, round(y1, 3), k)
        out.append(key)
    return out


def _scaled(signal: list[tuple[float, float]], gain: float, delay: float = 0.0) -> list[tuple[float, float]]:
    last = len(signal) - 1
    return [(t + delay, 0.0 if i in (0, last) else v * gain) for i, (t, v) in enumerate(signal)]


def _pinned(keys: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Explicit keys forced to start at (0, 0) and end at value 0, so the clip begins and ends at setup pose."""
    keys = sorted(keys)
    if keys[0][0] > 0:
        keys.insert(0, (0.0, 0.0))
    keys[0] = (0.0, 0.0)
    if abs(keys[-1][1]) > 1e-9:
        keys.append((keys[-1][0] + 0.3, 0.0))
    return keys


def _blink_track(blinks: list[float], closed: float) -> list[dict[str, Any]]:
    keys: list[dict[str, Any]] = [{"time": 0}]
    for t in sorted(blinks):
        keys += [{"time": round(t - 0.07, 4)}, {"time": round(t, 4), "x": closed},
                 {"time": round(t + 0.1, 4), "x": closed}, {"time": round(t + 0.2, 4)}]
    return keys


def _bounce(beats: list[tuple[float, float]], end: float) -> list[tuple[float, float]]:
    """Hip y keys: down `depth` at each beat, up 15% halfway between beats, back to 0 about 0.5s after the last."""
    ys = [(0.0, 0.0)]
    for i, (t, depth) in enumerate(beats):
        ys.append((t, -abs(depth)))
        if i + 1 < len(beats):
            ys.append(((t + beats[i + 1][0]) / 2, abs(depth) * 0.15))
    ys.append((min(beats[-1][0] + 0.5, end), 0.0))
    return ys


def _pairs(raw: Any, what: str) -> list[tuple[float, float]]:
    try:
        out = [(float(a), float(b)) for a, b in raw]
    except (TypeError, ValueError):
        raise SpecError(f"{what}: expected [[time, value], ...]") from None
    if not out:
        raise SpecError(f"{what}: empty")
    return out


def bend_range(setup: float, limit: float) -> tuple[float, float]:
    """Allowed rotate offset for a joint: total bend within `limit` toward the rig's bend side (setup < 0 bends
    negative) and at most 3 deg of hyperextension; a joint drawn bent past the limit may move ±limit from setup."""
    sign = -1.0 if setup < 0 else 1.0
    if abs(setup) < limit:
        lo, hi = sorted((sign * limit - setup, -sign * HYPEREXTEND - setup))
    else:
        guard = -sign * HYPEREXTEND - setup
        lo, hi = (-limit, min(limit, guard)) if sign < 0 else (max(-limit, guard), limit)
    return min(lo, 0.0), max(hi, 0.0)


def _att_bones(att: dict[str, Any], bone: str, names: list[str]) -> set[str]:
    """Bones that move an attachment: the weights of a weighted mesh, else its slot's bone."""
    v, n = att.get("vertices") or [], len(att.get("uvs") or []) // 2
    if att.get("type") != "mesh" or len(v) == 2 * n:
        return {bone}
    bones, k = set(), 0
    for _ in range(n):
        c = int(v[k])
        bones.update(names[int(v[k + 1 + 4 * j])] for j in range(c))
        k += 1 + 4 * c
    return bones


def _tip_joints(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Child bones starting at their parent's tip (elbow, wrist, knee...), each with whether a weighted mesh spans
    the joint in any skin; parent and child must both carry attachments."""
    names = [b["name"] for b in doc["bones"]]
    by = {b["name"]: b for b in doc["bones"]}
    slot_bone = {s["name"]: s["bone"] for s in doc.get("slots") or []}
    owners: set[str] = set()
    spans: set[str] = set()
    for skin in doc.get("skins") or []:
        for slot, atts in (skin.get("attachments") or {}).items():
            if slot not in slot_bone:
                continue
            for a in atts.values():
                if a.get("type", "region") not in ("region", "mesh"):
                    continue
                bones = _att_bones(a, slot_bone[slot], names)
                owners |= bones
                spans |= {b for b in bones if by[b].get("parent") in bones}
    out = []
    for b in doc["bones"]:
        p = by.get(b.get("parent", ""))
        if not p or b["name"] not in owners or p["name"] not in owners:
            continue
        length = p.get("length", 0.0)
        if length <= 0 or b.get("length", 0.0) <= 0:
            continue
        if abs(b.get("x", 0.0) - length) > 0.15 * length or abs(b.get("y", 0.0)) > 0.15 * length:
            continue
        out.append({"bone": b["name"], "parent": p["name"], "setup": float(b.get("rotation", 0.0)),
                    "hard": b["name"] not in spans})
    return out


def _inside(pt: np.ndarray, tri: np.ndarray) -> bool:
    d = [float((tri[(i + 1) % 3][0] - tri[i][0]) * (pt[1] - tri[i][1])
               - (tri[(i + 1) % 3][1] - tri[i][1]) * (pt[0] - tri[i][0])) for i in range(3)]
    return min(d) >= 0 or max(d) <= 0


def _triangles(att: dict[str, Any], pose: Pose, bone: str) -> list[np.ndarray]:
    from .spine_runtime import _region_corners, mesh_world

    if att.get("type") == "mesh":
        pts = mesh_world(att, pose, bone)
        t = att.get("triangles") or []
        return [pts[[t[i], t[i + 1], t[i + 2]]] for i in range(0, len(t) - 2, 3)]
    c = _region_corners(att, pose, bone)
    return [c[[0, 1, 2]], c[[0, 2, 3]]]


def _subtree(doc: dict[str, Any], root: str) -> set[str]:
    out = {root}
    for b in doc["bones"]:
        if b.get("parent") in out:
            out.add(b["name"])
    return out


def joint_covers(doc: dict[str, Any], parts: list[str], mode: str = "auto") -> dict[str, str]:
    """Hard-seam joint bone -> skin hiding its seam. mode 'none', a bone list, or 'auto': a rendered skin whose
    attachments on that limb are drawn above everything else on the joint's two bones and cover a disc around the
    pivot (radius 10% of the child bone) at setup pose."""
    from .spine_runtime import resolve_skins

    mode = (mode or "auto").strip()
    hard = [j for j in _tip_joints(doc) if j["hard"]]
    if mode.lower() == "none" or not hard:
        return {}
    if mode.lower() != "auto":
        want = {b for b in re.split(r"[,\s]+", mode) if b}
        return {j["bone"]: "joint_cover" for j in hard if j["bone"] in want}
    skins, _ = resolve_skins(doc, parts)
    by_skin = {s["name"]: s.get("attachments") or {} for s in doc.get("skins") or []}
    names = [b["name"] for b in doc["bones"]]
    by = {b["name"]: b for b in doc["bones"]}
    order = {s["name"]: i for i, s in enumerate(doc.get("slots") or [])}
    slot_bone = {s["name"]: s["bone"] for s in doc.get("slots") or []}
    shown: dict[str, tuple[str, dict[str, Any]]] = {}
    for skin in ["default", *skins]:
        for s in doc.get("slots") or []:
            att = (by_skin.get(skin, {}).get(s["name"]) or {}).get(s.get("attachment") or "")
            if att and att.get("type", "region") in ("region", "mesh"):
                shown[s["name"]] = (skin, att)
    moved_by = {sl: _att_bones(att, slot_bone[sl], names) for sl, (_, att) in shown.items()}
    pose = Pose(doc)
    out: dict[str, str] = {}
    for j in hard:
        pair, limb = {j["parent"], j["bone"]}, _subtree(doc, j["parent"])
        pivot = pose.world[j["bone"]][1]
        r = 0.1 * float(by[j["bone"]].get("length", 0.0))
        ring = [pivot, *(pivot + r * np.array([math.cos(a), math.sin(a)]) for a in np.linspace(0, 2 * math.pi, 16,
                                                                                                endpoint=False))]
        for skin in skins:
            under = [sl for sl, (sk, _) in shown.items() if sk != skin and moved_by[sl] & pair]
            # Both bones must already be drawn by other skins, else this skin is the limb itself, not a cover.
            if not all(any(b in moved_by[sl] for sl in under) for b in pair):
                continue
            top = max(order[sl] for sl in under)
            tris = [t for sl, (sk, att) in shown.items() if sk == skin and order[sl] > top and moved_by[sl] & limb
                    for t in _triangles(att, pose, slot_bone[sl])]
            if tris and all(any(_inside(p, t) for t in tris) for p in ring):
                out[j["bone"]] = skin
                break
    return out


def joint_limits(doc: dict[str, Any], spec: dict[str, Any] | None = None,
                 covers: dict[str, str] | None = None, welded: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """bone -> {limit, setup, lo, hi, hard, cover?, welded?}: hard-seam joints get 20 deg (40 when `covers` hides the
    seam), welded joints their natural 40 deg; a spec (elbow_max for arm_lower_* / *elbow*, joint_limits) may only
    tighten those, other joints take the spec value as is."""
    spec = spec or {}
    covers = covers or {}
    setup = {b["name"]: float(b.get("rotation", 0.0)) for b in doc["bones"]}
    out: dict[str, dict[str, Any]] = {}
    for j in _tip_joints(doc):
        if j["hard"]:
            cover = covers.get(j["bone"])
            out[j["bone"]] = {"limit": COVERED_LIMIT if cover else HARD_SEAM_LIMIT, "hard": True,
                              **({"cover": cover} if cover else {})}
    ik_bones = {b for c in doc.get("ik") or [] for b in c.get("bones") or []}
    by = {b["name"]: b for b in doc["bones"]}
    for b in welded or []:
        # Hinges only: IK sets knees, and sockets (shoulder, hip: parent without length) swing far by design.
        if b in setup and b not in ik_bones and (by.get(by[b].get("parent", ""), {}).get("length") or 0) > 0:
            out[b] = {"limit": NATURAL_LIMIT, "hard": False, "welded": True}

    def put(b: str, lim: float) -> None:
        cur = out.get(b, {"hard": False})
        fixed = cur["hard"] or cur.get("welded")
        out[b] = {**cur, "limit": min(cur["limit"], lim) if fixed else lim}

    if spec.get("elbow_max") is not None:
        for b in setup:
            if b.startswith("arm_lower_") or "elbow" in b.lower():
                put(b, abs(float(spec["elbow_max"])))
    for b, lim in (spec.get("joint_limits") or {}).items():
        if b in setup:
            put(b, abs(float(lim)))
    for b, j in out.items():
        j["setup"] = setup[b]
        j["lo"], j["hi"] = bend_range(setup[b], j["limit"])
    return out


def _hands(doc: dict[str, Any]) -> set[str]:
    """Hands: tip of a forearm whose upper arm hangs off a length-less attach bone on the torso (arm_upper_L under
    chest), the shape of an arm chain; torso, hair and leg chains do not match."""
    by = {b["name"]: b for b in doc["bones"]}
    length = lambda n: float((by.get(n or "") or {}).get("length") or 0)  # noqa: E731
    in_chain = {b for c in _chains(doc) if len(c) >= 3 for b in c}
    tips = {j["bone"]: j["parent"] for j in _tip_joints(doc)}
    out = set()
    for b, lower in tips.items():
        upper = by.get(lower, {}).get("parent")
        attach = by.get(upper or "", {}).get("parent")
        if lower in tips and attach and not length(attach) and length(by[attach].get("parent"))                 and not {b, lower, upper} & in_chain:
            out.add(b)
    return out


def held_bones(doc: dict[str, Any]) -> list[str]:
    """Length-less bones under a hand (third bone of a limb chain) whose slots show only skins that do not draw the
    hand itself: hand-carried accessories (bag) that should hang rather than swing with the wrist."""
    hands = _hands(doc)
    slots: dict[str, list[str]] = {}
    for s in doc.get("slots") or []:
        slots.setdefault(s["bone"], []).append(s["name"])
    owners: dict[str, set[str]] = {}
    for skin in doc.get("skins") or []:
        for slot in skin.get("attachments") or {}:
            owners.setdefault(slot, set()).add(skin["name"])
    out = []
    for b in doc["bones"]:
        if b.get("length") or b.get("parent") not in hands or not slots.get(b["name"]):
            continue
        skins = set().union(*(owners.get(s, set()) for s in slots[b["name"]]))
        hand_skins = set().union(*(owners.get(s, set()) for s in slots.get(b["parent"], [])))
        if skins and "default" not in skins and not skins & hand_skins:
            out.append(b["name"])
    return out


def hang_plan(doc: dict[str, Any], spec: dict[str, Any], mode: str = "auto") -> list[dict[str, Any]]:
    """[{bone, ancestors, amount, lag}]: spec `hang` entries, else held_bones() unless mode is 'none' or a bone list."""
    parent = {b["name"]: b.get("parent") for b in doc["bones"]}
    mode = (mode or "auto").strip()
    if isinstance(spec.get("hang"), list):
        items = [h for h in spec["hang"] if isinstance(h, dict) and h.get("bone") in parent]
    elif mode.lower() == "none":
        items = []
    else:
        bones = held_bones(doc) if mode.lower() == "auto" else [b for b in re.split(r"[,\s]+", mode) if b in parent]
        items = [{"bone": b} for b in bones]
    out = []
    for h in items:
        chain, p = [], parent.get(h["bone"])
        while p:
            chain.append(p)
            p = parent.get(p)
        out.append({"bone": h["bone"], "ancestors": chain, "amount": float(h.get("amount", 1.0)),
                    "lag": float(h.get("lag", 0.05))})
    return out


def _hang_keys(bones: dict[str, dict[str, list]], h: dict[str, Any], end: float, step: float = 0.1
               ) -> list[tuple[float, float]]:
    # Counter-rotate by the summed rotation of every ancestor, a little late so the bag sways instead of snapping.
    keys, t = [], 0.0
    while t <= end - h["lag"] + 1e-6:
        total = sum(_sample_rotate(bones[a]["rotate"], t) for a in h["ancestors"] if "rotate" in bones.get(a, {}))
        keys.append((round(min(t + h["lag"], end), 4), -h["amount"] * total))
        t += step
    keys[0] = (0.0, 0.0)
    keys[-1] = (keys[-1][0], 0.0)
    return keys


def follow_plan(doc: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Fingers that trail the arm: spec `follow` entries, else every boned child of a hand (third bone of a limb
    chain); the thumb (not at the hand's tip) drags less and later, and the second hand waves in the other phase."""
    if isinstance(spec.get("follow"), list):
        return [f for f in spec["follow"] if isinstance(f, dict) and f.get("bone") and f.get("src")]
    by = {b["name"]: b for b in doc["bones"]}
    in_chain = {b for c in _chains(doc) for b in c}
    tips = {j["bone"]: j["parent"] for j in _tip_joints(doc)}
    setup = Pose(doc)
    fingers = {h: [b["name"] for b in doc["bones"] if b.get("parent") == h and b.get("length")
                   and b["name"] not in in_chain] for h in _hands(doc)}
    hands = sorted((h for h, fs in fingers.items() if fs), key=lambda h: float(setup.world[h][1][0]))
    out = []
    for i, hand in enumerate(hands):
        src, p = [], hand
        while p and by[p].get("length"):
            src.insert(0, p)
            p = by[p].get("parent")
        sign, phase = (1, 0.0) if i % 2 == 0 else (-1, 0.35)
        for f in fingers[hand]:
            tip = f in tips
            out.append({"bone": f, "src": src, "drag": 0.18 if tip else 0.12, "lag": 0.12 if tip else 0.16,
                        "wave_amp": sign * (4 if tip else 3), "wave_period": 1.7,
                        "wave_phase": phase + (0.0 if tip else 0.15), "limit": 10 if tip else 8})
    return out


def _follow_keys(bones: dict[str, dict[str, list]], f: dict[str, Any], end: float, step: float = 0.1
                 ) -> list[tuple[float, float]]:
    # The finger drags behind the arm (where the arm was `lag` ago minus where it is) plus a slight curl wave, soft
    # limited by tanh and eased in/out so the hand never reads as one rigid block.
    tracks = [bones[b]["rotate"] for b in f["src"] if "rotate" in bones.get(b, {})]

    def angle(t: float) -> float:
        return sum(_sample_rotate(tr, max(0.0, t)) for tr in tracks)

    lim = float(f.get("limit", 10.0))
    keys, t = [], 0.0
    while t < end - 0.05:
        v = float(f.get("drag", 0.0)) * (angle(t - float(f.get("lag", 0.1))) - angle(t))
        v += float(f.get("wave_amp", 0.0)) * math.sin(
            2 * math.pi * (t / float(f.get("wave_period", 1.6)) + float(f.get("wave_phase", 0.0))))
        keys.append((round(t, 4), lim * math.tanh(v / lim) * min(1.0, t / 0.3, (end - t) / 0.4)))
        t += step
    keys[0] = (0.0, 0.0)
    keys.append((end, 0.0))
    return keys


def build_animation(spec: dict[str, Any], bone_map: dict[str, Any] | None = None,
                    limits: dict[str, dict[str, Any]] | None = None,
                    hang: list[dict[str, Any]] | None = None,
                    skirt: dict[str, Any] | None = None,
                    follow: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    names = {**DEFAULT_BONES, **(spec.get("bones") or {}), **(bone_map or {})}
    k = float(spec.get("ease", 0.4))
    smooth = spec.get("graph", "smooth") != "ease"
    off = spec.get("offset") or {}
    end = float(spec["duration"])
    sway = _pairs(spec.get("sway_signal") or [[0, 0], [end - 0.3, 0]], "sway_signal")
    bones: dict[str, dict[str, list]] = {}

    def track(keys: list[tuple[float, float]]) -> list[dict[str, Any]]:
        return _rotate_track(keys, k, smooth)

    def rot(name: str, keys: list[tuple[float, float]]) -> None:
        bones.setdefault(name, {})["rotate"] = track(keys)

    hip = spec.get("hip") or {}
    # Zero-depth entries are anchors a key-list habit adds ([0, 0], [end, 0]); only real dips are beats.
    beats = sorted(b for b in _pairs(spec["bounce"], "bounce") if abs(b[1]) > 1e-6) if spec.get("bounce") else []
    if beats and not (beats[0][0] > 0 and beats[-1][0] < end):
        raise SpecError(f"bounce: beat times must lie inside (0, {end:g})")
    if beats:
        hip_tl = bones.setdefault(names["hip"], {})
        hip_tl["translatey"] = track(_bounce(beats, end))
        if hip:
            hip_tl["translatex"] = track(_scaled(sway, float(hip.get("shift_x", 0))))
    elif hip:
        last = len(sway) - 1
        hip_xy = [(t, v * float(hip.get("shift_x", 0)) if i not in (0, last) else 0.0,
                   0.0 if i in (0, last) else -float(hip.get("dip_y", 0)) * abs(v)) for i, (t, v) in enumerate(sway)]
        bones.setdefault(names["hip"], {})["translate"] = _xy_track(hip_xy, k)
    if hip:
        rot(names["hip"], _scaled(sway, float(hip.get("tilt", 0)), float(off.get("hip_tilt", 0.0))))
    lean = spec.get("upper_body") or {}
    if lean:
        lag = float(lean.get("lag", 0))
        rot(names["spine"], _scaled(sway, -float(hip.get("tilt", 0)) - float(lean.get("spine", 0)), lag))
        rot(names["chest"], _scaled(sway, -float(lean.get("chest", 0)), lag * 1.5))
        rot(names["neck"], _scaled(sway, float(lean.get("neck", 0)), lag * 2))
        rot(names["head"], _scaled(sway, float(lean.get("head", 0)), lag * 2.5))
    lags = spec.get("arm_lag") or {}
    for bone, keys in (spec.get("arm_keys") or {}).items():
        pts = _pinned(_pairs(keys, f"arm_keys.{bone}"))
        if limits and bone in limits:
            lo, hi = limits[bone]["lo"], limits[bone]["hi"]
            pts = [(t, min(max(v, lo), hi)) for t, v in pts]
        if smooth:
            pts = _drift_holds(pts)
        d = float(lags.get(bone, 0.0)) + float(off.get("arms", 0.0))
        rot(bone, [pts[0], *((t + d, v) for t, v in pts[1:])])
    for f in follow or []:
        if f["bone"] not in (spec.get("arm_keys") or {}):
            rot(f["bone"], _follow_keys(bones, f, end))
    skirt_bones = {b for c in (skirt or {}).get("chains") or [] for b in c}
    for chain in spec.get("secondary") or []:
        if skirt_bones & set(chain["bones"]):
            continue
        for i, bone in enumerate(chain["bones"]):
            gain = float(chain["gain"]) * (1 + float(chain.get("falloff", 0)) * i)
            rot(bone, _scaled(sway, gain, min(float(chain.get("lag", 0)) * (i + 1), max(end - sway[-1][0], 0.0))))
    for h in hang or []:
        if h["bone"] not in (spec.get("arm_keys") or {}):
            rot(h["bone"], _hang_keys(bones, h, end))
    if skirt:
        from .spine_skirt import skirt_keys

        for b, keys in skirt_keys(bones, skirt, names["hip"], end).items():
            rot(b, keys)
    if spec.get("blinks"):
        blink = _blink_track(spec["blinks"], float(spec.get("blink_closed_scale", 0.15)))
        for eye in names["eyes"]:
            bones.setdefault(eye, {})["scale"] = blink

    for name, tl in bones.items():
        for kind, track in tl.items():
            if track[-1]["time"] > end + 1e-6:
                raise SpecError(f"{name}.{kind} has a key at {track[-1]['time']}s, after duration {end}s")
            if track[-1]["time"] < end - 1e-6:
                # Last key pinned at `end` so the clip lasts exactly `duration` and rests at setup pose.
                track[-1].pop("curve", None)
                last = {k2: v for k2, v in track[-1].items() if k2 not in ("time", "curve")}
                track.append({"time": end, **last})
    return {"bones": bones}


def _member_spans(text: str, start: int) -> list[tuple[str, int, int, int]]:
    """(key, key_start, value_start, value_end) of each member of the JSON object whose '{' is at `start`."""
    dec = json.JSONDecoder()
    spans, i = [], start + 1
    ws = re.compile(r"[\s,]*")
    while True:
        i = ws.match(text, i).end()
        if text[i] == "}":
            return spans
        key_start = i
        key, i = dec.raw_decode(text, i)
        i = re.compile(r"\s*:\s*").match(text, i).end()
        value_start = i
        _, i = dec.raw_decode(text, i)
        spans.append((key, key_start, value_start, i))


def add_animation(text: str, name: str, anim: dict[str, Any]) -> str:
    """Skeleton JSON text with animation `name` added or replaced; every other byte of the file is kept."""
    top = _member_spans(text, text.index("{"))
    body = json.dumps(anim, ensure_ascii=False, separators=(",", ":"))
    entry = json.dumps(name, ensure_ascii=False) + ":" + body
    for key, _, vs, ve in top:
        if key != "animations":
            continue
        members = _member_spans(text, vs)
        for k2, ks, ws_, we in members:
            if k2 == name:
                return text[:ws_] + body + text[we:]
        if not members:
            return text[:vs] + "{" + entry + "}" + text[ve:]
        last_end = members[-1][3]
        return text[:last_end] + "," + entry + text[last_end:]
    last_end = top[-1][3]
    return text[:last_end] + ',"animations":{' + entry + "}" + text[last_end:]


def check_animation(doc: dict[str, Any], name: str, tol: float = 1e-3) -> list[str]:
    """Problems: unknown bones, and frame 0 / last frame not matching setup pose."""
    anim = doc["animations"][name]
    known = {b["name"] for b in doc["bones"]}
    problems = [f"unknown bone: {b}" for b in sorted(set(anim.get("bones", {})) - known)]
    if problems:
        return problems
    end = max((k.get("time", 0) for tl in anim.get("bones", {}).values() for tr in tl.values() for k in tr),
              default=0.0)
    setup = Pose(doc)
    for t in (0.0, end):
        pose = Pose(doc, name, t)
        worst = max((float(np.abs(np.r_[pose.world[b][0].ravel() - setup.world[b][0].ravel(),
                                         pose.world[b][1] - setup.world[b][1]]).max()), b) for b in setup.world)
        if worst[0] > tol:
            problems.append(f"t={t:g}s is not setup pose ({worst[1]} off by {worst[0]:.3g})")
    return problems


def _world_angles(doc: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for b in doc["bones"]:
        out[b["name"]] = b.get("rotation", 0.0) + (out[b["parent"]] if "parent" in b else 0.0)
    return out


def _chains(doc: dict[str, Any]) -> list[list[str]]:
    """Runs of parent→child bones sharing a name stem (hair2>hair3…): secondary-motion candidates."""
    kids: dict[str, list[str]] = {}
    for b in doc["bones"]:
        if "parent" in b:
            kids.setdefault(b["parent"], []).append(b["name"])

    def stem(n: str) -> str:
        return re.sub(r"\d+$", "", n)

    seen: set[str] = set()
    chains = []
    for b in doc["bones"]:
        n = b["name"]
        if n in seen or not re.search(r"\d+$", n):
            continue
        parent = b.get("parent")
        if parent and stem(parent) == stem(n) and re.search(r"\d+$", parent):
            continue
        run = [n]
        while True:
            nxt = [c for c in kids.get(run[-1], []) if stem(c) == stem(n) and re.search(r"\d+$", c)]
            if len(nxt) != 1:
                break
            run.append(nxt[0])
        if len(run) >= 2:
            chains.append(run)
            seen.update(run)
    return chains


def leg_chains(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The two 2-bone IK chains with the lowest targets (legs), ordered by knee x at setup pose."""
    pose = Pose(doc)
    by = {b["name"]: b for b in doc["bones"]}
    two = [c for c in doc.get("ik") or [] if len(c.get("bones") or []) == 2 and c.get("target") in pose.world]
    if len(two) < 2:
        return []
    two.sort(key=lambda c: pose.world[c["target"]][1][1])
    legs = [{"ik": c["name"], "bones": list(c["bones"]), "knee": c["bones"][1],
             "length": float(by[c["bones"][0]].get("length", 0.0)) + float(by[c["bones"][1]].get("length", 0.0)),
             "x": float(pose.world[c["bones"][1]][1][0])} for c in two[:2]]
    return sorted(legs, key=lambda c: c["x"])


def hip_bone(doc: dict[str, Any]) -> str | None:
    """Nearest common ancestor of the two leg chains: lowering it makes the leg IK bend both knees."""
    legs = leg_chains(doc)
    if not legs:
        return None
    parent = {b["name"]: b.get("parent") for b in doc["bones"]}

    def up(n: str | None) -> list[str]:
        out = []
        while n:
            out.append(n)
            n = parent.get(n)
        return out

    right = set(up(parent.get(legs[1]["bones"][0])))
    return next((n for n in up(parent.get(legs[0]["bones"][0])) if n in right), None)


def knee_gap(doc: dict[str, Any], anim: str, step: float = 0.02) -> dict[str, Any] | None:
    """Closest horizontal knee distance over the clip vs setup; under 35% of it, or crossed, is an overlap."""
    legs = leg_chains(doc)
    a = (doc.get("animations") or {}).get(anim)
    if not legs or a is None:
        return None
    ka, kb = legs[0]["knee"], legs[1]["knee"]
    setup = legs[1]["x"] - legs[0]["x"]
    if setup <= 1e-6:
        return None
    end = max((float(k.get("time", 0)) for tl in a.get("bones", {}).values() for tr in tl.values() for k in tr),
              default=0.0)
    gap, at = setup, 0.0
    for i in range(int(end / step + 1e-6) + 1):
        t = round(i * step, 4)
        p = Pose(doc, anim, t)
        g = float(p.world[kb][1][0] - p.world[ka][1][0])
        if g < gap:
            gap, at = g, t
    ratio = gap / setup
    return {"knees": [ka, kb], "setup_gap": round(setup, 2), "min_gap": round(gap, 2), "time": at,
            "ratio": round(ratio, 3), "overlap": ratio < KNEE_OVERLAP}


def rig_summary(doc: dict[str, Any], limit: int = 3000, covers: dict[str, str] | None = None,
                welded: list[str] | None = None) -> str:
    angles = _world_angles(doc)
    chains = _chains(doc)
    in_chain = {n for c in chains for n in c[1:]}
    lines = [(f"Spine {doc.get('skeleton', {}).get('spine', '?')}, {len(doc['bones'])} bones, y-up, CCW+. "
              "Bones (parent, world angle at setup, length):")]
    for b in doc["bones"]:
        if b["name"] in in_chain:
            continue
        ln = f" {b['name']}<{b.get('parent', '-')} {angles[b['name']]:.0f}°"
        if b.get("length"):
            ln += f" L{b['length']:.0f}"
        lines.append(ln)
    joints = _tip_joints(doc)
    limits = joint_limits(doc, covers=covers, welded=welded)
    for j in joints:
        lim = limits.get(j["bone"])
        if lim and (j["hard"] or lim.get("welded")):
            how = (f"max bend {lim['limit']:.0f}° total" if abs(j["setup"]) < lim["limit"]
                   else f"±{lim['limit']:.0f}° from setup")
            seam = ("welded (skin and clothing are 2-bone weighted meshes here, they bend as one piece; bend it "
                    "naturally)" if lim.get("welded")
                    else f"hard seam covered by {lim['cover']} (seam hidden, bend it)" if lim.get("cover")
                    else "hard seam (region/single-bone mesh)")
            lines.append(f"joint {j['bone']}: {seam}, setup {j['setup']:.1f}°, {how}, "
                         f"offset range [{lim['lo']:.1f}, {lim['hi']:.1f}]°, no hyperextension")
    soft = [j["bone"] for j in joints if not j["hard"] and j["bone"] not in in_chain and j["bone"] not in
            {c[0] for c in chains} and j["bone"] not in (welded or [])]
    if soft:
        lines.append("Weighted (bend freely): " + ", ".join(soft))
    if doc.get("ik"):
        lines.append("IK: " + "; ".join(
            f"{c['name']} {'>'.join(c['bones'])} -> {c['target']}"
            f"{' bend+' if c.get('bendPositive', True) else ' bend-'}{' stretch' if c.get('stretch') else ''}"
            for c in doc["ik"]))
    from .spine_skirt import skirt_chains

    generated = [f"skirt chains {' ; '.join('>'.join(c) for c in skirt_chains(doc))} (damped spring)"
                 if skirt_chains(doc) else "",
                 f"fingers {', '.join(f['bone'] for f in follow_plan(doc, {}))} (trail the arm)"
                 if follow_plan(doc, {}) else "",
                 f"held accessories {', '.join(held_bones(doc))} (hang by gravity)" if held_bones(doc) else ""]
    if any(generated):
        lines.append("Generated, do not key: " + "; ".join(g for g in generated if g))
    legs = leg_chains(doc)
    hip = hip_bone(doc)
    if hip:
        lines.append(f"Hip for bounce: {hip} (common parent of legs "
                     + ", ".join(f"{c['ik']} {'>'.join(c['bones'])} L{c['length']:.0f}" for c in legs)
                     + f"); knee gap at setup {legs[1]['x'] - legs[0]['x']:.0f}px")
    skirt_bones = {b for c in skirt_chains(doc) for b in c}
    candidates = [c for c in chains if not set(c) & skirt_bones]
    if candidates:
        lines.append("Chains (secondary candidates): " + "; ".join(">".join(c) for c in candidates))
    for aname, anim in (doc.get("animations") or {}).items():
        eyes = {b: tl["scale"] for b, tl in anim.get("bones", {}).items() if "eye" in b.lower() and "scale" in tl}
        if eyes:
            xs = sorted({round(float(k.get("x", 1)), 3) for keys in eyes.values() for k in keys})
            lines.append(f"Blink in '{aname}': scale on {', '.join(sorted(eyes))}, x values {xs}")
            break
    cats: dict[str, int] = {}
    for s in doc.get("skins") or []:
        c, _, _ = s["name"].partition("/")
        cats[c] = cats.get(c, 0) + 1
    lines.append("Skins (category: count): " + ", ".join(f"{c}:{n}" for c, n in sorted(cats.items())))
    lines.append("Animations: " + ", ".join(
        f"{n} {max((k.get('time', 0) for tl in a.get('bones', {}).values() for tr in tl.values() for k in tr), default=0):.2f}s"
        for n, a in (doc.get("animations") or {}).items()))
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit - 20] + "\n…(truncated)"


def deepest_bend(doc: dict[str, Any], anim: str, limits: dict[str, dict[str, Any]], step: float = 0.05
                 ) -> tuple[str, float, float] | None:
    """(bone, time, offset) where a limited joint animated by `anim` bends furthest relative to its limit."""
    tl = ((doc.get("animations") or {}).get(anim) or {}).get("bones") or {}
    best = None
    for bone, lim in limits.items():
        keys = (tl.get(bone) or {}).get("rotate")
        if not keys:
            continue
        end = float(keys[-1].get("time", 0.0))
        for i in range(int(end / step) + 1):
            t = round(i * step, 4)
            v = _sample_rotate(keys, t)
            ratio = abs(lim["setup"] + v) / max(lim["limit"], 1e-6)
            if best is None or ratio > best[0]:
                best = (ratio, bone, t, v)
    return None if best is None else (best[1], best[2], best[3])


def _sample_rotate(keys: list[dict[str, Any]], t: float) -> float:
    from .spine_runtime import sample

    return sample(keys, t, ("value",), 0.0)[0]


def diff_spec(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    """Leaf-level changes between two specs: [{path, old, new}]."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for k in list(dict.fromkeys([*old, *new])):
            out += diff_spec(old.get(k), new.get(k), f"{path}.{k}" if path else k)
        return out
    if old != new:
        return [{"path": path, "old": old, "new": new}]
    return []
