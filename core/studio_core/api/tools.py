"""External tool paths (ComfyUI, models, workflows, Blender, Codex): inspect and persist to settings.json."""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ValidationError
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.config import TOOL_KEYS, Settings, save_overrides

router = APIRouter(prefix="/api/settings", dependencies=[Depends(require_token)])

VERSION_TIMEOUT_S = 15.0
_COMFY_VERSION = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")


@dataclass(frozen=True)
class ToolSpec:
    label: str
    kind: str
    marker: str | None = None
    probe_version: bool = False
    version_flag: str = "--version"


TOOLS: dict[str, ToolSpec] = {
    "comfy_dir": ToolSpec("Thư mục ComfyUI", "dir", marker="main.py"),
    "comfy_python": ToolSpec("Python của ComfyUI", "file", probe_version=True),
    "comfy_models_dir": ToolSpec("Thư mục model", "dir"),
    "comfy_workflows_dir": ToolSpec("Thư mục workflow", "dir"),
    "blender_exe": ToolSpec("Blender", "file", probe_version=True),
    "codex_exe": ToolSpec("Codex CLI", "file", probe_version=True),
    "claude_exe": ToolSpec("Claude Code (claude.exe)", "file", probe_version=True),
    "ffmpeg_exe": ToolSpec("FFmpeg (đọc video tham chiếu)", "file", probe_version=True, version_flag="-version"),
}
assert tuple(TOOLS) == TOOL_KEYS


class ToolsUpdate(BaseModel):
    paths: dict[str, str]


def exe_version(path: Path, flag: str = "--version") -> str | None:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        r = subprocess.run([str(path), flag], capture_output=True, timeout=VERSION_TIMEOUT_S, check=False,
                           stdin=subprocess.DEVNULL, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    for raw in (r.stdout, r.stderr):
        for line in raw.decode("utf-8", "replace").splitlines():
            if line.strip():
                return line.strip()[:120]
    return None


def inspect_tool(key: str, path: Path, versions: bool = True) -> dict[str, Any]:
    spec = TOOLS[key]
    exists = path.is_dir() if spec.kind == "dir" else path.is_file()
    out: dict[str, Any] = {"key": key, "label": spec.label, "kind": spec.kind, "path": str(path), "exists": exists,
                           "ok": exists, "version": None, "detail": None}
    if not exists:
        out["detail"] = "Không tìm thấy thư mục" if spec.kind == "dir" else "Không tìm thấy file"
        return out
    if spec.marker and not (path / spec.marker).is_file():
        out["ok"] = False
        out["detail"] = f"Thiếu {spec.marker}, có vẻ không phải thư mục ComfyUI"
    if key == "comfy_dir":
        vf = path / "comfyui_version.py"
        if vf.is_file() and (m := _COMFY_VERSION.search(vf.read_text(encoding="utf-8", errors="replace"))):
            out["version"] = f"ComfyUI {m.group(1)}"
    elif key == "comfy_workflows_dir":
        out["detail"] = f"{sum(1 for _ in path.glob('*.json'))} workflow"
    elif key == "comfy_models_dir":
        out["detail"] = f"{sum(1 for p in path.iterdir() if p.is_dir())} thư mục con"
    if spec.probe_version and versions:
        out["version"] = exe_version(path, spec.version_flag)
        if out["version"] is None:
            out["ok"] = False
            out["detail"] = "Không chạy được --version"
    return out


async def describe_tools(settings: Settings, versions: bool = True) -> dict[str, Any]:
    tools = await asyncio.gather(*(asyncio.to_thread(inspect_tool, k, Path(getattr(settings, k)), versions)
                                   for k in TOOLS))
    return {"tools": list(tools), "settings_file": str(settings.settings_path)}


@router.get("/tools")
async def get_tools(request: Request, versions: bool = Query(True)) -> Response:
    return OrjsonResponse(await describe_tools(request.app.state.settings, versions))


@router.put("/tools")
async def put_tools(request: Request, body: ToolsUpdate) -> Response:
    unknown = sorted(set(body.paths) - set(TOOLS))
    if unknown:
        raise ApiError(400, "invalid", f"unknown tool keys: {', '.join(unknown)}")
    updates = {k: v.strip().strip('"').strip() for k, v in body.paths.items()}
    if empty := [k for k, v in updates.items() if not v]:
        raise ApiError(400, "invalid", f"empty path: {', '.join(empty)}")
    settings: Settings = request.app.state.settings
    try:
        await asyncio.to_thread(save_overrides, settings, updates)
    except ValidationError as e:
        raise ApiError(422, "invalid", str(e)) from e
    return OrjsonResponse(await describe_tools(settings))
