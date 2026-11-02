"""Pixel-art cleanup for AI-generated images: detect the real pixel size, snap to that grid, lock the palette."""
from __future__ import annotations

import asyncio
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _out_name, _put_png
from .pixel import PALETTES, _nearest, kmeans_palette, parse_hex, to_oklab

MAX_PIXEL = 16
FIT_MIN = 0.75
_N8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


@dataclass
class CleanResult:
    grid: Image.Image
    pixel_size: int
    offset: tuple[int, int]
    colors_before: int
    colors_after: int
    stray_removed: int
    warnings: list[str] = field(default_factory=list)


def _opaque_colors(arr: np.ndarray) -> int:
    px = arr[arr[..., 3] >= 128][:, :3].astype(np.int32)
    return int(len(np.unique((px[:, 0] << 16) | (px[:, 1] << 8) | px[:, 2]))) if len(px) else 0


def _edge_energy(arr: np.ndarray, axis: int) -> np.ndarray:
    d = np.abs(np.diff(arr.astype(np.int32), axis=axis)).sum(axis=2).astype(np.float64)
    # Most positions are inside a cell, so the median is the noise/blur floor; only edges above it count.
    return np.maximum(d - 2 * np.median(d), 0).sum(axis=1 - axis)


def _grid_fit(gx: np.ndarray, gy: np.ndarray, s: int) -> tuple[float, tuple[int, int]]:
    total = gx.sum() + gy.sum()
    fx = [gx[o::s].sum() for o in range(s)]
    fy = [gy[o::s].sum() for o in range(s)]
    ox, oy = int(np.argmax(fx)), int(np.argmax(fy))
    # diff index i sits between pixel i and i+1, so cells start one past the best line.
    return float((fx[ox] + fy[oy]) / total), ((ox + 1) % s, (oy + 1) % s)


def detect_pixel_size(arr: np.ndarray, max_size: int = MAX_PIXEL) -> tuple[int, tuple[int, int]]:
    """Largest size whose grid lines hold most of the edge energy; larger multiples hold only a fraction of it."""
    gx, gy = _edge_energy(arr, 1), _edge_energy(arr, 0)
    if gx.sum() + gy.sum() <= 0:
        return 1, (0, 0)
    best = (1, (0, 0))
    for s in range(2, max_size + 1):
        fit, off = _grid_fit(gx, gy, s)
        if fit >= FIT_MIN:
            best = (s, off)
    return best


def _cell_median(arr: np.ndarray, s: int) -> np.ndarray:
    h, w = arr.shape[0] // s, arr.shape[1] // s
    cells = arr[: h * s, : w * s].reshape(h, s, w, s, 4).transpose(0, 2, 1, 3, 4).reshape(h, w, s * s, 4)
    alpha = cells[..., 3].astype(np.float64)
    solid = alpha.mean(axis=2) >= 128
    rgb = np.where(alpha[..., None] >= 128, cells[..., :3].astype(np.float64), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(rgb, axis=2)
    med = np.where(np.isnan(med), 0, med)
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = np.where(solid[..., None], np.round(med), 0).astype(np.uint8)
    out[..., 3] = np.where(solid, 255, 0)
    return out


def read_palette_text(text: str, name: str = "palette") -> np.ndarray:
    colors: list[tuple[int, int, int]] = []
    for raw in text.splitlines():
        parts = raw.split()
        if not parts:
            continue
        if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
            colors.append(tuple(min(255, int(p)) for p in parts[:3]))
            continue
        try:
            colors.append(parse_hex(parts[0]))
        except ValueError:
            continue
    if not colors:
        raise ValueError(f"{name} has no colors")
    return np.unique(np.array(colors, np.uint8), axis=0)


def palette_from_image(img: Image.Image, limit: int = 256) -> np.ndarray:
    arr = np.asarray(img.convert("RGBA"))
    px = arr[arr[..., 3] >= 128][:, :3]
    if not len(px):
        raise ValueError("palette image has no opaque pixels")
    pal = np.unique(px, axis=0)
    return pal if len(pal) <= limit else kmeans_palette(px, limit)


def remove_stray(rgba: np.ndarray) -> tuple[np.ndarray, int]:
    """A lone cell differing from every opaque 8-neighbour takes the neighbours' majority colour."""
    h, w = rgba.shape[:2]
    codes = (rgba[..., 0].astype(np.int64) << 16) | (rgba[..., 1].astype(np.int64) << 8) | rgba[..., 2]
    solid = rgba[..., 3] > 0
    pad_c = np.pad(codes, 1, constant_values=-1)
    pad_s = np.pad(solid, 1, constant_values=False)
    same = np.zeros((h, w), np.int32)
    n_solid = np.zeros((h, w), np.int32)
    for dy, dx in _N8:
        nc = pad_c[1 + dy: 1 + dy + h, 1 + dx: 1 + dx + w]
        ns = pad_s[1 + dy: 1 + dy + h, 1 + dx: 1 + dx + w]
        same += (ns & (nc == codes)).astype(np.int32)
        n_solid += ns.astype(np.int32)
    stray = solid & (same == 0) & (n_solid >= 3)
    out = rgba.copy()
    for y, x in zip(*np.nonzero(stray)):
        votes: dict[int, int] = {}
        for dy, dx in _N8:
            if pad_s[1 + y + dy, 1 + x + dx]:
                c = int(pad_c[1 + y + dy, 1 + x + dx])
                votes[c] = votes.get(c, 0) + 1
        win = min(votes, key=lambda c: (-votes[c], c))
        out[y, x, :3] = ((win >> 16) & 255, (win >> 8) & 255, win & 255)
    return out, int(stray.sum())


def add_outline(rgba: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    solid = rgba[..., 3] > 0
    pad = np.pad(solid, 1, constant_values=False)
    near = pad[:-2, 1:-1] | pad[2:, 1:-1] | pad[1:-1, :-2] | pad[1:-1, 2:]
    out = rgba.copy()
    out[near & ~solid] = (*color, 255)
    return out


def lock_palette(rgba: np.ndarray, pal: np.ndarray) -> np.ndarray:
    solid = rgba[..., 3] > 0
    out = rgba.copy()
    if solid.any():
        out[solid, :3] = pal[_nearest(to_oklab(rgba[solid][:, :3]), to_oklab(pal))]
    return out


# Smooth-painted sprites of 128px+ turn to mush at 16 colours; true low-res pixel art keeps the small budget.
DEFAULT_COLORS = 16
BIG_CELL_COLORS = 64
BIG_CELL = 128


def choose_palette(grids: list[np.ndarray], colors: int, preset: str, given: np.ndarray | None) -> np.ndarray | None:
    if given is not None:
        return given
    if preset != "auto":
        return np.array([parse_hex(c) for c in PALETTES[preset]], np.uint8)
    parts = [g[g[..., 3] > 0][:, :3] for g in grids]
    px = np.concatenate(parts) if parts else np.zeros((0, 3), np.uint8)
    return kmeans_palette(px, colors) if len(px) else None


def _outline_color(spec: str, pal: np.ndarray | None) -> tuple[int, int, int] | None:
    spec = (spec or "none").strip().lower()
    if spec == "none":
        return None
    if spec == "dark":
        if pal is None or not len(pal):
            return (0, 0, 0)
        return tuple(int(v) for v in pal[int(np.argmin(to_oklab(pal)[:, 0]))])
    return parse_hex(spec)


def pixel_cleanup(frames: list[Image.Image], colors: int = 0, palette: str = "auto",
                  palette_colors: np.ndarray | None = None, pixel_size: int = 0, outline: str = "none"
                  ) -> list[CleanResult]:
    if not frames:
        raise ValueError("no frames")
    if colors and not 2 <= colors <= 256:
        raise ValueError("colors must be within 2..256")
    if palette != "auto" and palette not in PALETTES:
        raise ValueError(f"unknown palette '{palette}'")
    if palette_colors is not None and not len(palette_colors):
        raise ValueError("palette is empty")
    arrs = [np.asarray(f.convert("RGBA")) for f in frames]
    infos: list[tuple[int, tuple[int, int], list[str]]] = []
    grids: list[np.ndarray] = []
    for arr in arrs:
        notes: list[str] = []
        size, off = 1, (0, 0)
        if not (arr[..., 3] >= 128).any():
            notes.append("image is fully transparent")
        elif pixel_size > 1:
            size = pixel_size
            if arr.shape[0] > 1 and arr.shape[1] > 1:
                off = _grid_fit(_edge_energy(arr, 1), _edge_energy(arr, 0), size)[1]
        else:
            size, off = detect_pixel_size(arr) if not pixel_size else (1, (0, 0))
            if size == 1:
                notes.append("no pixel grid found (not pixel art?): pixel size 1")
        infos.append((size, off, notes))
        grids.append(_cell_median(arr[off[1]:, off[0]:], size) if size > 1 else _cell_median(arr, 1))
    colors = colors or (BIG_CELL_COLORS if max(max(g.shape[:2]) for g in grids) >= BIG_CELL else DEFAULT_COLORS)
    pal = choose_palette(grids, colors, palette, palette_colors)
    line = _outline_color(outline, pal)
    results = []
    for arr, grid, (size, off, notes) in zip(arrs, grids, infos):
        if pal is not None:
            grid = lock_palette(grid, pal)
        grid, stray = remove_stray(grid)
        if line is not None and (grid[..., 3] > 0).any():
            grid = add_outline(grid, line)
        results.append(CleanResult(Image.fromarray(grid, "RGBA"), size, off, _opaque_colors(arr),
                                   _opaque_colors(grid), stray, notes))
    return results


def upscale(img: Image.Image, factor: int) -> Image.Image:
    return img.resize((img.width * factor, img.height * factor), Image.Resampling.NEAREST) if factor > 1 else img


async def _read_palette(ctx: StepContext, inputs: Outputs) -> np.ndarray | None:
    try:
        files = inputs.get("palette_file") or []
        if files:
            text = Path(ctx.blobs.path(files[0].hash)).read_text(encoding="utf-8", errors="replace")
            return read_palette_text(text, files[0].name or "palette file")
        imgs = inputs.get("palette_image") or []
        if imgs:
            return palette_from_image(await _load(ctx, imgs[0]))
    except ValueError as e:
        raise StepError("op_bad_params", str(e)) from e
    return None


class PixelCleanup(OpExecutor):
    kind = "op.pixel_cleanup"
    version = "2"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        imgs = []
        for i, ref in enumerate(refs):
            ctx.progress(i, len(refs) * 2 + 1, label="load")
            imgs.append(await _load(ctx, ref))
        given = await _read_palette(ctx, inputs)
        try:
            results = await asyncio.to_thread(
                pixel_cleanup, imgs, int(params.get("colors") or 0), str(params.get("palette") or "auto"), given,
                int(params.get("pixel_size") or 0), str(params.get("outline") or "none"))
        except ValueError as e:
            raise StepError("op_bad_params", str(e)) from e
        scale_param = int(params.get("scale") or 0)
        grid_out: list[BlobRef] = []
        big_out: list[BlobRef] = []
        report = []
        for i, (ref, r) in enumerate(zip(refs, results)):
            ctx.progress(len(refs) + i, len(refs) * 2 + 1, label="save")
            factor = scale_param or r.pixel_size
            grid_out.append(await _put_png(ctx, r.grid, _out_name(ref, i, "grid")))
            big_out.append(await _put_png(ctx, upscale(r.grid, factor), _out_name(ref, i, "clean")))
            report.append({"name": ref.name, "pixel_size": r.pixel_size, "offset": list(r.offset),
                           "grid": [r.grid.width, r.grid.height], "scale": factor,
                           "colors_before": r.colors_before, "colors_after": r.colors_after,
                           "stray_removed": r.stray_removed, "warnings": r.warnings})
        rep_ref = await ctx.blobs.put_bytes(json.dumps({"images": report}, indent=1).encode(), name="cleanup.json",
                                            mime="application/json")
        ctx.progress(1, 1, label="done")
        sizes = {r["pixel_size"] for r in report}
        return StepResult(outputs={"images": big_out, "grid": grid_out, "report": [rep_ref]},
                          meta={"pixel_size": sizes.pop() if len(sizes) == 1 else sorted(sizes), "pixel": True,
                                "colors_before": max(r["colors_before"] for r in report),
                                "colors_after": max(r["colors_after"] for r in report),
                                "warnings": [w for r in report for w in r["warnings"]]})


PIXEL_CLEANUP_OPS: tuple[type[OpExecutor], ...] = (PixelCleanup,)
