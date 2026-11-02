"""Minimap from a level screenshot / mockup or a Tiled (.tmx/.tmj) / LDtk (.ldtk) map: `op.minimap_base` renders and
fits the map, `clean` style flattens it on CPU (palette regions + outline), `parchment` / `stylized` hand a copy to
Klein; `op.minimap_finish` puts POI icons on it (exact pixel centres), builds the circle mask, minimap.json."""
from __future__ import annotations

import asyncio
import base64
import gzip
import io
import json
import math
import re
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png, _read_json

MINIMAP_FILE = "minimap.json"
MINIMAP_TYPE = "asset-studio/minimap"
STYLES = ("clean", "parchment", "stylized")
SHAPES = ("circle", "square")
MAP_EXTS = (".tmx", ".tmj", ".ldtk")
MAX_RENDER = 2048
KLEIN_SIDE = 1024
MAX_POIS = 200
MERGE_RGB = 36.0
FLIP_MASK = 0x1FFFFFFF
# Decoration layers carry no terrain: unmatched names would take a fallback colour and paint over the ground.
NON_TERRAIN_LAYER_WORDS = ("shadow", "fog", "overlay", "decal")
PROMPTS = {
    "parchment": ("Redraw this top-down game minimap as an aged parchment fantasy map: sepia ink linework, muted "
                  "watercolor tints, hand-drawn hatching for forests and mountains, soft paper texture. Keep exactly "
                  "the same layout, coastlines, roads, rivers and region shapes in the same positions and sizes. No "
                  "text, no labels, no compass, no icons, no border."),
    "stylized": ("Redraw this top-down game minimap in a clean stylized hand-painted mobile game UI style: soft "
                 "painted colors, subtle texture, gentle shading, crisp readable region edges. Keep exactly the same "
                 "layout, coastlines, roads, rivers and region shapes in the same positions and sizes. No text, no "
                 "labels, no icons, no border."),
}
# Layer / entity names in Tiled and LDtk maps that say what the tiles are; the first keyword hit picks the colour.
KEYWORD_COLORS: list[tuple[tuple[str, ...], tuple[int, int, int]]] = [
    (("water", "sea", "lake", "river", "ocean", "nuoc", "pond"), (70, 130, 200)),
    (("lava", "magma", "fire"), (220, 95, 45)),
    (("wall", "cliff", "mountain", "rock", "stone", "collision", "solid", "block"), (112, 104, 98)),
    (("tree", "forest", "wood", "jungle", "bush"), (52, 112, 62)),
    (("road", "path", "street", "bridge", "dirt", "trail"), (206, 182, 132)),
    (("sand", "beach", "desert"), (230, 211, 152)),
    (("snow", "ice"), (236, 241, 246)),
    (("building", "house", "roof", "town", "city"), (170, 92, 72)),
    (("grass", "ground", "floor", "terrain", "base", "land", "field"), (124, 172, 92)),
]
FALLBACK_COLORS = [(124, 172, 92), (206, 182, 132), (112, 104, 98), (70, 130, 200), (170, 92, 72), (52, 112, 62)]
POI_STYLE: dict[str, tuple[str, tuple[int, int, int]]] = {
    "player": ("arrow", (60, 140, 255)), "town": ("house", (240, 236, 220)), "city": ("house", (240, 236, 220)),
    "village": ("house", (240, 236, 220)), "shop": ("coin", (250, 200, 60)), "quest": ("bang", (255, 214, 40)),
    "enemy": ("triangle", (225, 60, 60)), "boss": ("star", (210, 40, 40)), "chest": ("box", (190, 130, 60)),
    "treasure": ("box", (190, 130, 60)), "portal": ("ring", (170, 90, 230)), "npc": ("dot", (80, 200, 120)),
    "dungeon": ("arch", (90, 80, 100)), "save": ("diamond", (90, 210, 230)), "flag": ("flag", (230, 80, 70)),
}
DEFAULT_POI = ("pin", (240, 110, 60))


@dataclass
class Poi:
    name: str
    type: str
    x: float
    y: float
    px: tuple[int, int] = (0, 0)


@dataclass
class Loaded:
    image: Image.Image
    source_size: tuple[int, int]
    # image px per source px (tile maps render at a capped resolution).
    scale: float = 1.0
    pois: list[Poi] = field(default_factory=list)
    kind: str = "image"


def _color_for(name: str, index: int) -> tuple[int, int, int]:
    low = name.lower()
    for words, rgb in KEYWORD_COLORS:
        if any(w in low for w in words):
            return rgb
    return FALLBACK_COLORS[index % len(FALLBACK_COLORS)]


def _shade(rgb: tuple[int, int, int], gid: int) -> tuple[int, int, int]:
    k = 1 + (((gid * 2654435761) >> 7) % 9 - 4) / 60
    return tuple(int(max(0, min(255, c * k))) for c in rgb)  # type: ignore[return-value]


def _decode_tiles(data: str | list[int], encoding: str | None, compression: str | None) -> list[int]:
    if isinstance(data, list):
        return [int(v) for v in data]
    if encoding == "csv" or (encoding is None and "," in data):
        return [int(v) for v in re.split(r"[,\s]+", data.strip()) if v]
    raw = base64.b64decode(data.strip())
    if compression == "zlib":
        raw = zlib.decompress(raw)
    elif compression == "gzip":
        raw = gzip.decompress(raw)
    elif compression:
        raise StepError("op_bad_input", f"Tiled compression '{compression}' is not supported (use CSV, zlib or gzip)")
    return list(np.frombuffer(raw, "<u4").astype(np.int64))


def _render_grid(w: int, h: int, tw: int, th: int, layers: list[tuple[str, list[int]]]) -> Loaded:
    grid = np.zeros((h, w, 3), np.uint8)
    grid[:] = (36, 40, 44)
    for i, (name, gids) in enumerate(layers):
        if len(gids) < w * h or any(wd in name.lower() for wd in NON_TERRAIN_LAYER_WORDS):
            continue
        base = _color_for(name, i)
        arr = np.asarray(gids[:w * h], np.int64).reshape(h, w) & FLIP_MASK
        for gid in np.unique(arr):
            if gid:
                grid[arr == gid] = _shade(base, int(gid))
    src = (w * tw, h * th)
    s = min(1.0, MAX_RENDER / max(src))
    img = Image.fromarray(grid, "RGB").resize((max(1, round(src[0] * s)), max(1, round(src[1] * s))),
                                               Image.Resampling.NEAREST)
    return Loaded(img.convert("RGBA"), src, s, kind="tilemap")


def _poi_type(raw: str, name: str) -> str:
    t = (raw or name or "").strip().lower()
    for k in POI_STYLE:
        if k in t:
            return k
    return re.sub(r"[^\w-]", "", t)[:24] or "poi"


def _tmx(data: bytes) -> Loaded:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise StepError("op_bad_input", f"bad .tmx: {e}") from e
    if root.tag != "map" or root.get("orientation", "orthogonal") != "orthogonal":
        raise StepError("op_bad_input", "only orthogonal Tiled maps are supported")
    if root.get("infinite") == "1":
        raise StepError("op_bad_input", "infinite Tiled maps are not supported; resize the map to a fixed size")
    w, h, tw, th = (int(root.get(k, "0")) for k in ("width", "height", "tilewidth", "tileheight"))
    layers = []
    pois: list[Poi] = []
    for el in root.iter():
        if el.tag == "layer" and el.get("visible", "1") != "0":
            d = el.find("data")
            if d is None:
                continue
            if d.get("encoding"):
                gids = _decode_tiles(d.text or "", d.get("encoding"), d.get("compression"))
            else:
                gids = [int(t.get("gid", "0")) for t in d.findall("tile")]
            layers.append((el.get("name", ""), gids))
        elif el.tag == "objectgroup":
            for o in el.findall("object"):
                ox, oy = float(o.get("x", 0)), float(o.get("y", 0))
                ow, oh = float(o.get("width", 0)), float(o.get("height", 0))
                if o.get("gid"):
                    oy -= oh  # Tiled anchors tile objects at the bottom-left
                name = o.get("name") or ""
                pois.append(Poi(name or o.get("type", "") or o.get("class", "") or "poi",
                                _poi_type(o.get("type") or o.get("class") or "", name), ox + ow / 2, oy + oh / 2))
    if not (w and h and tw and th):
        raise StepError("op_bad_input", ".tmx has no size")
    out = _render_grid(w, h, tw, th, layers)
    out.pois = pois
    return out


def _tmj(doc: dict[str, Any]) -> Loaded:
    if doc.get("infinite"):
        raise StepError("op_bad_input", "infinite Tiled maps are not supported; resize the map to a fixed size")
    w, h, tw, th = (int(doc.get(k) or 0) for k in ("width", "height", "tilewidth", "tileheight"))
    layers, pois = [], []

    def walk(items: list[dict[str, Any]]) -> None:
        for la in items:
            if la.get("visible") is False:
                continue
            if la.get("type") == "group":
                walk(la.get("layers") or [])
            elif la.get("type") == "tilelayer":
                layers.append((str(la.get("name", "")),
                               _decode_tiles(la.get("data") or [], la.get("encoding"), la.get("compression"))))
            elif la.get("type") == "objectgroup":
                for o in la.get("objects") or []:
                    name = str(o.get("name") or "")
                    kind = str(o.get("type") or o.get("class") or "")
                    pois.append(Poi(name or kind or "poi", _poi_type(kind, name),
                                    float(o.get("x", 0)) + float(o.get("width", 0)) / 2,
                                    float(o.get("y", 0)) + float(o.get("height", 0)) / 2))

    walk(doc.get("layers") or [])
    if not (w and h and tw and th):
        raise StepError("op_bad_input", ".tmj has no size")
    out = _render_grid(w, h, tw, th, layers)
    out.pois = pois
    return out


def _hex(c: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    s = str(c or "").lstrip("#")
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except (ValueError, IndexError):
        return default


def _entity_name(e: dict[str, Any]) -> str:
    for f in e.get("fieldInstances") or []:
        if str(f.get("__identifier", "")).lower() in ("name", "label", "title") and isinstance(f.get("__value"), str):
            return f["__value"]
    return str(e.get("__identifier") or "poi")


def _ldtk(doc: dict[str, Any], level: str) -> Loaded:
    levels = doc.get("levels") or []
    if not levels:
        raise StepError("op_bad_input", ".ldtk has no levels")
    pick = None
    if level.strip().isdigit() and int(level) < len(levels):
        pick = levels[int(level)]
    pick = pick or next((lv for lv in levels if str(lv.get("identifier", "")).lower() == level.strip().lower()),
                        levels[0])
    if pick.get("layerInstances") is None:
        raise StepError("op_bad_input", "LDtk 'separate level files' is on; open the level .ldtkl or turn it off")
    defs = {d.get("uid"): d for d in (doc.get("defs") or {}).get("layers") or []}
    pw, ph = int(pick.get("pxWid") or 0), int(pick.get("pxHei") or 0)
    if not (pw and ph):
        raise StepError("op_bad_input", "LDtk level has no size")
    s = min(1.0, MAX_RENDER / max(pw, ph))
    bg = _hex(pick.get("__bgColor") or pick.get("bgColor") or doc.get("bgColor"), (36, 40, 44))
    canvas = Image.new("RGB", (max(1, round(pw * s)), max(1, round(ph * s))), bg)
    draw = ImageDraw.Draw(canvas)
    pois: list[Poi] = []
    for i, li in enumerate(reversed(pick["layerInstances"])):
        if li.get("visible") is False:
            continue
        g = int(li.get("__gridSize") or 16)
        cw = int(li.get("__cWid") or 0)
        name = str(li.get("__identifier", ""))
        ox, oy = int(li.get("__pxTotalOffsetX") or 0), int(li.get("__pxTotalOffsetY") or 0)

        def cell(x: float, y: float, rgb: tuple[int, int, int], size: int = g) -> None:
            draw.rectangle((round((x + ox) * s), round((y + oy) * s), round((x + ox + size) * s) - 1,
                            round((y + oy + size) * s) - 1), fill=rgb)

        kind = li.get("__type")
        if kind == "IntGrid":
            colors = {int(v.get("value")): _hex(v.get("color"), _color_for(str(v.get("identifier") or name), i))
                      for v in (defs.get(li.get("layerDefUid")) or {}).get("intGridValues") or []}
            for idx, v in enumerate(li.get("intGridCsv") or []):
                if v:
                    cell((idx % cw) * g, (idx // cw) * g, colors.get(int(v), _color_for(name, int(v))))
        if kind in ("Tiles", "AutoLayer") or (kind == "IntGrid" and not li.get("intGridCsv")):
            base = _color_for(name, i)
            for t in (li.get("gridTiles") or []) + (li.get("autoLayerTiles") or []):
                x, y = t.get("px") or (0, 0)
                cell(x, y, _shade(base, int(t.get("t") or 0)))
        if kind == "Entities":
            for e in li.get("entityInstances") or []:
                x, y = e.get("px") or (0, 0)
                ew, eh = float(e.get("width") or 0), float(e.get("height") or 0)
                piv = e.get("__pivot") or (0, 0)
                cx = x + ox + ew * (0.5 - float(piv[0]))
                cy = y + oy + eh * (0.5 - float(piv[1]))
                name_e = _entity_name(e)
                pois.append(Poi(name_e, _poi_type(str(e.get("__identifier") or ""), name_e), cx, cy))
    return Loaded(canvas.convert("RGBA"), (pw, ph), s, pois, "ldtk")


def load_source(data: bytes, name: str, level: str = "") -> Loaded:
    ext = Path(name.lower()).suffix
    if ext == ".tmx":
        return _tmx(data)
    if ext in (".tmj", ".ldtk", ".json"):
        try:
            doc = json.loads(data)
        except ValueError as e:
            raise StepError("op_bad_input", f"bad map JSON: {e}") from e
        if isinstance(doc, dict) and ("levels" in doc and "defs" in doc):
            return _ldtk(doc, level)
        if isinstance(doc, dict) and doc.get("type") == "map":
            return _tmj(doc)
        raise StepError("op_bad_input", "JSON is neither a Tiled map (.tmj) nor an LDtk project (.ldtk)")
    try:
        with Image.open(io.BytesIO(data)) as im:
            img = im.convert("RGBA")
    except OSError as e:
        raise StepError("op_bad_input", f"not an image or map file: {name}") from e
    return Loaded(img, img.size)


def parse_pois(text: str, source_size: tuple[int, int]) -> list[Poi]:
    """One POI per line: `name, x, y, type` (source pixels, or 0..1 when both have a decimal point); `;` or tab
    also separate; a line `x, y` alone gets the name poi_<n>."""
    out = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[,;\t|]", line)]
        nums = [i for i, p in enumerate(parts) if re.fullmatch(r"-?\d+(\.\d+)?", p)]
        if len(nums) < 2:
            raise StepError("op_bad_params", f"POI line needs x and y: '{line}'")
        xi, yi = nums[0], nums[1]
        x, y = float(parts[xi]), float(parts[yi])
        if "." in parts[xi] and "." in parts[yi] and 0 <= x <= 1 and 0 <= y <= 1:
            x, y = x * source_size[0], y * source_size[1]
        rest = [p for i, p in enumerate(parts) if i not in (xi, yi) and p]
        name = rest[0] if rest else f"poi_{len(out) + 1}"
        out.append(Poi(name, _poi_type(rest[1] if len(rest) > 1 else "", name), x, y))
    return out


def fit_square(img: Image.Image, size: int, nearest: bool) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Contain-fit into size x size on the map's dominant border colour; returns the canvas and the map rect."""
    w, h = img.size
    s = size / max(w, h)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    small = img.resize((nw, nh), Image.Resampling.NEAREST if nearest else Image.Resampling.LANCZOS)
    a = np.asarray(img.convert("RGB"))
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = tuple(int(c) for c in np.median(border.reshape(-1, 3), axis=0))
    canvas = Image.new("RGBA", (size, size), (*bg, 255))
    x0, y0 = (size - nw) // 2, (size - nh) // 2
    canvas.alpha_composite(small.convert("RGBA"), (x0, y0))
    return canvas, (x0, y0, nw, nh)


def _merge_close(lab: np.ndarray, pal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Median cut splits the biggest region (the ground) into near-twins; fold palette entries closer than
    MERGE_RGB into one so a single region keeps one colour and gets no outline through it."""
    used = np.unique(lab)
    counts = np.bincount(lab.ravel(), minlength=len(pal)).astype(np.float64)
    parent = {int(i): int(i) for i in used}

    def find(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    for a in used:
        for b in used:
            if a < b and np.linalg.norm(pal[a] - pal[b]) < MERGE_RGB:
                parent[find(int(b))] = find(int(a))
    roots = sorted({find(int(i)) for i in used})
    index = {r: n for n, r in enumerate(roots)}
    new_pal = np.zeros((len(roots), 3), np.float32)
    for r in roots:
        members = [int(i) for i in used if find(int(i)) == r]
        w = counts[members]
        new_pal[index[r]] = (pal[members] * w[:, None]).sum(0) / max(w.sum(), 1)
    lut = np.zeros(len(pal), np.int32)
    for i in used:
        lut[int(i)] = index[find(int(i))]
    return lut[lab], new_pal


def clean_style(img: Image.Image, colors: int) -> Image.Image:
    """Flat readable map: median-cut palette, mode filter to drop specks, then a dark line on region borders."""
    size = img.width
    # Screenshots carry noise / JPEG grain that would split one region into two palette colours.
    rgb = img.convert("RGB").filter(ImageFilter.GaussianBlur(max(1.0, size / 256))).filter(ImageFilter.MedianFilter(5))
    k = max(2, min(32, colors))
    q = rgb.quantize(colors=k, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = np.asarray(q.getpalette()[:3 * 256], np.float32).reshape(-1, 3)
    lab = np.asarray(q, np.int32)
    lab, pal = _merge_close(lab, pal)
    q = Image.fromarray(lab.astype(np.uint8), "L").filter(ImageFilter.ModeFilter(max(3, (size // 160) | 1)))
    lab = np.asarray(q, np.int32)
    flat = pal[lab]
    edge = np.zeros(lab.shape, bool)
    edge[:, 1:] |= lab[:, 1:] != lab[:, :-1]
    edge[1:, :] |= lab[1:, :] != lab[:-1, :]
    width = max(1, size // 384)
    if width > 1:
        edge = np.asarray(Image.fromarray(edge.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(width * 2 - 1))) > 0
    flat[edge] *= 0.55
    mean = flat.mean(-1, keepdims=True)
    flat = np.clip(mean + (flat - mean) * 1.08, 0, 255)
    return Image.fromarray(flat.astype(np.uint8), "RGB").convert("RGBA")


def _shape_points(shape: str, c: float, r: float) -> list[tuple[float, float]]:
    if shape == "triangle":
        return [(c, c - r), (c + r * 0.9, c + r * 0.7), (c - r * 0.9, c + r * 0.7)]
    if shape == "diamond":
        return [(c, c - r), (c + r, c), (c, c + r), (c - r, c)]
    if shape == "star":
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rr = r if i % 2 == 0 else r * 0.45
            pts.append((c + rr * math.cos(a), c + rr * math.sin(a)))
        return pts
    if shape == "arrow":
        return [(c, c - r), (c + r * 0.75, c + r * 0.8), (c, c + r * 0.35), (c - r * 0.75, c + r * 0.8)]
    if shape == "house":
        return [(c, c - r), (c + r, c - r * 0.1), (c + r * 0.7, c - r * 0.1), (c + r * 0.7, c + r * 0.8),
                (c - r * 0.7, c + r * 0.8), (c - r * 0.7, c - r * 0.1), (c - r, c - r * 0.1)]
    if shape == "pin":
        return [(c + r * 0.75 * math.cos(a), c - r * 0.25 + r * 0.75 * math.sin(a))
                for a in np.linspace(math.pi * 0.8, math.pi * 2.2, 14)] + [(c, c + r)]
    return []


def draw_icon(kind: str, size: int) -> Image.Image:
    """CPU POI icon centred on its canvas: dark outline + white rim keep it readable on any map colour."""
    shape, rgb = POI_STYLE.get(kind, DEFAULT_POI)
    ss = 4
    n = size * ss
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c, r = n / 2, n * 0.38
    rim, dark = max(2, n // 12), (25, 22, 30, 255)
    fill = (*rgb, 255)
    if shape in ("dot", "coin", "ring"):
        d.ellipse((c - r - rim, c - r - rim, c + r + rim, c + r + rim), fill=dark)
        d.ellipse((c - r, c - r, c + r, c + r), fill=(255, 255, 255, 255))
        d.ellipse((c - r + rim, c - r + rim, c + r - rim, c + r - rim), fill=fill)
        if shape == "ring":
            q = r * 0.45
            d.ellipse((c - q, c - q, c + q, c + q), fill=dark)
        if shape == "coin":
            q = r * 0.35
            d.rectangle((c - q * 0.35, c - q, c + q * 0.35, c + q), fill=dark)
    elif shape in ("box", "arch", "bang", "flag"):
        x0, y0, x1, y1 = c - r, c - r * 0.8, c + r, c + r * 0.8
        d.rounded_rectangle((x0 - rim, y0 - rim, x1 + rim, y1 + rim), radius=rim * 2, fill=dark)
        d.rounded_rectangle((x0, y0, x1, y1), radius=rim, fill=(255, 255, 255, 255))
        d.rounded_rectangle((x0 + rim, y0 + rim, x1 - rim, y1 - rim), radius=rim, fill=fill)
        if shape == "bang":
            d.rectangle((c - rim, y0 + rim * 2, c + rim, c + r * 0.15), fill=dark)
            d.ellipse((c - rim, c + r * 0.3, c + rim, c + r * 0.3 + 2 * rim), fill=dark)
        elif shape == "box":
            d.rectangle((x0 + rim, c - rim / 2, x1 - rim, c + rim / 2), fill=dark)
        elif shape == "arch":
            d.pieslice((c - r * 0.45, c - r * 0.2, c + r * 0.45, c + r * 0.7), 180, 360, fill=dark)
            d.rectangle((c - r * 0.45, c + r * 0.25, c + r * 0.45, y1 - rim), fill=dark)
        else:
            d.rectangle((c - r * 0.5, y0 + rim * 2, c - r * 0.5 + rim, y1 - rim * 2), fill=dark)
            d.polygon([(c - r * 0.5 + rim, y0 + rim * 2), (c + r * 0.6, c - r * 0.25),
                       (c - r * 0.5 + rim, c + r * 0.1)], fill=dark)
    else:
        pts = _shape_points(shape, c, r)
        d.polygon(pts, fill=dark, outline=dark, width=rim * 2)
        d.polygon(pts, fill=(255, 255, 255, 255), outline=(255, 255, 255, 255), width=rim)
        inner = [(c + (x - c) * 0.72, c + (y - c) * 0.72) for x, y in pts]
        d.polygon(inner, fill=fill)
    return im.resize((size, size), Image.Resampling.LANCZOS)


def circle_mask(size: int, inset: int = 0) -> Image.Image:
    ss = 4
    m = Image.new("L", (size * ss, size * ss), 0)
    ImageDraw.Draw(m).ellipse((inset * ss, inset * ss, (size - inset) * ss - 1, (size - inset) * ss - 1), fill=255)
    return m.resize((size, size), Image.Resampling.LANCZOS)


def place_pois(pois: list[Poi], scale: float, rect: tuple[int, int, int, int], fit_scale: float) -> list[Poi]:
    x0, y0, _, _ = rect
    out = []
    for p in pois:
        out.append(Poi(p.name, p.type, p.x, p.y, (round(x0 + p.x * scale * fit_scale),
                                                    round(y0 + p.y * scale * fit_scale))))
    return out


def stamp(img: Image.Image, pois: list[Poi], icon: int, icons: dict[str, Image.Image]) -> Image.Image:
    out = img.copy()
    cache: dict[str, Image.Image] = {}
    for p in pois:
        if p.type not in cache:
            src = icons.get(p.type)
            if src is not None:
                ic = src.convert("RGBA")
                ic.thumbnail((icon, icon), Image.Resampling.LANCZOS)
                pad = Image.new("RGBA", (icon, icon), (0, 0, 0, 0))
                pad.alpha_composite(ic, ((icon - ic.width) // 2, (icon - ic.height) // 2))
                cache[p.type] = pad
            else:
                cache[p.type] = draw_icon(p.type, icon)
        # Odd icon sizes put the exact centre pixel on the POI.
        out.alpha_composite(cache[p.type], (p.px[0] - icon // 2, p.px[1] - icon // 2))
    return out


def framed(img: Image.Image, shape: str) -> Image.Image:
    size = img.width
    if shape == "square":
        out = img.copy()
        d = ImageDraw.Draw(out)
        ring = max(2, size // 96)
        d.rectangle((0, 0, size - 1, size - 1), outline=(30, 26, 34, 255), width=ring)
        return out
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ring = max(2, size // 64)
    out.paste((30, 26, 34, 255), (0, 0), circle_mask(size))
    inner = img.copy()
    inner.putalpha(circle_mask(size, ring))
    out.alpha_composite(inner)
    hl = Image.new("L", (size, size), 0)
    ImageDraw.Draw(hl).ellipse((ring, ring, size - ring - 1, size - ring - 1), outline=255, width=max(1, ring // 3))
    out.paste((235, 225, 200, 255), (0, 0), hl)
    return out


def _font(px: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def preview(img: Image.Image, pois: list[Poi], shape: str, icon: int) -> Image.Image:
    out = framed(img, shape)
    d = ImageDraw.Draw(out)
    font = _font(max(11, img.width // 40))
    for p in pois:
        x, y = p.px[0] + icon // 2 + 3, p.px[1] - icon // 2
        d.text((x, y), p.name, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(20, 18, 24, 255))
    return out


def _int_param(params: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(params.get(key) if params.get(key) not in (None, "") else default)
    except (TypeError, ValueError):
        raise StepError("op_bad_params", f"{key} must be an integer") from None
    if not lo <= v <= hi:
        raise StepError("op_bad_params", f"{key} must be in [{lo}, {hi}]")
    return v


def _choice(params: dict[str, Any], key: str, options: tuple[str, ...]) -> str:
    v = str(params.get(key) or options[0]).strip().lower()
    if v not in options:
        raise StepError("op_bad_params", f"{key} must be one of {', '.join(options)}")
    return v


def _asset(params: dict[str, Any]) -> str:
    from .enemy_variants import slug

    return slug(str(params.get("asset") or "minimap")) or "minimap"


async def _put_json(ctx: StepContext, doc: Any, name: str) -> BlobRef:
    return await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode("utf-8"), name=name,
                                     mime="application/json")


class MinimapBase(OpExecutor):
    kind = "op.minimap_base"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("source") or inputs.get("images") or []
        if not refs:
            raise StepError("op_no_input", "input 'source' is empty (an image, .tmx, .tmj or .ldtk)")
        size = _int_param(params, "size", 512, 64, 2048)
        colors = _int_param(params, "colors", 8, 2, 32)
        style = _choice(params, "style", STYLES)
        _choice(params, "shape", SHAPES)
        ref = refs[0]
        data = await asyncio.to_thread(ctx.blobs.path(ref.hash).read_bytes)
        loaded = await asyncio.to_thread(load_source, data, ref.name or "map.png", str(params.get("level") or ""))
        pois = loaded.pois + parse_pois(str(params.get("pois") or ""), loaded.source_size)
        if len(pois) > MAX_POIS:
            raise StepError("op_bad_params", f"at most {MAX_POIS} POIs")
        ctx.progress(0.3, label="fit")
        canvas, rect = await asyncio.to_thread(fit_square, loaded.image, size, loaded.kind != "image")
        fit_scale = rect[2] / loaded.image.width
        placed = place_pois(pois, loaded.scale, rect, fit_scale)
        base = await asyncio.to_thread(clean_style, canvas, colors) if style == "clean" else canvas
        asset = _asset(params)
        base_ref = await _put_png(ctx, base, f"{asset}_base.png")
        restyle_in: list[BlobRef] = []
        prompts: list[str] = []
        if style != "clean":
            # Klein scales its input to 1 MP with nearest-exact; handing it 1024 px keeps the map edges smooth.
            big = canvas.convert("RGB").resize((KLEIN_SIDE, KLEIN_SIDE), Image.Resampling.LANCZOS)
            restyle_in.append(await _put_png(ctx, big, f"{asset}_restyle_in.png"))
            extra = str(params.get("prompt") or "").strip()
            prompts.append(PROMPTS[style] + (f" {extra}" if extra else ""))
        info = {"asset": asset, "style": style, "shape": str(params.get("shape") or "circle").lower(), "size": size,
                "source": {"name": ref.name, "kind": loaded.kind, "size": list(loaded.source_size)},
                "scale": loaded.scale * fit_scale, "map_rect": list(rect),
                "pois": [{"name": p.name, "type": p.type, "src": [round(p.x, 2), round(p.y, 2)], "px": list(p.px)}
                         for p in placed]}
        ctx.progress(1.0)
        return StepResult(outputs={"base": [base_ref], "restyle_in": restyle_in,
                                   "layout": [await _put_json(ctx, {"prompts": prompts}, "minimap_layout.json")],
                                   "info": [await _put_json(ctx, info, "minimap_info.json")]},
                          meta={"style": style, "pois": len(placed), "source": loaded.kind,
                                "source_size": list(loaded.source_size)})


class MinimapFinish(OpExecutor):
    kind = "op.minimap_finish"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        if not inputs.get("base") or not inputs.get("info"):
            raise StepError("op_no_input", "minimap_finish needs base and info")
        info = await _read_json(ctx, inputs["info"][0])
        base = await _load(ctx, inputs["base"][0])
        size = int(info["size"])
        edits = inputs.get("edits") or []
        img = base.convert("RGBA")
        if edits:
            img = (await _load(ctx, edits[0])).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        icon = _int_param(params, "icon", max(13, size // 22) | 1, 7, 255) | 1
        icons: dict[str, Image.Image] = {}
        for r in inputs.get("icons") or []:
            icons[Path((r.name or "").lower()).stem.split("_")[0]] = await _load(ctx, r)
        pois = [Poi(p["name"], p["type"], p["src"][0], p["src"][1], (int(p["px"][0]), int(p["px"][1])))
                for p in info.get("pois") or []]
        shape = info.get("shape", "circle")
        asset = info["asset"]
        out = await asyncio.to_thread(stamp, img, pois, icon, icons)
        mask = circle_mask(size) if shape == "circle" else Image.new("L", (size, size), 255)
        mask_rgba = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        mask_rgba.putalpha(mask)
        map_ref = await _put_png(ctx, out, f"{asset}_minimap.png")
        frame_ref = await _put_png(ctx, await asyncio.to_thread(framed, out, shape), f"{asset}_minimap_framed.png")
        mask_ref = await _put_png(ctx, mask_rgba, f"{asset}_minimap_mask.png")
        prev_ref = await _put_png(ctx, await asyncio.to_thread(preview, out, pois, shape, icon),
                                  f"{asset}_minimap_preview.png")
        outside = []
        r = size / 2
        rows = []
        for p in pois:
            x, y = p.px
            if shape == "circle" and (x + 0.5 - r) ** 2 + (y + 0.5 - r) ** 2 > (r - icon / 2) ** 2:
                outside.append(p.name)
            rows.append({"name": p.name, "type": p.type, "x": round((x + 0.5) / size, 5),
                         "y": round((y + 0.5) / size, 5), "px": [x, y], "src": [p.x, p.y]})
        doc = {"type": MINIMAP_TYPE, "asset": asset, "size": [size, size], "shape": shape, "style": info["style"],
               "convention": "x right, y down from the top-left of the image; x/y are 0..1 of the image, px pixels",
               "source": info["source"], "scale": info["scale"], "map_rect": info["map_rect"], "icon": icon,
               "pois": rows, "outside_circle": outside,
               "map": {"hash": map_ref.hash, "file": f"{asset}_minimap.png"},
               "framed": {"hash": frame_ref.hash, "file": f"{asset}_minimap_framed.png"},
               "mask": {"hash": mask_ref.hash, "file": f"{asset}_minimap_mask.png"}}
        doc_ref = await _put_json(ctx, doc, MINIMAP_FILE)
        ctx.progress(1.0)
        return StepResult(outputs={"images": [map_ref], "framed": [frame_ref], "mask": [mask_ref],
                                   "preview": [prev_ref], "minimap": [doc_ref]},
                          meta={"pois": len(rows), "outside_circle": outside, "restyled": bool(edits),
                                "style": info["style"], "shape": shape})


MINIMAP_OPS = (MinimapBase, MinimapFinish)
