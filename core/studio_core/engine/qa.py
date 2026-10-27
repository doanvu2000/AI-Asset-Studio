"""Deterministic 0–100 quality score for produced images / sprite sheets, with a per-criterion breakdown."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, BlobStore, StepRow
from ..ops.fx import FxParams, fx_colors

log = logging.getLogger(__name__)

QA_VERSION = 1
SOLID, VISIBLE = 128, 16
LABELS = {"alpha": "Độ sạch alpha", "framing": "Khung hình", "empty": "Khung rỗng",
          "consistency": "Nhất quán các hướng", "palette": "Bảng màu"}
WEIGHTS = {"alpha": 25, "framing": 25, "empty": 20, "consistency": 20, "palette": 10}
_N4 = ((-1, 0), (1, 0), (0, -1), (0, 1))


@dataclass
class QaContext:
    meta: dict[str, Any] | None = None
    palette: Sequence[tuple[int, int, int]] | None = None
    extra_colors: Sequence[tuple[int, int, int]] = ()
    shadow: bool = False
    shadow_color: tuple[int, int, int] | None = None
    normal: bool = False


@dataclass
class _Cell:
    index: int
    direction: int | None
    alpha: np.ndarray
    rgb: np.ndarray
    expected: bool = True
    stats: dict[str, Any] = field(default_factory=dict)


def _shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    out[max(dy, 0):h + min(dy, 0), max(dx, 0):w + min(dx, 0)] = a[max(-dy, 0):h - max(dy, 0), max(-dx, 0):w - max(dx, 0)]
    return out


def _grow(mask: np.ndarray, n: int) -> np.ndarray:
    out = mask
    for _ in range(n):
        out = out | np.logical_or.reduce([_shift(out, dy, dx) for dy, dx in ((-1, -1), (-1, 1), (1, -1), (1, 1), *_N4)])
    return out


def _components(mask: np.ndarray, cap: int = 4096) -> np.ndarray:
    """4-connected labels by min-propagation (no scipy); label 0 is background."""
    h, w = mask.shape
    big = h * w + 1
    lab = np.where(mask, np.arange(1, h * w + 1).reshape(h, w), big)
    for _ in range(cap):
        nb = np.minimum.reduce([lab, *(np.where(_shift(mask, dy, dx), _shift(lab, dy, dx), big) for dy, dx in _N4)])
        nb = np.where(mask, nb, big)
        if np.array_equal(nb, lab):
            break
        # Pointer jumping: follow each label to its own label, which collapses long chains in few passes.
        flat = nb.ravel()
        inner = flat < big
        flat[inner] = np.minimum(flat[inner], flat[np.clip(flat[inner] - 1, 0, h * w - 1)])
        lab = flat.reshape(h, w)
    return np.where(mask, lab, 0)


def _cells(arr: np.ndarray, meta: dict[str, Any] | None) -> list[_Cell]:
    h, w = arr.shape[:2]
    if not meta or not meta.get("frame_w") or not meta.get("cols"):
        return [_Cell(0, None, arr[..., 3], arr[..., :3])]
    fw, fh = int(meta["frame_w"]), int(meta["frame_h"])
    cols, pad = int(meta["cols"]), int(meta.get("padding") or 0)
    rows = int(meta.get("rows") or 1)
    count = int(meta.get("count") or cols * rows)
    frames = meta.get("frames") if isinstance(meta.get("frames"), list) else None
    clips = meta.get("clips") if isinstance(meta.get("clips"), list) else None
    row_angles = meta.get("row_angles")
    by_row = rows > 1 and isinstance(row_angles, list) and len(row_angles) == rows
    out: list[_Cell] = []
    for i in range(min(count, cols * rows)):
        r, c = divmod(i, cols)
        x, y = c * (fw + pad), r * (fh + pad)
        if x + fw > w or y + fh > h:
            break
        direction: int | None
        if clips:
            clip = next((k for k in clips if k["row0"] <= r < k["row0"] + k["rows"]), None)
            direction = None if clip is None else r - int(clip["row0"])
            expected = clip is not None and c < int(clip.get("frames") or cols)
        else:
            # Only `row_angles` says rows are directions (walk sheet); a preset grid (4x2 for 8 dirs) is one per cell.
            direction = r if by_row else i
            expected = True
        if frames is not None and i < len(frames) and frames[i] is None:
            expected = False
        out.append(_Cell(i, direction, arr[y:y + fh, x:x + fw, 3], arr[y:y + fh, x:x + fw, :3], expected))
    return out or [_Cell(0, None, arr[..., 3], arr[..., :3])]


def _small(mask: np.ndarray, limit: int = 96) -> np.ndarray:
    f = max(1, -(-max(mask.shape) // limit))
    if f == 1:
        return mask
    h, w = mask.shape[0] // f * f, mask.shape[1] // f * f
    return mask[:h, :w].reshape(h // f, f, w // f, f).any(axis=(1, 3))


def _cell_stats(cell: _Cell, ctx: QaContext, pivot: tuple[float, float] | None) -> dict[str, Any]:
    a = cell.alpha
    h, w = a.shape
    vis = a >= VISIBLE
    cov = float(vis.mean())
    s: dict[str, Any] = {"coverage": cov, "empty": cov < 0.002}
    if s["empty"]:
        return s
    ys, xs = np.nonzero(vis)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    s.update(bbox=(x0, y0, x1, y1), height=(y1 - y0) / h, area=float((a >= SOLID).mean()),
             bottom=y1 / h, centroid_x=float(xs.mean()) / w,
             clipped=int(x0 == 0) + int(y0 == 0) + int(x1 == w) + int(y1 == h),
             margin=min(x0, y0, w - x1, h - y1) / max(h, w))
    solid = a >= SOLID
    if ctx.shadow_color is not None:
        # Dithered pixel shadows are scattered on purpose; they must not read as stray pixels.
        solid &= ~(cell.rgb == np.array(ctx.shadow_color, np.uint8)).all(-1)
    small = _small(solid)
    lab = _components(small)
    ids, sizes = np.unique(lab[lab > 0], return_counts=True)
    stray = 0.0
    if len(sizes) > 1:
        main = sizes.max()
        frag = sizes[sizes < main * 0.05]
        stray = float(frag.sum()) / float(sizes.sum())
    s["fragments"] = int(len(sizes) - 1) if len(sizes) else 0
    s["stray"] = stray
    opaque = a >= 200
    semi = (a > 8) & (a < 200)
    if ctx.shadow:
        s["haze"] = None
    elif ctx.palette is not None:
        s["haze"] = float(semi.sum()) / max(1, int(opaque.sum()))
    else:
        s["haze"] = float((semi & ~_grow(opaque, 2)).sum()) / max(1, int(opaque.sum()))
    if pivot is not None:
        s["pivot_dx"] = (float(xs.mean()) - pivot[0]) / w
    return s


def _clamp(v: float) -> float:
    return float(max(0.0, min(100.0, v)))


def _part(key: str, score: float, detail: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": LABELS[key], "score": round(_clamp(score)), "weight": WEIGHTS[key],
            "detail": detail, **extra}


def score_image(img: Image.Image, ctx: QaContext | None = None) -> dict[str, Any]:
    ctx = ctx or QaContext()
    arr = np.array(img.convert("RGBA"))
    if int(arr[..., 3].min()) == 255:
        return {"version": QA_VERSION, "score": None, "skipped": "opaque",
                "note": "Ảnh không có nền trong suốt — không chấm"}
    meta = ctx.meta or {}
    cells = _cells(arr, meta)
    pivot = None
    if isinstance(meta.get("pivot"), list) and len(meta["pivot"]) == 2:
        pivot = (float(meta["pivot"][0]), float(meta["pivot"][1]))
    expected = [c for c in cells if c.expected]
    for c in expected:
        c.stats = _cell_stats(c, ctx, pivot)
    live = [c for c in expected if not c.stats["empty"]]
    parts: list[dict[str, Any]] = []
    issues: list[str] = []

    n_empty = len(expected) - len(live)
    frac = n_empty / max(1, len(expected))
    parts.append(_part("empty", 100 if n_empty == 0 else 60 - 100 * frac,
                       f"{n_empty}/{len(expected)} khung rỗng" if n_empty else "Không có khung rỗng",
                       count=n_empty))
    if n_empty:
        issues.append(f"{n_empty} khung gần như trống")
    if not live:
        return {"version": QA_VERSION, "score": 0, "parts": parts, "issues": issues, "cells": len(expected)}

    stray = float(np.mean([c.stats["stray"] for c in live]))
    frags = sum(c.stats["fragments"] for c in live)
    hazes = [c.stats["haze"] for c in live if c.stats["haze"] is not None]
    haze = float(np.mean(hazes)) if hazes else 0.0
    s_stray = 100 - 2500 * stray
    s_halo = 100 - (500 if ctx.palette is not None else 800) * haze
    alpha_detail = f"Pixel lẻ {stray * 100:.1f}%"
    alpha_detail += f" · viền mờ {haze * 100:.1f}%" if hazes else " · bỏ qua viền mờ (có bóng đổ)"
    parts.append(_part("alpha", min(s_stray, s_halo) * 0.6 + (s_stray + s_halo) / 2 * 0.4, alpha_detail,
                       stray=round(stray, 4), halo=round(haze, 4), fragments=frags))
    if stray > 0.01:
        issues.append("Có pixel lẻ / mảnh rời quanh sprite")
    if haze > 0.04:
        issues.append("Viền bán trong suốt (halo) dày" if ctx.palette is None else "Pixel art có alpha không cứng")

    clipped = [c for c in live if c.stats["clipped"]]
    tiny = [c for c in live if c.stats["height"] < 0.2]
    cell_scores = [100 - 35 * c.stats["clipped"] - (30 if c.stats["height"] < 0.2 else 0)
                   - (10 if 0 < c.stats["margin"] < 0.01 else 0) for c in live]
    parts.append(_part("framing", min(float(np.mean(cell_scores)), min(cell_scores) + 30),
                       f"{len(clipped)} khung chạm mép · {len(tiny)} khung quá nhỏ" if clipped or tiny
                       else "Không chạm mép, lề ổn", clipped=len(clipped), tiny=len(tiny)))
    if clipped:
        issues.append(f"{len(clipped)} khung bị cắt ở mép ô")
    if tiny:
        issues.append(f"{len(tiny)} khung có nhân vật quá nhỏ")

    dirs: dict[int, list[_Cell]] = {}
    for c in live:
        if c.direction is not None:
            dirs.setdefault(c.direction, []).append(c)
    if len(dirs) >= 2:
        def per_dir(key: str) -> np.ndarray:
            return np.array([np.median([c.stats[key] for c in cs]) for cs in dirs.values()], float)
        hgt, area, bottom = per_dir("height"), per_dir("area"), per_dir("bottom")
        drift_key = "pivot_dx" if pivot is not None else "centroid_x"
        drift = per_dir(drift_key)
        h_cv = float(hgt.std() / max(1e-6, hgt.mean()))
        a_cv = float(area.std() / max(1e-6, area.mean()))
        f_sd, d_sd = float(bottom.std()), float(drift.std())
        subs = [100 - 400 * h_cv, 100 - 200 * max(0.0, a_cv - 0.25), 100 - 800 * f_sd, 100 - 600 * d_sd]
        parts.append(_part("consistency", float(np.mean([_clamp(x) for x in subs])),
                           f"{len(dirs)} hướng · lệch cao {h_cv * 100:.0f}% · lệch chân {f_sd * 100:.1f}% · "
                           f"lệch tâm {d_sd * 100:.1f}%", directions=len(dirs), height_cv=round(h_cv, 4),
                           area_cv=round(a_cv, 4), foot_drift=round(f_sd, 4), center_drift=round(d_sd, 4)))
        if h_cv > 0.12:
            issues.append("Chiều cao nhân vật lệch nhiều giữa các hướng")
        if f_sd > 0.03:
            issues.append("Vị trí chân lệch giữa các hướng")
        if d_sd > 0.05:
            issues.append("Tâm nhân vật trôi so với pivot giữa các hướng")

    if ctx.palette is not None and not ctx.normal:
        allowed = {tuple(int(v) for v in c) for c in (*ctx.palette, *ctx.extra_colors)}
        opaque = arr[..., 3] == 255
        px = arr[..., :3][opaque].astype(np.int32)
        codes, counts = np.unique((px[:, 0] << 16) | (px[:, 1] << 8) | px[:, 2], return_counts=True)
        ok = np.array([((int(k) >> 16) & 255, (int(k) >> 8) & 255, int(k) & 255) in allowed for k in codes], bool)
        inside = float(counts[ok].sum()) / max(1, int(counts.sum()))
        off = int((~ok).sum())
        parts.append(_part("palette", 100 - 500 * (1 - inside),
                           f"{inside * 100:.1f}% pixel đúng bảng màu · {off} màu ngoài bảng",
                           inside=round(inside, 4), off_colors=off, colors=int(len(codes))))
        if inside < 0.98:
            issues.append(f"{off} màu nằm ngoài bảng màu")

    total_w = sum(p["weight"] for p in parts)
    score = sum(p["score"] * p["weight"] for p in parts) / total_w
    if n_empty:
        score = min(score, 70)
    if clipped:
        score = min(score, 85)
    return {"version": QA_VERSION, "score": int(round(_clamp(score))), "parts": parts, "issues": issues,
            "cells": len(expected), "normal": ctx.normal or None, "pixel": ctx.palette is not None or None}


async def _json_blob(blobs: BlobStore, ref: BlobRef) -> Any:
    try:
        return json.loads(await asyncio.to_thread(blobs.path(ref.hash).read_bytes))
    except (OSError, ValueError):
        return None


def _hex(c: Any) -> tuple[int, int, int] | None:
    try:
        h = str(c).strip().lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return None


def lineage(steps: Sequence[StepRow], step: StepRow) -> list[StepRow]:
    """`step` and every step it (transitively) consumed, so a sibling branch's fx or palette is not applied."""
    by_key = {s.key: s for s in steps}
    seen: dict[str, StepRow] = {step.key: step}
    todo = list(step.deps)
    while todo:
        k = todo.pop()
        if k in seen or k not in by_key:
            continue
        seen[k] = by_key[k]
        todo.extend(by_key[k].deps)
    return list(seen.values())


async def build_context(blobs: BlobStore, steps: Iterable[StepRow], step: StepRow, ref: BlobRef) -> QaContext:
    steps = list(steps)
    ctx = QaContext()
    if step.kind == "op.sheet" and any(b.hash == ref.hash for b in step.outputs.get("sheet", [])):
        metas = step.outputs.get("meta") or []
        meta = await _json_blob(blobs, metas[0]) if metas else None
        ctx.meta = meta if isinstance(meta, dict) else None
    name = (ref.name or "").lower()
    ctx.normal = bool((ctx.meta or {}).get("normal")) or name.endswith("_n.png") or "_normal" in name
    for s in lineage(steps, step):
        if s.kind == "op.sprite_fx":
            try:
                fx = FxParams.from_params(s.params)
            except (ValueError, TypeError):
                continue
            if fx.shadow != "none" and not fx.normal:
                ctx.shadow = True
                ctx.shadow_color = fx.shadow_color if fx.pixel else None
            ctx.extra_colors = (*ctx.extra_colors, *(c for c in (_hex(x) for x in fx_colors(fx)) if c))
        if s.kind == "op.pixel_art" and not ctx.normal and (pals := s.outputs.get("palette")):
            info = await _json_blob(blobs, pals[0])
            if isinstance(info, dict):
                cols = [c for c in (_hex(x) for x in [*(info.get("colors") or []), *(info.get("outline") or [])]) if c]
                if cols:
                    ctx.palette = cols
    return ctx


async def version_qa(blobs: BlobStore, steps: Iterable[StepRow], step: StepRow, ref: BlobRef) -> dict[str, Any] | None:
    """None for non-images; never raises. A failure is stored as `skipped: "error"` so it is not retried forever."""
    if not (ref.mime or "").startswith("image/"):
        return None
    try:
        ctx = await build_context(blobs, steps, step, ref)

        def run() -> dict[str, Any]:
            with Image.open(blobs.path(ref.hash)) as img:
                return score_image(img, ctx)
        return await asyncio.to_thread(run)
    except Exception as e:
        log.warning("qa failed for %s: %s", ref.hash, e)
        return {"version": QA_VERSION, "score": None, "skipped": "error", "note": "Không chấm được (lỗi đọc ảnh)"}
