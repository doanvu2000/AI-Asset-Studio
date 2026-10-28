"""UI kit ops: `op.ui_split` cuts a generated UI mockup sheet into its elements, `op.nine_slice` finds each element's
9-slice borders, adds hover/pressed/disabled states and a preview where every element is drawn stretched."""
from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png

SLICES_FILE = "ui_slices.json"
SLICES_TYPE = "asset_studio.ui_slices"
STATES = ("hover", "pressed", "disabled")
STATEFUL = ("button", "tab", "slot")
Border = tuple[int, int, int, int]


def _label(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """4-connected components; the grids here are downsampled, so a plain BFS stays well under a second."""
    h, w = mask.shape
    flat = mask.ravel().tolist()
    labels = [0] * (h * w)
    n = 0
    for start in np.flatnonzero(mask.ravel()).tolist():
        if labels[start]:
            continue
        n += 1
        labels[start] = n
        q = deque([start])
        while q:
            i = q.popleft()
            y, x = divmod(i, w)
            for j, ok in ((i - 1, x > 0), (i + 1, x < w - 1), (i - w, y > 0), (i + w, y < h - 1)):
                if ok and flat[j] and not labels[j]:
                    labels[j] = n
                    q.append(j)
    return np.asarray(labels, np.int32).reshape(h, w), n


def _border_connected(mask: np.ndarray) -> np.ndarray:
    pad = np.pad(mask, 1, constant_values=True)
    labels, _ = _label(pad)
    keep = labels[0, 0]
    return (labels == keep)[1:-1, 1:-1] if keep else np.zeros_like(mask)


def has_alpha(img: Image.Image) -> bool:
    if img.mode != "RGBA":
        return False
    return float((np.asarray(img)[..., 3] < 250).mean()) > 0.01


def key_background(img: Image.Image, tol: float = 26, step: int = 2) -> Image.Image:
    """Removes the plain background a mockup sheet is drawn on: only background-coloured pixels connected to the image
    edge go, so a white panel interior enclosed by its frame stays opaque. Edge pixels get partial alpha with the
    background colour un-blended out of them."""
    rgb = np.asarray(img.convert("RGB"), np.float32)
    ring = np.concatenate([rgb[:3].reshape(-1, 3), rgb[-3:].reshape(-1, 3), rgb[:, :3].reshape(-1, 3),
                           rgb[:, -3:].reshape(-1, 3)])
    bg = np.median(ring, 0)
    dist = np.abs(rgb - bg).max(2)
    near = dist < tol
    small = near[::step, ::step]
    outside = _border_connected(small)
    outside = np.repeat(np.repeat(outside, step, 0), step, 1)[:near.shape[0], :near.shape[1]]
    # Pixels of the edge band next to the connected background may be anti-aliased element edges.
    grown = np.asarray(Image.fromarray(outside.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3))) > 0
    bgmask = grown & near
    a = np.ones(near.shape, np.float32)
    a[bgmask] = np.clip((dist[bgmask] - tol * 0.35) / (tol * 0.65), 0, 1)
    a[outside & near & (dist < tol * 0.35)] = 0
    safe = np.maximum(a, 1e-3)[..., None]
    col = np.where(a[..., None] < 1, (rgb - bg * (1 - a[..., None])) / safe, rgb)
    out = np.dstack([np.clip(col, 0, 255), a * 255]).round().astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def classify(w: int, h: int, sheet: tuple[int, int]) -> str:
    """Rough kind from shape and size on the sheet; it picks the name, the states and the preview stretch."""
    aspect = w / max(h, 1)
    if aspect >= 4 or aspect <= 0.25:
        return "bar"
    if aspect >= 1.4 and h <= 0.14 * sheet[1]:
        return "button"
    if 0.75 <= aspect <= 1.33 and max(w, h) <= 0.3 * min(sheet):
        return "slot"
    if w * h >= 0.03 * sheet[0] * sheet[1]:
        return "panel"
    return "element"


def split_elements(img: Image.Image, gap: int = 2, min_area: float = 0.002, pad: int = 2,
                   max_count: int = 48, step: int = 2) -> list[tuple[str, Image.Image, list[int]]]:
    """(name, cropped RGBA, [x0, y0, x1, y1]) per element, top-to-bottom then left-to-right."""
    rgba = img.convert("RGBA")
    alpha = np.asarray(rgba)[..., 3]
    small = Image.fromarray(((alpha > 24) * 255).astype(np.uint8)).resize(
        (max(1, rgba.width // step), max(1, rgba.height // step)), Image.Resampling.BOX)
    k = max(1, gap // step) * 2 + 1
    fg = np.asarray(small.filter(ImageFilter.MaxFilter(k))) > 40
    labels, n = _label(fg)
    up = np.repeat(np.repeat(labels, step, 0), step, 1)
    up = np.pad(up, ((0, max(0, alpha.shape[0] - up.shape[0])), (0, max(0, alpha.shape[1] - up.shape[1]))),
                mode="edge")[:alpha.shape[0], :alpha.shape[1]]
    total = alpha.shape[0] * alpha.shape[1]
    found = []
    for lab in range(1, n + 1):
        region = (up == lab) & (alpha > 24)
        if region.sum() < min_area * total:
            continue
        ys, xs = np.nonzero(region)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        if x1 - x0 < 12 or y1 - y0 < 12:
            continue
        crop = np.asarray(rgba)[y0:y1, x0:x1].copy()
        crop[..., 3] = np.where((up[y0:y1, x0:x1] == lab), crop[..., 3], 0)
        el = Image.fromarray(crop, "RGBA")
        if pad:
            canvas = Image.new("RGBA", (el.width + 2 * pad, el.height + 2 * pad), (0, 0, 0, 0))
            canvas.paste(el, (pad, pad))
            el = canvas
        found.append((el, [x0, y0, x1, y1]))
    found.sort(key=lambda f: f[1][0] * f[1][1], reverse=True)
    found = found[:max_count]
    # Elements on one visual row share a band; sort rows by top, then left to right.
    found.sort(key=lambda f: (f[1][1] + f[1][3]) / 2)
    rows: list[list[tuple[Image.Image, list[int]]]] = []
    for f in found:
        if rows and (f[1][1] + f[1][3]) / 2 < max(r[1][3] for r in rows[-1]):
            rows[-1].append(f)
        else:
            rows.append([f])
    counts: dict[str, int] = {}
    out = []
    for row in rows:
        for el, box in sorted(row, key=lambda f: f[1][0]):
            kind = classify(box[2] - box[0], box[3] - box[1], rgba.size)
            counts[kind] = counts.get(kind, 0) + 1
            out.append((f"{kind}_{counts[kind]:02d}", el, box))
    return out


def slug(text: str, fallback: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    if words and words[0] in ("a", "an", "the"):
        words = words[1:]
    return "_".join(words)[:40].strip("_") or fallback


def _premul(img: Image.Image, blur: float = 1.0) -> np.ndarray:
    a = np.asarray(img.convert("RGBA"), np.float32) / 255
    pm = np.concatenate([a[..., :3] * a[..., 3:], a[..., 3:]], 2)
    if blur <= 0:
        return pm
    # Generated art has fine grain; a light blur keeps it from breaking every run of flat columns.
    chans = [np.asarray(Image.fromarray((pm[..., c] * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(blur)), np.float32) / 255 for c in range(4)]
    return np.stack(chans, 2)


def _runs(idx: np.ndarray) -> list[tuple[int, int]]:
    """Consecutive index runs as [start, stop)."""
    if not len(idx):
        return []
    cuts = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate([[idx[0]], idx[cuts + 1]])
    stops = np.concatenate([idx[cuts], [idx[-1]]]) + 1
    return list(zip(starts.tolist(), stops.tolist()))


def _band_max(diff: np.ndarray, bands: int = 4) -> np.ndarray:
    """Per line pair, the worst of the mean changes over `bands` stretches of the line: a gem on one edge covers a
    small share of a whole column and would vanish in its mean, while painted grain still averages out."""
    chunks = np.array_split(diff, bands, axis=1) if diff.shape[1] >= 4 * bands else [diff]
    return np.max([c.mean(1) for c in chunks], axis=0)


def _stable_run(profile: np.ndarray, min_frac: float) -> tuple[int, int] | None:
    """Longest run of neighbouring lines (columns or rows) that look alike, as (first, last) line index.
    profile: lines × RGBA-interleaved values. Linear gradients still stretch cleanly, so a constant step counts as
    alike too."""
    n = profile.shape[0]
    if n < 4:
        return None
    d1 = _band_max(np.abs(np.diff(profile, axis=0)))
    step = _band_max(np.abs(np.diff(profile, n=2, axis=0)))
    base = float(np.percentile(d1, 20))
    t = float(np.clip(2.5 * base + 0.003, 0.004, 0.03))
    ok = d1 <= t
    # A gradient only changes colour; a tapering ornament also steps evenly but changes the outline (alpha).
    same_shape = np.abs(np.diff(profile[:, 3::4], axis=0)).mean(1) <= 0.004
    ok[1:] |= (step <= t * 0.5) & (d1[1:] <= 2 * t) & same_shape[1:]
    # Hairline cracks and seams in painted stone or wood would split the centre into slivers; stretched, a 1-3 px
    # line only smears a little, so short breaks between good lines are bridged.
    blip = max(2, round(0.015 * n))
    bad = np.flatnonzero(~ok)
    for start, stop in _runs(bad):
        if start > 0 and stop < len(ok) and stop - start <= blip:
            ok[start:stop] = True
    best, cur = None, None
    for i, v in enumerate(ok):
        if v:
            cur = (cur[0], i) if cur else (i, i)
            if best is None or cur[1] - cur[0] > best[1] - best[0] or (
                    cur[1] - cur[0] == best[1] - best[0]
                    and abs((cur[0] + cur[1]) / 2 - n / 2) < abs((best[0] + best[1]) / 2 - n / 2)):
                best = cur
        else:
            cur = None
    if best is None:
        return None
    first, last = best[0], best[1] + 1
    return (first, last) if last - first + 1 >= max(3, min_frac * n) else None


def nine_slice_borders(img: Image.Image, margin: int = 1, min_frac: float = 0.08) -> tuple[Border, dict[str, Any]]:
    """(left, top, right, bottom) in pixels: everything between the borders repeats line for line, so stretching it
    keeps frame, corners and ornaments crisp. An axis with no such run gets thirds and is flagged not stretchable."""
    pm = _premul(img)
    h, w = pm.shape[:2]
    cols = _stable_run(pm.transpose(1, 0, 2).reshape(w, -1), min_frac)
    rows = _stable_run(pm.reshape(h, -1), min_frac)

    def borders(run: tuple[int, int] | None, size: int) -> tuple[int, int]:
        if run is None:
            return size // 3, size // 3
        lo = min(run[0] + margin, size // 2)
        hi = min(size - 1 - run[1] + margin, size - lo - 1)
        return lo, max(0, hi)

    left, right = borders(cols, w)
    top, bottom = borders(rows, h)
    # An ornament in the middle of an edge cannot stay centred under 9-slice; the stretch then runs beside it.
    off = abs(left - right) > 0.15 * w or abs(top - bottom) > 0.15 * h
    return (left, top, right, bottom), {"stretch_x": cols is not None, "stretch_y": rows is not None,
                                        "off_center": off}


def nine_slice_scale(img: Image.Image, border: Border, size: tuple[int, int]) -> Image.Image:
    """Draws img at `size` the way an engine's sliced sprite does: corners as is, edges stretched along, centre both."""
    l, t, r, b = border
    w, h = img.size
    tw, th = max(size[0], l + r + 1), max(size[1], t + b + 1)
    out = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    xs, txs = (0, l, w - r, w), (0, l, tw - r, tw)
    ys, tys = (0, t, h - b, h), (0, t, th - b, th)
    for i in range(3):
        for j in range(3):
            sx0, sx1, sy0, sy1 = xs[j], xs[j + 1], ys[i], ys[i + 1]
            dx0, dx1, dy0, dy1 = txs[j], txs[j + 1], tys[i], tys[i + 1]
            if sx1 <= sx0 or sy1 <= sy0 or dx1 <= dx0 or dy1 <= dy0:
                continue
            patch = img.crop((sx0, sy0, sx1, sy1))
            if patch.size != (dx1 - dx0, dy1 - dy0):
                patch = patch.resize((dx1 - dx0, dy1 - dy0), Image.Resampling.BILINEAR)
            out.paste(patch, (dx0, dy0))
    return out


def make_states(img: Image.Image) -> dict[str, Image.Image]:
    """Cheap CPU states with the same canvas and borders as the normal sprite, so one 9-slice fits all four."""
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")

    def put(rgb_img: Image.Image, alpha_scale: float = 1.0) -> Image.Image:
        out = rgb_img.convert("RGBA")
        out.putalpha(alpha if alpha_scale == 1 else alpha.point(lambda v: round(v * alpha_scale)))
        return out

    hover = ImageEnhance.Color(ImageEnhance.Brightness(rgb).enhance(1.15)).enhance(1.1)
    pressed = ImageEnhance.Contrast(ImageEnhance.Brightness(rgb).enhance(0.8)).enhance(1.05)
    disabled = ImageEnhance.Brightness(ImageEnhance.Color(rgb).enhance(0.12)).enhance(0.9)
    return {"hover": put(hover), "pressed": put(pressed), "disabled": put(disabled, 0.75)}


def stretch_target(name: str, w: int, h: int, factor: float = 3) -> tuple[int, int]:
    kind = name.split("_")[0]
    if kind in ("button", "tab") or (kind == "bar" and w >= h):
        return round(w * factor), h
    if kind == "bar":
        return w, round(h * factor)
    return round(w * factor), round(h * factor)


def _checker(size: tuple[int, int], cell: int = 12) -> Image.Image:
    a = (np.indices((size[1], size[0])) // cell).sum(0) % 2
    v = np.where(a, 92, 74).astype(np.uint8)
    return Image.fromarray(np.dstack([v, v, v + 6, np.full_like(v, 255)]), "RGBA")


def preview(entries: list[dict[str, Any]], images: dict[str, Image.Image], max_w: int = 1400,
            stretch: bool = True) -> Image.Image:
    """One row per element: original, its states, then (with stretch) the 9-slice drawn at its stretch target with
    the border lines overlaid."""
    gap, label_h = 16, 16
    rows: list[list[Image.Image]] = []
    titles: list[str] = []
    for e in entries:
        if e.get("state"):
            continue
        base = images[e["name"]]
        row = [base] + [images[s] for s in e.get("states", [])]
        if stretch:
            tw, th = stretch_target(e["name"], base.width, base.height)
            big = nine_slice_scale(base, tuple(e["border"]), (tw, th))
            l, t, r, b = e["border"]
            d = ImageDraw.Draw(big)
            for x in (l, big.width - r):
                d.line([(x, 0), (x, big.height)], fill=(0, 255, 255, 160))
            for y in (t, big.height - b):
                d.line([(0, y), (big.width, y)], fill=(0, 255, 255, 160))
            row.append(big)
        rows.append(row)
        titles.append(f"{e['name']}  border L{e['border'][0]} T{e['border'][1]} R{e['border'][2]} B{e['border'][3]}"
                      if stretch else e["name"])
    if not rows:
        raise StepError("op_no_input", "no elements to preview")
    widths = [sum(i.width for i in r) + gap * (len(r) + 1) for r in rows]
    scale = min(1.0, max_w / max(widths))
    heights = [round(max(i.height for i in r) * scale) + label_h + gap for r in rows]
    width = round(max(widths) * scale) if scale < 1 else max(widths)
    canvas = _checker((max(width, 320), sum(heights) + gap))
    d = ImageDraw.Draw(canvas)
    y = gap
    for row, title, rh in zip(rows, titles, heights):
        d.text((gap, y), title, fill=(235, 235, 235, 255))
        x = gap
        for im_ in row:
            s = im_ if scale == 1 else im_.resize((max(1, round(im_.width * scale)), max(1, round(im_.height * scale))),
                                                  Image.Resampling.LANCZOS)
            canvas.alpha_composite(s, (x, y + label_h))
            x += s.width + gap
        y += rh
    return canvas


class UiSplit(OpExecutor):
    kind = "op.ui_split"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        mode = str(params.get("background") or "auto")
        tol = float(params.get("tolerance", 26))

        def work(src: Image.Image) -> tuple[Image.Image, list[tuple[str, Image.Image, list[int]]]]:
            keyed = src if (mode == "alpha" or (mode == "auto" and has_alpha(src))) else key_background(src, tol)
            return keyed, split_elements(keyed, int(params.get("gap", 2)), float(params.get("min_area", 0.002)),
                                         int(params.get("pad", 2)), int(params.get("max_count", 48)))

        out: list[BlobRef] = []
        keyed_refs: list[BlobRef] = []
        boxes: dict[str, list[int]] = {}
        for k, ref in enumerate(refs):
            keyed, parts = await asyncio.to_thread(work, await _load(ctx, ref))
            # Several picked sheets: the kind stays the first word of the name, which picks states and stretch.
            tag = f"_s{k + 1}" if len(refs) > 1 else ""
            for name, el, box in parts:
                out.append(await _put_png(ctx, el, f"{name}{tag}.png"))
                boxes[f"{name}{tag}"] = box
            keyed_refs.append(await _put_png(ctx, keyed, f"ui_sheet{tag}_keyed.png"))
            ctx.progress(k + 1, len(refs))
        if not out:
            raise StepError("ui_no_elements", "no UI elements found on the sheet (background not plain?)")
        return StepResult(outputs={"images": out, "keyed": keyed_refs}, meta={"count": len(out), "boxes": boxes})


class NineSlice(OpExecutor):
    kind = "op.nine_slice"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        do_slice = bool(params.get("slice", True))
        states_for = str(params.get("states", "auto")).lower()
        margin = int(params.get("margin", 1))
        given = params.get("names") or []
        given = [ln for ln in (given.splitlines() if isinstance(given, str) else map(str, given)) if ln.strip()]
        images: dict[str, Image.Image] = {}
        entries: list[dict[str, Any]] = []
        out: list[BlobRef] = []
        seen: set[str] = set()
        for i, ref in enumerate(refs):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            img = await _load(ctx, ref)
            name = (slug(given[i], f"icon_{i + 1:02d}") if i < len(given)
                    else (ref.name or f"element_{i + 1:02d}").rsplit(".", 1)[0])
            while name in seen:
                name += "_b"
            seen.add(name)
            if do_slice:
                border, info = await asyncio.to_thread(nine_slice_borders, img, margin)
            else:
                border, info = (0, 0, 0, 0), {"stretch_x": False, "stretch_y": False, "off_center": False}
            images[name] = img
            entry: dict[str, Any] = {"name": name, "file": f"{name}.png", "w": img.width, "h": img.height,
                                     "border": list(border), **info, "states": []}
            base_ref = ref if ref.name == entry["file"] else await _put_png(ctx, img, entry["file"])
            entry["hash"] = base_ref.hash
            out.append(base_ref)
            entries.append(entry)
            wants = states_for == "all" or (states_for == "auto" and do_slice and name.split("_")[0] in STATEFUL)
            if wants:
                for state, simg in (await asyncio.to_thread(make_states, img)).items():
                    sname = f"{name}_{state}"
                    sref = await _put_png(ctx, simg, f"{sname}.png")
                    images[sname] = simg
                    entry["states"].append(sname)
                    entries.append({"name": sname, "file": f"{sname}.png", "w": img.width, "h": img.height,
                                    "border": list(border), **info, "state": state, "state_of": name,
                                    "states": [], "hash": sref.hash})
                    out.append(sref)
            ctx.progress(i + 1, len(refs))
        doc = {"type": SLICES_TYPE, "sliced": do_slice, "elements": entries}
        slices_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=SLICES_FILE,
                                               mime="application/json")
        prev = await asyncio.to_thread(preview, entries, images, 1400, do_slice)
        prev_ref = await _put_png(ctx, prev, "ui_preview.png" if do_slice else "icons_preview.png")
        flat = sum(1 for e in entries if not e.get("state") and not (e["stretch_x"] or e["stretch_y"]))
        return StepResult(outputs={"images": out, "slices": [slices_ref], "preview": [prev_ref]},
                          meta={"elements": sum(1 for e in entries if not e.get("state")), "files": len(out),
                                "not_stretchable": flat if do_slice else None,
                                "off_center": sum(1 for e in entries if not e.get("state") and e.get("off_center"))})


UI_OPS = (UiSplit, NineSlice)
