"""External tool adapters: ComfyUI, Codex, Claude, Blender."""
from __future__ import annotations

from typing import Any

from ..config import Settings
from ..contracts import ExecutorRegistry, Resource
from .blender import BlenderCleanupExecutor, BlenderRenderDirsExecutor, BlenderResource
from .claude import ClaudeResource, ClaudeTaskExecutor
from .codex import CodexImageExecutor, CodexResource
from .comfy import ComfyExecutor, ComfyResource


def register(registry: ExecutorRegistry, settings: Settings, bus: Any, blobs: Any = None) -> list[Resource]:
    comfy = ComfyResource(settings, bus)
    blender = BlenderResource(settings, bus)
    codex = CodexResource(settings, bus)
    claude = ClaudeResource(settings, bus)
    registry.register_prefix("comfy.", lambda kind: ComfyExecutor(kind, settings, comfy))
    registry.register(CodexImageExecutor(settings, codex))
    registry.register(ClaudeTaskExecutor(settings))
    registry.register(BlenderRenderDirsExecutor(settings, blender, comfy))
    registry.register(BlenderCleanupExecutor(settings, blender))
    return [comfy, blender, codex, claude]
