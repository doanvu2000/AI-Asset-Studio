"""3D terrain (#47): a region plan (from `claude.terrain_plan`) -> 16-bit heightmap (fBm + domain warp, light
stream-power erosion, rivers carved along splines) + 4-layer splat, then the layer textures and an optional skybox are
packed into one terrain.json for the Unity / Godot exporters.

Grid convention: row 0 = north (plan y = 0), column 0 = west; heights are 0..1 of `height_m` metres."""
from __future__ import annotations

import asyncio
import io
import json
import math
import zlib
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png

TERRAIN_FILE = "terrain.json"
TERRAIN_TYPE = "asset-studio/terrain"
HEIGHT_FILE = "terrain_height.json"
HEIGHT_TYPE = "asset-studio/terrain-height"
PLAN_TYPE = "asset-studio/terrain-plan"
REGION_TYPES = ("mountains", "hills", "plateau", "plain", "valley", "lake", "sea")
# Splat channel order R, G, B, A.
ROLES = ("ground", "shore", "cliff", "peak")
ROLE_COLORS = {"ground": (96, 128, 64), "shore": (196, 178, 128), "cliff": (120, 116, 110), "peak": (236, 238, 242)}
DEFAULT_TILE_M = {"ground": 10.0, "shore": 8.0, "cliff": 16.0, "peak": 12.0}
SIZES = (129, 257, 513, 1025, 2049, 4097)
ERODE_RES = 513
# Slope (rise over run) where rock starts / fully takes over: tan 30° and tan 42°, roughly where soil stops holding.
CLIFF_SLOPE = (0.58, 0.9)


@dataclass
class HeightParams:
    size: int = 1025
    world_m: float = 1024.0
    height_m: float = 200.0
    erosion: float = 0.5
    seed: int = 0
    asset: str = "terrain"

    @classmethod
    def parse(cls, p: dict[str, Any]) -> HeightParams:
        from .enemy_variants import slug

        try:
            out = cls(size=int(p.get("size") or 1025), world_m=float(p.get("world") or p.get("world_m") or 1024),
                      height_m=float(p.get("height") or p.get("height_m") or 200),
                      erosion=float(p.get("erosion", 0.5)), seed=int(p.get("seed") or 0),
                      asset=slug(str(p.get("asset") or "terrain")) or "terrain")
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", f"bad terrain parameter: {e}") from e
        if out.size not in SIZES:
            raise StepError("op_bad_params", f"size must be one of {SIZES} (2^n + 1, the Unity heightmap rule)")
        if not 16 <= out.world_m <= 16384 or not 1 <= out.height_m <= 8000 or not 0 <= out.erosion <= 1:
            raise StepError("op_bad_params", "world 16..16384 m, height 1..8000 m, erosion 0..1")
        return out


def _smooth(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def _resize(a: np.ndarray, n: int, resample: Image.Resampling = Image.Resampling.BICUBIC) -> np.ndarray:
    return np.asarray(Image.fromarray(a.astype(np.float32), "F").resize((n, n), resample), np.float32)


def value_noise(n: int, freq: int, rng: np.random.Generator) -> np.ndarray:
    """Smooth 0..1 noise: a random lattice upsampled bicubic, cropped so the lattice border never shows."""
    lat = rng.random((freq + 4, freq + 4)).astype(np.float32)
    cell = n / freq
    big = _resize(lat, int(round(cell * (freq + 4))))
    o = int(round(cell * 2))
    return np.clip(big[o:o + n, o:o + n], 0, 1)


def fbm(n: int, freq: int, octaves: int, rng: np.random.Generator, gain: float = 0.5) -> np.ndarray:
    """-1..1 fractal noise; octaves stop at ~4 px wavelength."""
    out = np.zeros((n, n), np.float32)
    amp, total = 1.0, 0.0
    for _ in range(octaves):
        if freq > n / 4:
            break
        out += amp * (value_noise(n, freq, rng) * 2 - 1)
        total += amp
        amp *= gain
        freq *= 2
    return out / max(total, 1e-6)


def ridged(n: int, freq: int, octaves: int, rng: np.random.Generator) -> np.ndarray:
    """0..1 ridges: sharp crests where the noise crosses its midpoint."""
    out = np.zeros((n, n), np.float32)
    amp, total = 1.0, 0.0
    for _ in range(octaves):
        if freq > n / 4:
            break
        r = 1 - np.abs(value_noise(n, freq, rng) * 2 - 1)
        out += amp * r * r
        total += amp
        amp *= 0.5
        freq *= 2
    return out / max(total, 1e-6)


def sample(field: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Bilinear lookup at 0..1 coordinates (u = column, v = row), clamped at the border."""
    n = field.shape[0]
    x = np.clip(u * (n - 1), 0, n - 1.001)
    y = np.clip(v * (n - 1), 0, n - 1.001)
    x0, y0 = x.astype(np.int32), y.astype(np.int32)
    fx, fy = x - x0, y - y0
    a, b = field[y0, x0], field[y0, x0 + 1]
    c, d = field[y0 + 1, x0], field[y0 + 1, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _f(v: Any, default: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(x) else min(hi, max(lo, x))


def clean_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Clamp every number of a (possibly hand-written) plan; unknown region types are dropped."""
    regions = []
    for r in plan.get("regions") or []:
        if not isinstance(r, dict) or str(r.get("type")) not in REGION_TYPES:
            continue
        regions.append({"type": str(r["type"]), "label": str(r.get("label") or r["type"])[:40],
                        "x": _f(r.get("x"), 0.5, -0.2, 1.2), "y": _f(r.get("y"), 0.5, -0.2, 1.2),
                        "rx": _f(r.get("rx"), 0.2, 0.02, 1.5), "ry": _f(r.get("ry", r.get("rx")), 0.2, 0.02, 1.5),
                        "angle": _f(r.get("angle"), 0, -360, 360), "height": _f(r.get("height"), 0.3, 0, 1)})
    rivers = []
    for r in plan.get("rivers") or []:
        pts = [(_f(p[0], 0.5, -0.1, 1.1), _f(p[1], 0.5, -0.1, 1.1)) for p in (r or {}).get("points") or []
               if isinstance(p, (list, tuple)) and len(p) >= 2]
        if len(pts) >= 2:
            rivers.append({"points": [list(p) for p in pts], "width": _f(r.get("width"), 0.008, 0.002, 0.04),
                           "depth": _f(r.get("depth"), 0.025, 0.005, 0.2)})
    wl = plan.get("water_level")
    has_water = any(r["type"] in ("lake", "sea") for r in regions) or bool(rivers)
    water = _f(wl, 0.12, 0, 0.8) if has_water and wl is not None and _f(wl, -1, -1, 1) >= 0 else (
        0.12 if has_water else None)
    return {"regions": regions, "rivers": rivers, "base": _f(plan.get("base"), 0.18, 0, 0.8), "water_level": water,
            "snowline": _f(plan.get("snowline"), 0.8, 0.3, 1.2)}


def _region_masks(r: dict[str, Any], u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """0..1 elliptical distance (0 = centre, 1 = edge, >1 outside) in warped coordinates."""
    a = math.radians(r["angle"])
    dx, dy = u - r["x"], v - r["y"]
    px = dx * math.cos(a) + dy * math.sin(a)
    py = -dx * math.sin(a) + dy * math.cos(a)
    return np.sqrt((px / r["rx"]) ** 2 + (py / r["ry"]) ** 2)


ORDER = {"mountains": 0, "hills": 0, "valley": 1, "plateau": 2, "plain": 3, "lake": 4, "sea": 4}


def compose(plan: dict[str, Any], n: int, rng: np.random.Generator) -> np.ndarray:
    """Relief before erosion, in plan units (0 = sea floor-ish, 1 = tallest peak)."""
    vv, uu = np.mgrid[0:n, 0:n].astype(np.float32) / (n - 1)
    wx, wy = fbm(n, 3, 4, rng), fbm(n, 3, 4, rng)
    # Domain warp: region outlines and ridges bend like real coasts and ranges instead of perfect ellipses.
    uw, vw = uu + 0.07 * wx, vv + 0.07 * wy
    ridge = ridged(n, 5, 5, rng)
    rough = fbm(n, 8, 7, rng) * 0.5 + 0.5
    edge = fbm(n, 12, 4, rng) * 0.12
    h = plan["base"] + 0.035 * fbm(n, 4, 6, rng)
    wl = plan["water_level"]
    for r in sorted(plan["regions"], key=lambda r: ORDER[r["type"]]):
        d = _region_masks(r, uw, vw) + edge
        t, top = r["type"], r["height"]
        if t == "mountains":
            f = _smooth(1.0, 0.0, d) ** 1.3
            h = h + top * f * (0.3 + 0.7 * ridge)
        elif t == "hills":
            f = _smooth(1.0, 0.0, d)
            h = h + top * f * (0.45 + 0.55 * rough)
        elif t == "valley":
            h = h - top * _smooth(1.0, 0.0, d) * 0.8
        elif t == "plateau":
            f = _smooth(1.0, 0.7, d)
            h = h * (1 - f) + np.maximum(h, plan["base"] + top + 0.02 * rough) * f
        elif t == "plain":
            f = _smooth(1.0, 0.3, d) * 0.85
            h = h * (1 - f) + (plan["base"] + top * 0.3 + 0.015 * rough) * f
        else:
            level = wl if wl is not None else plan["base"]
            depth = 0.05 + 0.25 * max(top, 0.1)
            bowl = level - depth * _smooth(1.0, 0.0, d) ** 0.7 - 0.01
            f = _smooth(1.0, 0.55, d)
            h = h * (1 - f) + np.minimum(h, bowl) * f
    return h.astype(np.float32)


def _receivers(h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """D8 steepest-descent neighbour (flat index, self for pits) and the drop per cell length."""
    n = h.shape[0]
    pad = np.pad(h, 1, mode="edge")
    idx = np.arange(n * n).reshape(n, n)
    best = np.zeros_like(h)
    tgt = idx.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nb = pad[1 + dy:1 + dy + n, 1 + dx:1 + dx + n]
            drop = (h - nb) / (1.4142 if dx and dy else 1.0)
            better = drop > best
            best = np.where(better, drop, best)
            ny = np.clip(np.arange(n)[:, None] + dy, 0, n - 1)
            nx = np.clip(np.arange(n)[None, :] + dx, 0, n - 1)
            tgt = np.where(better, ny * n + nx, tgt)
    return tgt.ravel(), best.ravel()


def flow_accumulation(h: np.ndarray, tgt: np.ndarray) -> np.ndarray:
    """Upstream cell count of every cell (D8), highest cells first."""
    acc = np.ones(h.size, np.float64)
    order = np.argsort(-h.ravel(), kind="stable")
    t = tgt[order]
    for i, j in zip(order.tolist(), t.tolist()):
        if i != j:
            acc[j] += acc[i]
    return acc.reshape(h.shape)


def erode(h: np.ndarray, strength: float, iterations: int = 5) -> np.ndarray:
    """Light stream-power incision (E ~ k·A^0.5·S) with a little deposition and talus slumping: carves dendritic
    gullies into slopes without flattening the relief."""
    if strength <= 0:
        return h
    from .tile import soft_blur

    h = h.astype(np.float64).copy()
    n = h.shape[0]
    cell = 1.0 / (n - 1)
    for _ in range(iterations):
        tgt, drop = _receivers(h)
        acc = flow_accumulation(h, tgt)
        slope = drop.reshape(h.shape) / cell
        e = 0.0012 * strength * np.sqrt(acc) * np.minimum(slope, 3.0)
        # Never dig below the receiver: that would turn every gully into a chain of pits.
        e = np.minimum(e, 0.45 * drop.reshape(h.shape))
        h -= e
        flat = _smooth(0.15, 0.0, slope)
        h += (soft_blur(h, 2, wrap=False) - h) * flat * 0.5 * strength
        talus = 1.2 * cell
        tgt, drop = _receivers(h)
        move = np.clip(drop - talus, 0, None) * 0.25
        h -= move.reshape(h.shape)
        h += np.bincount(tgt, weights=move, minlength=h.size).reshape(h.shape)
    return h.astype(np.float32)


def _catmull(points: list[list[float]], per: int = 24) -> np.ndarray:
    p = np.asarray(points, np.float64)
    p = np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    out = []
    for i in range(1, len(p) - 2):
        t = np.linspace(0, 1, per, endpoint=False)[:, None]
        a, b, c, d = p[i - 1], p[i], p[i + 1], p[i + 2]
        out.append(0.5 * (2 * b + (c - a) * t + (2 * a - 5 * b + 4 * c - d) * t ** 2
                          + (3 * b - a - 3 * c + d) * t ** 3))
    out.append(p[-2][None])
    return np.vstack(out)


def river_field(river: dict[str, Any], h: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Distance (0..1 map units) to the river's spline and the bed height along it, which only ever goes down."""
    n = h.shape[0]
    pts = _catmull(river["points"])
    # Small meanders: the planner draws rivers with a handful of points.
    seg = np.diff(pts, axis=0)
    normal = np.stack([-seg[:, 1], seg[:, 0]], 1)
    normal = np.vstack([normal, normal[-1:]])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    wobble = np.convolve(rng.standard_normal(len(pts)), np.ones(9) / 9, mode="same") * 0.012
    wobble[0] = wobble[-1] = 0
    pts = pts + normal * wobble[:, None]
    along = sample(h, pts[:, 0], pts[:, 1])
    bed = np.minimum.accumulate(along) - river["depth"]
    vv, uu = np.mgrid[0:n, 0:n].astype(np.float32) / (n - 1)
    best = np.full((n, n), np.inf, np.float32)
    best_bed = np.zeros((n, n), np.float32)
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        ab = b - a
        l2 = float(ab @ ab) or 1e-12
        t = np.clip(((uu - a[0]) * ab[0] + (vv - a[1]) * ab[1]) / l2, 0, 1)
        d = np.hypot(uu - (a[0] + t * ab[0]), vv - (a[1] + t * ab[1]))
        closer = d < best
        best = np.where(closer, d, best)
        best_bed = np.where(closer, bed[i] + (bed[i + 1] - bed[i]) * t, best_bed)
    return best, best_bed


def carve_rivers(h: np.ndarray, rivers: list[dict[str, Any]], rng: np.random.Generator
                 ) -> tuple[np.ndarray, np.ndarray]:
    """Rivers worked out on a coarse grid (distance + bed), applied at full size; returns heights and a 0..1 bank
    mask for the splat."""
    n = h.shape[0]
    m = min(n, ERODE_RES)
    small = _resize(h, m) if m != n else h
    bank = np.zeros_like(h)
    for r in rivers:
        dist, bed = river_field(r, small, rng)
        if m != n:
            dist, bed = _resize(dist, n, Image.Resampling.BILINEAR), _resize(bed, n, Image.Resampling.BILINEAR)
        w = r["width"]
        channel = bed - r["depth"] * 0.5 * np.clip(1 - (dist / w) ** 2, 0, 1)
        f = _smooth(w * 4.0, w * 0.5, dist)
        h = h * (1 - f) + np.minimum(h, channel) * f
        bank = np.maximum(bank, _smooth(w * 2.5, w * 0.8, dist))
    return h, bank


def quantize16(h: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """0..1 -> uint16 with ±0.5 LSB triangular dither, so gentle slopes never band into terraces."""
    d = (rng.random(h.shape) - rng.random(h.shape)) * 0.5
    return np.clip(np.round(h * 65535 + d), 0, 65535).astype(np.uint16)


def slope_map(h01: np.ndarray, world_m: float, height_m: float) -> np.ndarray:
    n = h01.shape[0]
    cell = world_m / (n - 1)
    gy, gx = np.gradient(h01.astype(np.float64) * height_m, cell)
    return np.hypot(gx, gy).astype(np.float32)


def splat_weights(h01: np.ndarray, slope: np.ndarray, water: float | None, snowline: float, bank: np.ndarray,
                  rng: np.random.Generator) -> np.ndarray:
    """(n, n, 4) weights ground, shore, cliff, peak, summing to 1: snow above the snowline unless near-vertical,
    then rock by slope, then shore near water, ground for the rest."""
    n = h01.shape[0]
    jitter = fbm(n, 16, 4, rng)
    peak = _smooth(snowline - 0.04, snowline + 0.04, h01 + 0.03 * jitter) * (1 - _smooth(1.0, 1.6, slope))
    cliff = _smooth(CLIFF_SLOPE[0], CLIFF_SLOPE[1], slope * (1 + 0.25 * jitter))
    shore = bank.copy()
    if water is not None:
        band = 0.02 + 0.01 * jitter
        shore = np.maximum(shore, _smooth(water + band, water, h01))
    rest = 1 - peak
    w_cliff = rest * cliff
    rest2 = rest - w_cliff
    w_shore = rest2 * shore
    w = np.stack([rest2 - w_shore, w_shore, w_cliff, peak], -1)
    return np.clip(w, 0, 1).astype(np.float32)


def splat8(w: np.ndarray) -> np.ndarray:
    """Weights -> RGBA8 whose channels add up to exactly 255 per pixel (largest remainder)."""
    w = w / np.maximum(w.sum(-1, keepdims=True), 1e-9)
    s = w * 255
    q = np.floor(s).astype(np.int16)
    deficit = 255 - q.sum(-1)
    rank = np.argsort(-(s - q), axis=-1, kind="stable")
    for k in range(4):
        add = (deficit > k).astype(np.int16)
        np.put_along_axis(q, rank[..., k:k + 1], np.take_along_axis(q, rank[..., k:k + 1], -1) + add[..., None], -1)
    return q.astype(np.uint8)


def histogram_report(h16: np.ndarray) -> dict[str, Any]:
    """Terracing check on the 16-bit levels actually used: a stair-stepped map piles samples on few levels and
    leaves runs of empty levels between them."""
    lo, hi = int(h16.min()), int(h16.max())
    counts = np.bincount(h16.ravel(), minlength=65536)[lo:hi + 1]
    used = int((counts > 0).sum())
    # The coarse histogram (up to 256 bins, fewer on small maps) must have no empty bin inside the range: a gap
    # there is the jump between two terraces.
    bins = max(16, min(256, h16.shape[0] // 4))
    coarse = np.histogram(h16, bins=bins, range=(lo, hi + 1))[0]
    return {"min": lo, "max": hi, "levels_used": used, "levels_span": hi - lo + 1,
            "max_level_share": round(float(counts.max()) / h16.size, 5),
            "coarse_empty_bins": int((coarse == 0).sum()), "continuous": bool((coarse > 0).all())}


def hillshade(h01: np.ndarray, world_m: float, height_m: float) -> np.ndarray:
    n = h01.shape[0]
    gy, gx = np.gradient(h01.astype(np.float64) * height_m, world_m / (n - 1))
    nrm = np.stack([-gx, np.ones_like(gx), -gy], -1)
    nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True)
    light = np.array([-0.5, 0.75, -0.45])
    light /= np.linalg.norm(light)
    return np.clip(nrm @ light, 0, 1).astype(np.float32)


def map_preview(h01: np.ndarray, w: np.ndarray, water: float | None, world_m: float, height_m: float,
                colors: dict[str, tuple[int, int, int]] | None = None, size: int = 768) -> Image.Image:
    """Top-down shaded relief tinted by the splat, water in blue."""
    cols = np.array([(colors or ROLE_COLORS)[r] for r in ROLES], np.float32) / 255
    small_h = _resize(h01, size, Image.Resampling.BILINEAR)
    small_w = np.stack([_resize(w[..., k], size, Image.Resampling.BILINEAR) for k in range(4)], -1)
    shade = hillshade(small_h, world_m, height_m)[..., None]
    rgb = (small_w @ cols) * (0.35 + 0.75 * shade)
    if water is not None:
        depth = np.clip((water - small_h) * 6, 0, 1)[..., None]
        wet = small_h[..., None] < water
        rgb = np.where(wet, rgb * 0.35 + np.array([0.12, 0.3, 0.5]) * (1 - depth * 0.5), rgb)
    return Image.fromarray(np.clip(rgb * 255, 0, 255).astype(np.uint8), "RGB")


def raw16(h16: np.ndarray) -> bytes:
    """Little-endian ("Windows" byte order in Unity's Import Raw), row 0 = north."""
    return h16.astype("<u2").tobytes()


def png16(h16: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(h16, np.uint16)).save(buf, "PNG")
    return buf.getvalue()


def png_rgba(a: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(a, "RGBA").save(buf, "PNG")
    return buf.getvalue()


def build_height(plan: dict[str, Any], p: HeightParams) -> dict[str, Any]:
    """Everything op.terrain_height computes, without the blob store (tests call this directly)."""
    plan = clean_plan(plan)
    if not plan["regions"] and not plan["rivers"]:
        raise ValueError("plan has no regions or rivers")
    seed = p.seed or zlib.crc32(json.dumps(plan, sort_keys=True).encode())
    rng = np.random.default_rng(seed)
    n = p.size
    m = min(n, ERODE_RES)
    base = compose(plan, m, rng)
    eroded = erode(base, p.erosion)
    h = compose(plan, n, np.random.default_rng(seed)) if n != m else base
    if n != m:
        # Detail comes from the full-size noise; the erosion runs on the coarse grid and adds its difference.
        h = h + _resize(eroded - base, n)
    else:
        h = eroded
    h, bank = carve_rivers(h, plan["rivers"], rng)
    lo, hi = float(h.min()), float(h.max())
    span = max(hi - lo, 1e-6)
    h01 = ((h - lo) / span).astype(np.float32)
    water = None if plan["water_level"] is None else float(np.clip((plan["water_level"] - lo) / span, 0, 1))
    snow = plan["snowline"]
    h16 = quantize16(h01, rng)
    h01q = h16.astype(np.float32) / 65535
    slope = slope_map(h01q, p.world_m, p.height_m)
    w = splat_weights(h01q, slope, water, snow, bank, rng)
    splat_n = n - 1
    ws = np.stack([_resize(w[..., k], splat_n, Image.Resampling.BILINEAR) for k in range(4)], -1)
    s8 = splat8(ws)
    hist = histogram_report(h16)
    coverage = {r: round(float(s8[..., k].mean()) / 255, 4) for k, r in enumerate(ROLES)}
    return {"h16": h16, "splat": s8, "weights": w, "h01": h01q, "water": water, "snowline": snow, "seed": seed,
            "plan": plan, "histogram": hist, "coverage": coverage,
            "slope_max_deg": round(math.degrees(math.atan(float(np.percentile(slope, 99.5)))), 1)}


async def _read_json_ref(ctx: StepContext, ref: BlobRef) -> Any:
    try:
        return json.loads(await asyncio.to_thread(ctx.blobs.path(ref.hash).read_bytes))
    except (OSError, ValueError) as e:
        raise StepError("op_bad_input", f"bad json input {ref.name}: {e}") from e


async def _put_bytes(ctx: StepContext, data: bytes, name: str, mime: str) -> BlobRef:
    return await ctx.blobs.put_bytes(data, name=name, mime=mime)


async def _put_json(ctx: StepContext, doc: Any, name: str) -> BlobRef:
    return await _put_bytes(ctx, json.dumps(doc, indent=1, ensure_ascii=False).encode("utf-8"), name,
                            "application/json")


def _ref(r: BlobRef) -> dict[str, Any]:
    return {"file": r.name, "hash": r.hash}


class TerrainHeight(OpExecutor):
    kind = "op.terrain_height"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("plan") or []
        if not refs:
            raise StepError("op_no_input", "input 'plan' (terrain plan json) is empty")
        p = HeightParams.parse(params)
        plan = await _read_json_ref(ctx, refs[0])
        if not isinstance(plan, dict):
            raise StepError("op_bad_input", "terrain plan is not an object")
        ctx.progress(0, 3, label="heightmap")
        try:
            res = await asyncio.to_thread(build_height, plan, p)
        except ValueError as e:
            raise StepError("op_bad_input", str(e)) from e
        if ctx.cancelled.is_set():
            raise asyncio.CancelledError
        ctx.progress(2, 3, label="save")
        a = p.asset
        h16, s8 = res["h16"], res["splat"]
        height_png = await _put_bytes(ctx, await asyncio.to_thread(png16, h16), f"{a}_height16.png", "image/png")
        raw = await _put_bytes(ctx, raw16(h16), f"{a}_height.raw", "application/octet-stream")
        splat = await _put_bytes(ctx, await asyncio.to_thread(png_rgba, s8), f"{a}_splat.png", "image/png")
        preview = await asyncio.to_thread(map_preview, res["h01"], res["weights"], res["water"], p.world_m, p.height_m)
        prev_ref = await _put_png(ctx, preview, f"{a}_map.png")
        water_m = None if res["water"] is None else round(res["water"] * p.height_m, 3)
        doc = {"type": HEIGHT_TYPE, "asset": a, "resolution": p.size, "world_m": p.world_m, "height_m": p.height_m,
               "water_level": res["water"], "water_m": water_m, "snowline": round(res["snowline"], 4),
               "seed": res["seed"], "erosion": p.erosion, "splat_size": p.size - 1, "channels": list(ROLES),
               "convention": "row 0 = north (-Z Godot, +Z Unity), column 0 = west; heights 0..1 of height_m",
               "heightmap": _ref(height_png), "raw": _ref(raw), "splat": _ref(splat),
               "histogram": res["histogram"], "coverage": res["coverage"], "slope_max_deg": res["slope_max_deg"],
               "plan": res["plan"]}
        ctx.progress(3, 3)
        return StepResult(outputs={"terrain": [await _put_json(ctx, doc, HEIGHT_FILE)], "heightmap": [height_png],
                                   "raw": [raw], "splat": [splat], "preview": [prev_ref]},
                          meta={"resolution": p.size, "continuous": res["histogram"]["continuous"],
                                "levels_used": res["histogram"]["levels_used"], "coverage": res["coverage"],
                                "water_m": water_m, "seed": res["seed"]})


def _avg_color(img: Image.Image) -> tuple[int, int, int]:
    c = np.asarray(img.convert("RGB").resize((16, 16), Image.Resampling.BOX), np.float32).reshape(-1, 3).mean(0)
    return int(c[0]), int(c[1]), int(c[2])


def render_view(h01: np.ndarray, splat: np.ndarray, albedo: list[np.ndarray], tile_m: list[float],
                water: float | None, world_m: float, height_m: float, w: int = 960, hgt: int = 540) -> Image.Image:
    """Oblique heightfield render (voxel-space march, all screen columns at once) textured with the real layers, so
    the gate shows the terrain the engines will draw, not just its map. Camera in the south-west corner."""
    n = h01.shape[0]
    hm = h01 * height_m
    shade = hillshade(h01, world_m, height_m)
    sp = np.stack([_resize(splat[..., k].astype(np.float32) / 255, n, Image.Resampling.BILINEAR)
                   for k in range(4)], -1)
    cam = np.array([-0.1 * world_m, 1.1 * world_m])
    fwd = np.array([0.55 * world_m, 0.45 * world_m]) - cam
    fwd /= np.linalg.norm(fwd)
    side = np.array([-fwd[1], fwd[0]])
    cam_h = float(hm.max()) + 0.3 * world_m * max(height_m / world_m, 0.12)
    fov = math.tan(math.radians(32))
    focal = w / 2 / fov
    horizon = hgt * 0.22
    sky_top, sky_bot = np.array([0.36, 0.52, 0.78]), np.array([0.78, 0.84, 0.9])
    t = np.linspace(0, 1, hgt)[:, None, None]
    img = np.broadcast_to(sky_top * (1 - t) + sky_bot * t, (hgt, w, 3)).astype(np.float32).copy()
    ybuf = np.full(w, hgt, np.int32)
    xs = (np.arange(w) / (w - 1) * 2 - 1) * fov
    rows = np.arange(hgt)[:, None]
    cell = world_m / (n - 1)
    z, dz, far = cell * 2, cell * 0.5, world_m * 2.2
    water_h = None if water is None else water * height_m
    while z < far:
        px = cam[0] + (fwd[0] + xs * side[0]) * z
        pz = cam[1] + (fwd[1] + xs * side[1]) * z
        inside = (px >= 0) & (px <= world_m) & (pz >= 0) & (pz <= world_m)
        if inside.any():
            u, v = np.clip(px / world_m, 0, 1), np.clip(pz / world_m, 0, 1)
            ground = sample(hm, u, v)
            top = ground if water_h is None else np.maximum(ground, water_h)
            sy = np.clip(horizon + (cam_h - top) / z * focal, 0, hgt).astype(np.int32)
            cols = np.nonzero(inside & (sy < ybuf))[0]
            if len(cols):
                uc, vc = u[cols], v[cols]
                col = np.zeros((len(cols), 3), np.float32)
                for k in range(4):
                    a = albedo[k]
                    tx = ((px[cols] / tile_m[k]) % 1) * (a.shape[1] - 1)
                    ty = ((pz[cols] / tile_m[k]) % 1) * (a.shape[0] - 1)
                    col += a[ty.astype(np.int32), tx.astype(np.int32)] * sample(sp[..., k], uc, vc)[:, None]
                col *= 0.35 + 0.8 * sample(shade, uc, vc)[:, None]
                if water_h is not None:
                    under = (ground[cols] < water_h)[:, None]
                    col = np.where(under, col * 0.25 + np.array([0.15, 0.32, 0.5]) * 0.75, col)
                fog = min(1.0, z / far) ** 1.5 * 0.7
                col = col * (1 - fog) + sky_bot * fog
                mask = (rows >= sy[cols][None]) & (rows < ybuf[cols][None])
                img[:, cols] = np.where(mask[..., None], col[None], img[:, cols])
                ybuf[cols] = sy[cols]
        z += dz
        dz *= 1.008
    return Image.fromarray(np.clip(img * 255, 0, 255).astype(np.uint8), "RGB")


def mask_map(maps: dict[str, Image.Image]) -> Image.Image:
    """Unity terrain mask map: R metallic, G AO, B height, A smoothness (1 - roughness)."""
    size = maps["albedo"].size

    def ch(role: str, default: int) -> np.ndarray:
        m = maps.get(role)
        if m is None:
            return np.full((size[1], size[0]), default, np.uint8)
        return np.asarray(m.convert("L").resize(size), np.uint8)

    rgba = np.stack([ch("metallic", 0), ch("ao", 255), ch("height", 128), 255 - ch("roughness", 190)], -1)
    return Image.fromarray(rgba, "RGBA")


class TerrainPack(OpExecutor):
    """Joins the heightmap step, the four PBR layers (in ground, shore, cliff, peak order) and an optional skybox
    into terrain.json, plus a textured oblique render for the review gate."""

    kind = "op.terrain_pack"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from .pbr import MATERIAL_NAME, MATERIAL_TYPE
        from .pano import SKYBOX_FILE

        t_refs = [r for r in inputs.get("terrain") or [] if r.name == HEIGHT_FILE]
        if not t_refs:
            raise StepError("op_no_input", "input 'terrain' (op.terrain_height output) is empty")
        hdoc = await _read_json_ref(ctx, t_refs[0])
        if hdoc.get("type") != HEIGHT_TYPE:
            raise StepError("op_bad_input", "input 'terrain' is not a terrain_height.json")
        mats = [r for r in inputs.get("material") or [] if r.name == MATERIAL_NAME]
        if not mats:
            raise StepError("op_no_input", "input 'material' (op.pbr pbr_material.json) is empty")
        mdoc = await _read_json_ref(ctx, mats[0])
        materials = mdoc.get("materials") if mdoc.get("type") == MATERIAL_TYPE else None
        if not materials or len(materials) < len(ROLES):
            raise StepError("op_bad_input", f"need {len(ROLES)} PBR layers (ground, shore, cliff, peak), "
                                            f"got {len(materials or [])}")
        plan_layers = await self._plan_layers(ctx, inputs)
        a = str(hdoc["asset"])
        ctx.progress(0, 4, label="layers")
        layers, albedo, tiles, colors, files = [], [], [], {}, []
        for k, role in enumerate(ROLES):
            m = materials[k]
            maps = {r: await _load(ctx, BlobRef(hash=v["hash"], mime="image/png", size=0, name=v.get("name")))
                    for r, v in m["maps"].items()}
            mask = await _put_png(ctx, await asyncio.to_thread(mask_map, maps), f"{a}_{role}_mask.png")
            info = plan_layers.get(role) or {}
            tile = _f(info.get("tile_m"), DEFAULT_TILE_M[role], 1, 200)
            entry = {"role": role, "name": str(info.get("name") or role), "tile_m": tile,
                     "maps": {r: {"file": v.get("name"), "hash": v["hash"]} for r, v in m["maps"].items()},
                     "mask": _ref(mask), "color": "#%02x%02x%02x" % _avg_color(maps["albedo"])}
            layers.append(entry)
            files.append(mask)
            colors[role] = _avg_color(maps["albedo"])
            alb = maps["albedo"].convert("RGB")
            alb.thumbnail((256, 256))
            albedo.append(np.asarray(alb, np.float32) / 255)
            tiles.append(tile)
        ctx.progress(1, 4, label="render")
        h16 = np.frombuffer(await asyncio.to_thread(ctx.blobs.path(hdoc["raw"]["hash"]).read_bytes), "<u2")
        n = int(hdoc["resolution"])
        h01 = h16.reshape(n, n).astype(np.float32) / 65535
        with Image.open(ctx.blobs.path(hdoc["splat"]["hash"])) as im:
            s8 = np.asarray(im.convert("RGBA"), np.uint8)
        world, height = float(hdoc["world_m"]), float(hdoc["height_m"])
        small = _resize(h01, min(n, 513), Image.Resampling.BILINEAR)
        view = await asyncio.to_thread(render_view, small, s8, albedo, tiles, hdoc.get("water_level"), world, height)
        ws = np.stack([_resize(s8[..., k].astype(np.float32) / 255, 768, Image.Resampling.BILINEAR)
                       for k in range(4)], -1)
        tinted = await asyncio.to_thread(map_preview, _resize(h01, 768, Image.Resampling.BILINEAR), ws,
                                         hdoc.get("water_level"), world, height, colors)
        ctx.progress(2, 4, label="skybox")
        sky = None
        for r in inputs.get("skybox") or []:
            if r.name == SKYBOX_FILE:
                sky = _ref(r)
        view_ref = await _put_png(ctx, view, f"{a}_view.png")
        map_ref = await _put_png(ctx, tinted, f"{a}_map.png")
        doc = {**{k: v for k, v in hdoc.items() if k != "type"}, "type": TERRAIN_TYPE, "layers": layers,
               "skybox": sky, "preview": _ref(view_ref)}
        ctx.progress(3, 4, label="terrain.json")
        out = await _put_json(ctx, doc, TERRAIN_FILE)
        ctx.progress(4, 4)
        return StepResult(outputs={"terrain": [out], "preview": [view_ref, map_ref], "masks": files},
                          meta={"layers": [l["name"] for l in layers], "skybox": sky is not None,
                                "continuous": hdoc.get("histogram", {}).get("continuous")})

    @staticmethod
    async def _plan_layers(ctx: StepContext, inputs: Outputs) -> dict[str, Any]:
        for r in inputs.get("plan") or []:
            doc = await _read_json_ref(ctx, r)
            if isinstance(doc, dict) and isinstance(doc.get("layers"), dict):
                return doc["layers"]
        return {}


class TerrainSky(OpExecutor):
    """The terrain's optional skybox: op.pano_fix + op.pano_cubemap on the generated panorama, or nothing at all when
    the run asked for no skybox (the generator step then made no image)."""

    kind = "op.terrain_sky"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from .pano import PanoCubemap, PanoFix

        refs = inputs.get("images") or []
        if not refs:
            return StepResult(outputs={"images": [], "faces": [], "irradiance": [], "preview": [], "skybox": [],
                                       "seam": []}, meta={"skipped": True})
        asset = str(params.get("asset") or "sky")
        fix = await PanoFix().run(ctx, {"asset": asset}, {"images": refs[:1]})
        cube = await PanoCubemap().run(ctx, {"asset": asset}, {"images": fix.outputs["images"],
                                                                "info": fix.outputs["info"]})
        return StepResult(outputs={**cube.outputs, "seam": fix.outputs["preview"]},
                          meta={**cube.meta, "seam_after": fix.meta.get("seam_after")})


TERRAIN_OPS = (TerrainHeight, TerrainPack, TerrainSky)
