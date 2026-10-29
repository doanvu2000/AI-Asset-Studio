"""Hit VFX binding: the attack tracks of a sprite sheet with their detected impact frame/point, the VFX flipbooks in
the library, and the export option that resolves a picked VFX step into HitOptions."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import BlobRef, StepRow, StepStatus
from studio_core.export.hitfx import DEFAULT_SIZE, HitOptions, Vfx, analyze, vfx_layout, vfx_name
from studio_core.store.bundle import engine_source

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

VFX_RECIPE = "vfx-flipbook"
DONE = (StepStatus.SUCCEEDED, StepStatus.CACHED)


class HitsBody(BaseModel):
    vfx_step: str = Field(min_length=1, max_length=80)
    frames: dict[str, int] = Field(default_factory=dict)
    points: dict[str, tuple[float, float]] = Field(default_factory=dict)
    size: float = Field(DEFAULT_SIZE, gt=0, le=4)
    clips: list[str] | None = Field(None, max_length=32)


def _sheet_refs(step: StepRow) -> tuple[BlobRef, BlobRef] | None:
    s, m = step.outputs.get("sheet"), step.outputs.get("meta")
    return (s[0], m[0]) if s and m else None


def _read_json(blobs: Any, ref: BlobRef) -> dict[str, Any] | None:
    try:
        doc = json.loads(blobs.path(ref.hash).read_bytes())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _is_flipbook(meta: dict[str, Any]) -> bool:
    return bool(meta.get("frame_w")) and not meta.get("clips") and not meta.get("normal") \
        and not meta.get("row_angles") and not meta.get("angles")


async def _vfx_entry(request: Request, run: Any, step: StepRow) -> dict[str, Any] | None:
    refs = _sheet_refs(step)
    if step.kind != "op.sheet" or step.status not in DONE or refs is None:
        return None
    blobs = request.app.state.blobs
    meta = await asyncio.to_thread(_read_json, blobs, refs[1])
    if meta is None or not _is_flipbook(meta):
        return None
    prompt = str((getattr(run, "inputs", None) or {}).get("prompt") or "")
    v = Vfx(vfx_name(prompt, f"vfx_{step.id[:6]}"), blobs.path(refs[0].hash), meta)
    lay, cells, fps, loop = vfx_layout(v)
    return {"step_id": step.id, "run_id": step.run_id, "name": v.name, "prompt": prompt,
            "mode": (getattr(run, "inputs", None) or {}).get("mode"), "sheet": refs[0], "meta": refs[1],
            "frames": len(cells), "fps": fps, "loop": loop, "frame_w": lay.frame_w, "frame_h": lay.frame_h,
            "created_at": getattr(run, "created_at", None)}


@router.get("/hitfx/vfx")
async def list_vfx(request: Request, limit: int = Query(200, ge=1, le=1000)) -> Response:
    """Finished VFX flipbooks (vfx-flipbook runs), newest first."""
    repo = request.app.state.repo
    out = []
    for run in await repo.list_runs(limit=limit):
        if run.recipe_id != VFX_RECIPE:
            continue
        steps = [s for s in await repo.get_steps(run.id) if s.kind == "op.sheet"]
        for s in sorted(steps, key=lambda s: s.key != "sheet"):
            entry = await _vfx_entry(request, run, s)
            if entry is not None:
                out.append(entry)
                break
    return OrjsonResponse({"vfx": out})


async def _step(request: Request, step_id: str) -> StepRow:
    step = await request.app.state.repo.get_step(step_id)
    if step is None:
        raise ApiError(404, "not_found", f"unknown step: {step_id}")
    if step.status not in DONE or not step.outputs:
        raise ApiError(409, "not_exportable", "step has no finished outputs")
    return step


@router.get("/steps/{step_id}/hits")
async def step_hits(request: Request, step_id: str, clips: str | None = None) -> Response:
    """Attack tracks of the step's sprite sheet with the detected impact frame and per-frame impact points."""
    step = await _step(request, step_id)
    repo, blobs = request.app.state.repo, request.app.state.blobs
    siblings = await repo.get_steps(step.run_id)
    src = await asyncio.to_thread(engine_source, step, siblings, blobs, "x")
    if src.sheet is None or src.meta is None:
        raise ApiError(422, "not_exportable", "step has no sprite sheet")
    wanted = tuple(c.strip() for c in clips.split(",") if c.strip()) if clips else None
    sockets = (src.paperdoll or {}).get("sockets_doc")
    lay, hits = await asyncio.to_thread(analyze, Path(src.sheet), src.meta, src.clip_hint, src.directional_rows,
                                        sockets, wanted)
    refs = _sheet_refs(step)
    if refs is None and src.paperdoll:
        sheet, meta = await blobs.get(str(src.paperdoll["sheet"])), await blobs.get(str(src.paperdoll["sheet_meta"]))
        refs = (sheet, meta) if sheet and meta else None
    return OrjsonResponse({"step_id": step.id, "frame_w": lay.frame_w, "frame_h": lay.frame_h,
                           "pivot": list(lay.pivot), "clips": lay.clips(), "socket": bool(sockets),
                           "sheet": refs[0] if refs else None, "meta": refs[1] if refs else None,
                           "tracks": [h.as_dict() for h in hits]})


async def resolve(request: Request, body: HitsBody | None) -> HitOptions | None:
    """The export option: the picked VFX step must be a finished flipbook sheet."""
    if body is None:
        return None
    step = await _step(request, body.vfx_step)
    run = await request.app.state.repo.get_run(step.run_id)
    entry = await _vfx_entry(request, run, step)
    if entry is None:
        raise ApiError(422, "invalid", "hit VFX must be a finished VFX flipbook sheet")
    blobs = request.app.state.blobs
    meta = await asyncio.to_thread(_read_json, blobs, entry["meta"])
    return HitOptions(Vfx(entry["name"], blobs.path(entry["sheet"].hash), meta or {}),
                      frames=dict(body.frames), clips=tuple(body.clips) if body.clips else None, size=body.size,
                      points={k: (float(v[0]), float(v[1])) for k, v in body.points.items()})


def has_attacks(src: Any) -> bool:
    from studio_core.export.hitfx import is_attack
    from studio_core.export.sheet import layout

    if src is None or src.sheet is None or src.meta is None:
        return False
    try:
        lay = layout(src.meta, src.clip_hint, directional_rows=src.directional_rows)
    except (KeyError, TypeError, ValueError):
        return False
    return any(t.dir and len(t.cells) > 1 and is_attack(t.clip) for t in lay.tracks)
