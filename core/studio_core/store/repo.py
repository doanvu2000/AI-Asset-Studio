from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import orjson
from pydantic import BaseModel, TypeAdapter
from pydantic_core import to_jsonable_python

from studio_core.contracts import (
    BlobRow,
    Outputs,
    ProjectRow,
    RunRow,
    RunStatus,
    StepRow,
    StepStatus,
    VersionRow,
    VersionStatus,
)
from studio_core.store.db import Database


class _Table[M: BaseModel]:
    def __init__(self, name: str, model: type[M], json_cols: set[str], pk: str = "id") -> None:
        self.name = name
        self.model = model
        self.json_cols = json_cols
        self.pk = pk
        self.cols = list(model.model_fields)

    def encode(self, col: str, value: Any) -> Any:
        if col not in self.model.model_fields:
            raise ValueError(f"{self.name}: unknown column {col!r}")
        value = to_jsonable_python(value)
        if col in self.json_cols:
            return None if value is None else orjson.dumps(value).decode()
        return value

    def to_params(self, row: M) -> list[Any]:
        return [self.encode(c, getattr(row, c)) for c in self.cols]

    def decode(self, row: Mapping[str, Any]) -> M:
        data = {c: row[c] for c in self.cols}
        for c in self.json_cols:
            if data[c] is not None:
                data[c] = orjson.loads(data[c])
        return self.model.model_validate(data)

    def insert_sql(self, verb: str = "INSERT") -> str:
        cols = ", ".join(f'"{c}"' for c in self.cols)
        marks = ", ".join("?" for _ in self.cols)
        return f"{verb} INTO {self.name} ({cols}) VALUES ({marks})"

    def update_sql(self, fields: Mapping[str, Any]) -> tuple[str, list[Any]]:
        sets = ", ".join(f'"{c}" = ?' for c in fields)
        return (f'UPDATE {self.name} SET {sets} WHERE "{self.pk}" = ?',
                [self.encode(c, v) for c, v in fields.items()])


_RUNS = _Table("runs", RunRow, {"inputs", "params"})
_STEPS = _Table("steps", StepRow, {"deps", "params", "inputs", "outputs", "error", "meta"})
_VERSIONS = _Table("versions", VersionRow, {"blob", "qa"})
_BLOBS = _Table("blobs", BlobRow, set(), pk="hash")
_PROJECTS = _Table("projects", ProjectRow, {"style"})
_OUTPUTS: TypeAdapter[Outputs] = TypeAdapter(Outputs)
_BATCH_ID = """json_extract(params, '$."$meta".batch_id')"""


class SqliteRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def _update(self, table: _Table[Any], key: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        if table.pk in fields:
            raise ValueError(f"{table.name}: primary key is immutable")
        sql, params = table.update_sql(fields)
        await self.db.write(sql, [*params, key])

    async def create_run(self, run: RunRow) -> None:
        await self.db.write(_RUNS.insert_sql(), _RUNS.to_params(run))

    async def update_run(self, run_id: str, **fields: Any) -> None:
        await self._update(_RUNS, run_id, fields)

    async def get_run(self, run_id: str) -> RunRow | None:
        row = await self.db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
        return _RUNS.decode(row) if row else None

    async def list_runs(self, limit: int = 50, before: float | None = None, status: RunStatus | None = None,
                        batch_id: str | None = None, batched: bool = False,
                        project_id: str | None = None) -> list[RunRow]:
        where: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        if batch_id is not None:
            where.append(f"{_BATCH_ID} = ?")
            params.append(batch_id)
        elif batched:
            where.append(f"{_BATCH_ID} IS NOT NULL")
        if before is not None:
            where.append("created_at < ?")
            params.append(before)
        if status is not None:
            where.append("status = ?")
            params.append(RunStatus(status).value)
        sql = "SELECT * FROM runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        rows = await self.db.fetchall(sql, [*params, limit])
        return [_RUNS.decode(r) for r in rows]

    async def run_preview_hashes(self, run_ids: list[str]) -> dict[str, str]:
        """Per run, the first image output of the latest step that produced one (list thumbnails). A sprite sheet is
        skipped for the frames before it: eight frames squeezed into a square tile read as specks."""
        if not run_ids:
            return {}
        out: dict[str, str] = {}
        # SQLite caps bound parameters per statement; 500 stays far below every build's limit.
        for i in range(0, len(run_ids), 500):
            chunk = run_ids[i:i + 500]
            marks = ", ".join("?" for _ in chunk)
            rows = await self.db.fetchall(
                f"SELECT run_id, outputs FROM steps WHERE run_id IN ({marks}) AND outputs != '{{}}' "
                'ORDER BY run_id, "order" DESC, rowid DESC', chunk)
            for row in rows:
                if row["run_id"] in out:
                    continue
                for port, blobs in orjson.loads(row["outputs"]).items():
                    if port == "sheet":
                        continue
                    image = next((b for b in blobs if str(b.get("mime", "")).startswith("image/")), None)
                    if image:
                        out[row["run_id"]] = image["hash"]
                        break
        return out

    async def create_steps(self, steps: list[StepRow]) -> None:
        if steps:
            await self.db.write_many(_STEPS.insert_sql(), [_STEPS.to_params(s) for s in steps])

    async def update_step(self, step_id: str, **fields: Any) -> None:
        await self._update(_STEPS, step_id, fields)

    async def get_step(self, step_id: str) -> StepRow | None:
        row = await self.db.fetchone("SELECT * FROM steps WHERE id = ?", (step_id,))
        return _STEPS.decode(row) if row else None

    async def get_steps(self, run_id: str) -> list[StepRow]:
        rows = await self.db.fetchall('SELECT * FROM steps WHERE run_id = ? ORDER BY "order", rowid', (run_id,))
        return [_STEPS.decode(r) for r in rows]

    async def list_steps_by_status(self, statuses: list[StepStatus]) -> list[StepRow]:
        if not statuses:
            return []
        marks = ", ".join("?" for _ in statuses)
        rows = await self.db.fetchall(
            f'SELECT * FROM steps WHERE status IN ({marks}) ORDER BY run_id, "order"',
            [StepStatus(s).value for s in statuses])
        return [_STEPS.decode(r) for r in rows]

    async def recent_failed_steps(self, limit: int = 20) -> list[StepRow]:
        rows = await self.db.fetchall(
            "SELECT * FROM steps WHERE status = ? ORDER BY COALESCE(ended_at, started_at, 0) DESC LIMIT ?",
            (StepStatus.FAILED.value, limit))
        return [_STEPS.decode(r) for r in rows]

    async def cache_get(self, key: str) -> Outputs | None:
        row = await self.db.fetchone('SELECT outputs FROM cache WHERE "key" = ?', (key,))
        if row is None:
            return None
        return _OUTPUTS.validate_json(row["outputs"])

    async def cache_put(self, key: str, outputs: Outputs) -> None:
        await self.db.write('INSERT OR REPLACE INTO cache ("key", outputs, created_at) VALUES (?, ?, ?)',
                            (key, orjson.dumps(to_jsonable_python(outputs)).decode(), time.time()))

    async def add_version(self, v: VersionRow) -> None:
        await self.db.write(_VERSIONS.insert_sql(), _VERSIONS.to_params(v))

    async def set_version_status(self, version_id: str, status: VersionStatus) -> VersionRow | None:
        n = await self.db.write("UPDATE versions SET status = ? WHERE id = ?",
                                (VersionStatus(status).value, version_id))
        if not n:
            return None
        row = await self.db.fetchone("SELECT * FROM versions WHERE id = ?", (version_id,))
        return _VERSIONS.decode(row) if row else None

    async def set_version_qa(self, version_id: str, qa: dict[str, Any] | None) -> VersionRow | None:
        await self._update(_VERSIONS, version_id, {"qa": qa})
        row = await self.db.fetchone("SELECT * FROM versions WHERE id = ?", (version_id,))
        return _VERSIONS.decode(row) if row else None

    async def get_version(self, version_id: str) -> VersionRow | None:
        row = await self.db.fetchone("SELECT * FROM versions WHERE id = ?", (version_id,))
        return _VERSIONS.decode(row) if row else None

    async def list_versions(self, run_id: str | None = None, status: VersionStatus | None = None,
                            limit: int = 200, project_id: str | None = None) -> list[VersionRow]:
        where: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        if status is not None:
            where.append("status = ?")
            params.append(VersionStatus(status).value)
        sql = "SELECT * FROM versions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        rows = await self.db.fetchall(sql, [*params, limit])
        return [_VERSIONS.decode(r) for r in rows]

    async def versions_to_score(self, run_id: str | None = None, project_id: str | None = None,
                                stale_than: int | None = None, limit: int = 500) -> list[VersionRow]:
        """Image versions, oldest first; with `stale_than`, only those unscored or scored by another QA version."""
        where = ["json_extract(blob, '$.mime') LIKE 'image/%'"]
        params: list[Any] = []
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        if stale_than is not None:
            where.append("(qa IS NULL OR json_extract(qa, '$.version') IS NOT ?)")
            params.append(stale_than)
        sql = f"SELECT * FROM versions WHERE {' AND '.join(where)} ORDER BY created_at LIMIT ?"
        return [_VERSIONS.decode(r) for r in await self.db.fetchall(sql, [*params, limit])]

    async def put_blob_row(self, b: BlobRow) -> None:
        await self.db.write(_BLOBS.insert_sql("INSERT OR IGNORE"), _BLOBS.to_params(b))

    async def get_blob_row(self, hash: str) -> BlobRow | None:
        row = await self.db.fetchone("SELECT * FROM blobs WHERE hash = ?", (hash,))
        return _BLOBS.decode(row) if row else None

    async def create_project(self, p: ProjectRow) -> None:
        await self.db.write(_PROJECTS.insert_sql(), _PROJECTS.to_params(p))

    async def update_project(self, project_id: str, **fields: Any) -> ProjectRow | None:
        await self._update(_PROJECTS, project_id, {**fields, "updated_at": time.time()})
        return await self.get_project(project_id)

    async def get_project(self, project_id: str) -> ProjectRow | None:
        row = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        return _PROJECTS.decode(row) if row else None

    async def list_projects(self, archived: bool = False) -> list[ProjectRow]:
        sql = "SELECT * FROM projects" + ("" if archived else " WHERE archived = 0") + " ORDER BY updated_at DESC"
        return [_PROJECTS.decode(r) for r in await self.db.fetchall(sql)]

    async def get_manifest(self, project_id: str) -> tuple[list[dict[str, Any]], float | None]:
        row = await self.db.fetchone("SELECT rows, updated_at FROM manifests WHERE project_id = ?", (project_id,))
        return (orjson.loads(row["rows"]), row["updated_at"]) if row else ([], None)

    async def save_manifest(self, project_id: str, rows: list[dict[str, Any]]) -> float:
        now = time.time()
        await self.db.write("INSERT INTO manifests (project_id, rows, updated_at) VALUES (?, ?, ?) "
                            "ON CONFLICT(project_id) DO UPDATE SET rows = excluded.rows, updated_at = excluded.updated_at",
                            (project_id, orjson.dumps(rows, default=to_jsonable_python).decode(), now))
        return now

    async def get_style_bible(self, project_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT data FROM style_bibles WHERE project_id = ?", (project_id,))
        return orjson.loads(row["data"]) if row else None

    async def save_style_bible(self, project_id: str, data: dict[str, Any]) -> None:
        await self.db.write("INSERT INTO style_bibles (project_id, data, updated_at) VALUES (?, ?, ?) "
                            "ON CONFLICT(project_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                            (project_id, orjson.dumps(data, default=to_jsonable_python).decode(), time.time()))

    async def run_states(self, run_ids: list[str]) -> dict[str, dict[str, Any]]:
        """run id -> {status, approved (count of approved versions)}; unknown ids are left out."""
        out: dict[str, dict[str, Any]] = {}
        ids = list(dict.fromkeys(run_ids))
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = await self.db.fetchall(
                "SELECT r.id, r.status, (SELECT COUNT(*) FROM versions v WHERE v.run_id = r.id AND v.status = ?) "
                f"AS approved FROM runs r WHERE r.id IN ({','.join('?' * len(chunk))})",
                (VersionStatus.APPROVED.value, *chunk))
            out.update({r["id"]: {"status": r["status"], "approved": r["approved"]} for r in rows})
        return out

    async def project_summary(self, project_id: str) -> dict[str, Any]:
        rows = await self.db.fetchall(
            "SELECT status, COUNT(*) AS n, MAX(MAX(created_at, COALESCE(ended_at, 0))) AS last FROM runs "
            "WHERE project_id = ? GROUP BY status", (project_id,))
        runs = {r["status"]: r["n"] for r in rows}
        last = max((r["last"] for r in rows), default=None)
        vrows = await self.db.fetchall(
            "SELECT status, COUNT(*) AS n FROM versions WHERE project_id = ? GROUP BY status", (project_id,))
        versions = {r["status"]: r["n"] for r in vrows}
        cover = await self.db.fetchone(
            "SELECT blob FROM versions WHERE project_id = ? AND status != ? AND json_extract(blob, '$.mime') "
            "LIKE 'image/%' ORDER BY status = ? DESC, created_at DESC LIMIT 1",
            (project_id, VersionStatus.REJECTED.value, VersionStatus.APPROVED.value))
        cover_hash = orjson.loads(cover["blob"])["hash"] if cover else None
        if cover_hash is None:
            latest = await self.db.fetchall(
                "SELECT id FROM runs WHERE project_id = ? ORDER BY created_at DESC LIMIT 5", (project_id,))
            previews = await self.run_preview_hashes([r["id"] for r in latest])
            cover_hash = next((previews[r["id"]] for r in latest if r["id"] in previews), None)
        return {"project_id": project_id, "runs": runs, "runs_total": sum(runs.values()),
                "versions": versions, "approved_versions": versions.get(VersionStatus.APPROVED.value, 0),
                "last_activity": last, "cover_hash": cover_hash}
