"""Cross-asset consistency of a project's images (CPU heuristics): palette drift, framing scale, outline weight and
light direction, each scored against the rest of the set, plus a one-click fix as a Klein edit run."""
from __future__ import annotations

import io
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, ProjectStyle, VersionRow, VersionStatus
from ..ops.pixel import PALETTES, from_oklab, kmeans_palette, to_oklab

REPORT_VERSION = 1
MAX_SIDE = 384
MAX_ASSETS = 120
PER_RUN = 8
SHEET_ASPECT = 2.2
MIN_PEERS = 3
FIX_RECIPE = "variants-2d"
WEIGHTS = {"palette": 35, "scale": 20, "outline": 20, "light": 25}
LABELS = {"palette": "Bảng màu", "scale": "Tỉ lệ", "outline": "Nét viền", "light": "Hướng sáng"}
_DIRS = ["phải", "trên-phải", "trên", "trên-trái", "trái", "dưới-trái", "dưới", "dưới-phải"]
_DIRS_EN = ["right", "top right", "top", "top left", "left", "bottom left", "bottom", "bottom right"]


@dataclass
class Measure:
    version_id: str
    ok: bool = True
    note: str = ""
    full_frame: bool = False
    fill_h: float | None = None
    fill_w: float | None = None
    colors: list[str] = field(default_factory=list)
    samples: np.ndarray | None = None
    outlined: bool | None = None
    outline: float | None = None
    light: tuple[float, float] | None = None


def _hex(rgb: Sequence[int]) -> str:
    return "#" + "".join(f"{int(c):02x}" for c in rgb[:3])


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _sheet_like(b: BlobRef) -> bool:
    if "sheet" in (b.name or "").lower():
        return True
    if b.width and b.height:
        r = b.width / b.height
        return r >= SHEET_ASPECT or r <= 1 / SHEET_ASPECT
    return False


def pick_versions(versions: Sequence[VersionRow], only_approved: bool = False,
                  limit: int = MAX_ASSETS) -> list[VersionRow]:
    """One entry per run: its approved images, else (unless only_approved) its non-rejected ones; approved runs
    first, newest first. Sprite sheets/strips are left out, their cells are the run's other outputs."""
    by_run: dict[str, list[VersionRow]] = {}
    seen: set[str] = set()
    for v in sorted(versions, key=lambda v: v.created_at, reverse=True):
        if (not (v.blob.mime or "").startswith("image/") or v.status == VersionStatus.REJECTED
                or _sheet_like(v.blob) or v.blob.hash in seen):
            continue
        seen.add(v.blob.hash)
        by_run.setdefault(v.run_id, []).append(v)
    picked: list[tuple[bool, float, list[VersionRow]]] = []
    for vs in by_run.values():
        approved = [v for v in vs if v.status == VersionStatus.APPROVED]
        if only_approved and not approved:
            continue
        pool = (approved or vs)[:PER_RUN]
        picked.append((bool(approved), pool[0].created_at, pool))
    picked.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [v for _, _, pool in picked for v in pool][:limit]


def _load(img: Image.Image) -> np.ndarray:
    im = img.convert("RGBA")
    if max(im.size) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return np.asarray(im)


def _erode(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, 1, constant_values=False)
    return m & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]


def _subject(arr: np.ndarray) -> tuple[np.ndarray, bool]:
    a = arr[..., 3]
    if a.min() < 250:
        return a >= 128, False
    rgb = arr[..., :3]
    corners = np.stack([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]])
    bg = to_oklab(np.median(corners, axis=0)[None])[0]
    dist = np.linalg.norm(to_oklab(rgb.reshape(-1, 3)) - bg, axis=1).reshape(a.shape)
    mask = dist > 0.08
    if not 0.01 < mask.mean() < 0.92:
        return np.ones_like(mask), True
    return mask, False


def _stride(n: int, cap: int) -> slice:
    return slice(None, None, max(1, n // cap))


def measure(img: Image.Image, version_id: str = "") -> Measure:
    arr = _load(img)
    m = Measure(version_id)
    mask, full = _subject(arr)
    m.full_frame = full
    if mask.sum() < 16:
        return Measure(version_id, ok=False, note="Ảnh gần như trống")
    H, W = mask.shape
    ys, xs = np.nonzero(mask)
    bh, bw = int(ys.max() - ys.min() + 1), int(xs.max() - xs.min() + 1)
    rgb = arr[..., :3][mask]
    lab = to_oklab(rgb[_stride(len(rgb), 6000)])
    m.samples = lab
    pal = kmeans_palette(rgb[_stride(len(rgb), 20000)], 6)
    m.colors = [_hex(c) for c in pal]

    L = to_oklab(arr[..., :3].reshape(-1, 3))[:, 0].reshape(H, W)
    if not full:
        m.fill_h, m.fill_w = bh / H, bw / W
        core = mask
        for _ in range(3):
            nxt = _erode(core)
            if nxt.sum() < 16:
                break
            core = nxt
        interior = float(np.median(L[core]))
        dark = L < min(0.42, interior * 0.7)
        fracs: list[float] = []
        cur = mask
        for _ in range(12):
            er = _erode(cur)
            ring = cur & ~er
            if not ring.any():
                break
            fracs.append(float(dark[ring].mean()))
            cur = er
        thick = 0.0
        for f in fracs:
            if f < 0.4:
                break
            thick += min(1.0, f)
        m.outlined = bool(fracs) and fracs[0] >= 0.55
        m.outline = round(thick * 256 / max(bh, 1), 2) if m.outlined else 0.0
        inner = mask
        for _ in range(int(thick) + 1):
            nxt = _erode(inner)
            if nxt.sum() < 16:
                break
            inner = nxt
    else:
        inner = mask
    iy, ix = np.nonzero(inner)
    if len(iy) >= 16:
        lum = L[iy, ix]
        w = lum - lum.mean()
        dx = (ix - ix.mean()) / max((ix.max() - ix.min()) / 2, 1)
        dy = (iy - iy.mean()) / max((iy.max() - iy.min()) / 2, 1)
        denom = float(np.abs(w).sum()) or 1.0
        vx, vy = float((w * dx).sum() / denom), float((w * dy).sum() / denom)
        strength = math.hypot(vx, vy)
        if strength >= 0.06:
            m.light = (math.degrees(math.atan2(-vy, vx)) % 360, round(strength, 3))
    return m


def _dir(angle: float, en: bool = False) -> str:
    return (_DIRS_EN if en else _DIRS)[int(((angle + 22.5) % 360) // 45)]


def _adiff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _clamp(v: float) -> int:
    return int(round(max(0.0, min(100.0, v))))


def palette_from_image(img: Image.Image, k: int) -> list[str]:
    arr = _load(img)
    mask, _ = _subject(arr)
    px = arr[..., :3][mask] if mask.sum() >= 16 else arr[..., :3].reshape(-1, 3)
    return [_hex(c) for c in kmeans_palette(px[_stride(len(px), 40000)], k)]


def reference_palette(style: ProjectStyle | None, load: Callable[[str], Image.Image | None],
                      measures: Sequence[Measure]) -> tuple[list[str], str]:
    """(hex colors, where they come from): pixel palette > palette image > reference image > the set itself."""
    if style is not None:
        name = (style.pixel.palette if style.pixel else None) or ""
        if name in PALETTES:
            return ["#" + c for c in PALETTES[name]], f"Bảng màu pixel {name}"
        for h, k, label in ((style.palette_image, 16, "Ảnh bảng màu của dự án"),
                            (style.reference_image, 12, "Ảnh tham chiếu của dự án")):
            img = load(h) if h else None
            if img is not None:
                return palette_from_image(img, k), label
    pooled = [m.samples for m in measures if m.ok and m.samples is not None and len(m.samples)]
    if not pooled:
        return [], ""
    lab = np.concatenate([p[_stride(len(p), 1500)] for p in pooled])
    return [_hex(c) for c in kmeans_palette(from_oklab(lab), 16)], "Màu chung của các asset trong dự án"


def _nearest_dist(lab: np.ndarray, ref: np.ndarray) -> np.ndarray:
    return np.sqrt(((lab[:, None, :] - ref[None, :, :]) ** 2).sum(-1)).min(1)


def _issue(key: str, severity: str, message: str, fix: str = "") -> dict[str, Any]:
    return {"key": key, "label": LABELS[key], "severity": severity, "message": message, "fix": fix}


def evaluate(versions: Sequence[VersionRow], measures: Sequence[Measure], palette: Sequence[str],
             palette_source: str = "") -> dict[str, Any]:
    ok = [m for m in measures if m.ok]
    ref = to_oklab(np.array([_rgb(h) for h in palette])) if palette else None
    drift = {m.version_id: float(_nearest_dist(m.samples, ref).mean())
             for m in ok if ref is not None and m.samples is not None and len(m.samples)}
    base_drift = max(float(np.median(list(drift.values()))), 0.03) if len(drift) >= MIN_PEERS else 0.05
    fills = [m.fill_h for m in ok if m.fill_h is not None]
    med_fill = float(np.median(fills)) if len(fills) >= MIN_PEERS else None
    framed = [m for m in ok if m.outlined is not None]
    majority_outlined = sum(bool(m.outlined) for m in framed) * 2 >= len(framed) if len(framed) >= MIN_PEERS else None
    thick = [m.outline for m in framed if m.outlined and m.outline]
    med_thick = float(np.median(thick)) if len(thick) >= MIN_PEERS else None
    lit = [m.light for m in ok if m.light is not None]
    mean_light = None
    if len(lit) >= MIN_PEERS:
        sx = sum(s * math.cos(math.radians(a)) for a, s in lit)
        sy = sum(s * math.sin(math.radians(a)) for a, s in lit)
        if math.hypot(sx, sy) > 0.25 * sum(s for _, s in lit):
            mean_light = math.degrees(math.atan2(sy, sx)) % 360

    by_id = {v.id: v for v in versions}
    assets: list[dict[str, Any]] = []
    for m in measures:
        v = by_id[m.version_id]
        entry: dict[str, Any] = {"version_id": v.id, "run_id": v.run_id, "asset_name": v.asset_name,
                                 "status": v.status.value if isinstance(v.status, VersionStatus) else v.status,
                                 "blob": v.blob.model_dump(mode="json"), "colors": m.colors}
        if not m.ok:
            assets.append({**entry, "score": None, "grade": "skip", "parts": {}, "issues": [], "note": m.note})
            continue
        parts: dict[str, dict[str, Any]] = {}
        issues: list[dict[str, Any]] = []
        if m.version_id in drift:
            d = drift[m.version_id]
            ratio = d / base_drift
            parts["palette"] = {"score": _clamp(100 - max(0.0, ratio - 1.15) * 70), "drift": round(d, 4),
                                "ratio": round(ratio, 2)}
            if ratio >= 1.6 and d > 0.045:
                far = [c for c in m.colors
                       if ref is not None and _nearest_dist(to_oklab(np.array([_rgb(c)])), ref)[0] > 0.08]
                msg = f"Màu lệch bảng màu chung (gấp {ratio:.1f}× mức chung)"
                if far:
                    msg += ": màu lạ " + ", ".join(far[:4])
                issues.append(_issue("palette", "bad" if ratio >= 2.2 else "warn", msg,
                                     "recolor it using only this color palette: " + ", ".join(palette[:12])
                                     + ", keeping the same shading and details"))
        if med_fill and m.fill_h is not None:
            ratio = m.fill_h / med_fill
            dev = abs(math.log2(ratio))
            parts["scale"] = {"score": _clamp(100 - dev * 110), "fill": round(m.fill_h, 3), "median": round(med_fill, 3)}
            if dev > 0.26:
                word = "lớn" if ratio > 1 else "nhỏ"
                issues.append(_issue("scale", "bad" if dev > 0.5 else "warn",
                                     f"Chủ thể {word} hơn mức chung {abs(ratio - 1) * 100:.0f}% (cao {m.fill_h * 100:.0f}% "
                                     f"khung, trung vị {med_fill * 100:.0f}%)",
                                     "keep the whole subject fully visible and centered"))
        if majority_outlined is not None and m.outlined is not None:
            if m.outlined != majority_outlined:
                parts["outline"] = {"score": 40, "outlined": m.outlined, "thickness": m.outline}
                if majority_outlined:
                    issues.append(_issue("outline", "warn", "Thiếu viền tối trong khi các asset khác có viền",
                                         "add a clean dark outline around the whole subject"))
                else:
                    issues.append(_issue("outline", "warn", "Có viền tối trong khi các asset khác không viền",
                                         "remove the dark outline around the subject"))
            elif m.outlined and med_thick and m.outline:
                ratio = m.outline / med_thick
                dev = abs(math.log2(ratio))
                parts["outline"] = {"score": _clamp(100 - dev * 90), "outlined": True, "thickness": m.outline,
                                    "median": round(med_thick, 2)}
                if dev > 0.58:
                    word = "dày" if ratio > 1 else "mảnh"
                    issues.append(_issue("outline", "bad" if dev > 1 else "warn",
                                         f"Nét viền {word} hơn mức chung ({m.outline:.1f} so với {med_thick:.1f} "
                                         "px trên 256 px chiều cao)",
                                         f"make the outline {'thinner' if ratio > 1 else 'thicker'}, a uniform dark "
                                         "line weight"))
            else:
                parts["outline"] = {"score": 100, "outlined": m.outlined, "thickness": m.outline}
        if mean_light is not None and m.light is not None:
            diff = _adiff(m.light[0], mean_light)
            parts["light"] = {"score": _clamp(100 - max(0.0, diff - 30) * 0.8), "angle": round(m.light[0]),
                              "median": round(mean_light), "strength": m.light[1]}
            if diff > 70:
                issues.append(_issue("light", "bad" if diff > 120 else "warn",
                                     f"Sáng từ phía {_dir(m.light[0])} trong khi các asset khác sáng từ "
                                     f"phía {_dir(mean_light)} (lệch {diff:.0f}°)",
                                     f"relight it so the main light comes from the {_dir(mean_light, True)} and "
                                     f"shadows fall to the {_dir(mean_light + 180, True)}"))
        total = sum(WEIGHTS[k] for k in parts)
        score = _clamp(sum(WEIGHTS[k] * p["score"] for k, p in parts.items()) / total) if total else None
        grade = "skip" if score is None else "ok" if score >= 80 and not issues else "warn" if score >= 60 else "bad"
        assets.append({**entry, "score": score, "grade": grade, "parts": parts, "issues": issues,
                       "note": "" if parts else "Chưa đủ asset để so (cần ít nhất 3)",
                       "fix": fix_inputs(v, issues)})
    assets.sort(key=lambda a: (a["score"] is None, a["score"] if a["score"] is not None else 0))
    scored = [a["score"] for a in assets if a["score"] is not None]
    return {
        "version": REPORT_VERSION, "created_at": time.time(), "assets": assets,
        "summary": {"total": len(assets), "scored": len(scored),
                    "score": _clamp(sum(scored) / len(scored)) if scored else None,
                    "flagged": sum(1 for a in assets if a.get("issues")),
                    "counts": {g: sum(1 for a in assets if a["grade"] == g) for g in ("ok", "warn", "bad", "skip")}},
        "reference": {"palette": list(palette), "palette_source": palette_source,
                      "fill_median": round(med_fill, 3) if med_fill else None,
                      "outlined": majority_outlined, "outline_median": round(med_thick, 2) if med_thick else None,
                      "light": round(mean_light) if mean_light is not None else None,
                      "light_label": _dir(mean_light) if mean_light is not None else None},
    }


def fix_inputs(v: VersionRow, issues: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Inputs for FIX_RECIPE (Klein edit, then background removal and re-framing, which also fixes framing)."""
    lines = [i["fix"] for i in issues if i.get("fix")]
    if not lines:
        return None
    side = max(v.blob.width or 512, v.blob.height or 512)
    size = min((128, 256, 512, 1024), key=lambda s: abs(s - side))
    return {"recipe_id": FIX_RECIPE, "title": f"Sửa QA · {v.asset_name}",
            "inputs": {"image": v.blob.hash, "instructions": "; ".join(dict.fromkeys(lines)), "size": size}}


def contact_sheet(images: Sequence[Image.Image], cell: int = 192, cols: int = 6) -> bytes:
    """Numbered grid (1-based, in the given order) so one vision call sees the whole set side by side."""
    rows = max(1, math.ceil(len(images) / cols))
    sheet = Image.new("RGB", (cols * cell, rows * cell), (236, 236, 236))
    draw = ImageDraw.Draw(sheet)
    for i, src in enumerate(images):
        im = src.convert("RGBA")
        im.thumbnail((cell - 12, cell - 24), Image.Resampling.LANCZOS)
        x, y = (i % cols) * cell, (i // cols) * cell
        sheet.paste(im, (x + (cell - im.width) // 2, y + 20 + (cell - 24 - im.height) // 2), im)
        draw.rectangle((x, y, x + cell - 1, y + cell - 1), outline=(190, 190, 190))
        draw.text((x + 6, y + 4), str(i + 1), fill=(20, 20, 20))
    buf = io.BytesIO()
    sheet.save(buf, "PNG", optimize=True)
    return buf.getvalue()
