"""Skirts that sway: rigid skirt/dress pieces become grid meshes weighted to the skirt bone chains (rig step), and the
chains get a damped-spring swing driven by the hip's sideways acceleration (anim step). Port of spine-test
tools/skin_skirt.mjs and skirt_keys() in tools/gen_sway.py."""
from __future__ import annotations

import json
import math
import re
from typing import Any

import numpy as np

from .spine_anim import _chains, _inside, _member_spans, hip_bone, leg_chains
from .spine_runtime import Pose, _region_corners, sample
from .spine_weld import _deformed, _smooth

CELL, EPS, BLEND, MIN_SIZE = 28.0, 40.0, 70.0, 16.0
SPRING = {"amp": 4.0, "freq": 1.0, "freq_falloff": 0.92, "damping": 0.6, "grow": 0.05, "chain_delay": 0.08,
          "fade": 0.25, "key_step": 1 / 15}


def skirt_chains(doc: dict[str, Any], mode: str = "auto") -> list[list[str]]:
    """'none', explicit chains ('a>b>c;d>e>f'), or 'auto': runs of 4+ bones with length hanging down from near the
    hip (rooted within 10% of the leg length of it)."""
    mode = (mode or "auto").strip()
    if mode.lower() == "none":
        return []
    by = {b["name"]: b for b in doc["bones"]}
    if mode.lower() != "auto":
        chains = [[b for b in re.split(r"[>,\s]+", part) if b in by] for part in mode.split(";")]
        return [c for c in chains if c]
    hip = hip_bone(doc)
    legs = leg_chains(doc)
    if not hip or not legs:
        return []
    pose = Pose(doc)
    hy = float(pose.world[hip][1][1])
    out = []
    for c in _chains(doc):
        d = pose.world[c[0]][0][:, 0]
        if len(c) >= 4 and all(by[b].get("length") for b in c) and d[1] / np.linalg.norm(d) < -0.8 \
                and abs(float(pose.world[c[0]][1][1]) - hy) < 0.1 * legs[0]["length"]:
            out.append(c)
    return out


class _Skirt:
    def __init__(self, doc: dict[str, Any], chains: list[list[str]]):
        self.doc = doc
        self.pose = Pose(doc)
        self.by = {b["name"]: b for b in doc["bones"]}
        self.index = {b["name"]: i for i, b in enumerate(doc["bones"])}
        self.slot_bone = {s["name"]: s["bone"] for s in doc.get("slots") or []}
        self.chains = chains
        self.bones = {b for c in chains for b in c}
        self.segs = []
        for c in chains:
            for b in c:
                m, p = self.pose.world[b]
                self.segs.append((b, p, m @ np.array([float(self.by[b]["length"]), 0.0]) + p))
        self.root_y = max(float(self.pose.world[c[0]][1][1]) for c in chains)
        hip = hip_bone(doc)
        self.hosts = {hip, *(self.by[c[0]].get("parent") for c in chains)} - {None}

    def _dist(self, a: np.ndarray, b: np.ndarray, p: np.ndarray) -> float:
        d = b - a
        t = min(1.0, max(0.0, float((p - a) @ d) / float(d @ d)))
        return float(np.linalg.norm(p - a - t * d))

    def weights(self, base: str, p: np.ndarray) -> list[tuple[str, float]]:
        # Near the waistband (above the chain roots) the piece stays on its bone; below, the 4 nearest skirt segments
        # share it by inverse squared distance.
        share = _smooth(0.0, BLEND, self.root_y - float(p[1]))
        near = sorted(((b, 1 / (self._dist(a, e, p) ** 2 + EPS * EPS)) for b, a, e in self.segs),
                      key=lambda bw: -bw[1])[:4]
        tot = sum(w for _, w in near)
        out = [(b, share * w / tot) for b, w in near]
        if share < 1:
            out.append((base, 1 - share))
        return [(b, w) for b, w in out if w > 0.002]

    def encode(self, infl: list[tuple[str, float]], p: np.ndarray) -> list[float]:
        tot = sum(w for _, w in infl)
        arr: list[float] = [len(infl)]
        for b, w in infl:
            m, o = self.pose.world[b]
            x, y = np.linalg.solve(m, p - o)
            arr += [self.index[b], round(float(x), 2), round(float(y), 2), round(w / tot, 4)]
        return arr

    def corners(self, slot: str, att: dict[str, Any]) -> tuple[np.ndarray, ...]:
        bone = self.slot_bone[slot]
        if att.get("type") == "mesh":
            m, o = self.pose.world[bone]
            v, uv = att["vertices"], att["uvs"]
            pts = [(uv[2 * i], uv[2 * i + 1], m @ np.array([v[2 * i], v[2 * i + 1]]) + o) for i in range(4)]

            def pick(su: int, sv: int) -> np.ndarray:
                return min(pts, key=lambda q: su * q[0] + sv * q[1])[2]

            return pick(1, 1), pick(-1, 1), pick(1, -1), pick(-1, -1)
        # Original-size corners: mesh uvs 0..1 span the whole image, trimmed whitespace included.
        c = _region_corners(att, self.pose, bone)
        return c[0], c[1], c[3], c[2]

    def is_target(self, slot: str, att: dict[str, Any]) -> bool:
        """A rigid piece on the hip (or a chain root's parent) lying over at least two segments of one chain and
        reaching below the waistband blend."""
        kind = att.get("type", "region")
        if self.slot_bone.get(slot) not in self.hosts or kind not in ("region", "mesh"):
            return False
        if kind == "mesh" and (len(att["vertices"]) > len(att["uvs"]) or len(att["uvs"]) != 8):
            return False
        ul, ur, bl, br = self.corners(slot, att)
        tris = [np.array([ul, ur, br]), np.array([ul, br, bl]), np.array([ul, ur, bl]), np.array([ur, br, bl])]
        if min(ul[1], ur[1], bl[1], br[1]) >= self.root_y - BLEND:
            return False
        for c in self.chains:
            mids = [(a + e) / 2 for b, a, e in self.segs if b in c]
            if sum(any(_inside(m, t) for t in tris) for m in mids) >= 2:
                return True
        return False

    def grid(self, slot: str, att: dict[str, Any]) -> dict[str, Any]:
        ul, ur, bl, br = self.corners(slot, att)
        nu = max(2, math.ceil(round(float(np.linalg.norm(ur - ul)) / CELL, 6)))
        nv = max(2, math.ceil(round(float(np.linalg.norm(bl - ul)) / CELL, 6)))
        ring = ([(i, 0) for i in range(nu)] + [(nu, k) for k in range(nv)] + [(i, nv) for i in range(nu, 0, -1)]
                + [(0, k) for k in range(nv, 0, -1)])
        order = ring + [(i, k) for k in range(1, nv) for i in range(1, nu)]
        index = {p: n for n, p in enumerate(order)}
        tris: list[int] = []
        for k in range(nv):
            for i in range(nu):
                q = [index[(i, k)], index[(i + 1, k)], index[(i + 1, k + 1)], index[(i, k + 1)]]
                tris += [q[0], q[1], q[2], q[0], q[2], q[3]]
        base = self.slot_bone[slot]
        verts: list[float] = []
        uvs: list[float] = []
        for i, k in order:
            u, v = i / nu, k / nv
            p = (1 - u) * (1 - v) * ul + u * (1 - v) * ur + (1 - u) * v * bl + u * v * br
            verts += self.encode(self.weights(base, p), p)
            uvs += [round(u, 5), round(v, 5)]
        mesh: dict[str, Any] = {"type": "mesh", "uvs": uvs, "triangles": tris, "vertices": verts, "hull": len(ring),
                                "width": att.get("width", 0), "height": att.get("height", 0)}
        for key in ("name", "path", "color"):
            if key in att:
                mesh[key] = att[key]
        return mesh


def skirt_text(text: str, chains: list[list[str]]) -> tuple[str, dict[str, Any]]:
    """Skeleton text with rigid skirt pieces turned into chain-weighted meshes; only `skins` is rewritten."""
    doc = json.loads(text)
    report: dict[str, Any] = {"chains": chains, "changed": [], "skipped": []}
    if not chains:
        return text, report
    s = _Skirt(doc, chains)
    deformed = _deformed(doc)
    skins = json.loads(json.dumps(doc.get("skins") or []))
    for skin in skins:
        for slot, atts in (skin.get("attachments") or {}).items():
            if slot not in s.slot_bone or s.slot_bone[slot] in s.bones:
                continue
            for name, att in list(atts.items()):
                if not s.is_target(slot, att):
                    continue
                label = f"{skin['name']}/{name}"
                if (skin["name"], slot, name) in deformed or att.get("width", 0) < MIN_SIZE \
                        or att.get("height", 0) < MIN_SIZE:
                    report["skipped"].append(label)
                    continue
                atts[name] = s.grid(slot, att)
                report["changed"].append(label)
    if report["changed"]:
        body = json.dumps(skins, ensure_ascii=False, separators=(",", ":"))
        for key, _, vs, ve in _member_spans(text, text.index("{")):
            if key == "skins":
                text = text[:vs] + body + text[ve:]
                break
    return text, report


def keyed_chains(doc: dict[str, Any], chains: list[list[str]], skip: str = "") -> dict[str, float]:
    """Existing animations that rotate the skirt chains -> largest offset: the meshed skirt now follows those keys."""
    bones = {b for c in chains for b in c}
    out = {}
    for name, anim in (doc.get("animations") or {}).items():
        if name == skip:
            continue
        top = max((abs(float(k.get("value", 0.0))) for b, tl in (anim.get("bones") or {}).items() if b in bones
                   for k in tl.get("rotate") or []), default=0.0)
        if top > 0:
            out[name] = round(top, 1)
    return out


def skirt_plan(doc: dict[str, Any], spec: dict[str, Any], mode: str = "auto") -> dict[str, Any] | None:
    """Spring settings for the skirt chains: spec `skirt` ({...} overrides, false disables) over SPRING defaults; the
    counter bones are the chain root's ancestors below the hip (the torso the skirt must not lean with)."""
    raw = spec.get("skirt")
    if raw is False or (isinstance(raw, dict) and raw.get("enabled") is False) or (mode or "").lower() == "none":
        return None
    cfg = {**SPRING, **(raw if isinstance(raw, dict) else {})}
    by = {b["name"]: b for b in doc["bones"]}
    chains = [[b for b in c if b in by] for c in cfg.get("chains") or skirt_chains(doc, mode)]
    chains = [c for c in chains if c]
    if not chains:
        return None
    hip = hip_bone(doc)
    if "counter" not in cfg:
        counter, p = [], by[chains[0][0]].get("parent")
        while p and p != hip:
            counter.append(p)
            p = by[p].get("parent")
        cfg["counter"] = counter
    return {**cfg, "chains": chains, "hip": hip}


def skirt_keys(bones: dict[str, dict[str, list]], plan: dict[str, Any], hip: str, end: float
               ) -> dict[str, list[tuple[float, float]]]:
    """Per chain bone, local rotate keys of a damped spring: segment 0 follows minus the hip's sideways acceleration
    (scaled so the peak is `amp` degrees), each lower segment follows the one above (higher `grow`, lower frequency),
    so a wave runs down to the hem; the root also cancels the torso's rotation so the skirt hangs from the hip."""
    dt = 1 / 240
    hip_tl = bones.get(hip, {})
    if "translatex" in hip_tl:
        x_at = lambda t: sample(hip_tl["translatex"], t, ("value",), 0.0)[0]  # noqa: E731
    elif "translate" in hip_tl:
        x_at = lambda t: sample(hip_tl["translate"], t, ("x", "y"), 0.0)[0]  # noqa: E731
    else:
        x_at = lambda t: 0.0  # noqa: E731
    n_steps = int(end / dt) + 1
    xs = [x_at(i * dt) for i in range(n_steps + 2)]
    acc = [(xs[i + 2] - 2 * xs[i + 1] + xs[i]) / dt ** 2 for i in range(n_steps)]
    gain = plan["amp"] / max(1e-6, max(abs(a) for a in acc))
    fade0 = end - plan["fade"]
    step = max(1, round(plan["key_step"] / dt))

    def counter(t: float) -> float:
        return sum(sample(bones[c]["rotate"], t, ("value",), 0.0)[0] for c in plan["counter"]
                   if "rotate" in bones.get(c, {}))

    out: dict[str, list[tuple[float, float]]] = {}
    for ci, chain in enumerate(plan["chains"]):
        n, delay = len(chain), round(plan["chain_delay"] * ci / dt)
        th, vel, hist = [0.0] * n, [0.0] * n, []
        for i in range(n_steps):
            for j in range(n):
                w = plan["freq"] * 2 * math.pi * plan["freq_falloff"] ** j
                target = -gain * acc[max(0, i - delay)] if j == 0 else th[j - 1] * (1 + plan["grow"])
                vel[j] += (w * w * (target - th[j]) - 2 * plan["damping"] * w * vel[j]) * dt
                th[j] += vel[j] * dt
            hist.append(list(th))
        for j, b in enumerate(chain):
            keys = []
            for i in range(0, n_steps, step):
                t = i * dt
                if t > end - 0.02:
                    break
                v = hist[i][j] - (hist[i][j - 1] if j else counter(t))
                if t > fade0:
                    u = (t - fade0) / (end - fade0)
                    v *= 1 - u * u * (3 - 2 * u)
                keys.append((round(t, 4), v))
            keys[0] = (0.0, 0.0)
            keys.append((end, 0.0))
            out[b] = keys
    return out
