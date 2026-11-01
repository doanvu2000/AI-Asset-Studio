"""Project style lock: set the style from a finished style-lock run, list assets that drift from it, and start
the runs behind them again so the current style applies."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.api.project_qa import _open, _project
from studio_core.api.routes import META_KEY
from studio_core.api.style_bible import _real_project, load_bible
from studio_core.contracts import StepStatus
from studio_core.engine import project_qa as pq
from studio_core.engine import style_bible as sb
from studio_core.ops.style_audit import DEFAULT_STYLE, REPORT_TYPE

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}/style", dependencies=[Depends(require_token)])

LOCK_RECIPE = "style-lock"
ANCHOR_INPUTS = ("anchor", "anchor_2", "anchor_3")
DONE = (StepStatus.SUCCEEDED, StepStatus.CACHED)
MAX_RESTYLE = 50
DRIFT_GRADES = ("warn", "bad")
_MEASURE_CAP = 400
_measures: dict[str, pq.Measure] = {}


class FromRunBody(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class RestyleBody(BaseModel):
    version_ids: list[str] = Field([], max_length=MAX_RESTYLE)
    run_ids: list[str] = Field([], max_length=MAX_RESTYLE)


def _read_report(blobs: Any, h: str) -> dict[str, Any]:
    try:
        doc = json.loads(blobs.path(h).read_bytes())
    except (OSError, ValueError) as e:
        raise ApiError(409, "missing_report", f"style-lock report unreadable: {e}") from e
    if not isinstance(doc, dict) or doc.get("type") != REPORT_TYPE:
        raise ApiError(409, "missing_report", "output is not a style-lock report")
    return doc


@router.post("/from-run")
async def from_run(request: Request, project_id: str, body: FromRunBody) -> Response:
    """Locks the project style to a finished style-lock run: its anchor images become the bible's style refs."""
    await _real_project(request, project_id)
    repo = request.app.state.repo
    run = await repo.get_run(body.run_id)
    if run is None:
        raise ApiError(404, "not_found", f"unknown run: {body.run_id}")
    if run.recipe_id != LOCK_RECIPE:
        raise ApiError(422, "bad_run", f"run {run.id} is not a {LOCK_RECIPE} run")
    if run.project_id != project_id:
        raise ApiError(422, "bad_run", "the run belongs to another project")
    steps = {s.key: s for s in await repo.get_steps(run.id)}
    done = [steps[k] for k in ("approve", "finish") if k in steps and steps[k].status in DONE]
    if not done:
        raise ApiError(409, "not_finished", "the style-lock run is not finished and approved yet")
    outs = done[0].outputs
    blobs = request.app.state.blobs
    report_refs = outs.get("report") or []
    report = await asyncio.to_thread(_read_report, blobs, report_refs[0].hash) if report_refs else {}
    hashes = [h for k in ANCHOR_INPUTS if isinstance(h := run.inputs.get(k), str) and h]
    hashes = hashes or [r.hash for key in ("images", "preview") for r in outs.get(key) or []]
    refs: list[sb.BibleRef] = []
    for h in dict.fromkeys(hashes):
        try:
            known = await blobs.get(h)
        except ValueError:
            known = None
        if known is not None:
            refs.append(sb.BibleRef(hash=h))
    if not refs:
        raise ApiError(409, "no_refs", "the style-lock run has no reference image left")
    palette = [c for c in ((report.get("anchors") or {}).get("palette") or []) if isinstance(c, str)]
    text = str((report.get("options") or {}).get("style") or "").strip()
    bible = sb.lock_bible(await load_bible(repo, project_id), run.id, refs, palette,
                          "" if text == DEFAULT_STYLE else text)
    await repo.save_style_bible(project_id, bible.model_dump(mode="json"))
    return OrjsonResponse({"project_id": project_id, "bible": bible.model_dump(mode="json")})


def _measure(blobs: Any, h: str, version_id: str) -> pq.Measure:
    cached = _measures.get(h)
    if cached is not None:
        return pq.Measure(**{**cached.__dict__, "version_id": version_id})
    img = _open(blobs, h)
    if img is None:
        return pq.Measure(version_id, ok=False, note="Không đọc được ảnh")
    try:
        m = pq.measure(img, version_id)
    except Exception as e:
        log.warning("style drift: measure %s failed: %s", version_id, e)
        return pq.Measure(version_id, ok=False, note="Không phân tích được ảnh")
    if len(_measures) >= _MEASURE_CAP:
        _measures.pop(next(iter(_measures)))
    _measures[h] = m
    return m


def _evaluate(blobs: Any, versions: list[Any], palette: list[str], source: str) -> dict[str, Any]:
    return pq.evaluate(versions, [_measure(blobs, v.blob.hash, v.id) for v in versions], palette, source)


@router.get("/drift")
async def drift(request: Request, project_id: str, limit: int = Query(30, ge=1, le=pq.MAX_ASSETS)) -> Response:
    """Recent assets scored against the locked palette, worst first; CPU measures are cached by blob hash."""
    await _project(request, project_id)
    repo = request.app.state.repo
    bible = await load_bible(repo, project_id)
    lock = bible.lock if bible else None
    palette = (lock.palette if lock else None) or (bible.palette if bible else [])
    if not palette:
        raise ApiError(409, "no_style", "the project has no locked style yet")
    versions = pq.pick_versions(await repo.list_versions(project_id=project_id, limit=5000), False, limit)
    source = "Style đã khoá của dự án"
    report = await asyncio.to_thread(_evaluate, request.app.state.blobs, versions, palette, source)
    assets = [{"version_id": a["version_id"], "run_id": a["run_id"], "asset_name": a["asset_name"],
               "status": a["status"], "blob": a["blob"], "score": a["score"], "grade": a["grade"],
               "drifted": a["grade"] in DRIFT_GRADES, "note": a.get("note", ""),
               "issues": [{k: i[k] for k in ("key", "label", "severity", "message")} for i in a["issues"]]}
              for a in report["assets"]]
    return OrjsonResponse({
        "project_id": project_id, "created_at": report["created_at"], "assets": assets, "summary": report["summary"],
        "reference": {"palette": palette, "source": source, "run_id": lock.run_id if lock else None,
                      "locked_at": lock.locked_at if lock else None}})


@router.post("/restyle")
async def restyle(request: Request, project_id: str, body: RestyleBody) -> Response:
    """Starts the runs behind the given assets again with the current project style. Inputs the old style filled
    are cleared so the new one applies; prompt text the old style was appended to stays as it was."""
    await _real_project(request, project_id)
    repo = request.app.state.repo
    run_ids = list(body.run_ids)
    skipped: list[dict[str, str]] = []
    for vid in body.version_ids:
        v = await repo.get_version(vid)
        if v is None or v.project_id != project_id:
            skipped.append({"id": vid, "reason": "unknown version in this project"})
        else:
            run_ids.append(v.run_id)
    if not run_ids and not skipped:
        raise ApiError(400, "invalid", "pass version_ids or run_ids")
    started: list[dict[str, str]] = []
    for rid in dict.fromkeys(run_ids):
        run = await repo.get_run(rid)
        if run is None or run.project_id != project_id:
            skipped.append({"id": rid, "reason": "unknown run in this project"})
            continue
        if run.recipe_id == LOCK_RECIPE:
            skipped.append({"id": rid, "reason": "style-lock runs are not restyled"})
            continue
        meta = run.params.get(META_KEY) or {}
        filled = set(meta.get("project_applied") or []) | set((meta.get(sb.RECORD_KEY) or {}).get("applied") or [])
        inputs = {k: v for k, v in run.inputs.items() if k not in filled or k in sb.PROMPT_INPUTS}
        params = {META_KEY: {"source": "style_restyle", "restyled_from": run.id}}
        try:
            new_id = await request.app.state.engine.start_run(
                run.recipe_id, inputs, params, project_id=project_id, title=f"Style mới · {run.title}".strip(" ·"))
        except (LookupError, ValueError) as e:
            skipped.append({"id": rid, "reason": str(e.args[0]) if e.args else "cannot start"})
            continue
        started.append({"source_run_id": run.id, "run_id": new_id})
    return OrjsonResponse({"started": started, "skipped": skipped}, status_code=201 if started else 200)
