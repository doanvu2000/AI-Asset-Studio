"""Asset pack of a project (Unity UPM package / Godot addon + provenance) written to local disk only."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.api.project_qa import _project
from studio_core.contracts import StepStatus, VersionRow, VersionStatus
from studio_core.export.atlas import AtlasOptions
from studio_core.export.collider import DEFAULT_TOLERANCE, ColliderOptions, default_enabled
from studio_core.export.normal2d import Normal2DOptions
from studio_core.export.shaders import ShaderError, resolve as resolve_shaders
from studio_core.store import pack as pk
from studio_core.store.bundle import engine_ready
from studio_core.store.repo import SqliteRepo

router = APIRouter(prefix="/api/projects/{project_id}", dependencies=[Depends(require_token)])
_locks: dict[str, asyncio.Lock] = {}
MAX_VERSIONS = 5000


class PackBody(BaseModel):
    engines: list[Literal["unity", "godot"]] = Field(default_factory=lambda: ["unity"], min_length=1, max_length=2)
    license: Literal["cc0", "cc-by-4.0", "proprietary"] = "cc0"
    version: str | None = Field(None, max_length=32)
    # Approved version ids to pack; None = every approved version of the project.
    include: list[str] | None = Field(None, max_length=MAX_VERSIONS)
    org: str = Field("assetstudio", max_length=60)
    author: str = Field("", max_length=120)
    description: str = Field("", max_length=2000)
    out_dir: str | None = Field(None, max_length=1024)
    zip: bool = True
    ppu: float = Field(100.0, gt=0, le=10000)
    pixel: bool | None = None
    collider: bool | None = None
    collider_tolerance: float = Field(DEFAULT_TOLERANCE, ge=0, le=64)
    collider_mode: Literal["union", "frame"] = "union"
    occluder: bool = False
    shaders: list[str] = Field(default_factory=list, max_length=16)
    shader_params: dict[str, dict[str, float | str]] = Field(default_factory=dict)
    normal2d: bool = False
    normal2d_strength: float = Field(1.0, gt=0, le=10)
    normal2d_bevel: float | None = Field(None, gt=0, le=64)
    mobile: bool = False
    atlas: bool = False
    atlas_max_size: Literal[512, 1024, 2048, 4096] = 2048
    atlas_padding: int = Field(2, ge=0, le=16)
    atlas_extrude: int = Field(1, ge=0, le=8)
    atlas_pot: bool = True
    atlas_trim: bool = True


def atlas_options(body: Any) -> AtlasOptions:
    return AtlasOptions(max_size=body.atlas_max_size, padding=body.atlas_padding, extrude=body.atlas_extrude,
                        pot=body.atlas_pot, trim=body.atlas_trim)


def _repo(request: Request) -> SqliteRepo:
    return request.app.state.repo


def _data_dir(request: Request) -> Path:
    return Path(request.app.state.settings.data_dir)


async def _approved(request: Request, project_id: str) -> list[VersionRow]:
    return await _repo(request).list_versions(project_id=project_id, status=VersionStatus.APPROVED,
                                              limit=MAX_VERSIONS)


async def _assets(request: Request, versions: list[VersionRow]) -> tuple[list[pk.PackAsset], list[dict[str, Any]]]:
    """Approved versions grouped by the step that made them; one step = one asset folder."""
    repo = _repo(request)
    by_step: dict[str, list[VersionRow]] = {}
    for v in sorted(versions, key=lambda v: v.created_at):
        by_step.setdefault(v.step_id, []).append(v)
    out, skipped = [], []
    runs: dict[str, Any] = {}
    for step_id, vs in by_step.items():
        step = await repo.get_step(step_id)
        if step is None or step.status not in (StepStatus.SUCCEEDED, StepStatus.CACHED):
            skipped.append({"name": vs[0].asset_name, "step_id": step_id, "reason": "step missing or unfinished"})
            continue
        if step.run_id not in runs:
            runs[step.run_id] = (await repo.get_run(step.run_id), await repo.get_steps(step.run_id))
        run, siblings = runs[step.run_id]
        out.append(pk.PackAsset(name=vs[0].asset_name or step.key, step=step, siblings=siblings, run=run,
                                versions=vs))
    return out, skipped


def _engine_options(body: PackBody) -> pk.EngineOptions:
    try:
        shaders = resolve_shaders(body.shaders, body.shader_params) if body.shaders else None
    except ShaderError as e:
        raise ApiError(422, "invalid", str(e)) from e
    opts = ColliderOptions(body.collider_tolerance, body.collider_mode == "frame", body.occluder)

    def collider(a: pk.PackAsset) -> ColliderOptions | None:
        on = body.collider if body.collider is not None else default_enabled(a.run.recipe_id if a.run else None)
        return opts if on else None

    normal2d = Normal2DOptions(body.normal2d_strength, body.normal2d_bevel) if body.normal2d else None
    return pk.EngineOptions(ppu=body.ppu, pixel=body.pixel, collider=collider, shaders=shaders, normal2d=normal2d,
                            mobile=body.mobile)


@router.get("/packs")
async def list_packs(request: Request, project_id: str) -> Response:
    """Packs built so far (newest first), the next auto version and the approved assets a pack would include."""
    p = await _project(request, project_id)
    history = await asyncio.to_thread(pk.load_history, _data_dir(request), project_id)
    assets, skipped = await _assets(request, await _approved(request, project_id))
    blobs = request.app.state.blobs
    items = []
    for a in assets:
        ready = await asyncio.to_thread(engine_ready, a.step, blobs)
        img = next((v.blob.hash for v in a.versions if v.blob.mime.startswith("image/")), None)
        items.append({"name": a.name, "step_id": a.step.id, "run_id": a.step.run_id,
                      "recipe_id": a.run.recipe_id if a.run else None, "version_ids": [v.id for v in a.versions],
                      "engine_ready": ready, "preview_hash": img})
    name = p.name if p else project_id
    return OrjsonResponse({"project_id": project_id, "slug": pk.slugify(name, project_id[:12] or "pack"),
                           "next_version": pk.next_version(history),
                           "default_dir": str(_data_dir(request) / "packs"),
                           "packs": list(reversed(history)), "assets": items, "skipped": skipped})


@router.post("/pack")
async def build_pack(request: Request, project_id: str, body: PackBody) -> Response:
    p = await _project(request, project_id)
    if body.version:
        try:
            pk.parse_semver(body.version)
        except pk.PackError as e:
            raise ApiError(422, "invalid", str(e)) from e
    if body.out_dir and not Path(body.out_dir).is_absolute():
        raise ApiError(422, "invalid", "out_dir must be an absolute path")
    versions = await _approved(request, project_id)
    if body.include is not None:
        want = set(body.include)
        unknown = want - {v.id for v in versions}
        if unknown:
            raise ApiError(422, "invalid", f"not approved versions of this project: {', '.join(sorted(unknown)[:5])}")
        versions = [v for v in versions if v.id in want]
    assets, skipped = await _assets(request, versions)
    opts = pk.PackOptions(engines=list(dict.fromkeys(body.engines)), license=body.license, version=body.version,
                          org=body.org, author=body.author, description=body.description,
                          out_dir=Path(body.out_dir) if body.out_dir else None, zip=body.zip,
                          export=_engine_options(body), atlas=atlas_options(body) if body.atlas else None)
    lock = _locks.setdefault(project_id, asyncio.Lock())
    if lock.locked():
        raise ApiError(409, "busy", "a pack for this project is already being built")
    async with lock:
        try:
            out = await asyncio.to_thread(pk.build_pack, assets, request.app.state.blobs, opts, project_id,
                                          p.name if p else project_id, _data_dir(request))
        except pk.PackError as e:
            raise ApiError(422, "not_packable", str(e)) from e
        except OSError as e:
            raise ApiError(500, "pack_failed", f"{type(e).__name__}: {e}") from e
    out["skipped"] = skipped
    return OrjsonResponse(out, status_code=201)
