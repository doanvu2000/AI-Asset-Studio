"""Project style bible: build it from the approved assets (+ optional Claude vision text), edit its text, toggle it."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.adapters.style_bible import MAX_VISION_ASSETS, StyleBibleWriter
from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.api.project_qa import _open, _project
from studio_core.contracts import StepError
from studio_core.engine import project_qa as pq
from studio_core.engine import style_bible as sb
from studio_core.store.repo import SqliteRepo

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}/style", dependencies=[Depends(require_token)])
_locks: dict[str, asyncio.Lock] = {}


class BuildBody(BaseModel):
    vision: bool = True
    model: str | None = None


class EditBody(BaseModel):
    suffix: str | None = Field(None, max_length=2000)
    negative: str | None = Field(None, max_length=2000)
    active: bool | None = None
    refs: list[str] | None = Field(None, max_length=sb.MAX_REFS)


def _repo(request: Request) -> SqliteRepo:
    return request.app.state.repo


def _writer(request: Request) -> StyleBibleWriter:
    st = request.app.state
    if getattr(st, "style_bible_writer", None) is None:
        st.style_bible_writer = StyleBibleWriter(st.settings.data_dir / "tmp" / "style_bible")
    return st.style_bible_writer


async def load_bible(repo: SqliteRepo, project_id: str) -> sb.StyleBible | None:
    raw = await repo.get_style_bible(project_id)
    return sb.StyleBible.model_validate(raw) if raw else None


async def _real_project(request: Request, project_id: str) -> Any:
    p = await _project(request, project_id)
    if p is None:
        raise ApiError(400, "invalid", "the default project has no style bible; runs there are never styled")
    return p


def _out(project_id: str, bible: sb.StyleBible | None) -> Response:
    return OrjsonResponse({"project_id": project_id, "bible": bible.model_dump(mode="json") if bible else None})


@router.get("")
async def get_bible(request: Request, project_id: str) -> Response:
    await _project(request, project_id)
    return _out(project_id, await load_bible(_repo(request), project_id))


@router.post("/build")
async def build_bible(request: Request, project_id: str, body: BuildBody) -> Response:
    p = await _real_project(request, project_id)
    lock = _locks.setdefault(project_id, asyncio.Lock())
    if lock.locked():
        raise ApiError(409, "busy", "style bible is already being built")
    async with lock:
        repo = _repo(request)
        versions = pq.pick_versions(await repo.list_versions(project_id=project_id, limit=5000), only_approved=True,
                                    limit=sb.MAX_SOURCES)
        if not versions:
            raise ApiError(400, "no_approved", "the project has no approved image assets yet")
        blobs = request.app.state.blobs
        imgs = await asyncio.gather(*(asyncio.to_thread(_open, blobs, v.blob.hash) for v in versions))
        measures, pix = await asyncio.to_thread(sb.measure_all, [(v.id, im) for v, im in zip(versions, imgs)])
        stats = sb.aggregate(measures, pix)
        by_id = {v.id: v for v in versions}
        ref_ids = sb.pick_refs(measures, stats["palette"], {v.id: v.run_id for v in versions})
        refs = [sb.BibleRef(hash=by_id[i].blob.hash, version_id=i, asset_name=by_id[i].asset_name) for i in ref_ids]
        suffix, negative = sb.fallback_text(stats)
        summary = ""
        extra: dict[str, Any] = {}
        if body.vision:
            shown = [(v, im) for v, im, m in zip(versions, imgs, measures) if im is not None and m.ok]
            shown = shown[:MAX_VISION_ASSETS]
            if shown:
                sheet = await asyncio.to_thread(pq.contact_sheet, [im for _, im in shown])
                context = f"{p.name}. {p.description}".strip(" .")
                try:
                    res = await _writer(request).write(sheet, stats, len(shown), project=context, model=body.model)
                except StepError as e:
                    extra["vision_error"] = f"{e.code}: {e.message}"
                else:
                    suffix = res["suffix"] or suffix
                    negative = res["negative"] or negative
                    summary = res["summary"]
                    if res["pixel_art"] is not None:
                        stats["pixel_art"] = res["pixel_art"]
                    extra.update(cost_usd=res["cost_usd"], model=res["model"])
        bible = sb.new_bible(await load_bible(repo, project_id), stats, refs, suffix, negative, summary,
                             len(versions), **extra)
        await repo.save_style_bible(project_id, bible.model_dump(mode="json"))
    return _out(project_id, bible)


@router.post("")
async def edit_bible(request: Request, project_id: str, body: EditBody) -> Response:
    """Text edits and ref changes bump the version (runs record which one they got); toggling active does not."""
    await _real_project(request, project_id)
    repo = _repo(request)
    bible = await load_bible(repo, project_id)
    if bible is None:
        raise ApiError(404, "not_found", "no style bible yet; build it first")
    upd: dict[str, Any] = {}
    if body.suffix is not None and body.suffix.strip() != bible.suffix:
        upd["suffix"] = body.suffix.strip()
    if body.negative is not None and body.negative.strip() != bible.negative:
        upd["negative"] = body.negative.strip()
    if body.refs is not None:
        refs: list[sb.BibleRef] = []
        for h in dict.fromkeys(body.refs):
            known = next((r for r in bible.refs if r.hash == h), None)
            try:
                found = known or await request.app.state.blobs.get(h)
            except ValueError:
                found = None
            if found is None:
                raise ApiError(400, "invalid", f"unknown blob {h}")
            refs.append(known or sb.BibleRef(hash=h))
        if [r.hash for r in refs] != [r.hash for r in bible.refs]:
            upd["refs"] = refs
    if upd:
        upd.update(version=bible.version + 1, edited=True)
    if body.active is not None and body.active != bible.active:
        upd["active"] = body.active
    if upd:
        bible = bible.model_copy(update={**upd, "updated_at": time.time()})
        await repo.save_style_bible(project_id, bible.model_dump(mode="json"))
    return _out(project_id, bible)
