"""`op.pbr`: albedo texture -> normal / height / roughness / metallic / AO maps by CPU heuristics.

Every filter wraps around the edges when `seamless` is on, so maps of a tileable texture tile with it."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png
from .tile import _enabled, _lum, soft_blur

MATERIAL_NAME = "pbr_material.json"
MATERIAL_TYPE = "asset-studio/pbr-material"
ROLES = ("albedo", "normal", "height", "roughness", "metallic", "ao")
METALLIC_MODES = ("none", "auto")
GREEN = ("opengl", "directx")


@dataclass
class PbrParams:
    normal_strength: float = 3.0
    blur: int = 1
    invert_height: bool = False
    roughness: float = 0.75
    roughness_contrast: float = 0.5
    metallic: str = "none"
    metallic_level: float = 1.0
    ao_strength: float = 0.6
    green: str = "opengl"
    seamless: bool = True

    @classmethod
    def parse(cls, p: dict[str, Any]) -> PbrParams:
        d = cls()
        try:
            out = cls(normal_strength=float(p.get("normal_strength", d.normal_strength)),
                      blur=int(p.get("blur", d.blur)),
                      invert_height=_enabled(p.get("invert_height", d.invert_height)),
                      roughness=float(p.get("roughness", d.roughness)),
                      roughness_contrast=float(p.get("roughness_contrast", d.roughness_contrast)),
                      metallic=str(p.get("metallic") or d.metallic).lower(),
                      metallic_level=float(p.get("metallic_level", d.metallic_level)),
                      ao_strength=float(p.get("ao_strength", d.ao_strength)),
                      green=str(p.get("green") or d.green).lower(),
                      seamless=_enabled(p.get("seamless", d.seamless)))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", f"bad pbr parameter: {e}") from e
        if out.metallic not in METALLIC_MODES:
            raise StepError("op_bad_params", f"metallic must be one of {METALLIC_MODES}")
        if out.green not in GREEN:
            raise StepError("op_bad_params", f"green must be one of {GREEN}")
        if not 0 <= out.blur <= 32 or out.normal_strength <= 0:
            raise StepError("op_bad_params", "blur must be 0..32 and normal_strength > 0")
        return out


def _shift(a: np.ndarray, d: int, ax: int, wrap: bool) -> np.ndarray:
    """a[i + d] along `ax`, wrapping or clamping at the edge."""
    n = a.shape[ax]
    idx = np.arange(n) + d
    return np.take(a, idx % n if wrap else np.clip(idx, 0, n - 1), axis=ax)


def _norm(a: np.ndarray, lo_q: float = 1.0, hi_q: float = 99.0) -> np.ndarray:
    lo, hi = np.percentile(a, [lo_q, hi_q])
    if hi - lo < 1e-6:
        return np.full_like(a, 0.5)
    return np.clip((a - lo) / (hi - lo), 0, 1).astype(np.float32)


def _smooth(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def height_map(rgb: np.ndarray, p: PbrParams) -> np.ndarray:
    lum = _lum(rgb)
    h = soft_blur(lum, p.blur, wrap=p.seamless) if p.blur > 0 else lum
    # Remove the broad lighting drift: left in, a lit corner reads as a hill across the whole tile.
    low = soft_blur(h, max(4, min(h.shape) // 8), wrap=p.seamless)
    h = _norm(h - low * 0.7)
    return 1 - h if p.invert_height else h


def normal_map(h: np.ndarray, p: PbrParams) -> np.ndarray:
    w = p.seamless
    sx = _shift(h, 1, 1, w) - _shift(h, -1, 1, w)
    sy = _shift(h, 1, 0, w) - _shift(h, -1, 0, w)
    dx = (_shift(sx, -1, 0, w) + 2 * sx + _shift(sx, 1, 0, w)) / 8
    dy = (_shift(sy, -1, 1, w) + 2 * sy + _shift(sy, 1, 1, w)) / 8
    k = p.normal_strength
    # Image rows grow downwards; OpenGL (Unity, Godot) wants +G pointing up the texture.
    n = np.stack([-dx * k, dy * k if p.green == "opengl" else -dy * k, np.ones_like(h)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return (n * 0.5 + 0.5).astype(np.float32)


def roughness_map(rgb: np.ndarray, p: PbrParams) -> np.ndarray:
    """Dark and busy reads rough, bright and even reads glossy: a guess, tuned by base level and contrast."""
    lum = _lum(rgb)
    r = max(2, min(lum.shape) // 128)
    detail = lum - soft_blur(lum, r, wrap=p.seamless)
    contrast = np.sqrt(soft_blur(detail * detail, r, wrap=p.seamless))
    raw = 0.5 * (1 - _norm(lum)) + 0.5 * _norm(contrast)
    return np.clip(p.roughness + p.roughness_contrast * (raw - raw.mean()), 0.02, 1).astype(np.float32)


def metallic_map(rgb: np.ndarray, p: PbrParams) -> np.ndarray:
    if p.metallic == "none" or p.metallic_level <= 0:
        return np.zeros(rgb.shape[:2], np.float32)
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0)
    lum = _lum(rgb)
    spec = np.clip(lum - soft_blur(lum, max(2, min(lum.shape) // 64), wrap=p.seamless), 0, 1)
    # Bare metal: bright with crisp highlights; saturated bright areas are mostly paint, so they are left out.
    m = _smooth(0.45, 0.8, lum) * (1 - _smooth(0.25, 0.55, sat)) * (0.6 + 0.4 * _norm(spec))
    m = soft_blur(m, 1, wrap=p.seamless)
    return np.clip(m * p.metallic_level, 0, 1).astype(np.float32)


def ao_map(h: np.ndarray, p: PbrParams) -> np.ndarray:
    cavity = np.clip(soft_blur(h, max(2, min(h.shape) // 48), wrap=p.seamless) - h, 0, None)
    hi = float(np.percentile(cavity, 99))
    if hi < 1e-6:
        return np.ones_like(h)
    return np.clip(1 - p.ao_strength * np.clip(cavity / hi, 0, 1), 0, 1).astype(np.float32)


def _gray(a: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(np.round(a * 255), 0, 255).astype(np.uint8), "L")


def _rgb8(a: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(np.round(a * 255), 0, 255).astype(np.uint8), "RGB")


def derive(img: Image.Image, p: PbrParams) -> dict[str, Image.Image]:
    """All six maps at the source size; albedo is the source, opaque."""
    rgb = np.asarray(img.convert("RGB"), np.float32) / 255.0
    h = height_map(rgb, p)
    return {"albedo": img.convert("RGB"), "normal": _rgb8(normal_map(h, p)), "height": _gray(h),
            "roughness": _gray(roughness_map(rgb, p)), "metallic": _gray(metallic_map(rgb, p)),
            "ao": _gray(ao_map(h, p))}


def preview_grid(maps: dict[str, Image.Image], cell: int = 256) -> Image.Image:
    """2×3 sheet: albedo, normal, height on top; roughness, metallic, AO below, each labelled."""
    gap, label = 6, 18
    out = Image.new("RGB", (cell * 3 + gap * 4, (cell + label) * 2 + gap * 3), (32, 32, 36))
    d = ImageDraw.Draw(out)
    for i, role in enumerate(ROLES):
        x, y = gap + (i % 3) * (cell + gap), gap + (i // 3) * (cell + label + gap)
        out.paste(maps[role].convert("RGB").resize((cell, cell), Image.Resampling.LANCZOS), (x, y + label))
        d.text((x + 2, y + 3), role, fill=(230, 230, 230))
    return out


class Pbr(OpExecutor):
    kind = "op.pbr"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        if not _enabled(params.get("enabled", True)):
            return StepResult(outputs={"maps": [], "preview": [], "material": []}, meta={"skipped": True})
        p = PbrParams.parse(params)
        refs = _images_in(inputs)
        maps_out: list[BlobRef] = []
        previews: list[BlobRef] = []
        materials: list[dict[str, Any]] = []
        for i, ref in enumerate(refs):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.progress(i, len(refs), label="pbr")
            src = await _load(ctx, ref)
            maps = await asyncio.to_thread(derive, src, p)
            stem = (ref.name or f"texture_{i:02d}").rsplit(".", 1)[0]
            entry: dict[str, Any] = {"name": stem, "size": list(src.size), "maps": {}}
            for role in ROLES:
                r = await _put_png(ctx, maps[role], f"{stem}_{role}.png")
                maps_out.append(r)
                entry["maps"][role] = {"name": r.name, "hash": r.hash}
            entry["roughness_mean"] = round(float(np.asarray(maps["roughness"]).mean()) / 255, 3)
            entry["metallic_mean"] = round(float(np.asarray(maps["metallic"]).mean()) / 255, 3)
            materials.append(entry)
            grid = await asyncio.to_thread(preview_grid, maps)
            previews.append(await _put_png(ctx, grid, f"{stem}_pbr_preview.png"))
        ctx.progress(len(refs), len(refs))
        doc = {"type": MATERIAL_TYPE, "green": p.green, "seamless": p.seamless, "materials": materials}
        mat = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=MATERIAL_NAME,
                                        mime="application/json")
        return StepResult(outputs={"maps": maps_out, "preview": previews, "material": [mat]},
                          meta={"materials": len(materials), "green": p.green, "seamless": p.seamless,
                                "metallic": p.metallic})


PBR_OPS = (Pbr,)
