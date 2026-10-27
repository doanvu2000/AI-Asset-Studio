"""Seamless textures: every array op wraps around the edges (np.roll / modular indexing) so outputs stay tileable."""
from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png
from .pixel import PALETTE_CHOICES, check_params, plan_palette, quantize, to_hex

REPORT_NAME = "tile_check.json"
PREVIEW_NAME = "tile_preview.png"
ATLAS_NAME = "tileset.png"
MIN_TILE = 16


def _arr(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"), np.float32) / 255.0


def _rgb(img: Image.Image) -> np.ndarray:
    """Textures are opaque: alpha is dropped rather than blurred into the colour."""
    return np.asarray(img.convert("RGB"), np.float32) / 255.0


def _img(a: np.ndarray) -> Image.Image:
    a8 = np.clip(np.round(a * 255.0), 0, 255).astype(np.uint8)
    if a8.shape[-1] == 3:
        a8 = np.concatenate([a8, np.full(a8.shape[:2] + (1,), 255, np.uint8)], -1)
    return Image.fromarray(a8, "RGBA")


def _lum(a: np.ndarray) -> np.ndarray:
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def box_blur(a: np.ndarray, r: int, axes: Sequence[int] = (0, 1), wrap: bool | Sequence[bool] = True) -> np.ndarray:
    """Mean over a (2r+1) window, wrapping around (or clamping at) the edges; cumulative sums keep it O(n)."""
    out = np.asarray(a, np.float32)
    if r <= 0:
        return out
    wraps = [wrap] * len(axes) if isinstance(wrap, bool) else list(wrap)
    for ax, wr in zip(axes, wraps):
        n = out.shape[ax]
        raw = np.arange(-r - 1, n + r)
        idx = raw % n if wr else np.clip(raw, 0, n - 1)
        c = np.cumsum(np.take(out, idx, axis=ax), axis=ax, dtype=np.float64)
        hi = np.take(c, np.arange(2 * r + 1, n + 2 * r + 1), axis=ax)
        lo = np.take(c, np.arange(0, n), axis=ax)
        out = ((hi - lo) / (2 * r + 1)).astype(np.float32)
    return out


def soft_blur(a: np.ndarray, r: int, axes: Sequence[int] = (0, 1), wrap: bool | Sequence[bool] = True) -> np.ndarray:
    """Three box passes ≈ a Gaussian of sigma ≈ r."""
    rb = max(1, round(r * 0.8)) if r > 0 else 0
    for _ in range(3):
        a = box_blur(a, rb, axes, wrap)
    return a


def _closed_path(cost: np.ndarray) -> np.ndarray:
    """Min-cost top-to-bottom path (one column per row, steps of at most 1) that ends in the column it starts in,
    so the cut also lines up across the vertical wrap."""
    h, w = cost.shape
    start = int(np.argmin(cost.mean(0)))
    inf = np.inf
    acc = np.full(w, inf)
    acc[start] = cost[0, start]
    back = np.zeros((h, w), np.int8)
    for y in range(1, h):
        left = np.concatenate([[inf], acc[:-1]])
        right = np.concatenate([acc[1:], [inf]])
        stack = np.stack([left, acc, right])
        k = stack.argmin(0)
        back[y] = k - 1
        acc = stack[k, np.arange(w)] + cost[y]
    path = np.empty(h, np.int64)
    x = start
    for y in range(h - 1, -1, -1):
        path[y] = x
        x = x + int(back[y, x]) if y else x
    return path


def join(x: np.ndarray, y: np.ndarray, feather: int = 3) -> np.ndarray:
    """Overlap window that starts as exactly `x` (column 0) and ends as exactly `y` (last column): detail switches
    along the min-error cut, the low band ramps across the window so a brightness step never shows as a line."""
    o = x.shape[1]
    cost = box_blur(((x - y) ** 2).sum(-1), 1, wrap=(True, False)) + 1e-6
    path = _closed_path(cost)
    m = (np.arange(o)[None, :] >= path[:, None]).astype(np.float32)
    if feather > 0:
        m = soft_blur(m, feather, (1,), wrap=False)
    m[:, 0], m[:, -1] = 0.0, 1.0
    r = max(1, o // 4)
    lx, ly = soft_blur(x, r, wrap=(True, False)), soft_blur(y, r, wrap=(True, False))
    ramp = np.linspace(0, 1, o, dtype=np.float32)[None, :, None]
    m3 = m[..., None]
    return m3 * (y - ly) + (1 - m3) * (x - lx) + ramp * ly + (1 - ramp) * lx


def _overlap(n: int, frac: float) -> int:
    o = max(4, round(n * frac))
    if n - o < MIN_TILE or o > n // 3:
        raise ValueError(f"image too small for an overlap of {frac:.3f} ({n}px)")
    return o


def _quilt_x(a: np.ndarray, frac: float, feather: int) -> np.ndarray:
    # The last `o` columns run into the first `o` across the wrap; overlapping them and dropping one copy makes
    # every column appear exactly once, so nothing repeats inside the tile (width shrinks by o).
    w = a.shape[1]
    o = _overlap(w, frac)
    return np.concatenate([join(a[:, w - o:], a[:, :o], feather), a[:, o:w - o]], 1)


def detrend(a: np.ndarray) -> np.ndarray:
    """Subtracts the best-fit brightness plane. A lit-from-one-side source would otherwise put its dark and bright
    edges next to each other inside the overlap window, which reads as a band in every tile."""
    h, w = a.shape[:2]
    g = 16
    cells = _cells(_lum(a), g)
    yy, xx = np.mgrid[0:g, 0:g] / (g - 1) - 0.5
    coef, *_ = np.linalg.lstsq(np.stack([np.ones(g * g), xx.ravel(), yy.ravel()], 1), cells.ravel(), rcond=None)
    xs = (np.arange(w) / max(1, w - 1) - 0.5)[None, :]
    ys = (np.arange(h) / max(1, h - 1) - 0.5)[:, None]
    plane = coef[1] * xs + coef[2] * ys
    return np.clip(a - plane.astype(np.float32)[..., None], 0, 1)


def flatten_light(a: np.ndarray, strength: float, wrap: bool = True) -> np.ndarray:
    """Removes the large-scale brightness drift that makes a repeated tile read as a grid of patches."""
    if strength <= 0:
        return a
    lum = _lum(a)
    low = soft_blur(lum, max(2, round(min(lum.shape) / 6)), wrap=wrap)
    out = a.copy()
    out[..., :3] += (strength * (lum.mean() - low))[..., None]
    return np.clip(out, 0, 1)


def seamless(img: Image.Image, overlap: float = 0.125, feather: int = 3, flatten: float = 0.5) -> Image.Image:
    """Wrap-overlap quilting on both axes; the result is `overlap` smaller per axis (tile_resize restores the size)."""
    a = _rgb(img)
    if min(a.shape[:2]) < MIN_TILE:
        raise ValueError(f"image too small for seamless ({img.width}x{img.height})")
    if flatten > 0:
        a = detrend(a)
    out = _quilt_x(a, overlap, feather)
    out = _quilt_x(out.transpose(1, 0, 2), overlap, feather).transpose(1, 0, 2)
    return _img(flatten_light(np.ascontiguousarray(out), flatten))


def match_edges(img: Image.Image, base: Image.Image, overlap: float = 0.08, feather: int = 3,
                flatten: float = 0.5) -> Image.Image:
    """Variant whose outermost lines are `base`'s, joined by a narrow quilted window per edge, so variants of one
    tileable base tile with each other in any order while keeping their own content up to the windows."""
    if base.size != img.size:
        img = img.resize(base.size, Image.Resampling.LANCZOS)
    b = _rgb(base)
    a = flatten_light(detrend(_rgb(img)) if flatten > 0 else _rgb(img), flatten, wrap=False)
    # Variants from one prompt still differ in overall tone; matching mean/std keeps a mixed tiling from reading
    # as a checkerboard.
    am, asd = a.mean((0, 1)), a.std((0, 1)) + 1e-6
    a = np.clip((a - am) * (b.std((0, 1)) / asd) + b.mean((0, 1)), 0, 1).astype(np.float32)

    def edges(v: np.ndarray, ref: np.ndarray) -> np.ndarray:
        w = v.shape[1]
        o = _overlap(w, overlap)
        return np.concatenate([join(ref[:, :o], v[:, :o], feather), v[:, o:w - o],
                               join(v[:, w - o:], ref[:, w - o:], feather)], 1)

    out = edges(a, b)
    out = edges(out.transpose(1, 0, 2), b.transpose(1, 0, 2)).transpose(1, 0, 2)
    return _img(out)


def wrap_resize(img: Image.Image, w: int, h: int, resample: Image.Resampling = Image.Resampling.LANCZOS) -> Image.Image:
    """Resamples with the image wrap-padded by the filter support, so taps at the edges read the wrapped neighbours."""
    if img.size == (w, h):
        return img
    src = img.convert("RGBA")
    sw, sh = src.size
    mx = min(sw, math.ceil(3 * max(1.0, sw / w)) + 2)
    my = min(sh, math.ceil(3 * max(1.0, sh / h)) + 2)
    pad = np.pad(np.asarray(src), ((my, my), (mx, mx), (0, 0)), mode="wrap")
    return Image.fromarray(pad, "RGBA").resize((w, h), resample, box=(mx, my, mx + sw, my + sh))


def tile_grid(tiles: Sequence[Image.Image], n: int = 3, cell: int | None = None, seed: int = 0) -> Image.Image:
    """n×n preview; with several variants each cell picks one (fixed seed), which shows whether they mix."""
    cw = cell or tiles[0].width
    ch = round(cw * tiles[0].height / tiles[0].width)
    cells = [t.convert("RGBA").resize((cw, ch), Image.Resampling.LANCZOS) if t.size != (cw, ch) else t.convert("RGBA")
             for t in tiles]
    rng = np.random.default_rng(seed)
    out = Image.new("RGBA", (cw * n, ch * n))
    for y in range(n):
        for x in range(n):
            out.paste(cells[int(rng.integers(len(cells))) if len(cells) > 1 else 0], (x * cw, y * ch))
    return out


def _seam_ratio(a: np.ndarray) -> dict[str, float]:
    """Mean colour step across each wrap edge over a high percentile of the steps between interior neighbour lines
    (≤1 when seamless): grout or plank lines make some interior lines legitimately sharp, so the mean misleads."""
    out: dict[str, float] = {}
    for name, ax in (("x", 1), ("y", 0)):
        n = a.shape[ax]
        other = tuple(i for i in range(a.ndim) if i != ax)
        lines = np.abs(np.diff(a, axis=ax)).mean(axis=other)
        wrap = np.abs(np.take(a, [0], axis=ax) - np.take(a, [n - 1], axis=ax)).mean()
        out[name] = float(wrap / max(float(np.percentile(lines, 95)), 1e-4))
    return out


def _duplicate(a: np.ndarray) -> float:
    """Share of pixels equal to their half-tile partner, above what an unrelated shift gives: a tile made of two
    copies of itself shows as a visible grid at half its size."""
    h, w = a.shape[:2]

    def same(dy: int, dx: int) -> float:
        return float((np.abs(np.roll(a, (dy, dx), (0, 1)) - a).max(-1) < 2 / 255).mean())

    base = same(max(1, h // 3), max(1, w // 5))
    return max(0.0, max(same(0, w // 2), same(h // 2, 0), same(h // 2, w // 2)) - base)


def _cells(lum: np.ndarray, g: int) -> np.ndarray:
    h, w = lum.shape
    ch, cw = max(1, h // g), max(1, w // g)
    return lum[:ch * g, :cw * g].reshape(g, ch, g, cw).mean((1, 3))


def tile_report(img: Image.Image, min_score: float = 70.0) -> dict[str, Any]:
    a = _arr(img)[..., :3]
    h, w = a.shape[:2]
    ratios = _seam_ratio(a)
    if min(h, w) >= 64:
        # Fine noise hides a low-frequency jump at full resolution; a 4× box reduction exposes it.
        small = a[:h - h % 4, :w - w % 4].reshape(h // 4, 4, w // 4, 4, 3).mean((1, 3))
        ratios.update({f"{k}4": v for k, v in _seam_ratio(small).items()})
    worst = max(ratios.values())
    seam_score = 100.0 * math.exp(-0.35 * max(0.0, worst - 1.1))

    lum = _lum(a)
    grid = _cells(lum, 8)
    yy, xx = np.mgrid[0:8, 0:8] / 7.0 - 0.5
    design = np.stack([np.ones(64), xx.ravel(), yy.ravel()], 1)
    coef, *_ = np.linalg.lstsq(design, grid.ravel(), rcond=None)
    tilt = float(math.hypot(coef[1], coef[2]))
    grad_pen = float(np.clip((tilt - 0.04) / 0.16, 0, 1))
    # Tile-sized blobs (one dark stone, a bright patch) are what the eye locks onto when the tile repeats.
    lowfreq = float(grid.std() / max(lum.std(), 1e-4))
    rep_pen = float(np.clip((lowfreq - 0.35) / 0.4, 0, 1))

    dup = _duplicate(a)
    dup_pen = float(np.clip((dup - 0.08) * 4, 0, 1))
    score = seam_score * (1 - 0.5 * grad_pen) * (1 - 0.25 * rep_pen) * (1 - dup_pen)
    issues = [k for k, bad in (("seam", seam_score < 70), ("gradient", grad_pen > 0.3), ("repetition", rep_pen > 0.5),
                               ("duplicate", dup > 0.08)) if bad]
    return {"score": round(score, 1), "seam_score": round(seam_score, 1),
            "seam_ratio": {k: round(v, 3) for k, v in ratios.items()},
            "gradient": round(tilt, 4), "lowfreq": round(lowfreq, 4), "duplicate": round(dup, 4), "issues": issues,
            "passed": score >= min_score, "width": w, "height": h}


def height_normal(img: Image.Image, strength: float = 4.0, blur: int = 1, green: str = "opengl") -> Image.Image:
    """Tangent-space normal map from luminance as height (Sobel with wrap-around)."""
    hgt = soft_blur(_lum(_arr(img)), blur) if blur > 0 else _lum(_arr(img))
    sx = np.roll(hgt, -1, 1) - np.roll(hgt, 1, 1)
    sy = np.roll(hgt, -1, 0) - np.roll(hgt, 1, 0)
    dx = (np.roll(sx, 1, 0) + 2 * sx + np.roll(sx, -1, 0)) / 8
    dy = (np.roll(sy, 1, 1) + 2 * sy + np.roll(sy, -1, 1)) / 8
    # Image rows grow downwards; OpenGL (Unity, Godot) wants +G pointing up the texture.
    n = np.stack([-dx * strength, dy * strength if green == "opengl" else -dy * strength, np.ones_like(hgt)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    rgb = (n * 0.5 + 0.5)
    return _img(np.concatenate([rgb, np.ones((*hgt.shape, 1))], -1))


def tile_pixel(img: Image.Image, size: int, colors: int = 16, palette: str = "auto",
               dither: str = "none") -> tuple[Image.Image, list[str]]:
    check_params(colors, palette, dither)
    h = max(1, round(size * img.height / img.width))
    small = np.array(wrap_resize(img.convert("RGBA"), size, h, Image.Resampling.BOX))
    small[..., 3] = 255
    solid = np.ones(small.shape[:2], bool)
    # No outline and no despeckle: both treat the canvas border as an edge and would break the wrap.
    plan = plan_palette([(small, solid)], size / img.width, colors, palette, None, "none", dither, 0.6, False)
    return quantize(plan, small, solid), [to_hex(c) for c in plan.pal]


def _stem(ref: BlobRef, i: int) -> str:
    return (ref.name or f"texture_{i:02d}").rsplit(".", 1)[0]


def _enabled(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "")
    return v is None or bool(v)


class _TileOp(OpExecutor):
    @staticmethod
    def _tick(ctx: StepContext, done: int, total: int, label: str | None = None) -> None:
        if ctx.cancelled.is_set():
            raise asyncio.CancelledError
        ctx.progress(done, total, label=label)


class Seamless(_TileOp):
    kind = "op.seamless"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        name = str(params.get("name") or "texture")
        match = _enabled(params.get("match_edges", False)) and len(refs) > 1
        feather, flatten = int(params.get("feather", 3)), float(params.get("flatten", 0.5))
        overlap, edge_overlap = float(params.get("overlap", 0.125)), float(params.get("edge_overlap", 0.08))
        if not (0.02 <= overlap <= 0.3 and 0.02 <= edge_overlap <= 0.3 and 0 <= feather <= 16):
            raise StepError("op_bad_params", "overlap/edge_overlap must be within 0.02..0.3, feather 0..16 px")
        out: list[BlobRef] = []
        base: Image.Image | None = None
        for i, ref in enumerate(refs):
            self._tick(ctx, i, len(refs), "seamless")
            src = await _load(ctx, ref)
            try:
                if base is not None:
                    res = await asyncio.to_thread(match_edges, src, base, edge_overlap, feather, flatten)
                else:
                    res = await asyncio.to_thread(seamless, src, overlap, feather, flatten)
            except ValueError as e:
                raise StepError("op_bad_params", str(e)) from e
            if match and base is None:
                base = res
            label = f"{name}_{i + 1:02d}" if len(refs) > 1 else name
            out.append(await _put_png(ctx, res, f"{label}_seamless.png"))
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": out},
                          meta={"tileable": True, "match_edges": match, "count": len(out)})


class TileResize(_TileOp):
    kind = "op.tile_resize"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        size = int(params.get("size") or 0)
        levels = int(params.get("levels") or 0)
        if size and not MIN_TILE <= size <= 4096:
            raise StepError("op_bad_params", f"size must be within {MIN_TILE}..4096")
        if not 0 <= levels <= 6:
            raise StepError("op_bad_params", "levels must be within 0..6")
        main: list[BlobRef] = []
        sizes: list[BlobRef] = []
        for i, ref in enumerate(refs):
            self._tick(ctx, i, len(refs), "resize")
            src = await _load(ctx, ref)
            w = size or src.width
            h = max(1, round(w * src.height / src.width))
            top = await asyncio.to_thread(wrap_resize, src, w, h)
            main.append(ref if (w, h) == src.size else await _put_png(ctx, top, ref.name or f"texture_{i:02d}.png"))
            for k in range(1, levels + 1):
                lw, lh = w >> k, h >> k
                if min(lw, lh) < MIN_TILE:
                    break
                small = await asyncio.to_thread(wrap_resize, top, lw, lh)
                sizes.append(await _put_png(ctx, small, f"{_stem(ref, i)}_{lw}.png"))
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": main, "sizes": sizes}, meta={"tileable": True, "size": size or None})


class TileNormal(_TileOp):
    kind = "op.tile_normal"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        if not _enabled(params.get("enabled", True)):
            return StepResult(outputs={"images": []}, meta={"skipped": True})
        refs = _images_in(inputs)
        strength, blur = float(params.get("strength", 4.0)), int(params.get("blur", 1))
        green = str(params.get("green") or "opengl")
        if green not in ("opengl", "directx"):
            raise StepError("op_bad_params", "green must be opengl or directx")
        out: list[BlobRef] = []
        for i, ref in enumerate(refs):
            self._tick(ctx, i, len(refs), "normal")
            src = await _load(ctx, ref)
            res = await asyncio.to_thread(height_normal, src, strength, blur, green)
            out.append(await _put_png(ctx, res, f"{_stem(ref, i)}_normal.png"))
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": out}, meta={"normal": True, "tileable": True, "green": green})


class TilePixel(_TileOp):
    kind = "op.tile_pixel"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        size = int(params.get("size") or 0)
        if not size:
            return StepResult(outputs={"images": [], "palette": []}, meta={"skipped": True})
        if not 8 <= size <= 512:
            raise StepError("op_bad_params", "size must be within 8..512 (0 = off)")
        refs = _images_in(inputs)
        colors, palette = int(params.get("colors") or 16), str(params.get("palette") or "auto")
        dither = str(params.get("dither") or "none")
        out: list[BlobRef] = []
        pals: list[list[str]] = []
        for i, ref in enumerate(refs):
            self._tick(ctx, i, len(refs), "pixel")
            src = await _load(ctx, ref)
            try:
                res, pal = await asyncio.to_thread(tile_pixel, src, size, colors, palette, dither)
            except ValueError as e:
                raise StepError("op_bad_params", str(e)) from e
            pals.append(pal)
            out.append(await _put_png(ctx, res, f"{_stem(ref, i)}_pixel.png"))
        info = {"colors": pals[0] if len(pals) == 1 else pals, "size": size, "tileable": True}
        pal_ref = await ctx.blobs.put_bytes(json.dumps(info, indent=1).encode(), name="palette.json",
                                            mime="application/json")
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": out, "palette": [pal_ref]},
                          meta={"pixel": True, "filter": "point", "tileable": True, "frame_w": size})


class TileCheck(_TileOp):
    kind = "op.tile_check"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        min_score = float(params.get("min_score", 70))
        grid = int(params.get("grid", 3))
        if not 2 <= grid <= 8:
            raise StepError("op_bad_params", "grid must be within 2..8")
        imgs, reports = [], []
        for i, ref in enumerate(refs):
            self._tick(ctx, i, len(refs) + 1, "check")
            img = await _load(ctx, ref)
            imgs.append(img)
            rep = await asyncio.to_thread(tile_report, img, min_score)
            reports.append({"name": ref.name, "hash": ref.hash, **rep})
        worst = min(reports, key=lambda r: r["score"])
        cell = max(MIN_TILE, min(imgs[0].width, int(params.get("preview_px", 1536)) // grid))
        preview = await asyncio.to_thread(tile_grid, imgs, grid, cell)
        outputs: Outputs = {"images": list(refs), "preview": [await _put_png(ctx, preview, PREVIEW_NAME)]}
        if len(imgs) > 1:
            atlas = Image.new("RGBA", (imgs[0].width * len(imgs), imgs[0].height))
            for i, img in enumerate(imgs):
                atlas.paste(img.resize(imgs[0].size) if img.size != imgs[0].size else img, (i * imgs[0].width, 0))
            outputs["atlas"] = [await _put_png(ctx, atlas, ATLAS_NAME)]
        for port in ("nmap", "pixel", "sizes"):
            if inputs.get(port):
                outputs[port] = list(inputs[port])
        report = {"tileable": True, "score": worst["score"], "seam_score": worst["seam_score"],
                  "passed": all(r["passed"] for r in reports), "min_score": min_score, "grid": grid,
                  "variants": len(imgs),
                  "textures": reports, "normal": [r.hash for r in inputs.get("nmap") or []],
                  "pixel": [r.hash for r in inputs.get("pixel") or []],
                  "sizes": [r.hash for r in inputs.get("sizes") or []]}
        outputs["report"] = [await ctx.blobs.put_bytes(json.dumps(report, indent=1).encode(), name=REPORT_NAME,
                                                       mime="application/json")]
        if _enabled(params.get("fail", False)) and not report["passed"]:
            raise StepError("tile_check_failed", f"seam score {worst['score']} < {min_score}",
                            detail={"report": report})
        ctx.progress(len(refs) + 1, len(refs) + 1)
        return StepResult(outputs=outputs, meta={"tileable": True, "score": report["score"],
                                                 "seam_score": report["seam_score"], "passed": report["passed"]})


TILE_OPS: tuple[type[OpExecutor], ...] = (Seamless, TileResize, TileNormal, TilePixel, TileCheck)
__all__ = ["PALETTE_CHOICES", "TILE_OPS", "seamless", "match_edges", "tile_report", "height_normal", "wrap_resize"]
