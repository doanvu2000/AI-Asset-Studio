"""Spine 4.2 rig -> Godot 4 Skeleton2D scene (Bone2D chain, one Polygon2D per attachment, AnimationPlayer). Also holds
the engine-neutral rig model (`Rig`) that spine_unity2d reuses: setup-pose bone world transforms, skin attachments with
their images and geometry, and animation clips as bone / visibility / draw-order keys."""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from studio_core.export.godot import _num
from studio_core.export.sheet import ident
from studio_core.export.source import Files
from studio_core.ops.spine_pose import BONE_CHANNELS, Pose, sample

LOOP_NAMES = {"idle", "walk", "run", "sprint", "jog", "loop"}
HELD_GAP = 0.001
RESERVED_NODES = ("Skeleton2D", "AnimationPlayer")
_BAD_NODE = re.compile(r'[./:@"%\\]')
_BAD_FILE = re.compile(r"[^\w\-]+")
GROUPS = {"rotation": (("rotate",), 0.0), "position": (("translate", "translatex", "translatey"), 0.0),
          "scale": (("scale", "scalex", "scaley"), 1.0)}

Key = tuple[float, tuple[float, ...], bool]  # time, values, held (stepped) until the next key


def node_name(name: str, taken: set[str], fallback: str) -> str:
    base = _BAD_NODE.sub("_", name).strip() or fallback
    out, n = base, 2
    while out in taken:
        out, n = f"{base}_{n}", n + 1
    taken.add(out)
    return out


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _color(hex_color: str | None) -> tuple[float, ...]:
    h = (hex_color or "ffffffff").ljust(8, "f")
    return tuple(int(h[i:i + 2], 16) / 255 for i in range(0, 8, 2))


def _linear(rotation: float, sx: float, sy: float) -> np.ndarray:
    r = math.radians(rotation)
    return np.array([[math.cos(r) * sx, -math.sin(r) * sy], [math.sin(r) * sx, math.cos(r) * sy]])


@dataclass
class Bone:
    name: str
    parent: int
    x: float
    y: float
    rotation: float
    sx: float
    sy: float
    length: float
    node: str
    path: str
    m: np.ndarray
    p: np.ndarray


@dataclass
class Part:
    slot: int
    key: str
    att: dict[str, Any]
    kind: str
    image: str
    node: str
    bone: int
    visible: bool
    color: tuple[float, ...]


@dataclass
class Geometry:
    world: np.ndarray
    uv: np.ndarray
    triangles: list[int]
    weights: list[list[tuple[int, float]]]


@dataclass
class Clip:
    name: str
    length: float
    loop: bool
    bones: dict[int, dict[str, list[Key]]]
    visible: dict[int, list[tuple[float, bool]]]
    order: list[tuple[float, list[int]]]


def _max_time(obj: Any) -> float:
    if isinstance(obj, dict):
        own = float(obj["time"]) if isinstance(obj.get("time"), (int, float)) else 0.0
        return max([own, *(_max_time(v) for v in obj.values())])
    if isinstance(obj, list):
        return max((_max_time(v) for v in obj), default=0.0)
    return 0.0


def _keys_of(tl: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    # Spine 3.x wrote rotate keys as "angle"
    return [{**k, "value": k["angle"]} if "angle" in k and "value" not in k else k for k in tl.get(kind) or []]


class Rig:
    def __init__(self, doc: dict[str, Any], images: Any, skin: str = "default", bake: str = "auto",
                 fps: float = 30.0):
        self.doc, self.images, self.fps = doc, images, max(1.0, float(fps))
        self.warnings: list[str] = []
        self.anims: dict[str, Any] = doc.get("animations") or {}
        self.bake = bake == "on" or (bake == "auto" and any(doc.get(k) for k in ("ik", "transform", "path")))
        self.bones = self._bones()
        self.skins = self._skins(skin)
        self.parts = self._parts()
        self.uses_order = any(a.get("drawOrder") for a in self.anims.values())
        toggled = {s for a in self.anims.values() for s, tl in (a.get("slots") or {}).items() if tl.get("attachment")}
        slot_names = [s["name"] for s in doc["slots"]]
        self.toggled = {i for i, p in enumerate(self.parts) if slot_names[p.slot] in toggled}
        self._constraint_warnings()

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def _bones(self) -> list[Bone]:
        out: list[Bone] = []
        index: dict[str, int] = {}
        taken: dict[int, set[str]] = {}
        sheared = oblique = 0
        for b in self.doc["bones"]:
            parent = index.get(b.get("parent", ""), -1)
            node = node_name(b["name"], taken.setdefault(parent, set()), "bone")
            path = node if parent < 0 else f"{out[parent].path}/{node}"
            x, y, rot = float(b.get("x", 0)), float(b.get("y", 0)), float(b.get("rotation", 0))
            sx, sy = float(b.get("scaleX", 1)), float(b.get("scaleY", 1))
            lin = _linear(rot, sx, sy)
            pm, pp = (out[parent].m, out[parent].p) if parent >= 0 else (np.eye(2), np.zeros(2))
            out.append(Bone(b["name"], parent, x, y, rot, sx, sy, float(b.get("length", 0)), node, path,
                            pm @ lin, pm @ np.array([x, y]) + pp))
            index[b["name"]] = len(out) - 1
            sheared += bool(b.get("shearX") or b.get("shearY"))
            oblique += str(b.get("inherit", b.get("transform", "normal"))).lower() != "normal"
        if sheared:
            self.warn(f"shear dropped on {sheared} bone(s)")
        if oblique:
            self.warn(f"{oblique} bone(s) use an inherit mode other than normal; exported as normal inheritance")
        return out

    def _skins(self, skin: str) -> list[str]:
        from studio_core.ops import spine_runtime as rt

        names = [s.strip() for s in str(skin or "").split(",") if s.strip() and s.strip() != "default"]
        found, missing = rt.resolve_skins(self.doc, names)
        for m in missing:
            self.warn(f"skin '{m}' not found; using default")
        return found

    def _linked(self, slot: str, att: dict[str, Any]) -> dict[str, Any] | None:
        by_skin = {s["name"]: s for s in self.doc.get("skins") or []}
        src = ((by_skin.get(att.get("skin", "default")) or {}).get("attachments", {}).get(slot) or {}).get(
            att.get("parent"))
        if not src:
            return None
        return {**src, "type": "mesh", **{k: att[k] for k in ("path", "color") if k in att}}

    def _parts(self) -> list[Part]:
        from studio_core.ops import spine_runtime as rt

        atts = rt.skin_attachments(self.doc, self.skins)
        timeline_names: dict[str, set[str]] = {}
        for a in self.anims.values():
            for s, tl in (a.get("slots") or {}).items():
                timeline_names.setdefault(s, set()).update(k["name"] for k in tl.get("attachment") or []
                                                           if k.get("name"))
        taken = {b.node for b in self.bones} | set(RESERVED_NODES)
        out: list[Part] = []
        skipped: set[str] = set()
        for si, slot in enumerate(self.doc["slots"]):
            first = slot.get("attachment")
            for key in dict.fromkeys([first, *sorted(timeline_names.get(slot["name"], ()))]):
                att = (atts.get(slot["name"]) or {}).get(key) if key else None
                if att is None:
                    continue
                kind = att.get("type", "region")
                if kind == "linkedmesh":
                    att, kind = self._linked(slot["name"], att), "mesh"
                if att is None or kind not in ("region", "mesh"):
                    skipped.add(kind)
                    continue
                path = att.get("path") or att.get("name") or key
                if self.images.get(path) is None:
                    self.warn(f"attachment '{key}': region '{path}' not in the atlas, skipped")
                    continue
                if slot.get("blend", "normal") != "normal":
                    self.warn(f"slot '{slot['name']}': blend {slot['blend']} exported as normal")
                bone = next((i for i, b in enumerate(self.bones) if b.name == slot["bone"]), 0)
                out.append(Part(si, key, att, kind, path, node_name(f"{slot['name']}_{key}", taken, "part"), bone,
                                key == first, tuple(a * b for a, b in zip(_color(slot.get("color")),
                                                                         _color(att.get("color"))))))
        for k in sorted(skipped):
            self.warn(f"attachment type '{k}' is not exported")
        return out

    def _constraint_warnings(self) -> None:
        d = self.doc
        if d.get("physics"):
            self.warn("physics constraints removed (no simulation; use the engine's own spring bones)")
        solved = [k for k in ("ik", "transform", "path") if d.get(k)]
        if solved and not self.bake:
            self.warn(f"{'/'.join(solved)} constraints are not solved; turn bake on to sample them at {self.fps:g} fps")
        elif solved:
            self.warn(f"{'/'.join(solved)} constraints are baked into bone keys at {self.fps:g} fps")
        for name, a in self.anims.items():
            if a.get("deform"):
                self.warn(f"animation '{name}': deform timelines are not exported")
            if a.get("events"):
                self.warn(f"animation '{name}': events are not exported")
            if any(set(tl) - {"attachment"} for tl in (a.get("slots") or {}).values()):
                self.warn(f"animation '{name}': slot color timelines are not exported")

    def region_image(self, part: Part) -> Image.Image:
        return self.images.get(part.image).convert("RGBA")

    def geometry(self, part: Part) -> Geometry:
        att, bone = part.att, self.bones[part.bone]
        img = self.images.get(part.image)
        w_px, h_px = img.size
        if part.kind == "region":
            w, h = float(att.get("width", w_px)), float(att.get("height", h_px))
            corners = np.array([[-w / 2, h / 2], [w / 2, h / 2], [w / 2, -h / 2], [-w / 2, -h / 2]])
            local = corners @ _linear(att.get("rotation", 0.0), att.get("scaleX", 1.0), att.get("scaleY", 1.0)).T
            local += (att.get("x", 0.0), att.get("y", 0.0))
            uv = np.array([[0, 0], [w_px, 0], [w_px, h_px], [0, h_px]], float)
            return Geometry(local @ bone.m.T + bone.p, uv, [0, 1, 2, 0, 2, 3], [[(part.bone, 1.0)]] * 4)
        uvs = np.array(att["uvs"], float).reshape(-1, 2)
        n, verts = len(uvs), att["vertices"]
        tris = [int(i) for i in att.get("triangles") or []]
        if len(verts) == 2 * n:
            pts = np.array(verts, float).reshape(-1, 2)
            return Geometry(pts @ bone.m.T + bone.p, uvs * (w_px, h_px), tris, [[(part.bone, 1.0)]] * n)
        world, weights, k = np.zeros((n, 2)), [], 0
        for i in range(n):
            count, row = int(verts[k]), []
            for bi, bx, by, wt in np.array(verts[k + 1:k + 1 + 4 * count], float).reshape(-1, 4):
                b = self.bones[int(bi)]
                world[i] += wt * (b.m @ np.array([bx, by]) + b.p)
                row.append((int(bi), float(wt)))
            weights.append(row)
            k += 1 + 4 * count
        return Geometry(world, uvs * (w_px, h_px), tris, weights)

    def clips(self) -> list[Clip]:
        return [self._clip(name, a) for name, a in self.anims.items()]

    def _clip(self, name: str, anim: dict[str, Any]) -> Clip:
        length = _max_time(anim)
        bones = self._baked(name, length) if self.bake else {}
        for i, b in enumerate(self.bones):
            tl = (anim.get("bones") or {}).get(b.name) or {}
            if tl.get("shear") or tl.get("shearx") or tl.get("sheary"):
                self.warn(f"animation '{name}': shear timelines are not exported")
            if not self.bake and tl:
                bones[i] = self._bone_keys(b, tl)
        slot_names = [s["name"] for s in self.doc["slots"]]
        visible = {}
        for pi in self.toggled:
            part = self.parts[pi]
            keys = ((anim.get("slots") or {}).get(slot_names[part.slot]) or {}).get("attachment") or []
            vis = [(float(k.get("time", 0.0)), k.get("name") == part.key) for k in keys]
            if not vis or vis[0][0] > 0:
                vis.insert(0, (0.0, part.visible))
            visible[pi] = vis
        order = self._order(anim.get("drawOrder") or []) if self.uses_order else []
        return Clip(name, length, bool(set(re.split(r"[^a-z0-9]+", name.lower())) & LOOP_NAMES), bones, visible, order)

    def _order(self, keys: list[dict[str, Any]]) -> list[tuple[float, list[int]]]:
        n = len(self.doc["slots"])
        index = {s["name"]: i for i, s in enumerate(self.doc["slots"])}
        out = [(0.0, list(range(n)))] if not keys or float(keys[0].get("time", 0.0)) > 0 else []
        for k in keys:
            draw, unchanged, orig = [-1] * n, [], 0
            for off in k.get("offsets") or []:
                si = index[off["slot"]]
                while orig != si:
                    unchanged.append(orig)
                    orig += 1
                draw[orig + int(off["offset"])] = orig
                orig += 1
            unchanged += range(orig, n)
            for i in range(n - 1, -1, -1):
                if draw[i] == -1:
                    draw[i] = unchanged.pop()
            out.append((float(k.get("time", 0.0)), [draw.index(s) for s in range(n)]))
        return out

    def _bone_keys(self, b: Bone, tl: dict[str, Any]) -> dict[str, list[Key]]:
        out: dict[str, list[Key]] = {}
        for group, (kinds, _) in GROUPS.items():
            if not any(tl.get(k) for k in kinds):
                continue
            times, held = self._times(tl, kinds)
            keys = []
            for t in times:
                if group == "rotation":
                    vals: tuple[float, ...] = (b.rotation + self._at(tl, "rotate", t)[0],)
                elif group == "position":
                    dx, dy = self._at(tl, "translate", t)
                    vals = (b.x + dx + self._at(tl, "translatex", t)[0], b.y + dy + self._at(tl, "translatey", t)[0])
                else:
                    kx, ky = self._at(tl, "scale", t)
                    vals = (b.sx * kx * self._at(tl, "scalex", t)[0], b.sy * ky * self._at(tl, "scaley", t)[0])
                keys.append((t, vals, t in held))
            out[group] = keys
        return out

    def _at(self, tl: dict[str, Any], kind: str, t: float) -> tuple[float, ...]:
        target, fields = BONE_CHANNELS[kind]
        return sample(_keys_of(tl, kind), t, fields, 1.0 if target.startswith("scale") else 0.0, True)

    def _times(self, tl: dict[str, Any], kinds: tuple[str, ...]) -> tuple[list[float], set[float]]:
        times, held = {0.0}, set()
        for kind in kinds:
            keys = tl.get(kind) or []
            for i, k in enumerate(keys):
                t = round(float(k.get("time", 0.0)), 6)
                times.add(t)
                curve = k.get("curve")
                if curve == "stepped":
                    held.add(t)
                elif isinstance(curve, list) and i + 1 < len(keys):
                    t1 = float(keys[i + 1].get("time", 0.0))
                    n = max(2, math.ceil((t1 - t) * self.fps))
                    times.update(round(t + (t1 - t) * j / n, 6) for j in range(1, n))
        return sorted(times), held

    def _baked(self, name: str, length: float) -> dict[int, dict[str, list[Key]]]:
        n = max(1, math.ceil(length * self.fps - 1e-9))
        times = [min(i / self.fps, length) for i in range(n + 1)]
        rows: list[list[tuple[float, ...]]] = []
        for t in times:
            pose = Pose(self.doc, name, t)
            rows.append([(nb.arotation, nb.ax, nb.ay, nb.ascaleX, nb.ascaleY)
                         for nb in (pose._b[b.name] for b in self.bones)])
        out: dict[int, dict[str, list[Key]]] = {}
        for i, b in enumerate(self.bones):
            rot: list[float] = []
            for row in rows:
                r = row[i][0]
                rot.append(r if not rot else r + 360 * round((rot[-1] - r) / 360))
            cols = {"rotation": [(r,) for r in rot], "position": [row[i][1:3] for row in rows],
                    "scale": [row[i][3:5] for row in rows]}
            setup = {"rotation": (b.rotation,), "position": (b.x, b.y), "scale": (b.sx, b.sy)}
            keep = {g: [(t, tuple(v), False) for t, v in zip(times, vals)] for g, vals in cols.items()
                    if any(max(abs(a - s) for a, s in zip(v, setup[g])) > 1e-4 for v in vals)}
            if keep:
                out[i] = keep
        return out


def _f(v: float) -> str:
    return "0" if abs(v) < 1e-9 else f"{v:.6g}"


def _vec(*v: float) -> str:
    return f"Vector2({_f(v[0])}, {_f(v[1])})"


def expand_held(keys: list[Key]) -> list[tuple[float, tuple[float, ...]]]:
    out: list[tuple[float, tuple[float, ...]]] = []
    for i, (t, v, held) in enumerate(keys):
        out.append((t, v))
        if held and i + 1 < len(keys):
            nt = keys[i + 1][0]
            out.append((nt - min(HELD_GAP, (nt - t) / 2), v))
    return out


def _track(i: int, path: str, times: list[float], values: list[str], update: int = 0) -> str:
    return (f'tracks/{i}/type = "value"\ntracks/{i}/imported = false\ntracks/{i}/enabled = true\n'
            f'tracks/{i}/path = NodePath("{path}")\ntracks/{i}/interp = 1\ntracks/{i}/loop_wrap = true\n'
            f'tracks/{i}/keys = {{\n"times": PackedFloat32Array({", ".join(_f(t) for t in times)}),\n'
            f'"transitions": PackedFloat32Array({", ".join("1" for _ in times)}),\n"update": {update},\n'
            f'"values": [{", ".join(values)}]\n}}')


def _clip_tracks(rig: Rig, clip: Clip) -> list[str]:
    tracks: list[str] = []
    for bi, groups in clip.bones.items():
        base = f"Skeleton2D/{rig.bones[bi].path}"
        for group, keys in groups.items():
            pts = expand_held(keys)
            ts = [t for t, _ in pts]
            if group == "rotation":
                tracks.append(_track(len(tracks), f"{base}:rotation", ts, [_f(-math.radians(v[0])) for _, v in pts]))
            elif group == "position":
                tracks.append(_track(len(tracks), f"{base}:position", ts, [_vec(v[0], -v[1]) for _, v in pts]))
            else:
                tracks.append(_track(len(tracks), f"{base}:scale", ts, [_vec(*v) for _, v in pts]))
    for pi, vis in clip.visible.items():
        tracks.append(_track(len(tracks), f"{rig.parts[pi].node}:visible", [t for t, _ in vis],
                             ["true" if v else "false" for _, v in vis], update=1))
    for pi, part in enumerate(rig.parts):
        if clip.order:
            tracks.append(_track(len(tracks), f"{part.node}:z_index", [t for t, _ in clip.order],
                                 [str(pos[part.slot]) for _, pos in clip.order], update=1))
    return tracks


def image_files(rig: Rig) -> dict[str, str]:
    """atlas region path -> file name under images/ (shared by parts that use the same region)."""
    names: dict[str, str] = {}
    for p in rig.parts:
        if p.image not in names:
            stem = _BAD_FILE.sub("_", p.image).strip("_") or "image"
            name, n = f"{stem}.png", 2
            while name in names.values():
                name, n = f"{stem}_{n}.png", n + 1
            names[p.image] = name
    return names


def godot_scene(rig: Rig, asset: str, files: dict[str, str]) -> str:
    root = ident(asset, "spine")
    ext, subs, nodes = [], [], [f'[node name="{root}" type="Node2D"]\n', '[node name="Skeleton2D" type="Skeleton2D" parent="."]\n']
    tex_id = {}
    for rel in dict.fromkeys(files[p.image] for p in rig.parts):
        tex_id[rel] = f"{len(tex_id) + 1}_{_BAD_FILE.sub('_', rel[:-4])}"
        ext.append(f'[ext_resource type="Texture2D" path="images/{rel}" id="{tex_id[rel]}"]')
    for b in rig.bones:
        parent = "Skeleton2D" if b.parent < 0 else f"Skeleton2D/{rig.bones[b.parent].path}"
        lin = _linear(b.rotation, b.sx, b.sy)
        rest = (f"Transform2D({_f(lin[0, 0])}, {_f(-lin[1, 0])}, {_f(-lin[0, 1])}, {_f(lin[1, 1])}, "
                f"{_f(b.x)}, {_f(-b.y)})")
        lines = [f'[node name="{b.node}" type="Bone2D" parent="{parent}"]', f"position = {_vec(b.x, -b.y)}"]
        if abs(b.rotation) > 1e-9:
            lines.append(f"rotation = {_f(-math.radians(b.rotation))}")
        if (b.sx, b.sy) != (1, 1):
            lines.append(f"scale = {_vec(b.sx, b.sy)}")
        lines += [f"rest = {rest}", "auto_calculate_length_and_angle = false", f"length = {_f(b.length)}",
                  "bone_angle = 0.0"]
        nodes.append("\n".join(lines) + "\n")
    for part in rig.parts:
        g = rig.geometry(part)
        lines = [f'[node name="{part.node}" type="Polygon2D" parent="."]']
        if rig.uses_order:
            lines.append(f"z_index = {part.slot}")
        if not part.visible:
            lines.append("visible = false")
        if any(abs(c - 1) > 1e-6 for c in part.color):
            lines.append(f"modulate = Color({', '.join(_f(c) for c in part.color)})")
        lines += [f'texture = ExtResource("{tex_id[files[part.image]]}")', 'skeleton = NodePath("../Skeleton2D")',
                  f"polygon = PackedVector2Array({', '.join(_f(v) for x, y in g.world for v in (x, -y))})",
                  f"uv = PackedVector2Array({', '.join(_f(v) for v in g.uv.reshape(-1))})"]
        if part.kind == "mesh" and g.triangles:
            tris = [g.triangles[i:i + 3] for i in range(0, len(g.triangles) - 2, 3)]
            lines.append("polygons = [" + ", ".join(f"PackedInt32Array({', '.join(map(str, t))})" for t in tris) + "]")
        used = sorted({b for row in g.weights for b, _ in row})
        entries = []
        for b in used:
            w = [next((wt for bi, wt in row if bi == b), 0.0) for row in g.weights]
            entries.append(f'NodePath("{rig.bones[b].path}"), PackedFloat32Array({", ".join(_f(x) for x in w)})')
        lines.append(f"bones = [{', '.join(entries)}]")
        nodes.append("\n".join(lines) + "\n")
    names = []
    for k, clip in enumerate(rig.clips()):
        body = "\n".join(_clip_tracks(rig, clip))
        subs.append(f'[sub_resource type="Animation" id="Animation_{k}"]\nresource_name = "{clip.name}"\n'
                    f'length = {_num(max(clip.length, 0.01))}\nloop_mode = {1 if clip.loop else 0}\n'
                    f'step = {_f(1 / rig.fps)}\n{body}\n')
        names.append(f'&"{clip.name}": SubResource("Animation_{k}")')
    if names:
        subs.append('[sub_resource type="AnimationLibrary" id="AnimationLibrary_main"]\n_data = {\n'
                    + ",\n".join(names) + "\n}\n")
        nodes.append('[node name="AnimationPlayer" type="AnimationPlayer" parent="."]\nlibraries = {\n'
                     '&"": SubResource("AnimationLibrary_main")\n}\n')
    steps = len(ext) + len(subs) + 1
    return (f"[gd_scene load_steps={steps} format=3]\n\n" + "\n".join(ext) + ("\n\n" if ext else "")
            + "\n".join(subs) + "\n" + "\n".join(nodes))


def godot_files(rig: Rig, asset: str) -> tuple[Files, list[str]]:
    names = image_files(rig)
    files: Files = []
    for path, rel in names.items():
        part = next(p for p in rig.parts if p.image == path)
        files.append((f"images/{rel}", png_bytes(rig.region_image(part))))
    files.append((f"{asset}.tscn", godot_scene(rig, asset, names).encode("utf-8")))
    note = (f"- {asset}.tscn: Skeleton2D với {len(rig.bones)} Bone2D (rest = setup pose Spine, y lật cho Godot), "
            f"{len(rig.parts)} Polygon2D skin theo xương (images/), AnimationPlayer {len(rig.anims)} animation "
            "(rotation / position / scale + visible + z_index).")
    return files, [note]
