"""Project-wide consistency QA: run the CPU checks (+ optional Claude vision summary), keep the last report per
project, and start a one-click fix run for one asset."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from PIL import Image
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.adapters.project_qa import MAX_VISION_ASSETS, ProjectQaReviewer
from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.api.routes import META_KEY, _engine_call
from studio_core.contracts import DEFAULT_PROJECT, ProjectRow, StepError
from studio_core.engine import project_qa as pq
from studio_core.store.repo import SqliteRepo

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}/qa", dependencies=[Depends(require_token)])
_locks: dict[str, asyncio.Lock] = {}
_SAFE = re.compile(r"^[\w-]{1,80}$")


class QaBody(BaseModel):
    only_approved: bool = False
    vision: bool = False
    model: str | None = None
    limit: int = Field(pq.MAX_ASSETS, ge=1, le=pq.MAX_ASSETS)


class FixBody(BaseModel):
    version_id: str
    instructions: str | None = Field(None, max_length=4000)
    recipe_id: str | None = None


def _repo(request: Request) -> SqliteRepo:
    return request.app.state.repo


async def _project(request: Request, project_id: str) -> ProjectRow | None:
    if not _SAFE.match(project_id):
        raise ApiError(400, "invalid", f"invalid project id: {project_id}")
    p = await _repo(request).get_project(project_id)
    if p is None and project_id != DEFAULT_PROJECT:
        raise ApiError(404, "not_found", f"unknown project: {project_id}")
    return p


def _report_path(request: Request, project_id: str) -> Path:
    return request.app.state.settings.data_dir / "qa" / f"project_{project_id}.json"


def _reviewer(request: Request) -> ProjectQaReviewer:
    st = request.app.state
    if getattr(st, "project_qa_reviewer", None) is None:
        st.project_qa_reviewer = ProjectQaReviewer(st.settings.data_dir / "tmp" / "project_qa")
    return st.project_qa_reviewer


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _open(blobs: Any, h: str) -> Image.Image | None:
    try:
        with Image.open(blobs.path(h)) as im:
            im.load()
            return im.copy()
    except (OSError, ValueError):
        return None


def _analyse(blobs: Any, versions: list[Any], style: Any) -> dict[str, Any]:
    measures: list[pq.Measure] = []
    for v in versions:
        img = _open(blobs, v.blob.hash)
        if img is None:
            measures.append(pq.Measure(v.id, ok=False, note="Không đọc được ảnh"))
            continue
        try:
            measures.append(pq.measure(img, v.id))
        except Exception as e:
            log.warning("project qa: measure %s failed: %s", v.id, e)
            measures.append(pq.Measure(v.id, ok=False, note="Không phân tích được ảnh"))
    palette, source = pq.reference_palette(style, lambda h: _open(blobs, h), measures)
    return pq.evaluate(versions, measures, palette, source)


@router.get("")
async def get_report(request: Request, project_id: str) -> Response:
    await _project(request, project_id)
    report = await asyncio.to_thread(_read_json, _report_path(request, project_id))
    return OrjsonResponse(report or {"project_id": project_id, "created_at": None, "assets": [], "summary": None})


@router.post("")
async def run_report(request: Request, project_id: str, body: QaBody) -> Response:
    p = await _project(request, project_id)
    lock = _locks.setdefault(project_id, asyncio.Lock())
    if lock.locked():
        raise ApiError(409, "busy", "project QA is already running")
    async with lock:
        all_versions = await _repo(request).list_versions(project_id=project_id, limit=5000)
        versions = pq.pick_versions(all_versions, body.only_approved, body.limit)
        blobs = request.app.state.blobs
        report = await asyncio.to_thread(_analyse, blobs, versions, p.style if p else None)
        report.update({"project_id": project_id, "only_approved": body.only_approved})
        if body.vision and report["assets"]:
            shown = [a for a in report["assets"] if a["score"] is not None][:MAX_VISION_ASSETS]
            imgs = await asyncio.gather(*(asyncio.to_thread(_open, blobs, a["blob"]["hash"]) for a in shown))
            pairs = [(a, im) for a, im in zip(shown, imgs) if im is not None]
            if pairs:
                sheet = await asyncio.to_thread(pq.contact_sheet, [im for _, im in pairs])
                context = f"{p.name}. {p.description}".strip(" .") if p is not None else ""
                try:
                    vision = await _reviewer(request).review(sheet, [a for a, _ in pairs], report["reference"],
                                                             project=context, model=body.model)
                except StepError as e:
                    report["vision"] = {"error": {"code": e.code, "message": e.message}}
                else:
                    for a in report["assets"]:
                        if a["version_id"] in vision["notes"]:
                            a["vision_note"] = vision["notes"][a["version_id"]]
                    report["vision"] = {k: v for k, v in vision.items() if k != "notes"}
        await asyncio.to_thread(_write_json, _report_path(request, project_id), report)
    return OrjsonResponse(report)


@router.post("/fix")
async def fix_asset(request: Request, project_id: str, body: FixBody) -> Response:
    """Starts the fix recipe on one asset; instructions default to the report's suggestion for it."""
    await _project(request, project_id)
    v = await _repo(request).get_version(body.version_id)
    if v is None or v.project_id != project_id:
        raise ApiError(404, "not_found", f"unknown version in this project: {body.version_id}")
    if not (v.blob.mime or "").startswith("image/"):
        raise ApiError(400, "invalid", "only image assets can be fixed")
    report = await asyncio.to_thread(_read_json, _report_path(request, project_id)) or {}
    entry = next((a for a in report.get("assets") or [] if a.get("version_id") == v.id), None)
    fix = (entry or {}).get("fix") or pq.fix_inputs(v, [])
    instructions = (body.instructions or "").strip() or ((fix or {}).get("inputs") or {}).get("instructions")
    if not instructions:
        raise ApiError(400, "invalid", "no issues to fix for this asset; pass instructions")
    recipe_id = body.recipe_id or pq.FIX_RECIPE
    eng = request.app.state.engine
    try:
        recipe = eng.get_recipe(recipe_id)
    except LookupError as e:
        raise ApiError(409, "no_fix_recipe", f"fix recipe not available: {recipe_id}") from e
    side = max(v.blob.width or 512, v.blob.height or 512)
    inputs: dict[str, Any] = {"image": v.blob.hash, "instructions": instructions}
    size_spec = recipe.inputs.get("size")
    if size_spec and size_spec.get("options"):
        inputs["size"] = min(size_spec["options"], key=lambda s: abs(int(s) - side))
    inputs = {k: val for k, val in inputs.items() if k in recipe.inputs}
    title = (fix or {}).get("title") or f"Sửa QA · {v.asset_name}"
    params = {META_KEY: {"source": "project_qa", "qa_version": v.id}}
    run_id = await _engine_call(eng.start_run(recipe_id, inputs, params, project_id=project_id, title=title))
    return OrjsonResponse({"run_id": run_id, "recipe_id": recipe_id, "inputs": inputs, "title": title}, status_code=201)
