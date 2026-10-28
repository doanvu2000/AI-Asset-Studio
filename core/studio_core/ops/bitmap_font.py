"""Stylized bitmap fonts for game UI: glyphs rasterized from a local TTF (exact advance, bearings and pair kerning),
styled on CPU (gradient, outline, shadow, bevel, inner glow, pixel-art mode) or textured from a Klein-restyled glyph
sheet under the TTF's own alpha, packed into a power-of-two atlas with AngelCode BMFont text (.fnt) + font.json."""
from __future__ import annotations

import asyncio
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png
from .localize import _has_glyph
from .rig import _read_json
from .sfnt_kern import kerning
from .store_capsules import FONT_FILES, _hex, available_fonts, load_font

FONT_FILE = "font.json"
FONT_TYPE = "asset-studio/bitmap-font"
SHEET_FILE = "font_sheet.json"
SHEET_TYPE = "asset-studio/font-sheet"
CHARSETS = ("digits", "ascii", "vietnamese", "custom")
DIGITS = "0123456789 +-.,:/%x"
ASCII = "".join(chr(c) for c in range(32, 127))
VI_BASES = "aăâeêioôơuưy"
VI_TONES = "̣̀́̉̃"
SAMPLES = ("Điểm: 12345", "SCORE: 12345", "LEVEL UP!", "The quick brown fox jumps over the lazy dog 0123456789",
           "Chữ Việt: ăn ở đây, quả bưởi Ơn trời! ẮẶẲẴ ỐỘỔỖ ỨỰỬỮ Đ", "+250 x3 99% 1:30")
MAX_ATLAS = 4096
SHEET_SIDE = 1024
PRESETS: dict[str, dict[str, Any]] = {
    "gold": {"fonts": ("impact", "arial_black", "segoe_black", "arial_bold"), "top": "#fff6b0",
             "bottom": "#e8901a", "outline": 0.06, "outline_color": "#4a2100", "shadow": (0.0, 0.06), "blur": 0.03,
             "shadow_color": "#000000", "shadow_opacity": 0.55, "bevel": 0.7, "glow": 0.0, "glow_color": "#fffbe0"},
    "ice": {"fonts": ("segoe_black", "verdana_bold", "arial_bold"), "top": "#ffffff", "bottom": "#5cc2ff",
            "outline": 0.05, "outline_color": "#0a3566", "shadow": (0.0, 0.04), "blur": 0.02,
            "shadow_color": "#001020", "shadow_opacity": 0.5, "bevel": 0.5, "glow": 0.5, "glow_color": "#e6fbff"},
    "wood": {"fonts": ("cambria_bold", "georgia_bold", "times_bold"), "top": "#e0a868", "bottom": "#8a5426",
             "outline": 0.05, "outline_color": "#2e1a0b", "shadow": (0.02, 0.05), "blur": 0.01,
             "shadow_color": "#000000", "shadow_opacity": 0.6, "bevel": 0.8, "glow": 0.0, "glow_color": "#ffd9a0"},
    "candy": {"fonts": ("comic_bold", "trebuchet_bold", "verdana_bold", "segoe_black"), "top": "#ffb3e0",
              "bottom": "#ff3d8b", "outline": 0.08, "outline_color": "#ffffff", "shadow": (0.0, 0.06), "blur": 0.0,
              "shadow_color": "#8a1050", "shadow_opacity": 0.9, "bevel": 0.4, "glow": 0.3, "glow_color": "#ffffff"},
    "neon": {"fonts": ("bahnschrift", "segoe_bold", "arial_bold"), "top": "#ffffff", "bottom": "#a8fff4",
             "outline": 0.025, "outline_color": "#00e1ff", "shadow": (0.0, 0.0), "blur": 0.12,
             "shadow_color": "#00e1ff", "shadow_opacity": 0.9, "bevel": 0.0, "glow": 0.4, "glow_color": "#00e1ff"},
    "plain": {"fonts": ("segoe_bold", "arial_bold"), "top": "#ffffff", "bottom": "#ffffff", "outline": 0.05,
              "outline_color": "#000000", "shadow": (0.0, 0.04), "blur": 0.0, "shadow_color": "#000000",
              "shadow_opacity": 0.6, "bevel": 0.0, "glow": 0.0, "glow_color": "#ffffff"},
    "pixel": {"fonts": ("tahoma_bold", "verdana_bold", "arial_bold"), "top": "#ffe14d", "bottom": "#ff7a00",
              "outline": 0.07, "outline_color": "#1a0d00", "shadow": (0.07, 0.07), "blur": 0.0,
              "shadow_color": "#000000", "shadow_opacity": 1.0, "bevel": 0.6, "glow": 0.0, "glow_color": "#ffffff",
              "pixel": True},
}


def vietnamese_letters() -> str:
    out = []
    for b in VI_BASES + VI_BASES.upper():
        out += [b, *(unicodedata.normalize("NFC", b + t) for t in VI_TONES)]
    return "".join(c for c in out if c not in ASCII) + "đĐ"


def charset(kind: str, custom: str = "") -> str:
    if kind not in CHARSETS:
        raise StepError("op_bad_params", f"charset must be one of {', '.join(CHARSETS)}")
    base = {"digits": DIGITS, "ascii": ASCII, "vietnamese": ASCII + vietnamese_letters(), "custom": " "}[kind]
    extra = [c for c in unicodedata.normalize("NFC", custom or "") if c.isprintable() and not c.isspace()]
    if kind == "custom" and not extra:
        raise StepError("op_bad_params", "charset 'custom' needs the characters in 'custom'")
    return "".join(dict.fromkeys(base + "".join(extra)))


def missing_glyphs(path: str, chars: str) -> list[str]:
    try:
        return [c for c in chars if not c.isspace() and not _has_glyph(path, c)]
    except OSError:
        return [c for c in chars if not c.isspace()]


def resolve_font(choice: str, preset: str, chars: str) -> tuple[str, str]:
    """(font id or file name, path); 'auto' takes the preset's first installed font that has every glyph."""
    fonts = available_fonts()
    choice = (choice or "auto").strip()
    if choice != "auto":
        path = fonts.get(choice) or (choice if Path(choice).is_file() else None)
        if path is None:
            raise StepError("op_bad_params", f"font '{choice}' is not installed (have: {', '.join(fonts)})")
        miss = missing_glyphs(path, chars)
        if miss:
            raise StepError("op_bad_params", f"font '{choice}' lacks {len(miss)} glyphs: {''.join(miss[:40])}")
        return (choice if choice in fonts else Path(path).name), path
    order = [*PRESETS.get(preset, PRESETS["plain"])["fonts"], *FONT_FILES]
    for fid in dict.fromkeys(order):
        if fid in fonts and not missing_glyphs(fonts[fid], chars):
            return fid, fonts[fid]
    raise StepError("op_no_font", "no installed font covers this charset (Vietnamese needs e.g. Segoe UI / Arial)")


def _rgb(h: str) -> np.ndarray:
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255


def _num(v: Any, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return default if x != x else x


@dataclass
class Style:
    top: str
    bottom: str
    outline: float
    outline_color: str
    shadow: tuple[float, float]
    blur: float
    shadow_color: str
    shadow_opacity: float
    bevel: float
    glow: float
    glow_color: str
    pixel: bool = False
    scale: int = 1
    bands: int = 4
    tracking: int = 0

    def pads(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) room around the glyph ink for outline + shadow."""
        o, e = self.outline, (0 if self.pixel else 3 * self.blur)
        dx, dy = self.shadow
        sides = (o + max(0.0, e - dx), o + max(0.0, e - dy), o + max(0.0, e + dx), o + max(0.0, e + dy))
        return tuple(math.ceil(v) + (0 if self.pixel else 1) for v in sides)  # type: ignore[return-value]


def resolve_style(params: dict[str, Any], size: int) -> Style:
    """Preset values scale with the font size; explicit params (px, colours) override, -1 / auto keep the preset."""
    name = str(params.get("preset") or "gold")
    if name not in PRESETS:
        raise StepError("op_bad_params", f"preset must be one of {', '.join(PRESETS)}")
    p = PRESETS[name]
    pix = str(params.get("pixel") or "auto").lower()
    pixel = bool(p.get("pixel")) if pix == "auto" else pix in ("on", "true", "1")
    scale = int(_num(params.get("pixel_scale"), 3)) if pixel else 1
    if not 1 <= scale <= 4:
        raise StepError("op_bad_params", "pixel_scale must be 1..4")
    base = max(6, round(size / scale))

    def px(key: str, frac: float) -> float:
        v = _num(params.get(key), -1)
        val = v if v >= 0 else frac * base
        return float(round(val)) if pixel and val > 0 and v < 0 else val

    colors = [c for c in re.split(r"[,\s]+", str(params.get("colors") or "auto")) if c]
    top = _hex(colors[0], p["top"]) if colors and colors[0] != "auto" else p["top"]
    bottom = _hex(colors[1], top) if len(colors) > 1 else (top if colors and colors[0] != "auto" else p["bottom"])
    outline = px("outline", p["outline"])
    if pixel and outline:
        outline = max(1.0, round(outline))
    sy = _num(params.get("shadow"), -1)
    dx, dy = p["shadow"][0] * base, (p["shadow"][1] * base if sy < 0 else sy)
    if pixel:
        dx, dy = (max(1.0, round(dx)) if dx else 0.0), (max(1.0, round(dy)) if dy else 0.0)
    bevel, glow = _num(params.get("bevel"), -1), _num(params.get("glow"), -1)
    track = _num(params.get("tracking"), -1)
    st = Style(top, bottom, outline, _hex(params.get("outline_color"), p["outline_color"]), (dx, dy),
               0.0 if pixel else p["blur"] * base, _hex(params.get("shadow_color"), p["shadow_color"]),
               p["shadow_opacity"], min(1.0, bevel if bevel >= 0 else p["bevel"]),
               min(1.0, glow if glow >= 0 else p["glow"]), _hex(params.get("glow_color"), p["glow_color"]),
               pixel, scale)
    # Glyph quads overlap by the outline width when advances stay TTF-exact; 'auto' spaces them apart instead.
    st.tracking = int(track) if track >= 0 else math.ceil(outline) * scale
    return st


def _shift(a: np.ndarray, dx: int, dy: int) -> np.ndarray:
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    if abs(dx) >= w or abs(dy) >= h:
        return out
    out[max(dy, 0):h + min(dy, 0), max(dx, 0):w + min(dx, 0)] = a[max(-dy, 0):h - max(dy, 0),
                                                                  max(-dx, 0):w - max(dx, 0)]
    return out


def dilate(a: np.ndarray, r: float, hard: bool = False) -> np.ndarray:
    out = a.copy()
    R = math.ceil(r)
    for dy in range(-R, R + 1):
        for dx in range(-R, R + 1):
            d = math.hypot(dx, dy)
            if (dx or dy) and d <= r + 0.5:
                w = 1.0 if hard else min(1.0, r + 0.5 - d)
                if hard and d > r + 0.25:
                    continue
                np.maximum(out, _shift(a, dx, dy) * w, out=out)
    return out


def blur(a: np.ndarray, r: float) -> np.ndarray:
    if r <= 0:
        return a
    img = Image.fromarray(np.clip(np.round(a * 255), 0, 255).astype(np.uint8), "L")
    return np.asarray(img.filter(ImageFilter.GaussianBlur(r)), np.float32) / 255


def _over(top_rgb: np.ndarray, top_a: np.ndarray, bot_rgb: np.ndarray, bot_a: np.ndarray
          ) -> tuple[np.ndarray, np.ndarray]:
    a = top_a + bot_a * (1 - top_a)
    rgb = (top_rgb * top_a[..., None] + bot_rgb * (bot_a * (1 - top_a))[..., None]) / np.maximum(a, 1e-6)[..., None]
    return rgb, a


def stylize(mask: np.ndarray, line_y: np.ndarray, st: Style, span: tuple[float, float],
            texture: np.ndarray | None = None) -> np.ndarray:
    """RGBA uint8 from glyph coverage `mask` and each pixel's y in line space (for one gradient across glyphs)."""
    a = mask.astype(np.float32)
    t = np.clip((line_y - span[0]) / max(1.0, span[1] - span[0]), 0, 1)
    if st.pixel:
        t = np.minimum(np.floor(t * st.bands), st.bands - 1) / max(1, st.bands - 1)
    fill = _rgb(st.top) * (1 - t[..., None]) + _rgb(st.bottom) * t[..., None]
    if texture is not None:
        fill = np.where(texture[..., 3:] > 0, texture[..., :3], fill)
    if st.bevel > 0:
        if st.pixel:
            shade = (a * (1 - _shift(a, 0, 1)) + a * (1 - _shift(a, 1, 0)) * 0.5
                     - a * (1 - _shift(a, 0, -1)) - a * (1 - _shift(a, -1, 0)) * 0.5) * 0.45
        else:
            sigma = max(1.0, (span[1] - span[0]) * 0.06)
            gy, gx = np.gradient(blur(a, sigma))
            shade = (0.8 * gy + 0.6 * gx) * sigma * 2.5
        shade = np.clip(shade, -1, 1)[..., None] * st.bevel
        fill = np.where(shade > 0, fill + (1 - fill) * shade, fill * (1 + shade))
    if st.glow > 0:
        if st.pixel:
            inner = a * (1 - np.minimum.reduce([_shift(a, 1, 0), _shift(a, -1, 0), _shift(a, 0, 1), _shift(a, 0, -1)]))
        else:
            inner = np.clip((1 - blur(a, max(1.0, (span[1] - span[0]) * 0.08))) * 2 - 0.6, 0, 1)
        k = (inner * st.glow)[..., None]
        fill = fill * (1 - k) + _rgb(st.glow_color) * k
    rgb, alpha = np.clip(fill, 0, 1), a
    ring = a
    if st.outline > 0:
        ring = dilate(a, st.outline, hard=st.pixel)
        rgb, alpha = _over(rgb, alpha, np.broadcast_to(_rgb(st.outline_color), rgb.shape), ring)
    if st.shadow_opacity > 0 and (st.shadow != (0.0, 0.0) or st.blur > 0):
        sh = _shift(ring, round(st.shadow[0]), round(st.shadow[1]))
        sh = blur(sh, st.blur) * st.shadow_opacity
        rgb, alpha = _over(rgb, alpha, np.broadcast_to(_rgb(st.shadow_color), rgb.shape), sh)
    out = np.concatenate([rgb, alpha[..., None]], -1)
    return np.clip(np.round(out * 255), 0, 255).astype(np.uint8)


@dataclass
class Glyph:
    ch: str
    adv: int
    mask: np.ndarray | None
    left: int
    top: int


def raster_glyphs(font: Any, chars: str, pixel: bool) -> list[Glyph]:
    """Ink bitmaps placed relative to the pen at the line top (Pillow anchor 'la'), so offsets are TTF bearings."""
    out = []
    for ch in chars:
        adv = round(font.getlength(ch))
        l, t, r, b = font.getbbox(ch, anchor="la")
        if ch.isspace() or r <= l or b <= t:
            out.append(Glyph(ch, adv, None, 0, 0))
            continue
        img = Image.new("L", (r - l, b - t), 0)
        d = ImageDraw.Draw(img)
        d.fontmode = "1" if pixel else "L"
        d.text((-l, -t), ch, font=font, fill=255, anchor="la")
        m = np.asarray(img, np.float32) / 255
        if pixel:
            m = (m >= 0.5).astype(np.float32)
        out.append(Glyph(ch, adv, m, l, t))
    return out


def _pot(n: int) -> int:
    return 1 << max(5, math.ceil(math.log2(max(1, n))))


def pack(sizes: list[tuple[int, int]], spacing: int = 1) -> tuple[int, int, list[tuple[int, int]]]:
    """Shelf packing, tallest first, into the smallest power-of-two page that fits (wide before tall)."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    area = sum((w + spacing) * (h + spacing) for w, h in sizes if w and h)
    widest = max([w for w, _ in sizes] + [1]) + 2 * spacing
    cands = sorted({(w, h) for w in (1 << k for k in range(5, 13)) for h in (w // 2, w)
                    if w >= widest and w * h >= area and h >= 32}, key=lambda s: (s[0] * s[1], -s[0]))
    for W, H in cands:
        pos: list[tuple[int, int]] = [(0, 0)] * len(sizes)
        x = y = spacing
        row = 0
        ok = True
        for i in order:
            w, h = sizes[i]
            if not w or not h:
                continue
            if x + w + spacing > W:
                x, y, row = spacing, y + row + spacing, 0
            if y + h + spacing > H:
                ok = False
                break
            pos[i] = (x, y)
            x += w + spacing
            row = max(row, h)
        if ok:
            return W, H, pos
    raise StepError("op_too_big", f"glyphs don't fit a {MAX_ATLAS}px atlas; lower the size or the charset")


@dataclass
class FontBuild:
    atlas: np.ndarray
    doc: dict[str, Any]
    kerning: dict[tuple[str, str], int] = field(default_factory=dict)


class TextureSheet:
    """A Klein-restyled glyph sheet: colours inside each glyph (shrunk a little against drift), bled outward so the
    TTF mask never samples the white backdrop at its anti-aliased edge."""

    def __init__(self, texture: Image.Image, layout: dict[str, Any], font_path: str):
        self.size = int(layout["size"])
        self.pens = {c: (float(p[0]), float(p[1])) for c, p in layout["pens"].items()}
        w, h = int(layout["width"]), int(layout["height"])
        rgb = np.asarray(texture.convert("RGB").resize((w, h), Image.Resampling.LANCZOS), np.float32) / 255
        mask = np.asarray(render_sheet(layout["chars"], font_path, self.size, self.pens, (w, h),
                                       font_id=str(layout.get("font") or ""))[1], np.float32) / 255
        keep = (mask > 0.5).astype(np.float32)
        for _ in range(max(1, round(self.size / 40))):
            keep = np.minimum.reduce([keep, _shift(keep, 1, 0), _shift(keep, -1, 0), _shift(keep, 0, 1),
                                      _shift(keep, 0, -1)])
        col, wt = rgb * keep[..., None], keep.copy()
        for _ in range(max(4, round(self.size / 10))):
            nb = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)]
            sc = sum(_shift(col, dx, dy) for dx, dy in nb)
            sw = sum(_shift(wt, dx, dy) for dx, dy in nb)
            grow = (wt == 0) & (sw > 0)
            col[grow] = sc[grow] / sw[grow][..., None]
            wt[grow] = 1
        # Edge-padded so a patch box reaching past the sheet border (glyph padding) stays inside the image.
        self.margin = self.size
        col = np.pad(col, ((self.margin, self.margin), (self.margin, self.margin), (0, 0)), mode="edge")
        self.img = Image.fromarray(np.clip(np.round(col * 255), 0, 255).astype(np.uint8), "RGB")

    def patch(self, ch: str, pen: tuple[float, float], w: int, h: int, base: int) -> np.ndarray | None:
        if ch not in self.pens:
            return None
        k = self.size / base
        px, py = self.pens[ch][0] + self.margin, self.pens[ch][1] + self.margin
        box = (px - pen[0] * k, py - pen[1] * k, px + (w - pen[0]) * k, py + (h - pen[1]) * k)
        return np.asarray(self.img.resize((w, h), Image.Resampling.BICUBIC, box=box), np.float32) / 255


def _font(font_id: str, font_path: str, size: int) -> Any:
    return load_font(font_id, size) if font_id in available_fonts() else ImageFont.truetype(font_path, size)


def build_font(chars: str, font_id: str, font_path: str, size: int, st: Style, name: str = "font",
               texture: TextureSheet | None = None) -> FontBuild:
    base = max(6, round(size / st.scale))
    font = _font(font_id, font_path, base)
    ascent, descent = font.getmetrics()
    glyphs = raster_glyphs(font, chars, st.pixel)
    pl, pt, pr, pb = st.pads()
    sizes = [((g.mask.shape[1] + pl + pr), (g.mask.shape[0] + pt + pb)) if g.mask is not None else (0, 0)
             for g in glyphs]
    W, H, pos = pack(sizes, 1 if st.pixel else 2)
    mask = np.zeros((H, W), np.float32)
    line_y = np.zeros((H, W), np.float32)
    tex = np.zeros((H, W, 4), np.float32) if texture is not None else None
    for g, (x, y), (w, h) in zip(glyphs, pos, sizes):
        if g.mask is None:
            continue
        mh, mw = g.mask.shape
        mask[y + pt:y + pt + mh, x + pl:x + pl + mw] = g.mask
        line_y[y:y + h, x:x + w] = (np.arange(h, dtype=np.float32) + g.top - pt)[:, None]
        if tex is not None and texture is not None:
            p = texture.patch(g.ch, (pl - g.left, pt - g.top), w, h, base)
            if p is not None:
                tex[y:y + h, x:x + w, :3], tex[y:y + h, x:x + w, 3] = p, 1
    cap = font.getbbox("H" if "H" in chars else (chars.strip() or "0")[0], anchor="la")[1]
    atlas = stylize(mask, line_y, st, (float(cap), float(ascent)), tex)
    k = st.scale
    if k > 1:
        atlas = np.repeat(np.repeat(atlas, k, 0), k, 1)
        W2, H2 = _pot(W * k), _pot(H * k)
        if (W2, H2) != atlas.shape[1::-1]:
            atlas = np.pad(atlas, ((0, H2 - atlas.shape[0]), (0, W2 - atlas.shape[1]), (0, 0)))
    kern = {p: v * k for p, v in kerning(font_path, chars, base).items()}
    entries = []
    for g, (x, y), (w, h) in zip(glyphs, pos, sizes):
        empty = g.mask is None
        entries.append({"id": ord(g.ch), "char": g.ch, "x": x * k, "y": y * k, "width": w * k, "height": h * k,
                        "xoffset": 0 if empty else (g.left - pl) * k, "yoffset": 0 if empty else (g.top - pt) * k,
                        "xadvance": g.adv * k + st.tracking})
    doc = {"type": FONT_TYPE, "name": name, "face": _face(font, font_id), "font_file": Path(font_path).name,
           "size": base * k, "line_height": (ascent + descent) * k, "base": ascent * k,
           "scale_w": int(atlas.shape[1]), "scale_h": int(atlas.shape[0]), "pixel": st.pixel, "pixel_scale": k,
           "padding": [pl * k, pt * k, pr * k, pb * k], "tracking": st.tracking,
           "style": {key: getattr(st, key) for key in ("top", "bottom", "outline", "outline_color", "shadow", "blur",
                                                     "shadow_color", "shadow_opacity", "bevel", "glow", "glow_color")},
           "textured": texture is not None, "chars": entries,
           "kernings": [[ord(a), ord(b), v] for (a, b), v in sorted(kern.items())]}
    return FontBuild(atlas, doc, kern)


def _face(font: Any, font_id: str) -> str:
    try:
        fam, sty = font.getname()
        return f"{fam} {sty}".strip() if sty and sty.lower() != "regular" else str(fam)
    except (AttributeError, OSError):
        return font_id


def _q(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def fnt_text(doc: dict[str, Any], page_file: str) -> str:
    """AngelCode BMFont text format; offsets already include the effect padding, so `padding` stays 0."""
    smooth = 0 if doc.get("pixel") else 1
    lines = [f"info face={_q(str(doc['face']))} size={doc['size']} bold=0 italic=0 charset=\"\" unicode=1 "
             f"stretchH=100 smooth={smooth} aa=1 padding=0,0,0,0 spacing=1,1 outline=0",
             f"common lineHeight={doc['line_height']} base={doc['base']} scaleW={doc['scale_w']} "
             f"scaleH={doc['scale_h']} pages=1 packed=0 alphaChnl=0 redChnl=0 greenChnl=0 blueChnl=0",
             f"page id=0 file={_q(page_file)}",
             f"chars count={len(doc['chars'])}"]
    lines += [f"char id={c['id']} x={c['x']} y={c['y']} width={c['width']} height={c['height']} "
              f"xoffset={c['xoffset']} yoffset={c['yoffset']} xadvance={c['xadvance']} page=0 chnl=15"
              for c in doc["chars"]]
    if doc.get("kernings"):
        lines.append(f"kernings count={len(doc['kernings'])}")
        lines += [f"kerning first={a} second={b} amount={v}" for a, b, v in doc["kernings"]]
    return "\n".join(lines) + "\n"


_PAIR = re.compile(r'(\w+)=("[^"]*"|\S+)')


def parse_fnt(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"chars": {}, "kernings": {}, "pages": {}}
    for line in text.splitlines():
        tag, _, rest = line.strip().partition(" ")
        kv = {k: (v[1:-1] if v.startswith('"') else int(v) if re.fullmatch(r"-?\d+", v) else v)
              for k, v in _PAIR.findall(rest)}
        if tag == "char":
            out["chars"][kv["id"]] = kv
        elif tag == "kerning":
            out["kernings"][(kv["first"], kv["second"])] = kv["amount"]
        elif tag == "page":
            out["pages"][kv["id"]] = kv["file"]
        elif tag in ("info", "common"):
            out[tag] = kv
    return out


def render_text(fnt: dict[str, Any], atlas: Image.Image, text: str) -> Image.Image:
    """Lays `text` out exactly as an engine reading the .fnt would (offsets, advances, kerning)."""
    lh = int(fnt["common"]["lineHeight"])
    chars, kern = fnt["chars"], fnt["kernings"]
    pen, prev, items = 0, None, []
    for ch in text:
        g = chars.get(ord(ch))
        if g is None:
            continue
        if prev is not None:
            pen += kern.get((prev, ord(ch)), 0)
        if g["width"] and g["height"]:
            items.append((pen + g["xoffset"], g["yoffset"], g))
        pen += g["xadvance"]
        prev = ord(ch)
    x0 = min([0] + [x for x, _, _ in items])
    y0 = min([0] + [y for _, y, _ in items])
    w = max([pen] + [x + g["width"] for x, _, g in items]) - x0
    h = max([lh] + [y + g["height"] for _, y, g in items]) - y0
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    for x, y, g in items:
        crop = atlas.crop((g["x"], g["y"], g["x"] + g["width"], g["y"] + g["height"]))
        img.alpha_composite(crop, (x - x0, y - y0))
    return img


def preview(fnt: dict[str, Any], atlas: Image.Image, texts: list[str], pixel: bool) -> Image.Image:
    lines = [render_text(fnt, atlas, t) for t in texts if t.strip()]
    lines = [im for im in lines if im.width > 4]
    gap = max(8, int(fnt["common"]["lineHeight"]) // 4)
    max_w = 1600
    resample = Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS
    lines = [im if im.width <= max_w else im.resize((max_w, max(1, im.height * max_w // im.width)), resample)
             for im in lines]
    thumb = atlas.copy()
    thumb.thumbnail((360, 360), resample)
    W = max([thumb.width] + [im.width for im in lines]) + 2 * gap
    H = sum(im.height + gap for im in lines) + thumb.height + 3 * gap
    out = Image.new("RGBA", (W, H), (34, 38, 48, 255))
    y = gap
    for im in lines:
        out.alpha_composite(im, (gap, y))
        y += im.height + gap
    checker = Image.new("RGBA", thumb.size, (70, 74, 84, 255))
    cd = ImageDraw.Draw(checker)
    for cy in range(0, thumb.height, 12):
        for cx in range((cy // 12) % 2 * 12, thumb.width, 24):
            cd.rectangle((cx, cy, cx + 11, cy + 11), fill=(56, 60, 70, 255))
    checker.alpha_composite(thumb)
    out.alpha_composite(checker, (gap, y + gap))
    return out


def sample_texts(chars: str, extra: str = "") -> list[str]:
    """Samples the charset fully covers (a half-dropped word reads like a bug), plus the charset itself if few."""
    have = set(chars)
    out = [t for t in dict.fromkeys(unicodedata.normalize("NFC", t).strip()
                                    for t in ([extra] if extra.strip() else []) + list(SAMPLES))
           if t and all(c in have for c in t)]
    if len(out) < 2:
        body = "".join(c for c in chars if not c.isspace())
        out += [body[i:i + 32] for i in range(0, min(len(body), 128), 32)]
    return out


def sheet_layout(chars: str, font_path: str, font_id: str) -> dict[str, Any]:
    """Grid of glyphs for the Klein pass, as large as fits a ~1024 px square; pens are each glyph's 'la' origin."""
    n = len(chars)

    def grid(size: int) -> tuple[int, int, int, int]:
        font = _font(font_id, font_path, size)
        asc, desc = font.getmetrics()
        cw = max(max(font.getbbox(c, anchor="la")[2], round(font.getlength(c))) for c in chars) + size // 3
        ch = asc + desc + size // 3
        cols = max(1, min(n, math.ceil(math.sqrt(n * ch / cw))))
        return cw, ch, cols, math.ceil(n / cols)

    cw, ch, cols, rows = grid(100)
    size = min(256, int(100 * min(SHEET_SIDE / (cols * cw), SHEET_SIDE / (rows * ch))) + 4)
    while size > 12:
        cw, ch, cols, rows = grid(size)
        if cols * cw <= SHEET_SIDE and rows * ch <= SHEET_SIDE:
            break
        size -= 2
    pens = {c: [i % cols * cw + size // 6, i // cols * ch + size // 6] for i, c in enumerate(chars)}
    w, h = -(-cols * cw // 16) * 16, -(-rows * ch // 16) * 16
    return {"type": SHEET_TYPE, "chars": chars, "size": size, "width": w, "height": h, "pens": pens,
            "font": font_id, "font_file": Path(font_path).name}


def render_sheet(chars: str, font_path: str, size: int, pens: dict[str, Any], wh: tuple[int, int],
                 st: Style | None = None, font_id: str = "") -> tuple[Image.Image, Image.Image]:
    """(Klein input: glyphs in the style's base colour on white, glyph coverage mask)."""
    font = _font(font_id, font_path, size)
    mask = Image.new("L", wh, 0)
    d = ImageDraw.Draw(mask)
    for c in chars:
        if not c.isspace() and c in pens:
            d.text(tuple(pens[c]), c, font=font, fill=255, anchor="la")
    if st is None:
        return mask.convert("RGB"), mask
    a = np.asarray(mask, np.float32)[..., None] / 255
    col = (_rgb(st.top) + _rgb(st.bottom)) / 2
    rgb = np.ones((wh[1], wh[0], 3), np.float32) * (1 - a) + col * a
    return Image.fromarray(np.clip(np.round(rgb * 255), 0, 255).astype(np.uint8), "RGB"), mask


def _setup(params: dict[str, Any]) -> tuple[str, str, str, int, Style]:
    chars = charset(str(params.get("charset") or "ascii"), str(params.get("custom") or ""))
    size = int(_num(params.get("size"), 48))
    if not 8 <= size <= 256:
        raise StepError("op_bad_params", "size must be 8..256 px")
    st = resolve_style(params, size)
    fid, path = resolve_font(str(params.get("font") or "auto"), str(params.get("preset") or "gold"), chars)
    return chars, fid, path, size, st


def _stem(params: dict[str, Any]) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(params.get("name") or "game_font")).strip("_")[:40] or "game_font"


class FontSheet(OpExecutor):
    kind = "op.font_sheet"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        chars, fid, path, _, st = _setup(params)
        lay = await asyncio.to_thread(sheet_layout, chars, path, fid)
        img, _ = await asyncio.to_thread(render_sheet, chars, path, lay["size"], lay["pens"],
                                         (lay["width"], lay["height"]), st, fid)
        ref = await _put_png(ctx, img, f"{_stem(params)}_glyph_sheet.png")
        lj = await ctx.blobs.put_bytes(json.dumps(lay, ensure_ascii=False).encode(), name=SHEET_FILE,
                                       mime="application/json")
        return StepResult(outputs={"images": [ref], "sheet": [lj]},
                          meta={"glyphs": len(chars), "font": fid, "sheet_size": lay["size"]})


class BitmapFont(OpExecutor):
    kind = "op.bitmap_font"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        chars, fid, path, size, st = _setup(params)
        texture = None
        if inputs.get("texture"):
            lay = await _read_json(ctx, inputs, "sheet")
            if not isinstance(lay, dict) or lay.get("type") != SHEET_TYPE or lay.get("chars") != chars:
                raise StepError("op_bad_input", "glyph sheet layout doesn't match this charset (same params needed)")
            if lay.get("font_file") != Path(path).name:
                raise StepError("op_bad_input", f"glyph sheet used font {lay.get('font_file')}, this step "
                                                f"{Path(path).name}")
            tex_img = await _load(ctx, inputs["texture"][0])
            texture = await asyncio.to_thread(TextureSheet, tex_img, lay, path)
        name = _stem(params)
        ctx.progress(0, 3, label="glyphs")
        fb = await asyncio.to_thread(build_font, chars, fid, path, size, st, name, texture)
        atlas = Image.fromarray(fb.atlas, "RGBA")
        a_ref = await _put_png(ctx, atlas, f"{name}.png")
        text = fnt_text(fb.doc, f"{name}.png")
        f_ref = await ctx.blobs.put_bytes(text.encode(), name=f"{name}.fnt", mime="text/plain")
        ctx.progress(2, 3, label="preview")
        prev = await asyncio.to_thread(preview, parse_fnt(text), atlas,
                                       sample_texts(chars, str(params.get("sample") or "")), st.pixel)
        p_ref = await _put_png(ctx, prev, f"{name}_preview.png")
        doc = {**fb.doc, "atlas": {"file": a_ref.name, "hash": a_ref.hash}, "fnt": {"file": f_ref.name,
                                                                                    "hash": f_ref.hash}}
        j_ref = await ctx.blobs.put_bytes(json.dumps(doc, ensure_ascii=False, indent=1).encode(), name=FONT_FILE,
                                          mime="application/json")
        ctx.progress(3, 3)
        return StepResult(outputs={"images": [a_ref], "fnt": [f_ref], "font": [j_ref], "preview": [p_ref]},
                          meta={"glyphs": len(chars), "font": fid, "atlas": [doc["scale_w"], doc["scale_h"]],
                                "kerning_pairs": len(fb.kerning), "pixel": st.pixel, "textured": texture is not None})


BITMAP_FONT_OPS: tuple[type[OpExecutor], ...] = (FontSheet, BitmapFont)
