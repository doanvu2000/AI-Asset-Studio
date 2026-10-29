"""Enemy variants of an existing 8-direction sprite sheet (static or animated). Palette variants remap each colour
region's ramp through one colour table built from the whole sheet, so every frame of every clip changes identically
and the colour count never grows. An optional accessory is drawn by Flux.2 Klein on one key frame per direction, cut
out as the pixels the edit changed, and carried to that direction's other frames by template tracking (or by the
paper-doll sockets when a sockets.json is given). Cells, pivot and clips never move, so the source meta still applies."""
from __future__ import annotations

import asyncio
import hashlib
import json
import unicodedata
from collections import deque
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png, _read_json
from .pixel import parse_hex
from .rarity import LUM, _hex, ramp_lut

if TYPE_CHECKING:
    from ..export.sheet import Cell, Layout

VARIANTS_FILE = "enemy_variants.json"
VARIANTS_TYPE = "asset-studio/enemy-variants"
LAYOUT_FILE = "enemy_layout.json"
REF_SIZE = 1024
MAX_VARIANTS = 12
DARK = 0.12
PIXEL_COLORS = 256

PRESETS: dict[str, tuple[str, ...]] = {
    "lua": ("#d8401c", "#ff9a1f", "#ffd34a"),
    "fire": ("#d8401c", "#ff9a1f", "#ffd34a"),
    "bang": ("#3a8fd8", "#8fe3ff", "#dff6ff"),
    "ice": ("#3a8fd8", "#8fe3ff", "#dff6ff"),
    "doc": ("#4caf30", "#9b3fd0", "#c8f04a"),
    "poison": ("#4caf30", "#9b3fd0", "#c8f04a"),
    "elite": ("#7a2bd0", "#e0b030", "#c03030"),
    "bong_toi": ("#40306a", "#8a1a2e", "#9a8ab0"),
    "shadow": ("#40306a", "#8a1a2e", "#9a8ab0"),
    "dien": ("#f0c820", "#3aa0ff", "#e8f4ff"),
    "lightning": ("#f0c820", "#3aa0ff", "#e8f4ff"),
    "dat": ("#8a5a2a", "#6a8a3a", "#c8a060"),
    "earth": ("#8a5a2a", "#6a8a3a", "#c8a060"),
    "vang": ("#d4a017", "#f2d06b", "#8a5a10"),
    "gold": ("#d4a017", "#f2d06b", "#8a5a10"),
}


def slug(name: str) -> str:
    """ASCII engine-safe name: "Lửa" -> "lua", "bóng tối" -> "bong_toi"."""
    s = name.strip().lower().replace("đ", "d")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    out = "".join(c if c.isascii() and c.isalnum() else "_" for c in s)
    out = "_".join(p for p in out.split("_") if p)
    return f"v{out}" if out[:1].isdigit() else out


def _auto_colors(key: str) -> tuple[str, ...]:
    import colorsys

    h = int(hashlib.blake2b(key.encode(), digest_size=4).hexdigest(), 16) / 0xFFFFFFFF
    cols = [colorsys.hsv_to_rgb((h + d) % 1, s, v) for d, s, v in ((0, 0.75, 0.8), (0.08, 0.6, 0.95), (0.5, 0.5, 0.9))]
    return tuple(_hex(tuple(int(c * 255) for c in rgb)) for rgb in cols)  # type: ignore[arg-type]


def parse_variants(raw: Any) -> list[dict[str, Any]]:
    """`lửa, băng, elite` (presets) or one per line `tên: #hex #hex …` (main colour first, then accents)."""
    text = str(raw or "").strip()
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        if ":" in line:
            name, cols = line.split(":", 1)
            entries.append((name.strip(), cols.strip()))
        else:
            entries += [(p.strip(), "") for p in line.split(",") if p.strip()]
    out: list[dict[str, Any]] = []
    for name, cols in entries:
        key = slug(name)
        if not key:
            raise StepError("op_bad_params", f"bad variant name {name!r}")
        if any(v["slug"] == key for v in out):
            raise StepError("op_bad_params", f"duplicate variant {name!r}")
        parts = [c for c in cols.replace(",", " ").split() if c]
        try:
            colors = [parse_hex(c) for c in parts] or [parse_hex(c) for c in PRESETS.get(key) or _auto_colors(key)]
        except ValueError as e:
            raise StepError("op_bad_params", f"{name}: {e}") from e
        out.append({"name": name, "slug": key, "colors": colors})
    if not out:
        raise StepError("op_bad_params", "no variants given")
    if len(out) > MAX_VARIANTS:
        raise StepError("op_bad_params", f"at most {MAX_VARIANTS} variants")
    return out


def _hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mx, mn = rgb.max(-1), rgb.min(-1)
    d = mx - mn
    sat = np.where(mx > 1e-4, d / np.maximum(mx, 1e-4), 0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    safe = np.maximum(d, 1e-6)
    hue = np.where(mx == r, ((g - b) / safe) % 6, np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4)) / 6
    return np.where(d > 1e-6, hue, 0), sat


def unique_colors(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(mask of visible pixels, per-visible-pixel index into the colour list, colours (N, 3) uint8, counts)."""
    vis = arr[..., 3] > 0
    px = arr[vis][:, :3].astype(np.int32)
    keys = (px[:, 0] << 16) | (px[:, 1] << 8) | px[:, 2]
    uniq, inv, counts = np.unique(keys, return_inverse=True, return_counts=True)
    cols = np.stack([(uniq >> 16) & 255, (uniq >> 8) & 255, uniq & 255], -1).astype(np.uint8)
    return vis, inv, cols, counts


def find_regions(cols: np.ndarray, counts: np.ndarray, k: int = 3, bins: int = 36) -> list[float]:
    """Hue peaks of the saturated colours, biggest first: each becomes a region that takes one variant colour."""
    rgb = cols.astype(np.float32) / 255.0
    hue, sat = _hsv(rgb)
    lum = rgb @ LUM
    ok = (sat >= 0.15) & (lum >= DARK)
    if not ok.any():
        return []
    w = counts[ok] * sat[ok]
    hist = np.bincount((hue[ok] * bins).astype(int) % bins, weights=w, minlength=bins)
    total = float(hist.sum())
    peaks: list[float] = []
    hist = hist.copy()
    while len(peaks) < k:
        i = int(hist.argmax())
        if hist[i] < 0.08 * total:
            break
        near = [(i + d) % bins for d in range(-2, 3)]
        ang = np.array([(j + 0.5) / bins * 2 * np.pi for j in near])
        wt = hist[near]
        peaks.append(float(np.arctan2((np.sin(ang) * wt).sum(), (np.cos(ang) * wt).sum()) / (2 * np.pi) % 1))
        for d in range(-4, 5):
            hist[(i + d) % bins] = 0
    return peaks


def region_labels(cols: np.ndarray, peaks: list[float]) -> np.ndarray:
    """Nearest hue peak per colour; -1 for greys, darks and outlines."""
    rgb = cols.astype(np.float32) / 255.0
    hue, sat = _hsv(rgb)
    lum = rgb @ LUM
    if not peaks:
        return np.full(len(cols), -1, np.int32)
    p = np.asarray(peaks, np.float32)
    dist = np.abs(((hue[:, None] - p[None]) + 0.5) % 1 - 0.5)
    lab = dist.argmin(1).astype(np.int32)
    return np.where((sat >= 0.15) & (lum >= DARK), lab, -1)


def remap_colors(cols: np.ndarray, labels: np.ndarray, colors: list[tuple[int, int, int]], strength: float = 0.9,
                 grey: float = 0.25) -> np.ndarray:
    """Luma-exact ramp swap per region; greys follow the main colour weakly, darks (outline) stay as they are."""
    rgb = cols.astype(np.float32) / 255.0
    _, sat = _hsv(rgb)
    lum = np.clip(rgb @ LUM, 0, 1)
    idx = np.round(lum * 255).astype(np.int32)
    target = np.empty_like(rgb)
    for r in range(-1, max(int(labels.max(initial=-1)) + 1, 0)):
        sel = labels == r
        if sel.any():
            target[sel] = ramp_lut(colors[max(r, 0) % len(colors)])[idx[sel]]
    t = np.clip((sat - 0.08) / 0.27, 0, 1)
    w = strength * (grey + (1 - grey) * t * t * (3 - 2 * t))
    w = np.where(lum < DARK, 0, np.where(labels < 0, np.minimum(w, strength * grey), w))[:, None]
    return np.clip(np.round((rgb * (1 - w) + target * w) * 255), 0, 255).astype(np.uint8)


def recolor_sheet(sheet: Image.Image, peaks: list[float], colors: list[tuple[int, int, int]],
                  strength: float, grey: float) -> tuple[Image.Image, int, int]:
    """(recoloured sheet, colours before, colours after); one table for the whole sheet."""
    arr = np.asarray(sheet.convert("RGBA")).copy()
    vis, inv, cols, _ = unique_colors(arr)
    new = remap_colors(cols, region_labels(cols, peaks), colors, strength, grey)
    px = arr[vis]
    px[:, :3] = new[inv]
    arr[vis] = px
    after = len(np.unique((new[:, 0].astype(np.int32) << 16) | (new[:, 1].astype(np.int32) << 8) | new[:, 2]))
    return Image.fromarray(arr, "RGBA"), len(cols), after


def direction_keys(lay: Layout) -> list[tuple[float, Cell]]:
    """First frame of the first clip for each direction: the accessory is drawn there."""
    seen: dict[int, tuple[float, Cell]] = {}
    for t in lay.tracks:
        a = round(float(t.angle)) % 360
        if a not in seen and t.cells:
            seen[a] = (float(t.angle), t.cells[0])
    return list(seen.values())


def direction_cells(lay: Layout, angle: float) -> list[Cell]:
    a = round(angle) % 360
    return [c for t in lay.tracks if round(float(t.angle)) % 360 == a for c in t.cells]


def crop(sheet: Image.Image, c: Cell) -> Image.Image:
    return sheet.crop((c.x, c.y, c.x + c.w, c.y + c.h))


def letterbox(img: Image.Image, nearest: bool, size: int = REF_SIZE, margin: float = 0.1) -> tuple[Image.Image, list[int]]:
    """Frame on white, scaled up for the editor; the box maps the edit back onto the cell."""
    w, h = img.size
    s = size * (1 - 2 * margin) / max(w, h)
    bw, bh = max(1, round(w * s)), max(1, round(h * s))
    x, y = (size - bw) // 2, (size - bh) // 2
    out = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    rs = Image.Resampling.NEAREST if nearest else Image.Resampling.LANCZOS
    out.alpha_composite(img.convert("RGBA").resize((bw, bh), rs), (x, y))
    return out.convert("RGB"), [x, y, bw, bh]


def key_area(frame: Image.Image, room: float = 0.25) -> tuple[int, int, int, int]:
    """Figure box plus room for what the accessory adds (horns, crest): the editor then sees the figure large."""
    bb = _bbox(np.asarray(frame.convert("RGBA"))[..., 3] > 0)
    w, h = frame.size
    if bb is None:
        return 0, 0, w, h
    m = round(room * max(bb[2] - bb[0], bb[3] - bb[1]))
    return max(0, bb[0] - m), max(0, bb[1] - m), min(w, bb[2] + m), min(h, bb[3] + m)


def unbox(edit: Image.Image, box: list[int], cell: tuple[int, int], area: list[int] | None = None,
          size: int = REF_SIZE) -> Image.Image:
    """The edit back on the cell (white where the key area did not reach)."""
    img = edit.convert("RGB")
    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    x, y, bw, bh = box
    a = area or [0, 0, cell[0], cell[1]]
    part = img.crop((x, y, x + bw, y + bh)).resize((a[2] - a[0], a[3] - a[1]), Image.Resampling.BOX)
    out = Image.new("RGB", cell, (255, 255, 255))
    out.paste(part, (a[0], a[1]))
    return out


def is_pixel_art(sheet: Image.Image, frame_w: int) -> bool:
    if frame_w <= 64:
        return True
    _, _, cols, _ = unique_colors(np.asarray(sheet.convert("RGBA")))
    return len(cols) <= PIXEL_COLORS


def _on_white(img: Image.Image) -> np.ndarray:
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img.convert("RGBA"))
    return np.asarray(bg.convert("RGB"), np.float32)


def _shift(a: np.ndarray, dx: int, dy: int, fill: Any = 0) -> np.ndarray:
    out = np.full_like(a, fill)
    h, w = a.shape[:2]
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = a[ys, xs]
    return out


def _erode(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            out &= _shift(m, dx, dy, False)
    return out


def _dilate(m: np.ndarray, r: int = 1) -> np.ndarray:
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= _shift(m, dx, dy, False)
    return out


def _components(m: np.ndarray, min_area: int) -> np.ndarray:
    keep = np.zeros_like(m)
    seen = np.zeros_like(m)
    h, w = m.shape
    for y0, x0 in zip(*np.nonzero(m)):
        if seen[y0, x0]:
            continue
        q, pts = deque([(y0, x0)]), []
        seen[y0, x0] = True
        while q:
            y, x = q.popleft()
            pts.append((y, x))
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < h and 0 <= xx < w and m[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    q.append((yy, xx))
        if len(pts) >= min_area:
            ys, xs = zip(*pts)
            keep[list(ys), list(xs)] = True
    return keep


def accessory_layer(base: Image.Image, edit: Image.Image, threshold: float = 64,
                    palette: np.ndarray | None = None) -> tuple[Image.Image, np.ndarray, dict[str, Any]]:
    """What the edit added (RGBA layer) and removed (erase mask) on one key frame. The editor re-renders the whole
    figure, so only solid changed blobs count: thin outline drift and speckle are dropped."""
    src = _on_white(base)
    ed = np.asarray(edit.convert("RGB").resize(base.size, Image.Resampling.LANCZOS), np.float32)
    best = (np.inf, 0, 0)
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            d = float(np.abs(_shift(ed, dx, dy, 255.0) - src)[4:-4, 4:-4].mean())
            if d < best[0]:
                best = (d, dx, dy)
    ed = _shift(ed, best[1], best[2], 255.0)
    diff = np.abs(ed - src).max(-1)
    hard = _dilate(_erode(diff > threshold))
    area = max(6, base.width * base.height // 500)
    kept = _components(hard, area)
    mask = kept | (_dilate(kept) & (diff > threshold / 2))
    alpha = np.asarray(base.convert("RGBA"))[..., 3]
    erase = mask & (ed.min(-1) > 235) & (alpha > 127)
    paint = mask & ~erase
    layer = np.zeros((*diff.shape, 4), np.uint8)
    rgb = ed[paint]
    if palette is not None and len(palette) and len(rgb):
        pal = palette.astype(np.float32)
        rgb = pal[((rgb[:, None, :] - pal[None]) ** 2).sum(-1).argmin(1)]
    layer[paint, :3] = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
    layer[paint, 3] = 255
    info = {"align": [best[1], best[2]], "painted": int(paint.sum()), "erased": int(erase.sum())}
    return Image.fromarray(layer, "RGBA"), erase, info


def _premul(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGBA"), np.float32) / 255.0
    return np.concatenate([a[..., :3] * a[..., 3:], a[..., 3:]], -1)


def _valid_corr(a: np.ndarray, k: np.ndarray) -> np.ndarray:
    s = a.shape
    full = np.fft.irfft2(np.fft.rfft2(a, s) * np.fft.rfft2(k[::-1, ::-1], s), s)
    return full[k.shape[0] - 1:, k.shape[1] - 1:]


def match_shift(key: np.ndarray, frame: np.ndarray, box: tuple[int, int, int, int], weight: np.ndarray,
                radius: int) -> tuple[int, int, float]:
    """Translation of the key patch `box` that best fits `frame` (weighted SSD via FFT), with its RMS error."""
    x0, y0, x1, y1 = box
    t = key[y0:y1, x0:x1]
    pad = np.pad(frame, ((radius, radius), (radius, radius), (0, 0)))
    sub = pad[y0:y1 + 2 * radius, x0:x1 + 2 * radius]
    w = weight[..., None] * np.ones_like(t)
    ssd = float((w * t * t).sum()) - 2 * sum(_valid_corr(sub[..., c], w[..., c] * t[..., c]) for c in range(t.shape[2]))
    ssd = ssd + sum(_valid_corr(sub[..., c] ** 2, w[..., c]) for c in range(t.shape[2]))
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    # A tiny pull towards no motion breaks ties on flat patches (idle frames barely move).
    iy, ix = np.unravel_index(int(np.argmin(ssd + 1e-4 * (xx ** 2 + yy ** 2) * float(w.sum()))), ssd.shape)
    err = float(np.sqrt(max(float(ssd[iy, ix]), 0) / max(float(w.sum()), 1e-6)))
    return int(ix) - radius, int(iy) - radius, err


def _bbox(m: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(m)
    return None if not len(ys) else (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def socket_shift(sockets: dict[str, Any], key: Cell, cell: Cell, at: tuple[float, float]) -> tuple[int, int] | None:
    """Paper-doll sockets (op.paperdoll): follow the socket closest to the accessory on the key frame."""
    frames = {f.get("cell"): f for f in sockets.get("frames") or []}
    k, c = frames.get(key.name), frames.get(cell.name)
    if not k or not c:
        return None
    ks = {s["name"]: s for s in k.get("sockets") or []}
    cs = {s["name"]: s for s in c.get("sockets") or []}
    names = [n for n in ks if n in cs]
    if not names:
        return None
    n = min(names, key=lambda n: (ks[n]["x"] - at[0]) ** 2 + (ks[n]["y"] - at[1]) ** 2)
    return round(cs[n]["x"] - ks[n]["x"]), round(cs[n]["y"] - ks[n]["y"])


def propagate(sheet: Image.Image, lay: Layout, key: Cell, angle: float, layer: Image.Image, erase: np.ndarray,
              sockets: dict[str, Any] | None = None, max_err: float = 0.18) -> list[dict[str, Any]]:
    """Draws the key frame's accessory on every frame of its direction (in place); one record per frame."""
    occupied = (np.asarray(layer)[..., 3] > 0) | erase
    bb = _bbox(occupied)
    if bb is None:
        return []
    fw, fh = key.w, key.h
    m = max(3, fw // 12)
    box = (max(0, bb[0] - m), max(0, bb[1] - m), min(fw, bb[2] + m), min(fh, bb[3] + m))
    weight = (~occupied).astype(np.float32)[box[1]:box[3], box[0]:box[2]]
    key_arr = _premul(crop(sheet, key))
    radius = max(4, fw // 5)
    centre = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
    lay_arr = np.asarray(layer)
    recs = []
    for cell in direction_cells(lay, angle):
        err, how = 0.0, "key"
        if cell != key:
            via = socket_shift(sockets, key, cell, centre) if sockets else None
            if via is not None:
                (dx, dy), how = via, "socket"
            else:
                dx, dy, err = match_shift(key_arr, _premul(crop(sheet, cell)), box, weight, radius)
                how = "track"
        else:
            dx = dy = 0
        frame = np.asarray(crop(sheet, cell)).copy()
        gone = _shift(erase, dx, dy, False)
        frame[gone, 3] = 0
        img = Image.fromarray(frame, "RGBA")
        img.alpha_composite(Image.fromarray(_shift(lay_arr, dx, dy, 0), "RGBA"))
        sheet.paste(img, (cell.x, cell.y))
        recs.append({"cell": cell.name, "dx": dx, "dy": dy, "via": how, "err": round(err, 4),
                     "flagged": err > max_err})
    return recs


def _font(size: int) -> Any:
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def contact_columns(lay: Layout) -> list[tuple[str, Cell]]:
    """Every direction's key frame, then the middle frame of each other clip facing the first direction."""
    from ..export.sheet import compass

    keys = direction_keys(lay)
    cols = [(compass(a), c) for a, c in keys]
    if keys:
        first = round(keys[0][0]) % 360
        for clip in lay.clips()[1:]:
            t = next((t for t in lay.tracks if t.clip == clip and round(float(t.angle)) % 360 == first), None)
            if t and t.cells:
                cols.append((f"{clip} {len(t.cells) // 2}", t.cells[len(t.cells) // 2]))
    return cols


def contact_sheet(rows: list[tuple[str, list[tuple[int, int, int]], Image.Image, bool]], lay: Layout, pixel: bool,
                  flagged: set[str] | None = None) -> Image.Image:
    """Rows: (label, colours, sheet, has accessory); unsure accessory frames are outlined on accessory rows only."""
    cols = contact_columns(lay)
    # Rendered frames leave a wide empty margin; one shared crop keeps every tile aligned and the figure readable.
    boxes = [_bbox(np.asarray(crop(r[2], c))[..., 3] > 0) for r in rows for _, c in cols]
    boxes = [b for b in boxes if b]
    p = 2
    view = (max(0, min(b[0] for b in boxes) - p), max(0, min(b[1] for b in boxes) - p),
            min(lay.frame_w, max(b[2] for b in boxes) + p), min(lay.frame_h, max(b[3] for b in boxes) + p)) \
        if boxes else (0, 0, lay.frame_w, lay.frame_h)
    vw, vh = view[2] - view[0], view[3] - view[1]
    scale = min(1.0, 128 / max(vw, vh))
    if pixel and scale >= 1:
        scale = float(max(1, 112 // max(vw, vh)))
    cw, ch = max(1, round(vw * scale)), max(1, round(vh * scale))
    gap, label, head = 6, 128, 24
    width = label + len(cols) * (cw + gap) + gap
    out = Image.new("RGB", (width, head + len(rows) * (ch + gap) + gap), (30, 31, 36))
    d = ImageDraw.Draw(out)
    f, fs = _font(14), _font(11)
    rs = Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS
    for j, (name, _) in enumerate(cols):
        d.text((label + gap + j * (cw + gap) + 2, 5), name, fill=(150, 150, 160), font=fs)
    for i, (name, colors, sheet, acc) in enumerate(rows):
        y = head + gap + i * (ch + gap)
        d.text((8, y + 4), name, fill=(235, 235, 240), font=f)
        for k, c in enumerate(colors[:3]):
            d.rectangle((8 + k * 18, y + 26, 8 + k * 18 + 14, y + 40), fill=c, outline=(80, 80, 90))
        for j, (_, cell) in enumerate(cols):
            x = label + gap + j * (cw + gap)
            tile = Image.new("RGBA", (cw, ch), (52, 54, 62, 255))
            tile.alpha_composite(crop(sheet, cell).crop(view).resize((cw, ch), rs))
            out.paste(tile.convert("RGB"), (x, y))
            if acc and flagged and cell.name in flagged:
                d.rectangle((x, y, x + cw - 1, y + ch - 1), outline=(255, 110, 90), width=2)
    return out


def accessory_preview(keys: list[tuple[Image.Image, Image.Image, Image.Image]], pixel: bool) -> Image.Image:
    """Per direction: key frame | Klein edit (back on the cell) | key frame + extracted accessory."""
    if not keys:
        return Image.new("RGB", (8, 8), (30, 31, 36))
    cw = ch = 160
    gap = 6
    out = Image.new("RGB", (gap + len(keys) * (cw + gap), gap + 3 * (ch + gap)), (30, 31, 36))
    rs = Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS
    for j, trio in enumerate(keys):
        for i, im in enumerate(trio):
            tile = Image.new("RGBA", (cw, ch), (52, 54, 62, 255))
            s = min(cw / im.width, ch / im.height)
            if pixel:
                s = max(1, int(s))
            fit = im.convert("RGBA").resize((max(1, round(im.width * s)), max(1, round(im.height * s))), rs)
            tile.alpha_composite(fit, ((cw - fit.width) // 2, (ch - fit.height) // 2))
            out.paste(tile.convert("RGB"), (gap + j * (cw + gap), gap + i * (ch + gap)))
    return out


def _strip_meta(meta: dict[str, Any]) -> dict[str, Any]:
    # Frame hashes point at the source frames; a variant sheet has none of its own.
    return {k: v for k, v in meta.items() if k != "frames"}


def _layout(meta: dict[str, Any]) -> Layout:
    from ..export.sheet import layout

    try:
        lay = layout(meta, directional_rows=True)
    except (KeyError, ValueError, TypeError) as e:
        raise StepError("op_bad_input", f"bad sheet meta: {e}") from e
    if not lay.facing:
        raise StepError("op_bad_input", "the sheet has no directions (needs an 8-direction sprite or anim sheet meta)")
    return lay


async def _sheet_and_meta(ctx: StepContext, inputs: Outputs) -> tuple[Image.Image, dict[str, Any], Layout]:
    for port in ("sheet", "meta"):
        if len(inputs.get(port) or []) != 1:
            raise StepError("op_bad_input", f"expected exactly 1 '{port}'")
    meta = await _read_json(ctx, inputs["meta"][0])
    if not isinstance(meta, dict):
        raise StepError("op_bad_input", "sheet meta is not an object")
    lay = _layout(meta)
    sheet = (await _load(ctx, inputs["sheet"][0])).convert("RGBA")
    if sheet.size != (lay.width, lay.height):
        raise StepError("op_bad_input", f"sheet is {sheet.size}, meta says {(lay.width, lay.height)}")
    return sheet, meta, lay


class EnemyPrep(OpExecutor):
    """Key frames on white for the accessory edit (none when no accessory is asked for) + the layout to undo it."""
    kind = "op.enemy_prep"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        sheet, meta, lay = await _sheet_and_meta(ctx, inputs)
        parse_variants(params.get("variants"))
        accessory = str(params.get("accessory") or "").strip()
        pixel = await asyncio.to_thread(is_pixel_art, sheet, lay.frame_w)
        keys: list[BlobRef] = []
        entries = []
        if accessory:
            for i, (angle, cell) in enumerate(direction_keys(lay)):
                frame = crop(sheet, cell)
                area = key_area(frame)
                white, box = await asyncio.to_thread(letterbox, frame.crop(area), pixel)
                keys.append(await _put_png(ctx, white, f"key_{round(angle) % 360:03d}.png"))
                entries.append({"angle": angle, "cell": cell.name, "box": box, "area": list(area)})
                ctx.progress(i + 1, 8, label=cell.name)
        doc = {"ref_size": REF_SIZE, "frame": [lay.frame_w, lay.frame_h], "pixel": pixel, "accessory": accessory,
               "keys": entries}
        lay_ref = await ctx.blobs.put_bytes(json.dumps(doc).encode(), name=LAYOUT_FILE, mime="application/json")
        ctx.progress(1.0)
        return StepResult(outputs={"keys": keys, "layout": [lay_ref]},
                          meta={"directions": len(direction_keys(lay)), "accessory": bool(accessory), "pixel": pixel})


class EnemyVariants(OpExecutor):
    kind = "op.enemy_variants"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        sheet, meta, lay = await _sheet_and_meta(ctx, inputs)
        variants = parse_variants(params.get("variants"))
        try:
            strength = float(params.get("strength", 0.9))
            grey = float(params.get("grey", 0.25))
            max_err = float(params.get("max_err", 0.18))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        asset = slug(str(params.get("asset") or "enemy")) or "enemy"
        lay_doc = await _read_json(ctx, inputs["layout"][0]) if inputs.get("layout") else {}
        pixel = bool(lay_doc.get("pixel")) if lay_doc else await asyncio.to_thread(is_pixel_art, sheet, lay.frame_w)
        edits = inputs.get("edits") or []
        keys = lay_doc.get("keys") or []
        sockets = await _read_json(ctx, inputs["sockets"][0]) if inputs.get("sockets") else None
        on_raw = str(params.get("accessory_on") or "all").strip().lower()
        on = {v["slug"] for v in variants} if on_raw in ("all", "*", "") else \
            {slug(p) for p in on_raw.replace("\n", ",").split(",") if p.strip()}

        acc_sheet: Image.Image | None = None
        acc_records: list[dict[str, Any]] = []
        acc_keys: list[tuple[Image.Image, Image.Image, Image.Image]] = []
        if keys and edits:
            if len(edits) != len(keys):
                raise StepError("op_bad_input", f"{len(edits)} accessory edits for {len(keys)} directions")
            palette = None
            if pixel:
                _, _, palette, _ = await asyncio.to_thread(unique_colors, np.asarray(sheet))
            acc_sheet = sheet.copy()
            cells = {c.name: c for c in lay.cells}
            for i, (k, ref) in enumerate(zip(keys, edits)):
                cell = cells.get(k["cell"])
                if cell is None:
                    raise StepError("op_bad_input", f"layout key cell {k['cell']} is not in the sheet")
                base = crop(sheet, cell)
                edit = unbox(await _load(ctx, ref), [int(v) for v in k["box"]], (cell.w, cell.h),
                             [int(v) for v in k["area"]] if k.get("area") else None,
                             int(lay_doc.get("ref_size") or REF_SIZE))
                layer, erase, info = await asyncio.to_thread(accessory_layer, base, edit, 64, palette)
                recs = await asyncio.to_thread(propagate, acc_sheet, lay, cell, float(k["angle"]), layer, erase,
                                               sockets, max_err)
                with_acc = base.copy()
                with_acc.alpha_composite(layer)
                view = tuple(int(v) for v in k.get("area") or (0, 0, cell.w, cell.h))
                acc_keys.append((base.crop(view), edit.crop(view), with_acc.crop(view)))
                acc_records.append({"angle": k["angle"], "key": cell.name, **info, "frames": recs})
                ctx.progress(i + 1, len(keys) + len(variants), label=f"accessory {cell.name}")
        flagged = {r["cell"] for a in acc_records for r in a["frames"] if r["flagged"]}
        empty = [a["key"] for a in acc_records if not a["painted"] and not a["erased"]]

        _, _, base_cols, base_counts = await asyncio.to_thread(unique_colors, np.asarray(sheet))
        peaks = find_regions(base_cols, base_counts)
        sheets: list[BlobRef] = []
        entries = []
        rows: list[tuple[str, list[tuple[int, int, int]], Image.Image, bool]] = [("gốc", [], sheet, False)]
        for i, v in enumerate(variants):
            src = acc_sheet if acc_sheet is not None and v["slug"] in on else sheet
            img, before, after = await asyncio.to_thread(recolor_sheet, src, peaks, v["colors"], strength, grey)
            ref = await _put_png(ctx, img, f"{asset}_{v['slug']}_sheet.png")
            sheets.append(ref)
            rows.append((v["name"], v["colors"], img, src is acc_sheet))
            entries.append({"name": v["name"], "slug": v["slug"], "asset": f"{asset}_{v['slug']}",
                            "colors": [_hex(c) for c in v["colors"]], "accessory": src is acc_sheet,
                            "colors_before": before, "colors_after": after, "file": ref.name, "hash": ref.hash})
            ctx.progress(len(keys) + i + 1, len(keys) + len(variants), label=v["slug"])
        contact = await asyncio.to_thread(contact_sheet, rows, lay, pixel, flagged)
        preview = await _put_png(ctx, contact, f"{asset}_variants_contact.png")
        meta_out = _strip_meta(meta)
        meta_ref = await ctx.blobs.put_bytes(json.dumps(meta_out, indent=2).encode(), name="sheet.json",
                                             mime="application/json")
        outputs: dict[str, list[BlobRef]] = {"sheets": sheets, "meta": [meta_ref], "preview": [preview],
                                             "accessory_preview": []}
        if acc_keys:
            ap = await asyncio.to_thread(accessory_preview, acc_keys, pixel)
            outputs["accessory_preview"] = [await _put_png(ctx, ap, f"{asset}_accessory_keys.png")]
        doc = {"type": VARIANTS_TYPE, "version": 1, "asset": asset, "pixel": pixel,
               "source": {"hash": inputs["sheet"][0].hash, "name": inputs["sheet"][0].name},
               "meta": meta_out, "regions": [round(p * 360, 1) for p in peaks],
               "accessory": {"prompt": lay_doc.get("accessory") or "", "on": sorted(on & {v["slug"] for v in variants}),
                             "directions": acc_records, "flagged": sorted(flagged), "empty": empty}
               if acc_records else None,
               "variants": entries}
        doc_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode(), name=VARIANTS_FILE,
                                            mime="application/json")
        outputs["variants"] = [doc_ref]
        if flagged:
            ctx.log(f"accessory tracking unsure on {len(flagged)} frames (outlined red in the contact sheet)", "warning")
        if empty:
            ctx.log(f"Klein added nothing visible on: {', '.join(empty)}", "warning")
        ctx.progress(1.0)
        return StepResult(outputs=outputs, meta={"variants": [v["slug"] for v in variants],
                                                 "regions": doc["regions"], "flagged": len(flagged),
                                                 "accessory": bool(acc_records)})


ENEMY_OPS: tuple[type[OpExecutor], ...] = (EnemyPrep, EnemyVariants)
