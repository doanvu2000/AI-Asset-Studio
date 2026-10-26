"""Engine-neutral view of an op.sheet meta: named cells (top-left pixel rects) grouped into clip × direction tracks."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any

from studio_core.ops.presets import COMPASS_ANGLE

ANGLE_COMPASS = {a: d for d, a in COMPASS_ANGLE.items()}
_IDENT = re.compile(r"\W+")


def ident(name: str, fallback: str) -> str:
    """Engine-safe identifier: letters, digits, underscore; never starts with a digit."""
    s = _IDENT.sub("_", name).strip("_") or fallback
    return f"_{s}" if s[0].isdigit() else s


def compass(angle: float) -> str:
    a = round(float(angle)) % 360
    return ANGLE_COMPASS.get(a, f"A{a:03d}")


def direction_vector(angle: float) -> tuple[float, float]:
    """Screen-space facing for the studio angle convention (0 = facing the viewer/down, 90 = left), y up."""
    r = math.radians(float(angle))
    return round(-math.sin(r), 4) + 0.0, round(-math.cos(r), 4) + 0.0


def stable_hex(*parts: str) -> str:
    return hashlib.blake2b("\x1f".join(parts).encode(), digest_size=16).hexdigest()


@dataclass(frozen=True)
class Cell:
    name: str
    x: int
    y: int
    w: int
    h: int


@dataclass
class Track:
    clip: str
    dir: str
    angle: float
    cells: list[Cell]
    fps: float
    loop: bool

    @property
    def name(self) -> str:
        return f"{self.clip}_{self.dir}"


@dataclass
class Layout:
    width: int
    height: int
    frame_w: int
    frame_h: int
    pivot: tuple[float, float]
    cells: list[Cell] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)

    @property
    def pivot_norm(self) -> tuple[float, float]:
        """Pivot as a fraction of the cell, origin bottom-left (Unity's convention)."""
        return round(self.pivot[0] / self.frame_w, 4), round(1 - self.pivot[1] / self.frame_h, 4)

    def clips(self) -> list[str]:
        return list(dict.fromkeys(t.clip for t in self.tracks))


def _directional(angles: list[Any], compass_only: bool = True) -> bool:
    try:
        vals = [round(float(a)) % 360 for a in angles]
    except (TypeError, ValueError):
        return False
    return bool(vals) and len(set(vals)) == len(vals) and (not compass_only or all(v in ANGLE_COMPASS for v in vals))


def layout(meta: dict[str, Any], clip_hint: str = "anim", fps: float = 8.0, directional_rows: bool = False) -> Layout:
    fw, fh = int(meta["frame_w"]), int(meta["frame_h"])
    cols, rows = int(meta.get("cols") or 1), int(meta.get("rows") or 1)
    pad = int(meta.get("padding") or 0)
    count = int(meta.get("count") or cols * rows)
    width = int(meta.get("width") or cols * fw + (cols - 1) * pad)
    height = int(meta.get("height") or rows * fh + (rows - 1) * pad)
    pv = meta.get("pivot")
    pivot = (float(pv[0]), float(pv[1])) if isinstance(pv, list) and len(pv) == 2 else (fw / 2, float(fh))
    out = Layout(width, height, fw, fh, pivot)

    def cell(name: str, r: int, c: int) -> Cell:
        return Cell(name, c * (fw + pad), r * (fh + pad), fw, fh)

    def add_track(clip: str, angle: float, cells: list[Cell], t_fps: float, loop: bool) -> None:
        out.cells.extend(cells)
        out.tracks.append(Track(clip, compass(angle), angle, cells, t_fps, loop))

    clip_hint = ident(clip_hint, "anim")
    row_angles = meta.get("row_angles")
    clips = meta.get("clips")
    order = meta.get("order")
    named = meta.get("frames_named")

    if isinstance(clips, list) and clips:
        for i, c in enumerate(clips):
            name = ident(str(c.get("name") or f"clip{i}"), f"clip{i}")
            row0, n_rows = int(c.get("row0", 0)), int(c.get("rows", 1))
            frames = max(1, min(int(c.get("frames") or cols), cols))
            for k in range(n_rows):
                row = row0 + k
                angle = (row_angles[row] if isinstance(row_angles, list) and row < len(row_angles)
                         else k * 360 / n_rows)
                d = compass(angle)
                add_track(name, angle, [cell(f"{name}_{d}_{f}", row, f) for f in range(frames)],
                          float(c.get("fps") or fps), bool(c.get("loop", True)))
    elif isinstance(named, list) and named and all("x" in n and "dir" in n for n in named):
        for n in named:
            d = str(n["dir"])
            c = Cell(f"idle_{d}_0", int(n["x"]), int(n["y"]), int(n.get("w", fw)), int(n.get("h", fh)))
            add_track("idle", COMPASS_ANGLE.get(d, 0), [c], fps, True)
    elif (per_cell := next((v for v in (meta.get("angles"), order) if isinstance(v, list) and len(v) == count
                            and count > 1 and _directional(v)), None)) is not None:
        # One still frame per direction (static 8-dir sheet, any grid shape).
        for i, angle in enumerate(per_cell):
            r, c = divmod(i, cols)
            add_track("idle", float(angle), [cell(f"idle_{compass(angle)}_0", r, c)], fps, True)
    elif isinstance(row_angles, list) and len(row_angles) == rows and _directional(row_angles, compass_only=False):
        for row, angle in enumerate(row_angles):
            d = compass(angle)
            add_track(clip_hint, angle, [cell(f"{clip_hint}_{d}_{f}", row, f) for f in range(cols)], fps, True)
    elif ((directional_rows or meta.get("frames_per_dir")) and rows in (4, 8, 16) and cols > 1
          and count == rows * cols):
        # Legacy animate_dirs sheets carry no row_angles: rows are evenly spaced directions starting at 0°.
        # Plain grids (VFX flipbooks) share the shape, so only trust it when lineage says so.
        for row in range(rows):
            angle = row * 360 / rows
            d = compass(angle)
            add_track(clip_hint, angle, [cell(f"{clip_hint}_{d}_{f}", row, f) for f in range(cols)], fps, True)
    else:
        for i in range(count):
            r, c = divmod(i, cols)
            out.cells.append(cell(f"frame_{i}", r, c))
    return out
