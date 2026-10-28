"""Find similar assets (image / uploaded file / text), near-duplicates inside a project, and the "make more like
this" plan. Backed by engine.image_index; works on pHash alone when the CLIP model is not installed."""
from __future__ import annotations

import asyncio
import io
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from PIL import Image
from pydantic import BaseModel
from starlette.responses import Response

from studio_core.adapters.embedder import ClipEmbedder, install_hint, missing, model_dir
from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.api.project_qa import _project
from studio_core.contracts import VersionRow, VersionStatus
from studio_core.engine import similar as sm
from studio_core.engine.image_index import ImageIndex

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
LIBRARY_LIMIT = 20000
MAX_UPLOAD = 40 << 20


async def start_index(app: Any) -> ImageIndex:
    st = app.state
    embedder = getattr(st, "similar_embedder", None) or ClipEmbedder(model_dir(st.settings))
    index = ImageIndex(st.db, st.repo, st.blobs, embedder)
    await index.open()
    index.start(getattr(st, "bus", None))
    st.image_index = index
    return index


async def _index(request: Request) -> ImageIndex:
    st = request.app.state
    index = getattr(st, "image_index", None)
    return index if index is not None else await start_index(request.app)


async def _versions(request: Request, project_id: str | None) -> list[VersionRow]:
    return await request.app.state.repo.list_versions(project_id=project_id, limit=LIBRARY_LIMIT)


def _model_error(request: Request) -> ApiError:
    root = model_dir(request.app.state.settings)
    return ApiError(409, "similar_model_missing", f"thiếu {', '.join(missing(root)) or 'model'}. " + install_hint(root),
                    detail={"missing": missing(root), "model_dir": str(root)})


def _open_image(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            return im.copy()
    except (OSError, ValueError) as e:
        raise ApiError(400, "invalid", "not a readable image") from e


async def _results(request: Request, index: ImageIndex, query: sm.Entry, k: int, project_id: str | None,
                   only_approved: bool, exclude_hash: str | None, extra: dict[str, Any]) -> Response:
    versions = await _versions(request, project_id)
    if only_approved:
        versions = [v for v in versions if v.status == VersionStatus.APPROVED]
    pending = await index.ensure([v.blob.hash for v in sm.library_versions(versions)])
    mode, hits = index.search(query, versions, k, lambda v: v.blob.hash == exclude_hash)
    return OrjsonResponse({**extra, "mode": mode, "model": index.model_id, "pending": pending, "results": hits})


@router.get("/search/status")
async def status(request: Request) -> Response:
    index = await _index(request)
    out = index.status()
    if out["model"] is None:
        out["hint"] = install_hint(model_dir(request.app.state.settings))
    return OrjsonResponse(out)


class ReindexBody(BaseModel):
    project_id: str | None = None
    force: bool = False


@router.post("/search/reindex")
async def reindex(request: Request, body: ReindexBody) -> Response:
    index = await _index(request)
    queued = await index.reindex(await _versions(request, body.project_id), force=body.force)
    return OrjsonResponse({"queued": queued, **index.status()})


@router.get("/search/similar")
async def similar_to_blob(request: Request, blob: str | None = None, version_id: str | None = None,
                          k: int = Query(12, ge=1, le=100), project_id: str | None = None,
                          only_approved: bool = False) -> Response:
    repo = request.app.state.repo
    if version_id:
        v = await repo.get_version(version_id)
        if v is None:
            raise ApiError(404, "not_found", f"unknown version: {version_id}")
        blob = v.blob.hash
    if not blob:
        raise ApiError(400, "invalid", "pass blob or version_id")
    try:
        path = request.app.state.blobs.path(blob)
    except ValueError as e:
        raise ApiError(400, "invalid_hash", str(e)) from e
    if not await asyncio.to_thread(path.is_file):
        raise ApiError(404, "not_found", f"unknown blob: {blob}")
    index = await _index(request)
    await index.ensure([blob], budget_s=60)
    query = index.entries.get(blob)
    if query is None or query.phash is None:
        raise ApiError(400, "invalid", "this blob is not a readable image")
    return await _results(request, index, query, k, project_id, only_approved, blob, {"query": {"blob": blob}})


@router.post("/search/similar")
async def similar_to_upload(request: Request, file: UploadFile | None = File(None), text: str | None = Form(None),
                            k: int = Form(12), project_id: str | None = Form(None),
                            only_approved: bool = Form(False)) -> Response:
    k = max(1, min(100, k))
    index = await _index(request)
    if file is not None:
        data = await file.read(MAX_UPLOAD + 1)
        await file.close()
        if len(data) > MAX_UPLOAD:
            raise ApiError(413, "too_large", "image over 40 MB")
        img = _open_image(data)
        query = await asyncio.to_thread(index.query_entry, img)
        info: dict[str, Any] = {"file": file.filename}
    elif text and text.strip():
        if index.model_id is None or index.embedder is None:
            raise _model_error(request)
        vec = await asyncio.to_thread(index.embedder.embed_text, text.strip()[:500])
        query = sm.Entry(vec=vec)
        info = {"text": text.strip()}
    else:
        raise ApiError(400, "invalid", "pass an image file or a text query")
    return await _results(request, index, query, k, project_id, only_approved, None, {"query": info})


@router.get("/search/more-like")
async def more_like(request: Request, version_id: str | None = None, blob: str | None = None) -> Response:
    repo = request.app.state.repo
    v = await repo.get_version(version_id) if version_id else None
    if version_id and v is None:
        raise ApiError(404, "not_found", f"unknown version: {version_id}")
    if v is None and blob:
        v = next((x for x in await repo.list_versions(limit=LIBRARY_LIMIT) if x.blob.hash == blob), None)
    h = v.blob.hash if v else blob
    if not h:
        raise ApiError(400, "invalid", "pass version_id or blob")
    run = await repo.get_run(v.run_id) if v else None
    recipes = {r.id: {"name": r.name, "inputs": r.inputs} for r in request.app.state.engine.list_recipes()}
    plan = sm.more_like(h, run, recipes)
    if plan is None:
        raise ApiError(409, "no_recipe", "no recipe can take this asset as a style reference")
    plan["title"] = f"Giống · {v.asset_name}" if v else "Giống ảnh mẫu"
    plan["blob"] = h
    return OrjsonResponse(plan)


@router.get("/projects/{project_id}/duplicates")
async def duplicates(request: Request, project_id: str, threshold: float = Query(sm.DEFAULT_THRESHOLD, ge=0.5, le=1.0),
                     only_approved: bool = False) -> Response:
    await _project(request, project_id)
    index = await _index(request)
    versions = await _versions(request, project_id)
    if only_approved:
        versions = [v for v in versions if v.status == VersionStatus.APPROVED]
    lib = sm.library_versions(versions)
    pending = await index.ensure([v.blob.hash for v in lib], budget_s=30)
    groups = index.duplicates(versions, threshold)
    return OrjsonResponse({"project_id": project_id, "mode": "clip" if index.model_id else "phash",
                           "model": index.model_id, "threshold": threshold, "checked": len(lib), "pending": pending,
                           "groups": groups})
