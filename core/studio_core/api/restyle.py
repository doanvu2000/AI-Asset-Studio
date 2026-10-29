"""Restyle a project folder (recipe restyle-folder): scan preview with a time estimate, then "Áp vào project"
(backup zip + overwrite PNGs, importer files untouched) and undo, driven from a finished run."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import StepRow, StepStatus
from studio_core.export import restyle as rs
from studio_core.ops.restyle import restyled_files

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
_lock = asyncio.Lock()

RECIPE = "restyle-folder"
DONE = (StepStatus.SUCCEEDED, StepStatus.CACHED)
EDIT_KIND = "comfy.edit_flux2_klein4b"
DEFAULT_EDIT_S = 30.0
OVERHEAD_S = 45.0


class ScanBody(BaseModel):
    folder: str = Field(min_length=1, max_length=1024)
    include: str = Field("", max_length=2000)
    exclude: str = Field("", max_length=2000)


class RunBody(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class UndoBody(BaseModel):
    backup_id: str = Field(min_length=1, max_length=64)
    force: bool = False


def _backups(request: Request) -> Path:
    return rs.backup_dir(Path(request.app.state.settings.data_dir))


async def _edit_seconds(request: Request) -> tuple[float, int]:
    """Median seconds of recent Klein edits on this machine (DEFAULT_EDIT_S until there are some)."""
    repo = request.app.state.repo
    secs: list[float] = []
    for run in await repo.list_runs(limit=40):
        for s in await repo.get_steps(run.id):
            if s.kind != EDIT_KIND or s.status != StepStatus.SUCCEEDED:
                continue
            n = len(s.meta.get("items") or []) or 1
            if s.started_at and s.ended_at and s.ended_at > s.started_at:
                secs.append((s.ended_at - s.started_at) / n)
        if len(secs) >= 30:
            break
    if not secs:
        return DEFAULT_EDIT_S, 0
    secs.sort()
    return secs[len(secs) // 2], len(secs)


@router.post("/restyle/scan")
async def scan(request: Request, body: ScanBody) -> Response:
    """Read-only scan of the folder: images per kind, edit units and the estimated run time (trial and full)."""
    try:
        doc = await asyncio.to_thread(rs.scan_folder, body.folder, body.include, body.exclude)
    except rs.RestyleError as e:
        raise ApiError(422, "bad_folder", str(e)) from e
    per, measured = await _edit_seconds(request)
    trial = rs.sample_items(doc["items"], 6)
    trial_units = sum(len(rs.units_for(doc["items"][i])) for i in trial)
    items = [{"rel": i["rel"], "kind": i["kind"], "width": i["width"], "height": i["height"],
              "sprites": len(i["rects"]), "border": i["border"], "importer": i["importer"], "guid": i["guid"],
              "units": len(rs.units_for(i))} for i in doc["items"]]
    return OrjsonResponse({
        "root": doc["root"], "engine": doc["engine"], "project": doc["project"], "counts": doc["counts"],
        "units": doc["units"], "items": items, "skipped": doc["skipped"][:200], "skipped_total": len(doc["skipped"]),
        "estimate": {"edit_s": round(per, 1), "measured": measured,
                     "trial_s": round(OVERHEAD_S + trial_units * per), "trial_units": trial_units,
                     "full_s": round(OVERHEAD_S + doc["units"] * per)},
        "mirror": str(rs.mirror_root(doc)),
    })


async def _run_steps(request: Request, run_id: str) -> tuple[Any, dict[str, StepRow]]:
    repo = request.app.state.repo
    run = await repo.get_run(run_id)
    if run is None:
        raise ApiError(404, "not_found", f"unknown run: {run_id}")
    if run.recipe_id != RECIPE:
        raise ApiError(422, "bad_run", f"run {run_id} is not a {RECIPE} run")
    return run, {s.key: s for s in await repo.get_steps(run_id)}


def _read(blobs: Any, hash_: str) -> dict[str, Any]:
    try:
        doc = json.loads(blobs.path(hash_).read_bytes())
    except (OSError, ValueError) as e:
        raise ApiError(409, "missing_report", f"restyle report unreadable: {e}") from e
    if not isinstance(doc, dict):
        raise ApiError(409, "missing_report", "restyle report is not an object")
    return doc


@router.post("/restyle/apply")
async def apply(request: Request, body: RunBody) -> Response:
    """Overwrites the folder's PNGs with the approved restyle of a finished run; backup zip first."""
    run, steps = await _run_steps(request, body.run_id)
    gate, fit = steps.get("approve"), steps.get("fit")
    if fit is None or fit.status not in DONE or gate is None or gate.status not in DONE:
        raise ApiError(409, "not_approved", "the restyle is not finished and approved yet")
    refs = gate.outputs.get("restyle") or fit.outputs.get("restyle") or []
    if not refs:
        raise ApiError(409, "missing_report", "run has no restyle report")
    blobs = request.app.state.blobs
    report = await asyncio.to_thread(_read, blobs, refs[0].hash)
    files = await restyled_files(blobs, report, gate.outputs.get("images") or fit.outputs.get("images") or [])
    async with _lock:
        try:
            doc = await asyncio.to_thread(rs.apply_files, report, files, _backups(request), "apply",
                                          {"run_id": run.id, "style": report.get("style"),
                                           "title": getattr(run, "title", "")})
        except rs.RestyleError as e:
            raise ApiError(409, "apply_failed", str(e)) from e
    return OrjsonResponse(doc)


@router.post("/restyle/undo")
async def undo(request: Request, body: UndoBody) -> Response:
    async with _lock:
        try:
            out = await asyncio.to_thread(rs.undo, _backups(request), body.backup_id, body.force)
        except rs.RestyleError as e:
            raise ApiError(409, "undo_failed", str(e)) from e
    return OrjsonResponse(out)


@router.get("/restyle/backups")
async def backups(request: Request, run_id: str | None = None) -> Response:
    rows = await asyncio.to_thread(rs.list_backups, _backups(request))
    if run_id:
        rows = [r for r in rows if (r.get("source") or {}).get("run_id") == run_id]
    return OrjsonResponse({"backups": rows})
