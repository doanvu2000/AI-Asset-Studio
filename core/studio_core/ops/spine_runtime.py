"""Minimal Spine 4.2 runtime for skeletons made in the Spine editor: pose (bone timelines + IK) and software render.

Covers what review renders need: normal-inherit bones, bezier/stepped bone timelines, 1/2-bone IK (uniform scale),
skins combined by name, region + mesh attachments from a packed atlas (offsets, 90° rotation, PMA pages).
Not covered: deform/attachment/draw-order timelines, transform/path/physics constraints, tint, blend modes, clipping.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..spine_atlas import Atlas, AtlasRegion, parse_atlas
from .spine import _draw_mesh, _over

BONE_CHANNELS = {
    "rotate": ("rotation", ("value",)), "translate": ("xy", ("x", "y")), "translatex": ("x", ("value",)),
    "translatey": ("y", ("value",)), "scale": ("scale", ("x", "y")), "scalex": ("scaleX", ("value",)),
    "scaley": ("scaleY", ("value",)), "shear": ("shear", ("x", "y")), "shearx": ("shearX", ("value",)),
    "sheary": ("shearY", ("value",)),
}


def _bezier_at(t0: float, v0: float, t1: float, v1: float, cx1: float, cy1: float, cx2: float, cy2: float,
               t: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(40):
        s = (lo + hi) / 2
        x = (1 - s) ** 3 * t0 + 3 * (1 - s) ** 2 * s * cx1 + 3 * (1 - s) * s * s * cx2 + s ** 3 * t1
        lo, hi = (s, hi) if x < t else (lo, s)
    s = (lo + hi) / 2
    return (1 - s) ** 3 * v0 + 3 * (1 - s) ** 2 * s * cy1 + 3 * (1 - s) * s * s * cy2 + s ** 3 * v1


def sample(keys: list[dict[str, Any]], t: float, fields: tuple[str, ...], default: float) -> tuple[float, ...]:
    """Value of a Spine 4.2 timeline at time t (keys hold offsets; missing fields = default)."""
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
            out.append(_bezier_at(t0, a, t1, b, *curve[4 * c:4 * c + 4], t))
        else:
            out.append(a + (b - a) * ((t - t0) / ((t1 - t0) or 1)))
    return tuple(out)


@dataclass
class BonePose:
    x: float
    y: float
    rotation: float
    scaleX: float
    scaleY: float
    shearX: float
    shearY: float


def _local(p: BonePose) -> np.ndarray:
    ry = p.rotation + 90 + p.shearY
    rx = p.rotation + p.shearX
    return np.array([[math.cos(math.radians(rx)) * p.scaleX, math.cos(math.radians(ry)) * p.scaleY],
                     [math.sin(math.radians(rx)) * p.scaleX, math.sin(math.radians(ry)) * p.scaleY]])


class Pose:
    """World transforms of one skeleton pose: bone -> (2x2 matrix, world position), Spine y-up."""

    def __init__(self, doc: dict[str, Any], anim: str | None = None, t: float = 0.0):
        self.doc = doc
        self.bones = doc["bones"]
        self.index = {b["name"]: i for i, b in enumerate(self.bones)}
        self.children: dict[str, list[str]] = {}
        for b in self.bones:
            if "parent" in b:
                self.children.setdefault(b["parent"], []).append(b["name"])
        timelines = (((doc.get("animations") or {}).get(anim) or {}).get("bones") or {}) if anim else {}
        self.local = {b["name"]: self._apply(b, timelines.get(b["name"], {}), t) for b in self.bones}
        self.world: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for b in self.bones:
            self._update(b["name"])
        for ik in sorted(doc.get("ik") or [], key=lambda c: c.get("order", 0)):
            self._ik(ik)

    @staticmethod
    def _apply(b: dict[str, Any], tl: dict[str, list], t: float) -> BonePose:
        p = BonePose(b.get("x", 0.0), b.get("y", 0.0), b.get("rotation", 0.0), b.get("scaleX", 1.0),
                     b.get("scaleY", 1.0), b.get("shearX", 0.0), b.get("shearY", 0.0))
        for kind, keys in tl.items():
            if kind not in BONE_CHANNELS:
                continue
            target, fields = BONE_CHANNELS[kind]
            default = 1.0 if kind.startswith("scale") else 0.0
            v = sample(keys, t, fields, default)
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

    def _update(self, name: str, p: BonePose | None = None) -> None:
        b = self.bones[self.index[name]]
        p = p or self.local[name]
        m = _local(p)
        pos = np.array([p.x, p.y])
        if "parent" in b:
            pm, pp = self.world[b["parent"]]
            self.world[name] = (pm @ m, pp + pm @ pos)
        else:
            self.world[name] = (m, pos)

    def _update_subtree(self, name: str, skip: set[str]) -> None:
        for c in self.children.get(name, []):
            if c not in skip:
                self._update(c)
            self._update_subtree(c, skip)

    def _ik(self, ik: dict[str, Any]) -> None:
        names = ik["bones"]
        tx, ty = self.world[ik["target"]][1]
        mix = ik.get("mix", 1.0)
        if mix <= 0:
            return
        if len(names) == 1:
            self._ik1(names[0], tx, ty, mix)
            self._update_subtree(names[0], set())
            return
        self._ik2(names[0], names[1], tx, ty, 1 if ik.get("bendPositive", True) else -1, ik.get("stretch", False), mix)
        self._update_subtree(names[0], {names[1]})

    def _ik1(self, name: str, tx: float, ty: float, mix: float) -> None:
        b = self.bones[self.index[name]]
        p = self.local[name]
        pm, pp = self.world[b["parent"]] if "parent" in b else (np.eye(2), np.zeros(2))
        lx, ly = np.linalg.solve(pm, np.array([tx, ty]) - pp) - (p.x, p.y)
        rot = math.degrees(math.atan2(ly, lx)) - p.shearX - p.rotation
        rot = (rot + 180) % 360 - 180
        self._update(name, BonePose(p.x, p.y, p.rotation + rot * mix, p.scaleX, p.scaleY, p.shearX, p.shearY))

    def _ik2(self, pn: str, cn: str, tx: float, ty: float, bend: int, stretch: bool, mix: float) -> None:
        # Port of spine-core IkConstraint.apply2 for uniformly scaled bones (softness 0).
        parent, child = self.local[pn], self.local[cn]
        psx, sx, sy = parent.scaleX, parent.scaleX, parent.scaleY
        os1, s2 = (180, -1) if psx < 0 else (0, 1)
        psx = abs(psx)
        if parent.scaleY < 0:
            s2 = -s2
        csx = abs(child.scaleX)
        os2 = 180 if child.scaleX < 0 else 0
        pm, pw = self.world[pn]
        cx, cy = child.x, (0.0 if stretch else child.y)
        cw = pm @ np.array([cx, cy]) + pw
        ppm, ppw = self.world[self.bones[self.index[pn]]["parent"]]
        inv = np.linalg.inv(ppm)
        d = inv @ (cw - ppw) - (parent.x, parent.y)
        l1 = math.hypot(*d)
        l2 = self.bones[self.index[cn]].get("length", 0.0) * csx
        if l1 < 1e-4:
            return
        tl = inv @ (np.array([tx, ty]) - ppw) - (parent.x, parent.y)
        dd = float(tl @ tl)
        l2 *= psx
        cos = (dd - l1 * l1 - l2 * l2) / (2 * l1 * l2) if l2 else 1.0
        if cos < -1:
            cos, a2 = -1.0, math.pi * bend
        elif cos > 1:
            cos, a2 = 1.0, 0.0
            if stretch and l1 + l2 > 0:
                sx *= (math.sqrt(dd) / (l1 + l2) - 1) * mix + 1
        else:
            a2 = math.acos(cos) * bend
        a, b = l1 + l2 * cos, l2 * math.sin(a2)
        a1 = math.atan2(tl[1] * a - tl[0] * b, tl[0] * a + tl[1] * b)
        os = math.atan2(cy, cx) * s2
        r1 = (math.degrees(a1 - os) + os1 - parent.rotation + 180) % 360 - 180
        self._update(pn, BonePose(parent.x, parent.y, parent.rotation + r1 * mix, sx, sy, 0, 0))
        r2 = ((math.degrees(a2 + os) - child.shearX) * s2 + os2 - child.rotation + 180) % 360 - 180
        self._update(cn, BonePose(cx, cy, child.rotation + r2 * mix, child.scaleX, child.scaleY, child.shearX,
                                  child.shearY))


def skin_attachments(doc: dict[str, Any], skins: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """slot -> attachment name -> attachment, merging 'default' and the named skins (later wins)."""
    by_name = {s["name"]: s for s in doc.get("skins") or []}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for name in ["default", *skins]:
        for slot, atts in (by_name.get(name) or {}).get("attachments", {}).items():
            out.setdefault(slot, {}).update(atts)
    return out


def resolve_skins(doc: dict[str, Any], parts: list[str]) -> tuple[list[str], list[str]]:
    """Skin names for 'category/name' or bare 'name_0' parts (matched as '*/name_0'); returns (found, missing)."""
    names = [s["name"] for s in doc.get("skins") or []]
    found, missing = [], []
    for p in (x.strip() for x in parts):
        if not p:
            continue
        hit = p if p in names else next((n for n in names if n.split("/")[-1] == p), None)
        (found if hit else missing).append(hit or p)
    return found, missing


class RegionImages:
    """Original-size, premultiplied ('RGBa') images of atlas regions, cut lazily from the page files."""

    def __init__(self, atlas: Atlas, pages: list[Path]):
        self.atlas, self.pages = atlas, pages
        self._pages: dict[int, Image.Image] = {}
        self._cache: dict[str, Image.Image] = {}

    @classmethod
    def from_files(cls, atlas_file: Path, pages: list[Path]) -> RegionImages:
        return cls(parse_atlas(atlas_file.read_text(encoding="utf-8-sig", errors="replace")), pages)

    def _page(self, i: int) -> Image.Image:
        if i not in self._pages:
            im = Image.open(self.pages[i]).convert("RGBA")
            pma = self.atlas.pages[i].pma
            self._pages[i] = Image.frombytes("RGBa", im.size, im.tobytes()) if pma else im.convert("RGBa")
        return self._pages[i]

    def get(self, name: str) -> Image.Image | None:
        if name in self._cache:
            return self._cache[name]
        r: AtlasRegion | None = self.atlas.regions.get(name)
        if r is None:
            return None
        w, h = (r.height, r.width) if r.degrees in (90, 270) else (r.width, r.height)
        crop = self._page(r.page).crop((r.x, r.y, r.x + w, r.y + h))
        if r.degrees == 90:
            crop = crop.transpose(Image.Transpose.ROTATE_270)
        elif r.degrees == 270:
            crop = crop.transpose(Image.Transpose.ROTATE_90)
        img = Image.new("RGBa", (r.orig_w, r.orig_h), (0, 0, 0, 0))
        img.paste(crop, (r.offset_x, r.orig_h - r.height - r.offset_y))
        self._cache[name] = img
        return img


def _visible(doc: dict[str, Any], atts: dict[str, dict[str, dict[str, Any]]]):
    for slot in doc["slots"]:
        name = slot.get("attachment")
        att = (atts.get(slot["name"]) or {}).get(name) if name else None
        if att is None or att.get("type", "region") not in ("region", "mesh"):
            continue
        yield slot, att, att.get("path") or att.get("name") or name


def _region_corners(att: dict[str, Any], pose: Pose, bone: str) -> np.ndarray:
    m, p = pose.world[bone]
    w, h = att["width"], att["height"]
    am = _local(BonePose(att.get("x", 0.0), att.get("y", 0.0), att.get("rotation", 0.0), att.get("scaleX", 1.0),
                         att.get("scaleY", 1.0), 0, 0))
    local = np.array([[-w / 2, h / 2], [w / 2, h / 2], [w / 2, -h / 2], [-w / 2, -h / 2]])
    pts = (am @ local.T).T + (att.get("x", 0.0), att.get("y", 0.0))
    return (m @ pts.T).T + p


def mesh_world(att: dict[str, Any], pose: Pose, bone: str) -> np.ndarray:
    verts, n = att["vertices"], len(att["uvs"]) // 2
    if len(verts) == 2 * n:
        m, p = pose.world[bone]
        return (m @ np.array(verts, np.float64).reshape(-1, 2).T).T + p
    names = [b["name"] for b in pose.bones]
    out, k = np.zeros((n, 2)), 0
    for i in range(n):
        c = int(verts[k])
        for bi, bx, by, wt in np.array(verts[k + 1:k + 1 + 4 * c], np.float64).reshape(-1, 4):
            m, p = pose.world[names[int(bi)]]
            out[i] += wt * (m @ np.array([bx, by]) + p)
        k += 1 + 4 * c
    return out


def attachment_points(pose: Pose, atts: dict[str, dict[str, dict[str, Any]]]) -> np.ndarray:
    pts = [np.zeros((0, 2))]
    for slot, att, _ in _visible(pose.doc, atts):
        pts.append(mesh_world(att, pose, slot["bone"]) if att.get("type") == "mesh"
                   else _region_corners(att, pose, slot["bone"]))
    return np.concatenate(pts)


def render(pose: Pose, atts: dict[str, dict[str, dict[str, Any]]], images: RegionImages,
           box: tuple[float, float, float, float], scale: float) -> Image.Image:
    """Draw the posed skeleton; box = world (x0, y0, x1, y1) y-up mapped to an image at `scale` px per unit."""
    x0, y0, x1, y1 = box
    size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
    canvas = Image.new("RGBa", size, (0, 0, 0, 0))
    to_px = np.array([[scale, 0.0], [0.0, -scale]])
    origin = np.array([-x0 * scale, y1 * scale])
    for slot, att, path in _visible(pose.doc, atts):
        img = images.get(path)
        if img is None:
            continue
        if att.get("type") == "mesh":
            dst = mesh_world(att, pose, slot["bone"]) @ to_px.T + origin
            fake = {"uvs": att["uvs"], "triangles": att["triangles"], "vertices": []}
            canvas = _over(canvas, _draw_mesh_px(img, fake, dst, size))
            continue
        c = _region_corners(att, pose, slot["bone"]) @ to_px.T + origin
        w, h = img.size
        # affine from image px to canvas px using corners TL, TR, BL
        fwd = np.array([[(c[1, 0] - c[0, 0]) / w, (c[3, 0] - c[0, 0]) / h, c[0, 0]],
                        [(c[1, 1] - c[0, 1]) / w, (c[3, 1] - c[0, 1]) / h, c[0, 1]], [0, 0, 1.0]])
        inv = np.linalg.inv(fwd)
        layer = img.transform(size, Image.Transform.AFFINE, tuple(inv[:2].reshape(-1)),
                              resample=Image.Resampling.BILINEAR)
        canvas = _over(canvas, layer)
    return canvas.convert("RGBA")


def _draw_mesh_px(img: Image.Image, att: dict[str, Any], dst: np.ndarray, size: tuple[int, int]) -> Image.Image:
    # _draw_mesh positions vertices from bones; feed it precomputed canvas points through a one-bone identity pose.
    n = len(dst)
    verts: list[float] = []
    for x, y in dst:
        verts += [1, 0, x, -y, 1.0]
    return _draw_mesh(img, {**att, "vertices": verts}, {"_": (np.eye(2), np.zeros(2))}, ["_"], size, 0.0, 0.0) \
        if n else Image.new("RGBa", size, (0, 0, 0, 0))
