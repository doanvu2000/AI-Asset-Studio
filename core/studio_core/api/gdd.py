"""GDD -> asset plan (Claude) and starting an edited plan: workflow items become one flow batch per workflow, recipe
items one recipe batch, all attached to the project."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import orjson
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.adapters.gdd import MAX_GDD_CHARS
from studio_core.adapters.gdd_plan import MAX_ITEMS, GddPlanner, PlanItem, flow_specs, missing_inputs
from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.api.flows import _call, _flows
from studio_core.api.routes import BatchCreate, BatchItem, start_batch
from studio_core.contracts import DEFAULT_PROJECT, StepError
from studio_core.engine.flow_batch import FlowBatchCreate, FlowBatchItem

router = APIRouter(prefix="/api/gdd", dependencies=[Depends(require_token)])
MAX_PLANS = 50  # per project


class PlanBody(BaseModel):
    text: str | None = Field(None, max_length=MAX_GDD_CHARS)
    blob_id: str | None = None
    project_id: str = DEFAULT_PROJECT
    limit: int = Field(MAX_ITEMS, ge=1, le=MAX_ITEMS)
    model: str | None = None


class StartBody(BaseModel):
    items: list[PlanItem] | None = Field(None, max_length=MAX_ITEMS)
    project_id: str | None = None
    title: str = Field("", max_length=200)


def _planner(request: Request) -> GddPlanner:
    st = request.app.state
    if getattr(st, "gdd_planner", None) is None:
        st.gdd_planner = GddPlanner(st.settings.data_dir / "tmp" / "gdd")
    return st.gdd_planner


async def _save_plan(request: Request, plan: dict[str, Any], gdd: str) -> None:
    db = request.app.state.repo.db
    stored = {k: v for k, v in _plan_view(plan).items() if k not in ("id", "project_id")}
    await db.write("INSERT INTO gdd_plans (id, project_id, gdd, plan, created_at) VALUES (?, ?, ?, ?, ?)",
                   (plan["id"], plan["project_id"], gdd, orjson.dumps(stored).decode(), time.time()))
    await db.write("DELETE FROM gdd_plans WHERE project_id = ? AND id NOT IN "
                   "(SELECT id FROM gdd_plans WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?)",
                   (plan["project_id"], plan["project_id"], MAX_PLANS))


def _plan_from_row(row: Any) -> dict[str, Any]:
    data = orjson.loads(row["plan"])
    data["items"] = [PlanItem.model_validate(i) for i in data.get("items", [])]
    return {"id": row["id"], "project_id": row["project_id"], **data}


def _recipes(request: Request) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in request.app.state.engine.list_recipes()]


def _plan_view(plan: dict[str, Any]) -> dict[str, Any]:
    return {**plan, "items": [i.model_dump(mode="json") for i in plan["items"]]}


async def _project_context(request: Request, project_id: str) -> str:
    if project_id == DEFAULT_PROJECT:
        return ""
    p = await request.app.state.repo.get_project(project_id)
    if p is None:
        raise ApiError(404, "not_found", f"unknown project: {project_id}")
    return f"{p.name}. {p.description}".strip(" .")


async def _gdd_text(request: Request, body: PlanBody) -> str:
    if body.text and body.text.strip():
        return body.text
    if not body.blob_id:
        raise ApiError(400, "invalid", "send `text` or `blob_id`")
    blobs = request.app.state.engine.blobs
    if await blobs.get(body.blob_id) is None:
        raise ApiError(404, "not_found", f"unknown blob: {body.blob_id}")
    raw = await asyncio.to_thread(blobs.path(body.blob_id).read_bytes)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ApiError(400, "invalid", "only UTF-8 text/markdown files are read; paste the text of a PDF") from e
    if not text.strip():
        raise ApiError(400, "invalid", "the file is empty")
    return text[:MAX_GDD_CHARS]


@router.post("/plan")
async def make_plan(request: Request, body: PlanBody) -> Response:
    text = await _gdd_text(request, body)
    context = await _project_context(request, body.project_id)
    try:
        items, meta = await _planner(request).plan(text, _flows(request).library.list(), _recipes(request),
                                                   limit=body.limit, project=context, model=body.model)
    except StepError as e:
        status = 504 if e.code == "claude_timeout" else 502
        raise ApiError(status, e.code, e.message) from e
    plan = {"id": uuid.uuid4().hex, "project_id": body.project_id, "items": items, **meta}
    for it in items:
        it.id = uuid.uuid4().hex[:12]
    await _save_plan(request, plan, text)
    return OrjsonResponse(_plan_view(plan), status_code=201)


async def _plan(request: Request, plan_id: str) -> dict[str, Any]:
    row = await request.app.state.repo.db.fetchone("SELECT * FROM gdd_plans WHERE id = ?", (plan_id,))
    if row is None:
        raise ApiError(404, "not_found", f"unknown plan: {plan_id}")
    return _plan_from_row(row)


@router.get("/plans")
async def list_plans(request: Request, project_id: str = DEFAULT_PROJECT,
                     limit: int = Query(MAX_PLANS, ge=1, le=MAX_PLANS)) -> Response:
    rows = await request.app.state.repo.db.fetchall(
        "SELECT * FROM gdd_plans WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?", (project_id, limit))
    out = []
    for row in rows:
        view = _plan_view(_plan_from_row(row))
        out.append({**view, "status": row["status"], "created_at": row["created_at"],
                    "batch_ids": orjson.loads(row["batch_ids"])})
    return OrjsonResponse(out)


@router.get("/plan/{plan_id}")
async def get_plan(request: Request, plan_id: str) -> Response:
    return OrjsonResponse(_plan_view(await _plan(request, plan_id)))


@router.post("/plan/{plan_id}/start")
async def start_plan(request: Request, plan_id: str, body: StartBody) -> Response:
    plan = await _plan(request, plan_id)
    items = body.items if body.items is not None else plan["items"]
    if not items:
        raise ApiError(400, "invalid", "no items to start")
    project_id = body.project_id or plan["project_id"]
    if project_id != DEFAULT_PROJECT and await request.app.state.repo.get_project(project_id) is None:
        raise ApiError(404, "not_found", f"unknown project: {project_id}")
    flows = _flows(request)
    flow_pool = {d.id: d for d in flows.library.list()}
    recipe_pool = {r["id"]: r.get("inputs") or {} for r in _recipes(request)}
    by_flow: dict[str, list[PlanItem]] = {}
    recipe_items: list[PlanItem] = []
    for it in items:
        if it.kind == "flow":
            if it.target_id not in flow_pool:
                raise ApiError(404, "not_found", f"'{it.name}': unknown workflow {it.target_id}")
            specs = flow_specs(flow_pool[it.target_id])
            by_flow.setdefault(it.target_id, []).append(it)
        else:
            if it.target_id not in recipe_pool:
                raise ApiError(404, "not_found", f"'{it.name}': unknown recipe {it.target_id}")
            specs = recipe_pool[it.target_id]
            recipe_items.append(it)
        if gaps := missing_inputs(specs, it.inputs):
            raise ApiError(400, "invalid", f"'{it.name}': missing {', '.join(gaps)}")
    base = body.title or "Gói asset từ GDD"
    flow_batch_ids: list[str] = []
    for fid, group in by_flow.items():
        batch = FlowBatchCreate(flow_id=fid, project_id=project_id, title=f"{base} · {flow_pool[fid].title}",
                                items=[FlowBatchItem(inputs=i.inputs, title=i.name) for i in group])
        started = await _call(flows.batches.start(batch))
        flow_batch_ids.append(started["id"])
    recipe_batch: dict[str, Any] = {}
    if recipe_items:
        recipe_batch = await start_batch(request, BatchCreate(
            project_id=project_id, title=base, params={"$meta": {"source": "gdd"}},
            items=[BatchItem(recipe_id=i.target_id, inputs=i.inputs, title=i.name) for i in recipe_items]))
    result = {"plan_id": plan_id, "project_id": project_id, "flow_batch_ids": flow_batch_ids,
              "recipe_batch_id": recipe_batch.get("batch_id"), "run_ids": recipe_batch.get("run_ids", [])}
    await request.app.state.repo.db.write("UPDATE gdd_plans SET status = 'started', batch_ids = ? WHERE id = ?",
                                          (orjson.dumps(result).decode(), plan_id))
    return OrjsonResponse(result, status_code=201)
