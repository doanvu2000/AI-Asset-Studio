"""Recompute the QA score of Library versions (e.g. versions made before scoring existed)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.contracts import StepRow, VersionRow
from studio_core.engine.qa import QA_VERSION, version_qa
from studio_core.store.repo import SqliteRepo

router = APIRouter(prefix="/api/qa", dependencies=[Depends(require_token)])


class RecomputeBody(BaseModel):
    run_id: str | None = None
    project_id: str | None = None
    missing_only: bool = True
    limit: int = Field(500, ge=1, le=5000)


async def _rescore(request: Request, v: VersionRow, steps: list[StepRow]) -> VersionRow | None:
    """None when nothing was stored: no producing step, not an image, or a failed scoring that would replace a score."""
    repo: SqliteRepo = request.app.state.repo
    step = next((s for s in steps if s.id == v.step_id), None)
    if step is None:
        return None
    qa = await version_qa(request.app.state.blobs, steps, step, v.blob)
    if qa is None or (qa.get("skipped") == "error" and v.qa is not None and v.qa.get("score") is not None):
        return None
    out = await repo.set_version_qa(v.id, qa)
    if out is not None:
        request.app.state.bus.publish("version.updated", out.model_dump(mode="json"), run_id=out.run_id)
    return out


@router.post("/versions/{version_id}")
async def recompute_version(request: Request, version_id: str) -> Response:
    repo: SqliteRepo = request.app.state.repo
    v = await repo.get_version(version_id)
    if v is None:
        raise ApiError(404, "not_found", f"unknown version: {version_id}")
    out = await _rescore(request, v, await repo.get_steps(v.run_id))
    if out is None:
        raise ApiError(409, "not_scored", "this version cannot be scored (no producing step, not an image, or "
                                          "the image could not be read)")
    return OrjsonResponse(out)


@router.post("/recompute")
async def recompute(request: Request, body: RecomputeBody) -> Response:
    repo: SqliteRepo = request.app.state.repo
    versions = await repo.versions_to_score(run_id=body.run_id, project_id=body.project_id,
                                            stale_than=QA_VERSION if body.missing_only else None, limit=body.limit)
    steps_by_run: dict[str, list[StepRow]] = {}
    updated = skipped = 0
    for v in versions:
        if v.run_id not in steps_by_run:
            steps_by_run[v.run_id] = await repo.get_steps(v.run_id)
        out = await _rescore(request, v, steps_by_run[v.run_id])
        if out is None or (out.qa or {}).get("score") is None:
            skipped += 1
        else:
            updated += 1
    return OrjsonResponse({"updated": updated, "skipped": skipped, "total": len(versions)})
