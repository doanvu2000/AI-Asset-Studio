"""Add a procedural animation to an existing Spine 4.2 skeleton from a motion spec, and summarise a rig for prompts.

Motion spec (port of spine-test tools/gen_sway.py): a normalised driver `sway_signal` [[t, -1..1]] moves hip
(shift/dip/tilt) and leans spine/chest/neck/head with lag; `arm_keys` are explicit rotate offsets; `secondary`
chains follow the driver with growing gain and delay; `blinks` scale the eye bones. Every track starts and ends
at setup pose.
"""
from __future__ import annotations

import json
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
    "joint_limits / elbow_max): bending further only shows a sharp seam, so stay within it.")

HARD_SEAM_LIMIT = 20.0
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
    },
    "required": ["duration", "ease", "sway_signal", "hip", "upper_body", "arm_keys", "secondary", "blinks",
                 "blink_closed_scale"],
}
REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"score": {"type": "number"}, "critique": {"type": "string"}, "spec": MOTION_SPEC_SCHEMA},
    "required": ["score", "critique", "spec"],
}


class SpecError(ValueError):
    pass


def _ease(t0: float, v0: float, t1: float, v1: float, k: float) -> list[float]:
    # Flat bezier handles at both ends: sine-like motion between extremes, no snap at the turn.
    dt = t1 - t0
    return [round(t0 + dt * k, 4), v0, round(t1 - dt * k, 4), v1]


def _rotate_track(keys: list[tuple[float, float]], k: float) -> list[dict[str, Any]]:
    out = []
    for i, (t, v) in enumerate(keys):
        key: dict[str, Any] = {"time": round(t, 4), "value": round(v, 3)}
        if i + 1 < len(keys):
            t1, v1 = keys[i + 1]
            key["curve"] = _ease(t, round(v, 3), t1, round(v1, 3), k)
        out.append(key)
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
            for a in atts.values():
                kind = a.get("type", "region")
                if kind not in ("region", "mesh"):
                    continue
                v, n = a.get("vertices") or [], len(a.get("uvs") or []) // 2
                if kind == "mesh" and len(v) != 2 * n:
                    bones, k = set(), 0
                    for _ in range(n):
                        c = int(v[k])
                        bones.update(names[int(v[k + 1 + 4 * j])] for j in range(c))
                        k += 1 + 4 * c
                    owners |= bones
                    spans |= {b for b in bones if by[b].get("parent") in bones}
                elif slot in slot_bone:
                    owners.add(slot_bone[slot])
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


def joint_limits(doc: dict[str, Any], spec: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """bone -> {limit, setup, lo, hi, hard}: hard-seam joints get 20 deg, which a spec (elbow_max for arm_lower_* /
    *elbow*, joint_limits) may only tighten; other joints take the spec value as is."""
    spec = spec or {}
    setup = {b["name"]: float(b.get("rotation", 0.0)) for b in doc["bones"]}
    out: dict[str, dict[str, Any]] = {}
    for j in _tip_joints(doc):
        if j["hard"]:
            out[j["bone"]] = {"limit": HARD_SEAM_LIMIT, "hard": True}
    def put(b: str, lim: float) -> None:
        cur = out.get(b, {"hard": False})
        out[b] = {**cur, "limit": min(cur["limit"], lim) if cur["hard"] else lim}

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


def build_animation(spec: dict[str, Any], bone_map: dict[str, Any] | None = None,
                    limits: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    names = {**DEFAULT_BONES, **(spec.get("bones") or {}), **(bone_map or {})}
    k = float(spec.get("ease", 0.4))
    end = float(spec["duration"])
    sway = _pairs(spec.get("sway_signal") or [[0, 0], [end - 0.3, 0]], "sway_signal")
    bones: dict[str, dict[str, list]] = {}

    def rot(name: str, keys: list[tuple[float, float]]) -> None:
        bones.setdefault(name, {})["rotate"] = _rotate_track(keys, k)

    hip = spec.get("hip") or {}
    if hip:
        last = len(sway) - 1
        hip_xy = [(t, v * float(hip.get("shift_x", 0)) if i not in (0, last) else 0.0,
                   0.0 if i in (0, last) else -float(hip.get("dip_y", 0)) * abs(v)) for i, (t, v) in enumerate(sway)]
        bones.setdefault(names["hip"], {})["translate"] = _xy_track(hip_xy, k)
        rot(names["hip"], _scaled(sway, float(hip.get("tilt", 0))))
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
        d = float(lags.get(bone, 0.0))
        rot(bone, [pts[0], *((t + d, v) for t, v in pts[1:])])
    for chain in spec.get("secondary") or []:
        for i, bone in enumerate(chain["bones"]):
            gain = float(chain["gain"]) * (1 + float(chain.get("falloff", 0)) * i)
            rot(bone, _scaled(sway, gain, min(float(chain.get("lag", 0)) * (i + 1), max(end - sway[-1][0], 0.0))))
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


def rig_summary(doc: dict[str, Any], limit: int = 3000) -> str:
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
    limits = joint_limits(doc)
    for j in joints:
        if j["hard"]:
            lim = limits[j["bone"]]
            how = (f"max bend {lim['limit']:.0f}° total" if abs(j["setup"]) < lim["limit"]
                   else f"±{lim['limit']:.0f}° from setup")
            lines.append(f"joint {j['bone']}: hard seam (region/single-bone mesh), setup {j['setup']:.1f}°, {how}, "
                         f"offset range [{lim['lo']:.1f}, {lim['hi']:.1f}]°, no hyperextension")
    soft = [j["bone"] for j in joints if not j["hard"] and j["bone"] not in in_chain and j["bone"] not in
            {c[0] for c in chains}]
    if soft:
        lines.append("Weighted (bend freely): " + ", ".join(soft))
    if doc.get("ik"):
        lines.append("IK: " + "; ".join(
            f"{c['name']} {'>'.join(c['bones'])} -> {c['target']}"
            f"{' bend+' if c.get('bendPositive', True) else ' bend-'}{' stretch' if c.get('stretch') else ''}"
            for c in doc["ik"]))
    if chains:
        lines.append("Chains (secondary candidates): " + "; ".join(">".join(c) for c in chains))
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
