"""Shader preset catalog and "put existing files into the Library" (shared by the app and the MCP import_to_library)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import DEFAULT_PROJECT, RunStatus, VersionStatus
from studio_core.export.shaders import catalog as shader_catalog

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

RECIPE = "library-import"
IMPORT_TIMEOUT_S = 120.0
MAX_PATHS = 200
EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".glb", ".gltf", ".fbx", ".obj", ".json", ".atlas",
                        ".mp4", ".webm", ".mov", ".wav", ".mp3", ".ogg"})
FINISHED = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED)


class ImportBody(BaseModel):
    paths: list[str] = Field(max_length=MAX_PATHS)
    project_id: str | None = None
    asset_name: str | None = Field(default=None, max_length=200)
    approve: bool = True


@router.get("/shaders")
async def shaders() -> Response:
    return OrjsonResponse(shader_catalog())


def _checked(paths: list[str]) -> list[Path]:
    if not paths:
        raise ApiError(400, "invalid", "paths must list at least one absolute file path")
    out: list[Path] = []
    for raw in dict.fromkeys(paths):
        p = Path(raw)
        if not p.is_absolute():
            raise ApiError(400, "invalid", f"absolute path required: {raw}")
        if not p.is_file():
            raise ApiError(404, "not_found", f"file not found: {raw}")
        if p.suffix.lower() not in EXTENSIONS:
            raise ApiError(400, "unsupported", f"unsupported file type: {p.name}")
        out.append(p)
    return out


@router.post("/library/import")
async def import_files(request: Request, body: ImportBody) -> Response:
    state = request.app.state
    files = await asyncio.to_thread(_checked, body.paths)
    project_id = body.project_id or DEFAULT_PROJECT
    if project_id != DEFAULT_PROJECT and await state.repo.get_project(project_id) is None:
        raise ApiError(404, "not_found", f"unknown project: {project_id}")
    refs = [(await state.blobs.put_file(p, name=p.name)).hash for p in files]
    try:
        run_id = await state.engine.start_run(
            RECIPE, {"files": refs, "review": False}, None, project_id=project_id, title=body.asset_name or files[0].stem)
    except LookupError as e:
        raise ApiError(404, "not_found", str(e.args[0]) if e.args else "recipe not found") from e
    deadline = time.monotonic() + IMPORT_TIMEOUT_S
    while True:
        run = await state.repo.get_run(run_id)
        if run is not None and (run.status in FINISHED or run.status == RunStatus.AWAITING_HUMAN):
            break
        if time.monotonic() >= deadline:
            raise ApiError(504, "timeout", f"import run {run_id} did not finish in {IMPORT_TIMEOUT_S:.0f}s")
        await asyncio.sleep(0.1)
    if run.status != RunStatus.SUCCEEDED:
        raise ApiError(409, "import_failed", f"import run {run_id} ended {run.status.value}: {run.error or ''}".strip())
    versions = []
    for v in await state.repo.list_versions(run_id=run_id, limit=5000):
        if body.approve and v.status != VersionStatus.APPROVED:
            v = await state.repo.set_version_status(v.id, VersionStatus.APPROVED) or v
            state.bus.publish("version.updated", v.model_dump(mode="json"), run_id=run_id)
        versions.append({"version_id": v.id, "status": v.status.value, "asset_name": v.asset_name,
                         "name": v.blob.name, "hash": v.blob.hash, "mime": v.blob.mime})
    return OrjsonResponse({"run_id": run_id, "project_id": project_id, "versions": versions}, status_code=201)
