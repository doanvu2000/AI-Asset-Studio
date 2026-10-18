from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.responses import Response

from studio_core import __version__
from studio_core.api.auth import require_token, require_token_or_query
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import (
    BlobStore,
    Engine,
    GateResolution,
    Repo,
    Resource,
    RunStatus,
    StepStatus,
    VersionStatus,
)

public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
blob_api = APIRouter(prefix="/api", dependencies=[Depends(require_token_or_query)])

_IMMUTABLE = "public, max-age=31536000, immutable"
_GENERIC_MIMES = {None, "", "application/octet-stream"}


class RunCreate(BaseModel):
    recipe_id: str
    inputs: dict[str, Any] = {}
    params: dict[str, Any] | None = None
    title: str = ""
    project_id: str = "default"


def _json(content: Any, status: int = 200) -> OrjsonResponse:
    return OrjsonResponse(content, status_code=status)


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _repo(request: Request) -> Repo:
    return request.app.state.repo


def _blobs(request: Request) -> BlobStore:
    return request.app.state.blobs


def _resource(request: Request, name: str) -> Resource:
    res: dict[str, Resource] = request.app.state.resources
    if name not in res:
        raise ApiError(404, "not_found", f"unknown resource: {name}")
    return res[name]


async def _engine_call[T](aw: Awaitable[T]) -> T:
    try:
        return await aw
    except LookupError as e:
        raise ApiError(404, "not_found", str(e.args[0]) if e.args else "not found") from e
    except ValueError as e:
        raise ApiError(400, "invalid", str(e)) from e


@public.get("/health")
async def health() -> Response:
    return _json({"ok": True, "version": __version__})


@api.post("/shutdown")
async def shutdown(request: Request) -> Response:
    request_shutdown = request.app.state.request_shutdown
    asyncio.get_running_loop().call_later(0.1, request_shutdown)
    return _json({"ok": True})


@api.get("/resources")
async def list_resources(request: Request) -> Response:
    res: dict[str, Resource] = request.app.state.resources
    return _json(list(await asyncio.gather(*(r.health() for r in res.values()))))


@api.post("/resources/{name}/start")
async def start_resource(request: Request, name: str) -> Response:
    r = _resource(request, name)
    await r.ensure_started()
    return _json(await r.health())


@api.post("/resources/{name}/stop")
async def stop_resource(request: Request, name: str) -> Response:
    r = _resource(request, name)
    await r.stop()
    return _json(await r.health())


@api.get("/recipes")
async def list_recipes(request: Request) -> Response:
    return _json(_engine(request).list_recipes())


@api.get("/kinds")
async def list_kinds(request: Request) -> Response:
    return _json(request.app.state.registry.kinds())


@api.post("/runs")
async def create_run(request: Request, body: RunCreate) -> Response:
    run_id = await _engine_call(_engine(request).start_run(
        body.recipe_id, body.inputs, body.params, project_id=body.project_id, title=body.title))
    return _json({"run_id": run_id}, 201)


@api.get("/runs")
async def list_runs(request: Request, limit: int = Query(50, ge=1, le=500), before: float | None = None,
                    status: RunStatus | None = None) -> Response:
    return _json(await _repo(request).list_runs(limit=limit, before=before, status=status))


@api.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> Response:
    repo = _repo(request)
    run = await repo.get_run(run_id)
    if run is None:
        raise ApiError(404, "not_found", f"unknown run: {run_id}")
    return _json({"run": run, "steps": await repo.get_steps(run_id)})


@api.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str) -> Response:
    await _engine_call(_engine(request).cancel_run(run_id))
    return _json({"ok": True})


@api.post("/steps/{step_id}/retry")
async def retry_step(request: Request, step_id: str) -> Response:
    await _engine_call(_engine(request).retry_step(step_id))
    return _json({"ok": True})


@api.post("/gates/{step_id}")
async def resolve_gate(request: Request, step_id: str, body: GateResolution) -> Response:
    await _engine_call(_engine(request).resolve_gate(step_id, body))
    return _json({"ok": True})


@api.get("/gates")
async def list_gates(request: Request) -> Response:
    return _json(await _repo(request).list_steps_by_status([StepStatus.AWAITING_HUMAN]))


@api.get("/versions")
async def list_versions(request: Request, run_id: str | None = None,
                        status: VersionStatus | None = None) -> Response:
    return _json(await _repo(request).list_versions(run_id=run_id, status=status))


async def _set_version(request: Request, version_id: str, status: VersionStatus) -> Response:
    v = await _repo(request).set_version_status(version_id, status)
    if v is None:
        raise ApiError(404, "not_found", f"unknown version: {version_id}")
    request.app.state.bus.publish("version.updated", v.model_dump(mode="json"), run_id=v.run_id)
    return _json(v)


@api.post("/versions/{version_id}/approve")
async def approve_version(request: Request, version_id: str) -> Response:
    return await _set_version(request, version_id, VersionStatus.APPROVED)


@api.post("/versions/{version_id}/reject")
async def reject_version(request: Request, version_id: str) -> Response:
    return await _set_version(request, version_id, VersionStatus.REJECTED)


def _spool_to_disk(src: BinaryIO, tmp_dir: Path) -> Path:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dst = tmp_dir / f"upload-{uuid.uuid4().hex}"
    src.seek(0)
    with dst.open("wb") as out:
        shutil.copyfileobj(src, out, 1 << 20)
    return dst


@api.post("/blobs")
async def upload_blob(request: Request, file: UploadFile = File(...)) -> Response:
    tmp_root = Path(getattr(request.app.state.settings, "data_dir", tempfile.gettempdir())) / "tmp"
    tmp = await asyncio.to_thread(_spool_to_disk, file.file, tmp_root)
    try:
        mime = None if file.content_type in _GENERIC_MIMES else file.content_type
        ref = await _blobs(request).put_file(tmp, name=file.filename or None, mime=mime)
    finally:
        await asyncio.to_thread(tmp.unlink, True)
        await file.close()
    return _json(ref, 201)


def _blob_path(blobs: BlobStore, hash: str) -> Path:
    try:
        return blobs.path(hash)
    except ValueError as e:
        raise ApiError(400, "invalid_hash", str(e)) from e


@blob_api.get("/blobs/{hash}")
async def get_blob(request: Request, hash: str) -> Response:
    blobs = _blobs(request)
    path = _blob_path(blobs, hash)
    ref = await blobs.get(hash)
    if ref is None or not await asyncio.to_thread(path.is_file):
        raise ApiError(404, "not_found", f"unknown blob: {hash}")
    return FileResponse(path, media_type=ref.mime, headers={"Cache-Control": _IMMUTABLE})


@blob_api.get("/blobs/{hash}/thumb")
async def get_thumb(request: Request, hash: str, size: int = Query(256, ge=16, le=2048)) -> Response:
    blobs = _blobs(request)
    _blob_path(blobs, hash)
    if await blobs.get(hash) is None:
        raise ApiError(404, "not_found", f"unknown blob: {hash}")
    try:
        thumb = await blobs.thumbnail(hash, size)
    except FileNotFoundError as e:
        raise ApiError(404, "not_found", f"blob file missing: {hash}") from e
    except ImportError as e:
        raise ApiError(503, "thumbnail_unavailable", str(e)) from e
    except Exception as e:
        raise ApiError(422, "thumbnail_failed", f"{type(e).__name__}: {e}") from e
    return FileResponse(thumb, media_type="image/webp", headers={"Cache-Control": _IMMUTABLE})
