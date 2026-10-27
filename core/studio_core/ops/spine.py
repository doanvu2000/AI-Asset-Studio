"""Spine 4.2 export from positioned layers: skeleton JSON, .atlas, packed page, procedural animations, and a
pure-Python compositor that replays the same transforms (setup-pose check + animation frames)."""
from __future__ import annotations

import base64
import html
import json
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

SPINE_VERSION = "4.2.43"
# Exact pin (CDN rule); spine-player is only licensed for users holding a Spine Editor license.
PLAYER_VERSION = "4.2.43"
PLAYER_JS = f"https://unpkg.com/@esotericsoftware/spine-player@{PLAYER_VERSION}/dist/iife/spine-player.js"
PLAYER_CSS = f"https://unpkg.com/@esotericsoftware/spine-player@{PLAYER_VERSION}/dist/spine-player.css"

Point = tuple[float, float]

# name, parent, start joint, end joint, length factor (1 = reach the end joint)
BONES: tuple[tuple[str, str | None, str, str, float], ...] = (
    ("hip", "root", "pelvis", "neck", 0.15),
    ("torso", "hip", "pelvis", "neck", 1.0),
    ("neck", "torso", "neck", "head_top", 0.15),
    ("head", "neck", "neck", "head_top", 1.0),
    *((f"shoulder_{s}", "torso", "neck", f"shoulder_{s}", 1.0) for s in "lr"),
    *((f"upper_arm_{s}", f"shoulder_{s}", f"shoulder_{s}", f"elbow_{s}", 1.0) for s in "lr"),
    *((f"lower_arm_{s}", f"upper_arm_{s}", f"elbow_{s}", f"wrist_{s}", 1.0) for s in "lr"),
    *((f"hand_{s}", f"lower_arm_{s}", f"wrist_{s}", f"hand_tip_{s}", 1.0) for s in "lr"),
    *((f"thigh_{s}", "hip", f"hip_{s}", f"knee_{s}", 1.0) for s in "lr"),
    *((f"shin_{s}", f"thigh_{s}", f"knee_{s}", f"ankle_{s}", 1.0) for s in "lr"),
    *((f"foot_{s}", f"shin_{s}", f"ankle_{s}", f"foot_tip_{s}", 1.0) for s in "lr"),
)
# Pauldron layers share the clavicle bone names, but no clip rotates clavicles; they must ride the upper arm.
PART_BONE = {"cape": "torso", "shoulder_l": "upper_arm_l", "shoulder_r": "upper_arm_r"}


def _rot(deg: float) -> np.ndarray:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s], [s, c]])


def _norm_deg(d: float) -> float:
    d = (d + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


@dataclass
class Bone:
    name: str
    parent: str | None
    x: float
    y: float
    rotation: float
    length: float
    world_pos: tuple[float, float] = (0.0, 0.0)
    world_rot: float = 0.0


def to_spine(p: Point, origin: Point) -> Point:
    return p[0] - origin[0], origin[1] - p[1]


def build_bones(joints: dict[str, Point], origin: Point) -> list[Bone]:
    """Bones point from their joint to the next one (y-up); locals derive from the parent's world transform."""
    bones = [Bone("root", None, 0.0, 0.0, 0.0, 0.0)]
    by = {"root": bones[0]}
    for name, parent, a, b, k in BONES:
        pa, pb = to_spine(joints[a], origin), to_spine(joints[b], origin)
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        wrot = math.degrees(math.atan2(dy, dx)) if (dx or dy) else 90.0
        par = by[parent or "root"]
        lx, ly = _rot(-par.world_rot) @ np.array([pa[0] - par.world_pos[0], pa[1] - par.world_pos[1]])
        bone = Bone(name, parent, float(lx), float(ly), _norm_deg(wrot - par.world_rot), math.hypot(dx, dy) * k,
                    (pa[0], pa[1]), wrot)
        bones.append(bone)
        by[name] = bone
    return bones


def bone_for(part: str, center: Point, bones: list[Bone]) -> str:
    if part in PART_BONE:
        return PART_BONE[part]
    if part in {b.name for b in bones}:
        return part
    best, dist = "torso", math.inf
    for b in bones[1:]:
        ax, ay = b.world_pos
        ex = ax + math.cos(math.radians(b.world_rot)) * b.length
        ey = ay + math.sin(math.radians(b.world_rot)) * b.length
        dx, dy = ex - ax, ey - ay
        t = max(0.0, min(1.0, ((center[0] - ax) * dx + (center[1] - ay) * dy) / ((dx * dx + dy * dy) or 1.0)))
        d = math.hypot(center[0] - ax - t * dx, center[1] - ay - t * dy)
        if d < dist:
            best, dist = b.name, d
    return best


def pack(sizes: list[tuple[int, int]], padding: int = 2, max_side: int = 4096) -> tuple[list[tuple[int, int]], int, int]:
    """Shelf packing into the smallest power-of-two page that fits; returns positions, page w, page h."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    widest = max(w for w, _ in sizes) + 2 * padding
    side = 64
    while side < widest:
        side *= 2
    while side <= max_side:
        pos: list[tuple[int, int]] = [(0, 0)] * len(sizes)
        x = y = padding
        shelf = 0
        ok = True
        for i in order:
            w, h = sizes[i]
            if x + w + padding > side:
                x, y, shelf = padding, y + shelf + padding, 0
            if y + h + padding > max_side:
                ok = False
                break
            pos[i] = (x, y)
            x += w + padding
            shelf = max(shelf, h)
        height = y + shelf + padding
        if ok and height <= side:
            ph = 64
            while ph < height:
                ph *= 2
            return pos, side, ph
        side *= 2
    raise ValueError("layers do not fit a 4096 atlas page")


def atlas_text(page: str, w: int, h: int, regions: list[tuple[str, int, int, int, int]]) -> str:
    lines = [page, f"size:{w},{h}", "filter:Linear,Linear", "pma:false"]
    for name, x, y, rw, rh in regions:
        lines += [name, f"bounds:{x},{y},{rw},{rh}"]
    return "\n".join(lines) + "\n"


WIDE_SLEEVE = 3.0
MIN_ARM_DROP = 0.4


def arm_drop(layers: list[Layer], joints: dict[str, Point]) -> float:
    """Share of the usual arm lowering to apply. A sleeve much wider than the hand hangs below the T-pose arm;
    rotating it far swings that cloth up into wings, so wide sleeves only lower part way."""
    by = {l.name: l for l in layers}
    ratios = []
    for s in "lr":
        fore, hand = by.get(f"lower_arm_{s}"), by.get(f"hand_{s}")
        need = (f"elbow_{s}", f"wrist_{s}", f"hand_tip_{s}")
        if fore is None or hand is None or any(j not in joints for j in need):
            continue
        e, w, t = (joints[j] for j in need)
        fore_w = (np.asarray(fore.image)[..., 3] > 0).sum() / max(math.dist(e, w), 1.0)
        hand_w = (np.asarray(hand.image)[..., 3] > 0).sum() / max(math.dist(w, t), 1.0)
        if hand_w > 0:
            ratios.append(fore_w / hand_w)
    r = max(ratios, default=1.0)
    return 1.0 if r <= WIDE_SLEEVE else max(MIN_ARM_DROP, WIDE_SLEEVE / r)


def animations(bones: list[Bone], height: float, drop: float = 1.0) -> dict[str, Any]:
    """Deterministic clips for a front-view rig: loops `idle`, `relax`, `walk`; one-shots `attack`, `hurt`, `jump`.
    Limb directions come from the setup pose (which image side each limb sits on), never from an assumed sign."""
    by = {b.name: b for b in bones}
    h = height or 300.0
    pelvis_x = by["torso"].world_pos[0] if "torso" in by else 0.0

    def side_of(s: str) -> float:
        b = by.get(f"upper_arm_{s}")
        if b is None:
            return 1.0 if s == "l" else -1.0
        return 1.0 if b.world_pos[0] >= pelvis_x else -1.0

    side = {s: side_of(s) for s in "lr"}
    clips: dict[str, dict[str, Any]] = {}

    def add(clip: str, bone: str, kind: str, fn: Any, duration: float, steps: int) -> None:
        if bone not in by:
            return
        keys = []
        for i in range(steps + 1):
            u = i / steps
            t = round(duration * u, 4)
            v = fn(u)
            if kind == "rotate":
                keys.append({"time": t, "value": round(float(v), 3)})
            else:
                keys.append({"time": t, "x": round(float(v[0]), 4), "y": round(float(v[1]), 4)})
        clips.setdefault(clip, {}).setdefault(bone, {})[kind] = keys

    def ease(*pts: tuple[float, float]) -> Any:
        def f(u: float) -> float:
            for (u0, v0), (u1, v1) in zip(pts, pts[1:]):
                if u <= u1:
                    k = (u - u0) / ((u1 - u0) or 1.0)
                    return v0 + (v1 - v0) * (1 - math.cos(math.pi * k)) / 2
            return pts[-1][1]
        return f

    def wave(u: float, phase: float = 0.0) -> float:
        return math.sin(2 * math.pi * u + phase)

    def in_hip(dx: float, dy: float) -> tuple[float, float]:
        """Leg bones translate in hip space; hip points up the spine, so a world offset must be rotated into it."""
        par = by["hip"].world_rot if "hip" in by else 0.0
        x, y = _rot(-par) @ np.array([dx, dy])
        return float(x), float(y)

    def down(s: str, deg: float) -> float:
        """Arm rotation that lowers a T-pose arm by `deg` (clockwise on the +x side)."""
        return -side[s] * deg * drop

    rest = 62.0

    d, n = 2.0, 8
    add("idle", "torso", "rotate", lambda u: 1.2 * wave(u), d, n)
    add("idle", "torso", "scale", lambda u: (1.0, 1.0 + 0.015 * wave(u)), d, n)
    add("idle", "hip", "translate", lambda u: (0.0, -1.5 * (1 - math.cos(2 * math.pi * u)) / 2), d, n)
    add("idle", "head", "rotate", lambda u: -2.0 * wave(u, 0.6), d, n)
    for s in "lr":
        add("idle", f"upper_arm_{s}", "rotate", lambda u, s=s: down(s, -3.0 * wave(u)), d, n)
        add("idle", f"lower_arm_{s}", "rotate", lambda u, s=s: down(s, -4.0 * wave(u, -0.8)), d, n)

    d, n = 2.4, 12
    for s in "lr":
        add("relax", f"upper_arm_{s}", "rotate", lambda u, s=s: down(s, 60.0 * (1 - math.cos(2 * math.pi * u)) / 2), d, n)
        add("relax", f"lower_arm_{s}", "rotate", lambda u, s=s: down(s, 12.0 * (1 - math.cos(2 * math.pi * u)) / 2), d, n)
        add("relax", f"hand_{s}", "rotate", lambda u, s=s: down(s, 8.0 * (1 - math.cos(2 * math.pi * u)) / 2), d, n)
    add("relax", "head", "rotate", lambda u: 4.0 * wave(u), d, n)

    # Front view: stepping is depth, so a walk reads as a bob, alternating leg lifts and a small arm swing.
    d, n = 1.0, 16
    add("walk", "hip", "translate",
        lambda u: (0.008 * h * wave(u), -0.02 * h * (1 - math.cos(4 * math.pi * u)) / 2), d, n)
    add("walk", "torso", "rotate", lambda u: 2.0 * wave(u), d, n)
    add("walk", "head", "rotate", lambda u: -1.5 * wave(u), d, n)
    for s, ph in (("l", 0.0), ("r", math.pi)):
        add("walk", f"thigh_{s}", "translate", lambda u, ph=ph: in_hip(0.0, 0.045 * h * max(0.0, wave(u, ph))), d, n)
        add("walk", f"thigh_{s}", "rotate", lambda u, s=s, ph=ph: side[s] * 4.0 * max(0.0, wave(u, ph)), d, n)
        add("walk", f"upper_arm_{s}", "rotate", lambda u, s=s, ph=ph: down(s, rest + 6.0 * wave(u, ph + math.pi)), d, n)
        add("walk", f"lower_arm_{s}", "rotate", lambda u, s=s: down(s, 8.0), d, n)

    d, n = 0.9, 18
    g = side["r"]
    add("attack", "upper_arm_r", "rotate", ease((0, down("r", rest)), (0.3, down("r", -50)), (0.5, down("r", 85)),
                                                (1, down("r", rest))), d, n)
    add("attack", "lower_arm_r", "rotate", ease((0, down("r", 8)), (0.3, down("r", -25)), (0.5, down("r", 5)),
                                                (1, down("r", 8))), d, n)
    add("attack", "upper_arm_l", "rotate", ease((0, down("l", rest)), (0.4, down("l", rest - 8)), (1, down("l", rest))),
        d, n)
    add("attack", "torso", "rotate", ease((0, 0), (0.3, -g * 4), (0.5, g * 6), (1, 0)), d, n)
    hip_x = ease((0, 0), (0.3, -g * 0.01 * h), (0.5, g * 0.025 * h), (1, 0))
    add("attack", "hip", "translate", lambda u: (hip_x(u), 0.0), d, n)

    d, n = 0.6, 12
    add("hurt", "torso", "rotate", ease((0, 0), (0.15, 8), (1, 0)), d, n)
    add("hurt", "head", "rotate", ease((0, 0), (0.15, 12), (1, 0)), d, n)
    knock = ease((0, 0), (0.15, -0.02 * h), (1, 0))
    add("hurt", "hip", "translate", lambda u: (knock(u), 0.0), d, n)
    for s in "lr":
        add("hurt", f"upper_arm_{s}", "rotate", ease((0, down(s, rest)), (0.15, down(s, 35)), (1, down(s, rest))), d, n)

    d, n = 1.0, 20
    rise = ease((0, 0), (0.18, -0.05 * h), (0.5, 0.2 * h), (0.82, 0), (0.9, -0.03 * h), (1, 0))
    add("jump", "hip", "translate", lambda u: (0.0, rise(u)), d, n)
    for s in "lr":
        add("jump", f"upper_arm_{s}", "rotate", ease((0, down(s, rest)), (0.18, down(s, 75)), (0.5, down(s, -20)),
                                                     (0.82, down(s, rest)), (1, down(s, rest))), d, n)
        add("jump", f"thigh_{s}", "rotate", ease((0, 0), (0.5, side[s] * 8), (0.82, 0), (1, 0)), d, n)

    return {name: {"bones": tl} for name, tl in clips.items()}


@dataclass
class Layer:
    name: str
    image: Image.Image  # RGBA crop
    offset: tuple[int, int]  # top-left in source pixels


def build_skeleton(layers: list[Layer], joints: dict[str, Point], origin: Point, name: str,
                   with_animations: bool = True) -> tuple[dict[str, Any], list[Bone]]:
    bones = build_bones(joints, origin)
    world = {b.name: b for b in bones}
    slots, attachments = [], {}
    for layer in layers:
        w, h = layer.image.size
        c = to_spine((layer.offset[0] + w / 2, layer.offset[1] + h / 2), origin)
        bname = bone_for(layer.name, c, bones)
        b = world[bname]
        lx, ly = _rot(-b.world_rot) @ np.array([c[0] - b.world_pos[0], c[1] - b.world_pos[1]])
        slots.append({"name": layer.name, "bone": bname, "attachment": layer.name})
        attachments[layer.name] = {layer.name: {"x": round(float(lx), 3), "y": round(float(ly), 3),
                                                "rotation": round(_norm_deg(-b.world_rot), 4),
                                                "width": w, "height": h}}
    xs = [to_spine((l.offset[0] + dx, 0), origin)[0] for l in layers for dx in (0, l.image.width)]
    ys = [to_spine((0, l.offset[1] + dy), origin)[1] for l in layers for dy in (0, l.image.height)]
    bone_json = []
    for b in bones:
        bj: dict[str, Any] = {"name": b.name}
        if b.parent:
            bj["parent"] = b.parent
        if b.length:
            bj["length"] = round(b.length, 3)
        if b.x or b.y:
            bj["x"], bj["y"] = round(b.x, 3), round(b.y, 3)
        if b.rotation:
            bj["rotation"] = round(b.rotation, 4)
        bone_json.append(bj)
    doc: dict[str, Any] = {
        "skeleton": {"hash": "", "spine": SPINE_VERSION, "x": round(min(xs), 2), "y": round(min(ys), 2),
                     "width": round(max(xs) - min(xs), 2), "height": round(max(ys) - min(ys), 2),
                     "fps": 30, "images": "./", "audio": "", "name": name},
        "bones": bone_json,
        "slots": slots,
        "skins": [{"name": "default", "attachments": attachments}],
    }
    if with_animations:
        anims = animations(bones, max(ys) - min(ys), arm_drop(layers, joints))
        if "hurt" in anims:
            flash = [{"time": 0, "color": "ffffffff"}, {"time": 0.08, "color": "ff7070ff"}, {"time": 0.3, "color": "ffffffff"}]
            anims["hurt"]["slots"] = {s["name"]: {"rgba": flash} for s in slots}
        doc["animations"] = anims
    import hashlib
    doc["skeleton"]["hash"] = base64.b64encode(
        hashlib.sha1(json.dumps(doc, sort_keys=True).encode()).digest()).decode()[:11]
    return doc, bones


def validate_skeleton(doc: dict[str, Any], atlas: str) -> list[str]:
    """Structural checks mirroring what spine-ts SkeletonJson/TextureAtlas require."""
    bad: list[str] = []
    bones = doc.get("bones") or []
    names: set[str] = set()
    for i, b in enumerate(bones):
        if not isinstance(b.get("name"), str):
            bad.append(f"bone #{i} has no name")
            continue
        if i == 0 and "parent" in b:
            bad.append("first bone must be the root")
        if i and b.get("parent") not in names:
            bad.append(f"bone {b['name']} parent {b.get('parent')!r} not defined before it")
        names.add(b["name"])
    slot_names = set()
    for s in doc.get("slots") or []:
        if s.get("bone") not in names:
            bad.append(f"slot {s.get('name')} uses unknown bone {s.get('bone')!r}")
        slot_names.add(s.get("name"))
    regions = {ln for ln in atlas.splitlines()[4:] if ln and ":" not in ln}
    skins = doc.get("skins") or []
    if not skins or skins[0].get("name") != "default":
        bad.append("missing default skin")
    for skin in skins:
        for slot, atts in (skin.get("attachments") or {}).items():
            if slot not in slot_names:
                bad.append(f"skin {skin.get('name')} references unknown slot {slot}")
            for att_name, att in atts.items():
                if att.get("type", "region") == "region" and att.get("path", att_name) not in regions:
                    bad.append(f"attachment {att_name} has no atlas region")
                if not (att.get("width", 0) > 0 and att.get("height", 0) > 0):
                    bad.append(f"attachment {att_name} has no size")
    for aname, anim in (doc.get("animations") or {}).items():
        for bname, tls in (anim.get("bones") or {}).items():
            if bname not in names:
                bad.append(f"animation {aname} targets unknown bone {bname}")
            for tname, keys in tls.items():
                times = [k.get("time", 0) for k in keys]
                if times != sorted(times):
                    bad.append(f"animation {aname}/{bname}/{tname} keys out of order")
    return bad


def _sample(keys: list[dict[str, Any]], t: float, fields: tuple[str, ...], default: float) -> tuple[float, ...]:
    if not keys:
        return tuple(default for _ in fields)
    if t <= keys[0].get("time", 0):
        return tuple(float(keys[0].get(f, default)) for f in fields)
    for a, b in zip(keys, keys[1:]):
        ta, tb = a.get("time", 0), b.get("time", 0)
        if ta <= t <= tb:
            u = (t - ta) / ((tb - ta) or 1)
            return tuple(float(a.get(f, default)) * (1 - u) + float(b.get(f, default)) * u for f in fields)
    return tuple(float(keys[-1].get(f, default)) for f in fields)


def world_transforms(doc: dict[str, Any], anim: str | None = None, t: float = 0.0
                     ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """bone -> (2x2 matrix, position) with Spine's default (normal) inheritance for rotation/scale/translate."""
    tl = ((doc.get("animations") or {}).get(anim) or {}).get("bones", {}) if anim else {}
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for b in doc["bones"]:
        bt = tl.get(b["name"], {})
        rot = b.get("rotation", 0.0) + _sample(bt.get("rotate", []), t, ("value",), 0.0)[0]
        tx, ty = _sample(bt.get("translate", []), t, ("x", "y"), 0.0)
        sx, sy = _sample(bt.get("scale", []), t, ("x", "y"), 1.0)
        local_m = _rot(rot) @ np.diag([b.get("scaleX", 1.0) * sx, b.get("scaleY", 1.0) * sy])
        local_p = np.array([b.get("x", 0.0) + tx, b.get("y", 0.0) + ty])
        if "parent" in b:
            pm, pp = out[b["parent"]]
            out[b["name"]] = (pm @ local_m, pp + pm @ local_p)
        else:
            out[b["name"]] = (local_m, local_p)
    return out


def compose(doc: dict[str, Any], regions: dict[str, Image.Image], origin: Point, size: tuple[int, int],
            anim: str | None = None, t: float = 0.0, shift: tuple[int, int] = (0, 0)) -> Image.Image:
    """Draw every slot's region attachment in draw order onto a canvas in source-pixel space."""
    canvas = Image.new("RGBa", size, (0, 0, 0, 0))
    world = world_transforms(doc, anim, t)
    atts = doc["skins"][0]["attachments"]
    ox, oy = origin[0] + shift[0], origin[1] + shift[1]
    for slot in doc["slots"]:
        att_name = slot.get("attachment")
        if not att_name:
            continue
        att = atts[slot["name"]][att_name]
        img = regions[att.get("path", att_name)]
        w, h = img.size
        bm, bp = world[slot["bone"]]
        am = _rot(att.get("rotation", 0.0)) @ np.diag([att.get("scaleX", 1.0) * att["width"] / w,
                                                       att.get("scaleY", 1.0) * att["height"] / h])
        # image px (u right, v down) -> attachment space (y up) -> bone -> world (y up) -> canvas px (y down)
        img_to_att = np.array([[1.0, 0.0, -w / 2], [0.0, -1.0, h / 2], [0.0, 0.0, 1.0]])
        att_to_bone = np.eye(3)
        att_to_bone[:2, :2], att_to_bone[:2, 2] = am, (att["x"], att["y"])
        bone_to_world = np.eye(3)
        bone_to_world[:2, :2], bone_to_world[:2, 2] = bm, bp
        world_to_canvas = np.array([[1.0, 0.0, ox], [0.0, -1.0, oy], [0.0, 0.0, 1.0]])
        fwd = world_to_canvas @ bone_to_world @ att_to_bone @ img_to_att
        inv = np.linalg.inv(fwd)
        exact = np.allclose(fwd[:2, :2], np.eye(2), atol=1e-4)
        if exact and np.allclose(fwd[:2, 2], np.round(fwd[:2, 2]), atol=0.05):
            layer = Image.new("RGBa", size, (0, 0, 0, 0))
            layer.paste(img.convert("RGBa"), (int(round(fwd[0, 2])), int(round(fwd[1, 2]))))
        else:
            layer = img.convert("RGBa").transform(size, Image.Transform.AFFINE, tuple(inv[:2].reshape(-1)),
                                                   resample=Image.Resampling.BICUBIC)
        canvas = _over(canvas, layer)
    return canvas.convert("RGBA")


def _over(dst: Image.Image, src: Image.Image) -> Image.Image:
    d = np.asarray(dst, np.float32)
    s = np.asarray(src, np.float32)
    a = s[..., 3:4] / 255
    return Image.fromarray((s + d * (1 - a)).clip(0, 255).astype(np.uint8), "RGBa")


def compare(a: Image.Image, b: Image.Image) -> dict[str, float]:
    """Mean abs error of premultiplied RGBA (0-255) over the union of both silhouettes, plus alpha IoU."""
    x = np.asarray(a.convert("RGBa"), np.float32)
    y = np.asarray(b.convert("RGBa"), np.float32)
    ma, mb = x[..., 3] > 127, y[..., 3] > 127
    union = ma | mb
    if not union.any():
        return {"mae": 0.0, "iou": 1.0, "max_err_frac": 0.0}
    err = np.abs(x - y)[union]
    return {"mae": round(float(err.mean()), 3), "iou": round(float((ma & mb).sum() / union.sum()), 5),
            "max_err_frac": round(float((err.max(axis=1) > 32).mean()), 5)}


def preview_html(name: str, skeleton_json: bytes, atlas: bytes, page_name: str, page_png: bytes,
                 animations_list: list[str]) -> str:
    def uri(mime: str, data: bytes) -> str:
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"

    raw = {f"{name}.json": uri("application/json", skeleton_json), f"{name}.atlas": uri("text/plain", atlas),
           page_name: uri("image/png", page_png)}
    cfg = {"skeleton": f"{name}.json", "atlas": f"{name}.atlas", "jsonUrl": f"{name}.json",
           "atlasUrl": f"{name}.atlas", "rawDataURIs": raw, "animations": animations_list,
           "animation": animations_list[0] if animations_list else None, "premultipliedAlpha": False,
           "alpha": True, "backgroundColor": "#00000000", "showControls": True,
           "viewport": {"padLeft": "8%", "padRight": "8%", "padTop": "6%", "padBottom": "6%"}}
    title = html.escape(name)
    anims = html.escape(", ".join(animations_list) or "none")
    cfg_js = json.dumps(cfg).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Spine preview</title>
<link rel="stylesheet" href="{PLAYER_CSS}">
<script src="{PLAYER_JS}"></script>
<style>
:root {{ --bg: #f2f2f4; --fg: #1c1c1e; --muted: #6b6b70; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #1c1c1e; --fg: #f2f2f4; --muted: #a0a0a6; }} }}
html, body {{ margin: 0; height: 100%; background: var(--bg); color: var(--fg);
  font: 14px/1.4 Inter, system-ui, sans-serif; }}
main {{ display: flex; flex-direction: column; height: 100%; padding: 0 16px; box-sizing: border-box; }}
#player {{ flex: 1; min-height: 320px; }}
footer {{ color: var(--muted); padding: 8px 0 12px; font-size: 12px; }}
</style></head>
<body><main><div id="player"></div>
<footer>{title} — spine-player {PLAYER_VERSION} (Spine 4.2 runtime; using Spine runtimes requires a Spine Editor
license). Animations: {anims}.</footer></main>
<script>new spine.SpinePlayer("player", {cfg_js});</script>
</body></html>
"""
