"""Style measurements of one image against a set of anchor images (CPU, numpy): histogram palette ΔE2000, contrast,
saturation, outline weight and optional CLIP style distance, plus the median + k·MAD outlier rule and the colour
transfer target a LUT is fitted to."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

SIDE = 256
SAMPLES = 20000
PAL_ASSET = 12
PAL_ANCHOR = 16
METRICS = ("palette", "contrast", "saturation", "outline", "clip")
WEIGHTS = {"palette": 0.35, "contrast": 0.15, "saturation": 0.15, "outline": 0.2, "clip": 0.15}
COLOR_METRICS = ("palette", "contrast", "saturation")
STYLE_METRICS = ("outline", "clip")
# A set with almost no spread would turn noise into huge z-scores; the floor is the smallest spread worth reacting to.
SPREAD_FLOOR = {"palette": 2.0, "contrast": 0.03, "saturation": 0.04, "outline": 0.25, "clip": 0.03}
# Below this distance to the anchors an asset is never flagged, however tight the rest of the set is.
ABS_MIN = {"palette": 6.0, "contrast": 0.08, "saturation": 0.10, "outline": 0.6, "clip": 0.12}
MAD_SCALE = 1.4826
MIN_PEERS = 3

_M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
               [0.0193339, 0.1191920, 0.9503041]])
_M_INV = np.linalg.inv(_M)
_WHITE = np.array([0.95047, 1.0, 1.08883])


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB 0..1 (..., 3) -> CIELAB D65."""
    c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = (c @ _M.T) / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], -1)
    xyz = np.where(f ** 3 > 0.008856, f ** 3, (f - 16 / 116) / 7.787) * _WHITE
    c = np.clip(xyz @ _M_INV.T, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def ciede2000(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """CIEDE2000 between Lab arrays that broadcast against each other."""
    L1, a1, b1 = a[..., 0], a[..., 1], a[..., 2]
    L2, a2, b2 = b[..., 0], b[..., 1], b[..., 2]
    cm = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(cm ** 7 / (cm ** 7 + 25.0 ** 7)))
    ap1, ap2 = (1 + g) * a1, (1 + g) * a2
    cp1, cp2 = np.hypot(ap1, b1), np.hypot(ap2, b2)
    hp1 = np.degrees(np.arctan2(b1, ap1)) % 360
    hp2 = np.degrees(np.arctan2(b2, ap2)) % 360
    dh = hp2 - hp1
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    zero = cp1 * cp2 == 0
    dh = np.where(zero, 0.0, dh)
    d_big_h = 2 * np.sqrt(cp1 * cp2) * np.sin(np.radians(dh / 2))
    lm, cpm = (L1 + L2) / 2, (cp1 + cp2) / 2
    hs = hp1 + hp2
    hm = np.where(np.abs(hp1 - hp2) <= 180, hs / 2, np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2))
    hm = np.where(zero, hs, hm)
    t = (1 - 0.17 * np.cos(np.radians(hm - 30)) + 0.24 * np.cos(np.radians(2 * hm))
         + 0.32 * np.cos(np.radians(3 * hm + 6)) - 0.20 * np.cos(np.radians(4 * hm - 63)))
    rc = 2 * np.sqrt(cpm ** 7 / (cpm ** 7 + 25.0 ** 7))
    rt = -np.sin(np.radians(2 * 30 * np.exp(-(((hm - 275) / 25) ** 2)))) * rc
    sl = 1 + 0.015 * (lm - 50) ** 2 / np.sqrt(20 + (lm - 50) ** 2)
    x, y = (cp2 - cp1) / (1 + 0.045 * cpm), d_big_h / (1 + 0.015 * cpm * t)
    return np.sqrt(((L2 - L1) / sl) ** 2 + x ** 2 + y ** 2 + rt * x * y)


def histogram_palette(rgb: np.ndarray, top: int) -> tuple[np.ndarray, np.ndarray]:
    """Dominant colours of uint8 (N, 3) pixels from a 16-level-per-channel histogram: (colours 0..1, weights)."""
    q = (rgb >> 4).astype(np.int64)
    uniq, inv, cnt = np.unique(q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2], return_inverse=True, return_counts=True)
    mean = np.stack([np.bincount(inv, rgb[:, k], len(uniq)) for k in range(3)], 1) / cnt[:, None] / 255.0
    keep = np.argsort(-cnt, kind="stable")[:top]
    return mean[keep], cnt[keep] / cnt[keep].sum()


def palette_distance(colors: np.ndarray, weights: np.ndarray, ref_lab: np.ndarray) -> float:
    """Weighted mean ΔE2000 of each palette colour to its nearest reference colour."""
    de = ciede2000(rgb_to_lab(colors)[:, None, :], ref_lab[None, :, :]).min(1)
    return float((de * weights).sum())


@dataclass
class AssetStats:
    rgb: np.ndarray
    colors: np.ndarray
    weights: np.ndarray
    lab_mean: np.ndarray
    lab_std: np.ndarray
    contrast: float
    saturation: float
    outline: float | None


def _thumb(img: Image.Image) -> np.ndarray:
    im = img.convert("RGBA")
    if max(im.size) > SIDE:
        im.thumbnail((SIDE, SIDE), Image.Resampling.BOX)
    return np.asarray(im)


def _subject(arr: np.ndarray) -> np.ndarray:
    alpha = arr[..., 3]
    if alpha.min() < 250:
        return alpha >= 128
    rgb = arr[..., :3].astype(np.int32)
    bg = np.median(np.stack([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]]), axis=0)
    mask = np.abs(rgb - bg).sum(-1) > 40
    return mask if 0.01 < mask.mean() < 0.92 else np.ones_like(mask)


def _outline(img: Image.Image) -> float | None:
    from ..engine.project_qa import measure

    m = measure(img)
    if not m.ok or m.outlined is None:
        return None
    return float(m.outline or 0.0)


def measure_asset(img: Image.Image) -> AssetStats | None:
    """None when the image has too few subject pixels (fully transparent or blank)."""
    arr = _thumb(img)
    mask = _subject(arr)
    if int(mask.sum()) < 16:
        return None
    px = arr[..., :3][mask]
    px = px[:: max(1, len(px) // SAMPLES)]
    rgb = px.astype(np.float64) / 255.0
    lab = rgb_to_lab(rgb)
    colors, weights = histogram_palette(px, PAL_ASSET)
    lo, hi = np.percentile(lab[:, 0], [5, 95])
    mx = rgb.max(1)
    sat = np.where(mx > 0, (mx - rgb.min(1)) / np.maximum(mx, 1e-9), 0.0)
    return AssetStats(rgb, colors, weights, lab.mean(0), lab.std(0), float((hi - lo) / 100.0), float(sat.mean()),
                      _outline(img))


def anchor_profile(stats: list[AssetStats]) -> dict[str, Any]:
    """Pooled reference of the anchors: palette (Lab), Lab mean / std and the median of the scalar metrics."""
    per = max(1, SAMPLES // len(stats))
    pooled = np.concatenate([s.rgb[:: max(1, len(s.rgb) // per)] for s in stats])
    colors, weights = histogram_palette(np.round(pooled * 255).astype(np.uint8), PAL_ANCHOR)
    lab = rgb_to_lab(pooled)
    outlines = [s.outline for s in stats if s.outline is not None]
    return {"colors": colors, "weights": weights, "palette_lab": rgb_to_lab(colors), "lab_mean": lab.mean(0),
            "lab_std": lab.std(0), "contrast": float(np.median([s.contrast for s in stats])),
            "saturation": float(np.median([s.saturation for s in stats])),
            "outline": float(np.median(outlines)) if outlines else None}


def distances(s: AssetStats, ref: dict[str, Any]) -> dict[str, float]:
    out = {"palette": palette_distance(s.colors, s.weights, ref["palette_lab"]),
           "contrast": abs(s.contrast - ref["contrast"]), "saturation": abs(s.saturation - ref["saturation"])}
    if s.outline is not None and ref["outline"] is not None:
        out["outline"] = abs(float(np.log2((s.outline + 0.5) / (ref["outline"] + 0.5))))
    return out


def clip_distances(embedder: Any, anchors: list[Image.Image], images: list[Image.Image]) -> list[float]:
    """1 - cosine to the mean anchor embedding (CLIP is trained on style words too, so it sees brush / render look)."""
    ref = embedder.embed_images(anchors).mean(0)
    ref = ref / max(float(np.linalg.norm(ref)), 1e-9)
    return [float(1.0 - v @ ref) for v in embedder.embed_images(images)]


Limits = dict[str, dict[str, float]]


def rank(rows: list[dict[str, float]], k: float = 3.0) -> tuple[list[dict[str, Any]], Limits]:
    """Per metric: median + k·MAD of the set's distances to the anchors is the limit, the z-score the distance's
    excess over the median in spread units. `score` is the weighted mean of positive z over the metrics the asset has;
    an asset is flagged on a metric when it is past the limit and past that metric's absolute minimum."""
    stats: Limits = {}
    for m in METRICS:
        vals = np.array([r[m] for r in rows if m in r], dtype=np.float64)
        if len(vals) < MIN_PEERS:
            continue
        med = float(np.median(vals))
        spread = max(MAD_SCALE * float(np.median(np.abs(vals - med))), SPREAD_FLOOR[m])
        stats[m] = {"median": round(med, 4), "mad": round(spread, 4), "limit": round(med + k * spread, 4),
                    "floor": ABS_MIN[m], "n": len(vals)}
    out = []
    for r in rows:
        z, flags = {}, []
        for m, st in stats.items():
            if m not in r:
                continue
            z[m] = round((r[m] - st["median"]) / st["mad"], 3)
            if r[m] > st["limit"] and r[m] > ABS_MIN[m]:
                flags.append(m)
        used = {m: max(v, 0.0) for m, v in z.items()}
        score = sum(WEIGHTS[m] * v for m, v in used.items()) / sum(WEIGHTS[m] for m in used) if used else 0.0
        out.append({"z": z, "flags": flags, "score": round(score, 3)})
    return out, stats


def transfer_target(rgb: np.ndarray, ref: dict[str, Any], strength: float) -> np.ndarray:
    """Where each colour should land: Lab mean / std matched to the anchors, then pulled `strength` of the way to the
    nearest anchor palette colour. The LUT is fitted to these pairs, so it moves colours smoothly, not per pixel."""
    lab = rgb_to_lab(rgb)
    scale = np.clip(ref["lab_std"] / np.maximum(lab.std(0), 1e-3), 0.5, 2.0)
    out = (lab - lab.mean(0)) * scale + ref["lab_mean"]
    pal = ref["palette_lab"]
    near = pal[np.argmin(((out[:, None, :] - pal[None, :, :]) ** 2).sum(-1), axis=1)]
    return lab_to_rgb(out + strength * (near - out))


def outline_hint(outline: float | None, ref: float | None) -> str:
    if outline is None or ref is None:
        return ""
    if outline > ref * 1.4 + 0.5:
        return "make the outline thinner"
    if outline < ref * 0.7 - 0.5:
        return "make the outline thicker"
    return ""
