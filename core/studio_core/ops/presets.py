"""Export presets for sprite sheets: direction order, frame naming, sheet layout and pivot convention per engine."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Angle = how far the model is turned from facing the camera, in the blender.render_dirs convention
# (pivot z rotation = -angle, camera on -Y): 0 faces the viewer (screen-down), 90 faces screen-left.
COMPASS_ANGLE: dict[str, int] = {"S": 0, "SW": 45, "W": 90, "NW": 135, "N": 180, "NE": 225, "E": 270, "SE": 315}
COMPASS = tuple(COMPASS_ANGLE)
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_FIELDS = re.compile(r"\{(\w+)(?::[^}]*)?\}")
PATTERN_FIELDS = {"asset", "dir", "angle", "index"}


class ExportPreset(BaseModel):
    id: str
    name: str
    description: str = ""
    dir_order: list[str] = Field(default_factory=lambda: list(COMPASS))
    dir_labels: dict[str, str] = {}
    file_pattern: str = "{asset}_{dir}"
    layout: Literal["row", "column", "grid"] = "row"
    cols: int = Field(4, ge=1, le=64)
    padding: int = Field(0, ge=0, le=64)
    pivot_origin: Literal["top_left", "bottom_left"] = "top_left"
    # Pixel-art sheets: exporters must import with point/nearest filtering, no mipmaps or compression.
    pixel: bool = False
    builtin: bool = False

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("id: lowercase letters, digits, '-' or '_' (max 48)")
        return v

    @field_validator("dir_order")
    @classmethod
    def _order(cls, v: list[str]) -> list[str]:
        v = [d.upper() for d in v]
        if sorted(v) != sorted(COMPASS):
            raise ValueError(f"dir_order must list each of {', '.join(COMPASS)} exactly once")
        return v

    @field_validator("dir_labels")
    @classmethod
    def _labels(cls, v: dict[str, str]) -> dict[str, str]:
        out = {k.upper(): str(s).strip() for k, s in v.items() if str(s).strip()}
        bad = sorted(set(out) - set(COMPASS))
        if bad:
            raise ValueError(f"dir_labels: unknown directions {bad}")
        return out

    @model_validator(mode="after")
    def _pattern(self) -> ExportPreset:
        used = {m.group(1) for m in _FIELDS.finditer(self.file_pattern)}
        if used - PATTERN_FIELDS:
            raise ValueError(f"file_pattern: unknown fields {sorted(used - PATTERN_FIELDS)}; "
                             f"use {sorted(PATTERN_FIELDS)}")
        if not used & {"dir", "angle", "index"}:
            raise ValueError("file_pattern needs {dir}, {angle} or {index} so frame names differ")
        return self

    def label(self, compass: str) -> str:
        return self.dir_labels.get(compass, compass)

    def frame_name(self, asset: str, compass: str, index: int) -> str:
        try:
            return self.file_pattern.format(asset=asset, dir=self.label(compass), angle=COMPASS_ANGLE[compass],
                                            index=index)
        except (KeyError, ValueError, IndexError) as e:
            raise ValueError(f"file_pattern {self.file_pattern!r}: {e}") from None

    def cols_for(self, count: int) -> int:
        return {"row": count, "column": 1}.get(self.layout, min(self.cols, count))


# Unity's Mathf.Atan2 runs counter-clockwise from +X (east) and Godot's Vector2.angle() clockwise from +X
# (y points down), so an angle→index lookup in either engine expects this order.
BUILTIN: tuple[ExportPreset, ...] = (
    ExportPreset(id="studio", name="Studio (mặc định)", description="Theo góc xoay 0°→315°, một hàng, tên theo góc",
                 dir_order=list(COMPASS), file_pattern="{asset}_{angle:03d}", builtin=True),
    ExportPreset(id="unity", name="Unity", description="E, NE, N… ngược chiều kim đồng hồ (Atan2); pivot gốc dưới-trái",
                 dir_order=["E", "NE", "N", "NW", "W", "SW", "S", "SE"], file_pattern="{asset}_{dir}",
                 pivot_origin="bottom_left", builtin=True),
    ExportPreset(id="godot", name="Godot", description="right, down_right, down… theo chiều kim đồng hồ (Vector2.angle)",
                 dir_order=["E", "SE", "S", "SW", "W", "NW", "N", "NE"],
                 dir_labels={"E": "right", "SE": "down_right", "S": "down", "SW": "down_left", "W": "left",
                             "NW": "up_left", "N": "up", "NE": "up_right"},
                 file_pattern="{asset}_{dir}", builtin=True),
    ExportPreset(id="pixel", name="Pixel art", description="Như Studio, đánh dấu pixel art: engine lọc Point/Nearest",
                 dir_order=list(COMPASS), file_pattern="{asset}_{angle:03d}", pixel=True, builtin=True),
)


class PresetStore:
    """Built-in presets in code; user presets in one JSON file under the data dir."""

    def __init__(self, path: Path | None):
        self.path = path

    def _user(self) -> dict[str, ExportPreset]:
        if self.path is None or not self.path.is_file():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        out: dict[str, ExportPreset] = {}
        for item in raw if isinstance(raw, list) else []:
            try:
                p = ExportPreset.model_validate({**item, "builtin": False})
            except ValueError:
                continue
            out[p.id] = p
        return out

    def list(self) -> list[ExportPreset]:
        user = self._user()
        return [p for p in BUILTIN] + [p for p in user.values() if p.id not in {b.id for b in BUILTIN}]

    def get(self, pid: str) -> ExportPreset | None:
        return next((p for p in self.list() if p.id == pid), None)

    def _write(self, presets: dict[str, ExportPreset]) -> None:
        if self.path is None:
            raise RuntimeError("preset store has no file")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [p.model_dump(exclude={"builtin"}) for p in presets.values()]
        tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def save(self, preset: ExportPreset) -> ExportPreset:
        if any(b.id == preset.id for b in BUILTIN):
            raise ValueError(f"'{preset.id}' is a built-in preset; save a copy under another id")
        user = self._user()
        user[preset.id] = preset.model_copy(update={"builtin": False})
        self._write(user)
        return user[preset.id]

    def delete(self, pid: str) -> None:
        user = self._user()
        if pid not in user:
            raise LookupError(f"no user preset '{pid}'")
        del user[pid]
        self._write(user)


def resolve_preset(value: Any, store: PresetStore | None) -> ExportPreset | None:
    """op.sheet `preset` param: an id, an inline preset object, or empty for the legacy layout."""
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return ExportPreset.model_validate({"id": "inline", "name": "inline", **value})
    p = (store or PresetStore(None)).get(str(value))
    if p is None:
        raise ValueError(f"unknown export preset '{value}'")
    return p


def apply_preset(preset: ExportPreset, angles: list[int], asset: str) -> tuple[list[int], list[dict[str, Any]]]:
    """-> (frame index for each sheet slot, frame records) — reorders angle-ordered frames into preset order."""
    by_angle = {int(a) % 360: i for i, a in enumerate(angles)}
    if len(angles) != len(COMPASS) or len(by_angle) != len(angles):
        raise ValueError(f"export presets need exactly 8 distinct directional frames, got angles {angles}")
    slots: list[int] = []
    frames: list[dict[str, Any]] = []
    for slot, compass in enumerate(preset.dir_order):
        idx = by_angle.get(COMPASS_ANGLE[compass])
        if idx is None:
            raise ValueError(f"no frame for direction {compass} ({COMPASS_ANGLE[compass]}°) in angles {angles}")
        slots.append(idx)
        frames.append({"name": preset.frame_name(asset, compass, slot), "dir": compass, "label": preset.label(compass),
                       "angle": COMPASS_ANGLE[compass], "index": slot})
    return slots, frames


def annotate_sheet_meta(meta: dict[str, Any], named: list[dict[str, Any]], preset: ExportPreset) -> None:
    """Adds per-frame rects, direction labels and an engine-oriented normalized pivot to a pack_sheet meta."""
    fw, fh, c, pad = meta["frame_w"], meta["frame_h"], meta["cols"], meta.get("padding", 0)
    for rec in named:
        r, col = divmod(rec["index"], c)
        rec.update(x=col * (fw + pad), y=r * (fh + pad), w=fw, h=fh)
    meta.update(preset=preset.id, dirs=[n["label"] for n in named], frames_named=named,
                pivot_origin=preset.pivot_origin, pixel=preset.pixel, filter="point" if preset.pixel else "bilinear")
    if "pivot" in meta:
        px, py = meta["pivot"]
        meta["pivot_norm"] = [round(px / fw, 4),
                              round((fh - py) / fh if preset.pivot_origin == "bottom_left" else py / fh, 4)]
