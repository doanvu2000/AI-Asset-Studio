"""Modular kit layout math, shared by the core (params, checks, engine export) and Blender (modular_bpy imports it).

Blender coordinates, Z up, metres. Every piece has its pivot at the cell's bottom-left corner (x = 0, y = 0, z = 0)
and fills the cell from there: a wall runs along +X from 0 to grid, its outside face at y = 0 and its inside at
y = thickness, so four walls rotated about Z at the corners of a room enclose it from the inside."""
import math

PIECES = ("wall", "wall_window", "wall_door", "corner_out", "corner_in", "floor", "ceiling", "stair", "pillar",
          "railing")
GRIDS = (1, 2, 4)
TOL = 0.0005
MAX_TRIS = 2000


def storey_cells(grid, storey=0):
    """0 = auto: the smallest whole number of cells that is at least 3 m tall (a usable door + head room)."""
    s = int(storey or 0)
    return s if s > 0 else max(1, math.ceil(3.0 / grid - 1e-9))


def kit_dims(grid, storey=0):
    g = float(grid)
    h = g * storey_cells(grid, storey)
    t = min(0.35, max(0.15, 0.1 * g))
    door_w = min(1.2, 0.55 * g)
    door_h = min(2.3, h - 0.4)
    win_w = min(1.4, 0.5 * g)
    win_h = min(1.4, 0.4 * h)
    rise = 0.2
    steps = max(4, round(h / rise))
    run = 0.28
    return {"grid": g, "storey": h, "cells": round(h / g), "thickness": t, "inset": 0.02,
            "base_h": 0.12, "cap_h": 0.1, "floor_t": 0.1, "frame": 0.08,
            "door": [round(door_w, 4), round(door_h, 4)],
            "window": [round(win_w, 4), round(win_h, 4), round(min(0.9, 0.3 * h), 4)],
            "pillar": round(min(0.6, max(0.25, 0.15 * g)), 4), "rail_h": 1.0, "rail_w": 0.08,
            "steps": steps, "stair_cells": max(1, math.ceil(steps * run / g - 1e-9))}


def expected_bounds(piece, d):
    """-> (lo, hi, snapped axes): the axes whose length must be a whole number of grid cells."""
    g, h, t = d["grid"], d["storey"], d["thickness"]
    table = {
        "wall": ([0, 0, 0], [g, t, h], (0, 2)),
        "wall_window": ([0, 0, 0], [g, t, h], (0, 2)),
        "wall_door": ([0, 0, 0], [g, t, h], (0, 2)),
        "corner_out": ([0, 0, 0], [g, g, h], (0, 1, 2)),
        "corner_in": ([0, 0, 0], [g, g, h], (0, 1, 2)),
        "floor": ([0, 0, 0], [g, g, d["floor_t"]], (0, 1)),
        "ceiling": ([0, 0, h - d["floor_t"]], [g, g, h], (0, 1)),
        "stair": ([0, 0, 0], [g, g * d["stair_cells"], h], (0, 1, 2)),
        "pillar": ([0, 0, 0], [d["pillar"], d["pillar"], h], (2,)),
        "railing": ([0, 0, 0], [g, d["rail_w"], d["rail_h"]], (0,)),
    }
    return table[piece]


def normalize_params(p):
    grid = float(p.get("grid", 2) or 2)
    if grid not in GRIDS:
        raise ValueError(f"grid must be one of {GRIDS} m")
    storey = int(p.get("storey", 0) or 0)
    if not 0 <= storey <= 4:
        raise ValueError("storey must be 0 (auto) .. 4 cells")
    raw = p.get("pieces") or "all"
    if isinstance(raw, (list, tuple)):
        names = [str(s).strip() for s in raw]
    else:
        names = list(PIECES) if raw == "all" else [s.strip() for s in str(raw).replace(";", ",").split(",") if s.strip()]
    bad = [n for n in names if n not in PIECES]
    if bad or not names:
        raise ValueError(f"unknown pieces {bad}; use 'all' or a comma list of {', '.join(PIECES)}")
    max_tris = int(p.get("max_tris", MAX_TRIS) or MAX_TRIS)
    if max_tris < 200:
        raise ValueError("max_tris must be at least 200")
    bevel = float(p.get("bevel", 0.015))
    if not 0 <= bevel <= 0.05:
        raise ValueError("bevel must be 0..0.05 m")
    return {"grid": int(grid), "storey": storey, "pieces": [n for n in PIECES if n in names], "max_tris": max_tris,
            "bevel": bevel}


def check_bounds(lo, hi, piece, d, tol=TOL):
    elo, ehi, snapped = expected_bounds(piece, d)
    err = max(max(abs(a - b) for a, b in zip(lo, elo)), max(abs(a - b) for a, b in zip(hi, ehi)))
    g = d["grid"]
    multiple = all(abs((hi[i] - lo[i]) / g - round((hi[i] - lo[i]) / g)) * g <= tol
                   and round((hi[i] - lo[i]) / g) >= 1 for i in snapped)
    return {"expected_min": [round(v, 4) for v in elo], "expected_max": [round(v, 4) for v in ehi],
            "error_m": round(err, 6), "ok": err <= tol and multiple, "grid_multiple": multiple}


def end_profiles(verts, length, axis=0, tol=TOL):
    """Vertices on the piece's two ends along `axis`, as sorted sets of the other two coordinates (rounded to tol).
    Two copies placed end to end close without a gap when both sets are equal."""
    others = [i for i in range(3) if i != axis]
    q = 1.0 / tol

    def key(v):
        return tuple(round(v[i] * q) for i in others)

    lo = sorted({key(v) for v in verts if abs(v[axis]) <= tol})
    hi = sorted({key(v) for v in verts if abs(v[axis] - length) <= tol})
    return lo, hi


def ends_match(verts, length, axis=0, tol=TOL):
    lo, hi = end_profiles(verts, length, axis, tol)
    return {"start": len(lo), "end": len(hi), "ok": bool(lo) and lo == hi}


def sample_room(d, pieces):
    """Placements of a small room (4×2 cells) using every piece that was built: corners, walls with a door and
    a window, floor, a pillar, and a stair with railing outside. rot is degrees about +Z; pos is the pivot, always
    on a grid point so a GridMap can hold the same room."""
    g = d["grid"]
    nx, ny = 4, 2
    have = set(pieces)
    out = []

    def put(piece, x, y, z=0.0, rot=0):
        if piece in have:
            out.append({"piece": piece, "pos": [round(x, 4), round(y, 4), round(z, 4)], "rot": rot % 360})

    for i in range(nx):
        for j in range(ny):
            put("floor", i * g, j * g)
    corner = "corner_out" in have
    south = ["wall", "wall_door", "wall", "wall"]
    north = ["wall", "wall_window", "wall", "wall"]
    for i in range(nx):
        if corner and i in (0, nx - 1):
            continue
        put(south[i] if south[i] in have else "wall", i * g, 0, rot=0)
        put(north[i] if north[i] in have else "wall", (i + 1) * g, ny * g, rot=180)
    for j in range(ny):
        if corner and j in (0, ny - 1) and ny > 1:
            continue
        put("wall_window" if "wall_window" in have else "wall", nx * g, j * g, rot=90)
        put("wall", 0, (j + 1) * g, rot=270)
    if corner:
        put("corner_out", 0, 0, rot=0)
        put("corner_out", nx * g, 0, rot=90)
        put("corner_out", nx * g, ny * g, rot=180)
        put("corner_out", 0, ny * g, rot=270)
    put("pillar", g, g)
    sx = (nx + 1) * g
    put("stair", sx, 0)
    put("railing", sx + g, 0, rot=90)
    put("corner_in", -2 * g, 0)
    return out


def rot_z(p, deg):
    c, s = round(math.cos(math.radians(deg)), 12), round(math.sin(math.radians(deg)), 12)
    return [p[0] * c - p[1] * s, p[0] * s + p[1] * c, p[2]]


def to_godot(p):
    """Blender (x, y, z) -> glTF / Godot (x, z, -y)."""
    return [p[0], p[2], -p[1]]


def to_unity(p):
    """Blender (x, y, z) -> Unity, for FBX exported -Z forward / Y up with the transform baked: Unity mirrors X on
    import, so (-x, z, -y)."""
    return [-p[0], p[2], -p[1]]
