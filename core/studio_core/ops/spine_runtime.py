"""Minimal Spine 4.2 software renderer for skeletons made in the Spine editor (pose comes from spine_pose).

Covers what review renders need: skins combined by name, region + mesh attachments from a packed atlas (offsets,
90° rotation, PMA pages), setup slot/attachment tint and additive blend. Not covered: deform/attachment/draw-order/
color timelines, physics simulation, multiply/screen blend, clipping.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..spine_atlas import Atlas, AtlasRegion, parse_atlas
from .spine import _draw_mesh, _over
from .spine_pose import BONE_CHANNELS, BonePose, Pose, sample  # noqa: F401


def _local(p: BonePose) -> np.ndarray:
    ry = p.rotation + 90 + p.shearY
    rx = p.rotation + p.shearX
    return np.array([[math.cos(math.radians(rx)) * p.scaleX, math.cos(math.radians(ry)) * p.scaleY],
                     [math.sin(math.radians(rx)) * p.scaleX, math.sin(math.radians(ry)) * p.scaleY]])


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
    size = _canvas_size(box, scale)
    canvas = Image.new("RGBa", size, (0, 0, 0, 0))
    for slot, _, layer, _ in layers(pose, atts, images, box, scale):
        canvas = _add(canvas, layer) if slot.get("blend") == "additive" else _over(canvas, layer)
    return canvas.convert("RGBA")


def _canvas_size(box: tuple[float, float, float, float], scale: float) -> tuple[int, int]:
    return max(1, round((box[2] - box[0]) * scale)), max(1, round((box[3] - box[1]) * scale))


def layers(pose: Pose, atts: dict[str, dict[str, dict[str, Any]]], images: RegionImages,
           box: tuple[float, float, float, float], scale: float):
    """(slot, region path, tinted premultiplied canvas-size layer, RGBA tint) in draw order."""
    x0, y0, x1, y1 = box
    size = _canvas_size(box, scale)
    to_px = np.array([[scale, 0.0], [0.0, -scale]])
    origin = np.array([-x0 * scale, y1 * scale])
    for slot, att, path in _visible(pose.doc, atts):
        img = images.get(path)
        if img is None:
            continue
        if att.get("type") == "mesh":
            dst = mesh_world(att, pose, slot["bone"]) @ to_px.T + origin
            fake = {"uvs": att["uvs"], "triangles": att["triangles"], "vertices": []}
            layer = _draw_mesh_px(img, fake, dst, size)
        else:
            c = _region_corners(att, pose, slot["bone"]) @ to_px.T + origin
            w, h = img.size
            # affine from image px to canvas px using corners TL, TR, BL
            fwd = np.array([[(c[1, 0] - c[0, 0]) / w, (c[3, 0] - c[0, 0]) / h, c[0, 0]],
                            [(c[1, 1] - c[0, 1]) / w, (c[3, 1] - c[0, 1]) / h, c[0, 1]], [0, 0, 1.0]])
            inv = np.linalg.inv(fwd)
            layer = img.transform(size, Image.Transform.AFFINE, tuple(inv[:2].reshape(-1)),
                                  resample=Image.Resampling.BILINEAR)
        color = _rgba(slot.get("color")) * _rgba(att.get("color"))
        tint = color.copy()
        tint[:3] *= tint[3]  # layers are premultiplied
        if (tint != 1).any():
            layer = Image.fromarray((np.asarray(layer, np.float32) * tint).astype(np.uint8), "RGBa")
        yield slot, path, layer, color


def _rgba(hex_color: str | None) -> np.ndarray:
    if not hex_color:
        return np.ones(4, np.float32)
    v = [int(hex_color[i:i + 2], 16) / 255 for i in range(0, min(len(hex_color), 8), 2)]
    return np.array((v + [1.0])[:4], np.float32)


def _add(dst: Image.Image, src: Image.Image) -> Image.Image:
    # Premultiplied additive: colour adds, coverage stays the destination's (Spine's additive blend).
    d = np.asarray(dst, np.float32).copy()
    d[..., :3] += np.asarray(src, np.float32)[..., :3]
    return Image.fromarray(d.clip(0, 255).astype(np.uint8), "RGBa")


def _draw_mesh_px(img: Image.Image, att: dict[str, Any], dst: np.ndarray, size: tuple[int, int]) -> Image.Image:
    # _draw_mesh positions vertices from bones; feed it precomputed canvas points through a one-bone identity pose.
    n = len(dst)
    verts: list[float] = []
    for x, y in dst:
        verts += [1, 0, x, -y, 1.0]
    return _draw_mesh(img, {**att, "vertices": verts}, {"_": (np.eye(2), np.zeros(2))}, ["_"], size, 0.0, 0.0) \
        if n else Image.new("RGBa", size, (0, 0, 0, 0))
