"""Seamless textures: every array op wraps around the edges (np.roll / modular indexing) so outputs stay tileable."""
from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png
from .pixel import PALETTE_CHOICES, check_params, plan_palette, quantize, to_hex

REPORT_NAME = "tile_check.json"
PREVIEW_NAME = "tile_preview.png"
ATLAS_NAME = "tileset.png"
MIN_TILE = 16


def _arr(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"), np.float32) / 255.0


def _rgb(img: Image.Image) -> np.ndarray:
    """Textures are opaque: alpha is dropped rather than blurred into the colour."""
    return np.asarray(img.convert("RGB"), np.float32) / 255.0


def _img(a: np.ndarray) -> Image.Image:
    a8 = np.clip(np.round(a * 255.0), 0, 255).astype(np.uint8)
    if a8.shape[-1] == 3:
        a8 = np.concatenate([a8, np.full(a8.shape[:2] + (1,), 255, np.uint8)], -1)
    return Image.fromarray(a8, "RGBA")


def _lum(a: np.ndarray) -> np.ndarray:
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def box_blur(a: np.ndarray, r: int, axes: Sequence[int] = (0, 1), wrap: bool | Sequence[bool] = True) -> np.ndarray:
    """Mean over a (2r+1) window, wrapping around (or clamping at) the edges; cumulative sums keep it O(n)."""
    out = np.asarray(a, np.float32)
    if r <= 0:
        return out
    wraps = [wrap] * len(axes) if isinstance(wrap, bool) else list(wrap)
    for ax, wr in zip(axes, wraps):
        n = out.shape[ax]
        raw = np.arange(-r - 1, n + r)
        idx = raw % n if wr else np.clip(raw, 0, n - 1)
        c = np.cumsum(np.take(out, idx, axis=ax), axis=ax, dtype=np.float64)
        hi = np.take(c, np.arange(2 * r + 1, n + 2 * r + 1), axis=ax)
        lo = np.take(c, np.arange(0, n), axis=ax)
        out = ((hi - lo) / (2 * r + 1)).astype(np.float32)
    return out


def soft_blur(a: np.ndarray, r: int, axes: Sequence[int] = (0, 1), wrap: bool | Sequence[bool] = True) -> np.ndarray:
    """Three box passes ≈ a Gaussian of sigma ≈ r."""
    rb = max(1, round(r * 0.8)) if r > 0 else 0
    for _ in range(3):
        a = box_blur(a, rb, axes, wrap)
    return a


def _closed_path(cost: np.ndarray) -> np.ndarray:
    """Min-cost top-to-bottom path (one column per row, steps of at most 1) that ends in the column it starts in,
    so the cut also lines up across the vertical wrap."""
    h, w = cost.shape
    start = int(np.argmin(cost.mean(0)))
    inf = np.inf
    acc = np.full(w, inf)
    acc[start] = cost[0, start]
    back = np.zeros((h, w), np.int8)
    for y in range(1, h):
        left = np.concatenate([[inf], acc[:-1]])
        right = np.concatenate([acc[1:], [inf]])
        stack = np.stack([left, acc, right])
        k = stack.argmin(0)
        back[y] = k - 1
        acc = stack[k, np.arange(w)] + cost[y]
    path = np.empty(h, np.int64)
    x = start
    for y in range(h - 1, -1, -1):
        path[y] = x
        x = x + int(back[y, x]) if y else x
    return path


def join(x: np.ndarray, y: np.ndarray, feather: int = 3) -> np.ndarray:
    """Overlap window that starts as exactly `x` (column 0) and ends as exactly `y` (last column): detail switches
    along the min-error cut, the low band ramps across the window so a brightness step never shows as a line."""
    o = x.shape[1]
    cost = box_blur(((x - y) ** 2).sum(-1), 1, wrap=(True, False)) + 1e-6
    path = _closed_path(cost)
    m = (np.arange(o)[None, :] >= path[:, None]).astype(np.float32)
    if feather > 0:
        m = soft_blur(m, feather, (1,), wrap=False)
    m[:, 0], m[:, -1] = 0.0, 1.0
    r = max(1, o // 4)
    lx, ly = soft_blur(x, r, wrap=(True, False)), soft_blur(y, r, wrap=(True, False))
    ramp = np.linspace(0, 1, o, dtype=np.float32)[None, :, None]
    m3 = m[..., None]
    return m3 * (y - ly) + (1 - m3) * (x - lx) + ramp * ly + (1 - ramp) * lx


def _overlap(n: int, frac: float) -> int:
    o = max(4, round(n * frac))
    if n - o < MIN_TILE or o > n // 3:
        raise ValueError(f"image too small for an overlap of {frac:.3f} ({n}px)")
    return o


def _quilt_x(a: np.ndarray, frac: float, feather: int, o: int | None = None) -> np.ndarray:
    # The last `o` columns run into the first `o` across the wrap; overlapping them and dropping one copy makes
    # every column appear exactly once, so nothing repeats inside the tile (width shrinks by o).
    w = a.shape[1]
    o = o or _overlap(w, frac)
    return np.concatenate([join(a[:, w - o:], a[:, :o], feather), a[:, o:w - o]], 1)


def detrend(a: np.ndarray) -> np.ndarray:
    """Subtracts the best-fit brightness plane. A lit-from-one-side source would otherwise put its dark and bright
    edges next to each other inside the overlap window, which reads as a band in every tile."""
    h, w = a.shape[:2]
    g = 16
    cells = _cells(_lum(a), g)
    yy, xx = np.mgrid[0:g, 0:g] / (g - 1) - 0.5
    coef, *_ = np.linalg.lstsq(np.stack([np.ones(g * g), xx.ravel(), yy.ravel()], 1), cells.ravel(), rcond=None)
    xs = (np.arange(w) / max(1, w - 1) - 0.5)[None, :]
    ys = (np.arange(h) / max(1, h - 1) - 0.5)[:, None]
    plane = coef[1] * xs + coef[2] * ys
    return np.clip(a - plane.astype(np.float32)[..., None], 0, 1)


GRID_MIN_STRENGTH = 0.1
GRID_SOLO_STRENGTH = 0.17
GRID_MIN_SIDE = 384
STAMP_STRENGTH = 0.4
GRID_SIDE = 384
CROSS_KEEP = 0.6
CROSS_MIN = 0.2
SIGNED_MIN = -0.5
GRID_MIN_REPEATS = 3
LINE_MIN_STRENGTH = 0.5
LINE_MIN_HARMONIC = 0.35
# Brightness profiles of grass and noise reach ~0.45 / 0.45 on their own, so this one needs a wider margin.
BRIGHT_LINE_MIN = (0.6, 0.55)


def _reduce(lum: np.ndarray, side: int) -> tuple[np.ndarray, int]:
    s = max(1, math.ceil(max(lum.shape) / side))
    h, w = lum.shape[0] // s * s, lum.shape[1] // s * s
    return lum[:h, :w].reshape(h // s, s, w // s, s).mean((1, 3)), s


def acf2(lum: np.ndarray) -> np.ndarray:
    """Normalised linear autocorrelation (zero-padded FFT, divided by the overlap area), indexed [dy % 2h, dx % 2w]."""
    h, w = lum.shape
    z = lum - lum.mean()
    f = np.fft.rfft2(z, s=(2 * h, 2 * w))
    raw = np.fft.irfft2(f * np.conj(f), s=(2 * h, 2 * w))
    ones = np.fft.rfft2(np.ones_like(z), s=(2 * h, 2 * w))
    count = np.maximum(np.fft.irfft2(ones * np.conj(ones), s=(2 * h, 2 * w)), 1.0)
    acf = raw / count
    return acf / max(float(acf[0, 0]), 1e-12)


def _fundamental(p: np.ndarray, min_lag: int) -> tuple[float, float]:
    """(lag, strength) of the shortest strong peak, strength = height over the dip before it. A peak only counts when
    the best one is a near-integer multiple of it, so bricks report their course height rather than a 2-course lag."""
    n = len(p)
    prom = {i: float(p[i] - p[i // 2:i].min()) for i in range(max(2, min_lag), n - 1)
            if p[i] > p[i - 1] and p[i] >= p[i + 1]}
    prom = {i: v for i, v in prom.items() if v > 0.08}
    if not prom:
        return 0.0, 0.0
    # Strength is the peak's own correlation, not its height over the dip: band-limited noise rings (a negative
    # lobe then a small positive one), which looks prominent but never correlates much.
    best = max(prom, key=lambda i: min(prom[i], float(p[i])))
    for i in sorted(prom):
        if p[i] >= 0.7 * p[best] and abs(best / i - round(best / i)) < 0.2:
            return float(i), min(prom[i], float(p[i]))
    return float(best), min(prom[best], float(p[best]))


def _harmonic(p: np.ndarray, c: float) -> float | None:
    if c + 2 >= len(p):
        return None
    w = max(1, int(0.05 * c))
    return min(float(p[int(c - w):int(c + w) + 1].max()),
               float(p[int(c - w):int(c + w) + 1].max() - p[int(c * 0.75) - 1:int(c)].min()))


def _refine(hp: np.ndarray, axis: int, est: float) -> float:
    """Sub-pixel period from the full-resolution 1D autocorrelation at the largest multiple below half the size."""
    n = hp.shape[axis]
    m = max(1, int(0.45 * n / est))
    z = np.moveaxis(hp, axis, -1)
    f = np.fft.rfft(z - z.mean(-1, keepdims=True), n=2 * n, axis=-1)
    prof = np.fft.irfft(f * np.conj(f), n=2 * n, axis=-1).reshape(-1, 2 * n).mean(0)[:n] / (n - np.arange(n))
    c = m * est
    lo, hi = max(1, int(c - 0.12 * est * m ** 0.5) - 1), min(n - 2, int(c + 0.12 * est * m ** 0.5) + 2)
    if hi <= lo:
        return est
    i = lo + int(np.argmax(prof[lo:hi + 1]))
    a, b, d = prof[i - 1], prof[i], prof[i + 1]
    den = a - 2 * b + d
    off = 0.5 * (a - d) / den if den < 0 else 0.0
    return (i + float(np.clip(off, -0.5, 0.5))) / m


def detect_grid(a: np.ndarray) -> dict[str, Any]:
    """Dominant repeat per axis (bricks, planks, floor tiles): period in px, strength = autocorrelation peak height,
    strong = periodic enough for wrap_overlap to phase-align that axis."""
    lum = _lum(a)
    r = max(4, min(lum.shape) // 8)
    info: dict[str, Any] = {}
    for name, ax in (("x", 1), ("y", 0)):
        # High-pass along the axis only: grout running along the axis is constant there and would lift the whole
        # profile, hiding the joints across it.
        hp_ax = lum - soft_blur(lum, r, axes=(ax,), wrap=False)
        sm, s = _reduce(hp_ax, GRID_SIDE)
        c = acf2(sm)
        prof = c[0, :sm.shape[1] // 2] if ax == 1 else c[:sm.shape[0] // 2, 0]
        lag, strength = _fundamental(prof, max(3, len(prof) // 32))
        period = _refine(hp_ax, ax, lag * s) if lag else 0.0
        # Cobbles and grass clumps give one bump at their mean spacing; a real grid repeats at twice the period too.
        h2 = _harmonic(prof, 2 * lag) if lag else None
        periodic = strength >= GRID_MIN_STRENGTH and (strength >= GRID_SOLO_STRENGTH or (h2 or 0.0) >= 0.06)
        # A repeat that fits fewer than three times is a stamped patch (stamp_repeat reports it), not a grid to align.
        strong = periodic and period * GRID_MIN_REPEATS <= lum.shape[ax]
        entry = {"period": round(period, 2), "strength": round(strength, 3), "strong": bool(strong),
                 "periodic": bool(periodic)}
        if not strong:
            for edges, need in ((True, (LINE_MIN_STRENGTH, LINE_MIN_HARMONIC)), (False, BRIGHT_LINE_MIN)):
                llag, lstr, lh2 = _line_period(lum, ax, edges)
                if lstr >= need[0] and lh2 >= need[1]:
                    break
            else:
                llag = 0.0
            if llag:
                entry = {"period": round(llag, 2), "strength": round(lstr, 3), "strong": True, "periodic": True,
                         "source": "lines"}
        info[name] = entry
    return info


def _line_period(lum: np.ndarray, ax: int, edges: bool = True) -> tuple[float, float, float]:
    """(lag, strength, 2nd-harmonic) of a profile projected across the axis. Plank rows and thin grout lines run
    straight through the whole image, so averaging along them keeps them while grain, stone speckle and per-plank
    tone (which swamp the 2D autocorrelation) average out. Edge strength suits planks (tone changes per board);
    plain brightness suits flagstones, whose dark joints are wider than one edge."""
    if edges:
        e = np.abs(np.diff(lum, axis=ax, append=np.take(lum, [-1], axis=ax))).mean(axis=1 - ax)[None]
        e = soft_blur(e, 1, axes=(1,), wrap=False)
        p = (e - soft_blur(e, max(4, e.shape[1] // 32), axes=(1,), wrap=False))[0]
    else:
        p = (lum - soft_blur(lum, max(4, min(lum.shape) // 8), axes=(ax,), wrap=False)).mean(axis=1 - ax)
    n = len(p)
    f = np.fft.rfft(p - p.mean(), 2 * n)
    acf = np.fft.irfft(f * np.conj(f), 2 * n)[:n] / (n - np.arange(n))
    acf = acf / max(float(acf[0]), 1e-12)
    lag, strength = _fundamental(acf[:n // GRID_MIN_REPEATS], max(3, n // 64))
    return lag, strength, ((_harmonic(acf[:n // 2], 2 * lag) or 0.0) if lag else 0.0)


def _period_near(x: np.ndarray, est: float, tol: float = 0.3) -> float | None:
    """Period along axis 1 of `x` within est·(1 ± tol), sharpened on its 2nd/3rd multiple."""
    n = x.shape[1]
    z = x - x.mean(1, keepdims=True)
    f = np.fft.rfft(z, n=2 * n, axis=1)
    prof = np.fft.irfft(f * np.conj(f), n=2 * n, axis=1).mean(0)[:n] / (n - np.arange(n))
    prof = prof / max(float(prof[0]), 1e-12)

    def peak(c: float, w: float) -> tuple[float, float] | None:
        lo, hi = max(1, int(c - w)), min(n // 2, int(c + w) + 1)
        if hi - lo < 2:
            return None
        i = lo + int(np.argmax(prof[lo:hi]))
        if i <= lo or i >= hi - 1:
            return None
        a, b, d = prof[i - 1], prof[i], prof[i + 1]
        den = a - 2 * b + d
        return i + (0.5 * (a - d) / den if den < 0 else 0.0), float(b)

    first = peak(est, est * tol)
    if first is None or first[1] < 0.1:
        return None
    period = first[0]
    for m in (2, 3):
        nxt = peak(m * period, 0.15 * period)
        if nxt is None or nxt[1] < 0.1:
            break
        period = nxt[0] / m
    return period


def _scale_profile(lum: np.ndarray, periods: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Linear fit P(y) = p0 + p1·y of the local period down the image, per periodic axis: a camera pitched towards
    the surface makes the far rows smaller in both directions."""
    h, w = lum.shape
    bh = max(32, round(h * 0.3))
    out: dict[str, tuple[float, float]] = {}
    for name, ax in (("x", 1), ("y", 0)):
        g = periods[name]
        if not g["strong"] or not g["period"]:
            continue
        est = float(g["period"])
        hp = lum - soft_blur(lum, max(4, round(est)), axes=(ax,), wrap=False)
        if est * 3 > (bh if ax == 0 else w):
            continue
        ys, ps = [], []
        for c in np.linspace(bh / 2, h - bh / 2, 5):
            band = hp[int(c - bh / 2):int(c + bh / 2)]
            pk = _period_near(band if ax == 1 else band.T, est)
            if pk:
                ys.append(c)
                ps.append(pk)
        if len(ys) < 4:
            continue
        p1, p0 = np.polyfit(ys, ps, 1)
        resid = float(np.sqrt(np.mean((np.polyval([p1, p0], ys) - ps) ** 2)))
        mean = float(np.mean(ps))
        if resid < 0.03 * mean and 0.04 <= abs(p1) * h / mean <= 0.6:
            out[name] = (float(p0), float(p1))
    return out


def _lerp_rows(a: np.ndarray, pos: np.ndarray) -> np.ndarray:
    i0 = np.clip(np.floor(pos).astype(np.int64), 0, a.shape[0] - 1)
    i1 = np.clip(i0 + 1, 0, a.shape[0] - 1)
    t = (pos - i0).astype(np.float32).reshape(-1, *([1] * (a.ndim - 1)))
    return a[i0] * (1 - t) + a[i1] * t


def rectify_pitch(a: np.ndarray, periods: dict[str, Any]) -> tuple[np.ndarray, dict[str, float]]:
    """Undoes mild keystone along y: rows are respaced so the y period is constant and each row is stretched so the
    x period is, both to the largest local scale (upsampling, nothing cropped). Without it the courses at the top
    and bottom of a pitched photo differ in size and cannot meet at the wrap."""
    h, w = a.shape[:2]
    fit = _scale_profile(_lum(a), periods)
    if not fit:
        return a, {}
    ys = np.arange(h, dtype=np.float64)
    out = a
    if "y" in fit:
        py = np.maximum(np.polyval([fit["y"][1], fit["y"][0]], ys), 1e-3)
        v = np.concatenate([[0.0], np.cumsum(py.max() / py)])[:h]
        nh = int(v[-1]) + 1
        out = _lerp_rows(out, np.interp(np.arange(nh), v, ys))
        ys = np.interp(np.arange(nh), v, ys)
    if "x" in fit:
        px = np.maximum(np.polyval([fit["x"][1], fit["x"][0]], ys), 1e-3)
        sx = px / px.max()
        xc = (w - 1) / 2
        pos = xc + (np.arange(w)[None, :] - xc) * sx[:, None]
        i0 = np.clip(np.floor(pos).astype(np.int64), 0, w - 1)
        t = (pos - i0)[..., None].astype(np.float32)
        rows = np.arange(out.shape[0])[:, None]
        out = out[rows, i0] * (1 - t) + out[rows, np.clip(i0 + 1, 0, w - 1)] * t
    return np.ascontiguousarray(out, np.float32), {k: round(v[1] * h / max(v[0] + v[1] * h / 2, 1e-6), 3)
                                                     for k, v in fit.items()}


def wrap_overlap(a: np.ndarray, frac: float, min_ncc: float = 0.5) -> tuple[int | None, float]:
    """Overlap (along axis 1) that brings the content round the wrap in phase: the column profile's first stretch is
    matched against every later one and the lag L with the best normalised correlation fixes o = width - L, so
    column L + i meets column i inside the quilt window. Measured on the content itself, so courses of uneven
    height still line up at the wrap. The mean over rows keeps what runs straight across the image (grout rows,
    plank edges); staggered joints average out."""
    lum = _lum(a)
    n = lum.shape[1]
    # Edge strength rather than brightness: AI grout often turns from dark to light down the image, which flips the
    # sign of a brightness correlation while the joint positions still match.
    prof = np.abs(np.diff(lum, axis=1, append=lum[:, -1:])).mean(0, keepdims=True)
    prof = soft_blur(prof, 2, axes=(1,), wrap=False)
    prof = prof - soft_blur(prof, max(4, n // 16), axes=(1,), wrap=False)
    b = max(8, round(n * 0.08))
    o_lo, o_hi = max(4, round(n * 0.05)), n // 3
    if n - o_hi < MIN_TILE or o_hi <= o_lo or b >= n - o_hi:
        return None, 0.0
    lags = np.arange(n - o_hi, n - o_lo + 1)
    valid = np.minimum(b, n - lags)
    mask = (np.arange(b)[None, :] < valid[:, None]).astype(np.float64)
    head = prof[:, None, :b] * mask[None]
    seg = np.lib.stride_tricks.sliding_window_view(np.pad(prof, ((0, 0), (0, b))), b, axis=1)[:, lags] * mask[None]
    cnt = mask.sum(1)[None, :, None]
    head = (head - head.sum(2, keepdims=True) / cnt) * mask[None]
    seg = (seg - seg.sum(2, keepdims=True) / cnt) * mask[None]
    ncc = ((head * seg).sum(2) / np.maximum(np.linalg.norm(head, axis=2) * np.linalg.norm(seg, axis=2), 1e-9)).mean(0)
    ncc = np.where(valid >= max(4, b // 2), ncc, -1.0)
    best = float(ncc.max())
    if best < min_ncc:
        return None, best
    # Edge strength is blind to polarity: on a checkerboard the half-period lag matches every edge while turning
    # light into dark (signed ≈ -0.85). Real bricks and planks sit near 0 because every brick has its own tone, so
    # only a clear inversion rules a lag out.
    sp = lum - soft_blur(lum, max(4, n // 16), axes=(1,), wrap=False)
    # High-passed across the axis instead: keeps what varies across the wrap direction (the staggered joints of a
    # running bond) and drops what runs along it (grout rows), so it tells an odd course lag from an even one.
    cp = lum - soft_blur(lum, max(4, lum.shape[0] // 16), axes=(0,), wrap=False)

    def signed(src: np.ndarray, j: int) -> float:
        m = int(valid[j])
        x, y = src[:, :m] - src[:, :m].mean(), src[:, lags[j]:lags[j] + m] - src[:, lags[j]:lags[j] + m].mean()
        return float((x * y).sum() / max(float(np.linalg.norm(x) * np.linalg.norm(y)), 1e-9))

    peaks = [i for i in range(len(lags)) if ncc[i] >= 0.85 * best and (i == 0 or ncc[i] >= ncc[i - 1])
             and (i == len(lags) - 1 or ncc[i] >= ncc[i + 1]) and signed(sp, i) > SIGNED_MIN]
    if not peaks:
        return None, best
    cross = {i: signed(cp, i) for i in peaks}
    top = max(cross.values())
    if top >= CROSS_MIN:
        peaks = [i for i in peaks if cross[i] >= CROSS_KEEP * top]
    o0 = round(n * frac)
    i = min(peaks, key=lambda j: abs((n - lags[j]) - o0))
    return int(n - lags[i]), float(ncc[i])


def flatten_light(a: np.ndarray, strength: float, wrap: bool = True) -> np.ndarray:
    """Removes the large-scale brightness drift that makes a repeated tile read as a grid of patches."""
    if strength <= 0:
        return a
    lum = _lum(a)
    low = soft_blur(lum, max(2, round(min(lum.shape) / 6)), wrap=wrap)
    out = a.copy()
    out[..., :3] += (strength * (lum.mean() - low))[..., None]
    return np.clip(out, 0, 1)


GRID_MODES = ("auto", "off")


def seamless_info(img: Image.Image, overlap: float = 0.125, feather: int = 3, flatten: float = 0.5,
                  grid: str = "auto") -> tuple[Image.Image, dict[str, Any]]:
    """Wrap-overlap quilting on both axes; the result is `overlap` smaller per axis (tile_resize restores the size).
    grid=auto: on a strongly periodic source each axis is first cropped so the quilt keeps whole periods."""
    if grid not in GRID_MODES:
        raise ValueError(f"grid must be one of {', '.join(GRID_MODES)}")
    a = _rgb(img)
    if min(a.shape[:2]) < MIN_TILE:
        raise ValueError(f"image too small for seamless ({img.width}x{img.height})")
    if flatten > 0:
        a = detrend(a)
    info: dict[str, Any] = {"grid": False}
    g = detect_grid(a) if grid == "auto" and min(a.shape[:2]) >= GRID_MIN_SIDE else None
    keystone: dict[str, Any] = {}
    if g and (g["x"]["strong"] or g["y"]["strong"]):
        a, pitch = rectify_pitch(a, g)
        flipped = {"x": g["y"], "y": g["x"]}
        t, yaw = rectify_pitch(np.ascontiguousarray(a.transpose(1, 0, 2)), flipped)
        a = np.ascontiguousarray(t.transpose(1, 0, 2))
        keystone = {**({"pitch": pitch} if pitch else {}), **({"yaw": yaw} if yaw else {})}
    ox, ncc_x = wrap_overlap(a, overlap) if g and g["x"]["strong"] else (None, 0.0)
    out = _quilt_x(a, overlap, feather, ox)
    t = np.ascontiguousarray(out.transpose(1, 0, 2))
    oy, ncc_y = wrap_overlap(t, overlap) if g and g["y"]["strong"] else (None, 0.0)
    out = _quilt_x(t, overlap, feather, oy).transpose(1, 0, 2)
    fallback = False
    if ox or oy:
        # Safety net for a wrong phase pick: the aligned quilt must not seam clearly worse than the plain one.
        plain = _quilt_x(a, overlap, feather)
        plain = _quilt_x(np.ascontiguousarray(plain.transpose(1, 0, 2)), overlap, feather).transpose(1, 0, 2)
        if _worst_seam(out) > max(1.1, 1.3 * _worst_seam(plain)):
            out, ox, oy, fallback = plain, None, None, True
    if g:
        info = {"grid": bool(ox or oy), "periods": g, "aligned": [k for k, o in (("x", ox), ("y", oy)) if o],
                "overlap_px": {"x": ox, "y": oy}, "match": {"x": round(ncc_x, 3), "y": round(ncc_y, 3)},
                **({"keystone": keystone} if keystone else {}), **({"fallback": "plain"} if fallback else {})}
    return _img(flatten_light(np.ascontiguousarray(out), flatten)), info


def _worst_seam(a: np.ndarray) -> float:
    # The wrap-aware blur stands in for tile_report's 4× reduction, which would crop a size that is not a multiple
    # of 4 and so compare the wrong columns. 99th percentile: an aligned wrap lands on a grout line, which is as
    # sharp as the few interior joint lines — at 95 a 1024px running bond read as a bad seam and fell back to plain.
    r = _seam_ratio(a, 99)
    r.update({f"{k}b": v for k, v in _seam_ratio(box_blur(a, 2), 99).items()})
    return max(r.values())


def seamless(img: Image.Image, overlap: float = 0.125, feather: int = 3, flatten: float = 0.5,
             grid: str = "auto") -> Image.Image:
    return seamless_info(img, overlap, feather, flatten, grid)[0]


def match_edges(img: Image.Image, base: Image.Image, overlap: float = 0.08, feather: int = 3,
                flatten: float = 0.5) -> Image.Image:
    """Variant whose outermost lines are `base`'s, joined by a narrow quilted window per edge, so variants of one
    tileable base tile with each other in any order while keeping their own content up to the windows."""
    if base.size != img.size:
        img = img.resize(base.size, Image.Resampling.LANCZOS)
    b = _rgb(base)
    a = flatten_light(detrend(_rgb(img)) if flatten > 0 else _rgb(img), flatten, wrap=False)
    # Variants from one prompt still differ in overall tone; matching mean/std keeps a mixed tiling from reading
    # as a checkerboard.
    am, asd = a.mean((0, 1)), a.std((0, 1)) + 1e-6
    a = np.clip((a - am) * (b.std((0, 1)) / asd) + b.mean((0, 1)), 0, 1).astype(np.float32)

    def edges(v: np.ndarray, ref: np.ndarray) -> np.ndarray:
        w = v.shape[1]
        o = _overlap(w, overlap)
        return np.concatenate([join(ref[:, :o], v[:, :o], feather), v[:, o:w - o],
                               join(v[:, w - o:], ref[:, w - o:], feather)], 1)

    out = edges(a, b)
    out = edges(out.transpose(1, 0, 2), b.transpose(1, 0, 2)).transpose(1, 0, 2)
    return _img(out)


def wrap_resize(img: Image.Image, w: int, h: int, resample: Image.Resampling = Image.Resampling.LANCZOS) -> Image.Image:
    """Resamples with the image wrap-padded by the filter support, so taps at the edges read the wrapped neighbours."""
    if img.size == (w, h):
        return img
    src = img.convert("RGBA")
    sw, sh = src.size
    mx = min(sw, math.ceil(3 * max(1.0, sw / w)) + 2)
    my = min(sh, math.ceil(3 * max(1.0, sh / h)) + 2)
    pad = np.pad(np.asarray(src), ((my, my), (mx, mx), (0, 0)), mode="wrap")
    return Image.fromarray(pad, "RGBA").resize((w, h), resample, box=(mx, my, mx + sw, my + sh))


def tile_grid(tiles: Sequence[Image.Image], n: int = 3, cell: int | None = None, seed: int = 0) -> Image.Image:
    """n×n preview; with several variants each cell picks one (fixed seed), which shows whether they mix."""
    cw = cell or tiles[0].width
    ch = round(cw * tiles[0].height / tiles[0].width)
    cells = [t.convert("RGBA").resize((cw, ch), Image.Resampling.LANCZOS) if t.size != (cw, ch) else t.convert("RGBA")
             for t in tiles]
    rng = np.random.default_rng(seed)
    out = Image.new("RGBA", (cw * n, ch * n))
    for y in range(n):
        for x in range(n):
            out.paste(cells[int(rng.integers(len(cells))) if len(cells) > 1 else 0], (x * cw, y * ch))
    return out


def _seam_ratio(a: np.ndarray, pct: float = 95) -> dict[str, float]:
    """Mean colour step across each wrap edge over a high percentile of the steps between interior neighbour lines
    (≤1 when seamless): grout or plank lines make some interior lines legitimately sharp, so the mean misleads."""
    out: dict[str, float] = {}
    for name, ax in (("x", 1), ("y", 0)):
        n = a.shape[ax]
        other = tuple(i for i in range(a.ndim) if i != ax)
        lines = np.abs(np.diff(a, axis=ax)).mean(axis=other)
        wrap = np.abs(np.take(a, [0], axis=ax) - np.take(a, [n - 1], axis=ax)).mean()
        out[name] = float(wrap / max(float(np.percentile(lines, pct)), 1e-4))
    return out


def _duplicate(a: np.ndarray) -> float:
    """Share of pixels equal to their half-tile partner, above what an unrelated shift gives: a tile made of two
    copies of itself shows as a visible grid at half its size."""
    h, w = a.shape[:2]

    def same(dy: int, dx: int) -> float:
        return float((np.abs(np.roll(a, (dy, dx), (0, 1)) - a).max(-1) < 2 / 255).mean())

    base = same(max(1, h // 3), max(1, w // 5))
    return max(0.0, max(same(0, w // 2), same(h // 2, 0), same(h // 2, w // 2)) - base)


def stamp_repeat(a: np.ndarray) -> dict[str, Any] | None:
    """A strong repeat whose period is a large part of the tile: the generator stamped one patch two or three times
    (grass blotches every half image), which tiling then multiplies. Short periods are bricks or floor tiles and do
    not count."""
    if min(a.shape[:2]) < GRID_MIN_SIDE:
        return None
    g = detect_grid(a)
    hits = {k: g[k]["period"] for k, n in (("x", a.shape[1]), ("y", a.shape[0]))
            if g[k]["periodic"] and g[k]["strength"] >= STAMP_STRENGTH and g[k]["period"] >= n / 3.5}
    return {"period": hits, "strength": max(g[k]["strength"] for k in hits)} if hits else None


def _cells(lum: np.ndarray, g: int) -> np.ndarray:
    h, w = lum.shape
    ch, cw = max(1, h // g), max(1, w // g)
    return lum[:ch * g, :cw * g].reshape(g, ch, g, cw).mean((1, 3))


def tile_report(img: Image.Image, min_score: float = 70.0) -> dict[str, Any]:
    a = _arr(img)[..., :3]
    h, w = a.shape[:2]
    ratios = _seam_ratio(a)
    if min(h, w) >= 64:
        # Fine noise hides a low-frequency jump at full resolution; a 4× box reduction exposes it.
        if h % 4 or w % 4:
            # Cropping to a multiple of 4 would compare the wrong columns across the wrap.
            small = _rgb(wrap_resize(img, w // 4, h // 4, Image.Resampling.BOX))
        else:
            small = a.reshape(h // 4, 4, w // 4, 4, 3).mean((1, 3))
        ratios.update({f"{k}4": v for k, v in _seam_ratio(small).items()})
    worst = max(ratios.values())
    seam_score = 100.0 * math.exp(-0.35 * max(0.0, worst - 1.1))

    lum = _lum(a)
    grid = _cells(lum, 8)
    yy, xx = np.mgrid[0:8, 0:8] / 7.0 - 0.5
    design = np.stack([np.ones(64), xx.ravel(), yy.ravel()], 1)
    coef, *_ = np.linalg.lstsq(design, grid.ravel(), rcond=None)
    tilt = float(math.hypot(coef[1], coef[2]))
    grad_pen = float(np.clip((tilt - 0.04) / 0.16, 0, 1))
    # Tile-sized blobs (one dark stone, a bright patch) are what the eye locks onto when the tile repeats.
    lowfreq = float(grid.std() / max(lum.std(), 1e-4))
    rep_pen = float(np.clip((lowfreq - 0.35) / 0.4, 0, 1))

    dup = _duplicate(a)
    dup_pen = float(np.clip((dup - 0.08) * 4, 0, 1))
    stamp = stamp_repeat(a)
    score = seam_score * (1 - 0.5 * grad_pen) * (1 - 0.25 * rep_pen) * (1 - dup_pen)
    # stamp is report-only: a regular large pattern can be intended, and the score should not fail it.
    issues = [k for k, bad in (("seam", seam_score < 70), ("gradient", grad_pen > 0.3), ("repetition", rep_pen > 0.5),
                               ("duplicate", dup > 0.08), ("self_repeat", stamp is not None and dup <= 0.08)) if bad]
    return {"score": round(score, 1), "seam_score": round(seam_score, 1),
            "seam_ratio": {k: round(v, 3) for k, v in ratios.items()},
            "gradient": round(tilt, 4), "lowfreq": round(lowfreq, 4), "duplicate": round(dup, 4),
            "self_repeat": stamp, "issues": issues, "passed": score >= min_score, "width": w, "height": h}


def height_normal(img: Image.Image, strength: float = 4.0, blur: int = 1, green: str = "opengl") -> Image.Image:
    """Tangent-space normal map from luminance as height (Sobel with wrap-around)."""
    hgt = soft_blur(_lum(_arr(img)), blur) if blur > 0 else _lum(_arr(img))
    sx = np.roll(hgt, -1, 1) - np.roll(hgt, 1, 1)
    sy = np.roll(hgt, -1, 0) - np.roll(hgt, 1, 0)
    dx = (np.roll(sx, 1, 0) + 2 * sx + np.roll(sx, -1, 0)) / 8
    dy = (np.roll(sy, 1, 1) + 2 * sy + np.roll(sy, -1, 1)) / 8
    # Image rows grow downwards; OpenGL (Unity, Godot) wants +G pointing up the texture.
    n = np.stack([-dx * strength, dy * strength if green == "opengl" else -dy * strength, np.ones_like(hgt)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    rgb = (n * 0.5 + 0.5)
    return _img(np.concatenate([rgb, np.ones((*hgt.shape, 1))], -1))


def tile_pixel(img: Image.Image, size: int, colors: int = 16, palette: str = "auto",
               dither: str = "none") -> tuple[Image.Image, list[str]]:
    check_params(colors, palette, dither)
    h = max(1, round(size * img.height / img.width))
    small = np.array(wrap_resize(img.convert("RGBA"), size, h, Image.Resampling.BOX))
    small[..., 3] = 255
    solid = np.ones(small.shape[:2], bool)
    # No outline and no despeckle: both treat the canvas border as an edge and would break the wrap.
    plan = plan_palette([(small, solid)], size / img.width, colors, palette, None, "none", dither, 0.6, False)
    return quantize(plan, small, solid), [to_hex(c) for c in plan.pal]


WANG_NAME = "wang.json"
WANG_TYPE = "corner-wang-2"
WANG_CORNERS = ("nw", "ne", "sw", "se")


def wang_labels(i: int) -> str:
    """Corner materials of tile i as 'a'/'b' in NW, NE, SW, SE order; bit 3 = NW … bit 0 = SE."""
    return "".join("ab"[(i >> (3 - k)) & 1] for k in range(4))


def wang_layout() -> list[dict[str, Any]]:
    """The 16 tiles of a 2-material corner Wang set, row-major in a 4×4 atlas. Two tiles fit side by side when the
    shared edge has the same two corner labels, so any grid of corner labels has exactly one tile per cell."""
    out = []
    for i in range(16):
        c = dict(zip(WANG_CORNERS, wang_labels(i)))
        out.append({"index": i, "name": f"wang_{i:02d}_{wang_labels(i)}", "col": i % 4, "row": i // 4, "corners": c,
                    "edges": {"n": c["nw"] + c["ne"], "e": c["ne"] + c["se"], "s": c["sw"] + c["se"],
                              "w": c["nw"] + c["sw"]}})
    return out


def wang_index(nw: int, ne: int, sw: int, se: int) -> int:
    return nw << 3 | ne << 2 | sw << 1 | se


def _znorm(x: np.ndarray) -> np.ndarray:
    return (x - x.mean()) / max(float(x.std()), 1e-6)


def wang_set(a_img: Image.Image, b_img: Image.Image, size: int, seed: int = 0,
             softness: float = 0.08) -> tuple[list[Image.Image], list[Image.Image]]:
    """Corner Wang tiles from two seamless materials: tile = mix(A, B, mask) at the same pixel positions, where the
    mask is the bilinear blend of the corner labels pushed around by tile-periodic noise and the height difference
    of the two materials. Along an edge the blend only depends on that edge's two corners, and A, B and the noise
    all wrap at the tile size, so neighbours that agree on their shared corners meet without a seam."""
    a = _rgb(wrap_resize(a_img, size, size))
    b = _rgb(wrap_resize(b_img, size, size))
    rng = np.random.default_rng(seed)
    noise = _znorm(0.8 * _znorm(soft_blur(rng.random((size, size)), max(2, size // 10)))
                   + 0.2 * _znorm(soft_blur(rng.random((size, size)), max(1, size // 24))))
    dh = _znorm(soft_blur(_lum(b), 3)) - _znorm(soft_blur(_lum(a), 3))
    push = 0.25 * noise + 0.05 * dh
    na = np.asarray(height_normal(_img(a)), np.float32)[..., :3] / 127.5 - 1
    nb = np.asarray(height_normal(_img(b)), np.float32)[..., :3] / 127.5 - 1
    u = ((np.arange(size) + 0.5) / size)[None, :]
    v = ((np.arange(size) + 0.5) / size)[:, None]
    tiles, normals = [], []
    for t in wang_layout():
        c = {k: float(t["corners"][k] == "b") for k in WANG_CORNERS}
        f = (c["nw"] * (1 - u) * (1 - v) + c["ne"] * u * (1 - v) + c["sw"] * (1 - u) * v + c["se"] * u * v)
        # 4f(1-f) keeps single-material corners pure: no noise where every corner agrees.
        m = np.clip((f + 4 * f * (1 - f) * push - 0.5) / softness + 0.5, 0, 1)
        m = (m * m * (3 - 2 * m))[..., None]
        tiles.append(_img(a * (1 - m) + b * m))
        n = na * (1 - m) + nb * m
        n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
        normals.append(_img(n * 0.5 + 0.5))
    return tiles, normals


def wang_atlas(tiles: Sequence[Image.Image]) -> Image.Image:
    s = tiles[0].width
    atlas = Image.new("RGBA", (s * 4, s * 4))
    for t, img in zip(wang_layout(), tiles):
        atlas.paste(img, (t["col"] * s, t["row"] * s))
    return atlas


def wang_arrangement(tiles: Sequence[Image.Image], n: int = 6, seed: int = 0, cell: int | None = None,
                     labels: np.ndarray | None = None) -> tuple[Image.Image, np.ndarray]:
    """n×n cells from a random (n+1)×(n+1) grid of corner labels, each cell the one tile matching its corners."""
    if labels is None:
        labels = np.random.default_rng(seed).integers(0, 2, (n + 1, n + 1))
    cw = cell or tiles[0].width
    cells = [t.convert("RGBA") if t.width == cw else t.convert("RGBA").resize((cw, cw), Image.Resampling.LANCZOS)
             for t in tiles]
    out = Image.new("RGBA", (cw * n, cw * n))
    for y in range(n):
        for x in range(n):
            i = wang_index(int(labels[y, x]), int(labels[y, x + 1]), int(labels[y + 1, x]), int(labels[y + 1, x + 1]))
            out.paste(cells[i], (x * cw, y * cw))
    return out, labels


def wang_report(tiles: Sequence[Image.Image], min_score: float = 70.0, n: int = 6, seed: int = 0) -> dict[str, Any]:
    """Seam score of a random valid arrangement: colour step across the tile boundaries over the 95th percentile of
    the steps between neighbouring interior lines (≤1 when the set joins cleanly)."""
    img, _ = wang_arrangement(tiles, n, seed)
    a = _arr(img)[..., :3]
    cw = tiles[0].width
    ratios = {}
    for name, ax in (("x", 1), ("y", 0)):
        steps = np.abs(np.diff(a, axis=ax)).mean(axis=(0, 2) if ax == 1 else (1, 2))
        bounds = np.arange(cw - 1, a.shape[ax] - 1, cw)
        inner = np.delete(steps, bounds)
        ratios[name] = float(steps[bounds].mean() / max(float(np.percentile(inner, 95)), 1e-4))
    worst = max(ratios.values())
    score = 100.0 * math.exp(-0.35 * max(0.0, worst - 1.1))
    return {"score": round(score, 1), "seam_score": round(score, 1),
            "seam_ratio": {k: round(v, 3) for k, v in ratios.items()}, "passed": score >= min_score,
            "issues": ["seam"] if score < 70 else [], "arrangement": n}


def _stem(ref: BlobRef, i: int) -> str:
    return (ref.name or f"texture_{i:02d}").rsplit(".", 1)[0]


def _enabled(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "")
    return v is None or bool(v)


class _TileOp(OpExecutor):
    @staticmethod
    def _tick(ctx: StepContext, done: int, total: int, label: str | None = None) -> None:
        if ctx.cancelled.is_set():
            raise asyncio.CancelledError
        ctx.progress(done, total, label=label)


TILESET_MODES = ("edge", "wang")


class Seamless(_TileOp):
    kind = "op.seamless"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        name = str(params.get("name") or "texture")
        mode = str(params.get("mode") or "edge")
        grid = str(params.get("grid") or "auto")
        if mode not in TILESET_MODES or grid not in GRID_MODES:
            raise StepError("op_bad_params", f"mode must be {'/'.join(TILESET_MODES)}, grid {'/'.join(GRID_MODES)}")
        group = int(params.get("group") or 0)
        if mode == "wang":
            if len(refs) < 2:
                raise StepError("op_bad_params", "wang mode needs two images: two prompt lines, or count ≥ 2")
        elif 0 < group < len(refs):
            # Edge tilesets are variants of one material: a second prompt line (meant for Wang) is not mixed in.
            refs = refs[:group]
        match = mode == "edge" and _enabled(params.get("match_edges", False)) and len(refs) > 1
        feather, flatten = int(params.get("feather", 3)), float(params.get("flatten", 0.5))
        overlap, edge_overlap = float(params.get("overlap", 0.125)), float(params.get("edge_overlap", 0.08))
        if not (0.02 <= overlap <= 0.3 and 0.02 <= edge_overlap <= 0.3 and 0 <= feather <= 16):
            raise StepError("op_bad_params", "overlap/edge_overlap must be within 0.02..0.3, feather 0..16 px")
        if mode == "wang":
            return await self._wang_pair(ctx, refs, max(1, group or 1), overlap, feather, flatten, grid)
        out: list[BlobRef] = []
        grids: list[dict[str, Any]] = []
        base: Image.Image | None = None
        for i, ref in enumerate(refs):
            self._tick(ctx, i, len(refs), "seamless")
            src = await _load(ctx, ref)
            try:
                if base is not None:
                    res = await asyncio.to_thread(match_edges, src, base, edge_overlap, feather, flatten)
                    info: dict[str, Any] = {"grid": False, "matched": True}
                else:
                    res, info = await asyncio.to_thread(seamless_info, src, overlap, feather, flatten, grid)
            except ValueError as e:
                raise StepError("op_bad_params", str(e)) from e
            grids.append(info)
            if match and base is None:
                base = res
            label = f"{name}_{i + 1:02d}" if len(refs) > 1 else name
            out.append(await _put_png(ctx, res, f"{label}_seamless.png"))
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": out},
                          meta={"tileable": True, "match_edges": match, "count": len(out), "mode": mode,
                                "grid": grids})


    async def _wang_pair(self, ctx: StepContext, refs: list[BlobRef], group: int, overlap: float, feather: int,
                         flatten: float, grid: str) -> StepResult:
        """Material A = the best-scoring tile of the first prompt line's images, B = the best of the second line's
        (or, with one line, the runner-up), so every extra candidate the generator made is put to use."""
        lines = [refs[:group], refs[group:]] if len(refs) > group else [refs]
        scored: list[list[tuple[float, Image.Image, dict[str, Any]]]] = []
        done = 0
        for cands in lines:
            row = []
            for ref in cands:
                self._tick(ctx, done, len(refs), "seamless")
                done += 1
                try:
                    res, info = await asyncio.to_thread(seamless_info, await _load(ctx, ref), overlap, feather,
                                                        flatten, grid)
                except ValueError as e:
                    raise StepError("op_bad_params", str(e)) from e
                score = (await asyncio.to_thread(tile_report, res))["score"]
                row.append((score, res, {**info, "score": score, "source": ref.hash}))
            scored.append(sorted(row, key=lambda r: -r[0]))
        pair = [scored[0][0], scored[1][0]] if len(scored) > 1 else scored[0][:2]
        out = [await _put_png(ctx, res, f"wang_{'ab'[i]}_seamless.png") for i, (_, res, _) in enumerate(pair)]
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": out}, meta={"tileable": True, "match_edges": False, "count": 2,
                                                         "mode": "wang", "grid": [p[2] for p in pair]})


class TileWang(_TileOp):
    kind = "op.tile_wang"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        if str(params.get("mode") or "wang") != "wang":
            return StepResult(outputs={"images": list(refs), "nmap": [], "atlas": [], "wang": []},
                              meta={"skipped": True})
        size, seed = int(params.get("size") or 512), int(params.get("seed") or 0)
        softness = float(params.get("softness", 0.08))
        if not MIN_TILE <= size <= 2048 or not 0.01 <= softness <= 0.5:
            raise StepError("op_bad_params", f"size must be within {MIN_TILE}..2048, softness 0.01..0.5")
        if len(refs) < 2:
            raise StepError("op_bad_params", "op.tile_wang needs two seamless materials (A, B)")
        a, b = await _load(ctx, refs[0]), await _load(ctx, refs[1])
        self._tick(ctx, 0, 3, "wang")
        tiles, normals = await asyncio.to_thread(wang_set, a, b, size, seed, softness)
        self._tick(ctx, 1, 3, "wang")
        layout = wang_layout()
        imgs = [await _put_png(ctx, t, f"{e['name']}.png") for t, e in zip(tiles, layout)]
        atlases = [await _put_png(ctx, await asyncio.to_thread(wang_atlas, tiles), "wang.png")]
        nmaps: list[BlobRef] = []
        if _enabled(params.get("normal", True)):
            nmaps = [await _put_png(ctx, n, f"{e['name']}_normal.png") for n, e in zip(normals, layout)]
            atlases.append(await _put_png(ctx, await asyncio.to_thread(wang_atlas, normals), "wang_normal.png"))
        doc = {"type": WANG_TYPE, "materials": ["a", "b"], "corner_order": list(WANG_CORNERS),
               "tile_size": size, "grid": [4, 4], "atlas": "wang.png", "tiles": layout,
               "rule": "neighbours must share the labels of their common corners; tile index = "
                       "nw*8 + ne*4 + sw*2 + se (a=0, b=1), atlas cell (index % 4, index // 4)"}
        wang = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=WANG_NAME, mime="application/json")
        ctx.progress(3, 3)
        return StepResult(outputs={"images": imgs, "nmap": nmaps, "atlas": atlases, "wang": [wang]},
                          meta={"wang": True, "tiles": 16, "size": size, "tileable": True})


class TileResize(_TileOp):
    kind = "op.tile_resize"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        size = int(params.get("size") or 0)
        levels = int(params.get("levels") or 0)
        square = _enabled(params.get("square", False)) and bool(size)
        if size and not MIN_TILE <= size <= 4096:
            raise StepError("op_bad_params", f"size must be within {MIN_TILE}..4096")
        if not 0 <= levels <= 6:
            raise StepError("op_bad_params", "levels must be within 0..6")
        main: list[BlobRef] = []
        sizes: list[BlobRef] = []
        for i, ref in enumerate(refs):
            self._tick(ctx, i, len(refs), "resize")
            src = await _load(ctx, ref)
            w = size or src.width
            # square: grid alignment and keystone correction leave the tile a few % off square; engines want n×n.
            h = w if square else max(1, round(w * src.height / src.width))
            top = await asyncio.to_thread(wrap_resize, src, w, h)
            main.append(ref if (w, h) == src.size else await _put_png(ctx, top, ref.name or f"texture_{i:02d}.png"))
            for k in range(1, levels + 1):
                lw, lh = w >> k, h >> k
                if min(lw, lh) < MIN_TILE:
                    break
                small = await asyncio.to_thread(wrap_resize, top, lw, lh)
                sizes.append(await _put_png(ctx, small, f"{_stem(ref, i)}_{lw}.png"))
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": main, "sizes": sizes}, meta={"tileable": True, "size": size or None})


class TileNormal(_TileOp):
    kind = "op.tile_normal"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        if not _enabled(params.get("enabled", True)):
            return StepResult(outputs={"images": []}, meta={"skipped": True})
        refs = _images_in(inputs)
        if inputs.get("nmap"):
            # Wang tiles bring their own normals: a per-tile wrap-around Sobel would read the opposite edge.
            return StepResult(outputs={"images": list(inputs["nmap"])}, meta={"normal": True, "passthrough": True})
        strength, blur = float(params.get("strength", 4.0)), int(params.get("blur", 1))
        green = str(params.get("green") or "opengl")
        if green not in ("opengl", "directx"):
            raise StepError("op_bad_params", "green must be opengl or directx")
        out: list[BlobRef] = []
        for i, ref in enumerate(refs):
            self._tick(ctx, i, len(refs), "normal")
            src = await _load(ctx, ref)
            res = await asyncio.to_thread(height_normal, src, strength, blur, green)
            out.append(await _put_png(ctx, res, f"{_stem(ref, i)}_normal.png"))
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": out}, meta={"normal": True, "tileable": True, "green": green})


class TilePixel(_TileOp):
    kind = "op.tile_pixel"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        size = int(params.get("size") or 0)
        if not size:
            return StepResult(outputs={"images": [], "palette": []}, meta={"skipped": True})
        if not 8 <= size <= 512:
            raise StepError("op_bad_params", "size must be within 8..512 (0 = off)")
        refs = _images_in(inputs)
        colors, palette = int(params.get("colors") or 16), str(params.get("palette") or "auto")
        dither = str(params.get("dither") or "none")
        out: list[BlobRef] = []
        pals: list[list[str]] = []
        for i, ref in enumerate(refs):
            self._tick(ctx, i, len(refs), "pixel")
            src = await _load(ctx, ref)
            try:
                res, pal = await asyncio.to_thread(tile_pixel, src, size, colors, palette, dither)
            except ValueError as e:
                raise StepError("op_bad_params", str(e)) from e
            pals.append(pal)
            out.append(await _put_png(ctx, res, f"{_stem(ref, i)}_pixel.png"))
        info = {"colors": pals[0] if len(pals) == 1 else pals, "size": size, "tileable": True}
        pal_ref = await ctx.blobs.put_bytes(json.dumps(info, indent=1).encode(), name="palette.json",
                                            mime="application/json")
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": out, "palette": [pal_ref]},
                          meta={"pixel": True, "filter": "point", "tileable": True, "frame_w": size})


class TileCheck(_TileOp):
    kind = "op.tile_check"

    @staticmethod
    async def _wang_doc(ctx: StepContext, inputs: Outputs) -> dict[str, Any] | None:
        for ref in inputs.get("wang") or []:
            try:
                doc = json.loads(ctx.blobs.path(ref.hash).read_bytes())
            except (OSError, ValueError):
                continue
            if isinstance(doc, dict) and doc.get("type") == WANG_TYPE:
                return doc
        return None

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        min_score = float(params.get("min_score", 70))
        grid = int(params.get("grid", 3))
        if not 2 <= grid <= 8:
            raise StepError("op_bad_params", "grid must be within 2..8")
        wang = await self._wang_doc(ctx, inputs)
        imgs, reports = [], []
        for i, ref in enumerate(refs):
            self._tick(ctx, i, len(refs) + 1, "check")
            img = await _load(ctx, ref)
            imgs.append(img)
            if wang is None:
                rep = await asyncio.to_thread(tile_report, img, min_score)
                reports.append({"name": ref.name, "hash": ref.hash, **rep})
        outputs: Outputs = {"images": list(refs)}
        if wang is not None:
            if len(imgs) != 16:
                raise StepError("op_bad_params", f"a Wang set has 16 tiles, got {len(imgs)}")
            n = max(grid, 6)
            rep = await asyncio.to_thread(wang_report, imgs, min_score, n)
            reports = [{"name": refs[0].name, "hash": refs[0].hash, **rep}]
            cell = max(MIN_TILE, min(imgs[0].width, int(params.get("preview_px", 1536)) // n))
            preview, _ = await asyncio.to_thread(wang_arrangement, imgs, n, 0, cell)
        else:
            cell = max(MIN_TILE, min(imgs[0].width, int(params.get("preview_px", 1536)) // grid))
            preview = await asyncio.to_thread(tile_grid, imgs, grid, cell)
        worst = min(reports, key=lambda r: r["score"])
        outputs["preview"] = [await _put_png(ctx, preview, PREVIEW_NAME)]
        if inputs.get("atlas"):
            outputs["atlas"] = list(inputs["atlas"])
        elif len(imgs) > 1:
            atlas = Image.new("RGBA", (imgs[0].width * len(imgs), imgs[0].height))
            for i, img in enumerate(imgs):
                atlas.paste(img.resize(imgs[0].size) if img.size != imgs[0].size else img, (i * imgs[0].width, 0))
            outputs["atlas"] = [await _put_png(ctx, atlas, ATLAS_NAME)]
        for port in ("nmap", "pixel", "sizes", "wang"):
            if inputs.get(port):
                outputs[port] = list(inputs[port])
        report = {"tileable": True, "score": worst["score"], "seam_score": worst["seam_score"],
                  "passed": all(r["passed"] for r in reports), "min_score": min_score, "grid": grid,
                  "variants": len(imgs), "textures": reports,
                  "normal": [r.hash for r in inputs.get("nmap") or []],
                  "pixel": [r.hash for r in inputs.get("pixel") or []],
                  "sizes": [r.hash for r in inputs.get("sizes") or []]}
        if wang is not None:
            report["wang"] = {"type": WANG_TYPE, "arrangement": n,
                              "tiles": [{"hash": r.hash, **e} for r, e in zip(refs, wang_layout())]}
        outputs["report"] = [await ctx.blobs.put_bytes(json.dumps(report, indent=1).encode(), name=REPORT_NAME,
                                                       mime="application/json")]
        if _enabled(params.get("fail", False)) and not report["passed"]:
            raise StepError("tile_check_failed", f"seam score {worst['score']} < {min_score}",
                            detail={"report": report})
        ctx.progress(len(refs) + 1, len(refs) + 1)
        return StepResult(outputs=outputs, meta={"tileable": True, "score": report["score"],
                                                 "seam_score": report["seam_score"], "passed": report["passed"]})


TILE_OPS: tuple[type[OpExecutor], ...] = (Seamless, TileWang, TileResize, TileNormal, TilePixel, TileCheck)
__all__ = ["PALETTE_CHOICES", "TILE_OPS", "seamless", "seamless_info", "match_edges", "tile_report", "height_normal",
           "wrap_resize", "wang_set", "wang_layout", "wang_index", "wang_labels", "wang_arrangement", "wang_report"]
