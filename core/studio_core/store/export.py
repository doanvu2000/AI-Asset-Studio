"""Readable-name copies of content-addressed blobs, for "Export…", "Show in folder" and drag-out from the app."""
from __future__ import annotations

import mimetypes
import os
import re
import shutil
import uuid
from pathlib import Path

from studio_core.contracts import BlobRef

_MIME_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
    "model/gltf-binary": ".glb", "model/gltf+json": ".gltf", "application/json": ".json",
}
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_NAME = 120
_EXT = re.compile(r"\.[A-Za-z0-9]{1,8}")


def safe_filename(name: str | None, blob: BlobRef) -> str:
    """A single path component with an extension matching the blob; never a path, never a reserved device name."""
    raw = (name or blob.name or "").replace("\\", "/").rsplit("/", 1)[-1]
    clean = _BAD_CHARS.sub("_", raw).strip(" .")
    stem, ext = os.path.splitext(clean)
    if not _EXT.fullmatch(ext):
        ext = _MIME_EXT.get(blob.mime) or Path(blob.name or "").suffix or mimetypes.guess_extension(blob.mime) or ".bin"
        stem = clean
    stem = stem.strip(" .") or blob.hash[:12]
    if stem.upper().split(".")[0] in _WIN_RESERVED:
        stem = f"_{stem}"
    return stem[: MAX_NAME - len(ext)].rstrip(" .") + ext.lower()


def safe_component(name: str | None, fallback: str) -> str:
    """A folder name: same rules as safe_filename but no extension handling."""
    raw = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    clean = _BAD_CHARS.sub("_", raw).strip(" .")[:MAX_NAME].rstrip(" .") or fallback
    return f"_{clean}" if clean.upper().split(".")[0] in _WIN_RESERVED else clean


def export_copy(src: Path, blob: BlobRef, root: Path, name: str | None) -> Path:
    """Copies into root/<hash prefix>/<name>; the per-hash folder keeps equal names of different blobs apart."""
    dst = root / blob.hash[:16] / safe_filename(name, blob)
    if dst.is_file() and dst.stat().st_size == blob.size:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(src, tmp)
        try:
            os.replace(tmp, dst)
        except PermissionError:
            # Explorer or the target app may hold the previous copy open; the bytes are identical anyway.
            if not dst.is_file():
                raise
    finally:
        tmp.unlink(missing_ok=True)
    return dst
