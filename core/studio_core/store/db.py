from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    recipe_id TEXT NOT NULL,
    recipe_hash TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    inputs TEXT NOT NULL DEFAULT '{}',
    params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    error TEXT,
    created_at REAL NOT NULL,
    ended_at REAL
);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at);
-- Must match repo._BATCH_ID verbatim, or SQLite will not use it.
CREATE INDEX IF NOT EXISTS idx_runs_batch_id ON runs(json_extract(params, '$."$meta".batch_id'));

CREATE TABLE IF NOT EXISTS steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    "key" TEXT NOT NULL,
    kind TEXT NOT NULL,
    lane TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    deps TEXT NOT NULL DEFAULT '[]',
    params TEXT NOT NULL DEFAULT '{}',
    inputs TEXT NOT NULL DEFAULT '{}',
    outputs TEXT NOT NULL DEFAULT '{}',
    cache_key TEXT,
    error TEXT,
    progress REAL,
    meta TEXT NOT NULL DEFAULT '{}',
    "order" INTEGER NOT NULL DEFAULT 0,
    started_at REAL,
    ended_at REAL
);
CREATE INDEX IF NOT EXISTS idx_steps_run_id ON steps(run_id);
CREATE INDEX IF NOT EXISTS idx_steps_status ON steps(status);

CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    asset_name TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    blob TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_run_id ON versions(run_id);
CREATE INDEX IF NOT EXISTS idx_versions_created_at ON versions(created_at);

CREATE TABLE IF NOT EXISTS blobs (
    hash TEXT PRIMARY KEY,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    width INTEGER,
    height INTEGER,
    name TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    style TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cache (
    "key" TEXT PRIMARY KEY,
    outputs TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class Database:
    """Single aiosqlite connection; writes serialized by a lock so multi-statement writes stay atomic."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database not open")
        return self._conn

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        for table in ("runs", "versions"):
            async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
                cols = {r["name"] for r in await cur.fetchall()}
            if "project_id" not in cols:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")
        # After the ALTER: a DB from before projects has no column for these indexes to name.
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, created_at)")
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_versions_project ON versions(project_id, created_at)")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def write(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._write_lock:
            cur = await self.conn.execute(sql, params)
            await self.conn.commit()
            return cur.rowcount

    async def write_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        async with self._write_lock:
            await self.conn.executemany(sql, rows)
            await self.conn.commit()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())
