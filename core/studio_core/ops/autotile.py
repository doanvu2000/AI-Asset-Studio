"""`op.autotile`: blob-47 terrain autotiles (terrain A over terrain B) rendered on CPU, plus autotile.json.

A covers the points farther than `border` (pushed by tile-periodic noise) from the cell's B sides and B diagonals,
with convex corners rounded; the rule is written so two neighbouring tiles give the same value on their shared edge
(see `signed`), so any map painted with the set joins without seams by construction."""
from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png
from .pixel import plan_palette, quantize, to_hex
from .tile import _lum, _rgb, seamless, soft_blur, tile_report, wrap_resize

AUTOTILE_FILE = "autotile.json"
AUTOTILE_TYPE = "asset-studio/autotile-blob47"
# Clockwise from north, image y grows downwards; bit i of a mask = DIRS[i].
DIRS = (("n", 0, -1), ("ne", 1, -1), ("e", 1, 0), ("se", 1, 1), ("s", 0, 1), ("sw", -1, 1), ("w", -1, 0),
        ("nw", -1, -1))
BIT = {d: 1 << i for i, (d, _, _) in enumerate(DIRS)}
CORNER_SIDES = {"ne": ("n", "e"), "se": ("s", "e"), "sw": ("s", "w"), "nw": ("n", "w")}
GODOT_BITS = {"n": "top_side", "ne": "top_right_corner", "e": "right_side", "se": "bottom_right_corner",
              "s": "bottom_side", "sw": "bottom_left_corner", "w": "left_side", "nw": "top_left_corner"}
# RuleTile's default 3×3 neighbour order (y up); 1 = This, 2 = NotThis, 0 = don't care.
UNITY_NEIGHBORS = (("nw", -1, 1), ("n", 0, 1), ("ne", 1, 1), ("w", -1, 0), ("e", 1, 0), ("sw", -1, -1),
                   ("s", 0, -1), ("se", 1, -1))
COLS, ROWS = 8, 6
TILE_SIZES = (16, 32, 64, 128)
# Edge shape: (max push of the border in tile units, highest wave number per tile, 1/f falloff exponent).
EDGE_STYLES = {"smooth": (0.03, 2, 1.5), "organic": (0.07, 4, 1.2), "rough": (0.09, 8, 0.9)}


def reduce_mask(m: int) -> int:
    """A diagonal only changes the tile when both sides next to it are A (the 47-tile blob reduction)."""
    for c, (s1, s2) in CORNER_SIDES.items():
        if not (m & BIT[s1] and m & BIT[s2]):
            m &= ~BIT[c]
    return m


def blob_masks() -> list[int]:
    return sorted({reduce_mask(m) for m in range(256)})


def mask_at(cells: np.ndarray, y: int, x: int) -> int:
    """Neighbour mask of cell (y, x); off-map counts as B."""
    h, w = cells.shape
    m = 0
    for d, dx, dy in DIRS:
        yy, xx = y + dy, x + dx
        if 0 <= yy < h and 0 <= xx < w and cells[yy, xx]:
            m |= BIT[d]
    return reduce_mask(m)


def layout(names: tuple[str, str] = ("a", "b")) -> list[dict[str, Any]]:
    """47 A tiles in mask order, then the plain B tile; atlas cell (index % 8, index // 8)."""
    out = []
    for i, m in enumerate(blob_masks()):
        sides = {d: bool(m & BIT[d]) for d in ("n", "e", "s", "w")}
        corners = {c: bool(m & BIT[c]) for c in CORNER_SIDES}
        godot = {GODOT_BITS[d]: 0 if (sides | corners)[d] else 1 for d, *_ in DIRS}
        unity = [(1 if sides[d] else 2) if d in sides else
                 ((1 if corners[d] else 2) if all(sides[s] for s in CORNER_SIDES[d]) else 0)
                 for d, *_ in UNITY_NEIGHBORS]
        out.append({"index": i, "name": f"{names[0]}_{m:03d}", "terrain": names[0], "mask": m, "col": i % COLS,
                    "row": i // COLS, "sides": sides, "corners": corners, "godot_peering": godot,
                    "unity_neighbors": unity})
    i = len(out)
    out.append({"index": i, "name": f"{names[1]}_fill", "terrain": names[1], "mask": None, "col": i % COLS,
                "row": i // COLS, "godot_peering": {GODOT_BITS[d]: 1 for d, *_ in DIRS}})
    return out


@dataclass(frozen=True)
class AutotileOptions:
    size: int = 64
    pixel: bool = False
    colors: int = 0
    border: float = 0.3
    edge: str = "organic"
    round: float = 0.15
    softness: float = 0.04
    rim: float = 0.06
    rim_strength: float = 0.45
    seed: int = 0


def parse(params: dict[str, Any]) -> AutotileOptions:
    def flag(v: Any) -> bool:
        return v.strip().lower() not in ("false", "0", "no", "off", "") if isinstance(v, str) else bool(v)

    try:
        o = AutotileOptions(size=int(params.get("size") or 64), pixel=flag(params.get("pixel", False)),
                            colors=int(params.get("colors") or 0), border=float(params.get("border", 0.3)),
                            edge=str(params.get("edge") or "organic"), round=float(params.get("round", 0.15)),
                            softness=float(params.get("softness", 0.04)), rim=float(params.get("rim", 0.06)),
                            rim_strength=float(params.get("rim_strength", 0.45)), seed=int(params.get("seed") or 0))
    except (TypeError, ValueError) as e:
        raise StepError("op_bad_params", f"bad autotile parameter: {e}") from e
    if o.size not in TILE_SIZES or o.edge not in EDGE_STYLES:
        raise StepError("op_bad_params", f"size must be one of {TILE_SIZES}, edge one of {', '.join(EDGE_STYLES)}")
    if not (0.1 <= o.border <= 0.4 and 0 <= o.round <= 0.25 and 0 <= o.softness <= 0.1 and 0 <= o.rim <= 0.15
            and 0 <= o.rim_strength <= 1 and (o.colors == 0 or 2 <= o.colors <= 64)):
        raise StepError("op_bad_params", "border 0.1..0.4, round 0..0.25, softness 0..0.1, rim 0..0.15, "
                                         "rim_strength 0..1, colors 0 or 2..64")
    return o


@dataclass(frozen=True)
class _Field:
    m0: float
    amp: float
    r: float
    aa: float
    rim_w: float
    deep: float
    terms: np.ndarray
    norm: float


def _field(o: AutotileOptions) -> _Field:
    amp, kmax, beta = EDGE_STYLES[o.edge]
    rng = np.random.default_rng(o.seed)
    ks = [(kx, ky) for kx in range(-kmax, kmax + 1) for ky in range(0, kmax + 1)
          if (ky > 0 or kx > 0) and kx * kx + ky * ky <= kmax * kmax]
    terms = np.array([(kx, ky, rng.normal() / math.hypot(kx, ky) ** beta, rng.uniform(0, 2 * math.pi))
                      for kx, ky in ks], np.float64)
    g = (np.arange(96) + 0.5) / 96
    norm = float(np.abs(_noise(terms, *np.meshgrid(g, g))).max()) or 1.0
    aa = 0.0 if o.pixel else max(o.softness, 1.0 / o.size)
    rim_w = o.rim
    # Distances past one tile never reach a neighbour's side, so every term below must stay well inside it.
    r = max(0.0, min(o.round, (0.9 - o.border - amp - aa - rim_w) / 2))
    return _Field(o.border, amp, r, aa, rim_w, rim_w + aa + 2.0 / o.size, terms, norm)


def _noise(terms: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Integer frequencies only, so the noise wraps at the tile size and both sides of a seam read the same value."""
    out = np.zeros(np.broadcast(u, v).shape)
    for kx, ky, a, ph in terms:
        out += a * np.sin(2 * math.pi * (kx * u + ky * v) + ph)
    return out


SIDE_DIST = {"n": lambda u, v: v, "s": lambda u, v: 1 - v, "w": lambda u, v: u, "e": lambda u, v: 1 - u}
CORNER_AT = {"ne": (1, 0), "se": (1, 1), "sw": (0, 1), "nw": (0, 0)}


def signed(mask: int | None, u: np.ndarray, v: np.ndarray, f: _Field) -> np.ndarray:
    """Outward signed distance (tile units, < 0 = A) of an A cell with this blob mask, at tile coords (u, v).

    Each B side is a half-plane and each B diagonal between two A sides a corner point; a rounded intersection
    (radius r) of their `border` offsets. On a shared tile edge both cells see the same constraint values (a side
    there equals the neighbour's corner distance), so neighbouring tiles agree exactly along the edge."""
    u, v = np.broadcast_arrays(np.asarray(u, np.float64), np.asarray(v, np.float64))
    if mask is None:
        return np.full(u.shape, 1.0)
    dists = [SIDE_DIST[d](u, v) for d in ("n", "e", "s", "w") if not mask & BIT[d]]
    dists += [np.hypot(u - CORNER_AT[c][0], v - CORNER_AT[c][1]) for c, (s1, s2) in CORNER_SIDES.items()
              if mask & BIT[s1] and mask & BIT[s2] and not mask & BIT[c]]
    if not dists:
        return np.full(u.shape, -f.deep)
    thr = f.m0 + f.amp * np.clip(_noise(f.terms, u, v) / f.norm, -1, 1)
    di = [thr - d for d in dists]
    out = np.minimum(-f.r, np.maximum.reduce(di)) + np.sqrt(sum(np.maximum(f.r + d, 0) ** 2 for d in di))
    return np.maximum(out, -f.deep)


def weights(sd: np.ndarray, f: _Field, pixel: bool) -> tuple[np.ndarray, np.ndarray]:
    """A coverage and rim weight from the signed distance; pixel art thresholds at the pixel centre."""
    if pixel:
        inside = sd < 0
        return inside.astype(np.float32), (inside & (sd > -f.rim_w)).astype(np.float32)
    alpha = np.clip(0.5 - sd / f.aa, 0, 1)
    rim = np.clip((sd + f.rim_w) / f.aa + 0.5, 0, 1) * alpha if f.rim_w > 0 else np.zeros_like(alpha)
    return alpha.astype(np.float32), rim.astype(np.float32)


def textures(a_img: Image.Image, b_img: Image.Image, o: AutotileOptions) -> tuple[np.ndarray, np.ndarray]:
    rs = Image.Resampling.BOX if o.pixel else Image.Resampling.LANCZOS
    return (_rgb(wrap_resize(a_img.convert("RGB"), o.size, o.size, rs)),
            _rgb(wrap_resize(b_img.convert("RGB"), o.size, o.size, rs)))


def _rim_colour(a: np.ndarray) -> np.ndarray:
    lum = _lum(a)
    return a[lum <= np.percentile(lum, 15)].mean(0) * 0.7


def render_tile(mask: int | None, a: np.ndarray, b: np.ndarray, o: AutotileOptions,
                f: _Field | None = None) -> np.ndarray:
    f = f or _field(o)
    c = (np.arange(o.size) + 0.5) / o.size
    u, v = np.meshgrid(c, c)
    alpha, rim = weights(signed(mask, u, v, f), f, o.pixel)
    k = (o.rim_strength * rim)[..., None]
    col = a * (1 - k) + _rim_colour(a) * k
    return b * (1 - alpha[..., None]) + col * alpha[..., None]


def render_set(a: np.ndarray, b: np.ndarray, o: AutotileOptions) -> list[np.ndarray]:
    f = _field(o)
    return [render_tile(e["mask"], a, b, o, f) for e in layout()]


def band_mask(o: AutotileOptions, width: float = 0.12) -> np.ndarray:
    """Atlas-sized 0..1 weight of the transition band, faded to 0 at every tile border (for a repaint pass)."""
    f = _field(o)
    c = (np.arange(o.size) + 0.5) / o.size
    u, v = np.meshgrid(c, c)
    edge = np.clip(np.minimum(np.minimum(u, 1 - u), np.minimum(v, 1 - v)) * o.size / 3 - 0.5, 0, 1)
    tiles = [np.clip(1 - np.abs(signed(e["mask"], u, v, f)) / width, 0, 1) * edge if e["mask"] not in (None, 255)
             else np.zeros_like(u) for e in layout()]
    return atlas([t[..., None] for t in tiles])[..., 0]


def atlas(tiles: list[np.ndarray]) -> np.ndarray:
    t = tiles[0].shape[0]
    out = np.zeros((ROWS * t, COLS * t, 3), np.float32)
    for e, img in zip(layout(), tiles):
        out[e["row"] * t:(e["row"] + 1) * t, e["col"] * t:(e["col"] + 1) * t] = img
    return out


def tile_map(cells: np.ndarray, tiles: list[np.ndarray]) -> np.ndarray:
    """A map assembled from the set the way an engine paints it: each A cell gets the tile of its neighbour mask."""
    t = tiles[0].shape[0]
    index = {e["mask"]: e["index"] for e in layout()}
    h, w = cells.shape
    out = np.zeros((h * t, w * t, tiles[0].shape[2]), tiles[0].dtype)
    for y in range(h):
        for x in range(w):
            k = index[mask_at(cells, y, x)] if cells[y, x] else len(tiles) - 1
            out[y * t:(y + 1) * t, x * t:(x + 1) * t] = tiles[k]
    return out


def random_cells(w: int, h: int, seed: int) -> np.ndarray:
    """Blobby A areas with holes, single cells and diagonal touches, so most of the 47 tiles show up."""
    rng = np.random.default_rng(seed)
    field = soft_blur(rng.random((h, w)).astype(np.float32), 1, wrap=False)
    cells = field > np.quantile(field, 0.45)
    flip = rng.random((h, w)) < 0.08
    return cells ^ flip


def edge_profiles(cells: np.ndarray, o: AutotileOptions, steps: int = 64) -> float:
    """Largest A-coverage/rim mismatch where two map cells meet, each side evaluated on the shared edge line with
    its own tile's rule (0 = the tiles join without a seam)."""
    f = _field(o)
    h, w = cells.shape
    t = np.linspace(0, 1, steps + 1)
    zero, one = np.zeros_like(t), np.ones_like(t)

    def mask(y: int, x: int) -> int | None:
        return mask_at(cells, y, x) if cells[y, x] else None

    worst = 0.0
    for y in range(h):
        for x in range(w):
            pairs = []
            if x + 1 < w:
                pairs.append(((one, t), (zero, t), mask(y, x + 1)))
            if y + 1 < h:
                pairs.append(((t, one), (t, zero), mask(y + 1, x)))
            for here, there, other in pairs:
                wa = weights(signed(mask(y, x), *here, f), f, o.pixel)
                wb = weights(signed(other, *there, f), f, o.pixel)
                worst = max(worst, *(float(np.abs(p - q).max()) for p, q in zip(wa, wb)))
    return worst


def seam_check(cells: np.ndarray, tiles: list[np.ndarray]) -> dict[str, Any]:
    """Edge-line agreement plus the colour step across tile borders against the steps inside tiles (≈1 = no
    visible seam) on a map painted with the set."""
    img = tile_map(cells, tiles).astype(np.float32)
    t = tiles[0].shape[0]
    ratios = {}
    for name, ax in (("x", 1), ("y", 0)):
        steps = np.abs(np.diff(img, axis=ax)).mean(axis=(0, 2) if ax == 1 else (1, 2))
        bounds = np.arange(t - 1, img.shape[ax] - 1, t)
        inner = np.delete(steps, bounds)
        ratios[name] = round(float(steps[bounds].mean() / max(float(inner.mean()), 1e-6)), 3)
    return {"seam_ratio": ratios, "cells": [int(cells.shape[1]), int(cells.shape[0])]}


def _u8(a: np.ndarray) -> np.ndarray:
    return np.clip(np.round(a * 255), 0, 255).astype(np.uint8)


def palette_limit(img: np.ndarray, colors: int) -> tuple[np.ndarray, list[str]]:
    """No dither, outline or cleanup: quantizing pixel by pixel keeps the seams the render already guarantees."""
    rgba = np.concatenate([img, np.full((*img.shape[:2], 1), 255, np.uint8)], -1)
    solid = np.ones(img.shape[:2], bool)
    plan = plan_palette([(rgba, solid)], 1.0, colors, "auto", None, "none", "none", 0.6, False)
    return np.asarray(quantize(plan, rgba, solid))[..., :3], [to_hex(c) for c in plan.pal]


def preview(cells: np.ndarray, tiles: list[np.ndarray], grid: bool) -> Image.Image:
    img = tile_map(cells, tiles)
    t = tiles[0].shape[0]
    k = max(1, 1024 // img.shape[1])
    big = img.repeat(k, 0).repeat(k, 1)
    if grid:
        big = big.copy()
        tk = t * k
        line = np.array([255, 0, 160], np.uint8)
        big[::tk] = big[::tk] // 2 + line // 2
        big[:, ::tk] = big[:, ::tk] // 2 + line // 2
    return Image.fromarray(big, "RGB")


def terrain_names(text: Any) -> tuple[str, str]:
    parts = [re.sub(r"[^A-Za-z0-9_]+", "_", p.strip()).strip("_").lower()[:24]
             for p in re.split(r"[,\n]", str(text or ""))]
    a, b = (parts + ["", ""])[:2]
    a, b = a or "a", b or "b"
    return (a, b) if a != b else (a, f"{b}_2")


def _prepare(img: Image.Image, mode: str) -> tuple[Image.Image, bool]:
    if mode == "off" or min(img.size) < 32:
        return img, False
    if mode == "on" or tile_report(img)["seam_score"] < 70:
        return seamless(img), True
    return img, False


class Autotile(OpExecutor):
    kind = "op.autotile"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        o = parse(params)
        mode = str(params.get("seamless") or "auto")
        if mode not in ("auto", "on", "off"):
            raise StepError("op_bad_params", "seamless must be auto, on or off")
        names = terrain_names(params.get("terrains") or "a, b")
        a_ref, b_ref = _images_in(inputs, "tile_a")[0], _images_in(inputs, "tile_b")[0]
        ctx.progress(0, 4, label="seamless")
        (a_img, a_fix), (b_img, b_fix) = [await asyncio.to_thread(_prepare, await _load(ctx, r), mode)
                                          for r in (a_ref, b_ref)]
        a, b = await asyncio.to_thread(textures, a_img, b_img, o)
        if ctx.cancelled.is_set():
            raise asyncio.CancelledError
        ctx.progress(1, 4, label="tiles")
        tiles = await asyncio.to_thread(render_set, a, b, o)
        cells = random_cells(14, 9, o.seed + 1)
        ctx.progress(2, 4, label="seam check")
        check = await asyncio.to_thread(seam_check, cells, tiles)
        check["edge_mismatch"] = round(await asyncio.to_thread(edge_profiles, cells, o), 6)
        check["passed"] = check["edge_mismatch"] < 1e-6
        if not check["passed"]:
            ctx.log(f"neighbouring tiles disagree on a shared edge by {check['edge_mismatch']:.4f}", "warning")
        u8 = [_u8(t) for t in tiles]
        palette: list[str] = []
        if o.pixel and o.colors:
            sheet, palette = await asyncio.to_thread(palette_limit, _u8(atlas(tiles)), o.colors)
            t = o.size
            u8 = [sheet[e["row"] * t:(e["row"] + 1) * t, e["col"] * t:(e["col"] + 1) * t] for e in layout()]
        ctx.progress(3, 4, label="atlas")
        stem = f"{names[0]}_{names[1]}"
        sheet_ref = await _put_png(ctx, Image.fromarray(_u8(atlas([t.astype(np.float32) / 255 for t in u8])), "RGB"),
                                   f"{stem}_autotile.png")
        previews = [await _put_png(ctx, preview(cells, u8, g), f"{stem}_autotile_{n}.png")
                    for g, n in ((False, "map"), (True, "map_grid"))]
        doc = {"type": AUTOTILE_TYPE, "layout": "blob47", "tile_size": o.size, "grid": [COLS, ROWS],
               "terrains": list(names), "atlas": {"file": sheet_ref.name, "hash": sheet_ref.hash},
               "pixel": o.pixel, "bit_order": [d for d, *_ in DIRS],
               "unity_neighbor_order": [[dx, dy] for _, dx, dy in UNITY_NEIGHBORS],
               "rule": f"{names[0]} cells take the tile whose mask has bit i set when neighbour bit_order[i] is "
                       f"{names[0]}; a diagonal counts only when both sides next to it are {names[0]}. "
                       f"{names[1]} cells take {names[1]}_fill.",
               "tiles": layout(names), "seam_check": check, "palette": palette,
               "options": {**asdict(o), "seamless": mode}, "made_seamless": {"a": a_fix, "b": b_fix}}
        js = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=AUTOTILE_FILE,
                                       mime="application/json")
        ctx.progress(4, 4)
        return StepResult(outputs={"images": [sheet_ref], "preview": previews, "autotile": [js]},
                          meta={"tiles": len(u8), "size": o.size, "terrains": list(names), "seam_check": check,
                                "made_seamless": {"a": a_fix, "b": b_fix}, **({"palette": palette} if palette else {})})


AUTOTILE_OPS: tuple[type[OpExecutor], ...] = (Autotile,)
