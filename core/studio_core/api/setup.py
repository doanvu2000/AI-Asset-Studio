"""One-click setup: plan (what is missing), start / cancel the background job, open a Claude login console."""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.bootstrap.job import SetupJob, SetupRequest
from studio_core.bootstrap.plan import describe, manifest
from studio_core.config import Settings

router = APIRouter(prefix="/api/setup", dependencies=[Depends(require_token)])


class SetupBody(BaseModel):
    tools_dir: str
    models_dir: str
    packs: list[str] = []
    blender: bool = True
    claude: bool = True


def _job(request: Request) -> SetupJob | None:
    return getattr(request.app.state, "setup_job", None)


def _dir(raw: str | None) -> Path | None:
    raw = (raw or "").strip().strip('"').strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        raise ApiError(400, "invalid", f"cần đường dẫn đầy đủ: {raw}")
    return p


@router.get("")
async def get_setup(request: Request, tools_dir: str | None = Query(None),
                    models_dir: str | None = Query(None)) -> Response:
    settings: Settings = request.app.state.settings
    plan = await asyncio.to_thread(describe, settings, _dir(tools_dir), _dir(models_dir))
    job = _job(request)
    return OrjsonResponse({"plan": plan, "job": job.snapshot() if job else None})


@router.get("/job")
async def get_job(request: Request) -> Response:
    job = _job(request)
    return OrjsonResponse(job.snapshot() if job else None)


@router.post("/start")
async def start_setup(request: Request, body: SetupBody) -> Response:
    job = _job(request)
    if job and job.state == "running":
        raise ApiError(409, "busy", "đang cài")
    known = {p["id"] for p in manifest()["packs"]}
    if unknown := sorted(set(body.packs) - known):
        raise ApiError(400, "invalid", f"gói không có: {', '.join(unknown)}")
    tools, models = _dir(body.tools_dir), _dir(body.models_dir)
    if not tools or not models:
        raise ApiError(400, "invalid", "thiếu thư mục cài")
    req = SetupRequest(tools, models, body.packs, body.blender, body.claude)
    job = await asyncio.to_thread(SetupJob, request.app.state.settings, req)
    request.app.state.setup_job = job
    job.start()
    return OrjsonResponse(job.snapshot())


@router.post("/cancel")
async def cancel_setup(request: Request) -> Response:
    job = _job(request)
    if job:
        job.cancel()
    return OrjsonResponse(job.snapshot() if job else None)


@router.post("/claude-login")
async def claude_login(request: Request) -> Response:
    exe: Path = request.app.state.settings.claude_exe
    if not exe.is_file():
        raise ApiError(404, "not_found", "chưa cài Claude Code")
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    subprocess.Popen([str(exe)], creationflags=flags, cwd=str(Path.home()))
    return OrjsonResponse({"ok": True})
