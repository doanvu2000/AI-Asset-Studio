"""Store capsules from one key art: title fonts found on this machine, the title layout (from Claude or defaults),
crops that keep the focus box clear of the title, and the title rendered with Pillow."""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Steamworks graphical assets (2024 sizes): (name, width, height, has title).
STEAM_CAPSULES: tuple[tuple[str, int, int, bool], ...] = (
    ("header_capsule", 920, 430, True),
    ("small_capsule", 462, 174, True),
    ("main_capsule", 1232, 706, True),
    ("vertical_capsule", 748, 896, True),
    ("library_capsule", 600, 900, True),
    ("library_hero", 3840, 1240, False),
)
LIBRARY_LOGO = (1280, 720)
WIDE_ANCHORS = ("top", "bottom", "left", "right")
TALL_ANCHORS = ("top", "bottom")
CASES = ("upper", "title", "as_is")

# id -> (style note for Claude, file names tried in the font folders, covers Vietnamese)
FONT_FILES: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "impact": ("condensed heavy poster sans, action/arcade", ("impact.ttf", "Impact.ttf"), False),
    "arial_black": ("very heavy geometric sans, bold casual", ("ariblk.ttf", "Arial Black.ttf"), False),
    "segoe_black": ("heavy modern sans, clean", ("seguibl.ttf",), True),
    "segoe_bold": ("bold modern sans, clean UI-like", ("segoeuib.ttf",), True),
    "arial_bold": ("bold neutral sans", ("arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"), True),
    "verdana_bold": ("wide bold sans, friendly", ("verdanab.ttf", "Verdana Bold.ttf"), True),
    "tahoma_bold": ("compact bold sans", ("tahomabd.ttf", "Tahoma Bold.ttf"), True),
    "trebuchet_bold": ("bold humanist sans, playful", ("trebucbd.ttf", "Trebuchet MS Bold.ttf"), True),
    "bahnschrift": ("industrial DIN-like sans, sci-fi/tech", ("bahnschrift.ttf",), True),
    "comic_bold": ("rounded cartoon hand lettering, kids/casual", ("comicbd.ttf",), True),
    "georgia_bold": ("bold old-style serif, storybook", ("georgiab.ttf", "Georgia Bold.ttf"), False),
    "cambria_bold": ("bold transitional serif, epic/fantasy", ("cambriab.ttf",), True),
    "times_bold": ("bold classic serif, historic", ("timesbd.ttf", "Times New Roman Bold.ttf",
                                                    "LiberationSerif-Bold.ttf"), True),
    "dejavu_bold": ("bold neutral sans", ("DejaVuSans-Bold.ttf",), True),
}


def font_dirs() -> list[Path]:
    dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    dirs += [Path(p) for p in ("/Library/Fonts", "/System/Library/Fonts/Supplemental", "/System/Library/Fonts",
                               "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation",
                               "/usr/share/fonts/TTF", "/usr/share/fonts/dejavu")]
    dirs.append(Path.home() / "Library" / "Fonts")
    return dirs


@functools.cache
def available_fonts() -> dict[str, str]:
    """Font id -> file path, for the ids whose file exists here."""
    found: dict[str, str] = {}
    dirs = [d for d in font_dirs() if d.is_dir()]
    for fid, (_, names, _) in FONT_FILES.items():
        for d in dirs:
            hit = next((d / n for n in names if (d / n).is_file()), None)
            if hit:
                found[fid] = str(hit)
                break
    return found


def needs_vietnamese(text: str) -> bool:
    return any(ord(c) > 0xFF for c in text)


def font_choices(title: str) -> list[str]:
    fonts = available_fonts()
    vi = needs_vietnamese(title)
    return [f for f in FONT_FILES if f in fonts and (FONT_FILES[f][2] or not vi)]


def load_font(fid: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = available_fonts().get(fid or "") or (fid if fid and Path(fid).is_file() else None)
    if path is None:
        return ImageFont.load_default(size)
    font = ImageFont.truetype(path, size)
    if Path(path).name.lower() == "bahnschrift.ttf":
        try:  # variable font; its default instance is Regular
            font.set_variation_by_name("Bold")
        except (OSError, ValueError):
            pass
    return font


def _hex(v: Any, default: str) -> str:
    s = str(v or "").strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", s):
        return "#" + s.lstrip("#").lower()
    if re.fullmatch(r"#?[0-9a-fA-F]{3}", s):
        return "#" + "".join(c * 2 for c in s.lstrip("#").lower())
    return default


def _num(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return default if x != x else min(hi, max(lo, x))


def normalize_focus(raw: Any) -> list[float] | None:
    if isinstance(raw, dict):
        raw = [raw.get(k) for k in ("x0", "y0", "x1", "y1")]
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in raw)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 0.03 or y1 - y0 < 0.03:
        return None
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def normalize_layout(raw: Any, title: str, focus: list[float] | None = None) -> dict[str, Any]:
    """Model reply (or nothing) -> the layout op.store_kit renders; every field clamped and defaulted."""
    r = raw if isinstance(raw, dict) else {}
    choices = font_choices(title)
    font = str(r.get("font") or "")
    if font not in choices:
        font = choices[0] if choices else ""
    wide = str(r.get("wide_anchor") or "")
    tall = str(r.get("tall_anchor") or "")
    case = str(r.get("case") or "")
    return {
        "type": "store_layout",
        "title": title,
        "focus": normalize_focus(r.get("focus")) or focus,
        "font": font,
        "case": case if case in CASES else "as_is",
        "fill": _hex(r.get("fill"), "#ffffff"),
        "outline": _hex(r.get("outline"), "#1a1020"),
        "outline_width": _num(r.get("outline_width"), 0, 0.15, 0.06),
        "shadow": bool(r.get("shadow", True)),
        "plate": bool(r.get("plate", False)),
        "wide_anchor": wide if wide in WIDE_ANCHORS else "top",
        "wide_size": _num(r.get("wide_size"), 0.3, 0.9, 0.6),
        "tall_anchor": tall if tall in TALL_ANCHORS else "top",
        "tall_size": _num(r.get("tall_size"), 0.5, 0.95, 0.85),
        "reason": str(r.get("reason") or "").strip(),
    }


def detect_focus(img: Image.Image) -> list[float]:
    """CPU fallback for the focus box: detail and colour that stand out from the frame's mean, weighted to the centre,
    and the box holding the strongest ~70% of it."""
    small = img.convert("RGB").resize((192, max(1, round(192 * img.height / img.width))), Image.Resampling.BOX)
    a = np.asarray(small, np.float32) / 255
    h, w = a.shape[:2]
    gray = a.mean(2)
    gy, gx = np.gradient(np.asarray(Image.fromarray((gray * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(1.5)), np.float32) / 255)
    edge = np.asarray(Image.fromarray(np.clip(np.hypot(gx, gy) * 1020, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(4)), np.float32) / 255
    color = np.abs(a - a.reshape(-1, 3).mean(0)).sum(2)
    yy, xx = np.mgrid[0:h, 0:w]
    prior = np.exp(-(((xx / w - 0.5) / 0.35) ** 2 + ((yy / h - 0.5) / 0.4) ** 2))
    s = (edge / (edge.max() + 1e-6) + color / (color.max() + 1e-6)) * prior
    flat = s.ravel()
    thr = np.sort(flat)[::-1][np.searchsorted(np.cumsum(np.sort(flat)[::-1]), 0.7 * flat.sum())]
    ys, xs = np.nonzero(s >= thr)

    def span(v: np.ndarray, n: int) -> tuple[float, float]:
        lo, hi = np.percentile(v, (8, 92))
        return float(lo) / n, float(hi + 1) / n

    x0, x1 = span(xs, w)
    y0, y1 = span(ys, h)
    return normalize_focus([x0, y0, x1, y1]) or [0.3, 0.25, 0.7, 0.75]


def cased(title: str, case: str) -> str:
    if case == "upper":
        return title.upper()
    if case == "title":
        return " ".join(w[:1].upper() + w[1:] for w in title.split(" "))
    return title


def line_options(text: str) -> list[list[str]]:
    words = text.split()
    opts = [[text.strip()]]
    if len(words) >= 2:
        best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
        opts.append([" ".join(words[:best]), " ".join(words[best:])])
    return opts


def _block_size(lines: list[str], font: Any, stroke: int, spacing: float) -> tuple[int, int, list[tuple[int, int, int]]]:
    """(width, height, per line (x offset, top offset, width)) of the lines stacked with `spacing` x font size."""
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    boxes = [probe.textbbox((0, 0), ln, font=font, stroke_width=stroke) for ln in lines]
    asc, desc = font.getmetrics() if hasattr(font, "getmetrics") else (font.size, 0)
    step = round((asc + desc) * spacing)
    top = min(i * step + b[1] for i, b in enumerate(boxes))
    bottom = max(i * step + b[3] for i, b in enumerate(boxes))
    width = max(b[2] - b[0] for b in boxes)
    return width, bottom - top, [(b[0], i * step - top, b[2] - b[0]) for i, b in enumerate(boxes)]


def fit_text(lines: list[str], fid: str | None, box_w: int, box_h: int, outline: float
             ) -> tuple[int, Any, int, tuple[int, int, list[tuple[int, int, int]]]]:
    """Largest font size whose stacked lines (with outline) fit box_w x box_h: (size, font, stroke, block)."""
    lo, hi, best = 6, max(8, box_h * 2), None
    while lo <= hi:
        mid = (lo + hi) // 2
        font = load_font(fid, mid)
        stroke = round(outline * mid)
        block = _block_size(lines, font, stroke, 1.02)
        if block[0] <= box_w and block[1] <= box_h:
            best, lo = (mid, font, stroke, block), mid + 1
        else:
            hi = mid - 1
    if best is None:
        font = load_font(fid, 6)
        best = (6, font, 0, _block_size(lines, font, 0, 1.02))
    return best


def title_region(w: int, h: int, anchor: str, size: float) -> tuple[int, int, int, int]:
    m = round(0.05 * min(w, h))
    bw = round(size * w)
    if anchor in ("left", "right"):
        bh = round(0.62 * h)
        x0 = m if anchor == "left" else w - m - bw
        return x0, (h - bh) // 2, x0 + bw, (h + bh) // 2
    if anchor == "center":
        bh = round(0.6 * h)
        return (w - bw) // 2, (h - bh) // 2, (w + bw) // 2, (h + bh) // 2
    bh = round((0.3 if w >= h else 0.26) * h)
    y0 = m if anchor == "top" else h - m - bh
    return (w - bw) // 2, y0, (w + bw) // 2, y0 + bh


# Where the focus centre should land in the crop so the title has room on the other side.
_FOCUS_TARGET = {"left": (0.66, 0.5), "right": (0.34, 0.5), "top": (0.5, 0.6), "bottom": (0.5, 0.42),
                 "center": (0.5, 0.5), None: (0.5, 0.5)}


def crop_box(size: tuple[int, int], focus: list[float], aspect: float, anchor: str | None
             ) -> tuple[tuple[int, int, int, int], float]:
    """Largest crop of `aspect` placed so the focus sits opposite the title; (box, share of the focus kept)."""
    W, H = size
    cw, ch = (H * aspect, H) if W / H > aspect else (W, W / aspect)
    fx0, fy0, fx1, fy1 = focus[0] * W, focus[1] * H, focus[2] * W, focus[3] * H
    tx, ty = _FOCUS_TARGET.get(anchor, (0.5, 0.5))
    x0 = min(max((fx0 + fx1) / 2 - tx * cw, 0), W - cw)
    y0 = min(max((fy0 + fy1) / 2 - ty * ch, 0), H - ch)
    # A focus wider/taller than the crop keeps its middle rather than one side.
    if fx1 - fx0 > cw:
        x0 = min(max((fx0 + fx1 - cw) / 2, 0), W - cw)
    if fy1 - fy0 > ch:
        y0 = min(max((fy0 + fy1 - ch) / 2, 0), H - ch)
    box = (round(x0), round(y0), round(x0 + cw), round(y0 + ch))
    ix = max(0.0, min(box[2], fx1) - max(box[0], fx0))
    iy = max(0.0, min(box[3], fy1) - max(box[1], fy0))
    kept = ix * iy / max((fx1 - fx0) * (fy1 - fy0), 1e-6)
    return box, round(kept, 4)


def _rgba(hex_: str, a: int = 255) -> tuple[int, int, int, int]:
    return int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16), a


def _plate(size: tuple[int, int], box: tuple[int, int, int, int], anchor: str) -> Image.Image:
    """Soft dark gradient that fades from the title's side of the frame toward the art."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if anchor == "top":
        t = 1 - yy / max(box[3] + 0.15 * h, 1)
    elif anchor == "bottom":
        t = (yy - (box[1] - 0.15 * h)) / max(h - box[1] + 0.15 * h, 1)
    elif anchor == "left":
        t = 1 - xx / max(box[2] + 0.12 * w, 1)
    elif anchor == "right":
        t = (xx - (box[0] - 0.12 * w)) / max(w - box[0] + 0.12 * w, 1)
    else:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        t = 1 - np.sqrt(((xx - cx) / max(box[2] - box[0], 1)) ** 2 + ((yy - cy) / max(box[3] - box[1], 1)) ** 2)
    a = (np.clip(t, 0, 1) ** 1.4 * 0.6 * 255).astype(np.uint8)
    return Image.fromarray(np.dstack([np.zeros((h, w, 3), np.uint8), a]), "RGBA")


def render_title(size: tuple[int, int], text: str, layout: dict[str, Any], region: tuple[int, int, int, int],
                 align: str = "center", plate_anchor: str | None = None) -> tuple[Image.Image, dict[str, Any]]:
    """Transparent layer with the title fitted into `region`; info has the drawn box and font size."""
    rw, rh = region[2] - region[0], region[3] - region[1]
    fid = layout.get("font") or None
    best = None
    for lines in line_options(text):
        cand = fit_text(lines, fid, rw, rh, float(layout["outline_width"]))
        if best is None or cand[0] > best[1][0] * 1.08:
            best = (lines, cand)
    lines, (px, font, stroke, (bw, bh, rows)) = best
    x0 = region[0] if align == "left" else region[2] - bw if align == "right" else region[0] + (rw - bw) // 2
    y0 = region[1] + (rh - bh) // 2
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    if plate_anchor:
        layer = _plate(size, (x0, y0, x0 + bw, y0 + bh), plate_anchor)

    def draw_text(target: Image.Image, fill: tuple[int, ...], stroke_fill: tuple[int, ...] | None, dx: int = 0,
                  dy: int = 0) -> None:
        d = ImageDraw.Draw(target)
        for ln, (lx, ly, lw) in zip(lines, rows):
            x = x0 + (0 if align == "left" else bw - lw if align == "right" else (bw - lw) // 2) - lx
            d.text((x + dx, y0 + ly + dy), ln, font=font, fill=fill, stroke_width=stroke if stroke_fill else 0,
                   stroke_fill=stroke_fill)

    if layout.get("shadow"):
        sh = Image.new("RGBA", size, (0, 0, 0, 0))
        off = max(1, round(px * 0.05))
        draw_text(sh, (0, 0, 0, 170), (0, 0, 0, 170) if stroke else None, off, off)
        layer.alpha_composite(sh.filter(ImageFilter.GaussianBlur(max(1, px * 0.05))))
    text_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_text(text_layer, _rgba(layout["fill"]), _rgba(layout["outline"]) if stroke else None)
    layer.alpha_composite(text_layer)
    return layer, {"box": [x0, y0, x0 + bw, y0 + bh], "font_px": px, "lines": len(lines)}


def _overlap(a: list[int] | tuple[int, ...], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy / max((a[2] - a[0]) * (a[3] - a[1]), 1)


def capsule(art: Image.Image, layout: dict[str, Any], name: str, w: int, h: int, with_title: bool
            ) -> tuple[Image.Image, dict[str, Any]]:
    """One store capsule: crop-aware resize of the art, then the title (small capsules are mostly logo, Steam asks
    that it fill nearly the whole frame)."""
    focus = layout.get("focus") or [0.3, 0.25, 0.7, 0.75]
    aspect = w / h
    small = aspect > 2.3 and w < 600
    if not with_title:
        anchor, size_frac = None, 0.0
    elif small:
        anchor, size_frac = "center", 0.88
    elif aspect >= 1.2:
        anchor, size_frac = layout["wide_anchor"], layout["wide_size"]
    else:
        anchor, size_frac = layout["tall_anchor"], layout["tall_size"]
    box, kept = crop_box(art.size, focus, aspect, anchor)
    out = art.crop(box).resize((w, h), Image.Resampling.LANCZOS).convert("RGBA")
    info: dict[str, Any] = {"name": name, "w": w, "h": h, "crop": list(box), "focus_kept": kept, "title": with_title}
    if with_title and anchor:
        text = cased(layout["title"], layout["case"])
        region = title_region(w, h, anchor, size_frac)
        align = anchor if anchor in ("left", "right") else "center"
        layer, t = render_title((w, h), text, layout, region, align,
                                anchor if (layout.get("plate") or small) else None)
        out.alpha_composite(layer)
        sx, sy = w / (box[2] - box[0]), h / (box[3] - box[1])
        fpx = [(focus[0] * art.width - box[0]) * sx, (focus[1] * art.height - box[1]) * sy,
               (focus[2] * art.width - box[0]) * sx, (focus[3] * art.height - box[1]) * sy]
        info.update(anchor=anchor, text_box=t["box"], font_px=t["font_px"], lines=t["lines"],
                    text_over_focus=round(_overlap(t["box"], fpx), 3),
                    text_height=round((t["box"][3] - t["box"][1]) / h, 3))
    return out.convert("RGB"), info


def library_logo(layout: dict[str, Any]) -> Image.Image:
    """Title alone on transparency, trimmed and fitted into Steam's 1280x720 logo box."""
    lw, lh = LIBRARY_LOGO
    text = cased(layout["title"], layout["case"])
    layer, _ = render_title((lw, lh), text, {**layout, "plate": False}, (16, 16, lw - 16, lh - 16))
    bbox = layer.getbbox() or (0, 0, lw, lh)
    trimmed = layer.crop(bbox)
    s = min(lw / trimmed.width, lh / trimmed.height, 1.0)
    return trimmed if s >= 1 else trimmed.resize((round(trimmed.width * s), round(trimmed.height * s)),
                                                 Image.Resampling.LANCZOS)
