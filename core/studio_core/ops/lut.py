"""Day/night/season variants: `op.lut_prep` (opaque reference for Klein) and `op.fit_lut` (3D LUT original -> each Klein
variant by regularized least squares, the LUT applied back to the original, optional seam fix, .cube + 1024x32 strip,
labelled grid preview, lut.json)."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png
from .tile import _enabled, _overlap, _seam_ratio, match_edges

LUT_FILE = "lut.json"
LUT_TYPE = "asset-studio/color-lut"
GRID = 33
STRIP = 32
FIT_SIDE = 256
CG_TOL = 1e-5
JACOBI_TRIES = 6
DE_SIDE = 512
SEAM_MAX = 1.0
REF_BG = (128, 128, 128)
SEAMLESS_MODES = ("auto", "on", "off")
FINAL_MODES = ("edit", "lut")
DEFAULT_VARIANTS = ("dusk, warm orange sunset light, long soft shadows",
                    "night, dark blue moonlight, deep shadows",
                    "winter, cold pale light, light snow on top surfaces",
                    "autumn, orange and red foliage, warm low sun")


def variant_names(raw: Any) -> list[str]:
    """One name per non-empty line: the text before the first comma/colon, as a file-safe identifier."""
    lines = [ln.strip() for ln in str(raw or "").splitlines() if ln.strip()] or list(DEFAULT_VARIANTS)
    out: list[str] = []
    for i, ln in enumerate(lines):
        base = re.sub(r"[^a-z0-9]+", "_", re.split(r"[,:]", ln, maxsplit=1)[0].lower()).strip("_")
        base = base[:24] or f"variant_{i + 1:02d}"
        name, k = base, 2
        while name in out:
            name, k = f"{base}_{k}", k + 1
        out.append(name)
    return out


def _corners(c: np.ndarray, n: int) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Flat cell indices ((b * n + g) * n + r, .cube order) and trilinear weights of the 8 cells around each colour."""
    pos = np.clip(c, 0, 1) * (n - 1)
    i0 = np.minimum(np.floor(pos).astype(np.int64), n - 2)
    f = pos - i0
    idx, wts = [], []
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                d = np.array([dr, dg, db])
                ii = i0 + d
                w = np.prod(np.where(d == 1, f, 1 - f), axis=1)
                idx.append((ii[:, 2] * n + ii[:, 1]) * n + ii[:, 0])
                wts.append(w)
    return idx, wts


def identity_lut(n: int = GRID) -> np.ndarray:
    """(n, n, n, 3) indexed [b, g, r]."""
    t = np.linspace(0, 1, n, dtype=np.float64)
    b, g, r = np.meshgrid(t, t, t, indexing="ij")
    return np.stack([r, g, b], -1)


def apply_lut(lut: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    n = lut.shape[0]
    flat = lut.reshape(-1, 3)
    c = rgb.reshape(-1, 3).astype(np.float64)
    idx, wts = _corners(c, n)
    out = sum(w[:, None] * flat[i] for i, w in zip(idx, wts))
    return np.clip(out, 0, 1).reshape(rgb.shape).astype(np.float32)


def _bin_pairs(src: np.ndarray, dst: np.ndarray, levels: int = 128) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pairs averaged per quantized source colour: flat areas collapse to one weighted sample."""
    q = np.clip(np.round(src * (levels - 1)), 0, levels - 1).astype(np.int64)
    key = (q[:, 2] * levels + q[:, 1]) * levels + q[:, 0]
    uniq, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    s = np.stack([np.bincount(inv, src[:, k], len(uniq)) for k in range(3)], 1) / cnt[:, None]
    d = np.stack([np.bincount(inv, dst[:, k], len(uniq)) for k in range(3)], 1) / cnt[:, None]
    return s, d, cnt.astype(np.float64)


_CORNER_OFFSETS = [(db, dg, dr) for db in (0, 1) for dg in (0, 1) for dr in (0, 1)]


def _normal_stencil(idx: list[np.ndarray], wts: list[np.ndarray], weight: np.ndarray, m: int) -> np.ndarray:
    """A^T W A as 27 per-cell coefficients (one per neighbour offset): every sample only touches the 2x2x2 cells
    around it, so the data term is a stencil and each CG step costs 27 shifted multiplies, not a gather/scatter
    over all samples."""
    st = np.zeros((27, m))
    for a, (ia, wa, da) in enumerate(zip(idx, wts, _CORNER_OFFSETS)):
        wa = wa * weight
        for wb, db in zip(wts, _CORNER_OFFSETS):
            k = (db[0] - da[0] + 1) * 9 + (db[1] - da[1] + 1) * 3 + (db[2] - da[2] + 1)
            st[k] += np.bincount(ia, wa * wb, m)
    return st


def _restrict(r: np.ndarray, n: int) -> np.ndarray:
    """Adjoint of `_prolong`: (n³, 3) fine field to the ((n+1)/2)³ grid."""
    g = r.reshape(n, n, n, 3)
    for ax in range(3):
        ev = np.take(g, range(0, n, 2), ax).copy()
        od = np.take(g, range(1, n, 2), ax) * 0.5
        lo, hi = [slice(None)] * 4, [slice(None)] * 4
        lo[ax], hi[ax] = slice(None, -1), slice(1, None)
        ev[tuple(hi)] += od
        ev[tuple(lo)] += od
        g = ev
    return g.reshape(-1, 3)


def _stencil_op(st: np.ndarray, n: int):
    """Matvec of a 27-per-cell stencil on the n³ grid (zero outside), same flat-shift scheme as `_solve`."""
    q = n + 2
    shifts = [ob * q * q + og * q + orr for ob in (-1, 0, 1) for og in (-1, 0, 1) for orr in (-1, 0, 1)]
    lo, hi = q * q + q + 1, q ** 3 - q * q - q - 1
    st_pad = np.zeros((27, q, q, q))
    st_pad[:, 1:-1, 1:-1, 1:-1] = st.reshape(27, n, n, n)
    st_pad = st_pad.reshape(27, -1)[:, lo:hi]
    xpad = np.zeros((3, q, q, q))
    xflat = xpad.reshape(3, -1)
    ypad = np.zeros((3, q ** 3))
    tmp = np.empty((3, hi - lo))

    def op(x: np.ndarray) -> np.ndarray:
        xpad[:, 1:-1, 1:-1, 1:-1] = x.reshape(n, n, n, 3).transpose(3, 0, 1, 2)
        ypad[:] = 0
        for c, sh in zip(st_pad, shifts):
            np.multiply(c, xflat[:, lo + sh:hi + sh], out=tmp)
            ypad[:, lo:hi] += tmp
        return ypad.reshape(3, q, q, q)[:, 1:-1, 1:-1, 1:-1].transpose(1, 2, 3, 0).reshape(-1, 3)
    return op


def _galerkin(op, n: int) -> np.ndarray:
    """Coarse operator R A P as a 27-stencil. A cell only couples to coarse neighbours within one step, so probing
    with the 27 residue classes (coordinates mod 3) of coarse cells, three per call, reads every coefficient."""
    nc = (n - 1) // 2 + 1
    mc = nc ** 3
    cb, cg, cr = np.meshgrid(*(np.arange(nc),) * 3, indexing="ij")
    cls = ((cb % 3) * 9 + (cg % 3) * 3 + cr % 3).reshape(-1)
    probe = np.empty((27, mc))
    for k in range(0, 27, 3):
        e = np.stack([(cls == k + j).astype(np.float64) for j in range(3)], 1)
        probe[k:k + 3] = _restrict(op(_prolong(e, nc)), n).T
    st = np.zeros((27, mc))
    coords = (cb.reshape(-1), cg.reshape(-1), cr.reshape(-1))
    for kk, (db, dg, dr) in enumerate((b, g, r) for b in (-1, 0, 1) for g in (-1, 0, 1) for r in (-1, 0, 1)):
        jb, jg, jr = coords[0] + db, coords[1] + dg, coords[2] + dr
        ok = (jb >= 0) & (jb < nc) & (jg >= 0) & (jg < nc) & (jr >= 0) & (jr < nc)
        jcls = (jb % 3) * 9 + (jg % 3) * 3 + jr % 3
        st[kk, ok] = probe[jcls[ok], np.flatnonzero(ok)]
    return st


def _dense_from_stencil(st: np.ndarray, n: int) -> np.ndarray:
    m = n ** 3
    cb, cg, cr = np.meshgrid(*(np.arange(n),) * 3, indexing="ij")
    cb, cg, cr = cb.reshape(-1), cg.reshape(-1), cr.reshape(-1)
    mat = np.zeros((m, m))
    for kk, (db, dg, dr) in enumerate((b, g, r) for b in (-1, 0, 1) for g in (-1, 0, 1) for r in (-1, 0, 1)):
        jb, jg, jr = cb + db, cg + dg, cr + dr
        ok = (jb >= 0) & (jb < n) & (jg >= 0) & (jg < n) & (jr >= 0) & (jr < n)
        i = np.flatnonzero(ok)
        mat[i, (jb[ok] * n + jg[ok]) * n + jr[ok]] += st[kk, i]
    return mat


def _lambda_max(op, dinv: np.ndarray, m: int, steps: int = 6) -> float:
    v = np.random.default_rng(0).random((m, 3)) + 0.5
    lam = 1.0
    for _ in range(steps):
        w = dinv[:, None] * op(v)
        lam = float(np.sqrt((w * w).sum() / (v * v).sum()))
        v = w / (np.sqrt((w * w).sum()) + 1e-300)
    return lam * 1.1


def _smooth(op, dinv: np.ndarray, lam: float, b: np.ndarray, x: np.ndarray | None, degree: int = 2) -> np.ndarray:
    """Chebyshev smoother damping the upper part [lam/4, lam] of D^-1 A's spectrum; symmetric when used before and
    after the coarse correction."""
    lo, hi = lam / 4, lam
    theta, delta = (hi + lo) / 2, (hi - lo) / 2
    sigma = theta / delta
    rho = 1 / sigma
    if x is None:
        x, r = np.zeros_like(b), b.copy()
    else:
        r = b - op(x)
    d = dinv[:, None] * r / theta
    for k in range(degree):
        x = x + d
        if k == degree - 1:
            break
        r = r - op(d)
        rho_new = 1 / (2 * sigma - rho)
        d = rho_new * rho * d + 2 * rho_new / delta * dinv[:, None] * r
        rho = rho_new
    return x


def _mg_precond(op, diag: np.ndarray, n: int):
    """Symmetric V-cycle (Chebyshev smoothing, Galerkin coarse operators, dense solve at the bottom) as the CG
    preconditioner: Jacobi alone leaves the smooth error modes for hundreds of CG steps."""
    levels = [(n, op, 1.0 / diag)]
    lam = [_lambda_max(op, levels[0][2], n ** 3)]
    cur_n, cur_op = n, op
    while cur_n > 5 and cur_n % 2 == 1:
        st_c = _galerkin(cur_op, cur_n)
        cur_n = (cur_n - 1) // 2 + 1
        cur_op = _stencil_op(st_c, cur_n)
        dinv = 1.0 / np.maximum(st_c[13], 1e-30)
        levels.append((cur_n, cur_op, dinv))
        coarse_st = st_c
        if cur_n > 5 and cur_n % 2 == 1:
            lam.append(_lambda_max(cur_op, dinv, cur_n ** 3))
    if len(levels) == 1 or cur_n ** 3 > 2000:
        return None
    mat = _dense_from_stencil(coarse_st, cur_n)
    inv = np.linalg.pinv(mat, hermitian=True)

    def cycle(lv: int, b: np.ndarray) -> np.ndarray:
        nl, lop, dinv = levels[lv]
        if lv == len(levels) - 1:
            return inv @ b
        x = _smooth(lop, dinv, lam[lv], b, None)
        rc = _restrict(b - lop(x), nl)
        x = x + _prolong(cycle(lv + 1, rc), levels[lv + 1][0])
        return _smooth(lop, dinv, lam[lv], b, x)
    return lambda r: cycle(0, r)


def _solve(idx: list[np.ndarray], wts: list[np.ndarray], weight: np.ndarray, rhs_samples: np.ndarray, m: int,
           n: int, smooth: float, iters: int, x0: np.ndarray | None = None,
           stats: dict | None = None) -> np.ndarray:
    def splat(v: np.ndarray) -> np.ndarray:
        return np.stack([sum(np.bincount(i, w * v[:, k], m) for i, w in zip(idx, wts)) for k in range(3)], 1)

    st = _normal_stencil(idx, wts, weight, m)
    diag = st[13]
    occupied = diag[diag > 1e-12]
    # Data weights scaled so an occupied cell weighs ~1: `smooth` then means the same thing for any image size.
    alpha = 1.0 / float(occupied.mean()) if len(occupied) else 1.0
    mu = 1e-6
    edge = (np.arange(n) == 0) | (np.arange(n) == n - 1)
    deg = 6.0 - (edge[:, None, None].astype(float) + edge[None, :, None] + edge[None, None, :]).reshape(-1)
    # Flat padded grid: a neighbour offset is then one contiguous slice shift, far cheaper than strided 3D slices.
    q = n + 2
    shifts = np.array([ob * q * q + og * q + orr for ob in (-1, 0, 1) for og in (-1, 0, 1) for orr in (-1, 0, 1)])
    lo = q * q + q + 1
    hi = q ** 3 - lo
    xpad = np.zeros((3, q, q, q))
    xflat = xpad.reshape(3, -1)
    ypad = np.zeros((3, q ** 3))
    tmp = np.empty((3, hi - lo))
    st = st * alpha
    precond = 1.0 / (st[13] + smooth * deg + mu)
    cells = np.flatnonzero(np.abs(st).max(0) > 0)
    dense = len(cells) * 3 > m
    interior = np.zeros((q, q, q), bool)
    interior[1:-1, 1:-1, 1:-1] = True
    pos = np.flatnonzero(interior.reshape(-1))[cells]
    if dense:
        st[13] += smooth * deg + mu
        st[[4, 10, 12, 14, 16, 22]] -= smooth
        st_pad = np.zeros((27, q, q, q))
        st_pad[:, 1:-1, 1:-1, 1:-1] = st.reshape(27, n, n, n)
        st_pad = st_pad.reshape(27, -1)
    else:
        # Mostly empty grid (flat or tiny images): data stencil only where samples land, Laplacian as 7 shifts.
        cols = pos[:, None] + shifts[None, :]
        cd = st[:, cells].T
        lap_cell = np.zeros((q, q, q))
        lap_cell[1:-1, 1:-1, 1:-1] = (smooth * deg + mu).reshape(n, n, n)
        lap_cell = lap_cell.reshape(-1)
        axis_shifts = shifts[[4, 10, 12, 14, 16, 22]]

    def op(x: np.ndarray) -> np.ndarray:
        xpad[:, 1:-1, 1:-1, 1:-1] = x.reshape(n, n, n, 3).transpose(3, 0, 1, 2)
        if dense:
            ypad[:] = 0
            for c, s in zip(st_pad, shifts):
                np.multiply(c[lo:hi], xflat[:, lo + s:hi + s], out=tmp)
                ypad[:, lo:hi] += tmp
        else:
            ypad[:] = 0
            nb = ypad[:, lo:hi]
            for s in axis_shifts:
                nb += xflat[:, lo + s:hi + s]
            ypad[:, lo:hi] = lap_cell[lo:hi] * xflat[:, lo:hi] - smooth * nb
            ypad[:, pos] += np.einsum("lk,clk->cl", cd, xflat[:, cols])
        return ypad.reshape(3, q, q, q)[:, 1:-1, 1:-1, 1:-1].transpose(1, 2, 3, 0).reshape(m, 3)

    b = alpha * splat(rhs_samples * weight[:, None])
    b_norm = np.sqrt((b * b).sum(0)) + 1e-12
    x = np.zeros((m, 3)) if x0 is None else x0.copy()

    def cg(pre, x: np.ndarray, budget: int) -> tuple[np.ndarray, bool]:
        r = b - op(x)
        z = pre(r)
        p = z.copy()
        rz = (r * z).sum(0)
        for _ in range(budget):
            if stats is not None:
                stats["iters"] = stats.get("iters", 0) + 1
            ap = op(p)
            a = rz / np.maximum((p * ap).sum(0), 1e-30)
            x += a * p
            r -= a * ap
            if np.all(np.sqrt((r * r).sum(0)) / b_norm < CG_TOL):
                return x, True
            z = pre(r)
            rz_new = (r * z).sum(0)
            p = z + (rz_new / np.maximum(rz, 1e-30)) * p
            rz = rz_new
        return x, False

    # Data-dominated systems converge in a few Jacobi steps; the multigrid setup only pays off for the rest.
    x, done = cg(lambda v: v * precond[:, None], x, min(iters, JACOBI_TRIES))
    if done or iters <= JACOBI_TRIES:
        return x
    mg = _mg_precond(op, 1.0 / precond, n)
    x, _ = cg(mg if mg is not None else (lambda v: v * precond[:, None]), x, iters - JACOBI_TRIES)
    return x


def _prolong(x: np.ndarray, nc: int) -> np.ndarray:
    """Coarse (nc³, 3) field to the (2nc-1)³ grid whose even nodes coincide with it, linear in between."""
    g = x.reshape(nc, nc, nc, 3)
    for ax in range(3):
        mid = (np.take(g, range(nc - 1), ax) + np.take(g, range(1, nc), ax)) / 2
        new = np.empty(g.shape[:ax] + (2 * nc - 1,) + g.shape[ax + 1:])
        sl = [slice(None)] * 4
        sl[ax] = slice(0, None, 2)
        new[tuple(sl)] = g
        sl[ax] = slice(1, None, 2)
        new[tuple(sl)] = mid
        g = new
    return g.reshape(-1, 3)


def fit_lut(src: np.ndarray, dst: np.ndarray, n: int = GRID, smooth: float = 0.3, iters: int = 400,
            robust: bool = True) -> np.ndarray:
    """LUT L minimising sum w_i |L(src_i) - dst_i|^2 (trilinear) + smooth * sum |L_a - L_b|^2 over grid neighbours.

    Solved for the offset L - identity, so cells no colour lands in take their neighbours' shift (harmonic fill)
    instead of being pulled to black. With `robust`, a second pass drops pairs whose residual is far above the rest:
    content Klein added (snow, leaves) is not a colour mapping and would otherwise bend the whole LUT."""
    s, d, cnt = _bin_pairs(src.reshape(-1, 3).astype(np.float64), dst.reshape(-1, 3).astype(np.float64))
    ident = identity_lut(n).reshape(-1, 3)
    m = n ** 3
    weight = np.sqrt(cnt)
    idx, wts = _corners(s, n)
    off = _solve(idx, wts, weight, d - s, m, n, smooth, iters)
    if robust and len(s) > 64:
        pred = s + sum(w[:, None] * off[i] for i, w in zip(idx, wts))
        res = np.sqrt(((pred - d) ** 2).sum(1))
        cut = max(3.0 * float(np.median(res)), 0.06)
        keep = res <= cut
        if keep.sum() >= 32 and not keep.all():
            weight = np.where(keep, weight, 0.0)
            off = _solve(idx, wts, weight, d - s, m, n, smooth, iters, x0=off)
    return np.clip(ident + off, 0, 1).reshape(n, n, n, 3)


def cube_text(lut: np.ndarray, title: str) -> str:
    n = lut.shape[0]
    rows = "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in lut.reshape(-1, 3))
    return (f'TITLE "{title}"\n# AI Asset Studio: original -> {title}\nLUT_3D_SIZE {n}\n'
            f"DOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n{rows}\n")


def parse_cube(text: str) -> np.ndarray:
    n, vals = 0, []
    for ln in text.splitlines():
        t = ln.strip()
        if t.startswith("LUT_3D_SIZE"):
            n = int(t.split()[1])
        elif t and (t[0].isdigit() or t[0] in "-."):
            vals.append([float(v) for v in t.split()[:3]])
    return np.asarray(vals, np.float64).reshape(n, n, n, 3)


def strip_image(lut: np.ndarray, size: int = STRIP) -> Image.Image:
    """size² x size strip, x = blue slice * size + red, y = green growing downwards (UE / Godot Texture3D slices).
    Unity's Color Lookup wants green growing upwards: the Unity export flips it."""
    t = np.linspace(0, 1, size)
    b, g, r = np.meshgrid(t, t, t, indexing="ij")
    vals = apply_lut(lut, np.stack([r, g, b], -1))
    strip = vals.transpose(1, 0, 2, 3).reshape(size, size * size, 3)
    return Image.fromarray(np.clip(np.round(strip * 255), 0, 255).astype(np.uint8), "RGB")


def strip_to_lut(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGB"), np.float64) / 255.0
    n = a.shape[0]
    return a.reshape(n, n, n, 3).transpose(1, 0, 2, 3)


def _lab(rgb: np.ndarray) -> np.ndarray:
    c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = c @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def delta_e(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> dict[str, float]:
    """CIE76 ΔE between two sRGB arrays in 0..1 (≈2.3 is a just-noticeable difference)."""
    de = np.sqrt(((_lab(a.astype(np.float64)) - _lab(b.astype(np.float64))) ** 2).sum(-1))
    if mask is not None:
        de = de[mask]
    if de.size == 0:
        return {"mean": 0.0, "p95": 0.0}
    return {"mean": round(float(de.mean()), 2), "p95": round(float(np.percentile(de, 95)), 2)}


def _small(img: Image.Image, side: int) -> Image.Image:
    s = min(1.0, side / max(img.size))
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.Resampling.BOX)


def _rgba(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"), np.float32) / 255.0


def is_opaque(img: Image.Image) -> bool:
    return img.mode not in ("RGBA", "LA", "PA") or img.getchannel("A").getextrema()[0] >= 250


def seam_score(img: Image.Image) -> float:
    """Wrap-edge colour step over the interior's sharp-line level: ≤ 1 tiles without a visible seam."""
    return round(max(_seam_ratio(np.asarray(img.convert("RGB"), np.float32) / 255.0, 99).values()), 3)


def detect_seamless(img: Image.Image) -> bool:
    if not is_opaque(img) or min(img.size) < 64:
        return False
    return seam_score(img) <= SEAM_MAX


def fit_pair(orig: Image.Image, edit: Image.Image, smooth: float) -> tuple[np.ndarray, Image.Image]:
    """LUT from the two images (downsampled: a pixel or two of Klein drift averages out) and the edit at the
    original's size."""
    edit = edit.convert("RGB").resize(orig.size, Image.Resampling.LANCZOS)
    so, se = _small(orig.convert("RGBA"), FIT_SIDE), _small(edit, FIT_SIDE)
    a, b = _rgba(so), _rgba(se.resize(so.size))
    mask = a[..., 3] > 0.9
    if mask.sum() < 16:
        raise StepError("op_bad_image", "too few opaque pixels to fit a LUT")
    return fit_lut(a[..., :3][mask], b[..., :3][mask], smooth=smooth), edit


def lut_applied(orig: Image.Image, lut: np.ndarray) -> Image.Image:
    a = _rgba(orig)
    rgb = apply_lut(lut, a[..., :3])
    return Image.fromarray(np.clip(np.round(np.concatenate([rgb, a[..., 3:]], -1) * 255), 0, 255).astype(np.uint8),
                           "RGBA")


def with_alpha(img: Image.Image, alpha_src: Image.Image) -> Image.Image:
    out = img.convert("RGBA")
    out.putalpha(alpha_src.convert("RGBA").getchannel("A"))
    return out


def preview_grid(orig: Image.Image, names: list[str], edits: list[Image.Image], applied: list[Image.Image],
                 scores: list[dict[str, float]], cell: int = 224) -> Image.Image:
    """Top row: original then each variant; bottom row: the LUT applied to the original under its variant."""
    gap, label = 8, 30
    ch = max(1, round(cell * orig.height / orig.width))
    cols = 1 + len(names)
    out = Image.new("RGB", (gap + cols * (cell + gap), gap + 2 * (ch + label + gap)), (24, 24, 28))
    d = ImageDraw.Draw(out)

    def put(img: Image.Image, col: int, row: int, text: str, sub: str = "") -> None:
        x, y = gap + col * (cell + gap), gap + row * (ch + label + gap)
        tile = Image.new("RGBA", (cell, ch), (46, 46, 52, 255))
        tile.alpha_composite(img.convert("RGBA").resize((cell, ch), Image.Resampling.LANCZOS))
        out.paste(tile.convert("RGB"), (x, y))
        d.text((x + 2, y + ch + 3), text, fill=(230, 230, 230))
        if sub:
            d.text((x + 2, y + ch + 16), sub, fill=(170, 170, 170))

    put(orig, 0, 0, "original")
    d.text((gap + 2, gap + ch + label + gap + ch // 2), "LUT applied ->", fill=(150, 150, 150))
    for i, (name, e, a, s) in enumerate(zip(names, edits, applied, scores)):
        put(e, i + 1, 0, name, "Klein")
        put(a, i + 1, 1, f"{name} LUT", f"dE mean {s['mean']:.1f} p95 {s['p95']:.1f}")
    return out


def _stem(ref: BlobRef) -> str:
    return (ref.name or "image").rsplit(".", 1)[0]


class LutPrep(OpExecutor):
    kind = "op.lut_prep"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        ref = _images_in(inputs)[0]
        src = await _load(ctx, ref)
        if is_opaque(src):
            return StepResult(outputs={"images": [ref]}, meta={"opaque": True, "size": list(src.size)})
        # Klein reads only RGB: transparent pixels keep whatever colour they had, often black, which it would grade
        # as scenery. A flat mid grey is neutral; fit_lut ignores it and the variants get the source alpha back.
        flat = Image.new("RGBA", src.size, (*REF_BG, 255))
        flat.alpha_composite(src.convert("RGBA"))
        out = await _put_png(ctx, flat.convert("RGB"), f"{_stem(ref)}_ref.png")
        return StepResult(outputs={"images": [out]}, meta={"opaque": False, "size": list(src.size)})


class FitLut(OpExecutor):
    kind = "op.fit_lut"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        ref = _images_in(inputs)[0]
        edits = _images_in(inputs, "edits")
        names = variant_names(params.get("variants"))
        if len(names) != len(edits):
            names = [f"variant_{i + 1:02d}" for i in range(len(edits))]
        seam_mode = str(params.get("seamless") or "auto").lower()
        final_mode = str(params.get("final") or "edit").lower()
        try:
            smooth = float(params.get("smooth", 0.3))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", f"bad smooth: {e}") from e
        if seam_mode not in SEAMLESS_MODES or final_mode not in FINAL_MODES or not 0 < smooth <= 100:
            raise StepError("op_bad_params", f"seamless must be {'/'.join(SEAMLESS_MODES)}, final "
                                             f"{'/'.join(FINAL_MODES)}, smooth in (0, 100]")
        orig = await _load(ctx, ref)
        stem = _stem(ref)
        opaque = is_opaque(orig)
        seamless = (await asyncio.to_thread(detect_seamless, orig) if seam_mode == "auto"
                    else seam_mode == "on" and opaque)
        if seamless:
            try:
                _overlap(min(orig.size), 0.08)
            except ValueError:
                seamless = False
        orig_seam = await asyncio.to_thread(seam_score, orig) if opaque else None
        mask_small = _rgba(_small(orig.convert("RGBA"), DE_SIDE))[..., 3] > 0.5
        outs: dict[str, list[BlobRef]] = {"images": [], "applied": [], "cube": [], "strip": []}
        entries: list[dict[str, Any]] = []
        shown_edits, shown_applied, scores = [], [], []
        for i, (name, eref) in enumerate(zip(names, edits)):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.progress(i, len(edits), label=name)
            lut, edit = await asyncio.to_thread(fit_pair, orig, await _load(ctx, eref), smooth)
            applied = await asyncio.to_thread(lut_applied, orig, lut)
            score = await asyncio.to_thread(
                delta_e, _rgba(_small(applied, DE_SIDE))[..., :3],
                _rgba(_small(edit, DE_SIDE).resize(mask_small.shape[::-1]))[..., :3], mask_small)
            seam: dict[str, Any] = {}
            final = applied if final_mode == "lut" else edit
            if seamless and final_mode == "edit":
                seam["before"] = await asyncio.to_thread(seam_score, edit)
                # The LUT-applied original tiles exactly and already carries the variant's grade, so borrowing its
                # border lines (match_edges' tone match is then ~identity) restores the wrap without undoing the look.
                final = await asyncio.to_thread(match_edges, edit, applied.convert("RGB"), 0.08, 3, 0.0)
                seam["after"] = await asyncio.to_thread(seam_score, final)
            elif seamless:
                seam["after"] = await asyncio.to_thread(seam_score, applied)
            final = with_alpha(final, orig)
            r_img = await _put_png(ctx, final, f"{stem}_{name}.png")
            r_app = await _put_png(ctx, applied, f"{stem}_{name}_lut_applied.png")
            r_cube = await ctx.blobs.put_bytes(cube_text(lut, f"{stem}_{name}").encode(), name=f"{stem}_{name}.cube",
                                               mime="text/plain")
            r_strip = await _put_png(ctx, await asyncio.to_thread(strip_image, lut), f"{stem}_{name}_lut.png")
            for k, r in (("images", r_img), ("applied", r_app), ("cube", r_cube), ("strip", r_strip)):
                outs[k].append(r)
            entries.append({"name": name, "file": r_img.name, "hash": r_img.hash, "source": final_mode,
                            "applied": {"file": r_app.name, "hash": r_app.hash},
                            "cube": {"file": r_cube.name, "hash": r_cube.hash},
                            "strip": {"file": r_strip.name, "hash": r_strip.hash},
                            "delta_e": score, **({"seam": seam} if seam else {})})
            shown_edits.append(final)
            shown_applied.append(applied)
            scores.append(score)
        grid = await asyncio.to_thread(preview_grid, orig, names[:len(edits)], shown_edits, shown_applied, scores)
        prev = await _put_png(ctx, grid, f"{stem}_lut_preview.png")
        doc = {"type": LUT_TYPE, "source": stem, "size": list(orig.size), "grid": GRID, "strip": STRIP,
               "strip_layout": "x = b * 32 + r, y = g top-down (UE / Godot)", "seamless": seamless,
               "original_seam": orig_seam, "opaque": opaque, "variants": entries}
        lj = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=LUT_FILE, mime="application/json")
        bad = [e["name"] for e in entries if e["delta_e"]["mean"] > 8]
        if bad:
            ctx.log(f"LUT misses part of the edit (mean dE > 8, content changed?): {', '.join(bad)}", "warning")
        ctx.progress(len(edits), len(edits))
        return StepResult(outputs={**outs, "preview": [prev], "lut": [lj]},
                          meta={"variants": [e["name"] for e in entries], "seamless": seamless,
                                "delta_e": {e["name"]: e["delta_e"]["mean"] for e in entries},
                                **({"seam": {e["name"]: e.get("seam") for e in entries}} if seamless else {})})


LUT_OPS: tuple[type[OpExecutor], ...] = (LutPrep, FitLut)
