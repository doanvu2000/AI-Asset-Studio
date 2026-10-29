"""Trim sheet UV math (numpy only), shared by the core tests and Blender (modular_bpy, trim helper).

A face is mapped into one horizontal strip of the sheet by name: V runs across the strip (bottom to top of the face,
or across its short side for trims), U runs along the face in metres at the strip's texel density, snapped so a
grid cell holds a whole number of the strip's repeats and two pieces placed side by side continue the pattern."""
import numpy as np

ALIASES = {
    "wall": ("wall", "panel", "brick", "stone", "planks", "plaster"),
    "floor": ("floor", "tiles", "planks", "stone", "panel"),
    "pillar": ("pillar", "column", "stone", "brick", "panel"),
    "cap": ("cap", "molding", "cornice", "trim", "frame"),
    "base": ("base", "baseboard", "skirting", "molding", "trim"),
    "frame": ("frame", "molding", "trim", "planks"),
    "step": ("step", "edge", "frame", "planks", "stone"),
    "rail": ("rail", "metal", "frame", "pipe"),
    "metal": ("metal", "bolts", "pipe", "rail"),
    "dark": ("dark", "groove", "seam", "edge"),
}
# Parts that cover large areas take the tallest unclaimed strip when no name or alias matches; trims the shortest.
LARGE = ("wall", "floor", "pillar")


def resolve_strip(trim, part):
    strips = trim["strips"]
    by_name = {s["name"]: s for s in strips}
    for n in ALIASES.get(part, (part,)):
        if n in by_name:
            return by_name[n]
    order = sorted(strips, key=lambda s: (-s["px"], s["y0"]) if part in LARGE else (s["px"], s["y0"]))
    return order[0]


def strip_v(strip, safe=True):
    return (strip["v0_safe"], strip["v1_safe"]) if safe else (strip["v0"], strip["v1"])


def face_axes(normal):
    """In-plane (u, v) for a face: vertical faces get u horizontal (to the right seen from outside) and v up,
    horizontal faces u = +X and v = +Y."""
    n = np.asarray(normal, float)
    n = n / max(np.linalg.norm(n), 1e-12)
    if abs(n[2]) < 0.7:
        u = np.cross([0.0, 0.0, 1.0], n)
        u /= max(np.linalg.norm(u), 1e-12)
        return u, np.array([0.0, 0.0, 1.0])
    return np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])


def u_scale(px_per_m, cell_m, repeats, sheet_px=2048):
    """U per metre: the strip's density, snapped so `cell_m` holds a whole number (>= 1) of the strip's repeats
    (1/repeats of the sheet width each)."""
    raw = px_per_m * cell_m / sheet_px
    k = max(1, int(repeats or 1))
    n = max(1, round(raw * k))
    return n / k / cell_m


def face_uv(points, normal, strip, *, span=None, along_long=False, cell_m=2.0, sheet_px=2048, safe=True):
    """UVs (n, 2) of one face's corners. `span` = (lo, hi) of the face's v coordinate in metres to stretch across
    the strip (a whole wall height), else the face's own extent. `along_long` turns the axes so u runs along the
    face's longer side (rails, posts, frames)."""
    pts = np.asarray(points, float)
    u_ax, v_ax = face_axes(normal)
    us, vs = pts @ u_ax, pts @ v_ax
    if along_long and span is None and (us.max() - us.min()) < (vs.max() - vs.min()):
        u_ax, v_ax, us, vs = v_ax, -u_ax, vs, -(pts @ u_ax)
    lo, hi = span if span is not None else (float(vs.min()), float(vs.max()))
    extent = max(hi - lo, 1e-6)
    v0, v1 = strip_v(strip, safe)
    px_per_m = strip["px"] / extent
    su = u_scale(px_per_m, cell_m, strip.get("repeats", 1), sheet_px)
    u = us * su
    v = v0 + np.clip((vs - lo) / extent, 0, 1) * (v1 - v0)
    return np.stack([u, v], 1)


def uv_in_strip(uvs, strip, tol=1e-4):
    v = np.asarray(uvs, float)[:, 1]
    return bool(len(v)) and float(v.min()) >= strip["v0"] - tol and float(v.max()) <= strip["v1"] + tol

