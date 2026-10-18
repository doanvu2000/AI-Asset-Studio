"""Sanity check of generated turnaround views against the front image (pure; executors call it via to_thread).

Qwen multi-angle edits sometimes return the front pose rotated by 90° (character lying down) instead of a side
view. On a white or transparent background the silhouette alone gives that away: the bbox turns wide, its height
drops versus the front, and the shape matches the front rotated better than upright.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from PIL import Image

from .image import to_rgba

# Qwen backgrounds are off-white (~240–250) with JPEG-ish noise; 235 keeps them out and line art in.
BG_CUTOFF = 235
SHAPE_N = 64


@dataclass(frozen=True)
class ViewCheckConfig:
    max_aspect: float = 1.35
    height_tol: float = 0.15
    min_area: float = 0.02
    max_area: float = 0.85
    rot_margin: float = 0.08
    edge_px: int = 2

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> ViewCheckConfig:
        fields = cls.__dataclass_fields__
        return cls(**{k: type(getattr(cls, k))(params[k]) for k in fields if params.get(k) is not None})


@dataclass(frozen=True)
class Silhouette:
    width: int
    height: int
    bbox: tuple[int, int, int, int] | None
    area: float
    shape: np.ndarray | None

    @property
    def aspect(self) -> float:
        l, t, r, b = self.bbox or (0, 0, 1, 1)
        return (r - l) / max(b - t, 1)

    @property
    def box_height(self) -> int:
        l, t, r, b = self.bbox or (0, 0, 0, 0)
        return b - t


def foreground(img: Image.Image) -> np.ndarray:
    arr = np.asarray(to_rgba(img))
    alpha = arr[..., 3]
    if alpha.min() < 250:
        return alpha > 127
    return (arr[..., :3] < BG_CUTOFF).any(axis=2)


def silhouette(img: Image.Image) -> Silhouette:
    mask = foreground(img)
    h, w = mask.shape
    # A few stray pixels (signature specks, JPEG ringing) must not stretch the bbox.
    min_count = max(2, round(min(w, h) * 0.003))
    rows = np.nonzero(mask.sum(axis=1) >= min_count)[0]
    cols = np.nonzero(mask.sum(axis=0) >= min_count)[0]
    if not len(rows) or not len(cols):
        return Silhouette(w, h, None, 0.0, None)
    bbox = (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)
    crop = Image.fromarray(mask[bbox[1]:bbox[3], bbox[0]:bbox[2]].astype(np.uint8) * 255)
    shape = np.asarray(crop.resize((SHAPE_N, SHAPE_N), Image.Resampling.BILINEAR)) > 127
    return Silhouette(w, h, bbox, float(mask.mean()), shape)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / max((a | b).sum(), 1))


def check_view(front: Silhouette, view: Silhouette, cfg: ViewCheckConfig = ViewCheckConfig()) -> dict[str, Any]:
    """-> {ok, reasons, scores}; scores are relative to the front so any character proportions work."""
    if view.bbox is None or view.shape is None:
        return {"ok": False, "reasons": ["empty"], "scores": {"area": 0.0}}
    if front.bbox is None or front.shape is None:
        raise ValueError("front image has no foreground")
    aspect = view.aspect / front.aspect
    height = view.box_height / view.height / (front.box_height / front.height)
    upright = max(_iou(view.shape, front.shape), _iou(view.shape, front.shape[:, ::-1]))
    rotated = max(_iou(view.shape, np.rot90(front.shape, 1)), _iou(view.shape, np.rot90(front.shape, -1)))
    l, t, r, b = view.bbox
    e = cfg.edge_px
    clipped = l < e or t < e or r > view.width - e or b > view.height - e
    reasons = []
    if aspect > cfg.max_aspect:
        reasons.append("wide")
    if abs(height - 1.0) > cfg.height_tol:
        reasons.append("height")
    if not cfg.min_area <= view.area <= cfg.max_area:
        reasons.append("area")
    if rotated - upright > cfg.rot_margin:
        reasons.append("rotated")
    if clipped:
        reasons.append("clipped")
    scores = {"aspect": round(aspect, 3), "height": round(height, 3), "area": round(view.area, 4),
              "upright_iou": round(upright, 3), "rotated_iou": round(rotated, 3)}
    return {"ok": not reasons, "reasons": reasons, "scores": scores}


def check_views(front: Image.Image, views: list[Image.Image],
                cfg: ViewCheckConfig = ViewCheckConfig()) -> list[dict[str, Any]]:
    f = silhouette(front)
    return [check_view(f, silhouette(v), cfg) for v in views]


def config_dict(cfg: ViewCheckConfig) -> dict[str, Any]:
    return asdict(cfg)
