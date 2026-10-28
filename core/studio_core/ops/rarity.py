"""Item rarity tiers: `op.recolor_tiers` (common = source, uncommon/rare = luminance-preserving recolor, plus a white
reference of rare for the Klein edits) and `op.rarity_set` (puts the Klein epic/legendary edits back on the source
canvas, silhouette IoU check, legendary glow + shimmer, palette ramp, rarity.json, labelled preview)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json
from .fx import dilate
from .pixel import parse_hex
from .tile import _enabled

RARITY_FILE = "rarity.json"
RARITY_TYPE = "asset-studio/item-rarity"
LAYOUT_FILE = "rarity_layout.json"
DEFAULT_TIERS = ("common", "uncommon", "rare", "epic", "legendary")
DEFAULT_COLORS = ("#b4b4b4", "#3fbf4f", "#3a7bd5", "#a335ee", "#ff9a1f")
CPU_TIERS = 3
REF_SIZE = 1024
LUM = np.array([0.299, 0.587, 0.114], np.float32)


def parse_tiers(raw: Any) -> list[str]:
    text = str(raw or "").replace("\n", ",")
    names = [t.strip() for t in text.split(",") if t.strip()] or list(DEFAULT_TIERS)
    if len(names) != len(DEFAULT_TIERS):
        raise StepError("op_bad_params", f"need exactly {len(DEFAULT_TIERS)} tier names, got {len(names)}")
    if len({n.lower() for n in names}) != len(names):
        raise StepError("op_bad_params", "tier names must be unique")
    return names


def parse_colors(raw: Any) -> list[tuple[int, int, int]]:
    text = str(raw or "auto").strip()
    if text.lower() in ("", "auto"):
        return [parse_hex(c) for c in DEFAULT_COLORS]
    parts = [c.strip() for c in text.replace("\n", ",").split(",") if c.strip()]
    try:
        cols = [parse_hex(c) for c in parts]
    except ValueError as e:
        raise StepError("op_bad_params", str(e)) from e
    # A short list keeps the defaults for the remaining tiers.
    return cols[:len(DEFAULT_COLORS)] + [parse_hex(c) for c in DEFAULT_COLORS[len(cols):]]


def _hex(c: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*c)


def ramp_lut(color: tuple[int, int, int], n: int = 256) -> np.ndarray:
    """(n, 3) float ramp black -> color -> white whose luma at index i is exactly i / (n - 1)."""
    c = np.asarray(color, np.float32) / 255.0
    lc = float(np.clip(c @ LUM, 0.02, 0.98))
    c = c * (lc / max(float(c @ LUM), 1e-6))
    t = np.linspace(0, 1, n, dtype=np.float32)[:, None]
    dark = c[None] * (t / lc)
    light = c[None] + (1 - c[None]) * ((t - lc) / (1 - lc))
    return np.clip(np.where(t <= lc, dark, light), 0, 1)


def recolor(img: Image.Image, color: tuple[int, int, int], strength: float = 0.85) -> Image.Image:
    """Hue swap through a luma ramp: every pixel keeps its luma; near-grey pixels (steel, outlines) move less."""
    rgba = np.asarray(img.convert("RGBA")).astype(np.float32) / 255.0
    rgb = rgba[..., :3]
    lum = np.clip(rgb @ LUM, 0, 1)
    target = ramp_lut(color)[np.round(lum * 255).astype(np.int32)]
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0)
    t = np.clip((sat - 0.08) / 0.27, 0, 1)
    w = (strength * (0.3 + 0.7 * t * t * (3 - 2 * t)))[..., None]
    out = rgb * (1 - w) + target * w
    rgba = np.concatenate([out, rgba[..., 3:]], -1)
    return Image.fromarray(np.clip(np.round(rgba * 255), 0, 255).astype(np.uint8), "RGBA")


def reference(img: Image.Image, size: int = REF_SIZE, margin: float = 0.08) -> tuple[Image.Image, list[int]]:
    """Source canvas letterboxed on white for Klein; the box [x, y, w, h] maps an edit back onto the canvas."""
    w, h = img.size
    s = size * (1 - 2 * margin) / max(w, h)
    bw, bh = max(1, round(w * s)), max(1, round(h * s))
    x, y = (size - bw) // 2, (size - bh) // 2
    out = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    out.alpha_composite(img.convert("RGBA").resize((bw, bh), Image.Resampling.LANCZOS), (x, y))
    return out.convert("RGB"), [x, y, bw, bh]


def unref(edit: Image.Image, layout: dict[str, Any]) -> Image.Image:
    size = int(layout["ref_size"])
    x, y, bw, bh = (int(v) for v in layout["box"])
    img = edit.convert("RGBA")
    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    w, h = (int(v) for v in layout["canvas"])
    return img.crop((x, y, x + bw, y + bh)).resize((w, h), Image.Resampling.LANCZOS)


def alpha_iou(a: Image.Image, b: Image.Image) -> float:
    ma = np.asarray(a.convert("RGBA"))[..., 3] > 127
    mb = np.asarray(b.convert("RGBA").resize(a.size))[..., 3] > 127
    return round(float((ma & mb).sum() / max((ma | mb).sum(), 1)), 4)


def glow(img: Image.Image, color: tuple[int, int, int], width: int = 3, strength: float = 0.9) -> Image.Image:
    rgba = img.convert("RGBA")
    alpha = np.asarray(rgba)[..., 3]
    grown = Image.fromarray(dilate(alpha, max(1, width), plus_first=True), "L")
    halo = np.asarray(grown.filter(ImageFilter.GaussianBlur(max(1.0, width * 0.8))), np.float32) * strength
    layer = np.zeros((*alpha.shape, 4), np.uint8)
    layer[..., :3] = color
    layer[..., 3] = np.clip(halo, 0, 255).astype(np.uint8)
    out = Image.fromarray(layer, "RGBA")
    out.alpha_composite(rgba)
    return out


def shimmer(img: Image.Image, frames: int = 4, band: float = 0.18, power: float = 0.55) -> list[Image.Image]:
    """A diagonal highlight sweeping across the opaque pixels; frame 0 starts off the item so the loop restarts clean."""
    rgba = np.asarray(img.convert("RGBA")).astype(np.float32) / 255.0
    h, w = rgba.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    diag = (xx / max(w, 1) + yy / max(h, 1)) / 2
    out = []
    for i in range(frames):
        pos = -band + (1 + 2 * band) * (i + 0.5) / frames
        k = np.clip(1 - np.abs(diag - pos) / band, 0, 1) ** 2 * power
        rgb = rgba[..., :3] + (1 - rgba[..., :3]) * k[..., None]
        frame = np.concatenate([rgb, rgba[..., 3:]], -1)
        out.append(Image.fromarray(np.clip(np.round(frame * 255), 0, 255).astype(np.uint8), "RGBA"))
    return out


def dominant(img: Image.Image, n: int = 6) -> list[tuple[int, int, int]]:
    rgba = np.asarray(img.convert("RGBA"))
    px = rgba[rgba[..., 3] > 127][:, :3]
    if not len(px):
        return []
    strip = Image.fromarray(px.reshape(1, -1, 3).astype(np.uint8), "RGB")
    q = strip.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    cols = [tuple(pal[i * 3:i * 3 + 3]) for _, i in counts]
    return sorted(cols, key=lambda c: float(np.dot(c, LUM)))  # type: ignore[arg-type]


def palette_ramp(tiers: list[str], colors: list[tuple[int, int, int]], images: list[Image.Image]) -> Image.Image:
    """One row per tier: its colour, an 8-step luma ramp, then the item's dominant colours dark to light."""
    sw, label, gap = 28, 96, 4
    width = label + sw * (1 + 8 + 6) + gap * 3
    out = Image.new("RGB", (width, len(tiers) * (sw + gap) + gap), (28, 28, 32))
    d = ImageDraw.Draw(out)
    for r, (name, col, img) in enumerate(zip(tiers, colors, images)):
        y = gap + r * (sw + gap)
        d.text((6, y + 8), name, fill=col)
        x = label
        d.rectangle((x, y, x + sw - 1, y + sw - 1), fill=col)
        x += sw + gap
        lut = ramp_lut(col)
        for k in range(8):
            c = tuple(int(v * 255) for v in lut[int((k + 0.5) / 8 * 255)])
            d.rectangle((x, y, x + sw - 1, y + sw - 1), fill=c)
            x += sw
        x += gap
        for c in dominant(img):
            d.rectangle((x, y, x + sw - 1, y + sw - 1), fill=c)
            x += sw
    return out


def preview_row(tiers: list[str], colors: list[tuple[int, int, int]], images: list[Image.Image],
                ious: list[float | None], flags: list[bool], cell: int = 192) -> Image.Image:
    gap, label = 8, 34
    out = Image.new("RGB", (gap + len(images) * (cell + gap), cell + label + gap * 2), (24, 24, 28))
    d = ImageDraw.Draw(out)
    for i, (name, col, img, iou, bad) in enumerate(zip(tiers, colors, images, ious, flags)):
        x, y = gap + i * (cell + gap), gap
        d.rectangle((x - 2, y - 2, x + cell + 1, y + cell + 1), outline=col, width=2)
        d.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(46, 46, 52))
        im = img.convert("RGBA")
        s = min(cell / im.width, cell / im.height)
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.LANCZOS)
        tile = Image.new("RGBA", (cell, cell), (46, 46, 52, 255))
        tile.alpha_composite(im, ((cell - im.width) // 2, (cell - im.height) // 2))
        out.paste(tile.convert("RGB"), (x, y))
        d.text((x + 2, y + cell + 4), name, fill=col)
        if iou is not None:
            d.text((x + 2, y + cell + 18), f"IoU {iou:.2f}" + ("  ! shape" if bad else ""),
                   fill=(255, 110, 90) if bad else (170, 170, 170))
    return out


class RecolorTiers(OpExecutor):
    kind = "op.recolor_tiers"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        tiers = parse_tiers(params.get("tiers"))
        colors = parse_colors(params.get("colors"))
        try:
            strength = float(params.get("strength", 0.85))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", f"bad strength: {e}") from e
        ref = _images_in(inputs)[0]
        src = (await _load(ctx, ref)).convert("RGBA")
        stem = (ref.name or "item").rsplit(".", 1)[0]
        outs: list[BlobRef] = [await _put_png(ctx, src, f"{stem}_{tiers[0]}.png")]
        rare = src
        for i in range(1, CPU_TIERS):
            ctx.progress(i, CPU_TIERS, label=tiers[i])
            rare = await asyncio.to_thread(recolor, src, colors[i], strength)
            outs.append(await _put_png(ctx, rare, f"{stem}_{tiers[i]}.png"))
        white, box = await asyncio.to_thread(reference, rare)
        ref_out = await _put_png(ctx, white, f"{stem}_{tiers[CPU_TIERS - 1]}_ref.png")
        doc = {"stem": stem, "canvas": list(src.size), "ref_size": REF_SIZE, "box": box, "tiers": tiers,
               "colors": [_hex(c) for c in colors], "strength": strength}
        lay = await ctx.blobs.put_bytes(json.dumps(doc).encode(), name=LAYOUT_FILE, mime="application/json")
        ctx.progress(CPU_TIERS, CPU_TIERS)
        return StepResult(outputs={"images": outs, "ref": [ref_out], "layout": [lay]},
                          meta={"tiers": tiers[:CPU_TIERS], "canvas": list(src.size)})


class RaritySet(OpExecutor):
    kind = "op.rarity_set"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        base = _images_in(inputs)
        edits = _images_in(inputs, "edits")
        lays = inputs.get("layout") or []
        if not lays:
            raise StepError("op_no_input", "input 'layout' is empty")
        lay = await _read_json(ctx, lays[0])
        tiers, colors = list(lay["tiers"]), [parse_hex(c) for c in lay["colors"]]
        if len(base) < CPU_TIERS or len(edits) < len(tiers) - CPU_TIERS:
            raise StepError("op_no_input", f"need {CPU_TIERS} recolored tiers and {len(tiers) - CPU_TIERS} edits")
        try:
            min_iou = float(params.get("min_iou", 0.8))
            glow_width = int(params.get("glow_width", 3))
            frames = int(params.get("shimmer_frames", 4))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        raw_glow = str(params.get("glow") or "auto").strip().lower()
        glow_col = None if raw_glow == "none" else colors[-1] if raw_glow == "auto" else parse_hex(raw_glow)
        alpha_mode = str(params.get("alpha") or "edit")
        imgs = [(await _load(ctx, r)).convert("RGBA") for r in base[:CPU_TIERS]]
        orig = imgs[0]
        for r in edits[:len(tiers) - CPU_TIERS]:
            img = unref(await _load(ctx, r), lay)
            if alpha_mode == "original":
                img.putalpha(orig.getchannel("A"))
            imgs.append(img)
        ious: list[float | None] = [None] + [await asyncio.to_thread(alpha_iou, orig, im) for im in imgs[1:]]
        flags = [iou is not None and iou < min_iou for iou in ious]
        final = list(imgs)
        if glow_col is not None:
            final[-1] = await asyncio.to_thread(glow, imgs[-1], glow_col, glow_width)
        stem = str(lay.get("stem") or "item")
        out: list[BlobRef] = []
        entries = []
        for i, (name, img) in enumerate(zip(tiers, final)):
            ctx.progress(i, len(tiers), label=name)
            r = await _put_png(ctx, img, f"{stem}_{name}.png")
            out.append(r)
            entries.append({"tier": name, "rank": i, "color": _hex(colors[i]), "file": r.name, "hash": r.hash,
                            "source": "original" if i == 0 else "recolor" if i < CPU_TIERS else "klein",
                            "iou": ious[i], "flagged": flags[i]})
        shim: list[BlobRef] = []
        if _enabled(params.get("shimmer", True)) and frames > 0:
            for k, fr in enumerate(await asyncio.to_thread(shimmer, imgs[-1], frames)):
                fr = await asyncio.to_thread(glow, fr, glow_col, glow_width) if glow_col is not None else fr
                shim.append(await _put_png(ctx, fr, f"{stem}_{tiers[-1]}_shimmer_{k}.png"))
            entries[-1]["shimmer"] = [{"file": r.name, "hash": r.hash} for r in shim]
        ramp = await asyncio.to_thread(palette_ramp, tiers, colors, final)
        ramp_ref = await _put_png(ctx, ramp, f"{stem}_palette.png")
        row = await asyncio.to_thread(preview_row, tiers, colors, final, ious, flags)
        prev = await _put_png(ctx, row, f"{stem}_rarity_preview.png")
        doc = {"type": RARITY_TYPE, "item": stem, "canvas": lay["canvas"], "min_iou": min_iou,
               "glow": None if glow_col is None else _hex(glow_col), "tiers": entries,
               "flagged": [e["tier"] for e in entries if e["flagged"]]}
        rj = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=RARITY_FILE, mime="application/json")
        if doc["flagged"]:
            ctx.log(f"silhouette changed (IoU < {min_iou}): {', '.join(doc['flagged'])}", "warning")
        ctx.progress(len(tiers), len(tiers))
        return StepResult(outputs={"images": out, "palette": [ramp_ref], "rarity": [rj], "preview": [prev],
                                   "shimmer": shim},
                          meta={"tiers": tiers, "iou": {t: v for t, v in zip(tiers, ious) if v is not None},
                                "flagged": doc["flagged"]})


RARITY_OPS: tuple[type[OpExecutor], ...] = (RecolorTiers, RaritySet)
