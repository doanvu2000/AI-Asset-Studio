"""Isometric 2:1 (recipes iso-tiles, iso-tiles-image, iso-prop). `op.iso_tiles` maps a seamless square texture onto
the iso diamond so the texture period is one cell: tiles laid on the iso grid continue the texture across every edge,
variants only change the interior (the rim band stays the base texture), and the diamond alpha is decided per pixel
centre, which partitions the plane exactly (no gap, no overlap). `op.iso_prop` turns blender.iso_render frames into
4 direction sprites anchored on their footprint, with a check that nothing overhangs the footprint diamond."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from ..export.sheet import ident
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json
from .item_fx import resize_pm

ISO_TILES_FILE = "iso_tiles.json"
ISO_PROP_FILE = "iso_prop.json"
ISO_TILES_TYPE = "asset_studio.iso_tiles"
ISO_PROP_TYPE = "asset_studio.iso_prop"
TILE_WIDTHS = (64, 128, 256)
SUB = 3


def diamond_mask(w: int, h: int, grow: float = 0.0) -> np.ndarray:
    """Pixel centres strictly inside the diamond (grown by `grow` px vertically); with w = 2h, h even and no growth no
    centre lies on an edge, so neighbouring tiles partition the plane exactly."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64) + 0.5
    return np.abs(xx - w / 2) / (w / 2) + np.abs(yy - h / 2) / (h / 2) < 1 + 2 * grow / h


def tile_uv(x: np.ndarray, y: np.ndarray, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Screen px (from the tile's top-left) -> texture uv, uv (0,0) at the top vertex, u along the down-right edge."""
    px, py = x - w / 2, y
    return (px / (w / 2) + py / (h / 2)) / 2, (py / (h / 2) - px / (w / 2)) / 2


def bilinear_wrap(tex: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    s = tex.shape[0]
    fx, fy = u * s - 0.5, v * s - 0.5
    x0, y0 = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
    tx, ty = (fx - x0)[..., None], (fy - y0)[..., None]
    x0, y0 = x0 % s, y0 % s
    x1, y1 = (x0 + 1) % s, (y0 + 1) % s
    top = tex[y0, x0] * (1 - tx) + tex[y0, x1] * tx
    bot = tex[y1, x0] * (1 - tx) + tex[y1, x1] * tx
    return top * (1 - ty) + bot * ty


def periodic_resize(img: Image.Image, side: int) -> np.ndarray:
    """Resizes a tileable texture while keeping it tileable: resample a 3x3 tiling, keep the centre."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    big = Image.new("RGB", (w * 3, h * 3))
    for i in range(3):
        for j in range(3):
            big.paste(rgb, (i * w, j * h))
    small = big.resize((side * 3, side * 3), Image.Resampling.LANCZOS)
    return np.asarray(small.crop((side, side, 2 * side, 2 * side)), np.float32) / 255


def _smooth(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / max(1e-6, e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def render_tile(base: np.ndarray, w: int, h: int, interior: np.ndarray | None = None, edge: float = 0.12,
                gain: float = 1.0, bleed: float = 1.0) -> Image.Image:
    """One diamond tile; `interior` (same size as base, also tileable) replaces the base away from the rim.
    The alpha reaches `bleed` px past the exact diamond and colour fills the whole rect: engines filter sprites
    bilinearly, and a hard edge against transparent black shows as dark seams between tiles. The overlap is
    harmless because neighbours sample the same base texture at the same plane point there."""
    offs = (np.arange(SUB) + 0.5) / SUB
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    acc = np.zeros((h, w, 3), np.float64)
    for oy in offs:
        for ox in offs:
            u, v = tile_uv(xx + ox, yy + oy, w, h)
            col = bilinear_wrap(base, u, v)
            if interior is not None:
                d = np.minimum(np.minimum(u, 1 - u), np.minimum(v, 1 - v))
                m = _smooth(edge, edge + 0.18, d)[..., None]
                col = col * (1 - m) + bilinear_wrap(interior, u, v) * gain * m
            acc += col
    rgb = np.clip(acc / (SUB * SUB), 0, 1)
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = (rgb * 255 + 0.5).astype(np.uint8)
    out[..., 3] = np.where(diamond_mask(w, h, bleed), 255, 0)
    return Image.fromarray(out, "RGBA")


def variant_sources(textures: list[np.ndarray], count: int, seed: int, jitter: float
                    ) -> list[tuple[np.ndarray | None, float]]:
    """Variant 0 is the plain base; others take a quarter-turned, shifted interior (from the next texture when
    several were given) and a small brightness jitter."""
    rng = np.random.default_rng(seed)
    out: list[tuple[np.ndarray | None, float]] = [(None, 1.0)]
    for k in range(1, count):
        src = textures[k % len(textures)] if len(textures) > 1 else textures[0]
        s = src.shape[0]
        rolled = np.roll(np.rot90(src, k % 4), (int(s * ((k * 0.618) % 1)), int(s * ((k * 0.382) % 1))), (0, 1))
        out.append((np.ascontiguousarray(rolled), float(1 + rng.uniform(-jitter, jitter))))
    return out


def grid_positions(n: int, w: int, h: int) -> tuple[list[tuple[int, int, int, int]], tuple[int, int]]:
    """Cell (i, j) -> pixel offset on an n x n diamond-layout grid; i grows down-right, j down-left."""
    cells = [(i, j, (i - j) * w // 2 + (n - 1) * w // 2, (i + j) * h // 2) for j in range(n) for i in range(n)]
    return cells, (n * w, n * h)


def compose_grid(tiles: list[Image.Image], n: int, seed: int, bg: tuple[int, int, int, int] = (0, 0, 0, 0)
                 ) -> tuple[Image.Image, np.ndarray, list[int]]:
    w, h = tiles[0].size
    cells, size = grid_positions(n, w, h)
    rng = np.random.default_rng(seed)
    picks = [int(rng.integers(0, len(tiles))) for _ in cells]
    out = Image.new("RGBA", size, bg)
    cover = np.zeros((size[1], size[0]), np.int32)
    for (i, j, x, y), k in zip(cells, picks):
        out.alpha_composite(tiles[k], (x, y))
        cover[y:y + h, x:x + w] += np.asarray(tiles[k])[..., 3] > 0
    return out, cover, picks


def grid_qa(tiles: list[Image.Image], base_tex: np.ndarray, n: int = 4, seed: int = 0) -> dict[str, Any]:
    """Gaps inside the big n x n diamond, the bleed overlap, and how far tile rims stray from the base texture."""
    w, h = tiles[0].size
    grid, cover, picks = compose_grid(tiles, n, seed)
    big = diamond_mask(n * w, n * h)
    gaps = int(((cover == 0) & big).sum())
    overlaps = int((cover > 1).sum())
    ref, _, _ = compose_grid([tiles[0]] * len(tiles), n, seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64) + 0.5
    u, v = tile_uv(xx, yy, w, h)
    rim = (np.minimum(np.minimum(u, 1 - u), np.minimum(v, 1 - v)) < 0.06) & diamond_mask(w, h)
    rim_big = np.zeros((n * h, n * w), bool)
    for (_, _, x, y) in grid_positions(n, w, h)[0]:
        rim_big[y:y + h, x:x + w] |= rim
    a, b = np.asarray(grid, np.float32)[..., :3], np.asarray(ref, np.float32)[..., :3]
    seam = float(np.abs(a - b)[rim_big].max()) if rim_big.any() else 0.0
    return {"grid": n, "gaps_px": gaps, "overlap_px": overlaps, "rim_max_diff": round(seam, 2),
            "seamless": gaps == 0 and seam <= 2.0, "picks": picks}


def iso_tile_set(textures: list[Image.Image], tile_w: int, count: int, seed: int = 7, edge: float = 0.12,
                 jitter: float = 0.05) -> list[Image.Image]:
    if tile_w not in TILE_WIDTHS:
        raise ValueError(f"tile width must be one of {TILE_WIDTHS}")
    if not 1 <= count <= 8:
        raise ValueError("variants must be 1..8")
    side = tile_w
    texs = [periodic_resize(t, side) for t in textures]
    h = tile_w // 2
    return [render_tile(texs[0], tile_w, h, interior, edge, gain)
            for interior, gain in variant_sources(texs, count, seed, jitter)]


def strip(images: list[Image.Image]) -> Image.Image:
    w, h = images[0].size
    out = Image.new("RGBA", (w * len(images), h), (0, 0, 0, 0))
    for i, im in enumerate(images):
        out.paste(im, (i * w, 0))
    return out


def tiles_preview(tiles: list[Image.Image], seed: int) -> Image.Image:
    n = 8
    grid, _, _ = compose_grid(tiles, n, seed + 1, (0, 0, 0, 0))
    w, h = tiles[0].size
    s = min(1.0, 1024 / grid.width)
    if s < 1:
        grid = resize_pm(grid, (round(grid.width * s), round(grid.height * s)))
    out = Image.new("RGBA", (grid.width + 32, grid.height + 32 + h + 16), (30, 30, 33, 255))
    out.alpha_composite(grid, (16, 16))
    x = 16
    for t in tiles:
        tt = t if s >= 1 else resize_pm(t, (max(1, round(w * s)), max(1, round(h * s))))
        out.alpha_composite(tt, (x, grid.height + 24))
        x += tt.width + 6
    return out


class IsoTiles(OpExecutor):
    kind = "op.iso_tiles"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        asset = ident(str(params.get("asset") or "iso"), "iso")
        tile_w = int(params.get("tile", 128))
        count = int(params.get("variants", 6))
        seed = int(params.get("seed", 7))
        textures = [await _load(ctx, r) for r in refs[:8]]
        if params.get("seamless") in (True, "true", "True", 1, "1"):
            from .tile import seamless_info

            flatten = float(params.get("flatten", 0.5))
            try:
                textures = [(await asyncio.to_thread(seamless_info, t, 0.125, 3, flatten, "auto"))[0]
                            for t in textures]
            except ValueError as e:
                raise StepError("op_bad_params", str(e)) from e
        try:
            tiles = await asyncio.to_thread(iso_tile_set, textures, tile_w, count, seed,
                                            float(params.get("edge", 0.12)), float(params.get("jitter", 0.05)))
        except ValueError as e:
            raise StepError("op_bad_params", str(e)) from e
        qa = await asyncio.to_thread(grid_qa, tiles, periodic_resize(textures[0], tile_w), 4, seed)
        names = [f"{asset}_tile_{i:02d}" for i in range(len(tiles))]
        out: list[BlobRef] = [await _put_png(ctx, t, f"{n}.png") for t, n in zip(tiles, names)]
        atlas_ref = await _put_png(ctx, await asyncio.to_thread(strip, tiles), f"{asset}_iso_tiles.png")
        grid4, _, _ = await asyncio.to_thread(compose_grid, tiles, 4, seed, (30, 30, 33, 255))
        qa_ref = await _put_png(ctx, grid4, f"{asset}_grid4.png")
        prev_ref = await _put_png(ctx, await asyncio.to_thread(tiles_preview, tiles, seed), f"{asset}_iso_preview.png")
        doc = {"type": ISO_TILES_TYPE, "asset": asset, "tile": [tile_w, tile_w // 2], "seed": seed,
               "variants": [{"name": n, "file": f"{n}.png", "hash": r.hash} for n, r in zip(names, out)],
               "atlas": {"file": atlas_ref.name, "hash": atlas_ref.hash, "cols": len(tiles)},
               "qa": {k: v for k, v in qa.items() if k != "picks"}, "sample": qa["picks"]}
        doc_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=ISO_TILES_FILE,
                                            mime="application/json")
        warnings = [] if qa["seamless"] else [f"ghép 4×4: hở {qa['gaps_px']}px, chồng {qa['overlap_px']}px, "
                                              f"lệch mép {qa['rim_max_diff']}"]
        return StepResult(outputs={"images": out, "atlas": [atlas_ref], "grid": [qa_ref], "preview": [prev_ref],
                                   "iso": [doc_ref]},
                          meta={"tile": [tile_w, tile_w // 2], "variants": len(out),
                                **{k: v for k, v in qa.items() if k != "picks"}, "warnings": warnings})


def diamond_outside(alpha: np.ndarray, foot: tuple[float, float], tile_w: int, cells: int, tol: float = 1.5) -> int:
    """Opaque pixels below the footprint's left/right corners yet outside its diamond: parts overhanging the cell."""
    h, w = alpha.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64) + 0.5
    hw, hh = cells * tile_w / 2 + tol, cells * tile_w / 4 + tol / 2
    inside = np.abs(xx - foot[0]) / hw + np.abs(yy - foot[1]) / hh <= 1
    lower = yy > foot[1]
    return int(((alpha > 128) & lower & ~inside).sum())


def ground_tile(w: int, shade: int) -> Image.Image:
    h = w // 2
    a = diamond_mask(w, h)
    out = np.zeros((h, w, 4), np.uint8)
    out[a] = (shade, shade + 6, shade + 2, 255)
    edge = a & ~np.roll(a, 1, 0) | a & ~np.roll(a, -1, 0)
    out[edge] = (shade + 25, shade + 30, shade + 26, 255)
    return Image.fromarray(out, "RGBA")


def prop_preview(sprites: list[Image.Image], anchor: tuple[float, float], tile_w: int, cells: int) -> Image.Image:
    """Each direction stood on the front cell of a 3x3 (in cells) checker of plain diamonds."""
    n = cells + 2
    tiles = [ground_tile(tile_w, 70), ground_tile(tile_w, 88)]
    th = tile_w // 2
    panels = []
    for spr in sprites:
        cells_xy, size = grid_positions(n, tile_w, th)
        ground = Image.new("RGBA", (size[0], size[1] + spr.height), (30, 30, 33, 255))
        top = spr.height
        for i, j, x, y in cells_xy:
            ground.alpha_composite(tiles[(i + j) % 2], (x, y + top))
        fi, fj = n - 2, n - 2
        cx = (fi - fj) * tile_w / 2 + (n - 1) * tile_w / 2 + tile_w / 2
        cy = (fi + fj) * th / 2 + th / 2 + top
        ground.alpha_composite(spr, (round(cx - anchor[0]), round(cy - anchor[1])))
        panels.append(ground)
    out = Image.new("RGBA", (sum(p.width for p in panels) + 8 * (len(panels) + 1), max(p.height for p in panels) + 16),
                    (30, 30, 33, 255))
    x = 8
    for p in panels:
        out.alpha_composite(p, (x, 8))
        x += p.width + 8
    return out


class IsoProp(OpExecutor):
    kind = "op.iso_prop"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        meta_refs = inputs.get("json") or []
        if len(meta_refs) != 1:
            raise StepError("op_bad_input", "expected the iso_render.json")
        meta = await _read_json(ctx, meta_refs[0])
        asset = ident(str(params.get("asset") or meta.get("name") or "prop"), "prop")
        ss = int(meta.get("supersample", 1))
        cw, ch = (int(v) for v in meta["canvas"])
        tile_w, cells = int(meta["tile_w"]), int(meta["cells"])
        foot, anchor = tuple(meta["foot"]), tuple(meta["anchor"])
        dirs = [str(f["dir"]) for f in meta["frames"]]
        if len(dirs) != len(refs):
            raise StepError("op_bad_input", f"{len(refs)} renders for {len(dirs)} directions")
        sprites, outside = [], []
        for r in refs:
            img = await _load(ctx, r)
            if img.size != (cw * ss, ch * ss):
                raise StepError("op_bad_input", f"render {r.name} is {img.size}, expected {(cw * ss, ch * ss)}")
            spr = img.convert("RGBA") if ss == 1 else await asyncio.to_thread(resize_pm, img, (cw, ch))
            sprites.append(spr)
            outside.append(diamond_outside(np.asarray(spr)[..., 3], foot, tile_w, cells))  # type: ignore[arg-type]
        names = [f"{asset}_{d}" for d in dirs]
        out = [await _put_png(ctx, s, f"{n}.png") for s, n in zip(sprites, names)]
        strip_ref = await _put_png(ctx, await asyncio.to_thread(strip, sprites), f"{asset}_iso_prop.png")
        prev = await asyncio.to_thread(prop_preview, sprites, anchor, tile_w, cells)  # type: ignore[arg-type]
        prev_ref = await _put_png(ctx, prev, f"{asset}_iso_preview.png")
        qa = {"outside_px": outside, "on_tile": max(outside) <= max(4, cw // 64)}
        doc = {"type": ISO_PROP_TYPE, "asset": asset, "tile": [tile_w, tile_w // 2], "cells": cells,
               "canvas": [cw, ch], "foot": list(foot), "anchor": list(anchor), "height_px": meta.get("height_px"),
               "dirs": [{"dir": d, "name": n, "file": f"{n}.png", "hash": r.hash} for d, n, r in zip(dirs, names, out)],
               "strip": {"file": strip_ref.name, "hash": strip_ref.hash}, "qa": qa}
        doc_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=ISO_PROP_FILE,
                                            mime="application/json")
        warnings = [] if qa["on_tile"] else [f"prop tràn ra ngoài ô: {max(outside)}px"]
        return StepResult(outputs={"images": out, "strip": [strip_ref], "preview": [prev_ref], "iso": [doc_ref]},
                          meta={"canvas": [cw, ch], "anchor": list(anchor), **qa, "warnings": warnings,
                                "cells": cells, "tile_w": tile_w})


ISO_OPS = (IsoTiles, IsoProp)
