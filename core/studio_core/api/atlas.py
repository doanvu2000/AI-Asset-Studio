"""Texture atlas of sprites from several steps (Gallery multi-select) written under exports/app/atlas."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import StepStatus
from studio_core.export import atlas as at
from studio_core.export.collider import DEFAULT_TOLERANCE, ColliderOptions, default_enabled
from studio_core.export.sheet import ident
from studio_core.store.bundle import _write
from studio_core.store.export import safe_component

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
_lock = asyncio.Lock()


class AtlasItem(BaseModel):
    step_id: str = Field(max_length=64)
    name: str | None = Field(None, max_length=120)


class AtlasBody(BaseModel):
    steps: list[AtlasItem] = Field(min_length=1, max_length=500)
    name: str = Field("atlas", min_length=1, max_length=80)
    # None = engine-neutral PNG + JSON only (the one place rotation is allowed).
    engine: Literal["unity", "godot"] | None = None
    ppu: float = Field(100.0, gt=0, le=10000)
    pixel: bool | None = None
    collider: bool | None = None
    mobile: bool = False
    atlas_max_size: Literal[512, 1024, 2048, 4096] = 2048
    atlas_padding: int = Field(2, ge=0, le=16)
    atlas_extrude: int = Field(1, ge=0, le=8)
    atlas_pot: bool = True
    atlas_trim: bool = True
    atlas_rotate: bool = False


def _build(items: list[tuple[Any, list[Any], str, Any]], blobs: Any, body: AtlasBody, root: Path) -> dict[str, Any]:
    sprites: list[at.Sprite] = []
    sources: dict[str, tuple[int, int, int]] = {}
    skipped: list[str] = []
    for step, siblings, label, collider in items:
        got, src = at.sprites_from_step(step, siblings, blobs, label, body.ppu, body.pixel, collider)
        if not got:
            skipped.append(label)
        sprites += got
        sources.update(src)
    opts = at.AtlasOptions(max_size=body.atlas_max_size, padding=body.atlas_padding, extrude=body.atlas_extrude,
                           pot=body.atlas_pot, trim=body.atlas_trim, rotate=body.atlas_rotate and not body.engine)
    atlas = at.pack(sprites, body.name, opts, sources)
    files = at.engine_files(atlas, body.engine, body.ppu, f"atlas:{atlas.name}")
    budget = None
    if body.mobile and body.engine:
        from studio_core.export import mobile as mob

        files, budget = mob.apply(files, body.engine, atlas.pixel, f"atlas:{atlas.name}", atlas.name)
    folder = safe_component(f"{atlas.name}-{body.engine or 'json'}", "atlas")
    dst = root / "atlas" / folder
    dst.parent.mkdir(parents=True, exist_ok=True)
    written = _write(dst, files)
    return {"dir": str(dst.resolve()), "name": atlas.name, "engine": body.engine, "stats": atlas.stats,
            "files": [str(p.resolve()) for p in written], "mobile": budget, "skipped": skipped}


@router.post("/atlas")
async def build_atlas(request: Request, body: AtlasBody) -> Response:
    repo = request.app.state.repo
    settings = request.app.state.settings
    root = getattr(settings, "app_exports_dir", None) or Path(settings.data_dir) / "exports" / "app"
    items, labels, runs = [], set(), {}
    for it in body.steps:
        step = await repo.get_step(it.step_id)
        if step is None:
            raise ApiError(404, "not_found", f"unknown step: {it.step_id}")
        if step.status not in (StepStatus.SUCCEEDED, StepStatus.CACHED) or not step.outputs:
            raise ApiError(409, "not_exportable", f"step {it.step_id} has no finished outputs")
        if step.run_id not in runs:
            runs[step.run_id] = (await repo.get_run(step.run_id), await repo.get_steps(step.run_id))
        run, siblings = runs[step.run_id]
        base = ident(it.name or str(step.params.get("asset") or "") or step.key, "asset")
        label, n = base, 2
        while label.lower() in labels:
            label, n = f"{base}_{n}", n + 1
        labels.add(label.lower())
        on = body.collider if body.collider is not None else default_enabled(getattr(run, "recipe_id", None))
        items.append((step, siblings, label, ColliderOptions(DEFAULT_TOLERANCE) if on else None))
    if _lock.locked():
        raise ApiError(409, "busy", "an atlas is already being built")
    async with _lock:
        try:
            out = await asyncio.to_thread(_build, items, request.app.state.blobs, body, Path(root))
        except at.AtlasError as e:
            raise ApiError(422, "not_packable", str(e)) from e
    return OrjsonResponse(out, status_code=201)
