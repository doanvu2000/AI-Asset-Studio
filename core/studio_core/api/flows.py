"""Workflows: saved recipe graphs (/api/flows) and their runs (/api/flow-runs)."""
from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import DEFAULT_PROJECT, WorkflowDef
from studio_core.engine.flows import FlowEngine, FlowInvalid, FlowStateError

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


class FlowRunCreate(BaseModel):
    inputs: dict[str, Any] = {}
    project_id: str = DEFAULT_PROJECT
    title: str = Field("", max_length=200)


def _flows(request: Request) -> FlowEngine:
    flows = getattr(request.app.state.engine, "flows", None)
    if flows is None:
        raise ApiError(503, "engine_unavailable", "workflows need the studio engine")
    return flows


def _invalid(e: FlowInvalid) -> ApiError:
    return ApiError(400, "invalid", str(e), detail={"issues": e.issues})


async def _call[T](aw: Awaitable[T]) -> T:
    try:
        return await aw
    except FlowInvalid as e:
        raise _invalid(e) from e
    except FlowStateError as e:
        raise ApiError(409, "conflict", str(e)) from e
    except LookupError as e:
        raise ApiError(404, "not_found", str(e.args[0]) if e.args else "not found") from e
    except ValueError as e:
        raise ApiError(400, "invalid", str(e)) from e


def _get_def(flows: FlowEngine, fid: str) -> WorkflowDef:
    d = flows.library.get(fid)
    if d is None:
        raise ApiError(404, "not_found", f"unknown workflow: {fid}")
    return d


def _save(flows: FlowEngine, d: WorkflowDef, status: int) -> Response:
    try:
        saved, issues = flows.save_def(d)
    except FlowInvalid as e:
        raise _invalid(e) from e
    except ValueError as e:
        raise ApiError(400, "invalid", str(e)) from e
    return OrjsonResponse({**saved.model_dump(mode="json"), "issues": issues}, status_code=status)


@router.get("/flows")
async def list_flows(request: Request) -> Response:
    return OrjsonResponse([d.model_dump(mode="json") for d in _flows(request).library.list()])


@router.get("/flows/{fid}")
async def get_flow(request: Request, fid: str) -> Response:
    return OrjsonResponse(_get_def(_flows(request), fid).model_dump(mode="json"))


@router.post("/flows")
async def create_flow(request: Request, body: WorkflowDef) -> Response:
    flows = _flows(request)
    if flows.library.has_user(body.id):
        raise ApiError(409, "conflict", f"workflow '{body.id}' already exists; use PUT to replace it")
    return _save(flows, body, 201)


@router.put("/flows/{fid}")
async def put_flow(request: Request, fid: str, body: WorkflowDef) -> Response:
    if body.id != fid:
        raise ApiError(400, "invalid", f"workflow id '{body.id}' does not match '{fid}'")
    return _save(_flows(request), body, 200)


@router.delete("/flows/{fid}")
async def delete_flow(request: Request, fid: str) -> Response:
    flows = _flows(request)
    if not flows.library.has_user(fid):
        if flows.library.has_builtin(fid):
            raise ApiError(400, "invalid", f"'{fid}' is a built-in workflow and cannot be deleted")
        raise ApiError(404, "not_found", f"unknown workflow: {fid}")
    restored = flows.library.delete(fid)
    return OrjsonResponse({"ok": True, "restored_builtin": restored})


@router.post("/flows/{fid}/validate")
async def validate_flow(request: Request, fid: str, body: WorkflowDef | None = Body(None)) -> Response:
    """Checks `body` when given (an unsaved edit), else the stored definition."""
    flows = _flows(request)
    d = body if body is not None else _get_def(flows, fid)
    issues = flows.validate(d)
    return OrjsonResponse({"ok": not any(i["level"] == "error" for i in issues), "issues": issues})


@router.post("/flows/{fid}/runs")
async def start_flow_run(request: Request, fid: str, body: FlowRunCreate) -> Response:
    flows = _flows(request)
    wr = await _call(flows.start(fid, body.inputs, project_id=body.project_id, title=body.title))
    return OrjsonResponse(wr.model_dump(mode="json"), status_code=201)


@router.get("/flow-runs")
async def list_flow_runs(request: Request, workflow_id: str | None = None, project_id: str | None = None,
                         limit: int = 50) -> Response:
    rows = await _flows(request).list_runs(limit=max(1, min(limit, 500)), workflow_id=workflow_id,
                                           project_id=project_id)
    return OrjsonResponse([r.model_dump(mode="json") for r in rows])


@router.get("/flow-runs/{rid}")
async def get_flow_run(request: Request, rid: str) -> Response:
    return OrjsonResponse(await _call(_flows(request).detail(rid)))


@router.post("/flow-runs/{rid}/nodes/{node_id}/retry")
async def retry_flow_node(request: Request, rid: str, node_id: str) -> Response:
    wr = await _call(_flows(request).retry_node(rid, node_id))
    return OrjsonResponse(wr.model_dump(mode="json"))


@router.post("/flow-runs/{rid}/cancel")
async def cancel_flow_run(request: Request, rid: str) -> Response:
    wr = await _call(_flows(request).cancel(rid))
    return OrjsonResponse(wr.model_dump(mode="json"))
