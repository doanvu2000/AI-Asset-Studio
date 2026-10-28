"""Paper-doll math (stdlib only): equipment slots, fitting an item mesh onto the rest rig, projecting sockets into
sprite cells. Imported by the Blender worker and by the core."""
from __future__ import annotations

import math
from typing import Any

SLOTS = ("weapon", "shield", "helmet", "armor")
SHAPES: dict[str, tuple[str, ...]] = {"weapon": ("sword", "axe", "staff"), "shield": ("round", "kite"),
                                      "helmet": ("helmet", "hat"), "armor": ("plate",)}
# socket -> (bone, position along it: 0 = bone head, 1 = tail)
SOCKETS: dict[str, tuple[str, float]] = {"hand_r": ("hand_r", 0.5), "hand_l": ("hand_l", 0.5),
                                         "head": ("head", 1.0), "chest": ("spine", 0.7)}
# (length as a fraction of character height, grip position along the long axis from the grip end)
WEAPON_FIT = {"sword": (0.55, 0.1), "axe": (0.5, 0.12), "staff": (1.0, 0.45), None: (0.55, 0.12)}
# The rest pose hangs the arms; a weapon pointing straight forward would read as a dot from the front.
WEAPON_DIR = {"staff": (0.0, -0.25, 1.0), None: (0.0, -0.55, 0.85)}

Vec = tuple[float, float, float]
Mat = list[list[float]]


def plan_equip(params: dict[str, Any], meshes: set[str]) -> list[dict[str, Any]]:
    """Slots in fixed order; a slot set to anything but none uses its mesh input when one is wired, else the
    primitive shape. `mesh` without an input is an error, so a forgotten file never renders a silent default."""
    left = str(params.get("weapon_hand") or "right").lower() == "left"
    bones = {"weapon": "hand_l" if left else "hand_r", "shield": "forearm_r" if left else "forearm_l",
             "helmet": "head", "armor": "spine"}
    out = []
    for slot in SLOTS:
        value = str(params.get(slot) or "none").strip().lower()
        if value in ("none", "off", "false", ""):
            continue
        if slot in meshes:
            shape = None
        elif value == "mesh":
            raise ValueError(f"{slot}: 'mesh' needs a {slot} mesh input")
        elif value in SHAPES[slot]:
            shape = value
        else:
            raise ValueError(f"{slot}: unknown shape {value!r} (use none, mesh, {', '.join(SHAPES[slot])})")
        size = float(params.get(f"{slot}_size") or 1.0)
        if not 0.1 <= size <= 5:
            raise ValueError(f"{slot}_size must be within 0.1..5")
        out.append({"name": slot, "slot": slot, "bone": bones[slot], "shape": shape, "mesh": shape is None,
                    "size": size, "flip": bool(params.get(f"{slot}_flip")), "side": -1 if left else 1})
    return out


def _sub(a: Vec, b: Vec) -> Vec:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _norm(a: Vec) -> Vec:
    n = math.sqrt(_dot(a, a)) or 1.0
    return a[0] / n, a[1] / n, a[2] / n


def lerp(a: Vec, b: Vec, t: float) -> Vec:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t


def matmul(a: Mat, b: Mat) -> Mat:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def apply(m: Mat, p: Vec) -> Vec:
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))  # type: ignore[return-value]


def translation(t: Vec) -> Mat:
    return [[1, 0, 0, t[0]], [0, 1, 0, t[1]], [0, 0, 1, t[2]], [0, 0, 0, 1]]


def _linear(cols: list[Vec], scale: Vec) -> Mat:
    return [[cols[j][i] * scale[j] for j in range(3)] + [0.0] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def axis_basis(long_i: int, flat_i: int, d: Vec, n: Vec) -> list[Vec]:
    """Rotation columns sending local axis long_i to d and flat_i to n (made orthogonal to d), right-handed."""
    d = _norm(d)
    n = _norm(_sub(n, tuple(x * _dot(n, d) for x in d)))  # type: ignore[arg-type]
    cols: list[Vec] = [(0.0, 0.0, 0.0)] * 3
    cols[long_i], cols[flat_i] = d, n
    mid = 3 - long_i - flat_i
    cols[mid] = _cross(cols[(mid + 1) % 3], cols[(mid + 2) % 3])
    return cols


def _extents(lo: Vec, hi: Vec) -> Vec:
    return tuple(max(1e-6, h - l) for l, h in zip(lo, hi))  # type: ignore[return-value]


def _order(ext: Vec) -> tuple[int, int]:
    long_i = max(range(3), key=lambda i: ext[i])
    flat_i = min((i for i in range(3) if i != long_i), key=lambda i: ext[i])
    return long_i, flat_i


def fit_matrix(item: dict[str, Any], lo: Vec, hi: Vec, rig: dict[str, Any]) -> Mat:
    """Rest-pose transform (armature space) for an item whose local bounding box is lo..hi. `rig`: height, bones
    {name: [head, tail]} at rest, head_box and torso_box [lo, hi] from the character's vertices."""
    h = float(rig["height"])
    slot, shape, size = item["slot"], item.get("shape"), float(item.get("size", 1.0))
    ext = _extents(lo, hi)
    center = lerp(lo, hi, 0.5)
    bones = rig["bones"]
    if slot == "weapon":
        long_i, flat_i = _order(ext)
        length, grip = WEAPON_FIT.get(shape, WEAPON_FIT[None])
        d = WEAPON_DIR.get(shape, WEAPON_DIR[None])
        if item.get("flip"):
            d = (-d[0], -d[1], -d[2])
        cols = axis_basis(long_i, flat_i, d, (0.0, -1.0, 0.0))
        s = length * h * size / ext[long_i]
        anchor = list(center)
        anchor[long_i] = hi[long_i] - grip * ext[long_i] if item.get("flip") else lo[long_i] + grip * ext[long_i]
        target = lerp(*bones[item["bone"]], 0.5)
        lin = _linear(cols, (s, s, s))
    elif slot == "shield":
        long_i, flat_i = _order(ext)
        side = float(item.get("side", 1))
        out = _norm((0.6 * side, -0.8, 0.0))
        cols = axis_basis(flat_i, long_i, out, (0.0, 0.0, 1.0))
        s = 0.34 * h * size / ext[long_i]
        anchor = list(center)
        arm = bones[item["bone"]]
        target = tuple(p + o * 0.05 * h for p, o in zip(lerp(arm[0], arm[1], 0.6), out))
        lin = _linear(cols, (s, s, s))
    elif slot == "helmet":
        box_lo, box_hi = rig["head_box"]
        width = max(box_hi[0] - box_lo[0], box_hi[1] - box_lo[1])
        hat = shape == "hat"
        s = (1.5 if hat else 1.12) * width * size / max(ext[0], ext[1])
        head_h = box_hi[2] - box_lo[2]
        anchor = [center[0], center[1], lo[2] if hat else hi[2]]
        top = box_hi[2] - 0.35 * head_h if hat else box_hi[2] + 0.03 * head_h
        target = ((box_lo[0] + box_hi[0]) / 2, (box_lo[1] + box_hi[1]) / 2, top)
        lin = _linear([(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)], (s, s, s))
    elif slot == "armor":
        box_lo, box_hi = rig["torso_box"]
        sx = (box_hi[0] - box_lo[0]) * 1.08 * size / ext[0]
        sy = (box_hi[1] - box_lo[1]) * 1.15 * size / ext[1]
        sz = (box_hi[2] - box_lo[2]) * 0.8 / ext[2]
        anchor = [center[0], center[1], hi[2]]
        target = ((box_lo[0] + box_hi[0]) / 2, (box_lo[1] + box_hi[1]) / 2, box_hi[2])
        lin = _linear([(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)], (sx, sy, sz))
    else:
        raise ValueError(f"unknown slot {slot!r}")
    return matmul(translation(target), matmul(lin, translation((-anchor[0], -anchor[1], -anchor[2]))))


def to_view(p: Vec, angle: float, elev: float) -> Vec:
    """Turntable yaw `angle` (the worker turns the root by -angle) then the ortho camera tilted down `elev` degrees:
    (screen right, screen up, depth away from the camera)."""
    a, e = math.radians(angle), math.radians(elev)
    x = p[0] * math.cos(a) + p[1] * math.sin(a)
    y = -p[0] * math.sin(a) + p[1] * math.cos(a)
    return x, y * math.sin(e) + p[2] * math.cos(e), y * math.cos(e) - p[2] * math.sin(e)


def to_cell(p: Vec, angle: float, elev: float, k: float, pivot: tuple[float, float]) -> Vec:
    """Cell pixels (origin top-left, y down) for k pixels per world unit; pivot = where the feet origin lands."""
    x, up, depth = to_view(p, angle, elev)
    return pivot[0] + x * k, pivot[1] - up * k, depth


def socket_frame(pose: dict[str, Any], angle: float, elev: float, k: float, pivot: tuple[float, float],
                 height: float) -> dict[str, Any]:
    """`pose`: sockets {name: {p, d}} and item centres {name: p} in armature space for one clip frame. rot is the
    bone direction on screen in degrees, clockwise from +x (image space); z says whether the socket sits in front of
    the chest or behind the body from this view."""
    socks = pose["sockets"]
    chest = to_view(tuple(socks["chest"]["p"]), angle, elev)[2] if "chest" in socks else 0.0  # type: ignore[arg-type]
    tol = 0.04 * height
    out = []
    for name, s in socks.items():
        x, y, depth = to_cell(tuple(s["p"]), angle, elev, k, pivot)  # type: ignore[arg-type]
        dx, dup, _ = to_view(tuple(s["d"]), angle, elev)  # type: ignore[arg-type]
        length = math.sqrt(_dot(tuple(s["d"]), tuple(s["d"]))) or 1.0  # type: ignore[arg-type]
        out.append({"name": name, "x": round(x, 2), "y": round(y, 2),
                    "rot": round(math.degrees(math.atan2(-dup, dx)), 1),
                    "scale": round(math.hypot(dx, dup) / length, 3),
                    "z": "back" if depth > chest + tol else "front", "depth": round(depth - chest, 4)})
    depths = {n: to_view(tuple(p), angle, elev)[2] for n, p in pose.get("items", {}).items()}  # type: ignore[arg-type]
    return {"sockets": out, "order": sorted(depths, key=lambda n: -depths[n])}
