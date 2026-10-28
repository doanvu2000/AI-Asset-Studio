"""Image text localization: `op.localize_prep` (refines Claude's text boxes into ink masks, cleans flat backgrounds on
the CPU and cuts Klein inpaint crops for textured ones), `op.localize_render` (pastes Klein fills back inside the
masks only, renders each language with a local font that covers its script, auto-fit to the container, writes
<asset>_<lang>.png + localize.json + a contact sheet)."""
from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..contracts import Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json
from .fx import dilate

LOCALIZE_FILE = "localize.json"
LOCALIZE_TYPE = "asset-studio/image-localize"
LAYOUT_FILE = "localize_layout.json"
KLEIN_MIN, KLEIN_MAX, KLEIN_STEP = 512, 1024, 16
FLAT_RESIDUAL = 6.0
MIN_PX = 6

FONT_FILES: dict[str, dict[str, list[str]]] = {
    "latn": {"bold": ["segoeuib.ttf", "arialbd.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf", "tahomabd.ttf"],
             "regular": ["segoeui.ttf", "arial.ttf", "NotoSans-Regular.ttf", "DejaVuSans.ttf", "tahoma.ttf"]},
    "jpan": {"bold": ["YuGothB.ttc", "meiryob.ttc", "NotoSansJP-Bold.ttf", "NotoSansCJK-Bold.ttc", "msgothic.ttc"],
             "regular": ["YuGothM.ttc", "YuGothR.ttc", "meiryo.ttc", "NotoSansJP-Regular.ttf",
                         "NotoSansCJK-Regular.ttc", "msgothic.ttc"]},
    "hans": {"bold": ["msyhbd.ttc", "simhei.ttf", "NotoSansSC-Bold.ttf", "NotoSansCJK-Bold.ttc"],
             "regular": ["msyh.ttc", "simhei.ttf", "simsun.ttc", "NotoSansSC-Regular.ttf", "NotoSansCJK-Regular.ttc"]},
    "hant": {"bold": ["msjhbd.ttc", "NotoSansTC-Bold.ttf", "NotoSansCJK-Bold.ttc", "msyhbd.ttc"],
             "regular": ["msjh.ttc", "mingliu.ttc", "NotoSansTC-Regular.ttf", "NotoSansCJK-Regular.ttc", "msyh.ttc"]},
    "kore": {"bold": ["malgunbd.ttf", "NotoSansKR-Bold.ttf", "NotoSansCJK-Bold.ttc"],
             "regular": ["malgun.ttf", "NotoSansKR-Regular.ttf", "NotoSansCJK-Regular.ttc"]},
    "thai": {"bold": ["LeelaUIb.ttf", "LEELAWDB.TTF", "NotoSansThai-Bold.ttf", "tahomabd.ttf"],
             "regular": ["LeelawUI.ttf", "LEELAWAD.TTF", "NotoSansThai-Regular.ttf", "tahoma.ttf"]},
}
LATIN_EXTRA = {("black", "sans"): ["seguibl.ttf", "ariblk.ttf"], ("bold", "serif"): ["georgiab.ttf", "timesbd.ttf"],
               ("regular", "serif"): ["georgia.ttf", "times.ttf"], ("black", "serif"): ["georgiab.ttf"]}


def font_dirs(extra: Any = None) -> list[Path]:
    dirs = [Path(p) for p in re.split(r"[;\n]", str(extra or "")) if p.strip()]
    if sys.platform == "win32":
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
        if os.environ.get("LOCALAPPDATA"):
            dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs += [Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]
    return dirs


@functools.lru_cache(maxsize=8)
def _index(dirs: tuple[Path, ...]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        # The Windows font folder is flat; Linux keeps fonts in per-family subfolders.
        files = d.glob("*") if sys.platform == "win32" else d.rglob("*")
        for f in files:
            if f.suffix.lower() in (".ttf", ".ttc", ".otf"):
                found.setdefault(f.name.lower(), f)
    return found


def script_of(lang: str, text: str) -> str:
    if re.search(r"[\uac00-\ud7af\u1100-\u11ff]", text):
        return "kore"
    if re.search(r"[\u3040-\u30ff]", text):
        return "jpan"
    if re.search(r"[\u0e00-\u0e7f]", text):
        return "thai"
    lang = lang.lower()
    if re.search(r"[\u3400-\u9fff\uf900-\ufaff]", text):
        if lang.startswith("ja"):
            return "jpan"
        if lang.startswith("ko"):
            return "kore"
        return "hant" if re.search(r"hant|tw|hk|mo", lang) else "hans"
    return "latn"


def _weight(w: str) -> str:
    return "regular" if w == "regular" else "bold"


def candidates(script: str, weight: str = "regular", family: str = "sans") -> list[str]:
    w = _weight(weight)
    other = "regular" if w == "bold" else "bold"
    names = list(LATIN_EXTRA.get((weight, family), [])) if script == "latn" else []
    names += FONT_FILES[script][w] + FONT_FILES[script][other]
    for s, lists in FONT_FILES.items():
        if s != script:
            names += lists[w] + lists[other]
    return list(dict.fromkeys(names))


@functools.lru_cache(maxsize=256)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if not path:
        return ImageFont.load_default(size)
    return ImageFont.truetype(path, size, index=0)


@functools.lru_cache(maxsize=64)
def _notdef(path: str) -> tuple[tuple[int, int], bytes]:
    m = load_font(path, 32).getmask("\U0010fffd")
    return m.size, bytes(m)


@functools.lru_cache(maxsize=8192)
def _has_glyph(path: str, ch: str) -> bool:
    m = load_font(path, 32).getmask(ch)
    return (m.size, bytes(m)) != _notdef(path)


def covers(path: str, text: str) -> bool:
    try:
        return all(_has_glyph(path, ch) for ch in set(text) if not ch.isspace())
    except OSError:
        return False


def pick_font(lang: str, text: str, weight: str = "regular", family: str = "sans",
              dirs: list[Path] | None = None) -> tuple[str, str]:
    """(font path or "" for Pillow's built-in font, script): the first candidate for the text's script and weight
    that has a glyph for every character, then any other installed candidate that does."""
    script = script_of(lang, text)
    index = _index(tuple(dirs if dirs is not None else font_dirs()))
    for name in candidates(script, weight, family):
        p = index.get(name.lower())
        if p is not None and covers(str(p), text):
            return str(p), script
    return "", script


def wrap(text: str, n: int) -> list[str]:
    """Split into at most n lines with the shortest longest line: on spaces, or between characters for CJK/Thai."""
    text = " ".join(text.split())
    sep = " " if " " in text else ""
    if not sep and not re.search(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\u0e00-\u0e7f]", text):
        return [text]
    words = text.split(" ") if sep else list(text)
    n = min(n, len(words))
    if n <= 1:
        return [text]

    @functools.lru_cache(maxsize=None)
    def best(i: int, k: int) -> tuple[int, tuple[int, ...]]:
        if k == 1:
            return len(sep.join(words[i:])), ()
        out = (10 ** 9, ())
        for j in range(i + 1, len(words) - k + 2):
            rest, cuts = best(j, k - 1)
            cand = (max(len(sep.join(words[i:j])), rest), (j, *cuts))
            out = min(out, cand)
        return out

    cuts = (0, *best(0, n)[1], len(words))
    return [sep.join(words[a:b]) for a, b in zip(cuts, cuts[1:])]


def _rgb(hex_: str, default: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, int]:
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", hex_ or "")
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else default  # type: ignore[return-value]


def render_block(lines: list[str], font_path: str, size: int, style: dict[str, Any]) -> Image.Image:
    """The text block at `size` px with outline and drop shadow scaled from the source style, cropped to its ink."""
    font = load_font(font_path, size)
    ref = max(float(style.get("font_px") or size), 1.0)
    k = size / ref
    sw = round(float(style.get("stroke_px") or 0) * k) if style.get("stroke_color") else 0
    if style.get("stroke_color") and float(style.get("stroke_px") or 0) > 0:
        sw = max(sw, 1)
    dx = round(float(style.get("shadow_dx") or 0) * k) if style.get("shadow_color") else 0
    dy = round(float(style.get("shadow_dy") or 0) * k) if style.get("shadow_color") else 0
    asc, desc = font.getmetrics() if hasattr(font, "getmetrics") else (size, size // 4)
    lh = asc + desc + round(size * 0.08)
    widths = [font.getlength(t) + 2 * sw for t in lines]
    pad = sw + abs(dx) + abs(dy) + 4
    w = int(max(widths, default=1)) + 2 * pad
    h = lh * len(lines) + 2 * pad
    text = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td, sd = ImageDraw.Draw(text), ImageDraw.Draw(shadow)
    fill, stroke, shade = _rgb(style.get("color", "")), _rgb(style.get("stroke_color", ""), (0, 0, 0)), \
        _rgb(style.get("shadow_color", ""), (0, 0, 0))
    align = style.get("align") or "center"
    for i, (t, lw) in enumerate(zip(lines, widths)):
        x = pad + {"left": 0, "right": w - 2 * pad - lw}.get(align, (w - 2 * pad - lw) / 2) + sw
        y = pad + i * lh
        if dx or dy:
            sd.text((x + dx, y + dy), t, font=font, fill=(*shade, 255), stroke_width=sw, stroke_fill=(*shade, 255))
        td.text((x, y), t, font=font, fill=(*fill, 255), stroke_width=sw, stroke_fill=(*stroke, 255))
    shadow.alpha_composite(text)
    out = shadow
    if style.get("italic"):
        shear = 0.2
        out = out.transform((w + round(shear * h), h), Image.Transform.AFFINE, (1, shear, -shear * h, 0, 1, 0),
                            Image.Resampling.BICUBIC)
    box = out.getchannel("A").getbbox()
    return out.crop(box) if box else Image.new("RGBA", (1, 1), (0, 0, 0, 0))


def _largest(lo: int, hi: int, ok: Any) -> int:
    best = lo if ok(lo) else 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if ok(mid):
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def source_size(source: str, font_path: str, style: dict[str, Any], line_h: float) -> int:
    """Font size of the original text: the size at which its first line, in a font for its own script, is as tall
    (ink incl. outline and shadow) as one source line."""
    first = (source.splitlines() or [""])[0].strip() or "Hg"
    return _largest(MIN_PX, max(MIN_PX, round(line_h * 3)),
                    lambda s: render_block([first], font_path, s, style).height <= line_h + 1)


def fit_text(variants: dict[str, str], font_path: str, style: dict[str, Any], ink: list[int], container: list[int],
             cap: int, src_lines: int = 1, allow_wrap: bool = True, min_scale: float = 0.6) -> dict[str, Any]:
    """Largest size up to the source's font size `cap` whose block fits the container's width (minus a margin) and
    about the source's height. An extra line is used only when it buys a clearly bigger size; the full translation
    wins unless it would shrink below `min_scale` and the short one fits bigger."""
    ink_w, ink_h = ink[2] - ink[0], ink[3] - ink[1]
    cw, ch = container[2] - container[0], container[3] - container[1]
    avail_w = max(ink_w, cw - 2 * max(2, round(min(cw, ch) * 0.08)))
    line_h = ink_h / max(1, src_lines)
    cap = max(cap, MIN_PX)
    tries = []
    for variant in ("text", "short"):
        text = variants.get(variant) or variants.get("text") or ""
        if variant == "short" and text == variants.get("text"):
            continue
        best = None
        n_opts = [src_lines] + ([src_lines + 1] if allow_wrap and ch >= line_h * (src_lines + 1) * 0.9 else [])
        for n in n_opts:
            lines = wrap(text, n)
            # Glyph sets differ in ink height (CJK fills the em, Latin caps do not): allow a little extra.
            max_h = min(ch, ink_h * 1.3 * len(lines) / src_lines + 1)
            size = _largest(MIN_PX, cap, lambda s: (lambda b: b.width <= avail_w and b.height <= max_h)(
                render_block(lines, font_path, s, style)))
            t = {"variant": variant, "lines": lines, "size": size, "cap": cap}
            if best is None or (best["size"] < 0.8 * cap and size > best["size"] * 1.15):
                best = t
        tries.append(best)
    best = tries[0]
    if best["size"] < min_scale * cap:
        best = max(tries, key=lambda t: t["size"])
    scale = best["size"] / cap
    return {**best, "scale": round(scale, 3), "overflow": scale < min_scale, "avail_w": avail_w}


def place(block: Image.Image, ink: list[int], container: list[int], align: str) -> tuple[int, int]:
    cx = (ink[0] + ink[2]) / 2
    x = {"left": ink[0], "right": ink[2] - block.width}.get(align, cx - block.width / 2)
    y = (ink[1] + ink[3]) / 2 - block.height / 2
    x = min(max(x, container[0]), container[2] - block.width) if block.width <= container[2] - container[0] else x
    y = min(max(y, container[1]), container[3] - block.height) if block.height <= container[3] - container[1] else y
    return round(x), round(y)


def _plane(xs: np.ndarray, ys: np.ndarray, vals: np.ndarray) -> np.ndarray:
    a = np.stack([np.ones_like(xs), xs, ys], 1).astype(np.float64)
    coef, *_ = np.linalg.lstsq(a, vals.astype(np.float64), rcond=None)
    return coef


def analyze(rgba: np.ndarray, region: dict[str, Any], others: np.ndarray, flat_tau: float = FLAT_RESIDUAL
            ) -> dict[str, Any]:
    """Ink mask of one text region: pixels inside the padded box that differ from a plane fitted to the background
    ring around it (other regions excluded). A small plane residual = a flat/gradient background the CPU can redo."""
    H, W = rgba.shape[:2]
    x0, y0, x1, y1 = region["bbox"]
    bh = y1 - y0
    st = region.get("style") or {}
    extra = round(float(st.get("stroke_px") or 0) + abs(float(st.get("shadow_dx") or 0))
                  + abs(float(st.get("shadow_dy") or 0)))
    pad = max(3, round(0.2 * bh)) + extra
    R = [max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)]
    rw = max(2, round(0.15 * bh))
    O = [max(0, R[0] - rw), max(0, R[1] - rw), min(W, R[2] + rw), min(H, R[3] + rw)]
    yy, xx = np.mgrid[O[1]:O[3], O[0]:O[2]]
    inner = (xx >= R[0]) & (xx < R[2]) & (yy >= R[1]) & (yy < R[3])
    ring = ~inner & ~others[O[1]:O[3], O[0]:O[2]]
    c = region.get("container") or [0, 0, W, H]
    in_c = ring & (xx >= c[0]) & (xx < c[2]) & (yy >= c[1]) & (yy < c[3])
    if in_c.sum() >= 8:
        ring = in_c
    sub = rgba[O[1]:O[3], O[0]:O[2]].astype(np.float64)
    alpha_bg = float(np.median(sub[..., 3][ring])) if ring.any() else 255.0
    solid = ring & (sub[..., 3] > 127)
    norm = max(W, H)
    if solid.sum() >= 6:
        px, py, vals = xx[solid] / norm, yy[solid] / norm, sub[..., :3][solid]
        # The ring often clips a container outline or a neighbouring shape: start from the median colour (the
        # majority) and refit the plane only on pixels close to the current fit.
        coef = np.zeros((3, 3))
        coef[0] = np.median(vals, 0)
        a_all = np.stack([np.ones(len(px)), px, py], 1)
        for _ in range(4):
            err = np.sqrt(((vals - a_all @ coef) ** 2).sum(1))
            keep = err <= max(8.0, 2.5 * float(np.median(err)))
            if keep.sum() < 6:
                keep = np.ones(len(px), bool)
                break
            coef = _plane(px[keep], py[keep], vals[keep])
        px, py, vals = px[keep], py[keep], vals[keep]
        residual = float(np.sqrt(((vals - np.stack([np.ones(len(px)), px, py], 1) @ coef) ** 2).sum(1).mean()))
    else:
        coef = np.zeros((3, 3))
        coef[0] = np.median(sub[..., :3].reshape(-1, 3), 0)
        residual = 0.0 if alpha_bg < 128 else 999.0
    ry, rx = np.mgrid[R[1]:R[3], R[0]:R[2]]
    pred = np.stack([np.ones(rx.size), rx.ravel() / norm, ry.ravel() / norm], 1) @ coef
    pred = pred.reshape(rx.shape + (3,))
    box = rgba[R[1]:R[3], R[0]:R[2]].astype(np.float64)
    dist = np.sqrt(((box[..., :3] - pred) ** 2).sum(-1))
    tau = max(28.0, 3.0 * residual) if residual < 900 else 28.0
    ink = ((dist > tau) & (box[..., 3] > 127)) | (np.abs(box[..., 3] - alpha_bg) > 64)
    # Claude's box is approximate and the ring fit can miss an outline close to the background colour, so the text
    # colours count too, but only right around the box: the container's own outline is often the stroke colour.
    near = np.zeros(ink.shape, bool)
    g = max(2, round(0.1 * bh))
    near[max(0, y0 - R[1] - g):y1 - R[1] + g, max(0, x0 - R[0] - g):x1 - R[0] + g] = True
    hint = np.zeros(ink.shape, bool)
    for key in ("color", "stroke_color"):
        if re.fullmatch(r"#[0-9a-f]{6}", str(st.get(key) or "")):
            c_rgb = np.array([int(st[key][i:i + 2], 16) for i in (1, 3, 5)], np.float64)
            hint |= near & (np.sqrt(((box[..., :3] - c_rgb) ** 2).sum(-1)) < 40) & (box[..., 3] > 127)
    core = ink & near
    ink |= hint
    if not core.any():
        core = np.zeros(ink.shape, bool)
        core[y0 - R[1]:y1 - R[1], x0 - R[0]:x1 - R[0]] = True
        ink |= core
    flat = residual < flat_tau or alpha_bg < 128
    d = max(2, round(0.08 * bh)) * (1 if flat else 2)
    grown = dilate(np.pad(ink.astype(np.uint8) * 255, d), d, plus_first=True)[d:-d, d:-d] > 0
    ys, xs = np.nonzero(core)
    tight = [R[0] + int(xs.min()), R[1] + int(ys.min()), R[0] + int(xs.max()) + 1, R[1] + int(ys.max()) + 1]
    return {"R": R, "mask": grown, "ink": tight, "flat": bool(flat), "residual": round(residual, 2),
            "alpha_bg": alpha_bg, "plane": pred}


def cpu_fill(rgba: np.ndarray, R: list[int], mask: np.ndarray, plane: np.ndarray, alpha_bg: float,
             iters: int = 40) -> None:
    """In place: masked pixels start from the background plane, then relax toward their unmasked neighbours so the
    seam with untouched pixels disappears; alpha takes the background's."""
    sub = rgba[R[1]:R[3], R[0]:R[2]].astype(np.float64)
    rgb = sub[..., :3]
    rgb[mask] = plane[mask]
    pad = np.pad(rgb, ((1, 1), (1, 1), (0, 0)), mode="edge")
    for _ in range(iters):
        avg = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:]) / 4
        pad[1:-1, 1:-1][mask] = avg[mask]
    rgb = pad[1:-1, 1:-1]
    out = rgba[R[1]:R[3], R[0]:R[2]]
    out[..., :3][mask] = np.clip(np.round(rgb[mask]), 0, 255).astype(np.uint8)
    out[..., 3][mask] = np.uint8(round(alpha_bg))


def klein_crop(rgba: np.ndarray, R: list[int], mask_full: np.ndarray) -> tuple[Image.Image, list[int], list[int]]:
    """Crop with context around the region, scaled so Klein sees 512–1024 px (sides multiples of 16); alpha 0 marks
    the area to repaint, everything else stays opaque so transparent surroundings are not repainted."""
    H, W = rgba.shape[:2]
    bh = R[3] - R[1]
    ctx = max(16, round(bh * 0.6))
    C = [max(0, R[0] - ctx), max(0, R[1] - ctx), min(W, R[2] + ctx), min(H, R[3] + ctx)]
    cw, ch = C[2] - C[0], C[3] - C[1]
    long = max(cw, ch)
    s = KLEIN_MIN / long if long < KLEIN_MIN else (KLEIN_MAX / long if long > KLEIN_MAX else 1.0)
    size = [max(KLEIN_STEP, round(cw * s / KLEIN_STEP) * KLEIN_STEP),
            max(KLEIN_STEP, round(ch * s / KLEIN_STEP) * KLEIN_STEP)]
    crop = Image.fromarray(rgba[C[1]:C[3], C[0]:C[2]], "RGBA")
    flat = Image.new("RGBA", crop.size, (128, 128, 128, 255))
    flat.alpha_composite(crop)
    rgb = flat.convert("RGB").resize(tuple(size), Image.Resampling.LANCZOS)
    m = Image.fromarray(mask_full[C[1]:C[3], C[0]:C[2]].astype(np.uint8) * 255).resize(tuple(size),
                                                                                       Image.Resampling.BILINEAR)
    alpha = Image.fromarray(np.where(np.asarray(m) > 0, 0, 255).astype(np.uint8))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out, C, size


def paste_fill(rgba: np.ndarray, fill: Image.Image, C: list[int], mask: np.ndarray, feather: float = 1.5) -> None:
    """In place: the Klein fill only replaces pixels inside `mask` (canvas-sized bool); the edge is feathered inward,
    so every pixel outside the mask stays bit-identical."""
    cw, ch = C[2] - C[0], C[3] - C[1]
    f = np.asarray(fill.convert("RGB").resize((cw, ch), Image.Resampling.LANCZOS), np.float64)
    m = mask[C[1]:C[3], C[0]:C[2]]
    w = m.astype(np.float64)
    if feather > 0:
        # Inside the mask both sides are text-free (CPU fill vs Klein), so a soft inner edge only hides the seam.
        soft = np.asarray(Image.fromarray((w * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(feather)),
                          np.float64) / 255
        w = np.where(m, soft, 0.0)
    sub = rgba[C[1]:C[3], C[0]:C[2]]
    mixed = sub[..., :3].astype(np.float64) * (1 - w[..., None]) + f * w[..., None]
    sub[..., :3] = np.where(m[..., None], np.clip(np.round(mixed), 0, 255), sub[..., :3]).astype(np.uint8)


def _tile(img: Image.Image, cell: int) -> Image.Image:
    im = img.convert("RGBA")
    s = min(cell / im.width, cell / im.height)
    im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    # Checker so transparent sprites read as such.
    d = ImageDraw.Draw(tile)
    for y in range(0, cell, 16):
        for x in range(0, cell, 16):
            d.rectangle((x, y, x + 15, y + 15), fill=(58, 58, 64, 255) if (x + y) // 16 % 2 else (46, 46, 52, 255))
    tile.alpha_composite(im, ((cell - im.width) // 2, (cell - im.height) // 2))
    return tile.convert("RGB")


def contact_sheet(items: list[tuple[str, Image.Image]], cell: int = 320, cols: int = 4) -> Image.Image:
    cols = max(1, min(cols, len(items)))
    rows = -(-len(items) // cols)
    gap, label = 10, 22
    out = Image.new("RGB", (gap + cols * (cell + gap), gap + rows * (cell + label + gap)), (24, 24, 28))
    d = ImageDraw.Draw(out)
    font = load_font("", 14)
    for i, (name, img) in enumerate(items):
        x, y = gap + (i % cols) * (cell + gap), gap + (i // cols) * (cell + label + gap)
        out.paste(_tile(img, cell), (x, y))
        d.text((x + 2, y + cell + 4), name, fill=(225, 225, 225), font=font)
    return out


def _labels_png(labels: np.ndarray) -> Image.Image:
    return Image.fromarray(labels.astype(np.uint8), "L")


def _stem(name: str | None) -> str:
    stem = re.sub(r"[^\w.-]+", "_", (name or "image").rsplit(".", 1)[0]).strip("_.")
    return stem or "image"


def prep(src: Image.Image, plan: dict[str, Any], removal: str = "auto", flat_tau: float = FLAT_RESIDUAL
         ) -> tuple[Image.Image, list[Image.Image], np.ndarray, list[dict[str, Any]]]:
    orig = np.asarray(src.convert("RGBA"))
    rgba = orig.copy()
    H, W = rgba.shape[:2]
    regions = [r for r in plan.get("regions") or [] if r.get("translate", True)]
    def box_of(r: dict[str, Any]) -> np.ndarray:
        x0, y0, x1, y1 = r["bbox"]
        p = max(3, round(0.2 * (y1 - y0)))
        b = np.zeros((H, W), bool)
        b[max(0, y0 - p):y1 + p, max(0, x0 - p):x1 + p] = True
        return b

    counts = np.zeros((H, W), np.uint8)
    for r in regions:
        counts += box_of(r)
    labels = np.zeros((H, W), np.uint8)
    found = [analyze(rgba, r, (counts - box_of(r)) > 0, flat_tau) for r in regions]
    holes, info = [], []
    for i, (r, a) in enumerate(zip(regions, found)):
        R = a["R"]
        full = np.zeros((H, W), bool)
        full[R[1]:R[3], R[0]:R[2]] = a["mask"]
        full &= labels == 0
        labels[full] = i + 1
        local = full[R[1]:R[3], R[0]:R[2]]
        cpu_fill(rgba, R, local, a["plane"], a["alpha_bg"], iters=80 if a["flat"] else 40)
        method = "cpu" if removal == "cpu" or (removal == "auto" and a["flat"]) or a["alpha_bg"] < 128 else "klein"
        entry = {"id": r["id"], "bbox": r["bbox"], "container": r["container"], "ink": a["ink"], "box": R,
                 "flat": a["flat"], "residual": a["residual"], "removal": method, "label": i + 1}
        if method == "klein":
            # Klein sees the text it must remove: a blurred CPU prefill as reference gets copied back verbatim.
            hole, C, size = klein_crop(orig, R, full)
            entry.update({"crop": C, "crop_size": size, "hole": len(holes)})
            holes.append(hole)
        info.append(entry)
    return Image.fromarray(rgba, "RGBA"), holes, labels, info


class LocalizePrep(OpExecutor):
    kind = "op.localize_prep"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        ref = _images_in(inputs)[0]
        plans = inputs.get("plan") or []
        if not plans:
            raise StepError("op_no_input", "input 'plan' is empty")
        plan = await _read_json(ctx, plans[0])
        removal = str(params.get("removal") or "auto").lower()
        if removal not in ("auto", "cpu", "klein"):
            raise StepError("op_bad_params", f"removal must be auto | cpu | klein, got {removal!r}")
        try:
            flat_tau = float(params.get("flat_threshold", FLAT_RESIDUAL))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        src = await _load(ctx, ref)
        if list(src.size) != list(plan.get("canvas") or src.size):
            raise StepError("op_bad_input", f"plan canvas {plan.get('canvas')} != image {list(src.size)}")
        stem = _stem(ref.name)
        clean, holes, labels, regions = await asyncio.to_thread(prep, src, plan, removal, flat_tau)
        clean_ref = await _put_png(ctx, clean, f"{stem}_notext_cpu.png")
        mask_ref = await _put_png(ctx, _labels_png(labels), f"{stem}_text_mask.png")
        hole_refs = [await _put_png(ctx, h, f"{stem}_hole_{k}.png") for k, h in enumerate(holes)]
        doc = {"stem": stem, "canvas": list(src.size), "source": {"file": ref.name, "hash": ref.hash},
               "regions": regions, "plan": plan}
        lay = await ctx.blobs.put_bytes(json.dumps(doc, ensure_ascii=False).encode("utf-8"), name=LAYOUT_FILE,
                                        mime="application/json")
        return StepResult(outputs={"clean": [clean_ref], "holes": hole_refs, "mask": [mask_ref], "layout": [lay]},
                          meta={"regions": len(regions), "klein": len(holes),
                                "removal": {r["id"]: r["removal"] for r in regions}})


def render(clean: Image.Image, regions: list[dict[str, Any]], plan: dict[str, Any], lang: str,
           dirs: list[Path], allow_wrap: bool, min_scale: float) -> tuple[Image.Image, list[dict[str, Any]]]:
    out = clean.copy()
    by_id = {r["id"]: r for r in plan.get("regions") or []}
    notes = []
    for reg in regions:
        pr = by_id.get(reg["id"]) or {}
        variants = (pr.get("translations") or {}).get(lang) or {"text": pr.get("text", ""), "short": ""}
        style = pr.get("style") or {}
        sample = variants.get("text", "") + variants.get("short", "")
        font, script = pick_font(lang, sample, style.get("weight", "regular"), style.get("family", "sans"), dirs)
        src_lines = int(pr.get("lines") or 1)
        src_font, _ = pick_font(str(plan.get("source_lang") or ""), pr.get("text", ""), style.get("weight", "regular"),
                                style.get("family", "sans"), dirs)
        cap = source_size(pr.get("text", ""), src_font, style, (reg["ink"][3] - reg["ink"][1]) / src_lines)
        fit = fit_text(variants, font, style, reg["ink"], reg["container"], cap, src_lines, allow_wrap, min_scale)
        block = render_block(fit["lines"], font, fit["size"], style)
        x, y = place(block, reg["ink"], reg["container"], style.get("align", "center"))
        out.alpha_composite(block, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))
        notes.append({"id": reg["id"], "text": "\n".join(fit["lines"]), "variant": fit["variant"],
                      "font": Path(font).name if font else "pillow-default", "script": script, "size": fit["size"],
                      "scale": fit["scale"], "overflow": fit["overflow"], "at": [x, y, block.width, block.height]})
    return out, notes


class LocalizeRender(OpExecutor):
    kind = "op.localize_render"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        clean_ref = _images_in(inputs)[0]
        lays = inputs.get("layout") or []
        masks = inputs.get("mask") or []
        sources = inputs.get("source") or []
        if not lays or not masks or not sources:
            raise StepError("op_no_input", "inputs 'layout', 'mask' and 'source' are required")
        lay = await _read_json(ctx, lays[0])
        plan, regions = lay["plan"], lay["regions"]
        fills = inputs.get("fills") or []
        want = sum(1 for r in regions if r["removal"] == "klein")
        if len(fills) != want:
            raise StepError("op_bad_input", f"{len(fills)} Klein fills for {want} inpaint crops")
        try:
            min_scale = float(params.get("min_scale", 0.6))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        allow_wrap = str(params.get("wrap", True)).lower() not in ("false", "0", "no", "off")
        dirs = font_dirs(params.get("font_dir"))
        langs = [str(x) for x in plan.get("languages") or []]
        clean = (await _load(ctx, clean_ref)).convert("RGBA")
        labels = np.asarray((await _load(ctx, masks[0])).convert("L"))
        rgba = np.asarray(clean).copy()
        for reg in regions:
            if reg["removal"] == "klein":
                fill = await _load(ctx, fills[reg["hole"]])
                await asyncio.to_thread(paste_fill, rgba, fill, reg["crop"], labels == reg["label"])
        clean = Image.fromarray(rgba, "RGBA")
        stem = str(lay.get("stem") or "image")
        base = await _put_png(ctx, clean, f"{stem}_notext.png")
        outs, entries = [], []
        sheet = [("original", await _load(ctx, sources[0])), ("no text", clean)]
        for i, lang in enumerate(langs):
            ctx.progress(i, len(langs), label=lang)
            img, notes = await asyncio.to_thread(render, clean, regions, plan, lang, dirs, allow_wrap, min_scale)
            r = await _put_png(ctx, img, f"{stem}_{lang}.png")
            outs.append(r)
            entries.append({"lang": lang, "file": r.name, "hash": r.hash, "regions": notes})
            flags = [n["id"] for n in notes if n["overflow"]]
            short = [n["id"] for n in notes if n["variant"] == "short"]
            tag = (" (short)" if short else "") + (f" (overflow {','.join(flags)})" if flags else "")
            sheet.append((lang + tag, img))
        prev = await _put_png(ctx, await asyncio.to_thread(contact_sheet, sheet), f"{stem}_localize_preview.png")
        doc = {"type": LOCALIZE_TYPE, "asset": stem, "canvas": lay["canvas"], "source": lay["source"],
               "source_lang": plan.get("source_lang"), "clean": {"file": base.name, "hash": base.hash},
               "regions": [{**{k: reg[k] for k in ("id", "bbox", "container", "ink", "removal", "flat", "residual")},
                            "text": (next((p for p in plan.get("regions") or [] if p["id"] == reg["id"]), {})
                                     .get("text"))} for reg in regions],
               "languages": entries, "missing": plan.get("missing") or []}
        lj = await ctx.blobs.put_bytes(json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"),
                                       name=LOCALIZE_FILE, mime="application/json")
        ctx.progress(len(langs), len(langs))
        overflow = {e["lang"]: [n["id"] for n in e["regions"] if n["overflow"]] for e in entries}
        meta: dict[str, Any] = {"languages": langs, "regions": len(regions),
                                "short": {e["lang"]: [n["id"] for n in e["regions"] if n["variant"] == "short"]
                                          for e in entries}}
        if any(overflow.values()):
            meta["flagged"] = [f"{k}: {', '.join(v)} shrunk below {min_scale:.0%}" for k, v in overflow.items() if v]
        return StepResult(outputs={"images": outs, "clean": [base], "localize": [lj], "preview": [prev]}, meta=meta)


LOCALIZE_OPS: tuple[type[OpExecutor], ...] = (LocalizePrep, LocalizeRender)
