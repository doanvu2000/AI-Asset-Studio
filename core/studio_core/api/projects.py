"""Projects: a named asset set whose locked style is applied to every run started with its project_id."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import ProjectRow, ProjectStyle
from studio_core.engine.projects import style_blobs
from studio_core.ops.presets import PresetStore
from studio_core.store.repo import SqliteRepo

router = APIRouter(prefix="/api/projects", dependencies=[Depends(require_token)])


def _name(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("name must not be empty")
    return v


class ProjectCreate(BaseModel):
    name: str = Field(max_length=120)
    description: str = Field("", max_length=4000)
    style: ProjectStyle = ProjectStyle()

    _check_name = field_validator("name")(_name)


class ProjectPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=4000)
    style: ProjectStyle | None = None
    archived: bool | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return None if v is None else _name(v)


def _repo(request: Request) -> SqliteRepo:
    return request.app.state.repo


async def _check_style(request: Request, style: ProjectStyle) -> None:
    for h in style_blobs(style):
        try:
            known = await request.app.state.blobs.get(h)
        except ValueError:
            known = None
        if known is None:
            raise ApiError(400, "invalid", f"style: unknown blob {h}")
    if style.preset:
        store = PresetStore(getattr(request.app.state.settings, "presets_path", None))
        if await asyncio.to_thread(store.get, style.preset) is None:
            raise ApiError(400, "invalid", f"style: unknown export preset {style.preset}")


async def _get(request: Request, project_id: str) -> ProjectRow:
    p = await _repo(request).get_project(project_id)
    if p is None:
        raise ApiError(404, "not_found", f"unknown project: {project_id}")
    return p


async def _summary(request: Request, p: ProjectRow) -> dict[str, Any]:
    s = await _repo(request).project_summary(p.id)
    s["last_activity"] = max(s["last_activity"] or 0, p.updated_at)
    return s


def _publish(request: Request, p: ProjectRow) -> None:
    request.app.state.bus.publish("project.updated", p.model_dump(mode="json"))


@router.get("")
async def list_projects(request: Request, archived: bool = False, summary: bool = False) -> Response:
    rows = await _repo(request).list_projects(archived=archived)
    out = [p.model_dump(mode="json") for p in rows]
    if summary:
        for d, p in zip(out, rows):
            d["summary"] = await _summary(request, p)
    return OrjsonResponse(out)


@router.post("")
async def create_project(request: Request, body: ProjectCreate) -> Response:
    await _check_style(request, body.style)
    now = time.time()
    p = ProjectRow(id=uuid.uuid4().hex, name=body.name, description=body.description, style=body.style,
                   created_at=now, updated_at=now)
    await _repo(request).create_project(p)
    _publish(request, p)
    return OrjsonResponse(p.model_dump(mode="json"), status_code=201)


@router.get("/{project_id}")
async def get_project(request: Request, project_id: str) -> Response:
    return OrjsonResponse((await _get(request, project_id)).model_dump(mode="json"))


@router.patch("/{project_id}")
async def update_project(request: Request, project_id: str, body: ProjectPatch) -> Response:
    await _get(request, project_id)
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if body.style is not None:
        await _check_style(request, body.style)
        fields["style"] = body.style
    p = await _repo(request).update_project(project_id, **fields)
    assert p is not None
    _publish(request, p)
    return OrjsonResponse(p.model_dump(mode="json"))


@router.delete("/{project_id}")
async def archive_project(request: Request, project_id: str) -> Response:
    """Archives (hides from the default list); runs keep their project_id. PATCH archived=false restores it."""
    await _get(request, project_id)
    p = await _repo(request).update_project(project_id, archived=True)
    assert p is not None
    _publish(request, p)
    return OrjsonResponse(p.model_dump(mode="json"))


@router.get("/{project_id}/summary")
async def project_summary(request: Request, project_id: str) -> Response:
    return OrjsonResponse(await _summary(request, await _get(request, project_id)))

