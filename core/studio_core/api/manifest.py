"""Project asset list (manifest): CRUD, GDD -> rows via Claude, CSV/YAML import/export, run selected rows as one batch."""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.adapters.gdd import MAX_GDD_CHARS, MAX_ROWS, ManifestPlanner
from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.api.routes import MAX_BATCH, BatchCreate, BatchItem, start_batch
from studio_core.contracts import DEFAULT_PROJECT, StepError
from studio_core.engine import manifest as mf
from studio_core.engine.manifest import ManifestRow
from studio_core.store.repo import SqliteRepo

router = APIRouter(prefix="/api/projects/{project_id}/manifest", dependencies=[Depends(require_token)])
_locks: dict[str, asyncio.Lock] = {}
Format = Literal["csv", "yaml"]


class ManifestPut(BaseModel):
    rows: list[ManifestRow] = Field(max_length=mf.MAX_ROWS)


class GddBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_GDD_CHARS)
    limit: int = Field(MAX_ROWS, ge=1, le=MAX_ROWS)
    model: str | None = None
    append: bool = False


class ImportBody(BaseModel):
    format: Format
    text: str = Field(max_length=2_000_000)
    append: bool = True


class RunBody(BaseModel):
    row_ids: list[str] | None = None
    title: str = ""


def _repo(request: Request) -> SqliteRepo:
    return request.app.state.repo


def _lock(project_id: str) -> asyncio.Lock:
    return _locks.setdefault(project_id, asyncio.Lock())


def _planner(request: Request) -> ManifestPlanner:
    st = request.app.state
    if getattr(st, "manifest_planner", None) is None:
        st.manifest_planner = ManifestPlanner(st.settings.data_dir / "tmp" / "manifest")
    return st.manifest_planner


async def _project(request: Request, project_id: str) -> Any:
    p = await _repo(request).get_project(project_id)
    if p is None and project_id != DEFAULT_PROJECT:
        raise ApiError(404, "not_found", f"unknown project: {project_id}")
    return p


async def _load(request: Request, project_id: str) -> tuple[list[ManifestRow], float | None]:
    raw, updated = await _repo(request).get_manifest(project_id)
    return [ManifestRow.model_validate(r) for r in raw], updated


async def _save(request: Request, project_id: str, rows: list[ManifestRow]) -> float:
    if len(rows) > mf.MAX_ROWS:
        raise ApiError(400, "invalid", f"asset list limited to {mf.MAX_ROWS} rows")
    seen: set[str] = set()
    for r in rows:
        if r.id in seen:
            r.id = mf.new_row_id()
        seen.add(r.id)
    updated = await _repo(request).save_manifest(project_id, [r.model_dump(mode="json") for r in rows])
    request.app.state.bus.publish("manifest.updated", {"project_id": project_id, "updated_at": updated})
    return updated


async def _statuses(request: Request, rows: list[ManifestRow]) -> dict[str, dict[str, Any]]:
    states = await _repo(request).run_states([r.run_id for r in rows if r.run_id])
    return {r.id: {"status": mf.row_status(states.get(r.run_id) if r.run_id else None),
                   "run_status": (states.get(r.run_id) or {}).get("status") if r.run_id else None} for r in rows}


async def _view(request: Request, project_id: str, rows: list[ManifestRow], updated: float | None,
                **extra: Any) -> Response:
    st = await _statuses(request, rows)
    out = [{**r.model_dump(mode="json"), **st[r.id]} for r in rows]
    counts: dict[str, int] = {}
    for r in out:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return OrjsonResponse({"project_id": project_id, "rows": out, "counts": counts, "updated_at": updated, **extra})


def _recipes(request: Request) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in request.app.state.engine.list_recipes()]


@router.get("")
async def get_manifest(request: Request, project_id: str) -> Response:
    await _project(request, project_id)
    rows, updated = await _load(request, project_id)
    return await _view(request, project_id, rows, updated)


@router.put("")
async def put_manifest(request: Request, project_id: str, body: ManifestPut) -> Response:
    await _project(request, project_id)
    async with _lock(project_id):
        updated = await _save(request, project_id, body.rows)
    return await _view(request, project_id, body.rows, updated)


@router.post("/from-gdd")
async def from_gdd(request: Request, project_id: str, body: GddBody) -> Response:
    """Proposed rows (not saved unless `append`); `dropped` lists rows the model bound to unknown recipes."""
    p = await _project(request, project_id)
    current, _ = await _load(request, project_id)
    context = f"{p.name}. {p.description}".strip(" .") if p is not None else ""
    try:
        rows, meta = await _planner(request).propose(
            body.text, _recipes(request), limit=body.limit, project=context,
            existing=[r.name for r in current if r.name], model=body.model)
    except StepError as e:
        raise ApiError(504 if e.code == "claude_timeout" else 502, e.code, e.message) from e
    if not body.append:
        return OrjsonResponse({"rows": [r.model_dump(mode="json") for r in rows], **meta})
    async with _lock(project_id):
        merged = (await _load(request, project_id))[0] + rows
        updated = await _save(request, project_id, merged)
    return await _view(request, project_id, merged, updated, added=[r.id for r in rows], **meta)


@router.get("/export")
async def export_manifest(request: Request, project_id: str, format: Format = "csv") -> Response:
    await _project(request, project_id)
    rows, _ = await _load(request, project_id)
    if format == "yaml":
        return Response(mf.to_yaml(rows), media_type="application/x-yaml; charset=utf-8")
    statuses = {k: v["status"] for k, v in (await _statuses(request, rows)).items()}
    # BOM so Excel opens the Vietnamese names as UTF-8.
    return Response("﻿" + mf.to_csv(rows, statuses), media_type="text/csv; charset=utf-8")


@router.post("/import")
async def import_manifest(request: Request, project_id: str, body: ImportBody) -> Response:
    await _project(request, project_id)
    try:
        rows = (mf.from_csv if body.format == "csv" else mf.from_yaml)(body.text)
    except Exception as e:
        raise ApiError(400, "invalid", f"cannot read {body.format}: {e}") from e
    async with _lock(project_id):
        merged = ((await _load(request, project_id))[0] if body.append else []) + rows
        updated = await _save(request, project_id, merged)
    return await _view(request, project_id, merged, updated, added=[r.id for r in rows])


@router.post("/run")
async def run_manifest(request: Request, project_id: str, body: RunBody) -> Response:
    """Selected rows (default: every row not run yet or failed) start as one batch; each row keeps its own recipe."""
    await _project(request, project_id)
    async with _lock(project_id):
        rows, _ = await _load(request, project_id)
        if body.row_ids is None:
            st = await _statuses(request, rows)
            chosen = [r for r in rows if st[r.id]["status"] in ("todo", "failed")]
        else:
            by_id = {r.id: r for r in rows}
            if unknown := [i for i in body.row_ids if i not in by_id]:
                raise ApiError(404, "not_found", f"unknown row: {unknown[0]}")
            chosen = [by_id[i] for i in dict.fromkeys(body.row_ids)]
        if not chosen:
            raise ApiError(400, "invalid", "no rows to run")
        if len(chosen) > MAX_BATCH:
            raise ApiError(400, "invalid", f"batch limited to {MAX_BATCH} rows")
        for r in chosen:
            if not r.recipe_id:
                raise ApiError(400, "invalid", f"row '{r.name or r.id}': no recipe")
        batch = BatchCreate(project_id=project_id, title=body.title or "Danh sách asset",
                            items=[BatchItem(recipe_id=r.recipe_id, inputs=r.inputs, title=r.name,
                                             meta={"manifest_row": r.id}) for r in chosen],
                            params={"$meta": {"source": "manifest"}})
        started = await start_batch(request, batch)
        for r, run_id in zip(chosen, started["run_ids"]):
            r.run_id = run_id
        updated = await _save(request, project_id, rows)
    return await _view(request, project_id, rows, updated, **started)
