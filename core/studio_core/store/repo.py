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
    WorkflowRunRow,
    WorkflowStatus,
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
_WORKFLOW_RUNS = _Table("workflow_runs", WorkflowRunRow, {"def_snapshot", "inputs", "nodes"})
_OUTPUTS: TypeAdapter[Outputs] = TypeAdapter(Outputs)
_BATCH_ID = """json_extract(params, '$."$meta".batch_id')"""
_WORKFLOW_RUN_ID = """json_extract(params, '$."$meta".workflow_run_id')"""


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
                        project_id: str | None = None, recipe_id: str | None = None) -> list[RunRow]:
        where: list[str] = []
        params: list[Any] = []
        if recipe_id:
            where.append("recipe_id = ?")
            params.append(recipe_id)
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
        cached: dict[str, str | None] = {}
        # SQLite caps bound parameters per statement; 500 stays far below every build's limit.
        for i in range(0, len(run_ids), 500):
            chunk = run_ids[i:i + 500]
            marks = ", ".join("?" for _ in chunk)
            for r in await self.db.fetchall(f"SELECT id, preview_hash FROM runs WHERE id IN ({marks})", chunk):
                cached[r["id"]] = r["preview_hash"]
                if r["preview_hash"]:
                    out[r["id"]] = r["preview_hash"]
        stale = [rid for rid in dict.fromkeys(run_ids) if cached.get(rid) is None]
        if stale:
            computed = await self._compute_preview_hashes(stale)
            out.update(computed)
            await self.db.write_many("UPDATE runs SET preview_hash = ? WHERE id = ? AND preview_hash IS NULL",
                                     [(computed.get(rid, ""), rid) for rid in stale])
        return out

    async def _compute_preview_hashes(self, run_ids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
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
        if "outputs" in fields:
            await self.db.write("UPDATE runs SET preview_hash = NULL WHERE id = (SELECT run_id FROM steps WHERE id = ?)",
                                (step_id,))

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

    async def cache_get_meta(self, key: str) -> dict[str, Any]:
        row = await self.db.fetchone('SELECT meta FROM cache WHERE "key" = ?', (key,))
        if row is None or not row["meta"]:
            return {}
        meta = orjson.loads(row["meta"])
        return meta if isinstance(meta, dict) else {}

    async def cache_put(self, key: str, outputs: Outputs, meta: dict[str, Any] | None = None) -> None:
        await self.db.write('INSERT OR REPLACE INTO cache ("key", outputs, meta, created_at) VALUES (?, ?, ?, ?)',
                            (key, orjson.dumps(to_jsonable_python(outputs)).decode(),
                             orjson.dumps(to_jsonable_python(meta)).decode() if meta else None, time.time()))

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

    @staticmethod
    def _versions_where(run_id: str | None, status: VersionStatus | None,
                        project_id: str | None) -> tuple[str, list[Any]]:
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
        return (" WHERE " + " AND ".join(where)) if where else "", params

    async def list_versions(self, run_id: str | None = None, status: VersionStatus | None = None,
                            limit: int = 200, project_id: str | None = None, offset: int = 0) -> list[VersionRow]:
        clause, params = self._versions_where(run_id, status, project_id)
        # id breaks created_at ties so offset pages never skip or repeat rows within one snapshot.
        sql = f"SELECT * FROM versions{clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        rows = await self.db.fetchall(sql, [*params, max(limit, 0), max(offset, 0)])
        return [_VERSIONS.decode(r) for r in rows]

    async def count_versions(self, run_id: str | None = None, status: VersionStatus | None = None,
                             project_id: str | None = None) -> int:
        clause, params = self._versions_where(run_id, status, project_id)
        row = await self.db.fetchone(f"SELECT COUNT(*) AS n FROM versions{clause}", params)
        return int(row["n"]) if row else 0

    async def versions_to_score(self, run_id: str | None = None, project_id: str | None = None,
                                stale_than: int | None = None, limit: int = 500) -> list[VersionRow]:
        """Image versions, oldest first; with `stale_than`, only those unscored or scored by another QA version."""
        where = ["mime LIKE 'image/%'"]
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

    @staticmethod
    def _run_preset(r: Any) -> dict[str, Any]:
        return {"id": r["id"], "name": r["name"], "recipe_id": r["recipe_id"], "project_id": r["project_id"],
                "inputs": orjson.loads(r["inputs"]), "params": orjson.loads(r["params"]), "created_at": r["created_at"]}

    async def add_run_preset(self, preset: dict[str, Any]) -> None:
        await self.db.write(
            "INSERT INTO run_presets (id, name, recipe_id, project_id, inputs, params, created_at) VALUES (?,?,?,?,?,?,?)",
            (preset["id"], preset["name"], preset["recipe_id"], preset["project_id"],
             orjson.dumps(preset["inputs"], default=to_jsonable_python).decode(),
             orjson.dumps(preset["params"], default=to_jsonable_python).decode(), preset["created_at"]))

    async def list_run_presets(self, recipe_id: str | None = None, project_id: str | None = None) -> list[dict[str, Any]]:
        """Presets of the recipe that apply to everything (no project) plus those of `project_id`, newest first."""
        where, args = [], []
        if recipe_id:
            where.append("recipe_id = ?")
            args.append(recipe_id)
        if project_id:
            where.append("(project_id IS NULL OR project_id = ?)")
            args.append(project_id)
        else:
            where.append("project_id IS NULL")
        rows = await self.db.fetchall(
            f"SELECT * FROM run_presets WHERE {' AND '.join(where)} ORDER BY created_at DESC, rowid DESC", args)
        return [self._run_preset(r) for r in rows]

    async def get_run_preset(self, preset_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM run_presets WHERE id = ?", (preset_id,))
        return self._run_preset(row) if row else None

    async def update_run_preset(self, preset_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        cols = {"name": lambda v: v, "inputs": lambda v: orjson.dumps(v, default=to_jsonable_python).decode(),
                "params": lambda v: orjson.dumps(v, default=to_jsonable_python).decode()}
        sets = [k for k in fields if k in cols]
        if sets:
            await self.db.write(f"UPDATE run_presets SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                                (*(cols[k](fields[k]) for k in sets), preset_id))
        return await self.get_run_preset(preset_id)

    async def delete_run_preset(self, preset_id: str) -> bool:
        return await self.db.write("DELETE FROM run_presets WHERE id = ?", (preset_id,)) > 0

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

    async def create_workflow_run(self, wr: WorkflowRunRow) -> None:
        await self.db.write(_WORKFLOW_RUNS.insert_sql(), _WORKFLOW_RUNS.to_params(wr))

    async def update_workflow_run(self, wr_id: str, **fields: Any) -> None:
        await self._update(_WORKFLOW_RUNS, wr_id, fields)

    async def get_workflow_run(self, wr_id: str) -> WorkflowRunRow | None:
        row = await self.db.fetchone("SELECT * FROM workflow_runs WHERE id = ?", (wr_id,))
        return _WORKFLOW_RUNS.decode(row) if row else None

    async def list_workflow_runs(self, limit: int = 50, workflow_id: str | None = None,
                                 project_id: str | None = None,
                                 statuses: list[WorkflowStatus] | None = None) -> list[WorkflowRunRow]:
        where: list[str] = []
        params: list[Any] = []
        if workflow_id:
            where.append("workflow_id = ?")
            params.append(workflow_id)
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        if statuses:
            where.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params.extend(WorkflowStatus(s).value for s in statuses)
        sql = "SELECT * FROM workflow_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        return [_WORKFLOW_RUNS.decode(r) for r in await self.db.fetchall(sql, [*params, limit])]

    async def create_workflow_batch(self, batch: dict[str, Any]) -> None:
        await self.db.write("INSERT INTO workflow_batches (id, workflow_id, title, project_id, run_ids, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (batch["id"], batch["workflow_id"], batch["title"], batch["project_id"],
                             orjson.dumps(batch["run_ids"]).decode(), batch["created_at"]))

    @staticmethod
    def _batch(row: Any) -> dict[str, Any]:
        return {**{k: row[k] for k in ("id", "workflow_id", "title", "project_id", "created_at")},
                "run_ids": orjson.loads(row["run_ids"])}

    async def get_workflow_batch(self, batch_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM workflow_batches WHERE id = ?", (batch_id,))
        return self._batch(row) if row else None

    async def list_workflow_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM workflow_batches ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._batch(r) for r in rows]

    async def workflow_runs_by_ids(self, ids: list[str]) -> list[WorkflowRunRow]:
        out: list[WorkflowRunRow] = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = await self.db.fetchall(
                f"SELECT * FROM workflow_runs WHERE id IN ({', '.join('?' for _ in chunk)})", chunk)
            out.extend(_WORKFLOW_RUNS.decode(r) for r in rows)
        return out

    async def runs_of_workflow_run(self, wr_id: str) -> list[RunRow]:
        """Child runs started for a workflow run, oldest first (also those it lost track of in a crash)."""
        rows = await self.db.fetchall(f"SELECT * FROM runs WHERE {_WORKFLOW_RUN_ID} = ? ORDER BY created_at",
                                      (wr_id,))
        return [_RUNS.decode(r) for r in rows]

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
            "SELECT blob FROM versions WHERE project_id = ? AND status != ? AND mime "
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
