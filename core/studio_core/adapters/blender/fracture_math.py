"""Parameter rules, Voronoi seed placement and mass/budget maths for blender.fracture; numpy + stdlib only because
Blender's own Python imports this file next to fracture_bpy.py."""
from __future__ import annotations

import re
from typing import Any

import numpy as np

PATTERNS = ("uniform", "impact", "planks")
# density kg/m³, interior roughness, interior metallic
MATERIALS: dict[str, tuple[float, float, float]] = {
    "wood": (600.0, 0.85, 0.0), "stone": (2400.0, 0.9, 0.0), "ceramic": (2000.0, 0.55, 0.0),
    "metal": (7800.0, 0.45, 1.0), "glass": (2500.0, 0.08, 0.0), "concrete": (2300.0, 0.95, 0.0),
    "ice": (917.0, 0.15, 0.0), "plastic": (950.0, 0.5, 0.0)}
MIN_PIECES, MAX_PIECES = 8, 40
# Bbox-relative (x, y, z) in 0..1, Blender axes (Z up, -Y is the front a default camera looks at).
IMPACT_PRESETS = {"top": (0.5, 0.5, 1.0), "front": (0.5, 0.0, 0.55), "corner": (1.0, 0.0, 1.0),
                  "side": (1.0, 0.5, 0.55), "bottom": (0.5, 0.5, 0.0), "center": (0.5, 0.5, 0.5)}
GRAINS = ("auto", "x", "y", "z")
# Planks: Voronoi cells measured with the grain axis shrunk this much come out as long splinters along it.
PLANK_STRETCH = 4.0
# Caps add roughly this share of triangles on top of the pre-cut mesh, so the source is decimated below the budget.
CAP_SHARE = 0.2
MAX_HULL_POINTS = 64
_NUM = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$")


def parse_impact(spec: Any) -> tuple[float, float, float]:
    if isinstance(spec, (list, tuple)) and len(spec) == 3:
        v = tuple(float(x) for x in spec)
    else:
        s = str(spec if spec is not None else "top").strip().lower()
        if s in IMPACT_PRESETS:
            return IMPACT_PRESETS[s]
        m = _NUM.match(s)
        if not m:
            raise ValueError(f"impact must be {'|'.join(IMPACT_PRESETS)} or 'x,y,z' in 0..1, got {spec!r}")
        v = tuple(float(g) for g in m.groups())
    if not all(0.0 <= x <= 1.0 for x in v):
        raise ValueError(f"impact coordinates are bbox-relative 0..1, got {v}")
    return v  # type: ignore[return-value]


def normalize_params(p: dict[str, Any]) -> dict[str, Any]:
    pieces = int(p.get("pieces", 16))
    if not MIN_PIECES <= pieces <= MAX_PIECES:
        raise ValueError(f"pieces must be {MIN_PIECES}..{MAX_PIECES}, got {pieces}")
    pattern = str(p.get("pattern") or "uniform").strip().lower()
    if pattern not in PATTERNS:
        raise ValueError(f"pattern must be one of {PATTERNS}, got {pattern!r}")
    material = str(p.get("material") or "wood").strip().lower()
    if material not in MATERIALS:
        raise ValueError(f"material must be one of {sorted(MATERIALS)}, got {material!r}")
    density = float(p.get("density") or 0) or MATERIALS[material][0]
    if density <= 0:
        raise ValueError("density must be > 0")
    grain = str(p.get("grain") or "auto").strip().lower()
    if grain not in GRAINS:
        raise ValueError(f"grain must be one of {GRAINS}, got {grain!r}")
    max_tris = int(p.get("max_tris", 20000))
    if not 500 <= max_tris <= 500000:
        raise ValueError(f"max_tris must be 500..500000, got {max_tris}")
    explode = float(p.get("explode", 0.6))
    if not 0 <= explode <= 3:
        raise ValueError("explode must be 0..3")
    cut_scale = float(p.get("cut_scale", 0.5))
    if cut_scale <= 0:
        raise ValueError("cut_scale must be > 0")
    return {"pieces": pieces, "pattern": pattern, "material": material, "density": density,
            "roughness": MATERIALS[material][1], "metallic": MATERIALS[material][2],
            "impact": list(parse_impact(p.get("impact", "top"))), "grain": grain, "max_tris": max_tris,
            "seed": int(p.get("seed", 0)), "explode": explode, "cut_scale": cut_scale,
            "remesh": str(p.get("remesh") or "auto").strip().lower()}


def grain_axis(grain: str, size: np.ndarray) -> int:
    return int(np.argmax(size)) if grain == "auto" else "xyz".index(grain)


def metric(pattern: str, axis: int) -> np.ndarray:
    m = np.ones(3)
    if pattern == "planks":
        m[axis] = 1.0 / PLANK_STRETCH
    return m


def farthest_points(pts: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """Indices of n points, each the farthest from those already taken."""
    idx = [int(rng.integers(len(pts)))]
    d = np.linalg.norm(pts - pts[idx[0]], axis=1)
    for _ in range(1, min(n, len(pts))):
        i = int(np.argmax(d))
        idx.append(i)
        d = np.minimum(d, np.linalg.norm(pts - pts[i], axis=1))
    return np.array(idx)


def kmeans(pts: np.ndarray, n: int, rng: np.random.Generator, iters: int = 12) -> np.ndarray:
    """Centroids of the interior samples: spread evenly through the volume (farthest-point seeds alone crowd the
    surface and leave thin slivers there)."""
    c = pts[farthest_points(pts, n, rng)].copy()
    for _ in range(iters):
        lab = np.argmin(((pts[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
        for k in range(len(c)):
            m = pts[lab == k]
            if len(m):
                c[k] = m.mean(0)
    return c


def place_seeds(inside: np.ndarray, n: int, pattern: str, seed: int, impact: np.ndarray | None = None,
                scale: np.ndarray | None = None) -> np.ndarray:
    """`inside` = random points inside the mesh. Seeds are computed in the metric space `inside * scale` (planks
    stretch cells along the grain) and returned in world space."""
    if len(inside) < n:
        raise ValueError(f"only {len(inside)} interior samples for {n} pieces (mesh too thin or not closed)")
    rng = np.random.default_rng(seed)
    s = np.ones(3) if scale is None else np.asarray(scale, dtype=float)
    pts = inside * s
    if pattern == "impact" and impact is not None:
        # A few far seeds keep the side away from the hit in big chunks; the rest crowd the impact point.
        ip = np.asarray(impact, dtype=float) * s
        d = np.linalg.norm(pts - ip, axis=1)
        w = np.exp(-10.0 * d / max(float(d.max()), 1e-9))
        take = list(farthest_points(pts, max(2, n // 5), rng))
        gap = 0.03 * float(np.linalg.norm(pts.max(0) - pts.min(0)))
        for i in rng.choice(len(pts), size=len(pts), replace=False, p=w / w.sum()):
            if len(take) >= n:
                break
            if np.linalg.norm(pts[take] - pts[i], axis=1).min() > gap:
                take.append(int(i))
        out = pts[take] if len(take) >= n else pts[farthest_points(pts, n, rng)]
    else:
        # A centroid can fall outside a concave mesh (and its cell come out empty); the nearest interior sample can't.
        c = kmeans(pts, n, rng)
        out = pts[np.unique(np.argmin(((c[:, None, :] - pts[None]) ** 2).sum(-1), axis=1))]
        if len(out) < n:
            out = np.concatenate([out, pts[farthest_points(pts, n, rng)]])[:n]
    return out / s


def bisector(a: np.ndarray, b: np.ndarray, scale: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Plane (point, unit normal toward b) halfway between seeds a and b in the metric `scale`, in world space."""
    sa, sb = a * scale, b * scale
    co = (sa + sb) / 2 / scale
    no = (sb - sa) * scale
    return co, no / np.linalg.norm(no)


def cut_order(seeds: np.ndarray, i: int, scale: np.ndarray) -> list[int]:
    """Nearest neighbours first: their planes remove most of the mesh, so later bisects touch little geometry."""
    d = np.linalg.norm((seeds - seeds[i]) * scale, axis=1)
    return [int(j) for j in np.argsort(d) if j != i]


def pre_cut_target(source_tris: int, max_tris: int) -> int | None:
    target = int(max_tris * (1 - CAP_SHARE))
    return target if source_tris > target else None


def next_target(target: int, tris: int, cap_tris: int, max_tris: int) -> int | None:
    """Smaller pre-cut target when the pieces came out over budget: surface triangles scale with the target, caps
    roughly stay, so solve for the surface share that fits. None = nothing left to gain."""
    surface = tris - cap_tris
    if tris <= max_tris or surface <= 0:
        return None
    room = max(max_tris - cap_tris, max_tris * 0.25)
    new = int(target * room / surface * 0.95)
    return new if 200 <= new < target * 0.98 else None


def masses(volumes: list[float], density: float) -> list[float]:
    return [round(max(v, 0.0) * density, 4) for v in volumes]


def volume_error(total: float, pieces: list[float]) -> float:
    return abs(sum(pieces) - total) / total if total > 0 else 1.0


def exploded_offset(center: np.ndarray, middle: np.ndarray, size: float, factor: float) -> np.ndarray:
    """Pieces fly away from the middle in proportion to their distance, plus a lift so the bottom row clears the floor."""
    d = (np.asarray(center) - np.asarray(middle)) * factor
    return d + np.array([0.0, 0.0, size * factor * 0.15])


def hull_subset(points: np.ndarray, limit: int = MAX_HULL_POINTS) -> np.ndarray:
    if len(points) <= limit:
        return points
    return points[farthest_points(points, limit, np.random.default_rng(0))]


def chunk_name(asset: str, i: int) -> str:
    return f"{asset}_chunk{i:02d}"
