"""Trim sheets: horizontal strips stacked into one square atlas, each strip seamless along U only (op.seamless's
min-error quilt on one axis, like op.pano_fix). Strip heights are expressed on a 2048 px sheet and scaled to the
chosen size; trim.json lists every strip as {name, px, v0, v1} so meshes can map faces into a strip by name."""
from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json
from .pano import seam_report
from .pbr import MATERIAL_NAME, MATERIAL_TYPE, ROLES, PbrParams, derive, preview_grid
from .tile import _quilt_x, _rgb, detrend, flatten_light, wrap_resize

TRIM_FILE = "trim.json"
TRIM_TYPE = "asset-studio/trim-sheet"
LAYOUT_FILE = "trim_layout.json"
LAYOUT_TYPE = "asset-studio/trim-layout"
BASE = 2048
HEIGHTS = (32, 64, 128, 256, 512)
SIZES = (1024, 2048)
MAX_STRIPS = 16
# UV inset per strip edge (2048-px units): mip levels of a 2048 sheet blend neighbouring strips otherwise.
PAD_PX = 2
STRIP_SUFFIX = ("seamless horizontally tileable trim strip texture, flat front orthographic view, one continuous "
                "horizontal band filling the whole frame edge to edge, the pattern runs left to right, flat even "
                "diffuse lighting, no shadows, no perspective, no vanishing point, no border, no frame, game texture")

PRESETS: dict[str, list[tuple[str, int, str]]] = {
    "general": [
        ("panel", 256, "large flat wall panel surface"),
        ("brick", 256, "row of bricks with mortar lines"),
        ("planks", 256, "horizontal wooden planks"),
        ("stone", 256, "cut stone blocks"),
        ("molding", 128, "ornate cornice molding profile"),
        ("frame", 128, "carved wooden frame trim"),
        ("baseboard", 128, "baseboard trim with a bevelled top edge"),
        ("metal", 128, "riveted metal band"),
        ("rope", 128, "braided rope trim"),
        ("tiles", 128, "small square tiles in a row"),
        ("bolts", 64, "metal strip with bolts"),
        ("edge", 64, "worn chamfered edge"),
        ("groove", 64, "recessed groove line"),
        ("pipe", 64, "thin pipe"),
        ("seam", 32, "thin panel seam"),
        ("stitch", 32, "thin decorative bead line"),
    ],
    "modular": [
        ("wall", 512, "wall surface"),
        ("floor", 512, "floor surface seen from above"),
        ("pillar", 256, "column shaft surface"),
        ("cap", 128, "wall top cap cornice molding"),
        ("base", 128, "baseboard trim"),
        ("frame", 128, "door and window frame trim"),
        ("step", 128, "stair tread edge"),
        ("rail", 128, "railing handrail and baluster"),
        ("metal", 64, "metal fitting strip"),
        ("dark", 64, "dark grout and shadow gap"),
    ],
}


def slug(text: Any, fallback: str = "strip") -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:24] or fallback


def snap_height(px: Any) -> int:
    try:
        v = float(px)
    except (TypeError, ValueError):
        return 128
    return min(HEIGHTS, key=lambda h: abs(math.log2(max(v, 1.0)) - math.log2(h)))


def fit_total(heights: list[int], total: int = BASE) -> list[int]:
    """Halve the tallest / double the shortest strip until the stack is exactly `total`; every step keeps each
    height in HEIGHTS, and all of them are powers of two, so the loop always lands on the total."""
    hs = list(heights)
    while sum(hs) > total:
        i = max(range(len(hs)), key=lambda k: (hs[k], -k))
        if hs[i] == HEIGHTS[0]:
            hs.pop(i)
        else:
            hs[i] //= 2
    while sum(hs) < total:
        room = total - sum(hs)
        grow = [k for k in range(len(hs)) if hs[k] * 2 <= HEIGHTS[-1] and hs[k] <= room]
        if grow:
            i = min(grow, key=lambda k: (hs[k], k))
            hs[i] *= 2
        else:
            hs.append(max(h for h in HEIGHTS if h <= room))
    return hs


def normalize_layout(raw: Any, style: str = "") -> dict[str, Any]:
    """Model reply or preset -> strips whose heights are in HEIGHTS and add up to exactly 2048, unique names."""
    items = raw.get("strips") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("layout has no strips")
    strips: list[dict[str, Any]] = []
    taken: set[str] = set()
    for i, s in enumerate(items[:MAX_STRIPS]):
        if not isinstance(s, dict):
            continue
        name = slug(s.get("name"), f"strip_{i + 1}")
        base, k = name, 2
        while name in taken:
            name, k = f"{base}_{k}", k + 1
        taken.add(name)
        prompt = " ".join(str(s.get("prompt") or s.get("name") or name).split()).rstrip(" .,")
        strip: dict[str, Any] = {"name": name, "px": snap_height(s.get("px")), "prompt": prompt}
        try:
            if s.get("aspect"):
                strip["aspect"] = round(min(8.0, max(0.25, float(s["aspect"]))), 4)
        except (TypeError, ValueError):
            pass
        strips.append(strip)
    if len(strips) < 2:
        raise ValueError("a trim sheet needs at least 2 strips")
    heights = fit_total([s["px"] for s in strips])
    while len(heights) > len(strips):
        strips.append({"name": f"filler_{len(strips) + 1}", "px": 0, "prompt": "plain trim band"})
    strips = strips[:len(heights)]
    for s, h in zip(strips, heights):
        s["px"] = h
        s["gen_prompt"] = ", ".join(p for p in (s["prompt"], style.strip().rstrip(" .,"), STRIP_SUFFIX) if p)
    return {"type": LAYOUT_TYPE, "style": style.strip(), "base": BASE, "strips": strips}


def preset_layout(name: str, style: str, grid: float = 2, storey: int = 0) -> dict[str, Any]:
    """The modular preset fixes the tile aspect of its surface strips so one grid cell holds whole repeats: a wall
    strip spans the storey height, so its tile is grid wide per storey high; a floor strip spans one cell both ways."""
    if name not in PRESETS:
        raise ValueError(f"preset must be one of {', '.join(PRESETS)}")
    aspects: dict[str, float] = {}
    if name == "modular":
        from ..adapters.blender.modular_math import kit_dims

        d = kit_dims(grid, storey)
        aspects = {"wall": d["grid"] / d["storey"], "floor": 1.0}
    return normalize_layout([{"name": n, "px": px, "prompt": p, "aspect": aspects.get(n)}
                             for n, px, p in PRESETS[name]], style)


def gen_size(aspect: float, overlap: float = 0.125, area: int = 786432) -> tuple[int, int]:
    """Generation size whose quilted result (narrower by `overlap`) has the wanted tile aspect."""
    a = min(4.0, max(0.33, aspect / (1 - overlap)))
    h = math.sqrt(area / a)
    return max(256, round(a * h / 16) * 16), max(256, round(h / 16) * 16)


def strip_ranges(strips: list[dict[str, Any]], base: int = BASE) -> list[dict[str, Any]]:
    """Top-down pixel rows y0..y1 on the 2048 sheet and the matching UV rows (v grows upwards, as in Blender,
    Unity and glTF after their flips) with the mip-safe inset."""
    out, y = [], 0
    for s in strips:
        y0, y1 = y, y + int(s["px"])
        out.append({"name": s["name"], "px": int(s["px"]), "y0": y0, "y1": y1,
                    "v0": round(1 - y1 / base, 6), "v1": round(1 - y0 / base, 6),
                    "v0_safe": round(1 - (y1 - PAD_PX) / base, 6), "v1_safe": round(1 - (y0 + PAD_PX) / base, 6)})
        y = y1
    return out


def make_strip_repeats(height: float, aspect: float | None, width: int = BASE) -> int:
    """How many copies of the quilted tile fill the strip width: a power of two, so the band wraps at the sheet
    edge. Without a fixed aspect the default 3:1 generation quilts to about 2.6:1."""
    a = aspect or 3 * (1 - 0.125)
    return min(2 ** max(0, round(math.log2(max(1.0, width / (height * a))))), max(1, width // 16))


def make_strip(img: Image.Image, width: int, height: int, overlap: float = 0.125, feather: int = 3,
               flatten: float = 0.5, aspect: float | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """One generated image -> a `width`×`height` band that wraps along U. The quilted tile keeps its aspect and is
    repeated a power-of-two number of times, so the band still wraps at the sheet edge."""
    a = _rgb(img)
    if flatten > 0:
        a = detrend(a)
    try:
        q = _quilt_x(a, overlap, feather)
    except ValueError as e:
        raise StepError("op_bad_params", str(e)) from e
    q = flatten_light(q, flatten, wrap=(False, True))
    repeats = make_strip_repeats(height, aspect or q.shape[1] / q.shape[0], width)
    tile_w = width // repeats
    t8 = Image.fromarray(np.clip(np.round(q * 255), 0, 255).astype(np.uint8), "RGB")
    tile = _rgb(wrap_resize(t8, tile_w, height))
    band = np.tile(tile, (1, repeats, 1))
    if band.shape[1] != width:
        band = _rgb(wrap_resize(Image.fromarray(np.round(band * 255).astype(np.uint8), "RGB"), width, height))
    return band, {"repeats": repeats, "tile_w": tile_w, "seam": seam_report(band)}


def strip_maps(band: np.ndarray, p: PbrParams) -> dict[str, Image.Image]:
    """PBR maps of one strip. Reflect-padding rows first keeps the vertical wrap of derive() from mixing the
    strip's top edge with its bottom edge; U still wraps."""
    h = band.shape[0]
    pad = min(h, 16)
    padded = np.pad(band, ((pad, pad), (0, 0), (0, 0)), mode="reflect")
    img = Image.fromarray(np.clip(np.round(padded * 255), 0, 255).astype(np.uint8), "RGB")
    maps = derive(img, p)
    return {r: m.crop((0, pad, m.width, pad + h)) for r, m in maps.items()}


def assemble(maps: list[dict[str, Image.Image]], size: int) -> dict[str, Image.Image]:
    out: dict[str, Image.Image] = {}
    for role in ROLES:
        mode = "RGB" if role in ("albedo", "normal") else "L"
        sheet = Image.new(mode, (size, size))
        y = 0
        for m in maps:
            part = m[role].convert(mode)
            sheet.paste(part, (0, y))
            y += part.height
        out[role] = sheet
    return out


def preview(atlas: Image.Image, ranges: list[dict[str, Any]], scores: list[float], width: int = 1024) -> Image.Image:
    """Atlas with strip names and seam scores on the left; on the right every strip rolled half a turn so its wrap
    seam sits in the middle of the cell."""
    half = width // 2
    out = Image.new("RGB", (width, half), (28, 28, 32))
    out.paste(atlas.convert("RGB").resize((half, half), Image.Resampling.LANCZOS), (0, 0))
    a = np.asarray(atlas.convert("RGB"))
    rolled = Image.fromarray(np.roll(a, a.shape[1] // 2, axis=1), "RGB").resize((half, half), Image.Resampling.LANCZOS)
    out.paste(rolled, (half, 0))
    d = ImageDraw.Draw(out)
    s = half / BASE
    for r, sc in zip(ranges, scores):
        y0, y1 = r["y0"] * s, r["y1"] * s
        d.line([(0, y0), (width, y0)], fill=(255, 255, 255), width=1)
        if y1 - y0 >= 10:
            d.text((4, y0 + 1), f"{r['name']} {r['px']}", fill=(255, 255, 0))
            d.text((half + 4, y0 + 1), f"seam {sc:.0f}", fill=(0, 255, 255))
    d.line([(half + half // 2, 0), (half + half // 2, half)], fill=(255, 0, 255), width=1)
    return out


class TrimSheet(OpExecutor):
    """Strip images (in layout order) + trim_layout.json -> albedo atlas, PBR maps, pbr_material.json and trim.json."""

    kind = "op.trim_sheet"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        lay_refs = inputs.get("layout") or []
        if not lay_refs:
            raise StepError("op_no_input", "input 'layout' (trim_layout.json) is empty")
        layout = await _read_json(ctx, lay_refs[0])
        try:
            size = int(params.get("size", BASE))
            overlap, flatten = float(params.get("overlap", 0.125)), float(params.get("flatten", 0.5))
            asset = slug(params.get("asset") or "trim", "trim")
            layout = normalize_layout(layout, str(layout.get("style") or "") if isinstance(layout, dict) else "")
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        if size not in SIZES:
            raise StepError("op_bad_params", f"size must be one of {SIZES}")
        strips = layout["strips"]
        if len(refs) != len(strips):
            raise StepError("op_bad_input", f"{len(refs)} strip images for {len(strips)} strips in the layout")
        pbr = PbrParams.parse({**{k: params[k] for k in ("normal_strength", "blur", "roughness", "roughness_contrast",
                                                          "metallic", "ao_strength") if k in params},
                               "seamless": True})
        scale = size / BASE
        maps, info = [], []
        for i, (ref, s) in enumerate(zip(refs, strips)):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.progress(i, len(strips), label=s["name"])
            src = await _load(ctx, ref)
            h = max(1, round(s["px"] * scale))
            band, meta = await asyncio.to_thread(make_strip, src, size, h, overlap, 3, flatten, s.get("aspect"))
            maps.append(await asyncio.to_thread(strip_maps, band, pbr))
            info.append(meta)
        sheet = await asyncio.to_thread(assemble, maps, size)
        ranges = strip_ranges(strips)
        scores = [m["seam"]["score"] for m in info]
        for r, s, m in zip(ranges, strips, info):
            r.update({"prompt": s["prompt"], "repeats": m["repeats"], "seam": m["seam"]["score"]})
        maps_out: list[BlobRef] = []
        entry: dict[str, Any] = {"name": asset, "size": [size, size], "maps": {}}
        for role in ROLES:
            r = await _put_png(ctx, sheet[role], f"{asset}_{role}.png")
            maps_out.append(r)
            entry["maps"][role] = {"name": r.name, "hash": r.hash}
        material = {"type": MATERIAL_TYPE, "green": pbr.green, "seamless": True, "trim": True, "materials": [entry]}
        mat_ref = await ctx.blobs.put_bytes(json.dumps(material, indent=1).encode(), name=MATERIAL_NAME,
                                            mime="application/json")
        doc = {"type": TRIM_TYPE, "asset": asset, "size": size, "base": BASE, "pad_px": PAD_PX,
               "style": layout["style"], "strips": ranges, "seam_min": min(scores),
               "albedo": {"name": maps_out[0].name, "hash": maps_out[0].hash}}
        trim_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=TRIM_FILE,
                                             mime="application/json")
        prev = await asyncio.to_thread(preview, sheet["albedo"], ranges, scores)
        grid = await asyncio.to_thread(preview_grid, sheet)
        ctx.progress(len(strips), len(strips))
        return StepResult(outputs={"images": [maps_out[0]], "maps": maps_out, "material": [mat_ref],
                                   "trim": [trim_ref],
                                   "preview": [await _put_png(ctx, prev, f"{asset}_trim_preview.png"),
                                               await _put_png(ctx, grid, f"{asset}_pbr_preview.png")]},
                          meta={"strips": len(strips), "size": size, "seam_min": min(scores),
                                "seam": {r["name"]: r["seam"] for r in ranges}})


TRIM_OPS = (TrimSheet,)
