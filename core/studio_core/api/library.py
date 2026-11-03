"""Shader preset catalog and "put existing files into the Library" (shared by the app and the MCP import_to_library)."""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token, require_token_or_query
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import DEFAULT_PROJECT, RunStatus, VersionStatus
from studio_core.export.shader_preview import MAX_SAMPLE_BYTES, SampleError, preview
from studio_core.export.shaders import PRESETS, ShaderError
from studio_core.export.shaders import catalog as shader_catalog

router = APIRouter(prefix="/api")
authed = [Depends(require_token)]

RECIPE = "library-import"
IMPORT_TIMEOUT_S = 120.0
MAX_PATHS = 200
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".glb", ".gltf", ".fbx", ".obj", ".json", ".atlas",
                        ".mp4", ".webm", ".mov", ".wav", ".mp3", ".ogg"})
FINISHED = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED)


class ImportBody(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=MAX_PATHS)
    hashes: list[str] = Field(default_factory=list, max_length=MAX_PATHS)
    project_id: str | None = None
    asset_name: str | None = Field(default=None, max_length=200)
    approve: bool = True
    group: Literal["per_file", "single"] | None = None


@router.get("/shaders", dependencies=authed)
async def shaders() -> Response:
    return OrjsonResponse(shader_catalog())


@router.get("/shaders/{shader_id}/preview", dependencies=[Depends(require_token_or_query)])
async def shader_preview_image(request: Request, shader_id: str, color: str | None = None,
                               speed: float | None = Query(default=None, ge=0, le=20),
                               still: bool = False, blob: str | None = None) -> Response:
    if shader_id not in PRESETS:
        raise ApiError(404, "not_found", f"unknown shader preset '{shader_id}'")
    sample = None
    if blob is not None:
        blobs = request.app.state.blobs
        try:
            path = blobs.path(blob)
        except ValueError as e:
            raise ApiError(400, "invalid_hash", str(e)) from e
        ref = await blobs.get(blob)
        if ref is None or not await asyncio.to_thread(path.is_file):
            raise ApiError(404, "not_found", f"unknown blob: {blob}")
        if ref.size > MAX_SAMPLE_BYTES:
            raise ApiError(400, "invalid", "sample image is larger than 8 MB")
        sample = (blob, path)
    try:
        data, animated = await asyncio.to_thread(
            preview, request.app.state.settings.thumbs_dir / "shaders", shader_id, color, speed, still, sample)
    except (ShaderError, SampleError) as e:
        raise ApiError(400, "invalid", str(e)) from e
    return Response(data, media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=3600", "X-Shader-Preview": "animated" if animated and not still else "static"})


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


async def _checked_hashes(state, hashes: list[str]) -> list[tuple[str, str]]:
    """Blob hashes (regex + present in the store) with their stored names; never a path from the caller."""
    out: list[tuple[str, str]] = []
    for h in dict.fromkeys(hashes):
        if not isinstance(h, str) or not HASH_RE.match(h):
            raise ApiError(400, "invalid_hash", f"invalid blob hash: {h!r}")
        ref = await state.blobs.get(h)
        if ref is None or not await asyncio.to_thread(state.blobs.path(h).is_file):
            raise ApiError(404, "not_found", f"unknown blob: {h}")
        out.append((h, ref.name or h[:12]))
    return out


def _unique_names(stems: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for stem in stems:
        key = stem.casefold()
        seen[key] = seen.get(key, 0) + 1
        out.append(stem if seen[key] == 1 else f"{stem} {seen[key]}")
    return out


async def _import_run(state, refs: list[str], title: str, project_id: str, approve: bool) -> dict:
    try:
        run_id = await state.engine.start_run(RECIPE, {"files": refs, "review": False}, None,
                                              project_id=project_id, title=title)
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
        if approve and v.status != VersionStatus.APPROVED:
            v = await state.repo.set_version_status(v.id, VersionStatus.APPROVED) or v
            state.bus.publish("version.updated", v.model_dump(mode="json"), run_id=run_id)
        versions.append({"version_id": v.id, "status": v.status.value, "asset_name": v.asset_name,
                         "name": v.blob.name, "hash": v.blob.hash, "mime": v.blob.mime})
    return {"run_id": run_id, "asset_name": title, "versions": versions}


@router.post("/library/import", dependencies=authed)
async def import_files(request: Request, body: ImportBody) -> Response:
    state = request.app.state
    if not body.paths and not body.hashes:
        raise ApiError(400, "invalid", "paths or hashes must list at least one item")
    files = await asyncio.to_thread(_checked, body.paths) if body.paths else []
    blobs = await _checked_hashes(state, body.hashes)
    project_id = body.project_id or DEFAULT_PROJECT
    if project_id != DEFAULT_PROJECT and await state.repo.get_project(project_id) is None:
        raise ApiError(404, "not_found", f"unknown project: {project_id}")
    units: list[tuple[str, str]] = []  # (blob hash, display name), one per distinct blob
    for p in files:
        ref = await state.blobs.put_file(p, name=p.name)
        if all(ref.hash != h for h, _ in units):
            units.append((ref.hash, p.name))
    units += [(h, n) for h, n in blobs if all(h != u for u, _ in units)]
    group = body.group or ("single" if len(units) == 1 or body.asset_name else "per_file")
    if group == "per_file" and body.asset_name and len(units) > 1:
        raise ApiError(400, "invalid", "asset_name only applies to group=single (per_file names assets after each file)")
    if group == "single":
        jobs = [([h for h, _ in units], body.asset_name or Path(units[0][1]).stem)]
    else:
        jobs = [([h], n) for (h, _), n in zip(units, _unique_names([Path(n).stem for _, n in units]), strict=True)]
    items: list[dict] = []
    for refs, title in jobs:
        try:
            items.append(await _import_run(state, refs, title, project_id, body.approve))
        except ApiError as e:
            if not items:
                raise
            done = ", ".join(i["run_id"] for i in items)
            raise ApiError(e.status, e.code, f"{e.message} (after {len(items)} of {len(jobs)} assets were already created: {done})",
                           {"created_run_ids": [i["run_id"] for i in items]}) from e
    first = items[0]
    return OrjsonResponse({"run_id": first["run_id"], "project_id": project_id, "versions": first["versions"],
                           "group": group, "items": items}, status_code=201)
