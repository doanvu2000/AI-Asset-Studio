"""2D collision outlines and foot pivots traced from sprite alpha (marching squares + Douglas–Peucker)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from studio_core.export.sheet import Cell, Layout

if TYPE_CHECKING:
    from studio_core.export.source import Source

Point = tuple[float, float]
Polygon = list[Point]

ALPHA_THRESHOLD = 128
DEFAULT_TOLERANCE = 1.5
# Islands smaller than this share of the biggest one are specks (stray AA pixels, dust), not body parts.
MIN_ISLAND_SHARE = 0.02
_DEFAULT_RECIPES = ("sprite-8dir",)


@dataclass(frozen=True)
class ColliderOptions:
    tolerance: float = DEFAULT_TOLERANCE
    per_frame: bool = False
    occluder: bool = False


@dataclass
class SheetCollider:
    """Polygons in cell-local pixels (top-left origin, y down); `groups` keys follow Godot animation names."""
    shapes: dict[str, list[Polygon]] = field(default_factory=dict)
    groups: list[tuple[str, Polygon]] = field(default_factory=list)
    pivot: Point | None = None


def default_enabled(recipe_id: str | None) -> bool:
    return bool(recipe_id) and str(recipe_id).startswith(_DEFAULT_RECIPES)


def alpha_mask(img: object, threshold: int = ALPHA_THRESHOLD) -> np.ndarray:
    from PIL import Image

    assert isinstance(img, Image.Image)
    return np.asarray(img.convert("RGBA"))[..., 3] >= threshold


# Corner bits tl=8 tr=4 br=2 bl=1; saddles (5, 10) keep diagonal corners apart.
_SEGMENTS: dict[int, tuple[tuple[str, str], ...]] = {
    1: (("L", "B"),), 2: (("B", "R"),), 3: (("L", "R"),), 4: (("T", "R"),), 5: (("T", "R"), ("L", "B")),
    6: (("T", "B"),), 7: (("T", "L"),), 8: (("T", "L"),), 9: (("T", "B"),), 10: (("T", "L"), ("B", "R")),
    11: (("T", "R"),), 12: (("L", "R"),), 13: (("B", "R"),), 14: (("L", "B"),),
}
_CORNERS = ((8, -1, -1), (4, 1, -1), (2, 1, 1), (1, -1, 1))


def _loops(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Closed contours in doubled coordinates (pixel edge = even, pixel centre = odd), inside always on one side."""
    g = np.pad(mask.astype(np.uint8), 1)
    case = g[:-1, :-1] * 8 + g[:-1, 1:] * 4 + g[1:, 1:] * 2 + g[1:, :-1]
    nxt: dict[tuple[int, int], tuple[int, int]] = {}
    for cy, cx in zip(*np.nonzero((case > 0) & (case < 15))):
        c, x, y = int(case[cy, cx]), 2 * int(cx), 2 * int(cy)
        mid = {"T": (x, y - 1), "R": (x + 1, y), "B": (x, y + 1), "L": (x - 1, y)}
        inside = [(x + dx, y + dy) for bit, dx, dy in _CORNERS if c & bit]
        for a, b in _SEGMENTS[c]:
            p, q = mid[a], mid[b]
            mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            k = min(inside, key=lambda k: (k[0] - mx) ** 2 + (k[1] - my) ** 2)
            if (q[0] - p[0]) * (k[1] - p[1]) - (q[1] - p[1]) * (k[0] - p[0]) < 0:
                p, q = q, p
            nxt[p] = q
    out = []
    while nxt:
        start, cur = next(iter(nxt.items()))
        loop = [start]
        del nxt[start]
        while cur != start and cur in nxt:
            loop.append(cur)
            cur = nxt.pop(cur)
        out.append(loop)
    return out


def _area(poly: list[tuple[float, float]]) -> float:
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1])) / 2


def _seg_dist(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    if n == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(dx * (p[1] - a[1]) - dy * (p[0] - a[0])) / n


def _dp(pts: Polygon, tol: float) -> Polygon:
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        best, at = -1.0, -1
        for k in range(i + 1, j):
            d = _seg_dist(pts[k], pts[i], pts[j])
            if d > best:
                best, at = d, k
        if at > 0 and best > tol:
            keep[at] = True
            stack += [(i, at), (at, j)]
    return [p for p, k in zip(pts, keep) if k]


def simplify(poly: Polygon, tolerance: float) -> Polygon:
    """Douglas–Peucker on a closed ring, split at the vertex farthest from the first one."""
    if len(poly) < 4:
        return poly
    far = max(range(len(poly)), key=lambda i: (poly[i][0] - poly[0][0]) ** 2 + (poly[i][1] - poly[0][1]) ** 2)
    a = _dp(poly[: far + 1], tolerance)
    b = _dp(poly[far:] + poly[:1], tolerance)
    return a[:-1] + b[:-1]


def convex_hull(points: list[Point]) -> Polygon:
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq: list[Point]) -> list[Point]:
        h: list[Point] = []
        for p in seq:
            while len(h) >= 2 and ((h[-1][0] - h[-2][0]) * (p[1] - h[-2][1])
                                   - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0])) <= 0:
                h.pop()
            h.append(p)
        return h

    lower, upper = half(pts), half(pts[::-1])
    return lower[:-1] + upper[:-1]


def _oriented(poly: Polygon) -> Polygon:
    return poly if _area(poly) >= 0 else poly[::-1]


def trace(mask: np.ndarray, tolerance: float = DEFAULT_TOLERANCE, merge: str = "hull",
          min_island: float = MIN_ISLAND_SHARE) -> list[Polygon]:
    """Outer outlines of the opaque pixels (holes ignored). merge="hull" folds several real pieces into one convex
    polygon (one collider per sprite); "keep" returns each piece."""
    loops = _loops(mask)
    if not loops:
        return []
    areas = [_area(lp) for lp in loops]
    outer_sign = math.copysign(1, areas[max(range(len(areas)), key=lambda i: abs(areas[i]))])
    outers = [(abs(a) / 4, lp) for a, lp in zip(areas, loops) if a * outer_sign > 0]
    biggest = max(a for a, _ in outers)
    polys = []
    for a, lp in outers:
        if a < max(1.0, biggest * min_island):
            continue
        pts = [(x / 2, y / 2) for x, y in lp]
        s = simplify(pts, tolerance)
        polys.append(_oriented(s if len(s) >= 3 else convex_hull(pts)))
    if merge == "hull" and len(polys) > 1:
        return [_oriented(convex_hull([p for poly in polys for p in poly]))]
    return polys


def foot_pivot(mask: np.ndarray) -> Point | None:
    """Centre of the lowest opaque row, on that row's bottom edge (top-left origin, y down)."""
    rows = np.nonzero(mask.any(axis=1))[0]
    if not len(rows):
        return None
    y = int(rows[-1])
    xs = np.nonzero(mask[y])[0]
    return (int(xs[0]) + int(xs[-1]) + 1) / 2, float(y + 1)


def _union(alpha: np.ndarray, cells: list[Cell]) -> np.ndarray:
    h, w = max(c.h for c in cells), max(c.w for c in cells)
    out = np.zeros((h, w), dtype=bool)
    for c in cells:
        crop = alpha[c.y:c.y + c.h, c.x:c.x + c.w]
        out[:crop.shape[0], :crop.shape[1]] |= crop
    return out


def sheet_collider(sheet: Path, lay: Layout, opts: ColliderOptions) -> SheetCollider:
    from PIL import Image

    with Image.open(sheet) as im:
        alpha = alpha_mask(im)
    tracks = [(t.name, t.cells) for t in lay.tracks] or [("default", list(lay.cells))]
    out = SheetCollider()
    for anim, cells in tracks:
        if not cells:
            continue
        groups = [(f"{anim}_{i}", [c]) for i, c in enumerate(cells)] if opts.per_frame else [(anim, cells)]
        for key, members in groups:
            polys = trace(_union(alpha, members), opts.tolerance)
            for c in members:
                out.shapes[c.name] = polys
            if polys:
                out.groups.append((key, polys[0]))
    if lay.cells:
        out.pivot = foot_pivot(_union(alpha, lay.cells))
    return out


def for_sheet(src: Source, lay: Layout) -> SheetCollider | None:
    """Traces the sheet when the export asked for colliders; facing (character) sheets also get the foot pivot,
    VFX flipbooks keep their centred one."""
    opts = src.collider
    if opts is None or src.sheet is None or not lay.cells:
        return None
    col = sheet_collider(Path(src.sheet), lay, opts)
    if lay.facing and col.pivot is not None:
        lay.pivot = col.pivot
    return col


def _f(v: float) -> str:
    return f"{v:.6g}"


def unity_physics_shape(polys: list[Polygon], w: int, h: int) -> str:
    """Unity stores sprite physics outlines in pixels relative to the rect centre, y up."""
    if not polys:
        return "      physicsShape: []\n"
    lines = ["      physicsShape:"]
    for poly in polys:
        for i, (x, y) in enumerate(poly):
            lines.append(f"      {'- - ' if i == 0 else '  - '}{{x: {_f(x - w / 2)}, y: {_f(h / 2 - y)}}}")
    return "\n".join(lines) + "\n"


def _gd_num(v: float) -> str:
    s = _f(float(v) + 0.0)
    return s if any(ch in s for ch in ".e") else f"{s}.0"


def _packed(poly: Polygon, pivot: Point) -> str:
    return "PackedVector2Array(" + ", ".join(f"{_gd_num(x - pivot[0])}, {_gd_num(y - pivot[1])}"
                                             for x, y in poly) + ")"


GODOT_SCRIPT = """extends CollisionObject2D
## Turns on the Collider_<animation>[_<frame>] polygon (and Occluder_ twin) matching the Sprite child's frame.

@export var sprite: AnimatedSprite2D


func _ready() -> void:
	if sprite == null:
		sprite = get_node_or_null("Sprite") as AnimatedSprite2D
	if sprite == null:
		return
	sprite.animation_changed.connect(_sync)
	sprite.frame_changed.connect(_sync)
	_sync()


func _sync() -> void:
	var key := "%s_%d" % [sprite.animation, sprite.frame]
	if not has_node("Collider_" + key):
		key = String(sprite.animation)
	for child in get_children():
		if child is CollisionPolygon2D:
			child.set_deferred("disabled", String(child.name) != "Collider_" + key)
		elif child is LightOccluder2D:
			child.visible = String(child.name) == "Occluder_" + key
"""
GODOT_SCRIPT_FILE = "asset_studio_collider.gd"


def godot_body_scene(name: str, sprite_scene: str, col: SheetCollider, pivot: Point, default_anim: str,
                     body: str, occluder: bool) -> str:
    """CollisionPolygon2D must be a direct child of a physics body, so the sprite scene is instanced under one."""
    ext = [f'[ext_resource type="PackedScene" path="{sprite_scene}" id="1_sprite"]',
           f'[ext_resource type="Script" path="{GODOT_SCRIPT_FILE}" id="2_script"]']
    subs: list[str] = []
    nodes = [f'[node name="{name}" type="{body}"]', 'script = ExtResource("2_script")', '',
             '[node name="Sprite" parent="." instance=ExtResource("1_sprite")]']
    on = next((k for k, _ in col.groups if k in (default_anim, f"{default_anim}_0")), None)
    for i, (key, poly) in enumerate(col.groups):
        packed = _packed(poly, pivot)
        nodes += ['', f'[node name="Collider_{key}" type="CollisionPolygon2D" parent="."]', f"polygon = {packed}"]
        if key != on:
            nodes.append("disabled = true")
        if occluder:
            subs += [f'[sub_resource type="OccluderPolygon2D" id="OccluderPolygon2D_{i}"]', f"polygon = {packed}", '']
            nodes += ['', f'[node name="Occluder_{key}" type="LightOccluder2D" parent="."]',
                      f'occluder = SubResource("OccluderPolygon2D_{i}")']
            if key != on:
                nodes.append("visible = false")
    return "\n".join(['[gd_scene format=3]', '', *ext, '', *subs, *nodes]) + "\n"
