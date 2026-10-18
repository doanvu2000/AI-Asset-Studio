"""Folder exports of a whole step: every output under its real file name, sprite sheets repacked for an engine preset."""
from __future__ import annotations

import io
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobRef, BlobStore, StepRow
from studio_core.ops import image as im
from studio_core.ops.presets import ExportPreset, annotate_sheet_meta, apply_preset
from studio_core.store.export import safe_component, safe_filename


class BundleError(ValueError):
    pass


def sheet_meta(step: StepRow, blobs: BlobStore) -> dict[str, Any] | None:
    """The op.sheet JSON when this step can be repacked (8 directional frames with known angles)."""
    refs = step.outputs.get("meta") or []
    if step.kind != "op.sheet" or not refs:
        return None
    try:
        meta = json.loads(blobs.path(refs[0].hash).read_bytes())
    except (OSError, ValueError):
        return None
    frames, order = meta.get("frames"), meta.get("order")
    if not isinstance(frames, list) or not isinstance(order, list) or len(frames) != 8 or len(order) != 8:
        return None
    return meta


def _png(img: Any) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def _sprite_files(meta: dict[str, Any], blobs: BlobStore, preset: ExportPreset, asset: str
                  ) -> list[tuple[str, bytes | Path]]:
    angles = [int(a) for a in meta["order"]]
    try:
        slots, named = apply_preset(preset, angles, asset)
    except ValueError as e:
        raise BundleError(str(e)) from None
    hashes = [meta["frames"][i] for i in slots]
    imgs = [im.open_image(blobs.path(h)) for h in hashes]
    pivot = meta.get("pivot")
    sheet, out = im.pack_sheet(imgs, preset.cols_for(len(imgs)), (int(meta["frame_w"]), int(meta["frame_h"])),
                               preset.padding, (int(pivot[0]), int(pivot[1])) if pivot else None,
                               [angles[i] for i in slots])
    out["frames"] = hashes
    annotate_sheet_meta(out, named, preset)
    files: list[tuple[str, bytes | Path]] = [
        (f"{asset}_sheet.png", _png(sheet)),
        (f"{asset}_sheet.json", json.dumps(out, indent=2, ensure_ascii=False).encode()),
    ]
    for rec, h in zip(named, hashes):
        name = safe_filename(f"{rec['name']}.png", BlobRef(hash=h, mime="image/png", size=0))
        files.append((f"frames/{name}", blobs.path(h)))
    return files


def _raw_files(step: StepRow, blobs: BlobStore) -> list[tuple[str, bytes | Path]]:
    # Spine's .atlas names its page PNG, so outputs keep the blob names the executor gave them.
    files: list[tuple[str, bytes | Path]] = []
    seen: set[str] = set()
    hashes: set[str] = set()
    for refs in step.outputs.values():
        for ref in refs:
            if ref.hash in hashes:
                continue
            hashes.add(ref.hash)
            name = safe_filename(None, ref)
            stem, ext = os.path.splitext(name)
            n = 2
            while name.lower() in seen:
                name, n = f"{stem}_{n}{ext}", n + 1
            seen.add(name.lower())
            files.append((name, blobs.path(ref.hash)))
    return files


def _write(dst: Path, files: list[tuple[str, bytes | Path]]) -> list[Path]:
    tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.tmp")
    written: list[Path] = []
    try:
        for rel, src in files:
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(src, Path):
                shutil.copyfile(src, p)
            else:
                p.write_bytes(src)
            written.append(dst / rel)
        tmp.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            # Windows cannot replace a non-empty folder; move the old one aside first.
            old = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.old")
            os.replace(dst, old)
            shutil.rmtree(old, ignore_errors=True)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return written


def export_bundle(step: StepRow, blobs: BlobStore, root: Path, name: str | None,
                  preset: ExportPreset | None) -> dict[str, Any]:
    if not step.outputs:
        raise BundleError("step has no outputs")
    meta = sheet_meta(step, blobs)
    if preset is not None and meta is None:
        raise BundleError("export presets only apply to 8-direction sprite sheets")
    asset_param = str(step.params.get("asset") or "")
    folder = safe_component(name or asset_param or step.key, step.key)
    if meta is not None and preset is not None:
        asset = safe_component(name or asset_param, "sprite")
        files = _sprite_files(meta, blobs, preset, asset)
    else:
        files = _raw_files(step, blobs)
    dst = root / "bundles" / f"{step.id[:12]}-{preset.id if preset else 'raw'}" / folder
    dst.parent.mkdir(parents=True, exist_ok=True)
    written = _write(dst, files)
    return {"dir": str(dst.resolve()), "name": folder, "preset": preset.id if preset else None,
            "files": [str(p.resolve()) for p in written]}
