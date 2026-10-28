"""Spine 4.2 skeleton pose: a port of spine-core's world transform pass (update cache ordered by constraint `order`,
bone inherit modes, IK with softness/stretch/compress, transform and path constraints, constraint timelines).
Physics constraints keep their place in the update order but do not simulate. Checked against spine-core 4.2.119."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

BONE_CHANNELS = {
    "rotate": ("rotation", ("value",)), "translate": ("xy", ("x", "y")), "translatex": ("x", ("value",)),
    "translatey": ("y", ("value",)), "scale": ("scale", ("x", "y")), "scalex": ("scaleX", ("value",)),
    "scaley": ("scaleY", ("value",)), "shear": ("shear", ("x", "y")), "shearx": ("shearX", ("value",)),
    "sheary": ("shearY", ("value",)),
}
INHERITS = {"normal": 0, "onlytranslation": 1, "norotationorreflection": 2, "noscale": 3, "noscaleorreflection": 4}
RAD, DEG, PI2 = math.pi / 180, 180 / math.pi, 2 * math.pi


def _bezier_exact(t0: float, v0: float, t1: float, v1: float, cx1: float, cy1: float, cx2: float, cy2: float,
                  t: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(40):
        s = (lo + hi) / 2
        x = (1 - s) ** 3 * t0 + 3 * (1 - s) ** 2 * s * cx1 + 3 * (1 - s) * s * s * cx2 + s ** 3 * t1
        lo, hi = (s, hi) if x < t else (lo, s)
    s = (lo + hi) / 2
    return (1 - s) ** 3 * v0 + 3 * (1 - s) ** 2 * s * cy1 + 3 * (1 - s) * s * s * cy2 + s ** 3 * v1


def _bezier_at(t0: float, v0: float, t1: float, v1: float, cx1: float, cy1: float, cx2: float, cy2: float,
               t: float) -> float:
    # Spine runtimes sample the curve at 9 forward-differenced points and interpolate linearly between them
    # (CurveTimeline.setBezier/getBezierValue); matching that keeps far chain tips within ~0.01px of the game.
    tmpx, tmpy = (t0 - cx1 * 2 + cx2) * 0.03, (v0 - cy1 * 2 + cy2) * 0.03
    dddx, dddy = ((cx1 - cx2) * 3 - t0 + t1) * 0.006, ((cy1 - cy2) * 3 - v0 + v1) * 0.006
    ddx, ddy = tmpx * 2 + dddx, tmpy * 2 + dddy
    dx, dy = (cx1 - t0) * 0.3 + tmpx + dddx * 0.16666667, (cy1 - v0) * 0.3 + tmpy + dddy * 0.16666667
    x, y = t0 + dx, v0 + dy
    px, py = t0, v0
    for _ in range(9):
        if x >= t:
            return py + (t - px) / ((x - px) or 1) * (y - py)
        px, py = x, y
        dx, dy = dx + ddx, dy + ddy
        ddx, ddy = ddx + dddx, ddy + dddy
        x, y = x + dx, y + dy
    return py + (t - px) / ((t1 - px) or 1) * (v1 - py)


def sample(keys: list[dict[str, Any]], t: float, fields: tuple[str, ...], default: float, game: bool = False
           ) -> tuple[float, ...]:
    """Value of a Spine 4.2 timeline at time t (keys hold offsets; missing fields = default). `game` samples curves
    the way the runtimes do (piecewise linear); the exact curve keeps derivatives smooth for motion analysis."""
    if not keys:
        return tuple(default for _ in fields)
    vals = [tuple(float(k.get(f, default)) for f in fields) for k in keys]
    times = [float(k.get("time", 0.0)) for k in keys]
    if t < times[0]:
        return tuple(default for _ in fields)
    i = max(j for j in range(len(keys)) if times[j] <= t)
    if i == len(keys) - 1:
        return vals[i]
    curve = keys[i].get("curve")
    if curve == "stepped":
        return vals[i]
    t0, t1 = times[i], times[i + 1]
    out = []
    for c, (a, b) in enumerate(zip(vals[i], vals[i + 1])):
        if isinstance(curve, list) and len(curve) >= 4 * (c + 1):
            out.append((_bezier_at if game else _bezier_exact)(t0, a, t1, b, *curve[4 * c:4 * c + 4], t))
        else:
            out.append(a + (b - a) * ((t - t0) / ((t1 - t0) or 1)))
    return tuple(out)


def _wrap_pi(r: float) -> float:
    if r > math.pi:
        return r - PI2
    if r < -math.pi:
        return r + PI2
    return r


def _wrap_180(r: float) -> float:
    if r > 180:
        return r - 360
    if r < -180:
        return r + 360
    return r


@dataclass
class BonePose:
    x: float
    y: float
    rotation: float
    scaleX: float
    scaleY: float
    shearX: float
    shearY: float


class _Bone:
    __slots__ = ("name", "data", "parent", "children", "length", "inherit", "local", "ax", "ay", "arotation",
                 "ascaleX", "ascaleY", "ashearX", "ashearY", "a", "b", "c", "d", "wx", "wy", "sorted")

    def __init__(self, data: dict[str, Any], parent: _Bone | None, local: BonePose):
        self.name, self.data, self.parent, self.children = data["name"], data, parent, []
        self.length = float(data.get("length", 0.0))
        self.inherit = INHERITS.get(str(data.get("inherit", data.get("transform", "normal"))).lower(), 0)
        self.local = local
        self.a = self.b = self.c = self.d = self.wx = self.wy = 0.0
        self.sorted = False
        self.reset_applied()

    def reset_applied(self) -> None:
        p = self.local
        self.ax, self.ay, self.arotation = p.x, p.y, p.rotation
        self.ascaleX, self.ascaleY, self.ashearX, self.ashearY = p.scaleX, p.scaleY, p.shearX, p.shearY

    def update(self) -> None:
        self.update_with(self.ax, self.ay, self.arotation, self.ascaleX, self.ascaleY, self.ashearX, self.ashearY)

    def update_with(self, x: float, y: float, rotation: float, sx: float, sy: float, shx: float, shy: float) -> None:
        self.ax, self.ay, self.arotation, self.ascaleX, self.ascaleY, self.ashearX, self.ashearY = (
            x, y, rotation, sx, sy, shx, shy)
        p = self.parent
        if p is None:
            rx, ry = (rotation + shx) * RAD, (rotation + 90 + shy) * RAD
            self.a, self.b = math.cos(rx) * sx, math.cos(ry) * sy
            self.c, self.d = math.sin(rx) * sx, math.sin(ry) * sy
            self.wx, self.wy = x, y
            return
        pa, pb, pc, pd = p.a, p.b, p.c, p.d
        self.wx = pa * x + pb * y + p.wx
        self.wy = pc * x + pd * y + p.wy
        mode = self.inherit
        if mode == 0:
            rx, ry = (rotation + shx) * RAD, (rotation + 90 + shy) * RAD
            la, lb, lc, ld = math.cos(rx) * sx, math.cos(ry) * sy, math.sin(rx) * sx, math.sin(ry) * sy
            self.a, self.b = pa * la + pb * lc, pa * lb + pb * ld
            self.c, self.d = pc * la + pd * lc, pc * lb + pd * ld
        elif mode == 1:
            rx, ry = (rotation + shx) * RAD, (rotation + 90 + shy) * RAD
            self.a, self.b = math.cos(rx) * sx, math.cos(ry) * sy
            self.c, self.d = math.sin(rx) * sx, math.sin(ry) * sy
        elif mode == 2:
            s = pa * pa + pc * pc
            if s > 0.0001:
                s = abs(pa * pd - pb * pc) / s
                pb, pd = pc * s, pa * s
                prx = math.atan2(pc, pa) * DEG
            else:
                pa = pc = 0.0
                prx = 90 - math.atan2(pd, pb) * DEG
            rx, ry = (rotation + shx - prx) * RAD, (rotation + shy - prx + 90) * RAD
            la, lb, lc, ld = math.cos(rx) * sx, math.cos(ry) * sy, math.sin(rx) * sx, math.sin(ry) * sy
            self.a, self.b = pa * la - pb * lc, pa * lb - pb * ld
            self.c, self.d = pc * la + pd * lc, pc * lb + pd * ld
        else:
            r = rotation * RAD
            cos, sin = math.cos(r), math.sin(r)
            za, zc = pa * cos + pb * sin, pc * cos + pd * sin
            s = math.sqrt(za * za + zc * zc)
            if s > 0.00001:
                s = 1 / s
            za, zc = za * s, zc * s
            s = math.sqrt(za * za + zc * zc)
            if mode == 3 and pa * pd - pb * pc < 0:
                s = -s
            r = math.pi / 2 + math.atan2(zc, za)
            zb, zd = math.cos(r) * s, math.sin(r) * s
            shx_r, shy_r = shx * RAD, (90 + shy) * RAD
            la, lb, lc, ld = math.cos(shx_r) * sx, math.cos(shy_r) * sy, math.sin(shx_r) * sx, math.sin(shy_r) * sy
            self.a, self.b = za * la + zb * lc, za * lb + zb * ld
            self.c, self.d = zc * la + zd * lc, zc * lb + zd * ld

    def update_applied(self) -> None:
        p = self.parent
        if p is None:
            self.ax, self.ay = self.wx, self.wy
            self.arotation = math.atan2(self.c, self.a) * DEG
            self.ascaleX = math.hypot(self.a, self.c)
            self.ascaleY = math.hypot(self.b, self.d)
            self.ashearX = 0.0
            self.ashearY = math.atan2(self.a * self.b + self.c * self.d, self.a * self.d - self.b * self.c) * DEG
            return
        pa, pb, pc, pd = p.a, p.b, p.c, p.d
        pid = 1 / (pa * pd - pb * pc)
        ia, ib, ic, id_ = pd * pid, pb * pid, pc * pid, pa * pid
        dx, dy = self.wx - p.wx, self.wy - p.wy
        self.ax, self.ay = dx * ia - dy * ib, dy * id_ - dx * ic
        if self.inherit == 1:
            ra, rb, rc, rd = self.a, self.b, self.c, self.d
        else:
            if self.inherit == 2:
                s = abs(pa * pd - pb * pc) / (pa * pa + pc * pc)
                pb, pd = -pc * s, pa * s
                pid = 1 / (pa * pd - pb * pc)
                ia, ib = pd * pid, pb * pid
            elif self.inherit in (3, 4):
                cos, sin = math.cos(self.local.rotation * RAD), math.sin(self.local.rotation * RAD)
                pa, pc = pa * cos + pb * sin, pc * cos + pd * sin
                s = math.sqrt(pa * pa + pc * pc)
                if s > 0.00001:
                    s = 1 / s
                pa, pc = pa * s, pc * s
                s = math.sqrt(pa * pa + pc * pc)
                if self.inherit == 3 and pid < 0:
                    s = -s
                r = math.pi / 2 + math.atan2(pc, pa)
                pb, pd = math.cos(r) * s, math.sin(r) * s
                pid = 1 / (pa * pd - pb * pc)
                ia, ib, ic, id_ = pd * pid, pb * pid, pc * pid, pa * pid
            ra, rb = ia * self.a - ib * self.c, ia * self.b - ib * self.d
            rc, rd = id_ * self.c - ic * self.a, id_ * self.d - ic * self.b
        self.ashearX = 0.0
        self.ascaleX = math.hypot(ra, rc)
        if self.ascaleX > 0.0001:
            det = ra * rd - rb * rc
            self.ascaleY = det / self.ascaleX
            self.ashearY = -math.atan2(ra * rb + rc * rd, det) * DEG
            self.arotation = math.atan2(rc, ra) * DEG
        else:
            self.ascaleX = 0.0
            self.ascaleY = math.hypot(rb, rd)
            self.ashearY = 0.0
            self.arotation = 90 - math.atan2(rd, rb) * DEG

    def local_to_world(self, x: float, y: float) -> tuple[float, float]:
        return x * self.a + y * self.b + self.wx, x * self.c + y * self.d + self.wy


def apply_bone_timelines(b: dict[str, Any], tl: dict[str, list], t: float) -> BonePose:
    p = BonePose(b.get("x", 0.0), b.get("y", 0.0), b.get("rotation", 0.0), b.get("scaleX", 1.0),
                 b.get("scaleY", 1.0), b.get("shearX", 0.0), b.get("shearY", 0.0))
    for kind, keys in tl.items():
        if kind not in BONE_CHANNELS:
            continue
        target, fields = BONE_CHANNELS[kind]
        default = 1.0 if kind.startswith("scale") else 0.0
        v = sample(keys, t, fields, default, True)
        match target:
            case "rotation":
                p.rotation += v[0]
            case "xy":
                p.x, p.y = p.x + v[0], p.y + v[1]
            case "x" | "y" | "shearX" | "shearY":
                setattr(p, target, getattr(p, target) + v[0])
            case "scale":
                p.scaleX, p.scaleY = p.scaleX * v[0], p.scaleY * v[1]
            case "scaleX" | "scaleY":
                setattr(p, target, getattr(p, target) * v[0])
            case "shear":
                p.shearX, p.shearY = p.shearX + v[0], p.shearY + v[1]
    return p


def _keyed(keys: list[dict[str, Any]], t: float, defaults: dict[str, float], copies: dict[str, str]
           ) -> dict[str, float] | None:
    """Constraint timeline values at t; None before the first key (the setup values stay)."""
    if not keys or t < float(keys[0].get("time", 0.0)):
        return None
    filled = []
    for k in keys:
        f = {**k, **{n: k.get(n, d) for n, d in defaults.items()}}
        for n, src in copies.items():
            f[n] = k.get(n, f[src])
        filled.append(f)
    return dict(zip(defaults, sample(filled, t, tuple(defaults), 0.0, True)))


def _at_or_before(keys: list[dict[str, Any]], t: float) -> dict[str, Any]:
    return max((k for k in keys if float(k.get("time", 0.0)) <= t), key=lambda k: float(k.get("time", 0.0)))


class Pose:
    """World transforms of one skeleton pose: bone -> (2x2 matrix, world position), Spine y-up."""

    def __init__(self, doc: dict[str, Any], anim: str | None = None, t: float = 0.0):
        self.doc = doc
        self.bones = doc["bones"]
        self.index = {b["name"]: i for i, b in enumerate(self.bones)}
        a = ((doc.get("animations") or {}).get(anim) or {}) if anim else {}
        timelines = a.get("bones") or {}
        self.local = {b["name"]: apply_bone_timelines(b, timelines.get(b["name"], {}), t) for b in self.bones}
        self._b: dict[str, _Bone] = {}
        for b in self.bones:
            parent = self._b.get(b["parent"]) if "parent" in b else None
            self._b[b["name"]] = bone = _Bone(b, parent, self.local[b["name"]])
            if parent:
                parent.children.append(bone)
        self.children = {n: [c.name for c in b.children] for n, b in self._b.items() if b.children}
        self._constraints = self._constraint_state(a, t)
        for kind, c in self._cache():
            if kind == "bone":
                c.update()
            elif kind == "ik":
                self._ik(c)
            elif kind == "transform":
                self._transform(c)
            elif kind == "path":
                self._path(c)
        self.world: dict[str, tuple[np.ndarray, np.ndarray]] = {
            n: (np.array([[b.a, b.b], [b.c, b.d]]), np.array([b.wx, b.wy])) for n, b in self._b.items()}

    def _constraint_state(self, anim: dict[str, Any], t: float) -> list[tuple[str, dict[str, Any]]]:
        out: list[tuple[str, dict[str, Any]]] = []
        for c in self.doc.get("ik") or []:
            s = {**c, "mix": c.get("mix", 1.0), "softness": c.get("softness", 0.0)}
            keys = (anim.get("ik") or {}).get(c["name"])
            v = _keyed(keys or [], t, {"mix": 1.0, "softness": 0.0}, {})
            if v:
                k = _at_or_before(keys, t)
                s.update(v, bendPositive=k.get("bendPositive", True), compress=k.get("compress", False),
                         stretch=k.get("stretch", False))
            out.append(("ik", s))
        for c in self.doc.get("transform") or []:
            mixes = {"mixRotate": c.get("mixRotate", 1.0), "mixX": c.get("mixX", 1.0),
                     "mixScaleX": c.get("mixScaleX", 1.0), "mixShearY": c.get("mixShearY", 1.0)}
            mixes["mixY"] = c.get("mixY", mixes["mixX"])
            mixes["mixScaleY"] = c.get("mixScaleY", mixes["mixScaleX"])
            v = _keyed((anim.get("transform") or {}).get(c["name"]) or [], t,
                       {"mixRotate": 1.0, "mixX": 1.0, "mixY": 1.0, "mixScaleX": 1.0, "mixScaleY": 1.0,
                        "mixShearY": 1.0}, {"mixY": "mixX", "mixScaleY": "mixScaleX"})
            out.append(("transform", {**c, **mixes, **(v or {})}))
        for c in self.doc.get("path") or []:
            s = {**c, "position": c.get("position", 0.0), "spacing": c.get("spacing", 0.0),
                 "mixRotate": c.get("mixRotate", 1.0), "mixX": c.get("mixX", 1.0)}
            s["mixY"] = c.get("mixY", s["mixX"])
            tl = (anim.get("path") or {}).get(c["name"]) or {}
            for ch in ("position", "spacing"):
                v = _keyed(tl.get(ch) or [], t, {"value": 0.0}, {})
                if v:
                    s[ch] = v["value"]
            s.update(_keyed(tl.get("mix") or [], t, {"mixRotate": 1.0, "mixX": 1.0, "mixY": 1.0},
                            {"mixY": "mixX"}) or {})
            out.append(("path", s))
        for c in self.doc.get("physics") or []:
            out.append(("physics", c))
        return out

    def _cache(self) -> list[tuple[str, Any]]:
        cache: list[tuple[str, Any]] = []

        def sort_bone(b: _Bone | None) -> None:
            if b is None or b.sorted:
                return
            sort_bone(b.parent)
            b.sorted = True
            cache.append(("bone", b))

        def sort_reset(bones: list[_Bone]) -> None:
            for b in bones:
                if b.sorted:
                    sort_reset(b.children)
                b.sorted = False

        by = self._b
        for kind, c in sorted(self._constraints, key=lambda kc: kc[1].get("order", 0)):
            if kind == "ik":
                sort_bone(by[c["target"]])
                bones = [by[n] for n in c["bones"]]
                sort_bone(bones[0])
                if len(bones) > 1:
                    sort_bone(bones[-1])
                cache.append((kind, c))
                sort_reset(bones[0].children)
                bones[-1].sorted = True
            elif kind == "transform":
                sort_bone(by[c["target"]])
                bones = [by[n] for n in c["bones"]]
                for b in bones:
                    if c.get("local"):
                        sort_bone(b.parent)
                    sort_bone(b)
                cache.append((kind, c))
                for b in bones:
                    sort_reset(b.children)
                for b in bones:
                    b.sorted = True
            elif kind == "path":
                slot = next((s for s in self.doc.get("slots") or [] if s["name"] == c["target"]), None)
                if slot is None:
                    continue
                c["_slot"] = slot
                for att in self._path_attachments(slot["name"]):
                    for bi in self._path_bones(att):
                        sort_bone(by[self.bones[bi]["name"]])
                    if not self._path_bones(att):
                        sort_bone(by[slot["bone"]])
                bones = [by[n] for n in c["bones"]]
                for b in bones:
                    sort_bone(b)
                cache.append((kind, c))
                for b in bones:
                    sort_reset(b.children)
                for b in bones:
                    b.sorted = True
            else:
                b = by[c["bone"]]
                sort_bone(b)
                sort_reset(b.children)
                b.sorted = True
        for b in by.values():
            sort_bone(b)
        return cache

    def _ik(self, c: dict[str, Any]) -> None:
        mix = float(c["mix"])
        if mix == 0:
            return
        target = self._b[c["target"]]
        bones = [self._b[n] for n in c["bones"]]
        if len(bones) == 1:
            self._ik1(bones[0], target.wx, target.wy, bool(c.get("compress")), bool(c.get("stretch")),
                      bool(c.get("uniform")), mix)
        else:
            self._ik2(bones[0], bones[1], target.wx, target.wy, 1 if c.get("bendPositive", True) else -1,
                      bool(c.get("stretch")), bool(c.get("uniform")), float(c.get("softness", 0.0)), mix)

    @staticmethod
    def _ik1(bone: _Bone, tx: float, ty: float, compress: bool, stretch: bool, uniform: bool, alpha: float) -> None:
        p = bone.parent
        if p is None:
            return
        pa, pb, pc, pd = p.a, p.b, p.c, p.d
        rotation_ik = -bone.ashearX - bone.arotation
        if bone.inherit == 1:
            dx, dy = tx - bone.wx, ty - bone.wy
        else:
            if bone.inherit == 2:
                s = abs(pa * pd - pb * pc) / max(0.0001, pa * pa + pc * pc)
                pb, pd = -pc * s, pa * s
                rotation_ik += math.atan2(pc, pa) * DEG
            x, y = tx - p.wx, ty - p.wy
            d = pa * pd - pb * pc
            if abs(d) <= 0.0001:
                dx = dy = 0.0
            else:
                dx, dy = (x * pd - y * pb) / d - bone.ax, (y * pa - x * pc) / d - bone.ay
        rotation_ik += math.atan2(dy, dx) * DEG
        if bone.ascaleX < 0:
            rotation_ik += 180
        rotation_ik = _wrap_180(rotation_ik)
        sx, sy = bone.ascaleX, bone.ascaleY
        if compress or stretch:
            if bone.inherit in (3, 4):
                dx, dy = tx - bone.wx, ty - bone.wy
            b = bone.length * sx
            if b > 0.0001:
                dd = dx * dx + dy * dy
                if (compress and dd < b * b) or (stretch and dd > b * b):
                    s = (math.sqrt(dd) / b - 1) * alpha + 1
                    sx *= s
                    if uniform:
                        sy *= s
        bone.update_with(bone.ax, bone.ay, bone.arotation + rotation_ik * alpha, sx, sy, bone.ashearX, bone.ashearY)

    def _ik2(self, parent: _Bone, child: _Bone, tx: float, ty: float, bend: int, stretch: bool, uniform: bool,
             softness: float, alpha: float) -> None:
        if parent.inherit != 0 or child.inherit != 0:
            return
        px, py, psx, psy = parent.ax, parent.ay, parent.ascaleX, parent.ascaleY
        sx, sy, csx = psx, psy, child.ascaleX
        if psx < 0:
            psx, os1, s2 = -psx, 180, -1
        else:
            os1, s2 = 0, 1
        if psy < 0:
            psy, s2 = -psy, -s2
        if csx < 0:
            csx, os2 = -csx, 180
        else:
            os2 = 0
        cx = child.ax
        a, b, c, d = parent.a, parent.b, parent.c, parent.d
        u = abs(psx - psy) <= 0.0001
        if not u or stretch:
            cy = 0.0
            cwx, cwy = a * cx + parent.wx, c * cx + parent.wy
        else:
            cy = child.ay
            cwx, cwy = a * cx + b * cy + parent.wx, c * cx + d * cy + parent.wy
        pp = parent.parent
        if pp is None:
            return
        a, b, c, d = pp.a, pp.b, pp.c, pp.d
        id_ = a * d - b * c
        x, y = cwx - pp.wx, cwy - pp.wy
        id_ = 0.0 if abs(id_) <= 0.0001 else 1 / id_
        dx, dy = (x * d - y * b) * id_ - px, (y * a - x * c) * id_ - py
        l1, l2 = math.hypot(dx, dy), child.length * csx
        if l1 < 0.0001:
            self._ik1(parent, tx, ty, False, stretch, False, alpha)
            child.update_with(cx, cy, 0, child.ascaleX, child.ascaleY, child.ashearX, child.ashearY)
            return
        x, y = tx - pp.wx, ty - pp.wy
        ttx, tty = (x * d - y * b) * id_ - px, (y * a - x * c) * id_ - py
        dd = ttx * ttx + tty * tty
        if softness != 0:
            softness *= psx * (csx + 1) * 0.5
            td = math.sqrt(dd)
            sd = td - l1 - l2 * psx + softness
            if sd > 0:
                p = min(1.0, sd / (softness * 2)) - 1
                p = (sd - softness * (1 - p * p)) / td
                ttx -= p * ttx
                tty -= p * tty
                dd = ttx * ttx + tty * tty
        if u:
            l2 *= psx
            cos = (dd - l1 * l1 - l2 * l2) / (2 * l1 * l2)
            if cos < -1:
                cos, a2 = -1.0, math.pi * bend
            elif cos > 1:
                cos, a2 = 1.0, 0.0
                if stretch:
                    a = (math.sqrt(dd) / (l1 + l2) - 1) * alpha + 1
                    sx *= a
                    if uniform:
                        sy *= a
            else:
                a2 = math.acos(cos) * bend
            a = l1 + l2 * cos
            b = l2 * math.sin(a2)
            a1 = math.atan2(tty * a - ttx * b, ttx * a + tty * b)
        else:
            a1, a2 = self._ik2_scaled(psx, psy, l1, l2, ttx, tty, dd, bend)
        os = math.atan2(cy, cx) * s2
        rotation = parent.arotation
        r1 = _wrap_180((a1 - os) * DEG + os1 - rotation)
        parent.update_with(px, py, rotation + r1 * alpha, sx, sy, 0, 0)
        rotation = child.arotation
        r2 = _wrap_180(((a2 + os) * DEG - child.ashearX) * s2 + os2 - rotation)
        child.update_with(cx, cy, rotation + r2 * alpha, child.ascaleX, child.ascaleY, child.ashearX, child.ashearY)

    @staticmethod
    def _ik2_scaled(psx: float, psy: float, l1: float, l2: float, tx: float, ty: float, dd: float, bend: int
                    ) -> tuple[float, float]:
        a, b = psx * l2, psy * l2
        aa, bb, ta = a * a, b * b, math.atan2(ty, tx)
        c = bb * l1 * l1 + aa * dd - aa * bb
        c1, c2 = -2 * bb * l1, bb - aa
        d = c1 * c1 - 4 * c2 * c
        if d >= 0:
            q = math.sqrt(d)
            if c1 < 0:
                q = -q
            q = -(c1 + q) * 0.5
            r0, r1 = q / c2, c / q
            r = r0 if abs(r0) < abs(r1) else r1
            r0 = dd - r * r
            if r0 >= 0:
                y = math.sqrt(r0) * bend
                return ta - math.atan2(y, r), math.atan2(y / psy, (r - l1) / psx)
        min_angle, min_x, min_y = math.pi, l1 - a, 0.0
        min_dist = min_x * min_x
        max_angle, max_x, max_y = 0.0, l1 + a, 0.0
        max_dist = max_x * max_x
        c = -a * l1 / (aa - bb)
        if -1 <= c <= 1:
            c = math.acos(c)
            x, y = a * math.cos(c) + l1, b * math.sin(c)
            d = x * x + y * y
            if d < min_dist:
                min_angle, min_dist, min_x, min_y = c, d, x, y
            if d > max_dist:
                max_angle, max_dist, max_x, max_y = c, d, x, y
        if dd <= (min_dist + max_dist) * 0.5:
            return ta - math.atan2(min_y * bend, min_x), min_angle * bend
        return ta - math.atan2(max_y * bend, max_x), max_angle * bend

    def _transform(self, c: dict[str, Any]) -> None:
        mr, mx, my = float(c["mixRotate"]), float(c["mixX"]), float(c["mixY"])
        msx, msy, msh = float(c["mixScaleX"]), float(c["mixScaleY"]), float(c["mixShearY"])
        if not any((mr, mx, my, msx, msy, msh)):
            return
        t = self._b[c["target"]]
        ox, oy = float(c.get("x", 0.0)), float(c.get("y", 0.0))
        orot, osx, osy, osh = (float(c.get(k, 0.0)) for k in ("rotation", "scaleX", "scaleY", "shearY"))
        bones = [self._b[n] for n in c["bones"]]
        if c.get("local"):
            for b in bones:
                if c.get("relative"):
                    b.update_with(b.ax + (t.ax + ox) * mx, b.ay + (t.ay + oy) * my,
                                  b.arotation + (t.arotation + orot) * mr,
                                  b.ascaleX * ((t.ascaleX - 1 + osx) * msx + 1),
                                  b.ascaleY * ((t.ascaleY - 1 + osy) * msy + 1), b.ashearX,
                                  b.ashearY + (t.ashearY + osh) * msh)
                    continue
                rotation = b.arotation
                if mr:
                    rotation += (t.arotation - rotation + orot) * mr
                x = b.ax + (t.ax - b.ax + ox) * mx
                y = b.ay + (t.ay - b.ay + oy) * my
                sx, sy = b.ascaleX, b.ascaleY
                if msx and sx:
                    sx = (sx + (t.ascaleX - sx + osx) * msx) / sx
                if msy and sy:
                    sy = (sy + (t.ascaleY - sy + osy) * msy) / sy
                shy = b.ashearY
                if msh:
                    shy += (t.ashearY - shy + osh) * msh
                b.update_with(x, y, rotation, sx, sy, b.ashearX, shy)
            return
        ta, tb, tc, td = t.a, t.b, t.c, t.d
        reflect = RAD if ta * td - tb * tc > 0 else -RAD
        off_rot, off_shy = orot * reflect, osh * reflect
        relative = bool(c.get("relative"))
        for b in bones:
            if mr:
                a, bb, cc, d = b.a, b.b, b.c, b.d
                r = math.atan2(tc, ta) + off_rot if relative else math.atan2(tc, ta) - math.atan2(cc, a) + off_rot
                r = _wrap_pi(r) * mr
                cos, sin = math.cos(r), math.sin(r)
                b.a, b.b = cos * a - sin * cc, cos * bb - sin * d
                b.c, b.d = sin * a + cos * cc, sin * bb + cos * d
            if mx or my:
                wx, wy = t.local_to_world(ox, oy)
                if relative:
                    b.wx += wx * mx
                    b.wy += wy * my
                else:
                    b.wx += (wx - b.wx) * mx
                    b.wy += (wy - b.wy) * my
            if msx:
                if relative:
                    s = (math.hypot(ta, tc) - 1 + osx) * msx + 1
                else:
                    s = math.hypot(b.a, b.c)
                    if s:
                        s = (s + (math.hypot(ta, tc) - s + osx) * msx) / s
                b.a *= s
                b.c *= s
            if msy:
                if relative:
                    s = (math.hypot(tb, td) - 1 + osy) * msy + 1
                else:
                    s = math.hypot(b.b, b.d)
                    if s:
                        s = (s + (math.hypot(tb, td) - s + osy) * msy) / s
                b.b *= s
                b.d *= s
            if msh > 0:
                bb, d = b.b, b.d
                by = math.atan2(d, bb)
                if relative:
                    r = _wrap_pi(math.atan2(td, tb) - math.atan2(tc, ta))
                    r = by + (r - math.pi / 2 + off_shy) * msh
                else:
                    r = _wrap_pi(math.atan2(td, tb) - math.atan2(tc, ta) - (by - math.atan2(b.c, b.a)))
                    r = by + (r + off_shy) * msh
                s = math.hypot(bb, d)
                b.b, b.d = math.cos(r) * s, math.sin(r) * s
            b.update_applied()

    def _path_attachments(self, slot: str) -> list[dict[str, Any]]:
        out = []
        for skin in self.doc.get("skins") or []:
            for att in ((skin.get("attachments") or {}).get(slot) or {}).values():
                if att.get("type") == "path":
                    out.append(att)
        return out

    @staticmethod
    def _path_bones(att: dict[str, Any]) -> list[int]:
        verts, n = att.get("vertices") or [], int(att.get("vertexCount", 0))
        if len(verts) == 2 * n:
            return []
        out, k = [], 0
        while k < len(verts):
            c = int(verts[k])
            out += [int(verts[k + 1 + 4 * j]) for j in range(c)]
            k += 1 + 4 * c
        return out

    def _path_world(self, att: dict[str, Any], slot_bone: str) -> list[float]:
        verts, n = att.get("vertices") or [], int(att.get("vertexCount", 0))
        out: list[float] = []
        if len(verts) == 2 * n:
            b = self._b[slot_bone]
            for i in range(n):
                out += b.local_to_world(verts[2 * i], verts[2 * i + 1])
            return out
        k = 0
        for _ in range(n):
            c = int(verts[k])
            wx = wy = 0.0
            for j in range(c):
                bi, vx, vy, w = verts[k + 1 + 4 * j:k + 5 + 4 * j]
                x, y = self._b[self.bones[int(bi)]["name"]].local_to_world(vx, vy)
                wx, wy = wx + x * w, wy + y * w
            out += [wx, wy]
            k += 1 + 4 * c
        return out

    def _path(self, c: dict[str, Any]) -> None:
        slot = c["_slot"]
        name = slot.get("attachment")
        att = next((a for s in self.doc.get("skins") or []
                    for a in [((s.get("attachments") or {}).get(slot["name"]) or {}).get(name or "")]
                    if a and a.get("type") == "path"), None)
        mr, mx, my = float(c["mixRotate"]), float(c["mixX"]), float(c["mixY"])
        if att is None or not (mr or mx or my):
            return
        rotate_mode = str(c.get("rotateMode", "tangent")).lower()
        spacing_mode = str(c.get("spacingMode", "length")).lower()
        tangents, scale = rotate_mode == "tangent", rotate_mode == "chainscale"
        bones = [self._b[n] for n in c["bones"]]
        count = len(bones)
        spaces_count = count if tangents else count + 1
        spaces = [0.0] * spaces_count
        lengths = [0.0] * count
        spacing = float(c["spacing"])
        if spacing_mode == "percent":
            if scale:
                for i in range(spaces_count - 1):
                    b = bones[i]
                    lengths[i] = math.hypot(b.length * b.a, b.length * b.c)
            for i in range(1, spaces_count):
                spaces[i] = spacing
        elif spacing_mode == "proportional":
            total = 0.0
            for i in range(spaces_count - 1):
                b = bones[i]
                if b.length < 0.00001:
                    spaces[i + 1] = spacing
                else:
                    length = math.hypot(b.length * b.a, b.length * b.c)
                    lengths[i] = length
                    spaces[i + 1] = length
                    total += length
            if total > 0:
                total = spaces_count / total * spacing
                for i in range(1, spaces_count):
                    spaces[i] *= total
        else:
            for i in range(spaces_count - 1):
                b = bones[i]
                if b.length < 0.00001:
                    spaces[i + 1] = spacing
                else:
                    length = math.hypot(b.length * b.a, b.length * b.c)
                    lengths[i] = length
                    spaces[i + 1] = ((b.length + spacing) if spacing_mode == "length" else spacing) * length / b.length
        positions = self._path_positions(att, slot["bone"], c, spaces, spaces_count, tangents, spacing_mode)
        bx, by = positions[0], positions[1]
        off_rot = float(c.get("rotation", 0.0))
        if off_rot == 0:
            tip = rotate_mode == "chain"
        else:
            tip = False
            p = self._b[slot["bone"]]
            off_rot *= RAD if p.a * p.d - p.b * p.c > 0 else -RAD
        p = 3
        for i, bone in enumerate(bones):
            bone.wx += (bx - bone.wx) * mx
            bone.wy += (by - bone.wy) * my
            x, y = positions[p], positions[p + 1]
            dx, dy = x - bx, y - by
            if scale and lengths[i] != 0:
                s = (math.hypot(dx, dy) / lengths[i] - 1) * mr + 1
                bone.a *= s
                bone.c *= s
            bx, by = x, y
            if mr > 0:
                a, b, cc, d = bone.a, bone.b, bone.c, bone.d
                if tangents:
                    r = positions[p - 1]
                elif spaces[i + 1] == 0:
                    r = positions[p + 2]
                else:
                    r = math.atan2(dy, dx)
                r -= math.atan2(cc, a)
                if tip:
                    cos, sin = math.cos(r), math.sin(r)
                    bx += (bone.length * (cos * a - sin * cc) - dx) * mr
                    by += (bone.length * (sin * a + cos * cc) - dy) * mr
                else:
                    r += off_rot
                r = _wrap_pi(r) * mr
                cos, sin = math.cos(r), math.sin(r)
                bone.a, bone.b = cos * a - sin * cc, cos * b - sin * d
                bone.c, bone.d = sin * a + cos * cc, sin * b + cos * d
            bone.update_applied()
            p += 3

    def _path_positions(self, att: dict[str, Any], slot_bone: str, c: dict[str, Any], spaces: list[float],
                        spaces_count: int, tangents: bool, spacing_mode: str) -> list[float]:
        position = float(c["position"])
        percent_pos = str(c.get("positionMode", "percent")).lower() == "percent"
        out = [0.0] * (spaces_count * 3 + 2)
        closed = bool(att.get("closed", False))
        allw = self._path_world(att, slot_bone)
        verts_len = len(allw)
        curve_count = verts_len // 6
        prev_curve = -1

        def window(start: int, count: int) -> list[float]:
            return allw[start:start + count]

        if not att.get("constantSpeed", True):
            lengths = att.get("lengths") or []
            curve_count -= 1 if closed else 2
            path_length = lengths[curve_count]
            if percent_pos:
                position *= path_length
            mult = path_length if spacing_mode == "percent" else (
                path_length / spaces_count if spacing_mode == "proportional" else 1.0)
            world: list[float] = [0.0] * 8
            curve = 0
            for i in range(spaces_count):
                o = 3 * i
                space = spaces[i] * mult
                position += space
                p = position
                if closed:
                    p %= path_length
                    if p < 0:
                        p += path_length
                    curve = 0
                elif p < 0:
                    if prev_curve != -2:
                        prev_curve = -2
                        world = window(2, 4) + world[4:]
                    self._before(p, world, 0, out, o)
                    continue
                elif p > path_length:
                    if prev_curve != -3:
                        prev_curve = -3
                        world = window(verts_len - 6, 4) + world[4:]
                    self._after(p - path_length, world, 0, out, o)
                    continue
                while True:
                    length = lengths[curve]
                    if p > length:
                        curve += 1
                        continue
                    p = p / length if curve == 0 else (p - lengths[curve - 1]) / (length - lengths[curve - 1])
                    break
                if curve != prev_curve:
                    prev_curve = curve
                    if closed and curve == curve_count:
                        world = window(verts_len - 4, 4) + window(0, 4)
                    else:
                        world = window(curve * 6 + 2, 8)
                self._curve_pos(p, *world[:8], out, o, tangents or (i > 0 and space == 0))
            return out

        if closed:
            verts_len += 2
            world = window(2, verts_len - 4) + window(0, 2)
            world += world[:2]
        else:
            curve_count -= 1
            verts_len -= 4
            world = window(2, verts_len)
        curves = [0.0] * curve_count
        path_length = 0.0
        x1, y1 = world[0], world[1]
        for i in range(curve_count):
            w = 2 + 6 * i
            cx1, cy1, cx2, cy2, x2, y2 = world[w:w + 6]
            tmpx, tmpy = (x1 - cx1 * 2 + cx2) * 0.1875, (y1 - cy1 * 2 + cy2) * 0.1875
            dddfx, dddfy = ((cx1 - cx2) * 3 - x1 + x2) * 0.09375, ((cy1 - cy2) * 3 - y1 + y2) * 0.09375
            ddfx, ddfy = tmpx * 2 + dddfx, tmpy * 2 + dddfy
            dfx = (cx1 - x1) * 0.75 + tmpx + dddfx * 0.16666667
            dfy = (cy1 - y1) * 0.75 + tmpy + dddfy * 0.16666667
            path_length += math.hypot(dfx, dfy)
            dfx, dfy = dfx + ddfx, dfy + ddfy
            ddfx, ddfy = ddfx + dddfx, ddfy + dddfy
            path_length += math.hypot(dfx, dfy)
            dfx, dfy = dfx + ddfx, dfy + ddfy
            path_length += math.hypot(dfx, dfy)
            dfx, dfy = dfx + ddfx + dddfx, dfy + ddfy + dddfy
            path_length += math.hypot(dfx, dfy)
            curves[i] = path_length
            x1, y1 = x2, y2
        if percent_pos:
            position *= path_length
        mult = path_length if spacing_mode == "percent" else (
            path_length / spaces_count if spacing_mode == "proportional" else 1.0)
        segments = [0.0] * 10
        curve_length = 0.0
        curve = segment = 0
        x1 = y1 = cx1 = cy1 = cx2 = cy2 = x2 = y2 = 0.0
        for i in range(spaces_count):
            o = 3 * i
            space = spaces[i] * mult
            position += space
            p = position
            if closed:
                p %= path_length
                if p < 0:
                    p += path_length
                curve = 0
            elif p < 0:
                self._before(p, world, 0, out, o)
                continue
            elif p > path_length:
                self._after(p - path_length, world, verts_len - 4, out, o)
                continue
            while True:
                length = curves[curve]
                if p > length:
                    curve += 1
                    continue
                p = p / length if curve == 0 else (p - curves[curve - 1]) / (length - curves[curve - 1])
                break
            if curve != prev_curve:
                prev_curve = curve
                ii = curve * 6
                x1, y1, cx1, cy1, cx2, cy2, x2, y2 = world[ii:ii + 8]
                tmpx, tmpy = (x1 - cx1 * 2 + cx2) * 0.03, (y1 - cy1 * 2 + cy2) * 0.03
                dddfx, dddfy = ((cx1 - cx2) * 3 - x1 + x2) * 0.006, ((cy1 - cy2) * 3 - y1 + y2) * 0.006
                ddfx, ddfy = tmpx * 2 + dddfx, tmpy * 2 + dddfy
                dfx = (cx1 - x1) * 0.3 + tmpx + dddfx * 0.16666667
                dfy = (cy1 - y1) * 0.3 + tmpy + dddfy * 0.16666667
                curve_length = math.hypot(dfx, dfy)
                segments[0] = curve_length
                for ii in range(1, 8):
                    dfx, dfy = dfx + ddfx, dfy + ddfy
                    ddfx, ddfy = ddfx + dddfx, ddfy + dddfy
                    curve_length += math.hypot(dfx, dfy)
                    segments[ii] = curve_length
                dfx, dfy = dfx + ddfx, dfy + ddfy
                curve_length += math.hypot(dfx, dfy)
                segments[8] = curve_length
                dfx, dfy = dfx + ddfx + dddfx, dfy + ddfy + dddfy
                curve_length += math.hypot(dfx, dfy)
                segments[9] = curve_length
                segment = 0
            p *= curve_length
            while True:
                length = segments[segment]
                if p > length:
                    segment += 1
                    continue
                p = p / length if segment == 0 else segment + (p - segments[segment - 1]) / (
                    length - segments[segment - 1])
                break
            self._curve_pos(p * 0.1, x1, y1, cx1, cy1, cx2, cy2, x2, y2, out, o, tangents or (i > 0 and space == 0))
        return out

    @staticmethod
    def _before(p: float, temp: list[float], i: int, out: list[float], o: int) -> None:
        x1, y1 = temp[i], temp[i + 1]
        r = math.atan2(temp[i + 3] - y1, temp[i + 2] - x1)
        out[o], out[o + 1], out[o + 2] = x1 + p * math.cos(r), y1 + p * math.sin(r), r

    @staticmethod
    def _after(p: float, temp: list[float], i: int, out: list[float], o: int) -> None:
        x1, y1 = temp[i + 2], temp[i + 3]
        r = math.atan2(y1 - temp[i + 1], x1 - temp[i])
        out[o], out[o + 1], out[o + 2] = x1 + p * math.cos(r), y1 + p * math.sin(r), r

    @staticmethod
    def _curve_pos(p: float, x1: float, y1: float, cx1: float, cy1: float, cx2: float, cy2: float, x2: float,
                   y2: float, out: list[float], o: int, tangents: bool) -> None:
        if p == 0 or math.isnan(p):
            out[o], out[o + 1], out[o + 2] = x1, y1, math.atan2(cy1 - y1, cx1 - x1)
            return
        tt, u = p * p, 1 - p
        ttt, uu = tt * p, u * u
        uuu, ut = uu * u, u * p
        ut3 = ut * 3
        uut3, utt3 = u * ut3, ut3 * p
        x = x1 * uuu + cx1 * uut3 + cx2 * utt3 + x2 * ttt
        y = y1 * uuu + cy1 * uut3 + cy2 * utt3 + y2 * ttt
        out[o], out[o + 1] = x, y
        if tangents:
            if p < 0.001:
                out[o + 2] = math.atan2(cy1 - y1, cx1 - x1)
            else:
                out[o + 2] = math.atan2(y - (y1 * uu + cy1 * ut * 2 + cy2 * tt), x - (x1 * uu + cx1 * ut * 2 + cx2 * tt))
