"""Weather and atmosphere overlay (weather-overlay). `op.weather_overlay` draws rain (light / heavy / slanted), snow,
falling leaves, drifting ash, fireflies and scrolling mist on the CPU and writes, per layer, a loop sheet for engines
without particles, a particle texture, a neutral GPU-emitter spec and the QA of the loop, all into weather.json.

Loop rule: every particle's travel over the N frames is a whole number of tile widths (kx, ky integers) and its sway
and spin are whole-number harmonics of the loop, so frame N is exactly frame 0 (speed per frame = k * cell / N, hence
N frames x speed divides the cell). Mist reuses tile_anim's periodic sine fields, scrolled by whole tiles. Units: px at
the REF tile, y down; the sheet is the same picture scaled by cell / REF."""
from __future__ import annotations

import asyncio
import json
import math
import re
import zlib
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png
from .tile import _seam_ratio
from .tile_anim import SEAM_RATIO_MAX, _field, _terms, loop_ratio
from .vfx_particles import _grid, _hex_rgba, _noise, _rgba, _smoothstep, parse_list, soft_dot, spark, styled_cell
from .vfx_particles import sheet as pack_sheet

WEATHER_FILE = "weather.json"
WEATHER_TYPE = "asset-studio/weather"
TAU = 2 * math.pi
REF = 256
# 1024 is the largest texture every mobile GPU in the support range accepts without a fallback, so no sheet or tile
# goes past it.
MAX_TEX = 1024
CELLS = (128, 192, 256)
FRAME_RANGE = (4, 64)
MIST_SIZES = (256, 512, 1024)
PARTICLE_SIZES = (32, 64, 128)
MAX_PARTICLES = 400
MAX_AMOUNT = 512
WRAP_TOL = 1e-3
MIST_SCREEN_TILE = 640
ALPHA_MAX = 0.85
VIEWS = ("sideview", "topdown")
STYLED_FAMILIES = {"leaves": "leaf", "ash": "flake"}

KIND_CFG: dict[str, dict[str, Any]] = {
    "rain_light": {"family": "rain", "shape": "streak", "texture": "spark", "count": 14, "loop": 0.6,
                   "cycles": [(0, 1), (0, 2)], "size": (1.0, 1.5), "length": 34, "top_length": 0.45,
                   "alpha": (0.35, 0.65), "colors": ["#d6e6ff"], "life": 0.4, "spread": 3, "turb": 0,
                   "sway": (0, 0), "pulse": 0.0, "spin": (0, 0), "blend": "alpha", "quad": 1.0},
    "rain_heavy": {"family": "rain", "shape": "streak", "texture": "spark", "count": 24, "loop": 0.5,
                   "cycles": [(0, 2), (0, 3)], "size": (1.1, 1.7), "length": 62, "top_length": 0.45,
                   "alpha": (0.4, 0.75), "colors": ["#c8dcff"], "life": 0.4, "spread": 3, "turb": 0,
                   "sway": (0, 0), "pulse": 0.0, "spin": (0, 0), "blend": "alpha", "quad": 1.0},
    "rain_slanted": {"family": "rain", "shape": "streak", "texture": "spark", "count": 22, "loop": 0.55,
                     "cycles": [(1, 2), (2, 4)], "size": (1.0, 1.6), "length": 56, "top_length": 0.45,
                     "alpha": (0.4, 0.7), "colors": ["#cfe0ff"], "life": 0.4, "spread": 3, "turb": 0,
                     "sway": (0, 0), "pulse": 0.0, "spin": (0, 0), "blend": "alpha", "quad": 1.0},
    "snow": {"family": "snow", "shape": "disc", "texture": "snowflake", "count": 16, "loop": 3.0,
             "cycles": [(0, 1), (0, 2)], "cycles_top": [(1, 1), (2, 1)], "size": (1.6, 3.4), "alpha": (0.6, 0.95),
             "colors": ["#ffffff"], "life": 5.0, "spread": 14, "turb": 1.2, "sway": (7, 2), "pulse": 0.0,
             "spin": (0, 0), "blend": "alpha", "quad": 3.2},
    "leaves": {"family": "leaves", "shape": "leaf", "texture": "leaf", "count": 2, "loop": 4.0,
               "cycles": [(0, 1), (1, 1)], "cycles_top": [(1, 0), (2, 1)], "size": (9.0, 15.0),
               "alpha": (0.85, 1.0), "colors": ["#c98a3a", "#a8561f", "#8a9a35"], "life": 6.0, "spread": 25,
               "turb": 2.5, "sway": (22, 6), "pulse": 0.0, "spin": (1, 2), "blend": "alpha", "quad": 2.35},
    "ash": {"family": "ash", "shape": "disc", "texture": "flake", "count": 12, "loop": 5.0,
            "cycles": [(0, 1), (1, 1)], "cycles_top": [(1, 0), (1, 1)], "size": (1.4, 3.0), "alpha": (0.4, 0.8),
            "colors": ["#8c8a86", "#c0b8ae"], "life": 6.0, "spread": 25, "turb": 1.5, "sway": (8, 3),
            "pulse": 0.25, "spin": (0, 0), "blend": "alpha", "quad": 3.2},
    "fireflies": {"family": "fireflies", "shape": "glow", "texture": "soft_dot", "count": 4, "loop": 6.0,
                  "cycles": [(0, 0)], "size": (3.0, 5.0), "alpha": (0.7, 1.0), "colors": ["#d8ff7a"],
                  "life": 7.0, "spread": 180, "turb": 2.0, "sway": (16, 16), "pulse": 0.75, "spin": (0, 0),
                  "blend": "add", "quad": 5.0, "drift": (6, 18)},
}
MIST_CFG: dict[str, Any] = {"loop": 16.0, "scroll": (1, 0), "density": 0.3, "color": "#dfe8f2"}
KINDS = (*KIND_CFG, "mist")
DEFAULT_KINDS = KINDS


def _cycles(cfg: dict[str, Any], view: str) -> list[tuple[int, int]]:
    return list(cfg["cycles_top"] if view == "topdown" and cfg.get("cycles_top") else cfg["cycles"])


def loop_velocity(cell: int, frames: int, k: int) -> float:
    """Pixels per frame of a particle that wraps k whole tiles per loop."""
    return k * cell / frames


def sheet_grid(cell: int, frames: int) -> tuple[int, int]:
    cols = MAX_TEX // cell
    rows = math.ceil(frames / cols)
    if rows * cell > MAX_TEX:
        raise StepError("op_bad_params", f"{frames} frames of {cell} px need a {cols * cell}x{rows * cell} sheet, "
                                         f"over the {MAX_TEX} px texture budget (fewer frames or a smaller cell)")
    return cols, rows


def make_particles(cfg: dict[str, Any], view: str, cell: int, intensity: float, seed: int, kind: str,
                   colors: list[str]) -> dict[str, Any]:
    rng = np.random.default_rng([seed, zlib.crc32(kind.encode())])
    scale = cell / REF
    n = min(MAX_PARTICLES, int(round(cfg["count"] * intensity * scale * scale)))
    cyc = np.asarray(_cycles(cfg, view), np.float64).reshape(-1, 2)
    j = rng.integers(0, len(cyc), n)
    fast = j / (len(cyc) - 1) if len(cyc) > 1 else np.full(n, 0.5)
    kxy = cyc[j]
    mag = np.hypot(kxy[:, 0], kxy[:, 1])
    direction = np.where(mag[:, None] > 0, kxy / np.maximum(mag, 1e-9)[:, None], np.array([0.0, 1.0]))
    length = cfg.get("length", 0.0) * (cfg.get("top_length", 1.0) if view == "topdown" else 1.0)

    def lerp(pair: tuple[float, float]) -> np.ndarray:
        return pair[0] + (pair[1] - pair[0]) * rng.random(n)

    palette = np.array([_hex_rgba(c)[:3] for c in colors], np.float64)
    spin = rng.choice([-1, 1], n) * rng.integers(cfg["spin"][0], cfg["spin"][1] + 1, n)
    return {"n": n, "shape": cfg["shape"], "x0": rng.random(n) * cell, "y0": rng.random(n) * cell,
            "kx": kxy[:, 0], "ky": kxy[:, 1], "dir": direction, "size": lerp(cfg["size"]) * scale * (0.85 + 0.3 * fast),
            "length": length * scale * (0.8 + 0.4 * fast), "alpha": lerp(cfg["alpha"]) * (0.7 + 0.3 * fast),
            "rgb": palette[rng.integers(0, len(palette), n)], "p1": rng.random(n), "p2": rng.random(n),
            "mx": rng.integers(1, 3, n), "my": rng.integers(1, 3, n),
            "ax": cfg["sway"][0] * scale * (0.5 + rng.random(n)), "ay": cfg["sway"][1] * scale * (0.5 + rng.random(n)),
            "theta0": rng.random(n) * TAU, "spin": spin, "kp": rng.integers(1, 4, n), "pulse": cfg["pulse"]}


def make_ripples(cfg: dict[str, Any], cell: int, intensity: float, seed: int, kind: str,
                 colors: list[str]) -> dict[str, Any]:
    """Topdown rain lands: rings that grow once or twice per loop where the drops hit the ground."""
    rip = {**cfg, "shape": "ripple", "count": cfg["count"] * 0.5, "cycles": [(0, 0)], "cycles_top": None,
           "size": (5.0, 9.0), "alpha": (0.3, 0.55), "sway": (0, 0), "length": 0, "pulse": 0.0, "spin": (0, 0)}
    return make_particles(rip, "topdown", cell, intensity, seed, kind + "_ripple", colors)


def _reach(p: dict[str, Any], i: int) -> float:
    s, size = p["shape"], float(p["size"][i])
    if s == "streak":
        return float(p["length"][i]) + 3.5 * size
    if s == "ripple":
        return size + 4.0
    if s == "glow":
        return 2.4 * size
    return size + 1.5


def _streak(dx: np.ndarray, dy: np.ndarray, p: dict[str, Any], i: int, u: float) -> np.ndarray:
    length, w = max(float(p["length"][i]), 1e-3), float(p["size"][i])
    ux, uy = p["dir"][i]
    t = np.clip(((dx + ux * length) * ux + (dy + uy * length) * uy) / length, 0, 1)
    ex, ey = dx + ux * length * (1 - t), dy + uy * length * (1 - t)
    return np.exp(-(ex * ex + ey * ey) / (w * w)) * (0.3 + 0.7 * t)


def _disc(dx: np.ndarray, dy: np.ndarray, p: dict[str, Any], i: int, u: float) -> np.ndarray:
    s = float(p["size"][i])
    return 1 - _smoothstep(s * 0.45, s, np.sqrt(dx * dx + dy * dy))


def _glow(dx: np.ndarray, dy: np.ndarray, p: dict[str, Any], i: int, u: float) -> np.ndarray:
    s = float(p["size"][i])
    return np.exp(-2.0 * (dx * dx + dy * dy) / (s * s))


def _leaf(dx: np.ndarray, dy: np.ndarray, p: dict[str, Any], i: int, u: float) -> np.ndarray:
    th = float(p["theta0"][i]) + TAU * float(p["spin"][i]) * u
    c, s = math.cos(th), math.sin(th)
    xr, yr = dx * c + dy * s, -dx * s + dy * c
    flutter = 0.45 + 0.55 * abs(math.cos(TAU * (float(p["mx"][i]) * u + float(p["p2"][i]))))
    a_ax = float(p["size"][i])
    b_ax = max(0.5, a_ax * 0.42 * flutter)
    return 1 - _smoothstep(0.6, 1.0, (xr / a_ax) ** 2 + (yr / b_ax) ** 2)


def _ripple(dx: np.ndarray, dy: np.ndarray, p: dict[str, Any], i: int, u: float) -> np.ndarray:
    ph = (float(p["mx"][i]) * u + float(p["p1"][i])) % 1.0
    r = np.sqrt(dx * dx + dy * dy)
    return np.exp(-(((r - ph * float(p["size"][i])) / 1.2) ** 2)) * (1 - ph) ** 1.5


SHAPES = {"streak": _streak, "disc": _disc, "glow": _glow, "leaf": _leaf, "ripple": _ripple}


def render_frame(sets: list[dict[str, Any]], cell: int, u: float) -> np.ndarray:
    """Premultiplied RGB + alpha accumulator at loop phase u (1.0 = the frame after the last, equal to frame 0)."""
    acc = np.zeros((cell, cell, 4), np.float64)
    for p in sets:
        for i in range(p["n"]):
            px = p["x0"][i] + p["kx"][i] * cell * u + p["ax"][i] * math.sin(TAU * (p["mx"][i] * u + p["p1"][i]))
            py = p["y0"][i] + p["ky"][i] * cell * u + p["ay"][i] * math.sin(TAU * (p["my"][i] * u + p["p2"][i]))
            r = min(int(math.ceil(_reach(p, i))), cell // 2 - 1)
            xs, ys = math.floor(px) + np.arange(-r, r + 1), math.floor(py) + np.arange(-r, r + 1)
            a = SHAPES[p["shape"]]((xs - px)[None, :], (ys - py)[:, None], p, i, u) * p["alpha"][i]
            if p["pulse"]:
                glow = 0.5 + 0.5 * math.sin(TAU * (p["kp"][i] * u + p["p2"][i]))
                a = a * (1 - p["pulse"] + p["pulse"] * glow)
            acc[np.ix_(ys % cell, xs % cell)] += np.concatenate([a[..., None] * p["rgb"][i], a[..., None]], -1)
    return acc


def finish(acc: np.ndarray, base_rgb: np.ndarray) -> np.ndarray:
    a = acc[..., 3:]
    rgb = np.where(a > 1e-4, acc[..., :3] / np.maximum(a, 1e-4), base_rgb)
    return np.concatenate([np.clip(rgb, 0, 1), np.minimum(a, 1.0)], -1).astype(np.float32)


def premult(a: np.ndarray) -> np.ndarray:
    return np.concatenate([a[..., :3] * a[..., 3:], a[..., 3:]], -1)


def _u8(a: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(np.round(a * 255), 0, 255).astype(np.uint8), "RGBA")


def particle_loop(kind: str, view: str, cell: int, frames: int, intensity: float, seed: int,
                  tint: str | None, cfg: dict[str, Any] | None = None) -> tuple[list[np.ndarray], np.ndarray, int]:
    cfg = cfg or KIND_CFG[kind]
    colors = [tint] if tint else cfg["colors"]
    sets = [make_particles(cfg, view, cell, intensity, seed, kind, colors)]
    if view == "topdown" and cfg["shape"] == "streak":
        sets.append(make_ripples(cfg, cell, intensity, seed, kind, colors))
    base = np.array(_hex_rgba(colors[0])[:3])
    out = [finish(render_frame(sets, cell, t / frames), base) for t in range(frames)]
    return out, finish(render_frame(sets, cell, 1.0), base), sum(int(s["n"]) for s in sets)


def loop_qa(frames: list[np.ndarray], wrap: np.ndarray) -> dict[str, Any]:
    pm = [premult(f) for f in frames]
    diff = round(float(np.abs(premult(wrap) - pm[0]).mean()), 6)
    ratio = loop_ratio(pm)
    return {"wrap_diff": diff, "loop_ratio": ratio, "max_ratio": SEAM_RATIO_MAX,
            "passed": diff <= WRAP_TOL and ratio <= SEAM_RATIO_MAX}


def mist_terms(seed: int) -> tuple[np.ndarray, np.ndarray]:
    return _terms(seed, 12, 4, 1), _terms(seed + 1, 10, 8, 2)


def _mist_raw(terms: tuple[np.ndarray, np.ndarray], size: int, t: float, scroll: tuple[int, int]) -> np.ndarray:
    v, u = np.mgrid[0:size, 0:size].astype(np.float64)
    u, v = (u + 0.5) / size, (v + 0.5) / size
    sx, sy = scroll
    return 0.65 * _field(terms[0], u - sx * t, v - sy * t, t) + 0.35 * _field(terms[1], u + sx * t, v + sy * t, t)


def mist_levels(terms: tuple[np.ndarray, np.ndarray], scroll: tuple[int, int]) -> tuple[float, float]:
    """Density thresholds from a coarse grid so every size and frame shares one mapping."""
    f = _mist_raw(terms, 64, 0.0, scroll)
    return float(np.percentile(f, 10)), float(np.percentile(f, 99))


def mist_frame(terms: tuple[np.ndarray, np.ndarray], size: int, t: float, levels: tuple[float, float],
               density: float, color: str, scroll: tuple[int, int] = (1, 0)) -> np.ndarray:
    d = _smoothstep(levels[0], levels[1], _mist_raw(terms, size, t, scroll)) * min(density, ALPHA_MAX)
    rgb = np.broadcast_to(np.array(_hex_rgba(color)[:3]), (size, size, 3))
    return np.concatenate([rgb, d[..., None]], -1).astype(np.float32)


def snowflake(n: int) -> Image.Image:
    x, y = _grid(n)
    r, ang = np.hypot(x, y), np.arctan2(y, x)
    m = np.abs((ang / (math.pi / 3) + 0.5) % 1.0 - 0.5)
    arms = np.exp(-((m / 0.1) ** 2)) * (1 - _smoothstep(0.5, 0.9, r))
    return _rgba(np.clip(np.exp(-r * r * 40) + 0.7 * arms, 0, 1) * (1 - _smoothstep(0.85, 1.0, r)))


def leaf_texture(n: int) -> Image.Image:
    x, y = _grid(n)
    ax, bx = 0.85, 0.36
    width = bx * np.clip(1 - (x / ax) ** 2, 0, 1) ** 0.8
    alpha = np.clip((width - np.abs(y)) / 0.04, 0, 1) * (np.abs(x) < ax)
    return _rgba(alpha, 1 - 0.25 * np.exp(-((y / 0.03) ** 2)))


def flake(n: int, seed: int) -> Image.Image:
    x, y = _grid(n)
    edge = 0.55 + 0.35 * (_noise(n, np.random.default_rng(seed), 3) - 0.5) * 2
    return _rgba((1 - _smoothstep(edge * 0.55, edge, np.hypot(x, y))) * (1 - _smoothstep(0.85, 1.0, np.hypot(x, y))))


def particle_textures(n: int, seed: int) -> dict[str, Image.Image]:
    return {"spark": spark(n), "soft_dot": soft_dot(n), "snowflake": snowflake(n), "leaf": leaf_texture(n),
            "flake": flake(n, seed)}


def emitter_spec(kind: str, cfg: dict[str, Any], view: str, area: tuple[int, int], intensity: float,
                 color: str) -> dict[str, Any] | None:
    """Neutral GPU-emitter numbers (px, y down) that reproduce the loop's density and speeds on a screen of `area`."""
    w, h = area
    cyc = np.asarray(_cycles(cfg, view), np.float64).reshape(-1, 2)
    mag = np.hypot(cyc[:, 0], cyc[:, 1])
    speeds = mag * REF / cfg["loop"]
    unit = np.where(mag[:, None] > 0, cyc / np.maximum(mag, 1e-9)[:, None], 0.0).mean(0)
    norm = float(np.hypot(*unit))
    d = (float(unit[0] / norm), float(unit[1] / norm)) if norm > 1e-6 else (0.0, 1.0)
    lo, hi = float(speeds.min()), float(speeds.max())
    if hi <= 0 and cfg.get("drift"):
        lo, hi = (float(v) for v in cfg["drift"])
    on_screen = cfg["count"] * intensity * w * h / (REF * REF)
    if on_screen <= 0:
        return None
    length = cfg.get("length", 0.0) * (cfg.get("top_length", 1.0) if view == "topdown" else 1.0)
    q = ([0.8 * length / 0.7, 1.2 * length / 0.7] if cfg["shape"] == "streak"
         else [cfg["size"][0] * cfg["quad"], cfg["size"][1] * cfg["quad"]])
    top = view == "sideview" and cfg["family"] != "fireflies"
    if top:
        margin = int(math.ceil(max(length, q[1]))) + 16
        run = h + 2 * margin
        life = min(30.0, run / max(lo * abs(d[1]), 1.0))
        slide = run * d[0] / max(abs(d[1]), 1e-6)
        x0, x1 = min(0.0, -slide), max(float(w), w - slide)
        box = [(x0 + x1) / 2, -float(margin), (x1 - x0) / 2, 1.0]
        amount = on_screen * run / h
    else:
        margin, life = 0, float(cfg["life"])
        box = [w / 2, h / 2, w / 2, h / 2]
        amount = on_screen / 0.6
    deg = cfg["spin"][1] * 360.0 / cfg["loop"]
    spin = [-deg, deg]
    return {"texture": cfg["texture"], "blend": cfg["blend"], "spawn": "top" if top else "area",
            "amount": int(min(MAX_AMOUNT, max(1, math.ceil(amount)))), "capped": math.ceil(amount) > MAX_AMOUNT,
            "lifetime": round(life, 3), "direction": [round(d[0], 4), round(d[1], 4)], "spread": cfg["spread"],
            "speed": [round(lo, 2), round(hi, 2)], "quad_px": [round(v, 2) for v in q], "color": color,
            "alpha": round(sum(cfg["alpha"]) / 2, 3), "align_velocity": cfg["shape"] == "streak", "spin": spin,
            "random_angle": cfg["shape"] in ("leaf", "disc") and cfg["family"] != "snow",
            "turbulence": cfg["turb"], "margin": margin, "box": [round(v, 2) for v in box]}


def parse_area(raw: Any) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[xX*,]\s*(\d+)\s*", str(raw or "1280x720"))
    if not m or not all(320 <= int(g) <= 4096 for g in m.groups()):
        raise StepError("op_bad_params", f"area must be WxH with each side 320..4096, got {raw!r}")
    return int(m.group(1)), int(m.group(2))


def parse_color(raw: Any) -> str | None:
    s = str(raw or "auto").strip().lower()
    if s in ("", "auto", "none"):
        return None
    if not re.fullmatch(r"#?[0-9a-f]{6}", s):
        raise StepError("op_bad_params", f"tint must be auto or #rrggbb, got {raw!r}")
    return "#" + s.lstrip("#")


def _num(params: dict[str, Any], key: str, default: float, lo: float, hi: float, cast: Any) -> Any:
    raw = params.get(key)
    try:
        v = cast(default if raw in (None, "") else raw)
    except (TypeError, ValueError):
        raise StepError("op_bad_params", f"{key} must be a number") from None
    if not lo <= v <= hi:
        raise StepError("op_bad_params", f"{key} must be in {lo}..{hi}, got {v}")
    return v


def parse_params(params: dict[str, Any]) -> dict[str, Any]:
    kinds = parse_list(params.get("weather") or ",".join(DEFAULT_KINDS), KINDS, "weather")
    if not kinds:
        raise StepError("op_bad_params", "pick at least one weather kind")
    view = str(params.get("view") or "sideview").strip().lower()
    if view not in VIEWS:
        raise StepError("op_bad_params", f"view must be one of {VIEWS}")
    cell = _num(params, "cell", 256, 0, 99999, int)
    frames = _num(params, "frames", 16, *FRAME_RANGE, int)
    mist_size = _num(params, "mist_size", 512, 0, 99999, int)
    particle = _num(params, "particle_size", 64, 0, 99999, int)
    for name, v, allowed in (("cell", cell, CELLS), ("mist_size", mist_size, MIST_SIZES),
                             ("particle_size", particle, PARTICLE_SIZES)):
        if v not in allowed:
            raise StepError("op_bad_params", f"{name} must be one of {allowed}")
    sheet_grid(cell, frames)
    asset = re.sub(r"[^A-Za-z0-9_-]+", "_", str(params.get("asset") or "weather"))[:40] or "weather"
    return {"kinds": kinds, "view": view, "cell": cell, "frames": frames, "mist_size": mist_size,
            "particle_size": particle, "intensity": _num(params, "intensity", 1.0, 0.0, 1.0, float),
            "seed": _num(params, "seed", 7, 0, 99999, int), "area": parse_area(params.get("area")),
            "tint": parse_color(params.get("tint")), "asset": asset,
            "styled_for": parse_list(params.get("styled_for", "leaves, ash"), tuple(STYLED_FAMILIES), "styled family")}


def build_kind(kind: str, p: dict[str, Any]) -> dict[str, Any]:
    cell, frames, view = p["cell"], p["frames"], p["view"]
    cols, rows = sheet_grid(cell, frames)
    if kind == "mist":
        scroll = tuple(MIST_CFG["scroll"])
        terms = mist_terms(p["seed"])
        levels = mist_levels(terms, scroll)
        color = p["tint"] or MIST_CFG["color"]
        dens = MIST_CFG["density"] * max(p["intensity"], 0.0)
        fr = [mist_frame(terms, cell, t / frames, levels, dens, color, scroll) for t in range(frames)]
        wrap = mist_frame(terms, cell, 1.0, levels, dens, color, scroll)
        tile = mist_frame(terms, p["mist_size"], 0.0, levels, dens, color, scroll)
        edge = _seam_ratio(tile[..., 3:])
        qa = loop_qa(fr, wrap) | {"edge_ratio": {k: round(v, 3) for k, v in edge.items()}}
        qa["passed"] = qa["passed"] and max(edge.values()) <= SEAM_RATIO_MAX
        w, h = p["area"]
        layer = {"name": "mist", "kind": "mist", "family": "mist", "loop_seconds": MIST_CFG["loop"],
                 "cycles": [list(scroll)], "texture": "mist", "tint": color, "density": round(dens, 3),
                 "tiles": [round(w / MIST_SCREEN_TILE, 3), round(h / MIST_SCREEN_TILE, 3)],
                 "scroll": [round(scroll[0] / MIST_CFG["loop"], 5), round(scroll[1] / MIST_CFG["loop"], 5)],
                 "ground_fade": 1.0 if view == "sideview" else 0.0, "particles": 0}
        extra = {"tile": _u8(tile)}
    else:
        cfg = KIND_CFG[kind]
        fr, wrap, count = particle_loop(kind, view, cell, frames, p["intensity"], p["seed"], p["tint"])
        qa = loop_qa(fr, wrap)
        cyc = _cycles(cfg, view)
        layer = {"name": kind, "kind": "particles", "family": cfg["family"], "loop_seconds": cfg["loop"],
                 "cycles": [list(c) for c in cyc], "particles": count,
                 "velocity_px_per_frame": [[round(loop_velocity(cell, frames, kx), 4),
                                            round(loop_velocity(cell, frames, ky), 4)] for kx, ky in cyc],
                 "emitter": emitter_spec(kind, cfg, view, p["area"], p["intensity"], p["tint"] or cfg["colors"][0])}
        extra = {}
    layer |= {"sheet": f"{kind}_sheet", "cols": cols, "rows": rows, "frames": frames, "cell": cell,
              "fps": round(frames / layer["loop_seconds"], 3), "qa": qa}
    return {"layer": layer, "sheet": pack_sheet([_u8(f) for f in fr], cols, rows), "frames": fr, "wrap": wrap, **extra}


def preview(results: dict[str, dict[str, Any]], thumb: int = 160) -> Image.Image:
    gap, label = 6, 14
    names = list(results)
    out = Image.new("RGB", (4 * thumb + 5 * gap, len(names) * (thumb + label + gap) + gap), (24, 24, 28))
    d = ImageDraw.Draw(out)
    y, x0 = gap, gap
    yy, xx = np.mgrid[0:thumb, 0:thumb]
    check = np.where(((xx // 10) + (yy // 10)) % 2 == 0, 62, 44).astype(np.uint8)
    base = Image.fromarray(np.stack([check] * 3 + [np.full_like(check, 255)], -1), "RGBA")
    for name in names:
        res = results[name]
        n = len(res["frames"])
        for c, a in enumerate([res["frames"][0], res["frames"][n // 3], res["frames"][2 * n // 3], res["wrap"]]):
            cell = base.copy()
            cell.alpha_composite(_u8(a).resize((thumb, thumb), Image.Resampling.LANCZOS))
            out.paste(cell.convert("RGB"), (x0 + c * (thumb + gap), y))
        qa = res["layer"]["qa"]
        d.text((x0 + 2, y + thumb + 1), f"{name}  frames 0 / 1/3 / 2/3 / N(=0)  wrap {qa['wrap_diff']:.5f} "
                                        f"loop x{qa['loop_ratio']:g} {'ok' if qa['passed'] else 'FAIL'}",
               fill=(220, 220, 220))
        y += thumb + label + gap
    return out


class WeatherOverlay(OpExecutor):
    kind = "op.weather_overlay"
    version = "2"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        p = parse_params(params)
        asset, kinds = p["asset"], p["kinds"]
        total = len(kinds) + 3
        ctx.progress(0, total, label="textures")
        tex = await asyncio.to_thread(particle_textures, p["particle_size"], p["seed"])
        styled = []
        for ref in inputs.get("styled") or []:
            c = styled_cell(await _load(ctx, ref), p["particle_size"])
            if c is not None:
                styled.append(c)
        for i, fam in enumerate(p["styled_for"]):
            if styled:
                tex[STYLED_FAMILIES[fam]] = styled[min(i, len(styled) - 1)]
        results: dict[str, dict[str, Any]] = {}
        for i, kind in enumerate(kinds):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.progress(i + 1, total, label=kind)
            results[kind] = await asyncio.to_thread(build_kind, kind, p)
            if not results[kind]["layer"]["qa"]["passed"]:
                ctx.log(f"weather {kind}: loop check failed {results[kind]['layer']['qa']}", "warning")
        used = sorted({r["layer"]["emitter"]["texture"] for r in results.values()
                       if r["layer"].get("emitter")})
        styled_keys = {STYLED_FAMILIES[f] for f in p["styled_for"]} if styled else set()
        textures: dict[str, BlobRef] = {k: await _put_png(ctx, tex[k], f"{asset}_{k}.png") for k in used}
        if "mist" in results:
            textures["mist"] = await _put_png(ctx, results["mist"]["tile"], f"{asset}_mist.png")
        sheets: list[BlobRef] = []
        for kind, res in results.items():
            ref = await _put_png(ctx, res["sheet"], f"{asset}_{kind}_sheet.png")
            textures[res["layer"]["sheet"]] = ref
            sheets.append(ref)
            if res["layer"].get("emitter") and res["layer"]["emitter"]["texture"] in styled_keys:
                res["layer"]["emitter"]["color"] = "#ffffff"
        ctx.progress(len(kinds) + 1, total, label="preview")
        prev = await _put_png(ctx, await asyncio.to_thread(preview, results), f"{asset}_weather_preview.png")
        sizes = {k: list(await asyncio.to_thread(_png_size, ctx, r)) for k, r in textures.items()}
        largest = max(sizes.values(), key=lambda s: s[0] * s[1])
        doc = {"type": WEATHER_TYPE, "version": 1, "asset": asset, "view": p["view"], "cell": p["cell"],
               "reference_cell": REF, "frames": p["frames"], "area": list(p["area"]), "intensity": p["intensity"],
               "seed": p["seed"], "tint": p["tint"] or "auto", "styled": sorted(styled_keys),
               "units": "px at reference_cell, y down; sheets are the same picture scaled by cell / reference_cell",
               "budget": {"max_texture": MAX_TEX, "largest": largest, "passed": max(largest) <= MAX_TEX},
               "textures": {k: {"hash": r.hash, "size": sizes[k]} for k, r in textures.items()},
               "layers": [r["layer"] for r in results.values()],
               "qa": {"passed": all(r["layer"]["qa"]["passed"] for r in results.values())
                      and max(largest) <= MAX_TEX}}
        wj = await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode(), name=WEATHER_FILE,
                                       mime="application/json")
        ctx.progress(total, total)
        return StepResult(outputs={"images": [r for k, r in textures.items() if not k.endswith("_sheet")],
                                   "sheets": sheets, "preview": [prev], "weather": [wj]},
                          meta={"kinds": kinds, "view": p["view"], "qa": doc["qa"], "largest": largest})


def _png_size(ctx: StepContext, ref: BlobRef) -> tuple[int, int]:
    with Image.open(ctx.blobs.path(ref.hash)) as im:
        return im.size


WEATHER_OPS = (WeatherOverlay,)
