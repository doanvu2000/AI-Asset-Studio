"""`op.icon_compose` (recipe icon-3d): Blender icon renders -> same-size icons with the object's box centred and
filling the same share of every cell, optional outline, background (transparent, rarity tier tile, icon frame),
an atlas (export/atlas MaxRects), a contact sheet and icon3d.json with the QA numbers (centre offset, fill spread,
edge fringe)."""
from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .anim_video import checker
from .executors import OpExecutor, _images_in, _load, _put_png
from .item_fx import ALPHA_ON, fringe_ratio, resize_pm
from .rarity import DEFAULT_COLORS, parse_hex

ICON3D_FILE = "icon3d.json"
ICON3D_TYPE = "asset_studio.icon3d"
BACKGROUNDS = ("transparent", "rarity", "frame")
FRAME_COLOR = "#b98a4b"


def icon_slug(name: str, i: int) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return s[:48] or f"icon_{i + 1:02d}"


def _bbox(img: Image.Image) -> tuple[int, int, int, int] | None:
    a = np.asarray(img.convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(a > ALPHA_ON)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def fit_icon(render: Image.Image, size: int, padding: float, outline: int) -> Image.Image:
    """The object's alpha box, scaled premultiplied so its long side fills size * (1 - 2 * padding) minus the
    outline, centred on the cell (subpixel placement through the resample box)."""
    box = _bbox(render)
    if box is None:
        raise ValueError("render is empty (mesh outside the camera?)")
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    inner = size * (1 - 2 * padding) - 2 * outline
    if inner < 4:
        raise ValueError("padding / outline leave no room for the icon")
    s = inner / max(w, h)
    nw, nh = w * s, h * s
    ox, oy = (size - nw) / 2, (size - nh) / 2
    # Resample the whole cell from source coordinates so the fractional offset is kept, not rounded away.
    src_box = (x0 - ox / s, y0 - oy / s, x0 + (size - ox) / s, y0 + (size - oy) / s)
    pad = math.ceil(max(size / s, 1)) + 2
    canvas = Image.new("RGBA", (render.width + 2 * pad, render.height + 2 * pad), (0, 0, 0, 0))
    canvas.paste(render.convert("RGBA"), (pad, pad))
    shifted = tuple(v + pad for v in src_box)
    return resize_pm(canvas, (size, size), box=shifted)  # type: ignore[arg-type]


def disk_dilate(alpha: np.ndarray, r: int) -> np.ndarray:
    out = alpha.copy()
    h, w = alpha.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy > r * r + r or (dx == 0 and dy == 0):
                continue
            sy, sx = slice(max(0, dy), h + min(0, dy)), slice(max(0, dx), w + min(0, dx))
            ty, tx = slice(max(0, -dy), h + min(0, -dy)), slice(max(0, -dx), w + min(0, -dx))
            np.maximum(out[sy, sx], alpha[ty, tx], out=out[sy, sx])
    return out


def add_outline(icon: Image.Image, px: int, color: tuple[int, int, int]) -> Image.Image:
    if px <= 0:
        return icon
    a = np.asarray(icon, np.float32)[..., 3]
    grown = disk_dilate(a, px)
    layer = np.zeros((*a.shape, 4), np.uint8)
    layer[..., :3] = color
    layer[..., 3] = np.clip(grown, 0, 255).astype(np.uint8)
    out = Image.fromarray(layer, "RGBA").filter(ImageFilter.GaussianBlur(0.5))
    out.alpha_composite(icon)
    return out


def _round_mask(size: int, inset: float, radius: float) -> Image.Image:
    ss = 4
    m = Image.new("L", (size * ss, size * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle((inset * ss, inset * ss, (size - inset) * ss - 1, (size - inset) * ss - 1),
                                        radius * ss, fill=255)
    return m.resize((size, size), Image.Resampling.LANCZOS)


def _radial(size: int, inner: tuple[int, int, int], outer: tuple[int, int, int]) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    d = np.clip(np.hypot(xx - size / 2, yy - size * 0.42) / (size * 0.62), 0, 1)[..., None]
    return np.asarray(inner, np.float32) * (1 - d) + np.asarray(outer, np.float32) * d


def background(size: int, mode: str, tier: int) -> Image.Image | None:
    if mode == "transparent":
        return None
    if mode == "rarity":
        base = np.asarray(parse_hex(DEFAULT_COLORS[max(1, min(5, tier)) - 1]), np.float32)
        inner = tuple(int(v) for v in np.clip(base * 0.55 + 255 * 0.25, 0, 255))
        outer = tuple(int(v) for v in base * 0.28)
        rim = tuple(int(v) for v in np.clip(base * 1.1 + 20, 0, 255))
    else:
        inner, outer = (64, 58, 66), (30, 26, 32)
        rim = parse_hex(FRAME_COLOR)
    fill = _radial(size, inner, outer)  # type: ignore[arg-type]
    rgba = np.concatenate([fill, np.full((size, size, 1), 255, np.float32)], -1).astype(np.uint8)
    body = Image.fromarray(rgba, "RGBA")
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    border = max(2.0, size / 32)
    radius = size * 0.16
    rim_img = Image.new("RGBA", (size, size), (*rim, 255))
    out.paste(rim_img, (0, 0), _round_mask(size, 0.5, radius))
    out.paste(body, (0, 0), _round_mask(size, 0.5 + border, radius - border))
    if mode == "frame":
        hi = Image.new("RGBA", (size, size), (255, 236, 200, 70))
        top = _round_mask(size, 0.5, radius)
        cut = Image.new("L", (size, size), 0)
        ImageDraw.Draw(cut).rectangle((0, 0, size, size * 0.18), fill=255)
        out.alpha_composite(Image.composite(hi, Image.new("RGBA", (size, size), (0, 0, 0, 0)),
                                            Image.fromarray(np.minimum(np.asarray(top), np.asarray(cut)))))
    return out


def icon_stats(icon: Image.Image) -> dict[str, float]:
    size = icon.width
    box = _bbox(icon)
    if box is None:
        return {"fill": 0.0, "dx": 0.0, "dy": 0.0}
    x0, y0, x1, y1 = box
    return {"fill": round(max(x1 - x0, y1 - y0) / size, 4), "dx": round((x0 + x1) / 2 - size / 2, 2),
            "dy": round((y0 + y1) / 2 - size / 2, 2)}


def set_qa(stats: list[dict[str, float]], fringe: float | None) -> dict[str, Any]:
    fills = [s["fill"] for s in stats]
    mean = float(np.mean(fills)) if fills else 0.0
    spread = (max(fills) - min(fills)) / mean if mean else 0.0
    off = max((max(abs(s["dx"]), abs(s["dy"])) for s in stats), default=0.0)
    return {"fill_mean": round(mean, 4), "fill_spread": round(spread, 4), "fill_ok": spread <= 0.05,
            "center_max_px": round(off, 2), "center_ok": off <= 2.0, "fringe_ratio": fringe,
            "alpha_ok": fringe is None or fringe >= 0.8}


def contact_sheet(icons: list[Image.Image], names: list[str], cell: int = 160) -> Image.Image:
    cols = min(6, len(icons))
    rows = math.ceil(len(icons) / cols)
    gap, label = 8, 18
    out = Image.new("RGB", (cols * (cell + gap) + gap, rows * (cell + gap + label) + gap), (30, 30, 33))
    d = ImageDraw.Draw(out)
    bg = checker((cell, cell)).convert("RGB")
    for i, (ic, name) in enumerate(zip(icons, names)):
        x, y = gap + (i % cols) * (cell + gap), gap + (i // cols) * (cell + gap + label)
        tile = bg.copy().convert("RGBA")
        s = cell / ic.width
        tile.alpha_composite(resize_pm(ic, (cell, cell)) if s != 1 else ic)
        out.paste(tile.convert("RGB"), (x, y))
        d.text((x + 2, y + cell + 3), name[:22], fill=(220, 220, 225))
    return out


def compose_all(renders: list[Image.Image], cfg: dict[str, Any]) -> tuple[list[Image.Image], list[Image.Image]]:
    """Returns (final icons, the fitted objects alone) — the second feeds the fill / centre / fringe QA, since a dark
    outline would read as a black fringe."""
    bg = background(cfg["size"], cfg["background"], cfg["tier"])
    finals, bare = [], []
    for r in renders:
        fitted = fit_icon(r, cfg["size"], cfg["padding"], cfg["outline"])
        bare.append(fitted)
        icon = add_outline(fitted, cfg["outline"], cfg["outline_color"])
        if bg is not None:
            full = bg.copy()
            full.alpha_composite(icon)
            icon = full
        finals.append(icon)
    return finals, bare


def compose_params(params: dict[str, Any]) -> dict[str, Any]:
    size = int(params.get("size", 256))
    mode = str(params.get("background") or "transparent")
    if mode not in BACKGROUNDS:
        raise ValueError(f"background must be one of {', '.join(BACKGROUNDS)}")
    if not 16 <= size <= 1024:
        raise ValueError("size must be 16..1024")
    color = str(params.get("outline_color") or "#1b1420")
    return {"size": size, "padding": float(params.get("padding", 0.08)),
            "outline": max(0, min(16, int(params.get("outline", 0)))), "outline_color": parse_hex(color),
            "background": mode, "tier": int(params.get("tier", 3)), "asset": str(params.get("asset") or "icons")}


class IconCompose(OpExecutor):
    kind = "op.icon_compose"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        try:
            cfg = compose_params(params)
        except ValueError as e:
            raise StepError("op_bad_params", str(e)) from e
        renders = [await _load(ctx, r) for r in refs]
        names, seen = [], set()
        for i, r in enumerate(refs):
            n = icon_slug((r.name or "").rsplit(".", 1)[0], i)
            while n.lower() in seen:
                n += "_b"
            seen.add(n.lower())
            names.append(n)
        try:
            finals, bare = await asyncio.to_thread(compose_all, renders, cfg)
        except ValueError as e:
            raise StepError("op_bad_input", str(e)) from e
        stats = [icon_stats(b) for b in bare]
        fringe = await asyncio.to_thread(fringe_ratio, bare, renders)
        qa = set_qa(stats, fringe)
        out: list[BlobRef] = []
        for img, name in zip(finals, names):
            out.append(await _put_png(ctx, img, f"{name}.png"))
        atlas_png, atlas_json = await asyncio.to_thread(_atlas, finals, names, cfg["asset"])
        atlas_ref = await ctx.blobs.put_bytes(atlas_png, name=f"{cfg['asset']}_atlas.png", mime="image/png")
        atlas_json_ref = await ctx.blobs.put_bytes(atlas_json, name=f"{cfg['asset']}_atlas.json",
                                                   mime="application/json")
        sheet = await asyncio.to_thread(contact_sheet, finals, names)
        prev_ref = await _put_png(ctx, sheet, "icons_preview.png")
        doc = {"type": ICON3D_TYPE, "asset": cfg["asset"], "size": cfg["size"], "background": cfg["background"],
               "tier": cfg["tier"] if cfg["background"] == "rarity" else None, "outline": cfg["outline"],
               "padding": cfg["padding"], "qa": qa,
               "icons": [{"name": n, "file": f"{n}.png", "hash": r.hash, **s} for n, r, s in zip(names, out, stats)],
               "atlas": {"file": atlas_ref.name, "hash": atlas_ref.hash, "json": atlas_json_ref.name}}
        doc_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=ICON3D_FILE,
                                            mime="application/json")
        warnings = []
        if not qa["fill_ok"]:
            warnings.append(f"độ lấp đầy lệch {qa['fill_spread'] * 100:.1f}% (> 5%)")
        if not qa["center_ok"]:
            warnings.append(f"tâm lệch {qa['center_max_px']}px (> 2px)")
        if not qa["alpha_ok"]:
            warnings.append("viền alpha tối (fringe)")
        return StepResult(outputs={"images": out, "atlas": [atlas_ref], "atlas_json": [atlas_json_ref],
                                   "preview": [prev_ref], "report": [doc_ref]},
                          meta={"icons": len(out), **qa, "warnings": warnings})


def _atlas(icons: list[Image.Image], names: list[str], asset: str) -> tuple[bytes, bytes]:
    from studio_core.export.atlas import AtlasOptions, Sprite, atlas_json, pack

    sprites = [Sprite(n, ic, (ic.width / 2, ic.height / 2)) for ic, n in zip(icons, names)]
    at = pack(sprites, f"{asset}_atlas", AtlasOptions(max_size=4096, trim=False))
    if len(at.png) != 1:
        raise StepError("op_bad_params", "icons do not fit one 4096 atlas page")
    return at.png[0], atlas_json(at)


ICON3D_OPS = (IconCompose,)
