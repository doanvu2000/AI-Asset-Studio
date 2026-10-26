"""Pixel-art conversion for whole sprite sets: one scale and one shared palette for every frame."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png

PALETTES: dict[str, tuple[str, ...]] = {
    "pico8": ("000000", "1d2b53", "7e2553", "008751", "ab5236", "5f574f", "c2c3c7", "fff1e8",
              "ff004d", "ffa300", "ffec27", "00e436", "29adff", "83769c", "ff77a8", "ffccaa"),
    "sweetie16": ("1a1c2c", "5d275d", "b13e53", "ef7d57", "ffcd75", "a7f070", "38b764", "257179",
                  "29366f", "3b5dc9", "41a6f6", "73eff7", "f4f4f4", "94b0c2", "566c86", "333c57"),
    "db16": ("140c1c", "442434", "30346d", "4e4a4e", "854c30", "346524", "d04648", "757161",
             "597dce", "d27d2c", "8595a1", "6daa2c", "d2aa99", "6dc2ca", "dad45e", "deeed6"),
    "db32": ("000000", "222034", "45283c", "663931", "8f563b", "df7126", "d9a066", "eec39a",
             "fbf236", "99e550", "6abe30", "37946e", "4b692f", "524b24", "323c39", "3f3f74",
             "306082", "5b6ee1", "639bff", "5fcde4", "cbdbfc", "ffffff", "9badb7", "847e87",
             "696a6a", "595652", "76428a", "ac3232", "d95763", "d77bba", "8f974a", "8a6f30"),
    "endesga32": ("be4a2f", "d77643", "ead4aa", "e4a672", "b86f50", "733e39", "3e2731", "a22633",
                  "e43b44", "f77622", "feae34", "fee761", "63c74d", "3e8948", "265c42", "193c3e",
                  "124e89", "0099db", "2ce8f5", "ffffff", "c0cbdc", "8b9bb4", "5a6988", "3a4466",
                  "262b44", "181425", "ff0044", "68386c", "b55088", "f6757a", "e8b796", "c28569"),
}
PALETTE_CHOICES = ["auto", *PALETTES]
OUTLINES = ("none", "dark")
DITHERS = ("none", "bayer")

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])
_M1I, _M2I = np.linalg.inv(_M1), np.linalg.inv(_M2)
_BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16.0 + 1 / 32
_N8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
_N4 = ((-1, 0), (1, 0), (0, -1), (0, 1))


def to_oklab(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return np.cbrt(lin @ _M1.T) @ _M2.T


def from_oklab(lab: np.ndarray) -> np.ndarray:
    lin = np.clip((np.asarray(lab) @ _M2I.T) ** 3 @ _M1I.T, 0, 1)
    c = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
    return np.clip(np.round(c * 255), 0, 255).astype(np.uint8)


def parse_hex(c: str) -> tuple[int, int, int]:
    h = c.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) not in (6, 8):
        raise ValueError(f"bad color: {c!r}")
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        raise ValueError(f"bad color: {c!r}") from None


def to_hex(rgb: Sequence[int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(int(v) for v in rgb[:3]))


def _median_cut(lab: np.ndarray, w: np.ndarray, k: int) -> np.ndarray:
    boxes = [np.arange(len(lab))]
    while len(boxes) < k:
        # Split the box with the most weighted spread; boxes of one color cannot split.
        scores = [float(np.ptp(lab[b], axis=0).max() * w[b].sum()) if len(b) > 1 else -1.0 for b in boxes]
        i = int(np.argmax(scores))
        if scores[i] <= 0:
            break
        b = boxes.pop(i)
        axis = int(np.argmax(np.ptp(lab[b], axis=0)))
        b = b[np.argsort(lab[b, axis], kind="stable")]
        cw = np.cumsum(w[b])
        cut = int(np.clip(np.searchsorted(cw, cw[-1] / 2), 0, len(b) - 2)) + 1
        boxes += [b[:cut], b[cut:]]
    return np.array([np.average(lab[b], axis=0, weights=w[b]) for b in boxes])


def kmeans_palette(rgb: np.ndarray, k: int, iters: int = 16) -> np.ndarray:
    """k colors (uint8 RGB) for the given pixels; median-cut seeded Lloyd in OKLab, so no RNG is involved."""
    px = np.asarray(rgb, np.uint8).reshape(-1, 3)
    if len(px) == 0:
        return np.zeros((1, 3), np.uint8)
    uniq, counts = np.unique(px, axis=0, return_counts=True)
    if len(uniq) <= k:
        return uniq
    lab = to_oklab(uniq)
    # sqrt weights keep small accents (eyes, trims) from being swallowed by large flat areas.
    w = np.sqrt(counts.astype(np.float64))
    cent = _median_cut(lab, w, k)
    for _ in range(iters):
        lbl = _nearest(lab, cent)
        new = cent.copy()
        for j in range(len(cent)):
            m = lbl == j
            if m.any():
                new[j] = np.average(lab[m], axis=0, weights=w[m])
        if np.allclose(new, cent, atol=1e-6):
            break
        cent = new
    pal = from_oklab(cent)
    _, first = np.unique(pal, axis=0, return_index=True)
    pal = pal[np.sort(first)]
    return pal[np.argsort(to_oklab(pal)[:, 0], kind="stable")]


def _nearest(lab: np.ndarray, pal_lab: np.ndarray) -> np.ndarray:
    d = ((lab[:, None, :] - pal_lab[None, :, :]) ** 2).sum(-1)
    return d.argmin(1)


@dataclass
class PixelResult:
    frames: list[Image.Image]
    palette: list[tuple[int, int, int]]
    outline_colors: list[tuple[int, int, int]] = field(default_factory=list)
    size: tuple[int, int] = (0, 0)
    scale: float = 1.0
    source: str = "auto"

    def palette_json(self) -> dict[str, Any]:
        return {"colors": [to_hex(c) for c in self.palette], "outline": [to_hex(c) for c in self.outline_colors],
                "count": len(self.palette), "source": self.source, "frame_w": self.size[0],
                "frame_h": self.size[1], "scale": round(self.scale, 6)}

    def swatch(self, cell: int = 8) -> Image.Image:
        img = Image.new("RGBA", (cell * len(self.palette), cell))
        for i, c in enumerate(self.palette):
            img.paste((*c, 255), (i * cell, 0, (i + 1) * cell, cell))
        return img


def _shift(a: np.ndarray, dy: int, dx: int, fill: Any) -> np.ndarray:
    out = np.full_like(a, fill)
    h, w = a.shape[:2]
    out[max(dy, 0):h + min(dy, 0), max(dx, 0):w + min(dx, 0)] = a[max(-dy, 0):h - max(dy, 0), max(-dx, 0):w - max(dx, 0)]
    return out


def _downsample(img: Image.Image, scale: float) -> np.ndarray:
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    # Premultiplied box average: transparent pixels add no dark fringe and every source pixel counts.
    small = img.convert("RGBA").convert("RGBa").resize((w, h), Image.Resampling.BOX).convert("RGBA")
    return np.array(small)


def _clean_mask(solid: np.ndarray) -> np.ndarray:
    n8 = sum(_shift(solid, dy, dx, False).astype(np.int8) for dy, dx in _N8)
    n4 = sum(_shift(solid, dy, dx, False).astype(np.int8) for dy, dx in _N4)
    return (solid & (n8 > 0)) | (~solid & (n4 == 4))


def _fill_holes_rgb(rgb: np.ndarray, was: np.ndarray, now: np.ndarray) -> None:
    holes = now & ~was
    if not holes.any():
        return
    acc = sum(_shift(rgb.astype(np.float64) * was[..., None], dy, dx, 0) for dy, dx in _N4)
    rgb[holes] = np.round(acc[holes] / 4).astype(np.uint8)


def _despeckle(idx: np.ndarray, pal_lab: np.ndarray, tol: float) -> None:
    """Single pixels whose color no 8-neighbor shares are merged into the dominant neighbor if it is similar."""
    neigh = np.stack([_shift(idx, dy, dx, -1) for dy, dx in _N8])
    lone = (idx >= 0) & (neigh != idx).all(0)
    for y, x in zip(*np.nonzero(lone)):
        ns = neigh[:, y, x]
        ns = ns[ns >= 0]
        if len(ns) < 3:
            continue
        vals, cnt = np.unique(ns, return_counts=True)
        best = vals[np.lexsort((((pal_lab[vals] - pal_lab[idx[y, x]]) ** 2).sum(1), -cnt))[0]]
        if np.sqrt(((pal_lab[best] - pal_lab[idx[y, x]]) ** 2).sum()) < tol:
            idx[y, x] = best


def _dark_map(pal: np.ndarray, synthesize: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """For each palette entry, a darker entry of similar hue; synthesizes extra colors (auto palettes only) when
    the palette has none, otherwise falls back to its darkest entry."""
    lab = to_oklab(pal)
    extra: list[np.ndarray] = []
    out = np.zeros(len(pal), np.int64)
    for i, (L, a, b) in enumerate(lab):
        target = np.array([L * 0.45, a * 0.7, b * 0.7])
        ok = np.nonzero(lab[:, 0] <= L - 0.12)[0]
        if len(ok):
            j = ok[np.argmin(((lab[ok] - target) ** 2).sum(1))]
            if not synthesize or np.sqrt(((lab[j] - target) ** 2).sum()) < 0.12:
                out[i] = j
                continue
        if L < 0.2 or not synthesize:
            out[i] = i if L < 0.2 else int(np.argmin(lab[:, 0]))
            continue
        out[i] = len(pal) + len(extra)
        extra.append(target)
    if not extra:
        return out, np.zeros((0, 3), np.uint8)
    ext = from_oklab(np.array(extra))
    # Different targets can round to one color; dedupe so the palette lists each outline color once.
    uniq, inv = np.unique(ext, axis=0, return_inverse=True)
    for i in range(len(out)):
        if out[i] >= len(pal):
            out[i] = len(pal) + int(inv.reshape(-1)[out[i] - len(pal)])
    return out, uniq


def _preset_palette(name: str, colors: int | None, samples: np.ndarray) -> np.ndarray:
    pal = np.array([parse_hex(c) for c in PALETTES[name]], np.uint8)
    if colors and colors < len(pal) and len(samples):
        used = np.bincount(_nearest(to_oklab(samples), to_oklab(pal)), minlength=len(pal))
        keep = np.sort(np.argsort(-used, kind="stable")[:colors])
        pal = pal[keep]
    return pal


def pixel_art(frames: Sequence[Image.Image], size: int | None = 64, factor: float | None = None,
              colors: int = 16, palette: str = "auto", palette_image: Image.Image | None = None,
              outline: str = "dark", alpha_threshold: int = 128, dither: str = "none",
              dither_strength: float = 0.6, cleanup: bool = True) -> PixelResult:
    if not frames:
        raise ValueError("no frames")
    if not 2 <= int(colors) <= 256:
        raise ValueError("colors must be within 2..256")
    if dither not in DITHERS:
        raise ValueError(f"dither must be one of {DITHERS}")
    if palette != "auto" and palette not in PALETTES:
        raise ValueError(f"unknown palette '{palette}'; use {PALETTE_CHOICES}")
    canvas = max(max(f.width, f.height) for f in frames)
    if factor:
        if factor < 1:
            raise ValueError("factor must be >= 1")
        scale = 1.0 / float(factor)
    elif size:
        if size < 4:
            raise ValueError("size must be >= 4")
        scale = min(1.0, float(size) / canvas)
    else:
        raise ValueError("give size or factor")

    smalls = [_downsample(f, scale) for f in frames]
    masks = []
    for arr in smalls:
        solid = arr[..., 3] >= alpha_threshold
        if cleanup:
            clean = _clean_mask(solid)
            _fill_holes_rgb(arr[..., :3], solid, clean)
            solid = clean
        masks.append(solid)
    samples = np.concatenate([a[..., :3][m] for a, m in zip(smalls, masks)]) if any(m.any() for m in masks) \
        else np.zeros((0, 3), np.uint8)

    if palette_image is not None:
        ref = np.array(palette_image.convert("RGBA"))
        pal, source = kmeans_palette(ref[..., :3][ref[..., 3] >= 128], int(colors)), "image"
    elif palette != "auto":
        pal, source = _preset_palette(palette, int(colors), samples), palette
    else:
        pal, source = kmeans_palette(samples, int(colors)), "auto"

    out_lines: np.ndarray = np.zeros((0, 3), np.uint8)
    dark = None
    line_rgb = None
    if outline == "dark":
        dark, out_lines = _dark_map(pal, synthesize=source == "auto")
    elif outline not in ("none", "", None):
        line_rgb = parse_hex(outline)
    full = np.concatenate([pal, out_lines]) if len(out_lines) else pal
    pal_lab = to_oklab(pal)
    spread = 0.0
    if dither == "bayer" and len(pal) > 1:
        d = np.sqrt(((pal_lab[:, None] - pal_lab[None]) ** 2).sum(-1))
        np.fill_diagonal(d, np.inf)
        spread = float(np.median(d.min(1))) * float(dither_strength)

    result: list[Image.Image] = []
    for arr, solid in zip(smalls, masks):
        h, w = solid.shape
        lab = to_oklab(arr[..., :3].reshape(-1, 3)).reshape(h, w, 3)
        if spread:
            # Threshold map is indexed by canvas position, so the pattern stays put across frames.
            yy, xx = np.mgrid[0:h, 0:w]
            lab[..., 0] += (_BAYER4[yy % 4, xx % 4] - 0.5) * spread
        idx = np.full((h, w), -1, np.int64)
        if solid.any():
            idx[solid] = _nearest(lab[solid], pal_lab)
        if cleanup and not spread:
            _despeckle(idx, pal_lab, 0.18)
        out = np.zeros((h, w, 4), np.uint8)
        out[solid, :3] = pal[idx[solid]]
        out[solid, 3] = 255
        if dark is not None or line_rgb is not None:
            ring = ~solid & np.any([_shift(solid, dy, dx, False) for dy, dx in _N4], axis=0)
            if dark is not None:
                # Lightest touching color picks the hue: the render already darkens silhouette edges (AO), which would
                # otherwise turn every line black.
                neigh = np.stack([_shift(idx, dy, dx, -1) for dy, dx in _N4])
                lum = np.where(neigh >= 0, pal_lab[np.maximum(neigh, 0), 0], np.inf)
                src = np.take_along_axis(neigh, np.where(np.isinf(lum), -np.inf, lum).argmax(0)[None], 0)[0]
                out[ring, :3] = full[dark[src[ring]]]
            else:
                out[ring, :3] = line_rgb
            out[ring, 3] = 255
        result.append(Image.fromarray(out, "RGBA"))

    used = sorted({tuple(int(v) for v in c) for f in result for c in np.array(f)[np.array(f)[..., 3] > 0][:, :3]},
                  key=lambda c: float(to_oklab(np.array([c]))[0, 0]))
    lines = [tuple(int(v) for v in c) for c in out_lines] + ([line_rgb] if line_rgb else [])
    pal_list = [tuple(int(v) for v in c) for c in pal] + [c for c in lines if c not in map(tuple, pal.tolist())]
    return PixelResult(frames=result, palette=[c for c in pal_list if c in set(used)] or pal_list,
                       outline_colors=[c for c in lines if c in set(used)], size=result[0].size, scale=scale,
                       source=source)


def _opt_int(v: Any) -> int | None:
    return int(v) if v not in (None, "", 0, "0") else None


class PixelArt(OpExecutor):
    kind = "op.pixel_art"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        imgs = []
        for i, ref in enumerate(refs):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            imgs.append(await _load(ctx, ref))
            ctx.progress(i + 1, len(refs) * 2, label="load")
        pal_refs = inputs.get("palette_image") or []
        pal_img = await _load(ctx, pal_refs[0]) if pal_refs else None
        factor = params.get("factor")
        try:
            res = await asyncio.to_thread(
                pixel_art, imgs, size=_opt_int(params.get("size", 64)), factor=float(factor) if factor else None,
                colors=int(params.get("colors") or 16), palette=str(params.get("palette") or "auto"),
                palette_image=pal_img, outline=str(params.get("outline") or "none"),
                alpha_threshold=int(params.get("alpha_threshold", 128)), dither=str(params.get("dither") or "none"),
                dither_strength=float(params.get("dither_strength", 0.6)), cleanup=bool(params.get("cleanup", True)))
        except ValueError as e:
            raise StepError("op_bad_params", str(e)) from e
        out: list[BlobRef] = []
        for i, (ref, img) in enumerate(zip(refs, res.frames)):
            out.append(await _put_png(ctx, img, ref.name or f"img{i:02d}.png"))
            ctx.progress(len(refs) + i + 1, len(refs) * 2, label="save")
        info = res.palette_json()
        pal_ref = await ctx.blobs.put_bytes(json.dumps(info, indent=1).encode(), name="palette.json",
                                            mime="application/json")
        swatch_ref = await _put_png(ctx, res.swatch(), "palette.png")
        return StepResult(outputs={"images": out, "palette": [pal_ref], "swatch": [swatch_ref]},
                          meta={"palette": info["colors"], "frame_w": res.size[0], "frame_h": res.size[1],
                                "pixel": True})


PIXEL_OPS: tuple[type[OpExecutor], ...] = (PixelArt,)
