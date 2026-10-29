"""Particle VFX pack (vfx-particles). `op.vfx_style_plan` turns the optional style text into Z-Image prompts (none when
blank, so the ComfyUI step runs zero times); `op.vfx_particles` draws the procedural textures (soft dot, spark, smoke
puffs 2x2, ring, slash arc, star, magic rune), turns styled Z-Image renders on black into an additive 2x2 sheet, and
writes engine-neutral emitter presets to vfx_particles.json. A small CPU simulator of the same emitter model renders
the preview and bakes hit_spark into a flipbook for the hit-FX export (#38).

Units: world units, y up (1 unit = ppu pixels in 2D engines); gravity is the downward acceleration in units/s²; drag
is linear (v' = -drag * v); size_curve peaks at 1 so `size` is the largest a particle gets."""
from __future__ import annotations

import asyncio
import colorsys
import copy
import json
import math
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png

VFX_FILE = "vfx_particles.json"
VFX_TYPE = "asset-studio/vfx-particles"
PLAN_FILE = "vfx_plan.json"
SIZES = (64, 128, 256)
STYLED = "styled"
HIT_PRESET = "hit_spark"
HIT_FPS = 24
PREVIEW_CELL = 256
TEXTURES = ("soft_dot", "spark", "smoke", "ring", "slash_arc", "star", "magic_rune")
# Textures drawn as a 2x2 sheet of variants; particles pick one at random.
SHEETS = {"smoke": (2, 2), STYLED: (2, 2)}
STYLED_BATCH = 4


def _e(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"blend": "add", "delay": 0.0, "burst": 0, "rate": 0.0, "lifetime": [0.5, 1.0],
                            "speed": [1.0, 2.0], "shape": {"type": "point"}, "gravity": 0.0, "drag": 0.0,
                            "size": [0.2, 0.3], "size_curve": [[0, 1], [1, 1]], "rotation": [0, 0], "spin": [0, 0],
                            "align": "billboard", "space": "local", "styled": False}
    base.update(kw)
    return base


FADE = [[0, "#ffffffff"], [1, "#ffffff00"]]

PRESETS: dict[str, dict[str, Any]] = {
    "explosion": {"label": "Nổ", "loop": False, "duration": 1.6, "emitters": [
        _e(name="flash", texture="soft_dot", burst=1, lifetime=[0.16, 0.16], speed=[0, 0], size=[2.4, 2.4],
           size_curve=[[0, 0.55], [1, 1]], colors=[[0, "#fffbe8ff"], [0.5, "#ffd890a0"], [1, "#ff904000"]]),
        _e(name="fire", texture="smoke", burst=26, lifetime=[0.35, 0.75], speed=[1.5, 4.0],
           shape={"type": "sphere", "radius": 0.15}, drag=4.0, size=[0.7, 1.3], size_curve=[[0, 0.35], [0.3, 0.9],
                                                                                            [1, 1]],
           rotation=[0, 360], spin=[-90, 90], styled=True,
           colors=[[0, "#fff4c8ff"], [0.2, "#ffb030ff"], [0.55, "#e04414b0"], [1, "#50140000"]]),
        _e(name="sparks", texture="spark", burst=30, lifetime=[0.3, 0.8], speed=[4.0, 9.0],
           shape={"type": "sphere", "radius": 0.1}, gravity=6.0, drag=1.5, size=[0.18, 0.3], align="velocity",
           colors=[[0, "#fff8d8ff"], [0.4, "#ffa030ff"], [1, "#ff401000"]]),
        _e(name="smoke", texture="smoke", blend="alpha", delay=0.1, burst=10, lifetime=[0.9, 1.5], speed=[0.6, 1.6],
           shape={"type": "sphere", "radius": 0.3}, gravity=-0.8, drag=1.5, size=[1.0, 1.6],
           size_curve=[[0, 0.45], [1, 1]], rotation=[0, 360], spin=[-40, 40],
           colors=[[0, "#3a343000"], [0.15, "#3a3430c0"], [1, "#26222000"]]),
    ]},
    "heal": {"label": "Hồi máu", "loop": True, "duration": 2.0, "emitters": [
        _e(name="glow", texture="soft_dot", rate=18, lifetime=[0.8, 1.4], speed=[0.6, 1.2],
           shape={"type": "cone", "radius": 0.5, "angle": 10}, gravity=-0.3, size=[0.14, 0.26],
           size_curve=[[0, 0], [0.2, 1], [1, 0]],
           colors=[[0, "#d8ffd8ff"], [0.5, "#50ff90e0"], [1, "#20c06000"]]),
        _e(name="sparkle", texture="star", rate=5, lifetime=[0.9, 1.3], speed=[0.5, 0.9],
           shape={"type": "cone", "radius": 0.45, "angle": 5}, size=[0.2, 0.3], size_curve=[[0, 0], [0.25, 1],
                                                                                           [1, 0.2]],
           spin=[-60, 60], colors=[[0, "#ffffffff"], [0.5, "#a0ffb8ff"], [1, "#40ff8000"]]),
    ]},
    "pickup": {"label": "Nhặt đồ", "loop": False, "duration": 0.9, "emitters": [
        _e(name="ring", texture="ring", burst=1, lifetime=[0.45, 0.45], speed=[0, 0], size=[1.4, 1.4],
           size_curve=[[0, 0.2], [1, 1]], colors=[[0, "#fff8c0ff"], [1, "#ffd04000"]]),
        _e(name="stars", texture="star", burst=12, lifetime=[0.4, 0.7], speed=[1.5, 3.0],
           shape={"type": "circle", "radius": 0.1}, drag=3.0, size=[0.16, 0.28], size_curve=[[0, 1], [1, 0.3]],
           spin=[-180, 180], colors=[[0, "#ffffffff"], [0.4, "#ffe060ff"], [1, "#ffb02000"]]),
        _e(name="glints", texture="soft_dot", burst=16, lifetime=[0.5, 0.9], speed=[0.5, 2.0],
           shape={"type": "cone", "radius": 0.3, "angle": 30}, gravity=-1.0, drag=1.0, size=[0.06, 0.12],
           colors=[[0, "#fff6c0ff"], [1, "#ffc04000"]]),
    ]},
    "projectile_trail": {"label": "Vệt đạn", "loop": True, "duration": 1.0,
                         "trail": {"time": 0.25, "width": 0.22, "colors": [[0, "#c0f4ffff"], [1, "#2080ff00"]]},
                         "emitters": [
        _e(name="core", texture="soft_dot", rate=40, lifetime=[0.25, 0.45], speed=[0, 0.3],
           shape={"type": "sphere", "radius": 0.05}, size=[0.3, 0.45], size_curve=[[0, 1], [1, 0]], space="world",
           styled=True, colors=[[0, "#e8fcffff"], [0.4, "#60d0ffd0"], [1, "#2060ff00"]]),
        _e(name="sparkles", texture="spark", rate=20, lifetime=[0.3, 0.6], speed=[0.5, 1.2],
           shape={"type": "sphere", "radius": 0.08}, size=[0.08, 0.14], align="velocity", space="world",
           colors=[[0, "#ffffffff"], [1, "#60c0ff00"]]),
    ]},
    "dust_step": {"label": "Bụi bước chân", "loop": False, "duration": 1.0, "emitters": [
        _e(name="puffs", texture="smoke", blend="alpha", burst=8, lifetime=[0.45, 0.8], speed=[0.4, 1.2],
           shape={"type": "cone", "radius": 0.25, "angle": 70}, gravity=0.6, drag=3.0, size=[0.3, 0.5],
           size_curve=[[0, 0.45], [1, 1]], rotation=[0, 360], spin=[-30, 30],
           colors=[[0, "#c8b89ad0"], [1, "#a8987c00"]]),
    ]},
    "hit_spark": {"label": "Tia va chạm", "loop": False, "duration": 0.45, "emitters": [
        _e(name="flash", texture="soft_dot", burst=1, lifetime=[0.1, 0.1], speed=[0, 0], size=[0.9, 0.9],
           size_curve=[[0, 0.6], [1, 1]], colors=[[0, "#ffffffff"], [1, "#ffe0a000"]]),
        _e(name="slash", texture="slash_arc", burst=1, lifetime=[0.18, 0.18], speed=[0, 0], size=[0.9, 0.9],
           size_curve=[[0, 0.6], [1, 1]], rotation=[0, 360], colors=[[0, "#fffef0ff"], [1, "#ffc06000"]]),
        _e(name="sparks", texture="spark", burst=16, lifetime=[0.15, 0.35], speed=[4.0, 8.0],
           shape={"type": "sphere", "radius": 0.02}, drag=4.0, size=[0.16, 0.26], align="velocity",
           colors=[[0, "#ffffffff"], [0.5, "#ffd060ff"], [1, "#ff802000"]]),
    ]},
    "magic_aura": {"label": "Hào quang phép", "loop": True, "duration": 2.0, "emitters": [
        _e(name="rune", texture="magic_rune", rate=1, lifetime=[2.0, 2.0], speed=[0, 0], size=[1.8, 1.8],
           spin=[30, 30], colors=[[0, "#b070ff00"], [0.25, "#c890ffd0"], [0.75, "#c890ffd0"], [1, "#b070ff00"]]),
        _e(name="motes", texture="soft_dot", rate=20, lifetime=[0.9, 1.5], speed=[0.2, 0.5],
           shape={"type": "circle", "radius": 0.7, "thickness": 0}, gravity=-0.6, size=[0.07, 0.14],
           size_curve=[[0, 0], [0.2, 1], [1, 0]], colors=[[0, "#f0e0ffff"], [1, "#9040ff00"]]),
        _e(name="wisps", texture="smoke", rate=6, lifetime=[1.2, 1.8], speed=[0.1, 0.3],
           shape={"type": "circle", "radius": 0.5}, gravity=-0.3, size=[0.5, 0.8], size_curve=[[0, 0.3], [1, 1]],
           rotation=[0, 360], spin=[-20, 20], styled=True,
           colors=[[0, "#a060ff00"], [0.3, "#a060ff60"], [1, "#6020c000"]]),
    ]},
}
DEFAULT_PRESETS = tuple(PRESETS)
DEFAULT_STYLED_FOR = ("explosion", "magic_aura", "projectile_trail")


def parse_list(value: Any, allowed: tuple[str, ...], what: str) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").replace(";", ",").replace("\n", ",").split(",")
    out = []
    for v in raw:
        k = str(v).strip().lower().replace(" ", "_").replace("-", "_")
        if not k:
            continue
        if k not in allowed:
            raise StepError("op_bad_params", f"unknown {what} {k!r} (use {', '.join(allowed)})")
        if k not in out:
            out.append(k)
    return out


def _grid(n: int) -> tuple[np.ndarray, np.ndarray]:
    c = (np.arange(n, dtype=np.float32) + 0.5) / n * 2 - 1
    return np.meshgrid(c, c)


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def _rgba(alpha: np.ndarray, shade: np.ndarray | None = None) -> Image.Image:
    a = np.clip(alpha, 0, 1)
    rgb = np.ones(a.shape + (3,), np.float32) if shade is None else np.repeat(np.clip(shade, 0, 1)[..., None], 3, 2)
    return Image.fromarray(np.round(np.dstack([rgb, a]) * 255).astype(np.uint8), "RGBA")


def _noise(n: int, rng: np.random.Generator, octaves: int = 4) -> np.ndarray:
    out = np.zeros((n, n), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        g = 4 * 2 ** o
        cells = Image.fromarray((rng.random((g, g)) * 255).astype(np.uint8), "L")
        out += amp * np.asarray(cells.resize((n, n), Image.Resampling.BICUBIC), np.float32) / 255
        total += amp
        amp *= 0.5
    return out / total


def soft_dot(n: int) -> Image.Image:
    x, y = _grid(n)
    r = np.hypot(x, y)
    return _rgba(np.exp(-r * r * 4.0) * (1 - _smoothstep(0.75, 1.0, r)))


def spark(n: int) -> Image.Image:
    """Vertical streak: engines align the texture's Y axis to the velocity (Godot align_y, Unity stretched)."""
    x, y = _grid(n)
    body = np.exp(-(x / 0.07) ** 2) * np.exp(-(y / 0.7) ** 2)
    core = np.exp(-(x / 0.035) ** 2) * np.exp(-(y / 0.45) ** 2)
    return _rgba(np.clip(body * 0.8 + core, 0, 1) * (1 - _smoothstep(0.85, 1.0, np.abs(y))))


def smoke_puff(n: int, rng: np.random.Generator) -> Image.Image:
    x, y = _grid(n)
    r = np.hypot(x, y)
    nz = _noise(n, rng)
    edge = 0.62 + 0.38 * (nz - 0.5) * 2
    a = (1 - _smoothstep(edge * 0.55, edge, r)) * (0.55 + 0.45 * nz)
    shade = 0.78 + 0.22 * (-y * 0.5 + 0.5) * (0.7 + 0.3 * nz)
    return _rgba(a * (1 - _smoothstep(0.85, 0.98, r)), shade)


def ring(n: int) -> Image.Image:
    x, y = _grid(n)
    r = np.hypot(x, y)
    return _rgba(np.exp(-((r - 0.72) / 0.07) ** 2) + 0.18 * np.exp(-((r - 0.6) / 0.2) ** 2) * (r < 0.72))


def slash_arc(n: int) -> Image.Image:
    x, y = _grid(n)
    r = np.hypot(x, y)
    th = np.arctan2(y, x)
    span = math.radians(110)
    t = np.clip(1 - np.abs(th) / span, 0, 1)
    width = 0.02 + 0.13 * np.sin(t * math.pi / 2) ** 1.5
    a = np.exp(-((r - 0.68) / np.maximum(width, 1e-3)) ** 2) * (t > 0) * _smoothstep(0, 0.35, t)
    return _rgba(a * (1 - _smoothstep(0.9, 1.0, r)))


def star(n: int) -> Image.Image:
    x, y = _grid(n)
    ax, ay = np.abs(x), np.abs(y)
    rays = np.maximum(np.exp(-(ax * 14 + ay * 2.2)), np.exp(-(ay * 14 + ax * 2.2)))
    d = (x + y) / math.sqrt(2), (x - y) / math.sqrt(2)
    diag = 0.45 * np.maximum(np.exp(-(np.abs(d[0]) * 18 + np.abs(d[1]) * 4.5)),
                             np.exp(-(np.abs(d[1]) * 18 + np.abs(d[0]) * 4.5)))
    r = np.hypot(x, y)
    return _rgba(np.clip(rays + diag + np.exp(-r * r * 30), 0, 1) * (1 - _smoothstep(0.85, 1.0, r)))


def magic_rune(n: int) -> Image.Image:
    s = 4 * n
    img = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(img)
    c = s / 2
    w = max(2, s // 90)

    def circle(rr: float, width: int) -> None:
        d.ellipse((c - rr * c, c - rr * c, c + rr * c, c + rr * c), outline=255, width=width)

    circle(0.86, w * 2)
    circle(0.74, w)
    circle(0.42, w)
    for k in range(24):
        a = 2 * math.pi * k / 24
        r0, r1 = (0.76, 0.84) if k % 2 else (0.76, 0.81)
        d.line((c + r0 * c * math.cos(a), c + r0 * c * math.sin(a), c + r1 * c * math.cos(a),
                c + r1 * c * math.sin(a)), fill=255, width=w)
    for off in (0.0, math.pi):
        pts = [(c + 0.72 * c * math.cos(off + math.pi / 2 + 2 * math.pi * i / 3),
                c + 0.72 * c * math.sin(off + math.pi / 2 + 2 * math.pi * i / 3)) for i in range(3)]
        d.line(pts + pts[:1], fill=255, width=w)
    for k in range(6):
        a = 2 * math.pi * k / 6 + math.pi / 6
        px, py = c + 0.6 * c * math.cos(a), c + 0.6 * c * math.sin(a)
        rr = 0.05 * c
        d.ellipse((px - rr, py - rr, px + rr, py + rr), outline=255, width=w)
    sharp = img.resize((n, n), Image.Resampling.LANCZOS)
    glow = sharp.filter(ImageFilter.GaussianBlur(max(1.0, n / 40)))
    a = np.clip(np.asarray(sharp, np.float32) / 255 + 0.6 * np.asarray(glow, np.float32) / 255, 0, 1)
    x, y = _grid(n)
    return _rgba(a * (1 - _smoothstep(0.92, 1.0, np.hypot(x, y))))


def sheet(cells: list[Image.Image], cols: int, rows: int) -> Image.Image:
    n = cells[0].width
    out = Image.new("RGBA", (cols * n, rows * n), (0, 0, 0, 0))
    for i, c in enumerate(cells[:cols * rows]):
        out.paste(c, ((i % cols) * n, (i // cols) * n))
    return out


def procedural(n: int, seed: int = 7) -> dict[str, Image.Image]:
    rng = np.random.default_rng(seed)
    return {"soft_dot": soft_dot(n), "spark": spark(n), "smoke": sheet([smoke_puff(n, rng) for _ in range(4)], 2, 2),
            "ring": ring(n), "slash_arc": slash_arc(n), "star": star(n), "magic_rune": magic_rune(n)}


def styled_cell(img: Image.Image, n: int) -> Image.Image | None:
    """A render on black → white-keyed RGBA: alpha from brightness, colour un-premultiplied, edges faded so nothing
    the model drew near the border shows as a hard square. None when the render is (almost) empty or fills the frame."""
    a = np.asarray(img.convert("RGB").resize((n, n), Image.Resampling.LANCZOS), np.float32) / 255
    lum = a.max(2)
    alpha = _smoothstep(0.04, 0.6, lum)
    cover = float((alpha > 0.1).mean())
    x, y = _grid(n)
    alpha = alpha * (1 - _smoothstep(0.7, 0.98, np.hypot(x, y)))
    if not 0.02 <= cover <= 0.75:
        return None
    rgb = np.clip(a / np.maximum(lum, 1e-3)[..., None], 0, 1)
    return Image.fromarray(np.round(np.dstack([rgb, alpha]) * 255).astype(np.uint8), "RGBA")


def _hex_rgba(h: str) -> tuple[float, float, float, float]:
    s = h.lstrip("#")
    v = [int(s[i:i + 2], 16) / 255 for i in range(0, len(s), 2)]
    return (v[0], v[1], v[2], v[3] if len(v) > 3 else 1.0)


def _to_hex(c: tuple[float, ...]) -> str:
    return "#" + "".join(f"{int(round(max(0.0, min(1.0, v)) * 255)):02x}" for v in c)


def parse_tint(value: Any) -> float | None:
    """Hue of a #rrggbb tint, None for auto."""
    s = str(value or "auto").strip().lower()
    if s in ("", "auto", "none"):
        return None
    h = s.lstrip("#")
    if len(h) != 6 or any(ch not in "0123456789abcdef" for ch in h):
        raise StepError("op_bad_params", f"tint must be auto or #rrggbb, got {value!r}")
    r, g, b, _ = _hex_rgba(h)
    return colorsys.rgb_to_hsv(r, g, b)[0]


def tinted(keys: list[list[Any]], hue: float) -> list[list[Any]]:
    out = []
    for t, h in keys:
        r, g, b, a = _hex_rgba(h)
        hh, s, v = colorsys.rgb_to_hsv(r, g, b)
        if s > 0.12:
            r, g, b = colorsys.hsv_to_rgb(hue, s, v)
        out.append([t, _to_hex((r, g, b, a))])
    return out


def build_presets(names: list[str], hue: float | None, styled_for: list[str], has_styled: bool
                  ) -> list[dict[str, Any]]:
    out = []
    for name in names:
        p = copy.deepcopy(PRESETS[name])
        p["name"] = name
        for em in p["emitters"]:
            use_styled = has_styled and em.pop("styled") and name in styled_for
            if use_styled:
                em["texture"] = STYLED
                em["blend"] = "add"
                # The styled texture carries its own colour; keep only the alpha curve.
                em["colors"] = [[t, "#ffffff" + h[7:9]] for t, h in em["colors"]]
            elif hue is not None:
                em["colors"] = tinted(em["colors"], hue)
        if hue is not None and p.get("trail"):
            p["trail"]["colors"] = tinted(p["trail"]["colors"], hue)
        out.append(p)
    return out


def _interp(keys: list[list[Any]], u: np.ndarray) -> np.ndarray:
    ts = np.array([float(k[0]) for k in keys])
    vs = np.array([float(k[1]) for k in keys])
    return np.interp(u, ts, vs)


def _gradient(keys: list[list[Any]], u: np.ndarray) -> np.ndarray:
    ts = np.array([float(k[0]) for k in keys])
    cols = np.array([_hex_rgba(k[1]) for k in keys])
    return np.stack([np.interp(u, ts, cols[:, i]) for i in range(4)], -1)


def _rand(rng: np.random.Generator, pair: list[float], n: int) -> np.ndarray:
    return rng.uniform(float(pair[0]), float(pair[1]), n)


def spawn(em: dict[str, Any], rng: np.random.Generator, t_end: float, loop: bool, duration: float
          ) -> dict[str, np.ndarray]:
    t0 = [float(em["delay"])] * int(em["burst"])
    rate = float(em["rate"])
    if rate > 0:
        start = float(em["delay"]) - (float(em["lifetime"][1]) if loop else 0.0)
        stop = t_end if loop else float(em["delay"]) + duration
        t0 += list(np.arange(start, stop, 1.0 / rate))
    n = len(t0)
    sh = em["shape"]
    kind, radius = sh.get("type", "point"), float(sh.get("radius", 0.0))
    if kind == "sphere":
        d3 = rng.normal(size=(n, 3))
        d3 /= np.maximum(np.linalg.norm(d3, axis=1, keepdims=True), 1e-6)
        pos = d3[:, :2] * radius * rng.random((n, 1)) ** (1 / 3)
        dirs = d3[:, :2]
    elif kind == "circle":
        ang = rng.uniform(0, 2 * math.pi, n)
        thick = float(sh.get("thickness", 1.0))
        rr = radius * (1 - thick * (1 - np.sqrt(rng.random(n))))
        dirs = np.stack([np.cos(ang), np.sin(ang)], 1)
        pos = dirs * rr[:, None]
    elif kind == "cone":
        pos = np.stack([rng.uniform(-radius, radius, n), np.zeros(n)], 1)
        ang = math.pi / 2 + np.radians(rng.uniform(-1, 1, n) * float(sh.get("angle", 0.0)))
        dirs = np.stack([np.cos(ang), np.sin(ang)], 1)
    else:
        ang = rng.uniform(0, 2 * math.pi, n)
        pos = np.zeros((n, 2))
        dirs = np.stack([np.cos(ang), np.sin(ang)], 1)
    cols, rows = SHEETS.get(em["texture"], (1, 1))
    return {"t0": np.array(t0, np.float64), "life": _rand(rng, em["lifetime"], n), "pos": pos,
            "vel": dirs * _rand(rng, em["speed"], n)[:, None], "size": _rand(rng, em["size"], n),
            "rot": _rand(rng, em["rotation"], n), "spin": _rand(rng, em["spin"], n),
            "frame": rng.integers(0, cols * rows, n) if n else np.zeros(0, int)}


def state_at(em: dict[str, Any], p: dict[str, np.ndarray], t: float) -> dict[str, np.ndarray]:
    age = t - p["t0"]
    alive = (age >= 0) & (age < p["life"])
    a = age[alive]
    g = np.array([0.0, -float(em["gravity"])])
    k = float(em["drag"])
    v0, p0 = p["vel"][alive], p["pos"][alive]
    if k > 1e-6:
        e = np.exp(-k * a)[:, None]
        term = g / k
        pos = p0 + term * a[:, None] + (v0 - term) * (1 - e) / k
        vel = (v0 - term) * e + term
    else:
        pos = p0 + v0 * a[:, None] + 0.5 * g * (a * a)[:, None]
        vel = v0 + g * a[:, None]
    u = a / p["life"][alive]
    return {"pos": pos, "vel": vel, "u": u, "size": p["size"][alive] * _interp(em["size_curve"], u),
            "rot": p["rot"][alive] + p["spin"][alive] * a, "color": _gradient(em["colors"], u),
            "frame": p["frame"][alive]}


class Renderer:
    """Composites particles in premultiplied float RGBA; cell textures resized/rotated per particle and cached."""

    def __init__(self, textures: dict[str, Image.Image], w: int, h: int) -> None:
        self.tex = {k: np.asarray(v.convert("RGBA"), np.float32) / 255 for k, v in textures.items()}
        self.w, self.h = w, h
        self.cache: dict[tuple[Any, ...], np.ndarray] = {}
        self.rgb = np.zeros((h, w, 3), np.float32)
        self.a = np.zeros((h, w), np.float32)

    def _cell(self, key: str, frame: int) -> np.ndarray:
        t = self.tex[key]
        cols, rows = SHEETS.get(key, (1, 1))
        cw, ch = t.shape[1] // cols, t.shape[0] // rows
        x, y = (frame % cols) * cw, (frame // cols) * ch
        return t[y:y + ch, x:x + cw]

    def sprite(self, key: str, frame: int, px: int, angle: float) -> np.ndarray:
        ck = (key, frame, px, int(round(angle / 6)) % 60)
        hit = self.cache.get(ck)
        if hit is None:
            cell = Image.fromarray(np.round(self._cell(key, frame) * 255).astype(np.uint8), "RGBA")
            img = cell.resize((px, px), Image.Resampling.BILINEAR)
            if ck[3]:
                img = img.rotate(ck[3] * 6, Image.Resampling.BILINEAR, expand=True)
            hit = np.asarray(img, np.float32) / 255
            self.cache[ck] = hit
        return hit

    def draw(self, em: dict[str, Any], st: dict[str, np.ndarray], origin: tuple[float, float], ppx: float) -> None:
        add = em["blend"] == "add"
        for i in range(len(st["u"])):
            px = int(round(float(st["size"][i]) * ppx))
            if px < 2:
                continue
            if em["align"] == "velocity":
                vx, vy = st["vel"][i]
                angle = math.degrees(math.atan2(vy, vx)) - 90 if abs(vx) + abs(vy) > 1e-6 else 0.0
            else:
                angle = float(st["rot"][i])
            spr = self.sprite(em["texture"], int(st["frame"][i]), px, angle)
            c = st["color"][i]
            sa = spr[..., 3] * c[3]
            src = spr[..., :3] * c[:3] * sa[..., None]
            cx = origin[0] + float(st["pos"][i][0]) * ppx
            cy = origin[1] - float(st["pos"][i][1]) * ppx
            x0, y0 = int(round(cx - spr.shape[1] / 2)), int(round(cy - spr.shape[0] / 2))
            x1, y1 = max(x0, 0), max(y0, 0)
            x2, y2 = min(x0 + spr.shape[1], self.w), min(y0 + spr.shape[0], self.h)
            if x2 <= x1 or y2 <= y1:
                continue
            s_rgb, s_a = src[y1 - y0:y2 - y0, x1 - x0:x2 - x0], sa[y1 - y0:y2 - y0, x1 - x0:x2 - x0]
            d_rgb, d_a = self.rgb[y1:y2, x1:x2], self.a[y1:y2, x1:x2]
            if add:
                d_rgb += s_rgb
                d_a += s_a * (1 - d_a)
            else:
                d_rgb *= (1 - s_a)[..., None]
                d_rgb += s_rgb
                d_a *= 1 - s_a
                d_a += s_a

    def image(self, background: tuple[int, int, int] | None) -> Image.Image:
        if background is not None:
            bg = np.array(background, np.float32) / 255
            rgb = np.clip(self.rgb + bg * (1 - np.clip(self.a, 0, 1))[..., None], 0, 1)
            return Image.fromarray(np.round(rgb * 255).astype(np.uint8), "RGB")
        a = np.clip(self.a, 0, 1)
        rgb = np.clip(self.rgb / np.maximum(a, 1e-4)[..., None], 0, 1)
        return Image.fromarray(np.round(np.dstack([rgb, a]) * 255).astype(np.uint8), "RGBA")


def draw_order(emitters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Alpha-blended emitters (smoke, dust) behind the additive ones; engines use the same order."""
    return sorted(emitters, key=lambda em: em["blend"] != "alpha")


def simulate(preset: dict[str, Any], seed: int = 1) -> list[tuple[dict[str, Any], dict[str, np.ndarray]]]:
    rng = np.random.default_rng(seed)
    t_end = float(preset["duration"]) + 2.0
    return [(em, spawn(em, rng, t_end, bool(preset["loop"]), float(preset["duration"])))
            for em in draw_order(preset["emitters"])]


def show_time(preset: dict[str, Any]) -> float:
    return 1.0 if preset["loop"] else min(0.3, float(preset["duration"]) * 0.3)


def extent(preset: dict[str, Any], sims: list[tuple[dict[str, Any], dict[str, np.ndarray]]]) -> float:
    """Half-size (units) of a square holding the effect over its life."""
    ext = 0.3
    span = float(preset["duration"]) if not preset["loop"] else 1.5
    for t in np.linspace(0.02, span, 12):
        for em, p in sims:
            st = state_at(em, p, float(t))
            if len(st["u"]):
                r = np.abs(st["pos"]).max(1) + st["size"] * 0.5
                ext = max(ext, float(np.percentile(r, 95)))
    return ext


def render(preset: dict[str, Any], textures: dict[str, Image.Image], t: float, cell: int,
           background: tuple[int, int, int] | None, sims: list[tuple[dict[str, Any], dict[str, np.ndarray]]] | None
           = None, half: float | None = None) -> Image.Image:
    sims = sims or simulate(preset)
    half = half or extent(preset, sims)
    ppx = cell / (2 * half * 1.08)
    r = Renderer(textures, cell, cell)
    for em, p in sims:
        r.draw(em, state_at(em, p, t), (cell / 2, cell / 2), ppx)
    return r.image(background)


def flipbook(preset: dict[str, Any], textures: dict[str, Image.Image], cell: int, fps: int = HIT_FPS
             ) -> list[Image.Image]:
    sims = simulate(preset, seed=3)
    half = extent(preset, sims)
    n = max(4, int(round(float(preset["duration"]) * fps)))
    return [render(preset, textures, (i + 0.5) / fps, cell, None, sims, half) for i in range(n)]


BG = (30, 30, 33)


def preview(presets: list[dict[str, Any]], textures: dict[str, Image.Image]) -> Image.Image:
    cols = 4
    rows = math.ceil(len(presets) / cols)
    tw = 96
    tex_row = math.ceil(len(textures) / 8) * (tw + 20)
    out = Image.new("RGB", (cols * PREVIEW_CELL, tex_row + rows * (PREVIEW_CELL + 22)), BG)
    d = ImageDraw.Draw(out)
    for i, (k, img) in enumerate(textures.items()):
        x, y = (i % 8) * (tw + 32) + 8, (i // 8) * (tw + 20) + 2
        thumb = img.convert("RGBA")
        thumb.thumbnail((tw, tw))
        out.paste(thumb, (x, y), thumb)
        d.text((x, y + tw + 4), k, fill=(170, 170, 180))
    for i, p in enumerate(presets):
        x, y = (i % cols) * PREVIEW_CELL, tex_row + (i // cols) * (PREVIEW_CELL + 22)
        out.paste(render(p, textures, show_time(p), PREVIEW_CELL, BG), (x, y))
        d.text((x + 8, y + PREVIEW_CELL + 4), f"{p['name']} ({'loop' if p['loop'] else 'one-shot'})",
               fill=(220, 220, 230))
    return out


def _texture_doc(key: str, ref: BlobRef) -> dict[str, Any]:
    cols, rows = SHEETS.get(key, (1, 1))
    return {"file": ref.name, "hash": ref.hash, "cols": cols, "rows": rows}


def _asset(params: dict[str, Any]) -> str:
    from .enemy_variants import slug

    return slug(str(params.get("asset") or "vfx")) or "vfx"


def style_prompts(style: str) -> list[str]:
    s = " ".join(style.split())
    if not s:
        return []
    return [f"A single isolated particle texture for a game visual effect: one soft glowing puff of {s}, centered, "
            "filling about half of the frame with empty space around it, fading out softly at its edges, bright "
            "emissive colours on a pure solid black background, no ground, no objects, no text, no frame."]


class VfxStylePlan(OpExecutor):
    kind = "op.vfx_style_plan"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        prompts = style_prompts(str(params.get("style") or ""))
        doc = {"prompts": prompts, "batch": STYLED_BATCH if prompts else 0}
        ref = await ctx.blobs.put_bytes(json.dumps(doc, ensure_ascii=False).encode(), name=PLAN_FILE,
                                        mime="application/json")
        return StepResult(outputs={"plan": [ref]}, meta={"styled": bool(prompts)})


class VfxParticles(OpExecutor):
    kind = "op.vfx_particles"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        asset = _asset(params)
        names = parse_list(params.get("presets") or ",".join(DEFAULT_PRESETS), DEFAULT_PRESETS, "preset")
        if not names:
            raise StepError("op_bad_params", "pick at least one preset")
        styled_for = parse_list(params.get("styled_for", ",".join(DEFAULT_STYLED_FOR)), DEFAULT_PRESETS, "preset")
        hue = parse_tint(params.get("tint"))
        try:
            size = int(params.get("size") or 128)
        except (TypeError, ValueError):
            raise StepError("op_bad_params", f"size must be one of {SIZES}") from None
        if size not in SIZES:
            raise StepError("op_bad_params", f"size must be one of {SIZES}")
        ctx.progress(0, 4, label="textures")
        textures = await asyncio.to_thread(procedural, size, int(params.get("seed") or 7))
        styled_in = inputs.get("styled") or []
        cells = []
        for ref in styled_in:
            c = styled_cell(await _load(ctx, ref), size)
            if c is not None:
                cells.append(c)
        accepted = len(cells)
        if cells:
            cells = (cells * STYLED_BATCH)[:STYLED_BATCH]
            textures[STYLED] = sheet(cells, *SHEETS[STYLED])
        presets = build_presets(names, hue, styled_for, bool(cells))
        # The projectile's TrailRenderer (Unity) draws with the soft dot even when its particles use the styled sheet.
        trail = any(p.get("trail") for p in presets)
        used = [k for k in (*TEXTURES, STYLED) if k in textures and (any(
            em["texture"] == k for p in presets for em in p["emitters"]) or (trail and k == "soft_dot"))]
        ctx.progress(1, 4, label="save")
        refs = {k: await _put_png(ctx, textures[k], f"{asset}_{k}.png") for k in used}
        ctx.progress(2, 4, label="preview")
        prev = await asyncio.to_thread(preview, presets, {k: textures[k] for k in used})
        prev_ref = await _put_png(ctx, prev, f"{asset}_vfx_preview.png")
        ctx.progress(3, 4, label="hit flipbook")
        hit = copy.deepcopy(PRESETS[HIT_PRESET]) | {"name": HIT_PRESET}
        if hue is not None:
            for em in hit["emitters"]:
                em["colors"] = tinted(em["colors"], hue)
        frames = await asyncio.to_thread(flipbook, hit, textures, size)
        frame_refs = [await _put_png(ctx, f, f"{asset}_hit_spark_{i:02d}.png") for i, f in enumerate(frames)]
        doc = {"type": VFX_TYPE, "version": 1, "asset": asset, "texture_size": size,
               "units": "world units, y up; 2D engines multiply by ppu. gravity = downward acceleration (units/s²), "
                        "drag = linear velocity damping per second, rotation/spin in degrees, size_curve peaks at 1.",
               "tint": params.get("tint") or "auto", "styled": bool(cells),
               "styled_rejected": len(styled_in) - accepted,
               "textures": {k: _texture_doc(k, r) for k, r in refs.items()},
               "presets": presets, "hit": {"preset": HIT_PRESET, "frames": len(frames), "fps": HIT_FPS}}
        vfx_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode(), name=VFX_FILE,
                                            mime="application/json")
        ctx.progress(4, 4)
        return StepResult(outputs={"images": list(refs.values()), "preview": [prev_ref], "vfx": [vfx_ref],
                                   "hit_frames": frame_refs},
                          meta={"presets": names, "textures": used, "styled": bool(cells),
                                "hit_frames": len(frames)})


VFX_PARTICLE_OPS = (VfxStylePlan, VfxParticles)
