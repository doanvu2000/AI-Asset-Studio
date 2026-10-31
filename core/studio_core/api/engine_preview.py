"""Export a step for Unity/Godot, open it in the real engine and return import errors + a screenshot blob."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import StepStatus
from studio_core.export.preview import PreviewJob, PreviewManager, availability
from studio_core.store.bundle import BundleError, export_engine

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


class PreviewBody(BaseModel):
    engine: Literal["unity", "godot"]
    name: str | None = None
    ppu: float = Field(100.0, gt=0, le=10000)
    pixel: bool | None = None


def _manager(request: Request) -> PreviewManager:
    state = request.app.state
    if getattr(state, "engine_previews", None) is None:
        state.engine_previews = PreviewManager(state.settings, state.blobs)
    return state.engine_previews


def _job(request: Request, job_id: str) -> PreviewJob:
    job = _manager(request).get(job_id)
    if job is None:
        raise ApiError(404, "not_found", f"unknown engine preview: {job_id}")
    return job


@router.get("/engine-preview")
async def preview_engines(request: Request) -> Response:
    return OrjsonResponse({"engines": await asyncio.to_thread(availability, request.app.state.settings)})


@router.post("/steps/{step_id}/engine-preview")
async def start_preview(request: Request, step_id: str, body: PreviewBody) -> Response:
    engines = await asyncio.to_thread(availability, request.app.state.settings)
    if not engines[body.engine]["available"]:
        raise ApiError(409, "engine_unavailable", engines[body.engine]["reason"] or f"{body.engine} unavailable")
    repo = request.app.state.repo
    step = await repo.get_step(step_id)
    if step is None:
        raise ApiError(404, "not_found", f"unknown step: {step_id}")
    if step.status not in (StepStatus.SUCCEEDED, StepStatus.CACHED) or not step.outputs:
        raise ApiError(409, "not_exportable", "step has no finished outputs")
    settings = request.app.state.settings
    root = getattr(settings, "app_exports_dir", None) or Path(settings.data_dir) / "exports" / "app"
    siblings = await repo.get_steps(step.run_id)
    try:
        out = await asyncio.to_thread(export_engine, step, siblings, request.app.state.blobs, Path(root), body.name,
                                      body.engine, body.ppu, body.pixel)
    except BundleError as e:
        raise ApiError(422, "not_exportable", str(e)) from e
    job = _manager(request).start(step.id, body.engine, Path(out["dir"]))
    return OrjsonResponse(job.view(), status_code=202)


@router.get("/engine-preview/jobs/{job_id}")
async def get_preview(request: Request, job_id: str) -> Response:
    return OrjsonResponse(_job(request, job_id).view())


@router.post("/engine-preview/jobs/{job_id}/cancel")
async def cancel_preview(request: Request, job_id: str) -> Response:
    job = _job(request, job_id)
    _manager(request).cancel(job)
    return OrjsonResponse(job.view())


class OpenBody(BaseModel):
    engine: Literal["unity", "godot"]
    name: str | None = None


@router.post("/steps/{step_id}/open-in-engine")
async def open_in_engine(request: Request, step_id: str, body: OpenBody) -> Response:
    """Playable scene only: export it and open it in the engine's editor (Godot project / staged Unity project)."""
    from studio_core.export.playable_scene import open_project

    repo = request.app.state.repo
    step = await repo.get_step(step_id)
    if step is None:
        raise ApiError(404, "not_found", f"unknown step: {step_id}")
    if step.status not in (StepStatus.SUCCEEDED, StepStatus.CACHED) or not step.outputs:
        raise ApiError(409, "not_exportable", "step has no finished outputs")
    settings = request.app.state.settings
    blobs = request.app.state.blobs
    from studio_core.store.bundle import engine_source

    src = await asyncio.to_thread(engine_source, step, [], blobs, "x")
    if not src.playable:
        raise ApiError(422, "not_exportable", "step has no playable scene")
    root = getattr(settings, "app_exports_dir", None) or Path(settings.data_dir) / "exports" / "app"
    siblings = await repo.get_steps(step.run_id)
    name = body.name or str(src.playable.get("asset") or "") or None
    try:
        out = await asyncio.to_thread(export_engine, step, siblings, blobs, Path(root), name, body.engine)
        from studio_core.export.sheet import ident

        info = await asyncio.to_thread(open_project, settings, Path(out["dir"]), body.engine,
                                       ident(out["name"], "asset"))
    except BundleError as e:
        raise ApiError(422, "not_exportable", str(e)) from e
    except (RuntimeError, ValueError, OSError) as e:
        raise ApiError(409, "engine_unavailable", str(e)) from e
    return OrjsonResponse({**info, "export_dir": out["dir"]})
