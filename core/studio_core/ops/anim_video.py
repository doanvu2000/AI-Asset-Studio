"""Sprite animation from one image through Wan 2.2 TI2V (recipe sprite-anim-video). `op.anim_prep` puts the character
on a flat backdrop sized for the model; `op.anim_candidates` splits the seeds' clips into one contact strip each for
gate.pick; `op.anim_pick` keeps the picked clip's frames; `op.anim_loop` finds the cycle (loop) or the action span
(one-shot), crossfades the seam, locks the feet/pivot, removes bbox jitter and cuts equal cells plus the clip layout
op.sheet turns into Unity/Godot/Aseprite clips."""
from __future__ import annotations

import asyncio
import math
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json
from .keyart import FPS, frame_count, grid_size
from .rig import _put_json

ACTIONS: dict[str, dict[str, Any]] = {
    "idle": {"motion": "breathes slowly in place, chest rising and falling, a slight sway, the weapon held still",
             "loop": True, "lock": "feet", "fps": 8},
    "walk": {"motion": "walks in place with a steady stepping cycle, legs alternating and arms swinging, the body does "
                       "not travel across the frame", "loop": True, "lock": "feet", "fps": 10},
    "run": {"motion": "runs in place with a fast running cycle, knees high and arms pumping, the body does not travel "
                      "across the frame", "loop": True, "lock": "feet", "fps": 12},
    "slash": {"motion": "winds up and swings the weapon in one fast slash forward, follows through, then returns to "
                        "the starting stance", "loop": False, "lock": "feet", "fps": 12},
    "jump": {"motion": "crouches, jumps straight up, then lands back on the same spot and straightens up",
             "loop": False, "lock": "x", "fps": 12},
    "hit": {"motion": "gets hit, flinches and recoils backward, then recovers to the starting stance",
            "loop": False, "lock": "feet", "fps": 12},
    "cast": {"motion": "raises one hand and casts a spell with a short glowing burst, then lowers the hand",
             "loop": False, "lock": "feet", "fps": 10},
    "death": {"motion": "staggers, collapses to the ground and lies still", "loop": False, "lock": "ground", "fps": 10},
    "custom": {"motion": "", "loop": True, "lock": "feet", "fps": 10},
}
# Studio angle convention (export/sheet.py): 0 = facing the viewer, 90 = left, 270 = right.
FACINGS: dict[str, dict[str, Any]] = {
    "right": {"angle": 270, "text": "right"}, "left": {"angle": 90, "text": "left"},
    "front": {"angle": 0, "text": "the viewer"}, "back": {"angle": 180, "text": "away from the viewer"},
}
LOCKS = ("feet", "ground", "x", "none")
# Wan 2.2 TI2V 5B pixel budgets; a square canvas at 480p keeps a standing character big enough to animate cleanly.
AREAS = {"480p": 640 * 640, "540p": 736 * 736, "720p": 960 * 960}
BACKDROPS = {"grey": (128, 128, 128), "green": (0, 177, 64)}
ALPHA_ON = 32
MAX_PICK_FRAMES = 49
SIM_SIDE = 48


def resolve_loop(action: str, loop: Any) -> bool:
    v = str(loop or "auto").lower()
    if v in ("loop", "true", "1", "yes"):
        return True
    if v in ("oneshot", "false", "0", "no"):
        return False
    return bool(ACTIONS.get(action, ACTIONS["custom"])["loop"])


def parse_color(v: Any) -> tuple[int, int, int]:
    s = str(v or "grey").strip().lower()
    if s in BACKDROPS:
        return BACKDROPS[s]
    s = s.lstrip("#")
    if len(s) == 6:
        try:
            return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        except ValueError:
            pass
    raise StepError("op_bad_param", f"backdrop must be grey, green or #rrggbb, got {v!r}")


def subject_box(img: Image.Image) -> tuple[int, int, int, int]:
    rgba = img.convert("RGBA")
    a = np.asarray(rgba)[..., 3]
    if a.min() >= 250:
        return 0, 0, img.width, img.height
    ys, xs = np.nonzero(a > ALPHA_ON)
    if not len(xs):
        raise StepError("op_bad_input", "the image is fully transparent")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def prep_canvas(img: Image.Image, area: int, bg: tuple[int, int, int], fill: float = 0.6, ground: float = 0.86,
                max_width: float = 0.5) -> tuple[Image.Image, dict[str, Any]]:
    """Trimmed character, scaled to `fill` of the canvas height (or `max_width` of its width), feet on the `ground`
    line, centred: head-room for jumps and side room for weapon swings."""
    w, h = grid_size(1, 1, area)
    box = subject_box(img)
    sub = img.convert("RGBA").crop(box)
    scale = min(fill * h / sub.height, max_width * w / sub.width)
    sw, sh = max(1, round(sub.width * scale)), max(1, round(sub.height * scale))
    sub = sub.resize((sw, sh), Image.Resampling.LANCZOS)
    x0, y1 = (w - sw) // 2, round(ground * h)
    y0 = y1 - sh
    canvas = Image.new("RGBA", (w, h), (*bg, 255))
    canvas.alpha_composite(sub, (x0, y0))
    info = {"width": w, "height": h, "bbox": [x0, y0, x0 + sw, y1], "scale": round(scale, 4),
            "backdrop": "#{:02x}{:02x}{:02x}".format(*bg), "source": [img.width, img.height], "source_box": list(box)}
    return canvas.convert("RGB"), info


def to_rgba(img: Image.Image, bg: tuple[int, int, int] | None, tol: float = 48.0) -> np.ndarray:
    """RGBA array; an opaque frame (no matting yet) gets alpha from its distance to the flat backdrop."""
    a = np.asarray(img.convert("RGBA")).copy()
    if bg is not None and a[..., 3].min() >= 250:
        d = np.sqrt(((a[..., :3].astype(np.float32) - np.asarray(bg, np.float32)) ** 2).sum(-1))
        a[..., 3] = np.clip((d - tol * 0.5) / (tol * 0.5), 0, 1) * 255
    return a


def mask_of(a: np.ndarray) -> np.ndarray:
    return a[..., 3] > ALPHA_ON


def anchor(mask: np.ndarray, lock: str = "feet") -> tuple[float, float]:
    """Pivot point: the ground line (lowest row, a stray speck aside) under the torso. The torso x comes from the
    middle height band, not the feet, which would jump from foot to foot in a walk."""
    rows = mask.sum(1)
    on = np.nonzero(rows >= 2)[0]
    if not len(on):
        return math.nan, math.nan
    top, bottom = int(on[0]), int(on[-1])
    h = bottom - top + 1
    cols = mask[top + h // 4:top + 3 * h // 4 + 1].sum(0).astype(np.float64)
    if cols.max() <= 0:
        cols = mask.sum(0).astype(np.float64)
    # Columns at least half as tall as the densest one are the torso; a fist or blade passing by never is.
    core = np.where(cols >= 0.5 * cols.max(), cols, 0)
    return float((core * np.arange(len(core))).sum() / core.sum()), float(bottom)


def _lower_band(mask: np.ndarray, bottom: int, rows: int) -> np.ndarray:
    top = bottom - rows + 1
    band = mask[max(top, 0):bottom + 1]
    if top < 0:
        band = np.concatenate([np.zeros((-top, mask.shape[1]), bool), band])
    return band[::2, ::2]


def track(masks: list[np.ndarray], search: float = 0.15) -> np.ndarray:
    """(n, 2) pivot per frame. y is the ground line; x is the first frame's torso x plus the horizontal shift that best
    overlays each frame's silhouette (ground lines matched) on the first frame's. A per-frame torso estimate swings
    with a raised arm or a sword (tens of px in a slash); the overlap is dominated by head, torso and legs."""
    out = np.full((len(masks), 2), np.nan)
    ref_i = next((i for i, m in enumerate(masks) if m.any()), None)
    if ref_i is None:
        return out
    x0, b0 = anchor(masks[ref_i])
    rows_on = np.nonzero(masks[ref_i].sum(1) >= 2)[0]
    rows = rows_on[-1] - rows_on[0] + 1
    ref = _lower_band(masks[ref_i], int(b0), rows)
    reach = max(2, int(search * masks[ref_i].shape[1] / 2))
    for i, m in enumerate(masks):
        x, b = anchor(m)
        if math.isnan(x):
            continue
        band = _lower_band(m, int(b), rows)
        best, best_dx = -1, 0
        for dx in range(-reach, reach + 1):
            score = int((np.roll(band, -dx, 1) & ref).sum())
            if score > best or (score == best and abs(dx) < abs(best_dx)):
                best, best_dx = score, dx
        out[i] = (x0 + 2 * best_dx, b)
    return out


def _smooth(v: np.ndarray, circular: bool) -> np.ndarray:
    if len(v) < 3:
        return v.copy()
    p = np.concatenate([v[-1:], v, v[:1]]) if circular else np.concatenate([v[:1], v, v[-1:]])
    return 0.25 * p[:-2] + 0.5 * p[1:-1] + 0.25 * p[2:]


def _fill_nan(v: np.ndarray) -> np.ndarray:
    ok = ~np.isnan(v)
    if not ok.any():
        return np.zeros_like(v)
    idx = np.arange(len(v))
    return np.interp(idx, idx[ok], v[ok])


def lock_targets(anchors: np.ndarray, lock: str, circular: bool) -> np.ndarray:
    """Where each frame's anchor should sit: locked axes at their median, free axes on a smoothed path (removes the
    per-frame jitter of matting/sampling, keeps the real motion such as a jump)."""
    ax, ay = _fill_nan(anchors[:, 0]), _fill_nan(anchors[:, 1])
    lock_x = lock in ("feet", "x")
    lock_y = lock in ("feet", "ground")
    tx = np.full_like(ax, np.median(ax)) if lock_x else _smooth(ax, circular)
    ty = np.full_like(ay, np.median(ay)) if lock_y else _smooth(ay, circular)
    return np.stack([tx, ty], 1)


def shift(a: np.ndarray, dx: int, dy: int) -> np.ndarray:
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    xs0, xd0 = (0, dx) if dx >= 0 else (-dx, 0)
    ys0, yd0 = (0, dy) if dy >= 0 else (-dy, 0)
    cw, ch = w - abs(dx), h - abs(dy)
    if cw > 0 and ch > 0:
        out[yd0:yd0 + ch, xd0:xd0 + cw] = a[ys0:ys0 + ch, xs0:xs0 + cw]
    return out


def _box_mean(x: np.ndarray, r: int) -> np.ndarray:
    """Mean over (2r+1)^2 windows ('valid' region) of the last two axes, via integral images."""
    k = 2 * r + 1
    c = np.cumsum(np.cumsum(np.pad(x, [(0, 0)] * (x.ndim - 2) + [(1, 0), (1, 0)]), -1), -2)
    s = c[..., k:, k:] - c[..., :-k, k:] - c[..., k:, :-k] + c[..., :-k, :-k]
    return s / (k * k)


def _ssim_rows(x: np.ndarray, y: np.ndarray, r: int = 3) -> np.ndarray:
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    mx, my = _box_mean(x, r), _box_mean(y, r)
    vx = _box_mean(x * x, r) - mx * mx
    vy = _box_mean(y * y, r) - my * my
    cxy = _box_mean(x * y, r) - mx * my
    s = ((2 * mx * my + c1) * (2 * cxy + c2)) / ((mx * mx + my * my + c1) * (vx + vy + c2))
    return s.reshape(len(s), -1).mean(1)


def descriptors(frames: list[np.ndarray], box: tuple[int, int, int, int], side: int = SIM_SIDE) -> np.ndarray:
    """(n, 2, side, side): premultiplied luminance and alpha inside the shared box, so similarity ignores the
    backdrop and sees the whole character at the same scale in every frame."""
    out = np.zeros((len(frames), 2, side, side), np.float32)
    for i, a in enumerate(frames):
        im = Image.fromarray(a, "RGBA").crop(box).resize((side, side), Image.Resampling.BILINEAR)
        f = np.asarray(im, np.float32) / 255
        alpha = f[..., 3]
        out[i, 0] = (f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114) * alpha
        out[i, 1] = alpha
    return out


def similarity_matrix(desc: np.ndarray) -> np.ndarray:
    """SSIM of every frame pair (mean of the luminance and mask SSIM), 1 on the diagonal."""
    n = len(desc)
    s = np.eye(n, dtype=np.float64)
    for lag in range(1, n):
        a, b = desc[:-lag], desc[lag:]
        v = 0.5 * (_ssim_rows(a[:, 0], b[:, 0]) + _ssim_rows(a[:, 1], b[:, 1]))
        idx = np.arange(n - lag)
        s[idx, idx + lag] = v
        s[idx + lag, idx] = v
    return s


def find_cycle(sim: np.ndarray, min_period: int | None = None) -> tuple[int, int, float]:
    """(start, period, seam score) of the most repeating stretch. Every lag is scored by its mean self-similarity;
    multiples of the true period score as high as the period itself, so the shortest lag near the best wins."""
    n = len(sim)
    lo = max(3, min_period if min_period is not None else n // 10)
    hi = n - 2
    if hi < lo:
        return 0, n, 0.0
    lags = np.arange(lo, hi + 1)
    score = np.array([float(np.mean(np.diagonal(sim, int(L)))) for L in lags])
    best, worst = float(score.max()), float(score.min())
    tol = 0.1 * (best - worst) + 1e-6
    period = int(lags[int(np.argmax(score))])
    for i, L in enumerate(lags):
        left = score[i - 1] if i > 0 else -np.inf
        right = score[i + 1] if i + 1 < len(score) else -np.inf
        if score[i] >= best - tol and score[i] >= left and score[i] >= right:
            period = int(L)
            break
    diag = np.diagonal(sim, period)
    start = int(np.argmax(diag))
    return start, period, round(float(diag[start]), 4)


def action_span(sim: np.ndarray, rest: float = 0.25) -> tuple[int, int, float]:
    """One-shot: from the last still frame before the motion to the frame where the pose returns near the start
    (or the motion settles). Returns (start, end exclusive, peak change)."""
    n = len(sim)
    d = 1 - sim[0]
    peak_i = int(np.argmax(d))
    peak = float(d[peak_i])
    if peak <= 1e-3:
        return 0, n, 0.0
    moving = np.nonzero(d > 0.15 * peak)[0]
    start = max(0, int(moving[0]) - 1) if len(moving) else 0
    end = n
    back = [i for i in range(peak_i + 1, n) if d[i] < rest * peak]
    if back:
        end = back[0] + 1
    else:
        step = np.array([1 - sim[i, i + 1] for i in range(n - 1)])
        calm = 0.1 * float(step.max()) if len(step) else 0.0
        for i in range(peak_i + 1, n - 3):
            if (step[i:i + 3] < calm).all():
                end = i + 1
                break
    return start, max(end, start + 2), round(peak, 4)


def premul_blend(a: np.ndarray, b: np.ndarray, w: float) -> np.ndarray:
    fa, fb = a.astype(np.float32), b.astype(np.float32)
    pa, pb = fa[..., 3:] / 255, fb[..., 3:] / 255
    rgb = fa[..., :3] * pa * (1 - w) + fb[..., :3] * pb * w
    alpha = pa * (1 - w) + pb * w
    out = np.concatenate([np.where(alpha > 1e-4, rgb / np.maximum(alpha, 1e-4), 0), alpha * 255], -1)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def crossfade_seam(frames: list[np.ndarray], start: int, period: int, k: int) -> tuple[list[np.ndarray], int]:
    """Loop segment frames[start:start+period] with the wrap smoothed over k frames: the tail blends toward the
    frames just before `start` (in a clean cycle the same poses), or, when the clip has no lead-in, the head blends
    toward the frames right after the segment. Returns (segment, frames blended)."""
    seg = [f.copy() for f in frames[start:start + period]]
    end = start + period
    k = min(k, period // 3)
    if k <= 0:
        return seg, 0
    if start >= k:
        for i in range(k):
            j = period - k + i
            seg[j] = premul_blend(seg[j], frames[start - k + i], (i + 1) / (k + 1))
        return seg, k
    k = min(k, len(frames) - end)
    for i in range(k):
        seg[i] = premul_blend(seg[i], frames[end + i], (k - i) / (k + 1))
    return seg, max(k, 0)


def sample(n_src: int, n_out: int, loop: bool) -> list[int]:
    if loop:
        return [min(n_src - 1, int(math.floor(i * n_src / n_out + 1e-9))) for i in range(n_out)]
    if n_out == 1:
        return [0]
    return [round(i * (n_src - 1) / (n_out - 1)) for i in range(n_out)]


def union_box(frames: list[np.ndarray]) -> tuple[int, int, int, int] | None:
    m = np.zeros(frames[0].shape[:2], bool)
    for a in frames:
        m |= mask_of(a)
    ys, xs = np.nonzero(m)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def mass_box(frames: list[np.ndarray], keep: float = 0.999) -> tuple[int, int, int, int] | None:
    """Box holding `keep` of all frames' alpha mass: a matting streak or a motion-blur wisp at the canvas edge would
    otherwise widen every cell and shrink the character."""
    mass = np.zeros(frames[0].shape[:2], np.float64)
    for a in frames:
        mass += np.where(mask_of(a), a[..., 3], 0)
    if mass.sum() <= 0:
        return None
    cut = (1 - keep) / 2

    def span(profile: np.ndarray) -> tuple[int, int]:
        c = np.cumsum(profile)
        return int(np.searchsorted(c, cut * c[-1])), int(np.searchsorted(c, c[-1] * (1 - cut))) + 1

    (x0, x1), (y0, y1) = span(mass.sum(0)), span(mass.sum(1))
    return x0, y0, x1, y1


def cell_crop(box: tuple[int, int, int, int], pivot: tuple[float, float], pad: float = 0.06
              ) -> tuple[int, int, int, int]:
    """Square crop holding every frame, the pivot on the vertical centre line and just above the bottom edge."""
    x0, y0, x1, y1 = box
    px, py = pivot
    half = max(px - x0, x1 - px, 1.0)
    bottom = max(float(y1), py)
    side = max(2 * half, bottom - y0) * (1 + 2 * pad)
    side = int(math.ceil(side))
    left = int(round(px - side / 2))
    b = int(math.ceil(bottom + pad * side))
    return left, b - side, left + side, b


def cut_cells(frames: list[np.ndarray], crop: tuple[int, int, int, int], size: int) -> list[Image.Image]:
    out = []
    for a in frames:
        im = Image.fromarray(a, "RGBA").crop(crop)
        out.append(im if im.width == size else im.resize((size, size), Image.Resampling.LANCZOS))
    return out


def anim_loop(frames: list[np.ndarray], *, loop: bool, n_out: int, lock: str, crossfade: int, size: int,
              start: int = -1, end: int = -1) -> tuple[list[Image.Image], dict[str, Any]]:
    """Aligned, cut cells + report. `start`/`end` (source frame indices, -1 = auto) override the detected span."""
    n = len(frames)
    if n < 4:
        raise StepError("op_bad_input", f"need at least 4 frames, got {n}")
    if any(f.shape != frames[0].shape for f in frames):
        raise StepError("op_bad_input", "frames differ in size")
    masks = [mask_of(f) for f in frames]
    if not any(m.any() for m in masks):
        raise StepError("op_bad_input", "every frame is empty after background removal")
    raw = track(masks)
    target = lock_targets(raw, lock, circular=loop)
    offs = np.nan_to_num(np.rint(target - _fill_nan_2d(raw))).astype(int)
    aligned = [shift(f, int(dx), int(dy)) for f, (dx, dy) in zip(frames, offs)]

    box = union_box(aligned) or (0, 0, frames[0].shape[1], frames[0].shape[0])
    sim = similarity_matrix(descriptors(aligned, box))
    report: dict[str, Any] = {"source_frames": n, "lock": lock, "loop": loop,
                              "jitter_px": round(float(np.abs(offs).max()), 1) if len(offs) else 0.0,
                              "motion": round(float(1 - sim[0].min()), 4)}
    steps = [float(sim[i, i + 1]) for i in range(n - 1)]
    step_sim = float(np.median(steps)) if steps else 1.0
    if loop:
        s, period, seam = find_cycle(sim)
        if start >= 0:
            s = min(start, n - 4)
            period = (min(end, n) - s) if end > s + 3 else period
            period = min(period, n - s)
            seam = round(float(sim[s, s + period]) if s + period < n else float(sim[s, s + period - 1]), 4)
        last = s + period - 1
        seg, blended = crossfade_seam(aligned, s, period, round(crossfade * period / max(n_out, 1)))
        idx = sample(period, n_out, True)
        picked = [seg[i] for i in idx]
        report |= {"start": s, "end": s + period, "period": period, "seam_similarity": seam,
                   "step_similarity": round(step_sim, 4), "crossfade_frames": blended,
                   "wrap_similarity": round(float(sim[last, s]), 4), "indices": [s + i for i in idx]}
    else:
        s, e, peak = action_span(sim)
        if start >= 0:
            s = min(start, n - 2)
        if end > s + 1:
            e = min(end, n)
        idx = sample(e - s, n_out, False)
        picked = [aligned[s + i] for i in idx]
        report |= {"start": s, "end": e, "peak_change": peak, "indices": [s + i for i in idx]}
    pbox = mass_box(picked) or box
    # A free vertical axis (jump, fall) keeps the ground line where the feet rest, not mid-air.
    tx = float(np.median(target[:, 0]))
    ty = float(np.median(target[:, 1])) if lock in ("feet", "ground") else float(np.max(target[:, 1]))
    crop = cell_crop(pbox, (tx, ty))
    k = size / (crop[2] - crop[0])
    pivot = [round((tx - crop[0]) * k), round((ty - crop[1]) * k)]
    cells = refine(cut_cells(picked, crop, size), pivot, lock)
    report |= {"frames": len(cells), "size": size, "crop": list(crop), "pivot": pivot,
               "pivot_error_px": float(pivot_error(cells, pivot, lock))}
    return cells, report


def refine(cells: list[Image.Image], pivot: list[int], lock: str) -> list[Image.Image]:
    """Second, whole-pixel pass in cell space: resampling moves the measured anchor by up to a pixel or two, so the
    locked axes are snapped onto the pivot again after scaling."""
    if lock == "none":
        return cells
    out = []
    arrays = [np.asarray(c.convert("RGBA")) for c in cells]
    for c, a, (x, y) in zip(cells, arrays, track([mask_of(a) for a in arrays])):
        if math.isnan(x):
            out.append(c)
            continue
        dx = round(pivot[0] - x) if lock in ("feet", "x") else 0
        dy = round(pivot[1] - y) if lock in ("feet", "ground") else 0
        out.append(c if dx == dy == 0 else Image.fromarray(shift(a, dx, dy), "RGBA"))
    return out


def _fill_nan_2d(a: np.ndarray) -> np.ndarray:
    return np.stack([_fill_nan(a[:, 0]), _fill_nan(a[:, 1])], 1)


def pivot_error(cells: list[Image.Image], pivot: list[int], lock: str) -> float:
    """Largest distance between a cell's measured anchor and the shared pivot on the locked axes."""
    worst = 0.0
    for x, y in track([mask_of(np.asarray(c.convert("RGBA"))) for c in cells]):
        if math.isnan(x):
            continue
        if lock in ("feet", "x"):
            worst = max(worst, abs(x - pivot[0]))
        if lock in ("feet", "ground"):
            worst = max(worst, abs(y - pivot[1]))
    return round(worst, 2)


def checker(size: tuple[int, int], tile: int = 8) -> Image.Image:
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    v = np.where(((xx // tile) + (yy // tile)) % 2 == 0, 58, 72).astype(np.uint8)
    return Image.fromarray(np.stack([v, v, v, np.full_like(v, 255)], -1), "RGBA")


def preview_gif(cells: list[Image.Image], fps: float, loop: bool, side: int = 256) -> bytes:
    import io

    bg = checker((side, side))
    frames = []
    for c in cells:
        f = bg.copy()
        f.alpha_composite(c.resize((side, side), Image.Resampling.LANCZOS) if c.width != side else c)
        frames.append(f.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE, colors=255))
    durations = [max(20, round(1000 / max(fps, 1)))] * len(frames)
    if not loop:
        durations[-1] = 600
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=2)
    return buf.getvalue()


ACCENT = (158, 140, 252, 255)
PIVOT = (255, 64, 64, 255)


def qa_sheet(source: list[np.ndarray], cells: list[Image.Image], report: dict[str, Any], thumb: int = 64
             ) -> Image.Image:
    """Source timeline (picked frames outlined, span bright, the rest dimmed), the cells with pivot cross and
    baseline, and an onion-skin of all cells: drift and jitter show as a smeared feet area."""
    n = len(source)
    box = union_box(source) or (0, 0, source[0].shape[1], source[0].shape[0])
    cols = min(n, 16)
    rows = math.ceil(n / cols)
    cell = max(96, min(160, 1200 // max(len(cells), 1)))
    width = max(cols * (thumb + 4), len(cells) * (cell + 4)) + 8
    height = rows * (thumb + 4) + cell + 12 + 2 * cell + 16
    out = Image.new("RGBA", (width, height), (30, 30, 33, 255))
    d = ImageDraw.Draw(out)
    picked = set(report.get("indices") or [])
    s, e = int(report.get("start", 0)), int(report.get("end", n))
    for i, a in enumerate(source):
        im = Image.fromarray(a, "RGBA").crop(box)
        im.thumbnail((thumb, thumb))
        x, y = 4 + (i % cols) * (thumb + 4), 4 + (i // cols) * (thumb + 4)
        tile = checker((thumb, thumb), 6)
        tile.alpha_composite(im, ((thumb - im.width) // 2, (thumb - im.height) // 2))
        if not s <= i < e:
            tile = Image.blend(tile, Image.new("RGBA", tile.size, (30, 30, 33, 255)), 0.6)
        out.alpha_composite(tile, (x, y))
        if i in picked:
            d.rectangle((x - 2, y - 2, x + thumb + 1, y + thumb + 1), outline=ACCENT, width=2)
    y0 = 8 + rows * (thumb + 4)
    px, py = report["pivot"]
    k = cell / report["size"]
    for i, c in enumerate(cells):
        x = 4 + i * (cell + 4)
        tile = checker((cell, cell))
        tile.alpha_composite(c.resize((cell, cell), Image.Resampling.LANCZOS))
        out.alpha_composite(tile, (x, y0))
        cx, cy = x + px * k, y0 + py * k
        d.line((x, cy, x + cell, cy), fill=(255, 64, 64, 120), width=1)
        d.line((cx - 6, cy, cx + 6, cy), fill=PIVOT, width=2)
        d.line((cx, cy - 6, cx, cy + 6), fill=PIVOT, width=2)
    side = 2 * cell
    onion = checker((side, side), 10)
    layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    for c in cells:
        a = np.asarray(c.resize((side, side), Image.Resampling.LANCZOS)).copy()
        a[..., 3] = (a[..., 3].astype(np.float32) * (1.6 / max(len(cells), 1)) ** 0.5).clip(0, 255).astype(np.uint8)
        layer.alpha_composite(Image.fromarray(a, "RGBA"))
    onion.alpha_composite(layer)
    ox, oy = 4, y0 + cell + 8
    out.alpha_composite(onion, (ox, oy))
    cx, cy = ox + px * side / report["size"], oy + py * side / report["size"]
    d.line((cx - 10, cy, cx + 10, cy), fill=PIVOT, width=2)
    d.line((cx, cy - 10, cx, cy + 10), fill=PIVOT, width=2)
    return out


def contact_strip(frames: list[Image.Image], count: int = 8, cell: int = 192) -> Image.Image:
    """`count` evenly spaced frames of one clip in a 4-wide grid: enough to judge the motion at a glance."""
    n = len(frames)
    idx = sorted({round(i * (n - 1) / max(count - 1, 1)) for i in range(min(count, n))})
    cols = min(4, len(idx))
    rows = math.ceil(len(idx) / cols)
    out = Image.new("RGB", (cols * cell + (cols + 1) * 4, rows * cell + (rows + 1) * 4), (30, 30, 33))
    for k, i in enumerate(idx):
        im = frames[i].convert("RGB")
        im.thumbnail((cell, cell))
        r, c = divmod(k, cols)
        out.paste(im, (4 + c * (cell + 4) + (cell - im.width) // 2, 4 + r * (cell + 4) + (cell - im.height) // 2))
    return out


class AnimPrep(OpExecutor):
    kind = "op.anim_prep"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        ref = _images_in(inputs)[0]
        res = str(params.get("resolution") or "480p").lower()
        if res not in AREAS:
            raise StepError("op_bad_param", f"resolution must be one of {list(AREAS)}, got {res!r}")
        seconds = min(max(float(params.get("seconds") or 2), 1.0), 5.0)
        k = min(max(int(params.get("candidates") or 3), 1), 4)
        bg = parse_color(params.get("backdrop"))
        img = await _load(ctx, ref)
        canvas, info = await asyncio.to_thread(prep_canvas, img, AREAS[res], bg)
        info |= {"length": frame_count(seconds), "fps": FPS, "seconds": round(frame_count(seconds) / FPS, 3),
                 "resolution": res, "candidates": k}
        start = await _put_png(ctx, canvas, "anim_start.png")
        out = {"image": [start], "starts": [start] * k, "json": [await _put_json(ctx, info, "anim_prep.json")]}
        return StepResult(outputs=out, meta=info)


class AnimCandidates(OpExecutor):
    kind = "op.anim_candidates"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs, "frames")
        info = await _read_json(ctx, _images_in(inputs, "json")[0]) if inputs.get("json") else {}
        length = int(info.get("length") or 0)
        k = int(info.get("candidates") or 0) or (len(refs) // length if length else 1)
        if k < 1 or len(refs) % k:
            raise StepError("op_bad_input", f"{len(refs)} frames do not split into {k} clips")
        per = len(refs) // k
        if per < 4:
            raise StepError("op_bad_input", f"each clip needs at least 4 frames, got {per}")
        groups = [refs[i * per:(i + 1) * per] for i in range(k)]
        previews: list[BlobRef] = []
        for i, g in enumerate(groups):
            sample_idx = sorted({round(j * (per - 1) / 7) for j in range(8)})
            frames = [await _load(ctx, g[j]) for j in sample_idx]
            strip = await asyncio.to_thread(contact_strip, frames, len(frames))
            previews.append(await _put_png(ctx, strip, f"anim_candidate_{i + 1}.png"))
            ctx.progress(i + 1, k, "preview")
        doc = {"candidates": [{"preview": p.hash, "frames": [r.hash for r in g]} for p, g in zip(previews, groups)],
               "frames_per_clip": per}
        return StepResult(outputs={"previews": previews, "json": [await _put_json(ctx, doc, "anim_candidates.json")]},
                          meta={"candidates": k, "frames_per_clip": per})


class AnimPick(OpExecutor):
    kind = "op.anim_pick"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        picked = _images_in(inputs, "picked")[0]
        doc = await _read_json(ctx, _images_in(inputs, "candidates")[0])
        cands = doc.get("candidates") if isinstance(doc, dict) else None
        if not isinstance(cands, list) or not cands:
            raise StepError("op_bad_input", "candidates json has no clips")
        index = next((i for i, c in enumerate(cands) if c.get("preview") == picked.hash), None)
        if index is None:
            raise StepError("op_bad_input", "the picked image is not one of the candidate previews")
        hashes = list(cands[index]["frames"])
        # BiRefNet runs once per frame; past ~2 s at 24 fps every other frame is plenty for 8-16 output cells.
        stride = max(1, math.ceil(len(hashes) / MAX_PICK_FRAMES))
        hashes = hashes[::stride]
        refs = []
        for i, h in enumerate(hashes):
            ref = await ctx.blobs.get(h)
            if ref is None:
                raise StepError("op_bad_input", f"frame blob {h[:12]} is missing")
            refs.append(ref)
        return StepResult(outputs={"frames": refs}, meta={"candidate": index + 1, "frames": len(refs),
                                                          "stride": stride, "fps": FPS / stride})


class AnimLoop(OpExecutor):
    kind = "op.anim_loop"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        action = str(params.get("action") or "custom")
        preset = ACTIONS.get(action)
        if preset is None:
            raise StepError("op_bad_param", f"action must be one of {list(ACTIONS)}, got {action!r}")
        loop = resolve_loop(action, params.get("loop"))
        lock = str(params.get("lock") or "auto").lower()
        lock = preset["lock"] if lock == "auto" else lock
        if lock not in LOCKS:
            raise StepError("op_bad_param", f"lock must be auto or one of {list(LOCKS)}, got {lock!r}")
        n_out = int(params.get("frames") or 12)
        if not 2 <= n_out <= 64:
            raise StepError("op_bad_param", f"frames must be 2-64, got {n_out}")
        size = int(params.get("size") or 256)
        fps = float(params.get("fps") or 0) or float(preset["fps"])
        facing = str(params.get("facing") or "right").lower()
        if facing not in FACINGS:
            raise StepError("op_bad_param", f"facing must be one of {list(FACINGS)}, got {facing!r}")
        clip = str(params.get("clip") or "").strip() or action
        info = await _read_json(ctx, inputs["json"][0]) if inputs.get("json") else {}
        bg = parse_color(info.get("backdrop")) if isinstance(info, dict) and info.get("backdrop") else None

        frames = []
        for i, r in enumerate(refs):
            frames.append(to_rgba(await _load(ctx, r), bg))
            ctx.progress(i + 1, len(refs) * 2, "load")
        cells, report = await asyncio.to_thread(
            anim_loop, frames, loop=loop, n_out=n_out, lock=lock, crossfade=int(params.get("crossfade", 1)),
            size=size, start=int(params.get("start", -1)), end=int(params.get("end", -1)))
        report |= {"action": action, "clip": clip, "fps": fps, "facing": facing, "angle": FACINGS[facing]["angle"]}
        if report["motion"] < 0.03:
            ctx.log("the picked clip barely moves; try a stronger motion prompt or another candidate", "warning")
        if loop and report["seam_similarity"] < 0.6:
            ctx.log(f"no clean repeat found (best seam similarity {report['seam_similarity']}); set start/end by hand "
                    "or pick another candidate", "warning")
        pivot_doc = {"pivot": report["pivot"], "width": size, "height": size, "angles": [report["angle"]],
                     "clips": [{"name": clip, "frames": len(cells), "fps": fps, "loop": loop}]}
        ctx.progress(0.8, 1.0, "save")
        out_cells = [await _put_png(ctx, c, f"{clip}_{i:02d}.png") for i, c in enumerate(cells)]
        qa = await asyncio.to_thread(qa_sheet, frames, cells, report)
        gif = await asyncio.to_thread(preview_gif, cells, fps, loop)
        out: Outputs = {
            "images": out_cells,
            "pivot": [await _put_json(ctx, pivot_doc, "anim_pivot.json")],
            "json": [await _put_json(ctx, report, "anim_loop.json")],
            "qa": [await _put_png(ctx, qa, "anim_qa.png")],
            "gif": [await ctx.blobs.put_bytes(gif, name=f"{clip}_loop.gif" if loop else f"{clip}.gif",
                                              mime="image/gif")],
        }
        ctx.progress(1.0, 1.0, "done")
        return StepResult(outputs=out, meta={k: v for k, v in report.items() if k not in ("indices", "crop")})


ANIM_VIDEO_OPS: tuple[type[OpExecutor], ...] = (AnimPrep, AnimCandidates, AnimPick, AnimLoop)
