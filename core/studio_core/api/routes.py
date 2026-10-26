from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, BinaryIO, Literal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
import yaml
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.responses import Response

from studio_core import __version__
from studio_core.api.auth import require_token, require_token_or_query
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.adapters.translate import MAX_TEXTS
from studio_core.engine.kinds import describe_kinds
from studio_core.engine.library import RecipeLibrary, issue, merge_text, to_text, validate
from studio_core.engine.recipe import Recipe, RecipeError
from studio_core.ops.presets import ExportPreset, PresetStore
from studio_core.store.bundle import ENGINES, BundleError, engine_ready, export_bundle, export_engine, sheet_meta
from studio_core.store.export import export_copy
from studio_core.contracts import (
    BlobStore,
    Engine,
    GateResolution,
    Repo,
    Resource,
    RunStatus,
    StepError,
    StepStatus,
    VersionStatus,
)

public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
blob_api = APIRouter(prefix="/api", dependencies=[Depends(require_token_or_query)])

_IMMUTABLE = "public, max-age=31536000, immutable"
META_KEY = "$meta"
_GENERIC_MIMES = {None, "", "application/octet-stream"}


class TranslateBody(BaseModel):
    texts: list[str]


class RunCreate(BaseModel):
    recipe_id: str
    inputs: dict[str, Any] = {}
    params: dict[str, Any] | None = None
    title: str = ""
    project_id: str = "default"


def _json(content: Any, status: int = 200) -> OrjsonResponse:
    return OrjsonResponse(content, status_code=status)


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _repo(request: Request) -> Repo:
    return request.app.state.repo


def _blobs(request: Request) -> BlobStore:
    return request.app.state.blobs


def _resource(request: Request, name: str) -> Resource:
    res: dict[str, Resource] = request.app.state.resources
    if name not in res:
        raise ApiError(404, "not_found", f"unknown resource: {name}")
    return res[name]


async def _engine_call[T](aw: Awaitable[T]) -> T:
    try:
        return await aw
    except LookupError as e:
        raise ApiError(404, "not_found", str(e.args[0]) if e.args else "not found") from e
    except ValueError as e:
        raise ApiError(400, "invalid", str(e)) from e


@public.get("/health")
async def health() -> Response:
    return _json({"ok": True, "version": __version__})


@api.post("/shutdown")
async def shutdown(request: Request) -> Response:
    request_shutdown = request.app.state.request_shutdown
    asyncio.get_running_loop().call_later(0.1, request_shutdown)
    return _json({"ok": True})


@api.get("/resources")
async def list_resources(request: Request) -> Response:
    res: dict[str, Resource] = request.app.state.resources
    return _json(list(await asyncio.gather(*(r.health() for r in res.values()))))


@api.post("/resources/{name}/start")
async def start_resource(request: Request, name: str) -> Response:
    r = _resource(request, name)
    await r.ensure_started()
    return _json(await r.health())


@api.post("/resources/{name}/stop")
async def stop_resource(request: Request, name: str, force: bool = False) -> Response:
    r = _resource(request, name)
    if not force and (await r.health()).status == "busy":
        raise ApiError(409, "resource_busy", f"{name} is running a job; cancel the run first or stop with force=true")
    await r.stop()
    return _json(await r.health())


@api.post("/translate")
async def translate(request: Request, body: TranslateBody) -> Response:
    if len(body.texts) > MAX_TEXTS:
        raise ApiError(400, "invalid", f"at most {MAX_TEXTS} texts per call")
    try:
        texts, meta = await request.app.state.translator.to_english(body.texts)
    except StepError as e:
        raise ApiError(504 if e.code == "claude_timeout" else 502, e.code, e.message) from e
    return _json({"texts": texts, **meta})


@api.get("/recipes")
async def list_recipes(request: Request) -> Response:
    return _json(_engine(request).list_recipes())


@api.get("/kinds")
async def list_kinds(request: Request) -> Response:
    settings = request.app.state.settings
    return _json(await asyncio.to_thread(describe_kinds, request.app.state.registry,
                                         getattr(settings, "comfy_workflows_dir", None)))


class RecipeBody(BaseModel):
    yaml: str | None = None
    doc: dict[str, Any] | None = None

    def raw(self) -> str | dict[str, Any]:
        if self.yaml is not None:
            return self.yaml
        if self.doc is not None:
            return self.doc
        raise ApiError(400, "invalid", "send 'yaml' or 'doc'")


def _library(request: Request) -> RecipeLibrary:
    lib = getattr(_engine(request), "library", None)
    if lib is None:
        raise ApiError(503, "engine_unavailable", "recipe library not available")
    return lib


def _validate(request: Request, raw: str | dict[str, Any]) -> tuple[Recipe | None, list[dict[str, Any]]]:
    return validate(raw, request.app.state.registry, getattr(request.app.state.settings, "comfy_workflows_dir", None))


def _recipe_view(request: Request, lib: RecipeLibrary, r: Recipe) -> dict[str, Any]:
    return {"info": _engine(request).recipe_info(r), "source": lib.sources.get(r.id, "builtin"),
            "has_builtin": lib.has_builtin(r.id)}


@api.get("/recipes/{rid}")
async def get_recipe(request: Request, rid: str) -> Response:
    lib = _library(request)
    r = await asyncio.to_thread(lib.get, rid)
    if r is None:
        raise ApiError(404, "not_found", f"unknown recipe: {rid}")
    text = await asyncio.to_thread(lib.text, rid) or ""
    return _json({**_recipe_view(request, lib, r), "yaml": text, "doc": yaml.safe_load(text) or {}})


@api.post("/recipes/validate")
async def validate_recipe(request: Request, body: RecipeBody) -> Response:
    recipe, issues = await asyncio.to_thread(_validate, request, body.raw())
    ok = recipe is not None and not any(i["level"] == "error" for i in issues)
    return _json({"ok": ok, "issues": issues,
                  "info": _engine(request).recipe_info(recipe) if recipe is not None else None})


@api.put("/recipes/{rid}")
async def save_recipe(request: Request, rid: str, body: RecipeBody) -> Response:
    lib = _library(request)
    raw = body.raw()
    if isinstance(raw, dict):
        raw = {**raw, "id": raw.get("id") or rid}
    recipe, issues = await asyncio.to_thread(_validate, request, raw)
    errors = [i for i in issues if i["level"] == "error"]
    if recipe is not None and recipe.id != rid:
        errors.append(issue(f"recipe id '{recipe.id}' does not match '{rid}'", field="id"))
    if recipe is None or errors:
        return OrjsonResponse({"error": {"code": "invalid_recipe", "message": (errors or issues)[0]["message"],
                                         "issues": errors or issues}}, status_code=422)
    try:
        prev = lib.text(rid) if isinstance(raw, dict) else None
        text = merge_text(prev, raw) if prev is not None and isinstance(raw, dict) else to_text(raw)
        saved = await asyncio.to_thread(lib.save, rid, text)
    except RecipeError as e:
        raise ApiError(422, "invalid_recipe", str(e)) from e
    return _json({**_recipe_view(request, lib, saved), "issues": issues})


@api.delete("/recipes/{rid}")
async def delete_recipe(request: Request, rid: str) -> Response:
    lib = _library(request)
    try:
        restored = await asyncio.to_thread(lib.delete, rid)
    except LookupError as e:
        raise ApiError(404, "not_found", str(e)) from e
    except RecipeError as e:
        raise ApiError(400, "invalid", str(e)) from e
    return _json({"ok": True, "restored_builtin": restored})


class BatchItem(BaseModel):
    inputs: dict[str, Any] = {}
    title: str = ""


class BatchCreate(BaseModel):
    recipe_id: str
    items: list[BatchItem]
    inputs: dict[str, Any] = {}
    params: dict[str, Any] | None = None
    title: str = ""
    project_id: str = "default"


MAX_BATCH = 200


@api.post("/batches")
async def create_batch(request: Request, body: BatchCreate) -> Response:
    """N runs of one recipe tagged with `$meta.batch_*`; every item is checked before the first run starts."""
    if not body.items:
        raise ApiError(400, "invalid", "batch needs at least one item")
    if len(body.items) > MAX_BATCH:
        raise ApiError(400, "invalid", f"batch limited to {MAX_BATCH} items")
    eng = _engine(request)
    merged = [{**body.inputs, **it.inputs} for it in body.items]
    get_recipe_fn = getattr(eng, "get_recipe", None)
    if get_recipe_fn is not None:
        try:
            recipe = get_recipe_fn(body.recipe_id)
            check = getattr(eng, "check_inputs", None)
            for i, values in enumerate(merged):
                try:
                    if check is not None:
                        await check(recipe, values)
                    else:
                        recipe.coerce_inputs(values)
                except ValueError as e:
                    raise ApiError(400, "invalid", f"item {i + 1}: {e}") from e
        except LookupError as e:
            raise ApiError(404, "not_found", str(e)) from e
    batch_id = uuid.uuid4().hex
    params = dict(body.params or {})
    base_meta = dict(params.get(META_KEY) or {})
    run_ids: list[str] = []
    for i, (it, values) in enumerate(zip(body.items, merged)):
        meta = {"source": "app", **base_meta, "batch_id": batch_id, "batch_index": i, "batch_total": len(body.items),
                "batch_title": body.title}
        run_ids.append(await _engine_call(eng.start_run(body.recipe_id, values, {**params, META_KEY: meta},
                                                        project_id=body.project_id, title=it.title)))
    return _json({"batch_id": batch_id, "run_ids": run_ids}, 201)


def _presets(request: Request) -> PresetStore:
    return PresetStore(getattr(request.app.state.settings, "presets_path", None))


@api.get("/presets")
async def list_presets(request: Request) -> Response:
    return _json(await asyncio.to_thread(_presets(request).list))


@api.put("/presets/{pid}")
async def save_preset(request: Request, pid: str, body: dict[str, Any]) -> Response:
    try:
        preset = ExportPreset.model_validate({**body, "id": pid, "builtin": False})
        saved = await asyncio.to_thread(_presets(request).save, preset)
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise ApiError(422, "invalid", msg) from e
    except ValueError as e:
        raise ApiError(409, "conflict", str(e)) from e
    return _json(saved)


@api.delete("/presets/{pid}")
async def delete_preset(request: Request, pid: str) -> Response:
    try:
        await asyncio.to_thread(_presets(request).delete, pid)
    except LookupError as e:
        raise ApiError(404, "not_found", str(e)) from e
    return _json({"ok": True})


@api.post("/runs")
async def create_run(request: Request, body: RunCreate) -> Response:
    run_id = await _engine_call(_engine(request).start_run(
        body.recipe_id, body.inputs, body.params, project_id=body.project_id, title=body.title))
    return _json({"run_id": run_id}, 201)


@api.get("/runs")
async def list_runs(request: Request, limit: int = Query(50, ge=1, le=2000), before: float | None = None,
                    status: RunStatus | None = None, batch_id: str | None = None,
                    batched: bool = False, project_id: str | None = None) -> Response:
    repo = _repo(request)
    runs = await repo.list_runs(limit=limit, before=before, status=status, batch_id=batch_id, batched=batched,
                                project_id=project_id)
    previews = await repo.run_preview_hashes([r.id for r in runs])
    return _json([{**r.model_dump(mode="json"), "preview_hash": previews.get(r.id)} for r in runs])


@api.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> Response:
    repo = _repo(request)
    run = await repo.get_run(run_id)
    if run is None:
        raise ApiError(404, "not_found", f"unknown run: {run_id}")
    return _json({"run": run, "steps": await repo.get_steps(run_id)})


@api.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str) -> Response:
    await _engine_call(_engine(request).cancel_run(run_id))
    return _json({"ok": True})


@api.post("/steps/{step_id}/retry")
async def retry_step(request: Request, step_id: str) -> Response:
    await _engine_call(_engine(request).retry_step(step_id))
    return _json({"ok": True})


@api.post("/gates/{step_id}")
async def resolve_gate(request: Request, step_id: str, body: GateResolution) -> Response:
    await _engine_call(_engine(request).resolve_gate(step_id, body))
    return _json({"ok": True})


@api.get("/gates")
async def list_gates(request: Request) -> Response:
    return _json(await _repo(request).list_steps_by_status([StepStatus.AWAITING_HUMAN]))


@api.get("/versions")
async def list_versions(request: Request, run_id: str | None = None,
                        status: VersionStatus | None = None, project_id: str | None = None) -> Response:
    return _json(await _repo(request).list_versions(run_id=run_id, status=status, project_id=project_id))


async def _set_version(request: Request, version_id: str, status: VersionStatus) -> Response:
    v = await _repo(request).set_version_status(version_id, status)
    if v is None:
        raise ApiError(404, "not_found", f"unknown version: {version_id}")
    request.app.state.bus.publish("version.updated", v.model_dump(mode="json"), run_id=v.run_id)
    return _json(v)


@api.post("/versions/{version_id}/approve")
async def approve_version(request: Request, version_id: str) -> Response:
    return await _set_version(request, version_id, VersionStatus.APPROVED)


@api.post("/versions/{version_id}/reject")
async def reject_version(request: Request, version_id: str) -> Response:
    return await _set_version(request, version_id, VersionStatus.REJECTED)


def _spool_to_disk(src: BinaryIO, tmp_dir: Path) -> Path:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dst = tmp_dir / f"upload-{uuid.uuid4().hex}"
    src.seek(0)
    with dst.open("wb") as out:
        shutil.copyfileobj(src, out, 1 << 20)
    return dst


@api.post("/blobs")
async def upload_blob(request: Request, file: UploadFile = File(...)) -> Response:
    tmp_root = Path(getattr(request.app.state.settings, "data_dir", tempfile.gettempdir())) / "tmp"
    tmp = await asyncio.to_thread(_spool_to_disk, file.file, tmp_root)
    try:
        mime = None if file.content_type in _GENERIC_MIMES else file.content_type
        ref = await _blobs(request).put_file(tmp, name=file.filename or None, mime=mime)
    finally:
        await asyncio.to_thread(tmp.unlink, True)
        await file.close()
    return _json(ref, 201)


def _blob_path(blobs: BlobStore, hash: str) -> Path:
    try:
        return blobs.path(hash)
    except ValueError as e:
        raise ApiError(400, "invalid_hash", str(e)) from e


@blob_api.get("/blobs/{hash}")
async def get_blob(request: Request, hash: str) -> Response:
    blobs = _blobs(request)
    path = _blob_path(blobs, hash)
    ref = await blobs.get(hash)
    if ref is None or not await asyncio.to_thread(path.is_file):
        raise ApiError(404, "not_found", f"unknown blob: {hash}")
    return FileResponse(path, media_type=ref.mime, headers={"Cache-Control": _IMMUTABLE})


class BlobExport(BaseModel):
    name: str | None = None


@api.post("/blobs/{hash}/export")
async def export_blob(request: Request, hash: str, body: BlobExport) -> Response:
    """Absolute path of a readable-name copy under <data>/exports/app; the app reveals, copies or drags that file."""
    blobs = _blobs(request)
    path = _blob_path(blobs, hash)
    ref = await blobs.get(hash)
    if ref is None or not await asyncio.to_thread(path.is_file):
        raise ApiError(404, "not_found", f"unknown blob: {hash}")
    settings = request.app.state.settings
    root = getattr(settings, "app_exports_dir", None) or Path(settings.data_dir) / "exports" / "app"
    dst = await asyncio.to_thread(export_copy, path, ref, Path(root), body.name)
    return _json({"hash": hash, "path": str(dst.resolve()), "name": dst.name, "mime": ref.mime, "size": ref.size})


class StepExport(BaseModel):
    name: str | None = None
    preset: str | None = None
    engine: Literal["unity", "godot"] | None = None
    ppu: float = Field(100.0, gt=0, le=10000)
    pixel: bool | None = None


async def _exportable_step(request: Request, step_id: str) -> Any:
    step = await _repo(request).get_step(step_id)
    if step is None:
        raise ApiError(404, "not_found", f"unknown step: {step_id}")
    if step.status not in (StepStatus.SUCCEEDED, StepStatus.CACHED) or not step.outputs:
        raise ApiError(409, "not_exportable", "step has no finished outputs")
    return step


@api.get("/steps/{step_id}/export")
async def step_export_options(request: Request, step_id: str) -> Response:
    """What a folder export of this step can do: file count and whether engine presets apply."""
    step = await _exportable_step(request, step_id)
    meta = await asyncio.to_thread(sheet_meta, step, _blobs(request))
    files = len({r.hash for refs in step.outputs.values() for r in refs})
    engines = list(ENGINES) if await asyncio.to_thread(engine_ready, step, _blobs(request)) else []
    return _json({"step_id": step.id, "kind": step.kind, "files": files, "sprite_sheet": meta is not None,
                  "preset": (meta or {}).get("preset"), "engines": engines})


@api.post("/steps/{step_id}/export")
async def export_step(request: Request, step_id: str, body: StepExport) -> Response:
    """Writes every output of the step (or a preset-repacked sprite sheet + frames) into one folder under exports/app."""
    step = await _exportable_step(request, step_id)
    settings = request.app.state.settings
    root = getattr(settings, "app_exports_dir", None) or Path(settings.data_dir) / "exports" / "app"
    if body.engine:
        if body.preset:
            raise ApiError(400, "invalid", "pick either an engine or a preset, not both")
        # Siblings let a sheet pick up the normal-map sheet rendered by another step of the same run.
        siblings = await _repo(request).get_steps(step.run_id)
        try:
            out = await asyncio.to_thread(export_engine, step, siblings, _blobs(request), Path(root), body.name,
                                          body.engine, body.ppu, body.pixel)
        except BundleError as e:
            raise ApiError(422, "not_exportable", str(e)) from e
        return _json(out)
    preset = None
    if body.preset:
        preset = await asyncio.to_thread(_presets(request).get, body.preset)
        if preset is None:
            raise ApiError(404, "not_found", f"unknown export preset: {body.preset}")
    try:
        out = await asyncio.to_thread(export_bundle, step, _blobs(request), Path(root), body.name, preset)
    except BundleError as e:
        raise ApiError(422, "not_exportable", str(e)) from e
    return _json(out)


@blob_api.get("/blobs/{hash}/thumb")
async def get_thumb(request: Request, hash: str, size: int = Query(256, ge=16, le=2048)) -> Response:
    blobs = _blobs(request)
    _blob_path(blobs, hash)
    if await blobs.get(hash) is None:
        raise ApiError(404, "not_found", f"unknown blob: {hash}")
    try:
        thumb = await blobs.thumbnail(hash, size)
    except FileNotFoundError as e:
        raise ApiError(404, "not_found", f"blob file missing: {hash}") from e
    except ImportError as e:
        raise ApiError(503, "thumbnail_unavailable", str(e)) from e
    except Exception as e:
        raise ApiError(422, "thumbnail_failed", f"{type(e).__name__}: {e}") from e
    return FileResponse(thumb, media_type="image/webp", headers={"Cache-Control": _IMMUTABLE})
