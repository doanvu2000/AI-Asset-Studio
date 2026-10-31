"""`op.room_kit`: a kit of procedural rooms (roguelike / dungeon) on a playable scene's tileset and props. Every room
has the same size and its doors at fixed mid-edge cells, so any two rooms join where their doors meet. The interior is
filled by wave function collapse (adapters/wfc.py; a small grammar when it keeps contradicting) and a room is kept only
when a walk (topdown) or a jump route (sideview, up to `jump` tiles) joins all its doors. Writes room_kit.json, one
Tiled .tmx and LDtk .ldtk per room, a one-seed map preview and a contact sheet of the rooms. `generate_layout` is the
same joining algorithm the exported RoomGenerator.gd / RoomGenerator.cs run."""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import random
import xml.etree.ElementTree as ET
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..adapters import wfc
from ..contracts import Outputs, StepContext, StepError, StepResult
from . import playable_scene as ps
from .executors import OpExecutor, _put_png, _read_json

KIT_FILE = "room_kit.json"
KIT_TYPE = "asset-studio/room-kit"
RULES_FILE = "room_rules.json"
RULES_TYPE = "asset-studio/room-rules"
SIDES = ("N", "E", "S", "W")
STEP = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}
OPPOSITE = {"N": "S", "E": "W", "S": "N", "W": "E"}
KINDS = ("start", "combat", "corridor", "treasure", "boss")
SHAPES = ("open", "pillars", "blocks", "ring", "arena")
STYLES = ps.STYLES
SOLID, FLOOR, PATH, AIR = ps.SOLID, ps.FLOOR, ps.PATH, ps.AIR
LANE = 3
JUMP_REACH = 3
MAX_PROPS = 6
ROOM_TRIES = 8
WFC_TRIES = 6
WFC_RESTARTS = 5
WFC_PATTERN = 3
MIN_OPEN_SHARE = 0.45
DEFAULT_RULES: dict[str, dict[str, Any]] = {
    "start": {"min_doors": 1, "max_doors": 2, "straight": False, "shape": "open", "props": 0, "arena": 0, "share": 0.10},
    "combat": {"min_doors": 2, "max_doors": 4, "straight": False, "shape": "pillars", "props": 2, "arena": 0,
               "share": 0.35},
    "corridor": {"min_doors": 2, "max_doors": 2, "straight": True, "shape": "open", "props": 0, "arena": 0,
                 "share": 0.25},
    "treasure": {"min_doors": 1, "max_doors": 1, "straight": False, "shape": "ring", "props": 3, "arena": 0,
                 "share": 0.15},
    "boss": {"min_doors": 1, "max_doors": 1, "straight": False, "shape": "arena", "props": 1, "arena": 4,
             "share": 0.15},
}
MIN_COUNT = {"start": 1, "combat": 1, "boss": 1, "corridor": 0, "treasure": 0}

SAMPLES: dict[str, dict[str, list[str]]] = {
    "topdown": {
        "open": ["..........", "..........", "....##....", "....##....", "..........", "..........", "..#.......",
                 ".........."],
        "pillars": ["..........", ".##....##.", ".##....##.", "..........", "..........", ".##....##.", ".##....##.",
                    ".........."],
        "blocks": ["..........", ".####.....", ".#........", ".#...##...", ".....##...", "..........", "....#####.",
                   ".........."],
        "ring": ["..........", "..######..", "..#....#..", "..#....#..", "..######..", "..........", ".........."],
        "arena": ["..........", "..........", ".#......#.", "..........", "..........", ".#......#.", "..........",
                  ".........."],
    },
    "sideview": {
        "open": ["............", "............", "............", "....###.....", "............", ".##.........",
                 "............", "######..####"],
        "pillars": ["............", "............", "............", "............", "............", "..#....#....",
                    "..#....##...", "###...####.."],
        "blocks": ["............", "............", "..####......", "............", "........###.", "............",
                   "...##.......", "##.......###"],
        "ring": ["............", "............", ".###....###.", "............", "....####....", "............",
                 "..##....##..", "............"],
        "arena": ["............", "............", "............", "............", "............", "............",
                  ".##......##.", "............"],
    },
}


def sides_for(style: str) -> tuple[str, ...]:
    return SIDES if style == "topdown" else ("E", "W")


def door_cells(style: str, cols: int, rows: int, side: str) -> list[tuple[int, int]]:
    """The wall cells a door opens: two mid-edge cells (topdown) or the three cells above the floor (sideview)."""
    if style == "topdown":
        cx, cy = cols // 2, rows // 2
        return {"N": [(cx - 1, 0), (cx, 0)], "S": [(cx - 1, rows - 1), (cx, rows - 1)],
                "W": [(0, cy - 1), (0, cy)], "E": [(cols - 1, cy - 1), (cols - 1, cy)]}[side]
    x = 0 if side == "W" else cols - 1
    return [(x, y) for y in range(rows - 4, rows - 1)]


def lane_cells(style: str, cols: int, rows: int, side: str) -> list[tuple[int, int]]:
    dx, dy = STEP[side]
    return [(x - dx * k, y - dy * k) for x, y in door_cells(style, cols, rows, side) for k in range(1, LANE + 1)]


def spine_cells(cols: int, rows: int, side: str) -> set[tuple[int, int]]:
    """Topdown: a 2-wide arm from the door to the 2x2 centre every arm meets in."""
    cx, cy = cols // 2, rows // 2
    if side == "N":
        return {(x, y) for x in (cx - 1, cx) for y in range(0, cy + 1)}
    if side == "S":
        return {(x, y) for x in (cx - 1, cx) for y in range(cy - 1, rows)}
    if side == "W":
        return {(x, y) for y in (cy - 1, cy) for x in range(0, cx + 1)}
    return {(x, y) for y in (cy - 1, cy) for x in range(cx - 1, cols)}


def _int_between(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def normalize_rules(raw: Any, style: str) -> dict[str, Any]:
    """Claude's proposal (or a rules document this function wrote) -> a rule per kind inside the limits the generator
    can honour; missing or unusable entries keep the default rule. `source` is "claude" only when at least one entry
    was taken from Claude."""
    n_sides = len(sides_for(style))
    rules = {k: dict(v) for k, v in DEFAULT_RULES.items()}
    taken = 0
    items = raw.get("rooms") if isinstance(raw, dict) else None
    source = None
    if items is None and isinstance(raw, dict) and isinstance(raw.get("kinds"), dict):
        items = [{**v, "kind": k} for k, v in raw["kinds"].items() if isinstance(v, dict)]
        source = raw.get("source") if raw.get("source") in ("claude", "default") else None
    for item in items if isinstance(items, list) else []:
        kind = str(item.get("kind") or "").strip().lower() if isinstance(item, dict) else ""
        if kind not in rules:
            continue
        r, d = rules[kind], DEFAULT_RULES[kind]
        r["min_doors"] = _int_between(item.get("min_doors"), 1, n_sides, d["min_doors"])
        r["max_doors"] = max(r["min_doors"], _int_between(item.get("max_doors"), 1, n_sides, d["max_doors"]))
        r["straight"] = bool(item.get("straight", d["straight"]))
        r["shape"] = str(item.get("shape")).lower() if str(item.get("shape")).lower() in SHAPES else d["shape"]
        r["props"] = _int_between(item.get("props"), 0, MAX_PROPS, d["props"])
        r["arena"] = _int_between(item.get("arena"), 0, 6, d["arena"])
        try:
            r["share"] = max(0.02, min(1.0, float(item.get("share"))))
        except (TypeError, ValueError):
            r["share"] = d["share"]
        if r["straight"]:
            r["min_doors"] = r["max_doors"] = 2
        if item.get("note"):
            r["note"] = str(item["note"])[:200]
        taken += 1
    for kind, r in rules.items():
        if not door_sets(style, r):
            rules[kind] = dict(DEFAULT_RULES[kind])
    return {"type": RULES_TYPE, "style": style, "source": source or ("claude" if taken else "default"), "kinds": rules,
            "reason": str(raw.get("reason") or "")[:600] if isinstance(raw, dict) else ""}


def door_sets(style: str, rule: dict[str, Any]) -> list[tuple[str, ...]]:
    """Door combinations a rule allows, largest first: [0] is the hub with the most doors, so rooms of that kind
    always offer a door on every side the rule can reach."""
    sides = sides_for(style)
    out: list[tuple[str, ...]] = []
    for size in range(min(rule["max_doors"], len(sides)), max(1, rule["min_doors"]) - 1, -1):
        for combo in itertools.combinations(sides, size):
            if rule["straight"] and not (size == 2 and OPPOSITE[combo[0]] == combo[1]):
                continue
            out.append(combo)
    return out


def kind_counts(n: int, rules: dict[str, dict[str, Any]]) -> dict[str, int]:
    total = sum(rules[k]["share"] for k in KINDS)
    counts = {k: max(MIN_COUNT[k], round(n * rules[k]["share"] / total)) for k in KINDS}
    while sum(counts.values()) != n:
        if sum(counts.values()) < n:
            counts["combat"] += 1
        else:
            k = max(KINDS, key=lambda c: (counts[c] - MIN_COUNT[c], counts[c]))
            if counts[k] - MIN_COUNT[k] <= 0:
                break
            counts[k] -= 1
    return counts


def plan_rooms(n: int, rules: dict[str, dict[str, Any]], style: str, seed: int) -> list[tuple[str, tuple[str, ...]]]:
    """(kind, doors) per room. A kind's first room takes its largest door set; the others cycle the remaining sets
    from a seed-dependent offset."""
    out = []
    for kind, count in kind_counts(n, rules).items():
        sets = door_sets(style, rules[kind])
        off = random.Random(f"{seed}/{kind}").randrange(max(1, len(sets) - 1))
        for j in range(count):
            out.append((kind, sets[0] if j == 0 or len(sets) == 1 else sets[1 + (j - 1 + off) % (len(sets) - 1)]))
    order = {k: i for i, k in enumerate(KINDS)}
    out.sort(key=lambda kd: order[kd[0]])
    return out


def _seed(*parts: Any) -> int:
    return int.from_bytes(hashlib.blake2b("/".join(map(str, parts)).encode(), digest_size=8).digest(), "little")


def make_grid(style: str, cols: int, rows: int, doors: tuple[str, ...]) -> list[list[str]]:
    if style == "topdown":
        g = [[SOLID if x in (0, cols - 1) or y in (0, rows - 1) else FLOOR for x in range(cols)] for y in range(rows)]
        for side in doors:
            for x, y in spine_cells(cols, rows, side):
                g[y][x] = PATH
        return g
    g = [[SOLID if x in (0, cols - 1) or y in (0, rows - 1) else AIR for x in range(cols)] for y in range(rows)]
    for side in doors:
        for x, y in door_cells(style, cols, rows, side):
            g[y][x] = AIR
    return g


def pinned_open(style: str, cols: int, rows: int, doors: tuple[str, ...], arena: int) -> set[tuple[int, int]]:
    """Interior cells the fill must leave open: the lane in front of each door, the path arms, the arena."""
    cells: set[tuple[int, int]] = set()
    for side in doors:
        cells.update(lane_cells(style, cols, rows, side))
        if style == "topdown":
            cells.update(spine_cells(cols, rows, side))
    cx, cy = cols // 2, rows // 2
    if arena:
        if style == "topdown":
            cells.update((x, y) for x in range(cx - arena, cx + arena) for y in range(cy - arena, cy + arena))
        else:
            cells.update((x, y) for x in range(cx - arena, cx + arena) for y in range(rows - 6, rows - 1))
    return {(x, y) for x, y in cells if 1 <= x <= cols - 2 and 1 <= y <= rows - 2}


@lru_cache(maxsize=32)
def _model(style: str, shape: str, width: int, height: int) -> wfc.Wfc:
    samples = [wfc.sample_array(SAMPLES[style][shape], ".#")]
    return wfc.Wfc(samples, width, height, WFC_PATTERN)


def fill_wfc(style: str, shape: str, cols: int, rows: int, pinned: set[tuple[int, int]], seed: int) -> np.ndarray:
    """Interior solidity (1 = solid), shape (rows - 2, cols - 2)."""
    model = _model(style, shape, cols - 2, rows - 2)
    return model.solve(seed, {(x - 1, y - 1): 0 for x, y in pinned}, WFC_RESTARTS)


def fill_grammar(style: str, cols: int, rows: int, pinned: set[tuple[int, int]], seed: int) -> np.ndarray:
    """Three to six blocks (topdown) or low ledges (sideview) that avoid pinned cells, so doors always stay joined."""
    rng = random.Random(seed)
    out = np.zeros((rows - 2, cols - 2), np.uint8)
    want, placed = rng.randint(3, 6), 0
    for _ in range(80):
        if placed >= want:
            break
        if style == "topdown":
            w, h = rng.randint(2, 3), 2
            x, y = rng.randint(2, cols - 2 - w), rng.randint(2, rows - 2 - h)
            margin = 1
        else:
            w, h = min(rng.randint(3, 4), cols - 3 - 2 * LANE), 1
            x, y = rng.randint(LANE + 1, cols - 2 - LANE - w), rng.randint(rows - 8, rows - 5)
            margin = 0
        if any((cx, cy) in pinned for cx in range(x - margin, x + w + margin)
               for cy in range(y - margin, y + h + margin)):
            continue
        out[y - 1:y - 1 + h, x - 1:x - 1 + w] = 1
        placed += 1
    return out


def _free(grid: list[list[str]], x: int, y: int) -> bool:
    return 0 <= y < len(grid) and 0 <= x < len(grid[0]) and grid[y][x] == AIR


def _standable(grid: list[list[str]], x: int, y: int) -> bool:
    return (_free(grid, x, y) and _free(grid, x, y - 1) and y + 1 < len(grid) and grid[y + 1][x] == SOLID)


def _jump_path(grid: list[list[str]], x: int, y: int, tx: int, ty: int) -> bool:
    """L-shaped route: up (or level) to the row above the higher foot, across, then down onto the target."""
    top = min(y, ty) - 1
    step = 1 if tx > x else -1
    return (all(_free(grid, x, r) for r in range(top, y + 1))
            and all(_free(grid, xi, r) for xi in range(x, tx + step, step) for r in (top, top + 1))
            and all(_free(grid, tx, r) for r in range(top, ty + 1)))


def reach_sideview(grid: list[list[str]], start: tuple[int, int], jump: int) -> set[tuple[int, int]]:
    """Standing cells the player gets to from `start`: walking, walking off a ledge, and jumps that rise at most
    `jump` tiles, with the horizontal reach shrinking as the rise grows."""
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        found: list[tuple[int, int]] = []
        for dx in (-1, 1):
            nx = x + dx
            if _free(grid, nx, y) and _free(grid, nx, y - 1):
                ty = y
                while _free(grid, nx, ty + 1):
                    ty += 1
                found.append((nx, ty))
        for ty in range(y - jump, y + 4):
            reach = max(1, JUMP_REACH - max(0, y - ty - 1))
            for tx in range(x - reach, x + reach + 1):
                if tx != x and _standable(grid, tx, ty) and _jump_path(grid, x, y, tx, ty):
                    found.append((tx, ty))
        for cell in found:
            if cell not in seen and _standable(grid, *cell):
                seen.add(cell)
                queue.append(cell)
    return seen


def reach_topdown(grid: list[list[str]], start: tuple[int, int]) -> set[tuple[int, int]]:
    h, w = len(grid), len(grid[0])
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and grid[ny][nx] in (FLOOR, PATH):
                seen.add((nx, ny))
                queue.append((nx, ny))
    return seen


def doors_joined(style: str, grid: list[list[str]], doors: tuple[str, ...], jump: int) -> bool:
    """True when every door can be walked (topdown) or jumped (sideview) to from the first one."""
    cols, rows = len(grid[0]), len(grid)
    if style == "topdown":
        seen = reach_topdown(grid, door_cells(style, cols, rows, doors[0])[0])
        return all(c in seen for side in doors for c in door_cells(style, cols, rows, side))
    stands = [door_cells(style, cols, rows, side)[-1] for side in doors]
    if not all(_standable(grid, *c) for c in stands):
        return False
    seen = reach_sideview(grid, stands[0], jump)
    return all(c in seen for c in stands)


def _with_solid(grid: list[list[str]], cells: set[tuple[int, int]]) -> list[list[str]]:
    g = [row[:] for row in grid]
    for x, y in cells:
        g[y][x] = SOLID
    return g


def _apply_fill(grid: list[list[str]], fill: np.ndarray) -> None:
    for (y, x), v in np.ndenumerate(fill):
        if v and grid[y + 1][x + 1] != PATH:
            grid[y + 1][x + 1] = SOLID


def _seal_pockets(grid: list[list[str]], start: tuple[int, int]) -> None:
    seen = reach_topdown(grid, start)
    for y, row in enumerate(grid):
        for x, c in enumerate(row):
            if c == FLOOR and (x, y) not in seen:
                row[x] = SOLID


def _spawn(style: str, grid: list[list[str]], doors: tuple[str, ...], jump: int) -> tuple[int, int]:
    cols, rows = len(grid[0]), len(grid)
    if style == "topdown":
        return cols // 2, rows // 2
    stand = door_cells(style, cols, rows, doors[0])[-1]
    cells = reach_sideview(grid, stand, jump)
    return min(cells, key=lambda c: (abs(c[0] - cols // 2), abs(c[1] - (rows - 2)), c[1], c[0]))


def place_props(style: str, grid: list[list[str]], spawn: tuple[int, int], rng: random.Random, n_kinds: int,
                count: int, jump: int) -> list[tuple[int, int, int]]:
    """(kind, x, y) cells, spaced and clear of the door lanes."""
    if not n_kinds or count <= 0:
        return []
    cols = len(grid[0])
    if style == "topdown":
        seen = reach_topdown(grid, spawn)
        cand = [(x, y) for x, y in seen if grid[y][x] == FLOOR and 2 <= x <= cols - 3]
    else:
        seen = reach_sideview(grid, spawn, jump)
        cand = [(x, y) for x, y in seen if LANE + 1 <= x <= cols - 2 - LANE]
    cand.sort(key=lambda c: (c[1], c[0]))
    rng.shuffle(cand)
    out: list[tuple[int, int, int]] = []
    for x, y in cand:
        if len(out) < count and all(abs(x - px) + abs(y - py) > 3 for _, px, py in out):
            out.append((rng.randrange(n_kinds), x, y))
    return out


def build_grid(style: str, cols: int, rows: int, kind: str, doors: tuple[str, ...], rule: dict[str, Any],
               n_kinds: int, jump: int, key: tuple[Any, ...]) -> dict[str, Any]:
    """One valid room: tries WFC with fresh seeds, then the grammar; a try is kept only when its doors are joined."""
    pinned = pinned_open(style, cols, rows, doors, rule["arena"])
    errors: list[str] = []
    for attempt in range(ROOM_TRIES):
        seed = _seed(*key, attempt)
        use_wfc = attempt < WFC_TRIES
        try:
            fill = (fill_wfc(style, rule["shape"], cols, rows, pinned, seed) if use_wfc
                    else fill_grammar(style, cols, rows, pinned, seed))
        except wfc.WfcError as e:
            errors.append(str(e))
            continue
        grid = make_grid(style, cols, rows, doors)
        _apply_fill(grid, fill)
        if style == "topdown":
            _seal_pockets(grid, (cols // 2, rows // 2))
            walk = sum(c in (FLOOR, PATH) for row in grid[1:-1] for c in row[1:-1])
            if walk < MIN_OPEN_SHARE * (cols - 2) * (rows - 2):
                errors.append("too little open floor")
                continue
        if not doors_joined(style, grid, doors, jump):
            errors.append("doors not joined")
            continue
        spawn = _spawn(style, grid, doors, jump)
        rng = random.Random(seed)
        props = place_props(style, grid, spawn, rng, n_kinds, rule["props"], jump)
        if props and not doors_joined(style, _with_solid(grid, {(x, y) for _, x, y in props}), doors, jump):
            props = []
        return {"grid": grid, "spawn": spawn, "props": props, "source": "wfc" if use_wfc else "grammar",
                "attempts": attempt + 1}
    raise StepError("op_room_failed", f"no valid {kind} room after {ROOM_TRIES} tries ({'; '.join(errors[-3:])})")


def room_layers(style: str, grid: list[list[str]], doors: tuple[str, ...], at: ps.Atlas,
                autotile: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, list[list[int]]]]:
    """Tile layers of the open room and, per door, the Solid tiles that close it (cells that change when the door
    cells become wall) — what the generators switch on for a door nobody joins."""
    cols, rows = len(grid[0]), len(grid)

    def layout(g: list[list[str]]) -> dict[str, Any]:
        return {"cols": cols, "rows": rows, "style": style, "grid": ["".join(r) for r in g]}

    layers = ps.tile_layers(layout(grid), at, autotile)
    open_solid = next(la["tiles"] for la in layers if la["name"] == "Solid")
    plugs: dict[str, list[list[int]]] = {}
    for side in doors:
        sealed = _with_solid(grid, set(door_cells(style, cols, rows, side)))
        solid = next(la["tiles"] for la in ps.tile_layers(layout(sealed), at, autotile) if la["name"] == "Solid")
        plugs[side] = [[i % cols, i // cols, t] for i, (t, o) in enumerate(zip(solid, open_solid))
                       if t >= 0 and t != o]
    return layers, plugs


def room_scene(kit: dict[str, Any], room: dict[str, Any]) -> dict[str, Any]:
    """The playable-scene shaped dict ps.tiled_tmx / ps.ldtk_project write."""
    t = kit["tile"]
    sx, sy = room["spawn"]
    return {"asset": kit["asset"], "style": kit["style"], "tile": t, "cols": kit["cols"], "rows": kit["rows"],
            "tileset": kit["tileset"], "prop_kinds": kit["prop_kinds"], "background": [], "layers": room["layers"],
            "props": room["props"], "spawn": room["spawn"],
            "player": {"name": "PlayerSpawn", "pos": [(sx + 0.5) * t, (sy + 1) * t - 1 if kit["style"] == "sideview"
                                                       else (sy + 0.6) * t]}}


def _door_box(kit: dict[str, Any], room: dict[str, Any], side: str) -> tuple[int, int, int, int]:
    t = kit["tile"]
    cells = room["door_cells"][side]
    x0, y0 = min(c[0] for c in cells), min(c[1] for c in cells)
    x1, y1 = max(c[0] for c in cells), max(c[1] for c in cells)
    return x0 * t, y0 * t, (x1 - x0 + 1) * t, (y1 - y0 + 1) * t


def tmx_with_doors(kit: dict[str, Any], room: dict[str, Any]) -> str:
    root = ET.fromstring(ps.tiled_tmx(room_scene(kit, room), kit["asset"]).split("\n", 1)[1])
    group = next(g for g in root.iter("objectgroup") if g.get("name") == "Objects")
    oid = int(root.get("nextobjectid", "1"))
    ET.SubElement(root.find("properties"), "property", {"name": "kind", "value": room["kind"]})
    for side in room["doors"]:
        x, y, w, h = _door_box(kit, room, side)
        o = ET.SubElement(group, "object", {"id": str(oid), "name": side, "type": "door", "x": str(x), "y": str(y),
                                            "width": str(w), "height": str(h)})
        ET.SubElement(ET.SubElement(o, "properties"), "property", {"name": "side", "value": side})
        oid += 1
    root.set("nextobjectid", str(oid))
    ET.indent(root, " ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def ldtk_with_doors(kit: dict[str, Any], room: dict[str, Any]) -> str:
    doc = json.loads(ps.ldtk_project(room_scene(kit, room), kit["asset"]))
    t, def_uid = kit["tile"], 13
    doc["defs"]["entities"].append(ps._ldtk_entity_def("Door", def_uid, t, t, "#E0B030", []))
    entities = next(la for la in doc["levels"][0]["layerInstances"] if la["__identifier"] == "Entities")
    for n, side in enumerate(room["doors"]):
        x, y, w, h = _door_box(kit, room, side)
        px, py = x + w // 2, y + h
        entities["entityInstances"].append({
            "__identifier": "Door", "__grid": [x // t, y // t], "__pivot": [0.5, 1], "__tags": [], "__tile": None,
            "__smartColor": "#E0B030", "__worldX": px, "__worldY": py,
            "iid": ps._iid(kit["asset"], room["name"], "door", side, n), "width": w, "height": h, "defUid": def_uid,
            "px": [px, py], "fieldInstances": []})
    return json.dumps(doc, indent=1, ensure_ascii=False)


def _pick(rooms: list[dict[str, Any]], plan: dict[tuple[int, int], int], open_doors: list[tuple[tuple[int, int], str]],
          target: tuple[int, int], need: str, last: bool, rng: random.Random) -> int:
    closed = sum(1 for (cx, cy), s in open_doors if (cx + STEP[s][0], cy + STEP[s][1]) == target)
    others = len(open_doors) - closed
    pool: list[int] = []
    bosses: list[int] = []
    for i, r in enumerate(rooms):
        if r["kind"] == "start" or need not in r["doors"]:
            continue
        fresh = sum(1 for d in r["doors"] if (target[0] + STEP[d][0], target[1] + STEP[d][1]) not in plan)
        if not last and others + fresh == 0:
            continue
        if r["kind"] == "boss":
            if last:
                bosses.append(i)
            continue
        pool.append(i)
    pool = bosses if last and bosses else pool
    if not pool:
        return -1
    total = sum(max(0.0, rooms[i].get("weight", 1.0)) for i in pool) or 1.0
    roll = rng.random() * total
    for i in pool:
        roll -= max(0.0, rooms[i].get("weight", 1.0))
        if roll <= 0:
            return i
    return pool[-1]


def open_doors_of(rooms: list[dict[str, Any]], plan: dict[tuple[int, int], int]) -> list[tuple[tuple[int, int], str]]:
    return [(c, s) for c, i in plan.items() for s in rooms[i]["doors"]
            if (c[0] + STEP[s][0], c[1] + STEP[s][1]) not in plan]


def generate_layout(rooms: list[dict[str, Any]], count: int, seed: int, max_attempts: int = 40) -> dict[str, Any]:
    """Joins rooms on a grid of room-sized cells: the start first, each next room attached to an open door through
    a room that has the matching door and still leaves a door open, a boss in the last slot when a boss fits a door.
    Stops with a reason (never loops forever) when no door or no fitting room is left."""
    rng = random.Random(seed)
    starts = [i for i, r in enumerate(rooms) if r["kind"] == "start"]
    if not starts:
        return {"cells": {}, "stopped": "the kit has no start room", "tries": 0}
    plan = {(0, 0): starts[rng.randrange(len(starts))]}
    stopped, tries = "", 0
    limit = max(1, count) * max_attempts
    while len(plan) < count:
        tries += 1
        if tries > limit:
            stopped = f"gave up after {limit} attempts with {len(plan)} of {count} rooms"
            break
        open_list = open_doors_of(rooms, plan)
        if not open_list:
            stopped = f"no open door left after {len(plan)} of {count} rooms"
            break
        last = len(plan) == count - 1
        slots = [o for o in open_list if any(r["kind"] == "boss" and OPPOSITE[o[1]] in r["doors"] for r in rooms)]             if last else []
        (cx, cy), side = (slots or open_list)[rng.randrange(len(slots or open_list))]
        target = (cx + STEP[side][0], cy + STEP[side][1])
        pick = _pick(rooms, plan, open_list, target, OPPOSITE[side], last, rng)
        if pick >= 0:
            plan[target] = pick
    return {"cells": plan, "stopped": stopped, "tries": tries}


def sealed_doors(rooms: list[dict[str, Any]], plan: dict[tuple[int, int], int]) -> dict[tuple[int, int], list[str]]:
    """Doors with no matching door across them: the generators close these with the room's plug tiles."""
    out: dict[tuple[int, int], list[str]] = {}
    for c, i in plan.items():
        for s in rooms[i]["doors"]:
            n = (c[0] + STEP[s][0], c[1] + STEP[s][1])
            if n not in plan or OPPOSITE[s] not in rooms[plan[n]]["doors"]:
                out.setdefault(c, []).append(s)
    return out


def _atlas_tile(atlas: Image.Image, t: int, tid: int) -> Image.Image:
    c = ps.ATLAS_COLS
    return atlas.crop(((tid % c) * t, (tid // c) * t, (tid % c + 1) * t, (tid // c + 1) * t))


def render_room(kit: dict[str, Any], room: dict[str, Any], atlas: Image.Image, props: list[Image.Image],
                sealed: list[str]) -> Image.Image:
    t, cols, rows = kit["tile"], kit["cols"], kit["rows"]
    img = Image.new("RGBA", (cols * t, rows * t), (28, 30, 38, 255))
    for la in room["layers"]:
        for i, tid in enumerate(la["tiles"]):
            if tid >= 0:
                img.alpha_composite(_atlas_tile(atlas, t, tid), ((i % cols) * t, (i // cols) * t))
    for side in sealed:
        for x, y, tid in room["plugs"][side]:
            img.alpha_composite(_atlas_tile(atlas, t, tid), (x * t, y * t))
    for p in sorted(room["props"], key=lambda q: q["pos"][1]):
        spr = props[p["kind"]]
        ps._paste(img, spr, (int(p["pos"][0] - spr.width / 2), int(p["pos"][1] - spr.height)))
    return img


def _fit(img: Image.Image, scale: float, pixel: bool) -> Image.Image:
    if scale >= 1:
        return img
    size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    return img.resize(size, Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS)


def render_map(kit: dict[str, Any], atlas: Image.Image, props: list[Image.Image], limit: int = 3200) -> Image.Image:
    plan = {tuple(m["cell"]): m for m in kit["map"]["cells"]}
    if not plan:
        return Image.new("RGBA", (kit["cols"] * kit["tile"], kit["rows"] * kit["tile"]), (28, 30, 38, 255))
    xs, ys = [c[0] for c in plan], [c[1] for c in plan]
    w, h = (max(xs) - min(xs) + 1), (max(ys) - min(ys) + 1)
    rw, rh = kit["cols"] * kit["tile"], kit["rows"] * kit["tile"]
    s = min(1.0, limit / max(w * rw, h * rh))
    pixel = kit["tile"] <= 16
    canvas = Image.new("RGBA", (int(w * rw * s) + 1, int(h * rh * s) + 1), (12, 13, 18, 255))
    for (cx, cy), m in plan.items():
        room = kit["rooms"][m["room"]]
        tile_img = _fit(render_room(kit, room, atlas, props, m["sealed"]), s, pixel)
        canvas.alpha_composite(tile_img, (int((cx - min(xs)) * rw * s), int((cy - min(ys)) * rh * s)))
    return canvas


def render_sheet(kit: dict[str, Any], atlas: Image.Image, props: list[Image.Image], per_row: int = 5,
                 cell_w: int = 360) -> Image.Image:
    rooms = kit["rooms"]
    rw, rh = kit["cols"] * kit["tile"], kit["rows"] * kit["tile"]
    s = cell_w / rw
    ch = int(rh * s)
    n_rows = -(-len(rooms) // per_row)
    sheet = Image.new("RGBA", (per_row * (cell_w + 8) + 8, n_rows * (ch + 26) + 8), (12, 13, 18, 255))
    draw = ImageDraw.Draw(sheet)
    for i, room in enumerate(rooms):
        x, y = 8 + (i % per_row) * (cell_w + 8), 8 + (i // per_row) * (ch + 26)
        sheet.alpha_composite(_fit(render_room(kit, room, atlas, props, []), s, kit["tile"] <= 16), (x, y + 18))
        draw.text((x, y + 2), f"{i:02d} {room['kind']} {''.join(room['doors'])}", fill=(230, 230, 235, 255))
    return sheet


def _door_report(kit: dict[str, Any]) -> list[str]:
    bad = []
    for room in kit["rooms"]:
        grid = [list(r) for r in room["grid"]]
        if not doors_joined(kit["style"], grid, tuple(room["doors"]), kit["jump"]):
            bad.append(room["name"])
    return bad


def build_kit(blobs: Any, scene: dict[str, Any], autotile: dict[str, Any] | None, rules_raw: Any,
              params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Image.Image], list[tuple[str, str]],
                                                list[tuple[str, str]]]:
    style = scene["style"]
    want_style = str(params.get("style") or "auto")
    if want_style not in ("auto", *STYLES):
        raise StepError("op_bad_params", f"style must be auto, {', '.join(STYLES)}")
    if want_style not in ("auto", style):
        raise StepError("op_bad_params", f"style {want_style} does not match the scene's tileset ({style})")
    count = ps._int(params, "rooms", 20, 5, 64)
    cols = ps._int(params, "cols", 20, 12, 48) // 2 * 2
    rows = ps._int(params, "rows", 14, 10, 32) // 2 * 2
    seed = ps._int(params, "seed", 0, 0, 2**31 - 1)
    jump = ps._int(params, "jump", 3, 1, 5)
    map_rooms = ps._int(params, "map_rooms", 10, 3, 40)
    asset = ps._ident(str(params.get("asset") or "RoomKit"), "RoomKit")
    rules = normalize_rules(rules_raw, style)
    at = ps.Atlas(int(scene["tile"]))
    at.sets = {k: list(v) for k, v in scene["tileset"]["sets"].items()}
    need = ("floor", "path", "wall", "shadow") if style == "topdown" else ("wall", "top", "shadow")
    if not set(need) <= set(at.sets):
        raise StepError("op_bad_input", f"scene tileset lacks {sorted(set(need) - set(at.sets))} for style {style}")
    if autotile is not None and not (isinstance(autotile, dict) and isinstance(autotile.get("tiles"), list)):
        autotile = None
    kinds = scene.get("prop_kinds") or []
    kit: dict[str, Any] = {
        "type": KIT_TYPE, "asset": asset, "style": style, "tile": int(scene["tile"]), "cols": cols, "rows": rows,
        "seed": seed, "jump": jump, "tileset": {k: scene["tileset"][k] for k in ("hash", "count", "solid", "sets")},
        "prop_kinds": kinds, "rules": rules, "rooms": [], "warnings": []}
    for i, (kind, doors) in enumerate(plan_rooms(count, rules["kinds"], style, seed)):
        rule = rules["kinds"][kind]
        built = build_grid(style, cols, rows, kind, doors, rule, len(kinds), jump, (seed, i, kind))
        grid = built["grid"]
        layers, plugs = room_layers(style, grid, doors, at, autotile)
        t = kit["tile"]
        props = [{"kind": k, "cell": [x, y], "pos": [(x + 0.5) * t, (y + (1 if style == "sideview" else 0.85)) * t]}
                 for k, x, y in built["props"]]
        kit["rooms"].append({
            "name": f"Room{i:02d}_{kind.capitalize()}", "kind": kind, "doors": list(doors), "weight": rule["share"],
            "grid": ["".join(r) for r in grid], "spawn": list(built["spawn"]), "props": props, "layers": layers,
            "plugs": plugs, "door_cells": {s: [list(c) for c in door_cells(style, cols, rows, s)] for s in doors},
            "source": built["source"], "attempts": built["attempts"]})
        if built["source"] == "grammar":
            kit["warnings"].append(f"{kit['rooms'][-1]['name']}: WFC kept contradicting, used the grammar fill")
    layout = generate_layout(kit["rooms"], map_rooms, seed)
    sealed = sealed_doors(kit["rooms"], layout["cells"])
    kit["map"] = {"seed": seed, "count": len(layout["cells"]), "requested": map_rooms, "stopped": layout["stopped"],
                  "cells": [{"cell": list(c), "room": i, "sealed": sealed.get(c, [])}
                            for c, i in sorted(layout["cells"].items(), key=lambda kv: (kv[0][1], kv[0][0]))]}
    if layout["stopped"]:
        kit["warnings"].append(f"map: {layout['stopped']}")
    kit["qa"] = {"rooms": len(kit["rooms"]), "unjoined": _door_report(kit),
                 "by_kind": {k: sum(r["kind"] == k for r in kit["rooms"]) for k in KINDS}}
    with Image.open(blobs.path(scene["tileset"]["hash"])) as im:
        atlas = im.convert("RGBA")
    prop_imgs = []
    for k in kinds:
        with Image.open(blobs.path(k["hash"])) as im:
            prop_imgs.append(im.convert("RGBA"))
    images = {"map": render_map(kit, atlas, prop_imgs), "sheet": render_sheet(kit, atlas, prop_imgs)}
    tmx = [(f"{r['name']}.tmx", tmx_with_doors(kit, r)) for r in kit["rooms"]]
    ldtk = [(f"{r['name']}.ldtk", ldtk_with_doors(kit, r)) for r in kit["rooms"]]
    return kit, images, tmx, ldtk


class RoomKit(OpExecutor):
    kind = "op.room_kit"
    version = "1"

    def cache_material(self, params: dict[str, Any]) -> str:
        return super().cache_material(params) + hashlib.sha256(Path(wfc.__file__).read_bytes()).hexdigest()

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        scene = await ps.read_doc(ctx, inputs.get("scene") or [], ps.SCENE_TYPE, "scene")
        if not isinstance((scene.get("tileset") or {}).get("hash"), str):
            raise StepError("op_bad_input", "the scene document has no tile atlas hash")
        autotile = rules = None
        for ref in inputs.get("autotile") or []:
            autotile = await _read_json(ctx, ref)
            break
        for ref in inputs.get("rules") or []:
            doc = await _read_json(ctx, ref)
            rules = doc if isinstance(doc, dict) and doc.get("type") == RULES_TYPE else None
            break
        try:
            kit, images, tmx, ldtk = await asyncio.to_thread(build_kit, ctx.blobs, scene, autotile, rules, params)
        except wfc.WfcError as e:
            raise StepError("op_room_failed", str(e)) from e
        ctx.progress(0.9)
        map_ref = await _put_png(ctx, images["map"], f"{kit['asset']}_map.png")
        sheet_ref = await _put_png(ctx, images["sheet"], f"{kit['asset']}_rooms.png")
        prev = images["map"].copy()
        prev.thumbnail((1600, 1600))
        kit_ref = await ctx.blobs.put_bytes(json.dumps(kit, indent=1, ensure_ascii=False).encode("utf-8"),
                                            name=KIT_FILE, mime="application/json")
        tmx_refs = [await ctx.blobs.put_bytes(text.encode("utf-8"), name=name, mime="application/xml")
                    for name, text in tmx]
        ldtk_refs = [await ctx.blobs.put_bytes(text.encode("utf-8"), name=name, mime="application/json")
                     for name, text in ldtk]
        ctx.progress(1.0)
        return StepResult(outputs={"images": [map_ref, sheet_ref], "preview": [await _put_png(ctx, prev, "preview.png")],
                                   "rooms": [kit_ref], "tmx": tmx_refs, "ldtk": ldtk_refs},
                          meta={"style": kit["style"], "rooms": len(kit["rooms"]), "grid": [kit["cols"], kit["rows"]],
                                "rules": kit["rules"]["source"], "map_rooms": kit["map"]["count"],
                                "by_kind": kit["qa"]["by_kind"], "warnings": kit["warnings"]})


ROOM_KIT_OPS = (RoomKit,)
