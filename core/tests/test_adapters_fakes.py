"""Fake StepContext / BlobStore shared by adapter tests (no tests here)."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import blake3

from studio_core.contracts import BlobRef


class FakeBlobs:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    async def put_bytes(self, data: bytes, name: str | None = None, mime: str | None = None) -> BlobRef:
        h = blake3.blake3(data).hexdigest()
        self.path(h).write_bytes(data)
        return BlobRef(hash=h, mime=mime or "application/octet-stream", size=len(data), name=name)

    async def put_file(self, src: Path, name: str | None = None, mime: str | None = None) -> BlobRef:
        data = await asyncio.to_thread(Path(src).read_bytes)
        return await self.put_bytes(data, name or Path(src).name, mime)

    async def get(self, hash: str) -> BlobRef | None:
        p = self.path(hash)
        return BlobRef(hash=hash, mime="application/octet-stream", size=p.stat().st_size) if p.exists() else None

    def path(self, hash: str) -> Path:
        return self.root / hash

    async def thumbnail(self, hash: str, size: int = 256) -> Path:
        return self.path(hash)


class FakeCtx:
    def __init__(self, tmp: Path, run_id: str = "run1", step_id: str = "step1"):
        self.run_id = run_id
        self.step_id = step_id
        self.job_dir = tmp / "job"
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.blobs = FakeBlobs(tmp / "blobs")
        self.cancelled = asyncio.Event()
        self.progress_calls: list[tuple] = []
        self.logs: list[tuple[str, str]] = []
        self.previews: list[tuple[bytes, str]] = []

    def progress(self, value, max=1.0, label=None, node=None):
        self.progress_calls.append((value, max, label, node))

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def preview(self, data, mime="image/jpeg"):
        self.previews.append((data, mime))


def copy_blob(ctx: FakeCtx, src: Path, mime: str = "image/png") -> BlobRef:
    data = src.read_bytes()
    h = blake3.blake3(data).hexdigest()
    shutil.copyfile(src, ctx.blobs.path(h))
    return BlobRef(hash=h, mime=mime, size=len(data), name=src.name)
