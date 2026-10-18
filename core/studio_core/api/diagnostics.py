"""Read-only snapshot for bug reports: versions, paths, resource health, recent failures, log tails."""
from __future__ import annotations

import asyncio
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from studio_core import __version__
from studio_core.api.auth import require_token
from studio_core.api.errors import OrjsonResponse
from studio_core.api.tools import describe_tools
from studio_core.contracts import Resource
from studio_core.engine.kinds import describe_kinds
from studio_core.paths import is_frozen

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

STARTED_AT = time.time()
KNOWN_LOGS = ("comfyui.log", "blender.log", "mcp.log", "mcp-core.log")
TAIL_READ_BYTES = 256 * 1024
MAX_LINE = 1000
HEALTH_TIMEOUT_S = 3.0

_REDACTIONS = (
    (re.compile(r"(?i)(bearer\s+)[^\s\"',;]+"), r"\1***"),
    (re.compile(r"([?&]t=)[^&\s\"'#]+"), r"\1***"),
    (re.compile(r"(?i)(token[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9_\-.~+/=]{6,}"), r"\1***"),
)


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def tail_lines(path: Path, n: int) -> list[str]:
    if n <= 0:
        return []
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL_READ_BYTES))
        chunk = f.read()
    lines = chunk.decode("utf-8", "replace").splitlines()
    if size > TAIL_READ_BYTES and lines:
        lines = lines[1:]  # first line was cut mid-way by the seek
    return [ln[:MAX_LINE] for ln in lines[-n:]]


def read_logs(logs_dir: Path, n: int, secrets: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in KNOWN_LOGS:
        p = logs_dir / name
        if not p.is_file():
            continue
        try:
            st = p.stat()
            lines = [redact(ln, secrets) for ln in tail_lines(p, n)]
        except OSError as e:
            out.append({"name": name, "path": str(p), "error": str(e), "lines": []})
            continue
        out.append({"name": name, "path": str(p), "bytes": st.st_size, "modified": st.st_mtime, "lines": lines})
    return out


async def _health(r: Resource) -> dict[str, Any]:
    try:
        h = await asyncio.wait_for(r.health(), HEALTH_TIMEOUT_S)
        return h.model_dump(mode="json")
    except Exception as e:
        return {"name": getattr(r, "name", "?"), "status": "error", "detail": {"message": f"{type(e).__name__}: {e}"}}


def _count(fn: Any) -> int | None:
    try:
        return len(fn())
    except Exception:
        return None


@router.get("/diagnostics")
async def diagnostics(request: Request, lines: int = Query(100, ge=0, le=1000),
                      failed: int = Query(20, ge=0, le=200)) -> Response:
    state = request.app.state
    settings = state.settings
    secrets = (state.token,)
    resources: dict[str, Resource] = getattr(state, "resources", {})
    repo = getattr(state, "repo", None)
    engine = getattr(state, "engine", None)

    failed_rows = []
    if repo is not None and failed and hasattr(repo, "recent_failed_steps"):
        failed_rows = await repo.recent_failed_steps(failed)

    tools, health, kinds, logs = await asyncio.gather(
        describe_tools(settings, versions=False),
        asyncio.gather(*(_health(r) for r in resources.values())),
        asyncio.to_thread(_count, lambda: describe_kinds(state.registry, getattr(settings, "comfy_workflows_dir", None))),
        asyncio.to_thread(read_logs, settings.data_dir / "logs", lines, secrets),
    )
    return OrjsonResponse({
        "generated_at": time.time(),
        "core": {
            "version": __version__,
            "pid": os.getpid(),
            "started_at": STARTED_AT,
            "frozen": is_frozen(),
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "data_dir": str(settings.data_dir),
        "settings_file": str(settings.settings_path),
        "tools": [{k: t[k] for k in ("key", "label", "path", "exists", "ok", "detail")} for t in tools["tools"]],
        "resources": list(health),
        "counts": {
            "recipes": _count(engine.list_recipes) if engine is not None else None,
            "kinds": kinds,
        },
        "failed_steps": [{
            "run_id": s.run_id,
            "step_id": s.id,
            "key": s.key,
            "kind": s.kind,
            "attempt": s.attempt,
            "code": (s.error or {}).get("code"),
            "message": redact(str((s.error or {}).get("message", "")), secrets)[:MAX_LINE],
            "ended_at": s.ended_at,
        } for s in failed_rows],
        "logs": logs,
    })
