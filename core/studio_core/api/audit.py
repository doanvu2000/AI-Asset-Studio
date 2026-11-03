"""Game project audit: read-only scan of a Unity / Godot / Flutter folder, one-click importer fixes (backup zip + undo) and an
optional atlas written outside the project. The last report per project is kept so a fix can be checked against it."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

from blake3 import blake3
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.export import audit as au
from studio_core.scan_jobs import ScanCancelled, jobs_of

router = APIRouter(prefix="/api/audit", dependencies=[Depends(require_token)])
_lock = asyncio.Lock()


class ScanBody(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    engine: Literal["auto", "unity", "godot", "flutter"] = "auto"
    tri_budget: int = Field(au.DEFAULT_TRI_BUDGET, ge=100, le=10_000_000)
    duplicates: bool = True


class FixAction(BaseModel):
    type: Literal["mobile_preset", "max_size", "drop_alpha", "atlas", "flutter_declare", "flutter_variant",
                  "flutter_webp"]
    rel: str | None = Field(None, max_length=1024)
    rels: list[str] | None = Field(None, max_length=2000)
    kind: str | None = Field(None, max_length=32)
    max_size: int | None = Field(None, ge=16, le=16384)
    name: str | None = Field(None, max_length=80)


class FixBody(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    actions: list[FixAction] = Field(min_length=1, max_length=5000)
    dry_run: bool = True


class UndoBody(BaseModel):
    backup_id: str = Field(min_length=1, max_length=64)
    force: bool = False


def _data(request: Request) -> Path:
    return Path(request.app.state.settings.data_dir)


def _report_path(request: Request, project: str) -> Path:
    key = blake3(str(Path(project)).lower().encode()).hexdigest()[:16]
    return _data(request) / "audit" / f"report_{key}.json"


def _save(p: Path, doc: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _project_of(path: str) -> str:
    folder = Path(path).expanduser()
    if not folder.is_absolute() or not folder.is_dir():
        raise ApiError(422, "bad_folder", "path must be an existing absolute folder")
    engine, proj = au.detect_engine(folder)
    if engine == "none":
        raise ApiError(422, "bad_folder", "not inside a Unity, Godot or Flutter project")
    return str(proj)


def _embedder(request: Request) -> Any:
    st = request.app.state
    index = getattr(st, "image_index", None)
    if index is not None and index.embedder is not None:
        return index.embedder
    emb = getattr(st, "similar_embedder", None)
    if emb is None:
        from studio_core.adapters.embedder import ClipEmbedder, model_dir

        emb = st.similar_embedder = ClipEmbedder(model_dir(st.settings))
    return emb


async def _scan(request: Request, body: ScanBody) -> dict[str, Any]:
    embedder = _embedder(request) if body.duplicates else None
    try:
        with jobs_of(request).open("audit", body.path) as job:
            doc = await asyncio.to_thread(au.scan, body.path, body.engine, body.tri_budget, body.duplicates,
                                          au.MAX_FILES, embedder, job)
    except au.AuditError as e:
        raise ApiError(422, "bad_folder", str(e)) from e
    except ScanCancelled as e:
        raise ApiError(409, "cancelled", "Đã huỷ quét") from e
    doc["options"] = {"engine": body.engine, "tri_budget": body.tri_budget, "duplicates": body.duplicates}
    await asyncio.to_thread(_save, _report_path(request, doc["project"]), doc)
    return doc


@router.post("")
async def scan(request: Request, body: ScanBody) -> Response:
    """Read-only: textures vs. mobile budget, duplicates, unreferenced assets, heavy meshes, audio settings."""
    return OrjsonResponse(await _scan(request, body))


@router.get("/last")
async def last(request: Request, path: str) -> Response:
    p = _report_path(request, _project_of(path))
    if not p.is_file():
        raise ApiError(404, "not_found", "no audit for this project yet")
    return OrjsonResponse(json.loads(await asyncio.to_thread(p.read_text, encoding="utf-8")))


@router.post("/fix")
async def fix(request: Request, body: FixBody) -> Response:
    """Importer-only fixes from the last audit (dry_run plans them); `atlas` packs sprites into a folder outside the
    project. After a real fix the project is audited again so the next fix sees the new importer hashes."""
    project = _project_of(body.path)
    rp = _report_path(request, project)
    if not rp.is_file():
        raise ApiError(409, "no_audit", "audit the project first")
    report = json.loads(await asyncio.to_thread(rp.read_text, encoding="utf-8"))
    acts = [a.model_dump(exclude_none=True) for a in body.actions]
    importer = [a for a in acts if a["type"] != "atlas"]
    atlases = [a for a in acts if a["type"] == "atlas"]
    out: dict[str, Any] = {}
    async with _lock:
        try:
            if importer:
                with jobs_of(request).open("audit_fix", body.path) as job:
                    out = await asyncio.to_thread(au.apply_fixes, report, importer, au.backup_dir(_data(request)),
                                                  body.dry_run, {"path": body.path}, job)
            if atlases and not out.get("cancelled"):
                root = Path(getattr(request.app.state.settings, "app_exports_dir", _data(request) / "exports" / "app"))
                out["atlases"] = []
                for a in atlases:
                    if body.dry_run:
                        out["atlases"].append({"rels": a.get("rels") or [], "planned": True})
                        continue
                    out["atlases"].append(await asyncio.to_thread(
                        au.build_atlas, report, a.get("rels") or [], a.get("name") or "audit_atlas",
                        root / "audit_atlas", a.get("max_size") or 2048))
        except au.AuditError as e:
            raise ApiError(409, "fix_failed", str(e)) from e
        if out.get("cancelled"):
            # No re-scan here: it would delay the cancel by the whole scan; the UI re-audits when it wants fresh hashes.
            out["rescan_needed"] = bool(out.get("written"))
            return OrjsonResponse(out)
        if not body.dry_run and out.get("written"):
            opts = report.get("options") or {}
            out["report"] = await _scan(request, ScanBody(path=report["root"], engine=opts.get("engine", "auto"),
                                                          tri_budget=opts.get("tri_budget", au.DEFAULT_TRI_BUDGET),
                                                          duplicates=opts.get("duplicates", True)))
    return OrjsonResponse(out)


@router.post("/undo")
async def undo(request: Request, body: UndoBody) -> Response:
    async with _lock:
        try:
            out = await asyncio.to_thread(au.undo, au.backup_dir(_data(request)), body.backup_id, body.force)
        except au.AuditError as e:
            raise ApiError(409, "undo_failed", str(e)) from e
    return OrjsonResponse(out)


@router.get("/backups")
async def backups(request: Request, path: str | None = None) -> Response:
    project = _project_of(path) if path else None
    rows = await asyncio.to_thread(au.list_backups, au.backup_dir(_data(request)), project)
    return OrjsonResponse({"backups": rows})
