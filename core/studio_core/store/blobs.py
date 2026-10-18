from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import BinaryIO

import blake3

from studio_core.contracts import BlobRef, BlobRow, Repo

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1 << 20
GLB_MIME = "model/gltf-binary"

_EXT_MIME = {".glb": GLB_MIME, ".gltf": "model/gltf+json", ".webp": "image/webp", ".json": "application/json"}


def sniff_mime(head: bytes, name: str | None = None) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"glTF"):
        return GLB_MIME
    if name:
        ext = Path(name).suffix.lower()
        if ext in _EXT_MIME:
            return _EXT_MIME[ext]
        guessed, _ = mimetypes.guess_type(name)
        if guessed:
            return guessed
    stripped = head.lstrip()
    if stripped[:1] in (b"{", b"["):
        return "application/json"
    return "application/octet-stream"


def _image_size(path: Path) -> tuple[int | None, int | None]:
    from PIL import Image

    try:
        with Image.open(path) as im:
            return im.width, im.height
    except Exception:
        return None, None


def _hash_stream(f: BinaryIO) -> tuple[str, int, bytes]:
    h = blake3.blake3()
    size = 0
    head = b""
    while chunk := f.read(_CHUNK):
        if not head:
            head = chunk[:64]
        h.update(chunk)
        size += len(chunk)
    return h.hexdigest(), size, head


class FsBlobStore:
    """Content-addressed store: root/ab/cd/<blake3>, extension-less; mime/dims live in the blobs table."""

    def __init__(self, root: Path, thumbs_dir: Path, repo: Repo) -> None:
        self.root = root
        self.thumbs_dir = thumbs_dir
        self.repo = repo
        self._thumb_locks: dict[str, asyncio.Lock] = {}
        root.mkdir(parents=True, exist_ok=True)
        thumbs_dir.mkdir(parents=True, exist_ok=True)

    def path(self, hash: str) -> Path:
        if not _HASH_RE.match(hash):
            raise ValueError(f"invalid blob hash: {hash!r}")
        return self.root / hash[:2] / hash[2:4] / hash

    def _install(self, src: Path | None, data: bytes | None, hash: str) -> None:
        dst = self.path(hash)
        if dst.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{hash}.{uuid.uuid4().hex}.tmp")
        try:
            if src is not None:
                shutil.copyfile(src, tmp)
            else:
                tmp.write_bytes(data or b"")
            try:
                os.replace(tmp, dst)
            except PermissionError:
                # Windows refuses to replace a file another reader holds open; same hash means same bytes.
                if not dst.exists():
                    raise
        finally:
            tmp.unlink(missing_ok=True)

    def _ingest_file(self, src: Path, name: str | None, mime: str | None) -> BlobRow:
        with src.open("rb") as f:
            hash, size, head = _hash_stream(f)
        self._install(src, None, hash)
        return self._describe(hash, size, head, name or src.name, mime)

    def _ingest_bytes(self, data: bytes, name: str | None, mime: str | None) -> BlobRow:
        hash = blake3.blake3(data).hexdigest()
        self._install(None, data, hash)
        return self._describe(hash, len(data), data[:64], name, mime)

    def _describe(self, hash: str, size: int, head: bytes, name: str | None, mime: str | None) -> BlobRow:
        mime = mime or sniff_mime(head, name)
        width = height = None
        if mime.startswith("image/"):
            width, height = _image_size(self.path(hash))
        return BlobRow(hash=hash, mime=mime, size=size, width=width, height=height, name=name, created_at=time.time())

    async def _register(self, row: BlobRow) -> BlobRef:
        existing = await self.repo.get_blob_row(row.hash)
        if existing is None:
            await self.repo.put_blob_row(row)
            existing = row
        ref = BlobRef.model_validate(existing.model_dump(exclude={"created_at"}))
        if row.name:
            ref.name = row.name
        return ref

    async def put_file(self, src: Path, name: str | None = None, mime: str | None = None) -> BlobRef:
        return await self._register(await asyncio.to_thread(self._ingest_file, Path(src), name, mime))

    async def put_bytes(self, data: bytes, name: str | None = None, mime: str | None = None) -> BlobRef:
        return await self._register(await asyncio.to_thread(self._ingest_bytes, data, name, mime))

    async def get(self, hash: str) -> BlobRef | None:
        row = await self.repo.get_blob_row(hash)
        if row is None:
            return None
        return BlobRef.model_validate(row.model_dump(exclude={"created_at"}))

    async def thumbnail(self, hash: str, size: int = 256) -> Path:
        src = self.path(hash)
        dst = self.thumbs_dir / f"{hash}_{size}.webp"
        if dst.exists():
            return dst
        if not src.exists():
            raise FileNotFoundError(hash)
        key = dst.name
        lock = self._thumb_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                if not dst.exists():
                    from studio_core.ops.image import make_thumbnail

                    tmp = dst.with_name(f".{dst.stem}.{uuid.uuid4().hex}.webp")
                    try:
                        await asyncio.to_thread(make_thumbnail, src, tmp, size)
                        await asyncio.to_thread(os.replace, tmp, dst)
                    finally:
                        tmp.unlink(missing_ok=True)
        finally:
            if not lock.locked():
                self._thumb_locks.pop(key, None)
        return dst
