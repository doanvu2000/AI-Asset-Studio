"""What a one-click setup would install on this machine: model packs, ComfyUI, Blender, Claude Code."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from studio_core.config import Settings
from studio_core.paths import package_dir

GB = 1024 ** 3
_MODEL_FILE = re.compile(r"[\w.-]+\.safetensors")


@cache
def manifest() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))


def workflows_dir() -> Path:
    return package_dir() / "workflows"


@cache
def workflow_models(workflow: str) -> frozenset[str]:
    f = workflows_dir() / f"{workflow}.api.json"
    return frozenset(_MODEL_FILE.findall(f.read_text(encoding="utf-8"))) if f.is_file() else frozenset()


def pack_models(pack_ids: list[str] | set[str]) -> list[dict[str, Any]]:
    wanted = {p["id"] for p in manifest()["packs"] if p["required"] or p["id"] in pack_ids}
    names: set[str] = set()
    for p in manifest()["packs"]:
        if p["id"] in wanted:
            for w in p["workflows"]:
                names |= workflow_models(w)
    return [m for m in manifest()["models"] if m["name"] in names]


def model_path(models_dir: Path, m: dict[str, Any]) -> Path:
    return models_dir / m["dir"] / m["name"]


def model_present(models_dir: Path, m: dict[str, Any]) -> bool:
    p = model_path(models_dir, m)
    return p.is_file() and p.stat().st_size == m["size"]


def recipe_packs(recipe_text: str) -> set[str]:
    """Packs a recipe's workflows come from (`views.qwen*` steps run the multi-angle workflow)."""
    used = set(re.findall(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b", recipe_text))
    if "views.qwen" in recipe_text:
        used.add("multi_angles_qwen2511")
    return {p["id"] for p in manifest()["packs"] if used & set(p["workflows"])}


@dataclass
class Gpu:
    name: str | None
    driver: float | None
    vram_gb: float | None

    def problem(self) -> str | None:
        need = manifest()["comfyui"]["min_driver"]
        if not self.name:
            return "Không thấy GPU NVIDIA (nvidia-smi). ComfyUI cần card NVIDIA."
        if self.driver is not None and self.driver < need:
            return f"Driver NVIDIA {self.driver:g} cũ, cần ≥ {need}. Cập nhật driver rồi cài lại."
        return None


def detect_gpu() -> Gpu:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=15, creationflags=flags, check=False)
    except (OSError, subprocess.SubprocessError):
        return Gpu(None, None, None)
    parts = [p.strip() for p in (r.stdout.splitlines() or [""])[0].split(",")]
    if r.returncode != 0 or len(parts) < 3:
        return Gpu(None, None, None)
    try:
        driver = float(".".join(parts[1].split(".")[:2]))
        vram = round(float(parts[2]) / 1024, 1)
    except ValueError:
        driver, vram = None, None
    return Gpu(parts[0], driver, vram)


def default_dirs(settings: Settings) -> tuple[Path, Path]:
    tools = settings.data_dir.parent / "tools"
    models = settings.comfy_models_dir if settings.comfy_models_dir.is_dir() else tools / "models"
    return tools, models


def comfy_ready(settings: Settings) -> bool:
    return (settings.comfy_dir / "main.py").is_file() and settings.comfy_python.is_file()


def free_bytes(path: Path) -> int | None:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def describe(settings: Settings, tools_dir: Path | None = None, models_dir: Path | None = None) -> dict[str, Any]:
    d_tools, d_models = default_dirs(settings)
    tools_dir, models_dir = tools_dir or d_tools, models_dir or d_models
    packs = []
    base = pack_models([])
    for p in manifest()["packs"]:
        ms = base if p["required"] else [m for m in pack_models([p["id"]]) if m not in base]
        missing = [m for m in ms if not model_present(models_dir, m)]
        packs.append({"id": p["id"], "label": p["label"], "detail": p["detail"], "required": p["required"],
                      "files": len(ms), "size": sum(m["size"] for m in ms),
                      "missing_size": sum(m["size"] for m in missing), "installed": not missing})
    gpu = detect_gpu()
    blender = manifest()["blender"]
    return {
        "tools_dir": str(tools_dir), "models_dir": str(models_dir),
        "free": {"tools": free_bytes(tools_dir), "models": free_bytes(models_dir)},
        "gpu": {"name": gpu.name, "driver": gpu.driver, "vram_gb": gpu.vram_gb, "problem": gpu.problem()},
        "packs": packs,
        "components": {
            "comfyui": {"installed": comfy_ready(settings), "path": str(settings.comfy_dir),
                        "version": manifest()["comfyui"]["version"], "size": manifest()["comfyui"]["disk_gb"] * GB},
            "blender": {"installed": settings.blender_exe.is_file(), "path": str(settings.blender_exe),
                        "version": blender["version"], "size": blender["size"]},
            "claude": {"installed": settings.claude_exe.is_file(), "path": str(settings.claude_exe)},
        },
    }
