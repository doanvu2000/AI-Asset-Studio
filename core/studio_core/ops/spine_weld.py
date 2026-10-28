"""Weld joints so skin and clothing bend as one piece: attachments around a joint become meshes weighted across both
sides near the pivot. Hinges (elbow, wrist, knee) blend the full limb width within 2r of the pivot; sockets (shoulder,
hip: the parent bone has no length) only re-weight pieces bound to the child, so the torso piece is never dragged.
Port of spine-test tools/weld_joints.mjs."""
from __future__ import annotations

import json
import math
import re
from typing import Any

import numpy as np

from .spine_anim import _member_spans, _tip_joints
from .spine_runtime import Pose, _region_corners

Influence = tuple[str, float | None, float | None, float]  # bone, local x/y (None = compute), weight


def _deformed(doc: dict[str, Any]) -> set[tuple[str, str, str]]:
    """(skin, slot, attachment) with an attachment timeline (deform keys are stored per weight)."""
    out = set()
    for a in (doc.get("animations") or {}).values():
        for skin, slots in (a.get("attachments") or {}).items():
            for slot, atts in slots.items():
                out |= {(skin, slot, att) for att in atts}
        for skin, slots in (a.get("deform") or {}).items():
            for slot, atts in slots.items():
                out |= {(skin, slot, att) for att in atts}
    return out


def ik_joints(doc: dict[str, Any]) -> list[str]:
    """Child bones of 2-bone IK chains (knees): IK bends them even without rotate keys."""
    return [c["bones"][1] for c in doc.get("ik") or [] if len(c.get("bones") or []) == 2]


def _length(by: dict[str, dict[str, Any]], name: str | None) -> float:
    return float((by.get(name or "") or {}).get("length", 0.0) or 0.0)


def finger_joints(doc: dict[str, Any]) -> list[str]:
    """Finger bones at a hand's tip (hand = third bone of a limb chain): hand meshes often ignore them, so they are
    welded by force ('name!')."""
    from .spine_anim import _hands

    hands = _hands(doc)
    return [j["bone"] for j in _tip_joints(doc) if j["parent"] in hands]


def weld_candidates(doc: dict[str, Any], hinges: list[str] | None = None) -> list[str]:
    """Hinges (hard-seam tip joints and IK-bent ones, or `hinges`) plus the socket above each limb: the limb's first
    bone when it hangs off a bone without length (arm_upper_L2 under arm_upper_L, leg_upper_L under hip)."""
    by = {b["name"]: b for b in doc["bones"]}
    if hinges is None:
        ik = set(ik_joints(doc))
        hinges = [j["bone"] for j in _tip_joints(doc) if j["hard"] or j["bone"] in ik]
    picked = set(hinges)
    for h in hinges:
        top = by[h].get("parent")
        if top and _length(by, top) > 0 and by[top].get("parent") and _length(by, by[top]["parent"]) <= 0:
            picked.add(top)
    return [b["name"] for b in doc["bones"] if b["name"] in picked]


def resolve_joints(doc: dict[str, Any], mode: str, spec: dict[str, Any] | None = None) -> list[str]:
    """'none', a bone list, or 'auto' (weld_candidates); with a spec, hard seams only where the spec rotates them."""
    mode = (mode or "auto").strip()
    if mode.lower() == "none":
        return []
    if mode.lower() != "auto":
        known = {b["name"] for b in doc["bones"]}
        return [b for b in re.split(r"[,\s]+", mode) if b and b.rstrip("!") in known]
    fingers = [f"{b}!" for b in finger_joints(doc)]
    if spec and isinstance(spec.get("arm_keys"), dict):
        ik = set(ik_joints(doc))
        hinges = [j["bone"] for j in _tip_joints(doc)
                  if (j["hard"] and j["bone"] in spec["arm_keys"]) or j["bone"] in ik]
        return weld_candidates(doc, hinges) + fingers
    return weld_candidates(doc) + fingers


def _inside(poly: np.ndarray, p: np.ndarray) -> bool:
    """Even-odd point in polygon."""
    x, y = float(p[0]), float(p[1])
    c, k = False, len(poly) - 1
    for i in range(len(poly)):
        (xi, yi), (xk, yk) = poly[i], poly[k]
        if (yi > y) != (yk > y) and x < (xk - xi) * (y - yi) / (yk - yi) + xi:
            c = not c
        k = i
    return c


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


class _Welder:
    def __init__(self, doc: dict[str, Any], joints: list[str]):
        self.doc = doc
        self.pose = Pose(doc)
        self.names = [b["name"] for b in doc["bones"]]
        self.index = {n: i for i, n in enumerate(self.names)}
        self.by = {b["name"]: b for b in doc["bones"]}
        self.kids: dict[str, list[str]] = {}
        for b in doc["bones"]:
            if "parent" in b:
                self.kids.setdefault(b["parent"], []).append(b["name"])
        self.slot_bone = {s["name"]: s["bone"] for s in doc.get("slots") or []}
        # A trailing '!' forces the weld: every piece bound to exactly the parent bone (a hand mesh that ignores its
        # finger bones) is welded, whatever its skin draws on the other side.
        self.forced = {c[:-1] for c in joints if c.endswith("!")}
        self.children = [c.rstrip("!") for c in joints]
        self.joints = [self._joint(c) for c in self.children]
        # Sibling sockets close together (two hips under one waistband): the torso piece lying over a limb must bend
        # with that limb, or its edge cuts across the trouser leg.
        for j in self.joints:
            j["wrap"] = j["socket"] and any(
                o is not j and o["socket"] and o["parent"] == j["parent"]
                and float(np.linalg.norm(o["pivot"] - j["pivot"])) < 0.8 * _length(self.by, j["child"])
                for o in self.joints)
        self.skin: tuple[str, dict[str, dict[str, Any]]] = ("", {})
        self._polys: dict[tuple[str, str], list[np.ndarray]] = {}

    def _subtree(self, n: str) -> list[str]:
        return [n, *(x for k in self.kids.get(n, []) for x in self._subtree(k))]

    def _chain(self, n: str) -> bool:
        """A run of at least three bones with length (skirt, hair physics): not part of a joint's parent side."""
        has = lambda x: _length(self.by, x) > 0  # noqa: E731
        return has(n) and any(has(k) and any(has(g) for g in self.kids.get(k, [])) for k in self.kids.get(n, []))

    def _joint(self, child: str) -> dict[str, Any]:
        parent = self.by[child]["parent"]
        lc, lp = _length(self.by, child), _length(self.by, parent)
        dc = self.pose.world[child][0][:, 0]
        dp = self.pose.world[parent][0][:, 0] if lp > 0 else dc
        d = dp / np.linalg.norm(dp) + dc / np.linalg.norm(dc)
        pivot = self.pose.world[child][1].copy()
        side = {parent}
        a = parent
        # A length-less parent is only an attach point: the skin it carries is drawn on ancestors up to a real bone.
        while _length(self.by, a) <= 0 and self.by[a].get("parent") and self.by[self.by[a]["parent"]].get("parent"):
            a = self.by[a]["parent"]
            side.add(a)

        def walk(n: str) -> None:
            for k in self.kids.get(n, []):
                if k in self.children:
                    continue
                if float(np.linalg.norm(self.pose.world[k][1] - pivot)) >= 0.5 * lc:
                    continue
                # A trunk bone leading to another joint (spine above the hips) carries skin here; stop below it.
                if any(x in self.children for x in self._subtree(k)):
                    side.add(k)
                    continue
                if self._chain(k):
                    continue
                side.add(k)
                walk(k)

        walk(parent)
        # Blend zone of 25% of the shorter bone (elbow ~45px, wrist ~15px, hip/knee ~100px).
        return {"child": child, "parent": parent, "side": side, "pivot": pivot, "dir": d / np.linalg.norm(d),
                "r": max(8.0, 0.25 * min(lp or lc, lc)), "socket": lp <= 0}

    def child_polys(self, j: dict[str, Any]) -> list[np.ndarray]:
        """Setup outlines of the current skin's pieces on the joint's child: region = original-size corners, mesh =
        hull (meshes mostly weighted to the child). Cached on first use, as the skin is being welded."""
        key = (j["child"], self.skin[0])
        if key not in self._polys:
            polys = []
            for slot, atts in self.skin[1].items():
                if slot not in self.slot_bone:
                    continue
                for att in atts.values():
                    kind = att.get("type", "region")
                    if kind == "region" and self.slot_bone[slot] == j["child"]:
                        c = _region_corners(att, self.pose, j["child"])
                        polys.append(np.array([c[3], c[0], c[1], c[2]]))
                    elif kind == "mesh":
                        vs = self.vertex_infl(slot, att)
                        share = sum(i[3] for _, infl in vs for i in infl if i[0] == j["child"]) / max(len(vs), 1)
                        if share > 0.5:
                            polys.append(np.array([w for w, _ in vs[:att.get("hull") or len(vs)]]))
            self._polys[key] = polys
        return self._polys[key]

    def _s(self, j: dict[str, Any], w: np.ndarray) -> float:
        return float((w - j["pivot"]) @ j["dir"])

    def _near(self, j: dict[str, Any], w: np.ndarray) -> bool:
        return abs(self._s(j, w)) < 2 * j["r"] and float(np.linalg.norm(w - j["pivot"])) < 4 * j["r"] + 300

    def _local(self, bone: str, w: np.ndarray) -> tuple[float, float]:
        m, p = self.pose.world[bone]
        x, y = np.linalg.solve(m, w - p)
        return float(x), float(y)

    def _world(self, bone: str, x: float, y: float) -> np.ndarray:
        m, p = self.pose.world[bone]
        return m @ np.array([x, y]) + p

    def reweight(self, j: dict[str, Any], w: np.ndarray, infl: list[Influence]) -> list[tuple[str, float, float, float]]:
        """Re-split the share a vertex gives to the joint's two sides; the rest of its weights stay."""
        is_p = lambda i: i[0] in j["side"]  # noqa: E731
        is_c = lambda i: i[0] == j["child"]  # noqa: E731
        mine = [i for i in infl if is_p(i) or is_c(i)]
        total = sum(i[3] for i in mine)
        out = infl
        if total > 0 and self._near(j, w):
            s = self._s(j, w)
            f = 1 - _smooth(j["r"], 2 * j["r"], abs(s))
            orig_c = sum(i[3] for i in mine if is_c(i)) / total
            # A torso piece only bends where it lies over the same skin's limb piece, so the two layers move together.
            if j.get("wrap") and orig_c == 0 and not any(_inside(poly, w) for poly in self.child_polys(j)):
                f = 0.0
            wc = total * (orig_c * (1 - f) + _smooth(-j["r"], j["r"], s) * f)

            def scale(items: list[Influence], target: float, fallback: str) -> list[Influence]:
                if target <= 1e-3:
                    return []
                tot = sum(i[3] for i in items)
                return [(b, x, y, wt / tot * target) for b, x, y, wt in items] if tot > 0 \
                    else [(fallback, None, None, target)]

            out = [i for i in infl if not (is_p(i) or is_c(i))] + scale([i for i in mine if is_p(i)], total - wc,
                                                                       j["parent"]) \
                + scale([i for i in mine if is_c(i)], wc, j["child"])
        tot = sum(i[3] for i in out)
        res = []
        for b, x, y, wt in out:
            if wt <= 1e-3:
                continue
            if x is None or y is None:
                x, y = self._local(b, w)
            res.append((b, round(x, 2), round(y, 2), round(wt / tot, 4)))
        return res

    def _parse(self, att: dict[str, Any]) -> list[list[Influence]] | None:
        v, n = att.get("vertices") or [], len(att.get("uvs") or []) // 2
        if att.get("type") != "mesh" or len(v) == 2 * n:
            return None
        out, k = [], 0
        for _ in range(n):
            c = int(v[k])
            out.append([(self.names[int(v[k + 1 + 4 * i])], float(v[k + 2 + 4 * i]), float(v[k + 3 + 4 * i]),
                         float(v[k + 4 + 4 * i])) for i in range(c)])
            k += 1 + 4 * c
        return out

    def vertex_infl(self, slot: str, att: dict[str, Any]) -> list[tuple[np.ndarray, list[Influence]]]:
        parsed = self._parse(att)
        if parsed is not None:
            return [(sum((self._world(b, x, y) * wt for b, x, y, wt in infl), np.zeros(2)), infl) for infl in parsed]
        bone, v = self.slot_bone[slot], att["vertices"]
        return [(self._world(bone, v[2 * i], v[2 * i + 1]), [(bone, None, None, 1.0)]) for i in range(len(v) // 2)]

    def bound(self, slot: str, att: dict[str, Any]) -> set[str]:
        parsed = self._parse(att)
        return {i[0] for infl in parsed for i in infl} if parsed is not None else {self.slot_bone[slot]}

    def _flat(self, verts: list[list[tuple[str, float, float, float]]]) -> list[float]:
        out: list[float] = []
        for infl in verts:
            out.append(len(infl))
            for b, x, y, w in infl:
                out += [self.index[b], x, y, w]
        return out

    def rebind(self, slot: str, att: dict[str, Any], j: dict[str, Any]) -> dict[str, Any]:
        return {**att, "vertices": self._flat([self.reweight(j, w, infl) for w, infl in self.vertex_infl(slot, att)])}

    def hard_blend(self, slot: str, att: dict[str, Any], j: dict[str, Any]) -> bool:
        """Bound to both sides but switching weights (under half the vertices near the pivot blend) still breaks."""
        zone = [infl for w, infl in self.vertex_infl(slot, att) if abs(self._s(j, w)) < j["r"]]
        if not zone:
            return False
        mixed = 0
        for infl in zone:
            t = sum(i[3] for i in infl if i[0] in j["side"] or i[0] == j["child"])
            c = sum(i[3] for i in infl if i[0] == j["child"]) / (t or 1)
            mixed += 0.05 < c < 0.95
        return mixed / len(zone) < 0.5

    def region_to_mesh(self, slot: str, att: dict[str, Any], j: dict[str, Any]) -> dict[str, Any] | None:
        # Corners at the image's original size: mesh uvs 0..1 span the whole original, trimmed whitespace included.
        c = _region_corners(att, self.pose, self.slot_bone[slot])
        return self.grid(slot, att, j, c[0], c[1], c[3], c[2])

    def quad_to_mesh(self, slot: str, att: dict[str, Any], j: dict[str, Any]) -> dict[str, Any] | None:
        bone, v, uv = self.slot_bone[slot], att["vertices"], att["uvs"]
        pts = [(uv[2 * i], uv[2 * i + 1], self._world(bone, v[2 * i], v[2 * i + 1])) for i in range(4)]

        def pick(fu: Any, fv: Any) -> np.ndarray:
            return min(pts, key=lambda q: fu(q[0]) + fv(q[1]))[2]

        return self.grid(slot, att, j, pick(lambda u: u, lambda v_: v_), pick(lambda u: -u, lambda v_: v_),
                         pick(lambda u: u, lambda v_: -v_), pick(lambda u: -u, lambda v_: -v_))

    def grid(self, slot: str, att: dict[str, Any], j: dict[str, Any], ul: np.ndarray, ur: np.ndarray,
             bl: np.ndarray, br: np.ndarray) -> dict[str, Any] | None:
        if not any(self._near(j, p) for p in (bl, ul, ur, br)):
            return None
        du, dv = ur - ul, bl - ul
        lu, lv = float(np.linalg.norm(du)), float(np.linalg.norm(dv))
        along_u = abs(du @ j["dir"]) / max(lu, 1e-9) >= abs(dv @ j["dir"]) / max(lv, 1e-9)
        n_along = min(24, max(4, math.ceil((lu if along_u else lv) / (j["r"] / 3))))
        # Weights vary only along the bone unless the piece wraps a limb; then ~30px cells across give inner vertices.
        n_across = min(16, max(1, math.ceil((lv if along_u else lu) / 30))) if j.get("wrap") else 1
        nu, nv = (n_along, n_across) if along_u else (n_across, n_along)
        # Hull vertices first, as one closed loop, inner vertices after.
        ring = ([(i, 0) for i in range(nu)] + [(nu, k) for k in range(nv)] + [(i, nv) for i in range(nu, 0, -1)]
                + [(0, k) for k in range(nv, 0, -1)])
        order = ring + [(i, k) for k in range(1, nv) for i in range(1, nu)]
        index = {p: n for n, p in enumerate(order)}
        tris: list[int] = []
        for k in range(nv):
            for i in range(nu):
                q = [index[(i, k)], index[(i + 1, k)], index[(i + 1, k + 1)], index[(i, k + 1)]]
                tris += [q[0], q[1], q[2], q[0], q[2], q[3]]
        bone = self.slot_bone[slot]
        uvs = [(i / nu, k / nv) for i, k in order]
        verts = [self.reweight(j, (1 - u) * (1 - v) * ul + u * (1 - v) * ur + (1 - u) * v * bl + u * v * br,
                               [(bone, None, None, 1.0)]) for u, v in uvs]
        mesh: dict[str, Any] = {"type": "mesh", "uvs": [round(x, 5) for p in uvs for x in p], "triangles": tris,
                                "vertices": self._flat(verts), "hull": len(ring), "width": att.get("width", 0),
                                "height": att.get("height", 0)}
        for k in ("name", "path", "color"):
            if k in att:
                mesh[k] = att[k]
        return mesh


def weld(doc: dict[str, Any], joints: list[str]) -> tuple[list[dict[str, Any]], int, list[str]]:
    """(new skins, attachments changed, skipped 'skin/slot/att' with attachment timelines); `doc` is not modified.
    Per piece: one bound to only one side of a joint is welded when its skin has a piece on the other side; one bound
    to both keeps the artist's weights unless they switch hard at the pivot; at sockets only child-side pieces move."""
    w = _Welder(doc, joints)
    deformed = _deformed(doc)
    skins = json.loads(json.dumps(doc.get("skins") or []))
    changed, skipped = 0, []
    for skin in skins:
        w.skin = (skin["name"], skin.get("attachments") or {})
        entries = [(slot, atts, name) for slot, atts in (skin.get("attachments") or {}).items() if slot in w.slot_bone
                   for name, att in atts.items() if att.get("type", "region") in ("region", "mesh")]
        skin_bones = set().union(*(w.bound(slot, atts[name]) for slot, atts, name in entries))
        for slot, atts, name in entries:
            att = cur = atts[name]
            for j in w.joints:
                bones = w.bound(slot, cur)
                on_p, on_c = bool(bones & j["side"]), j["child"] in bones
                if not (on_p or on_c) or (j["socket"] and not on_c and not j["wrap"]):
                    continue
                if j["child"] in w.forced:
                    if j["parent"] not in bones or (on_c and not w.hard_blend(slot, cur, j)):
                        continue
                elif on_p and on_c:
                    if not w.hard_blend(slot, cur, j):
                        continue
                elif not (j["child"] in skin_bones if on_p else skin_bones & j["side"]):
                    continue
                if (skin["name"], slot, name) in deformed:
                    skipped.append(f"{skin['name']}/{slot}/{name}")
                    break
                if cur.get("type", "region") == "region":
                    cur = w.region_to_mesh(slot, cur, j) or cur
                elif w._parse(cur) is None and len(cur.get("uvs") or []) == 8:
                    cur = w.quad_to_mesh(slot, cur, j) or cur
                else:
                    cur = w.rebind(slot, cur, j)
            if cur is not att:
                atts[name] = cur
                changed += 1
    return skins, changed, skipped


def weld_text(text: str, joints: list[str]) -> tuple[str, dict[str, Any]]:
    """Skeleton text with only the `skins` member rewritten; every other byte is kept."""
    doc = json.loads(text)
    skins, changed, skipped = weld(doc, joints)
    body = json.dumps(skins, ensure_ascii=False, separators=(",", ":"))
    for key, _, vs, ve in _member_spans(text, text.index("{")):
        if key == "skins":
            text = text[:vs] + body + text[ve:]
            break
    by = {b["name"]: b for b in doc["bones"]}
    names = [b.rstrip("!") for b in joints]
    sockets = [b for b in names if _length(by, by[b].get("parent")) <= 0]
    return text, {"joints": names, "forced": [b[:-1] for b in joints if b.endswith("!")], "sockets": sockets,
                  "changed": changed, "skipped": sorted(set(skipped))}
