"""Per-blob image index (pHash + dHash + embedding) kept in SQLite and mirrored in memory; filled lazily by searches
and in the background when versions are approved."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

import numpy as np
from PIL import Image

from ..contracts import VersionRow, VersionStatus
from . import similar as sm

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS image_index (
    hash TEXT PRIMARY KEY,
    phash TEXT,
    dhash TEXT,
    model TEXT,
    vec BLOB,
    error TEXT,
    updated_at REAL NOT NULL
)
"""
CHUNK = 16


class Embedder(Protocol):
    model_id: str

    def available(self) -> bool: ...
    def embed_images(self, images: list[Image.Image]) -> np.ndarray: ...
    def embed_text(self, text: str) -> np.ndarray: ...


class ImageIndex:
    def __init__(self, db: Any, repo: Any, blobs: Any, embedder: Embedder | None) -> None:
        self.db = db
        self.repo = repo
        self.blobs = blobs
        self.embedder = embedder
        self.entries: dict[str, sm.Entry] = {}
        self.models: dict[str, str | None] = {}
        self.failed: set[str] = set()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._queued: set[str] = set()
        self._compute = asyncio.Lock()
        self._tasks: list[asyncio.Task[Any]] = []
        self._opened = False

    @property
    def model_id(self) -> str | None:
        return self.embedder.model_id if self.embedder is not None and self.embedder.available() else None

    async def open(self) -> None:
        if self._opened:
            return
        await self.db.write(SCHEMA)
        for r in await self.db.fetchall("SELECT hash, phash, dhash, model, vec, error FROM image_index"):
            vec = np.frombuffer(r["vec"], dtype=np.float16).astype(np.float32) if r["vec"] else None
            self.entries[r["hash"]] = sm.Entry(r["phash"], r["dhash"], vec)
            self.models[r["hash"]] = r["model"]
            if r["error"]:
                self.failed.add(r["hash"])
        self._opened = True

    def start(self, bus: Any | None) -> None:
        self._tasks.append(asyncio.create_task(self._worker(), name="image-index"))
        if bus is not None:
            self._tasks.append(asyncio.create_task(self._watch(bus), name="image-index-watch"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    def stale(self, h: str) -> bool:
        if h in self.failed:
            return False
        e = self.entries.get(h)
        if e is None or e.phash is None:
            return True
        model = self.model_id
        return model is not None and (e.vec is None or self.models.get(h) != model)

    def enqueue(self, hashes: Iterable[str]) -> int:
        n = 0
        for h in hashes:
            if h not in self._queued and self.stale(h):
                self._queued.add(h)
                self._queue.put_nowait(h)
                n += 1
        return n

    @property
    def pending(self) -> int:
        return len(self._queued)

    def _load(self, h: str) -> Image.Image | None:
        try:
            with Image.open(self.blobs.path(h)) as im:
                im.draft("RGB", (512, 512))
                im.load()
                return sm.flatten(im, 512)
        except (OSError, ValueError):
            return None

    def _compute_rows(self, hashes: list[str]) -> list[tuple[str, str | None, str | None, str | None, bytes | None,
                                                               str | None]]:
        imgs = {h: self._load(h) for h in hashes}
        ok = [h for h, im in imgs.items() if im is not None]
        vecs: dict[str, np.ndarray] = {}
        model = self.model_id
        if model is not None and ok and self.embedder is not None:
            try:
                arr = self.embedder.embed_images([imgs[h] for h in ok])  # type: ignore[misc]
                vecs = dict(zip(ok, arr))
            except Exception as e:
                log.warning("image index: embedding failed: %s", e)
                model = None
        rows = []
        for h, im in imgs.items():
            if im is None:
                rows.append((h, None, None, None, None, "unreadable"))
                continue
            p, d = sm.hashes(im)
            v = vecs.get(h)
            rows.append((h, p, d, model if v is not None else None,
                         v.astype(np.float16).tobytes() if v is not None else None, None))
        return rows

    async def _index(self, hashes: list[str]) -> None:
        async with self._compute:
            todo = [h for h in dict.fromkeys(hashes) if self.stale(h)]
            for i in range(0, len(todo), CHUNK):
                rows = await asyncio.to_thread(self._compute_rows, todo[i:i + CHUNK])
                now = time.time()
                await self.db.write_many(
                    "INSERT INTO image_index (hash, phash, dhash, model, vec, error, updated_at) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(hash) DO UPDATE SET phash=excluded.phash, dhash=excluded.dhash, model=excluded.model, "
                    "vec=excluded.vec, error=excluded.error, updated_at=excluded.updated_at",
                    [(*r, now) for r in rows])
                for h, p, d, model, vec, err in rows:
                    self.entries[h] = sm.Entry(p, d, np.frombuffer(vec, np.float16).astype(np.float32) if vec else None)
                    self.models[h] = model
                    if err:
                        self.failed.add(h)

    async def ensure(self, hashes: Iterable[str], budget_s: float = 15.0) -> int:
        """Indexes what it can within the budget; the rest goes to the background queue. Returns how many wait."""
        todo = [h for h in dict.fromkeys(hashes) if self.stale(h)]
        deadline = time.monotonic() + budget_s
        done = 0
        for i in range(0, len(todo), CHUNK):
            if time.monotonic() >= deadline:
                break
            await self._index(todo[i:i + CHUNK])
            done = i + CHUNK
        return self.enqueue(todo[done:])

    async def _worker(self) -> None:
        while True:
            h = await self._queue.get()
            batch = [h]
            while len(batch) < CHUNK and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            try:
                await self._index(batch)
            except Exception:
                log.exception("image index: background batch failed")
            finally:
                self._queued.difference_update(batch)

    async def _watch(self, bus: Any) -> None:
        async for ev in bus.subscribe():
            if ev.type != "version.updated" or ev.data.get("status") != VersionStatus.APPROVED.value:
                continue
            blob = ev.data.get("blob") or {}
            if str(blob.get("mime", "")).startswith("image/") and blob.get("hash"):
                self.enqueue([blob["hash"]])

    async def reindex(self, versions: list[VersionRow], force: bool = False) -> int:
        hashes = [v.blob.hash for v in sm.library_versions(versions)]
        if force:
            for h in hashes:
                self.entries.pop(h, None)
                self.failed.discard(h)
        return self.enqueue(hashes)

    def status(self) -> dict[str, Any]:
        model = self.model_id
        return {"model": model, "mode": "clip" if model else "phash", "indexed": len(self.entries),
                "embedded": sum(1 for h, e in self.entries.items() if e.vec is not None and self.models.get(h) == model),
                "pending": self.pending, "failed": len(self.failed)}

    def query_entry(self, img: Image.Image) -> sm.Entry:
        flat = sm.flatten(img, 512)
        p, d = sm.hashes(flat)
        vec = None
        if self.model_id is not None and self.embedder is not None:
            vec = self.embedder.embed_images([flat])[0]
        return sm.Entry(p, d, vec)

    def search(self, query: sm.Entry, versions: list[VersionRow], k: int,
               exclude: Callable[[VersionRow], bool] = lambda v: False) -> tuple[str, list[dict[str, Any]]]:
        pool = [v for v in sm.library_versions(versions) if not exclude(v) and v.blob.hash in self.entries]
        model = self.model_id
        if query.vec is not None and model is not None:
            rows = [v for v in pool if self.entries[v.blob.hash].vec is not None and self.models.get(v.blob.hash) == model]
            if rows:
                mat = np.stack([self.entries[v.blob.hash].vec for v in rows])  # type: ignore[misc]
                hits = sm.cosine_topk(query.vec, mat, k)
                return "clip", [{**sm.asset_view(rows[i]), "score": round(s, 4),
                                 "phash_distance": sm.hamming(query.phash, self.entries[rows[i].blob.hash].phash)}
                                for i, s in hits]
        ranked = sm.rank_by_hash(query, [(v, self.entries[v.blob.hash]) for v in pool], k)
        return "phash", [{**sm.asset_view(v), "score": round(s, 4), "phash_distance": d} for v, s, d in ranked]

    def duplicates(self, versions: list[VersionRow], threshold: float) -> list[dict[str, Any]]:
        lib = sm.library_versions(versions)
        model = self.model_id
        entries = {h: (e if self.models.get(h) == model else sm.Entry(e.phash, e.dhash, None))
                   for h, e in self.entries.items()}
        copies: dict[str, dict[str, VersionRow]] = {}
        for v in versions:
            if (v.blob.mime or "").startswith("image/") and v.status != VersionStatus.REJECTED:
                copies.setdefault(v.blob.hash, {}).setdefault(v.run_id, v)
        return sm.duplicate_groups(lib, entries, threshold,
                                   {h: list(by_run.values()) for h, by_run in copies.items() if len(by_run) > 1})
