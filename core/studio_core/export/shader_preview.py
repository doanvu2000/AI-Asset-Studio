"""CPU previews of the sprite shader presets: numpy ports of shader_templates/*.gdshader run over a built-in sprite and
saved as a looping WebP. Ports are for looks only; the engine shaders stay the reference."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from studio_core.export.shaders import PRESETS, Preset, parse_color

SIZE = 96
FRAMES = 24
MIN_FRAME_MS = 40
MAX_LOOP_S = 3.0
TAU = np.float32(2 * np.pi)
LUM = np.array([0.299, 0.587, 0.114], np.float32)
# Presets that do nothing without speed get one here, so the preview shows the effect.
DEMO_SPEED = {"hit_flash": 2.0, "dissolve": 0.8, "palette_swap": 1.0}
_VERSION = 1


def sample_sprite() -> np.ndarray:
    """(SIZE, SIZE, 4) float RGBA: a small slime with eyes, grounded at the bottom with a border for outlines."""
    scale = 4
    img = Image.new("RGBA", (SIZE * scale, SIZE * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = scale
    d.ellipse((22 * s, 30 * s, 74 * s, 84 * s), fill=(86, 176, 96, 255), outline=(38, 98, 52, 255), width=2 * s)
    d.pieslice((22 * s, 20 * s, 74 * s, 70 * s), 180, 360, fill=(86, 176, 96, 255))
    d.rectangle((24 * s, 66 * s, 72 * s, 84 * s), fill=(86, 176, 96, 255))
    d.ellipse((34 * s, 40 * s, 44 * s, 52 * s), fill=(255, 255, 255, 255))
    d.ellipse((52 * s, 40 * s, 62 * s, 52 * s), fill=(255, 255, 255, 255))
    d.ellipse((38 * s, 45 * s, 43 * s, 51 * s), fill=(24, 32, 40, 255))
    d.ellipse((55 * s, 45 * s, 60 * s, 51 * s), fill=(24, 32, 40, 255))
    d.arc((38 * s, 52 * s, 58 * s, 66 * s), 20, 160, fill=(24, 32, 40, 255), width=2 * s)
    d.ellipse((30 * s, 32 * s, 40 * s, 38 * s), fill=(176, 232, 184, 255))
    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    return np.asarray(img, np.float32) / 255.0


def _premul(img: np.ndarray) -> np.ndarray:
    return np.concatenate([img[..., :3] * img[..., 3:], img[..., 3:]], axis=-1)


def _unpremul(img: np.ndarray) -> np.ndarray:
    a = img[..., 3:]
    return np.concatenate([img[..., :3] / np.maximum(a, 1e-4), a], axis=-1)


def _shift_rows(img: np.ndarray, offset: np.ndarray) -> np.ndarray:
    """Output pixel x samples the sprite at x + offset[y] (linear, premultiplied, clamped like Godot's default)."""
    h, w, _ = img.shape
    pm = _premul(img)
    xs = np.arange(w, dtype=np.float32)[None, :] + offset[:, None]
    x0 = np.floor(xs)
    f = (xs - x0)[..., None]
    i0 = np.clip(x0.astype(int), 0, w - 1)
    i1 = np.clip(x0.astype(int) + 1, 0, w - 1)
    rows = np.arange(h)[:, None]
    return _unpremul(pm[rows, i0] * (1 - f) + pm[rows, i1] * f)


def _fract(v: np.ndarray) -> np.ndarray:
    return v - np.floor(v)


def _hash21(px: np.ndarray, py: np.ndarray) -> np.ndarray:
    x = _fract(px * np.float32(123.34))
    y = _fract(py * np.float32(456.21))
    d = x * (x + np.float32(45.32)) + y * (y + np.float32(45.32))
    return _fract((x + d) * (y + d))


def _value_noise(px: np.ndarray, py: np.ndarray) -> np.ndarray:
    ix, iy = np.floor(px), np.floor(py)
    fx, fy = px - ix, py - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = _hash21(ix, iy), _hash21(ix + 1, iy)
    c, d = _hash21(ix, iy + 1), _hash21(ix + 1, iy + 1)
    top, bot = a + (b - a) * fx, c + (d - c) * fx
    return top + (bot - top) * fy


def _rgb(v: object) -> np.ndarray:
    return np.array(parse_color(v), np.float32)


def _hit_flash(img: np.ndarray, p: dict, t: float) -> np.ndarray:
    col = _rgb(p["color"])
    blink = 1.0 if (p["speed"] > 0 and (t * p["speed"]) % 1.0 >= 0.5) else 0.0
    k = max(p["amount"], blink) * col[3]
    out = img.copy()
    out[..., :3] = img[..., :3] + (col[:3] - img[..., :3]) * k
    return out


def _dissolve(img: np.ndarray, p: dict, t: float) -> np.ndarray:
    col = _rgb(p["color"])
    h, w, _ = img.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sx, sy = (xx + 0.5) / p["noise_scale"], (yy + 0.5) / p["noise_scale"]
    n = _value_noise(sx, sy) * 0.65 + _value_noise(sx * 2 + 17, sy * 2 + 17) * 0.35
    prog = abs(((t * p["speed"] * 0.5) % 1.0) * 2 - 1) if p["speed"] > 0 else p["progress"]
    ew = p["edge_width"]
    d = n - (-ew + (1 + ew) * prog)
    keep = (d >= 0).astype(np.float32)
    edge = keep * (d < ew).astype(np.float32)
    out = img.copy()
    k = (edge * col[3])[..., None]
    out[..., :3] = img[..., :3] + (col[:3] - img[..., :3]) * k
    out[..., 3] = img[..., 3] * keep
    return out


def _dilated_alpha(a: np.ndarray, dx: float, dy: float) -> np.ndarray:
    h, w = a.shape
    ys, xs = np.mgrid[0:h, 0:w]
    ix = np.clip(np.round(xs + dx).astype(int), 0, w - 1)
    iy = np.clip(np.round(ys + dy).astype(int), 0, h - 1)
    return a[iy, ix]


def _outline_pulse(img: np.ndarray, p: dict, t: float) -> np.ndarray:
    col = _rgb(p["color"])
    wd = p["width"]
    a, k = img[..., 3], 0.7071 * wd
    nb = np.zeros_like(a)
    for dx, dy in ((wd, 0), (-wd, 0), (0, wd), (0, -wd), (k, k), (-k, -k), (k, -k), (-k, k)):
        nb = np.maximum(nb, _dilated_alpha(a, dx, dy))
    pulse = p["min_alpha"] + (1 - p["min_alpha"]) * (0.5 + 0.5 * np.sin(t * p["speed"] * TAU))
    oa = np.clip(nb - a, 0, 1) * col[3] * pulse
    out_a = a + oa * (1 - a)
    rgb = (img[..., :3] * a[..., None] + col[:3] * (oa * (1 - a))[..., None]) / np.maximum(out_a, 1e-4)[..., None]
    return np.concatenate([rgb, out_a[..., None]], axis=-1)


def _wind_sway(img: np.ndarray, p: dict, t: float) -> np.ndarray:
    h = img.shape[0]
    foot = np.nonzero(img[..., 3].max(axis=1) > 0.05)[0].max()
    above = np.maximum(foot - np.arange(h, dtype=np.float32), 0)
    # The shader moves vertices by +x, so the sampled source column is the opposite way.
    return _shift_rows(img, -np.sin(t * p["speed"] * TAU) * p["strength"] * above)


def _water_ripple(img: np.ndarray, p: dict, t: float) -> np.ndarray:
    y = np.arange(img.shape[0], dtype=np.float32)
    off = np.sin((y / p["wavelength"] + t * p["speed"]) * TAU) * p["amplitude"]
    out = _shift_rows(img, off)
    out[..., :3] *= _rgb(p["color"])[:3]
    return out


def _palette_swap(img: np.ndarray, p: dict, t: float) -> np.ndarray:
    from studio_core.ops.rarity import ramp_lut

    lut = ramp_lut(tuple(round(v * 255) for v in _rgb(p["color"])[:3]), 256)
    luma = np.clip(img[..., :3] @ LUM, 0, 1)
    mapped = lut[np.clip((luma * 255 + 0.5).astype(int), 0, 255)]
    strength = p["strength"] * (0.5 - 0.5 * np.cos(t * p["speed"] * TAU))
    out = img.copy()
    out[..., :3] = img[..., :3] + (mapped - img[..., :3]) * strength
    return out


RENDERERS = {"hit_flash": _hit_flash, "dissolve": _dissolve, "outline_pulse": _outline_pulse,
             "wind_sway": _wind_sway, "water_ripple": _water_ripple, "palette_swap": _palette_swap}


def _resolve(preset: Preset, color: str | None, speed: float | None) -> dict:
    p = {q.key: q.default for q in preset.params}
    if color and "color" in p:
        parse_color(color)
        p["color"] = color if color.startswith("#") else f"#{color}"
    if speed is not None and "speed" in p:
        p["speed"] = float(speed)
    return p


def cache_key(preset_id: str, color: str | None, speed: float | None, still: bool = False) -> str:
    p = _resolve(PRESETS[preset_id], color, speed)
    blob = json.dumps([preset_id, p, SIZE, FRAMES, _VERSION, still], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


STILL_AT = 0.3


def render(preset_id: str, color: str | None = None, speed: float | None = None,
           still: bool = False) -> tuple[bytes, bool]:
    """(webp bytes, animated). A preset whose speed is 0 and has no demo motion, or still=True, is one frame."""
    preset = PRESETS[preset_id]
    p = _resolve(preset, color, speed)
    if preset_id in DEMO_SPEED and (speed is None or "speed" not in p) and p.get("speed", 0) <= 0:
        p["speed"] = DEMO_SPEED[preset_id]
    sprite = sample_sprite()
    fn = RENDERERS[preset_id]
    animated = p.get("speed", 0) > 0
    period = (2.0 if preset_id == "dissolve" else 1.0) / p["speed"] if animated else 1.0
    n = FRAMES if animated and not still else 1
    # Frame 0 of a blink or dissolve is the untouched sprite, which tells nothing in a thumbnail.
    times = [period * STILL_AT] if still else [period * i / n for i in range(n)]
    frames = [Image.fromarray(np.clip(np.round(fn(sprite, p, t) * 255), 0, 255).astype(np.uint8), "RGBA")
              for t in times]
    buf = io.BytesIO()
    if n == 1:
        frames[0].save(buf, "WEBP", lossless=True)
    else:
        ms = max(MIN_FRAME_MS, round(min(period, MAX_LOOP_S) * 1000 / n))
        frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], duration=ms, loop=0, quality=70,
                       alpha_quality=80, method=1)
    return buf.getvalue(), animated


def preview(cache_dir: Path, preset_id: str, color: str | None = None, speed: float | None = None,
            still: bool = False) -> tuple[bytes, bool]:
    path = cache_dir / f"{preset_id}-{cache_key(preset_id, color, speed, still)}.webp"
    animated_path = path.with_suffix(".anim")
    try:
        return path.read_bytes(), animated_path.exists()
    except OSError:
        pass
    data, animated = render(preset_id, color, speed, still)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if animated:
            animated_path.touch()
    except OSError:
        pass
    return data, animated
