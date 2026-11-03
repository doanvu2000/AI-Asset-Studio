"""Saved NewRun settings: a named set of inputs + params per recipe (optionally per project)."""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import DEFAULT_PROJECT
from studio_core.store.repo import SqliteRepo

router = APIRouter(prefix="/api/presets/runs", dependencies=[Depends(require_token)])


class RunPresetCreate(BaseModel):
    name: str = Field(max_length=80)
    recipe_id: str = Field(min_length=1, max_length=120)
    project_id: str | None = None
    inputs: dict[str, Any] = {}
    params: dict[str, Any] = {}

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


def _repo(request: Request) -> SqliteRepo:
    return request.app.state.repo


@router.get("")
async def list_run_presets(request: Request, recipe_id: str | None = None, project_id: str | None = None) -> Response:
    return OrjsonResponse(await _repo(request).list_run_presets(recipe_id, project_id))


@router.post("")
async def create_run_preset(request: Request, body: RunPresetCreate) -> Response:
    repo = _repo(request)
    pid = None if body.project_id in (None, "", DEFAULT_PROJECT) else body.project_id
    if pid is not None and await repo.get_project(pid) is None:
        raise ApiError(404, "not_found", f"unknown project: {pid}")
    preset = {"id": uuid.uuid4().hex, "name": body.name, "recipe_id": body.recipe_id, "project_id": pid,
              "inputs": body.inputs, "params": body.params, "created_at": time.time()}
    await repo.add_run_preset(preset)
    return OrjsonResponse(preset, 201)


@router.delete("/{preset_id}")
async def delete_run_preset(request: Request, preset_id: str) -> Response:
    if not await _repo(request).delete_run_preset(preset_id):
        raise ApiError(404, "not_found", f"unknown preset: {preset_id}")
    return OrjsonResponse({"ok": True})
