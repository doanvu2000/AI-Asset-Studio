"""Damage states + debris: `op.damage_prep` (white Klein reference of the intact prop), `op.damage_states` (Klein
edits put back on the source canvas, nudged to best overlap the intact silhouette), `op.damage_debris` (splits one
state into RGBA pieces from SAM3 masks or a CPU crack cut, white hit-flash frames, strip, damage.json, preview)."""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json
from .fx import dilate
from .parts import grow_labels
from .rarity import LUM, REF_SIZE, alpha_iou, reference, unref

DAMAGE_FILE = "damage.json"
DAMAGE_TYPE = "asset-studio/damage-states"
LAYOUT_FILE = "damage_layout.json"
INFO_FILE = "damage_states.json"
DEFAULT_STATES = ("intact", "cracked", "broken", "wrecked")
WORK = 256


def parse_states(raw: Any) -> list[str]:
    text = str(raw or "").replace("\n", ",")
    names = ["_".join(t.split()) for t in text.split(",") if t.strip()] or list(DEFAULT_STATES)
    if len(names) != len(DEFAULT_STATES):
        raise StepError("op_bad_params", f"need exactly {len(DEFAULT_STATES)} state names, got {len(names)}")
    if len({n.lower() for n in names}) != len(names):
        raise StepError("op_bad_params", "state names must be unique")
    return names


def debris_index(raw: Any, states: list[str]) -> int:
    key = str(raw or "broken").strip().lower()
    for i, s in enumerate(states):
        if s.lower() == key:
            return max(i, 1)
    if key in DEFAULT_STATES:
        return max(DEFAULT_STATES.index(key), 1)
    try:
        return min(max(int(key), 1), len(states) - 1)
    except ValueError as e:
        raise StepError("op_bad_params", f"debris_from must be a state name or index, got {raw!r}") from e


def _shift_fill(a: np.ndarray, dy: int, dx: int, fill: Any = 0) -> np.ndarray:
    out = np.full_like(a, fill)
    h, w = a.shape[:2]
    out[max(dy, 0):h + min(dy, 0), max(dx, 0):w + min(dx, 0)] = a[max(-dy, 0):h - max(dy, 0), max(-dx, 0):w - max(dx, 0)]
    return out


def align_shift(ref_alpha: np.ndarray, alpha: np.ndarray, max_frac: float = 0.06) -> tuple[int, int]:
    """(dx, dy) in canvas pixels that best overlaps `alpha` onto `ref_alpha` (IoU over a coarse grid search)."""
    h, w = ref_alpha.shape
    s = min(1.0, 128 / max(h, w))
    size = (max(1, round(w * s)), max(1, round(h * s)))
    a = np.asarray(Image.fromarray(ref_alpha).resize(size, Image.Resampling.BOX)) > 127
    b = np.asarray(Image.fromarray(alpha).resize(size, Image.Resampling.BOX)) > 127
    if not a.any() or not b.any():
        return 0, 0
    r = max(1, math.ceil(max(size) * max_frac))
    best, best_iou = (0, 0), -1.0
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            m = _shift_fill(b, dy, dx, False)
            iou = (a & m).sum() / max((a | m).sum(), 1)
            if iou > best_iou + 1e-9 or (abs(iou - best_iou) <= 1e-9 and abs(dx) + abs(dy) < sum(map(abs, best))):
                best, best_iou = (dx, dy), iou
    return round(best[0] / s), round(best[1] / s)


def translate(img: Image.Image, dx: int, dy: int) -> Image.Image:
    if not dx and not dy:
        return img
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), (dx, dy))
    return out


def clip_to(img: Image.Image, orig_alpha: np.ndarray, grow: int = 2) -> Image.Image:
    """Damage only removes material: alpha may not grow past the intact silhouette (plus a small margin)."""
    rgba = np.asarray(img.convert("RGBA")).copy()
    limit = dilate(orig_alpha, grow, plus_first=True) if grow > 0 else orig_alpha
    rgba[..., 3] = np.minimum(rgba[..., 3], limit)
    return Image.fromarray(rgba, "RGBA")


def hit_flash(img: Image.Image, amount: float = 1.0) -> Image.Image:
    rgba = np.asarray(img.convert("RGBA")).astype(np.float32)
    rgba[..., :3] += (255 - rgba[..., :3]) * float(np.clip(amount, 0, 1))
    return Image.fromarray(np.clip(np.round(rgba), 0, 255).astype(np.uint8), "RGBA")


def components(mask: np.ndarray) -> np.ndarray:
    """4-connected labels 1..K (0 = background) by min-label propagation; fine for the ≤256 px work grid."""
    h, w = mask.shape
    big = h * w + 1
    lab = np.where(mask, np.arange(1, h * w + 1, dtype=np.int64).reshape(h, w), big)
    while True:
        m = lab
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            m = np.minimum(m, _shift_fill(lab, dy, dx, big))
        m = np.where(mask, m, big)
        if np.array_equal(m, lab):
            break
        lab = m
    out = np.zeros((h, w), np.int32)
    if mask.any():
        _, inv = np.unique(lab[mask], return_inverse=True)
        out[mask] = inv + 1
    return out


def crack_mask(rgba: np.ndarray, alpha: np.ndarray, sensitivity: float = 0.5) -> np.ndarray:
    """Dark thin lines inside the object (cracks, plank seams, outlines): darker than their blurred surroundings."""
    luma = (rgba[..., :3].astype(np.float32) / 255.0) @ LUM
    a = alpha.astype(np.float32)
    lum_img = Image.fromarray(np.clip(luma * a * 255, 0, 255).astype(np.uint8))
    a_img = Image.fromarray((a * 255).astype(np.uint8))
    rad = max(2.0, max(alpha.shape) / 80)
    num = np.asarray(lum_img.filter(ImageFilter.GaussianBlur(rad)), np.float32)
    den = np.asarray(a_img.filter(ImageFilter.GaussianBlur(rad)), np.float32)
    local = num / np.maximum(den, 1.0)
    delta = 0.03 + (1 - float(np.clip(sensitivity, 0, 1))) * 0.15
    return alpha & (luma < local - delta) & (luma < 0.55)


def _nearest_fill(labels: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    lab = grow_labels(labels, alpha)
    rest = alpha & (lab == 0)
    ids = [i for i in np.unique(lab) if i > 0]
    if rest.any() and ids:
        cents = np.array([np.argwhere(lab == i).mean(0) for i in ids])
        pts = np.argwhere(rest)
        d = ((pts[:, None, :] - cents[None]) ** 2).sum(-1)
        lab[rest] = np.asarray(ids)[d.argmin(1)]
    return lab


def _split_largest(lab: np.ndarray, k: int, jag: float = 0.08) -> bool:
    ids, counts = np.unique(lab[lab > 0], return_counts=True)
    if not len(ids):
        return False
    big = int(ids[counts.argmax()])
    pts = np.argwhere(lab == big).astype(np.float32)
    if len(pts) < 8:
        return False
    c = pts.mean(0)
    cov = np.cov((pts - c).T)
    _, vecs = np.linalg.eigh(cov)
    major, minor = vecs[:, -1], vecs[:, 0]
    along, across = (pts - c) @ major, (pts - c) @ minor
    ext = max(float(np.ptp(across)), 1.0)
    # A wavy cut reads as a break; a ruler-straight one looks like a slice.
    cut = jag * ext * np.sin(across / ext * math.pi * 3 + k)
    side = along > cut
    if side.all() or not side.any():
        return False
    sel = pts[side].astype(np.int64)
    lab[sel[:, 0], sel[:, 1]] = int(lab.max()) + 1
    return True


def finalize(seeds: np.ndarray, alpha: np.ndarray, min_n: int, max_n: int, min_area: float) -> np.ndarray:
    """Keeps the largest seeds ≥ min_area (fraction of the object) up to max_n, grows them over every opaque pixel
    so the pieces tile the state exactly, then splits the largest piece until there are min_n."""
    total = max(int(alpha.sum()), 1)
    ids, counts = np.unique(seeds[(seeds > 0) & alpha], return_counts=True)
    order = [int(i) for i, n in sorted(zip(ids, counts), key=lambda t: -t[1]) if n >= min_area * total][:max_n]
    lab = np.zeros(seeds.shape, np.int32)
    for new, old in enumerate(order, 1):
        lab[(seeds == old) & alpha] = new
    if not order:
        lab[alpha] = 1
    lab = _nearest_fill(lab, alpha)
    k = 0
    while len(np.unique(lab[lab > 0])) < min_n and _split_largest(lab, k):
        k += 1
    out = np.zeros(lab.shape, np.int32)
    _, inv = np.unique(lab[lab > 0], return_inverse=True)
    out[lab > 0] = inv + 1
    return out


def _work(img: Image.Image) -> tuple[Image.Image, float]:
    s = min(1.0, WORK / max(img.size))
    if s >= 1.0:
        return img, 1.0
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.Resampling.BOX), s


def seeds_from_masks(masks: list[np.ndarray], alpha: np.ndarray) -> np.ndarray:
    """Overlapping detections: smaller masks win, so a whole-object hit cannot swallow the pieces inside it."""
    lab = np.zeros(alpha.shape, np.int32)
    for i, m in sorted(enumerate(masks), key=lambda t: -int(t[1].sum())):
        lab[m & alpha] = i + 1
    return lab


def label_pieces(state: Image.Image, masks: list[np.ndarray] | None, min_n: int, max_n: int, min_area: float,
                 sensitivity: float = 0.5) -> tuple[np.ndarray, str]:
    """Full-resolution piece labels for one state. `masks` are canvas-sized bools (SAM3); None = CPU crack cut."""
    small, s = _work(state.convert("RGBA"))
    rgba = np.asarray(small)
    alpha = rgba[..., 3] > 127
    if not alpha.any():
        raise StepError("op_bad_image", "the debris state is empty (no opaque pixels)")
    method = "cpu"
    seeds = None
    if masks:
        ms = [np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize(small.size, Image.Resampling.BOX)) > 127
              for m in masks]
        seeds = seeds_from_masks(ms, alpha)
        total = alpha.sum()
        if sum(1 for i in np.unique(seeds) if i > 0 and (seeds == i).sum() >= min_area * total) >= 2:
            method = "sam3"
        else:
            seeds = None
    if seeds is None:
        seeds = components(alpha & ~crack_mask(rgba, alpha, sensitivity))
    lab = finalize(seeds, alpha, min_n, max_n, min_area)
    full_alpha = np.asarray(state.convert("RGBA"))[..., 3] > 0
    up = np.asarray(Image.fromarray(lab.astype(np.uint8)).resize(state.size, Image.Resampling.NEAREST)).astype(np.int32)
    up[~full_alpha] = 0
    return _nearest_fill(up, full_alpha), method


def cut_pieces(state: Image.Image, labels: np.ndarray) -> list[tuple[Image.Image, dict[str, Any]]]:
    """One cropped RGBA per label + where it sits: rect/center/centroid in canvas px (top-left origin, y down),
    offset of its center from the canvas center, pivot = centroid inside the piece (0..1), outward direction."""
    rgba = np.asarray(state.convert("RGBA"))
    h, w = labels.shape
    obj = np.argwhere(labels > 0)
    oy, ox = obj.mean(0) if len(obj) else (h / 2, w / 2)
    total = max(len(obj), 1)
    out = []
    for lid in [int(i) for i in np.unique(labels) if i > 0]:
        m = labels == lid
        ys, xs = np.nonzero(m)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        crop = rgba[y0:y1, x0:x1].copy()
        crop[..., 3] = np.where(m[y0:y1, x0:x1], crop[..., 3], 0)
        if not crop[..., 3].any():
            continue
        my, mx = float(ys.mean()), float(xs.mean())
        d = np.array([mx - ox, my - oy], np.float64)
        n = float(np.hypot(*d))
        dirv = (d / n) if n > 1e-6 else np.array([0.0, -1.0])
        bw, bh = x1 - x0, y1 - y0
        info = {"rect": [x0, y0, bw, bh], "center": [x0 + bw / 2, y0 + bh / 2],
                "offset": [round(x0 + bw / 2 - w / 2, 2), round(y0 + bh / 2 - h / 2, 2)],
                "centroid": [round(mx, 2), round(my, 2)],
                "pivot": [round((mx - x0) / bw, 4), round((my - y0) / bh, 4)],
                "area": int(m.sum()), "mass": round(float(m.sum()) / total, 4),
                "dir": [round(float(dirv[0]), 4), round(float(dirv[1]), 4)]}
        out.append((Image.fromarray(crop, "RGBA"), info))
    out.sort(key=lambda t: (t[1]["centroid"][1], t[1]["centroid"][0]))
    return out


def strip(images: list[Image.Image]) -> Image.Image:
    w, h = images[0].size
    out = Image.new("RGBA", (w * len(images), h), (0, 0, 0, 0))
    for i, im in enumerate(images):
        out.alpha_composite(im.convert("RGBA"), (i * w, 0))
    return out


def exploded(canvas: tuple[int, int], pieces: list[tuple[Image.Image, dict[str, Any]]], spread: float = 0.25
             ) -> Image.Image:
    w, h = canvas
    pad = round(max(w, h) * spread)
    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    for im, info in pieces:
        x0, y0 = info["rect"][:2]
        dx, dy = info["dir"]
        out.alpha_composite(im, (round(x0 + pad + dx * pad * 0.8), round(y0 + pad + dy * pad * 0.8)))
    return out


def _tile(img: Image.Image, cw: int, ch: int, bg: tuple[int, int, int]) -> Image.Image:
    im = img.convert("RGBA")
    s = min(cw / im.width, ch / im.height)
    im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (cw, ch), (*bg, 255))
    tile.alpha_composite(im, ((cw - im.width) // 2, (ch - im.height) // 2))
    return tile.convert("RGB")


def preview(states: list[str], images: list[Image.Image], hit: Image.Image, ious: list[float | None],
            boom: Image.Image, n_pieces: int, debris_state: str, method: str, cell: int = 192) -> Image.Image:
    gap, label = 8, 34
    cols = len(images) + 1
    width = gap + cols * (cell + gap)
    boom_h = cell * 2
    out = Image.new("RGB", (width, cell + label + gap * 3 + boom_h + 20), (24, 24, 28))
    d = ImageDraw.Draw(out)
    for i, (img, name) in enumerate([*zip(images, states), (hit, "hit flash")]):
        x = gap + i * (cell + gap)
        out.paste(_tile(img, cell, cell, (46, 46, 52)), (x, gap))
        d.text((x + 2, gap + cell + 4), name, fill=(220, 220, 220))
        iou = ious[i] if i < len(ious) else None
        if iou is not None:
            d.text((x + 2, gap + cell + 18), f"IoU {iou:.2f}", fill=(170, 170, 170))
    y = gap * 2 + cell + label
    d.text((gap, y), f"debris: {n_pieces} pieces from '{debris_state}' ({method})", fill=(255, 190, 90))
    out.paste(_tile(boom, width - 2 * gap, boom_h, (46, 46, 52)), (gap, y + 16))
    return out


class DamagePrep(OpExecutor):
    kind = "op.damage_prep"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        states = parse_states(params.get("states"))
        ref = _images_in(inputs)[0]
        src = (await _load(ctx, ref)).convert("RGBA")
        if not np.asarray(src)[..., 3].any():
            raise StepError("op_bad_image", "the prop image is fully transparent")
        stem = (ref.name or "prop").rsplit(".", 1)[0]
        white, box = await asyncio.to_thread(reference, src)
        base = await _put_png(ctx, src, f"{stem}_{states[0]}.png")
        ref_out = await _put_png(ctx, white, f"{stem}_ref.png")
        doc = {"stem": stem, "canvas": list(src.size), "ref_size": REF_SIZE, "box": box, "states": states}
        lay = await ctx.blobs.put_bytes(json.dumps(doc).encode(), name=LAYOUT_FILE, mime="application/json")
        return StepResult(outputs={"images": [base], "ref": [ref_out], "layout": [lay]},
                          meta={"states": states, "canvas": list(src.size)})


class DamageStates(OpExecutor):
    kind = "op.damage_states"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        base = _images_in(inputs)[0]
        edits = _images_in(inputs, "edits")
        raws = inputs.get("raw") or []
        lays = inputs.get("layout") or []
        if not lays:
            raise StepError("op_no_input", "input 'layout' is empty")
        lay = await _read_json(ctx, lays[0])
        states = list(lay["states"])
        n = len(states) - 1
        if len(edits) < n:
            raise StepError("op_no_input", f"need {n} edited states, got {len(edits)}")
        try:
            max_shift = float(params.get("max_shift", 0.06))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        do_align = str(params.get("align", True)).lower() not in ("false", "0", "no")
        do_clip = str(params.get("clip", True)).lower() not in ("false", "0", "no")
        idx = debris_index(params.get("debris_from"), states)
        orig = (await _load(ctx, base)).convert("RGBA")
        oa = np.asarray(orig)[..., 3]
        imgs, shifts = [orig], [[0, 0]]
        for i, r in enumerate(edits[:n]):
            ctx.progress(i, n, label=states[i + 1])
            img = unref(await _load(ctx, r), lay)
            dx, dy = (0, 0)
            if do_align:
                dx, dy = await asyncio.to_thread(align_shift, oa, np.asarray(img)[..., 3], max_shift)
            img = translate(img, dx, dy)
            if do_clip:
                img = await asyncio.to_thread(clip_to, img, oa)
            imgs.append(img)
            shifts.append([dx, dy])
        ious: list[float | None] = [None] + [await asyncio.to_thread(alpha_iou, orig, im) for im in imgs[1:]]
        stem = str(lay.get("stem") or "prop")
        out = [base] + [await _put_png(ctx, im, f"{stem}_{s}.png") for s, im in zip(states[1:], imgs[1:])]
        # SAM3 sees the raw Klein edit (white background, reference framing); masks map back with layout + shift.
        if len(raws) >= n:
            dsrc, src_shift = raws[idx - 1], shifts[idx]
        else:
            white, _ = await asyncio.to_thread(reference, imgs[idx])
            dsrc, src_shift = await _put_png(ctx, white, f"{stem}_{states[idx]}_ref.png"), [0, 0]
        doc = {**lay, "shifts": shifts, "ious": ious, "debris": idx, "src_shift": src_shift,
               "aligned": do_align, "clipped": do_clip}
        info = await ctx.blobs.put_bytes(json.dumps(doc).encode(), name=INFO_FILE, mime="application/json")
        ctx.progress(n, n)
        return StepResult(outputs={"images": out, "debris_src": [dsrc], "info": [info]},
                          meta={"states": states, "shifts": shifts,
                                "iou": {s: v for s, v in zip(states, ious) if v is not None},
                                "debris_from": states[idx]})


class DamageDebris(OpExecutor):
    kind = "op.damage_debris"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        infos = inputs.get("info") or []
        if not infos:
            raise StepError("op_no_input", "input 'info' is empty")
        info = await _read_json(ctx, infos[0])
        states = list(info["states"])
        if len(refs) < len(states):
            raise StepError("op_no_input", f"need {len(states)} state images, got {len(refs)}")
        try:
            min_n = int(params.get("min_pieces", 4))
            max_n = max(min_n, int(params.get("max_pieces", 8)))
            min_area = float(params.get("min_area", 0.01))
            sens = float(params.get("crack", 0.5))
            flash = float(params.get("flash", 1.0))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        split = str(params.get("split") or "auto").lower()
        idx = int(info.get("debris", 2))
        imgs = [(await _load(ctx, r)).convert("RGBA") for r in refs[:len(states)]]
        canvas = imgs[0].size
        masks: list[np.ndarray] | None = None
        if split != "cpu":
            dx, dy = (int(v) for v in info.get("src_shift") or (0, 0))
            masks = []
            for r in inputs.get("masks") or []:
                m = unref((await _load(ctx, r)).convert("L").convert("RGBA"), info)
                masks.append(np.asarray(translate(m, dx, dy).convert("L")) > 127)
            if not masks:
                if split == "sam3":
                    ctx.log("SAM3 returned no masks; falling back to the CPU crack cut", "warning")
                masks = None
        labels, method = await asyncio.to_thread(label_pieces, imgs[idx], masks, min_n, max_n, min_area, sens)
        if split == "sam3" and method != "sam3":
            ctx.log("SAM3 masks did not give at least 2 usable pieces; used the CPU crack cut", "warning")
        pieces = await asyncio.to_thread(cut_pieces, imgs[idx], labels)
        stem = str(info.get("stem") or "prop")
        hits = [await asyncio.to_thread(hit_flash, im, flash) for im in imgs]
        state_entries, hit_refs = [], []
        for i, (name, r, h) in enumerate(zip(states, refs, hits)):
            hr = await _put_png(ctx, h, f"{stem}_{name}_hit.png")
            hit_refs.append(hr)
            state_entries.append({"state": name, "rank": i, "file": r.name, "hash": r.hash,
                                  "hit": {"file": hr.name, "hash": hr.hash},
                                  "shift": (info.get("shifts") or [[0, 0]] * len(states))[i],
                                  "iou": (info.get("ious") or [None] * len(states))[i]})
        piece_refs, piece_entries = [], []
        for k, (im, pinfo) in enumerate(pieces):
            ctx.progress(k, len(pieces), label="pieces")
            pr = await _put_png(ctx, im, f"{stem}_piece_{k}.png")
            piece_refs.append(pr)
            piece_entries.append({"name": f"piece_{k}", "file": pr.name, "hash": pr.hash, **pinfo})
        strip_img = await asyncio.to_thread(strip, imgs)
        strip_ref = await _put_png(ctx, strip_img, f"{stem}_damage_strip.png")
        boom = await asyncio.to_thread(exploded, canvas, pieces)
        prev_img = await asyncio.to_thread(preview, states, imgs, hits[0], list(info.get("ious") or []), boom,
                                           len(pieces), states[idx], method)
        prev = await _put_png(ctx, prev_img, f"{stem}_damage_preview.png")
        doc = {"type": DAMAGE_TYPE, "asset": stem, "canvas": list(canvas), "states": state_entries,
               "strip": {"file": strip_ref.name, "hash": strip_ref.hash, "order": states},
               "debris": {"from": states[idx], "method": method, "pieces": piece_entries}}
        dj = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=DAMAGE_FILE, mime="application/json")
        ctx.progress(len(pieces), len(pieces))
        return StepResult(outputs={"images": list(refs[:len(states)]), "hit": hit_refs, "pieces": piece_refs,
                                   "strip": [strip_ref], "damage": [dj], "preview": [prev]},
                          meta={"states": states, "pieces": len(pieces), "method": method,
                                "debris_from": states[idx]})


DAMAGE_OPS: tuple[type[OpExecutor], ...] = (DamagePrep, DamageStates, DamageDebris)
