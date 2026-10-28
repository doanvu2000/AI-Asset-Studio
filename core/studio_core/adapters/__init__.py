"""External tool adapters: ComfyUI, Codex, Claude, Blender."""
from __future__ import annotations

from typing import Any

from ..config import Settings
from ..contracts import ExecutorRegistry, Resource
from .blender import (BlenderAgentExecutor, BlenderAnimateDirsExecutor, BlenderCleanupExecutor, BlenderGameReadyExecutor,
                      BlenderRenderDirsExecutor, BlenderResource,
                      BlenderRigExportExecutor, BlenderScriptExecutor, Procedural3DExecutor)
from .claude import ClaudeBriefExecutor, ClaudeResource, ClaudeTaskExecutor
from .codex import CodexImageExecutor, CodexResource
from .localize import ClaudeLocalizeExecutor
from .soundscape import ClaudeSoundscapeExecutor
from .npc import ClaudeNpcDialogueExecutor
from .tts import NpcVoiceExecutor
from .store_layout import ClaudeStoreLayoutExecutor
from .spine_review import SpineReviewLoopExecutor
from .comfy import CheckedMeshExecutor, CheckedViewsExecutor, ComfyExecutor, ComfyResource


def register(registry: ExecutorRegistry, settings: Settings, bus: Any, blobs: Any = None) -> list[Resource]:
    comfy = ComfyResource(settings, bus)
    blender = BlenderResource(settings, bus)
    codex = CodexResource(settings, bus)
    claude = ClaudeResource(settings, bus)
    registry.register_prefix("comfy.", lambda kind: ComfyExecutor(kind, settings, comfy))
    registry.register(CheckedViewsExecutor(registry.resolve, comfy))
    registry.register(CheckedMeshExecutor(registry.resolve, comfy, blender))
    registry.register(CodexImageExecutor(settings, codex))
    registry.register(ClaudeTaskExecutor(settings))
    registry.register(BlenderRenderDirsExecutor(settings, blender, comfy))
    registry.register(BlenderCleanupExecutor(settings, blender))
    registry.register(BlenderGameReadyExecutor(settings, blender))
    registry.register(BlenderAnimateDirsExecutor(settings, blender, comfy))
    registry.register(BlenderRigExportExecutor(settings, blender, comfy))
    registry.register(ClaudeBriefExecutor(settings))
    registry.register(ClaudeSoundscapeExecutor(settings))
    registry.register(ClaudeNpcDialogueExecutor(settings))
    registry.register(NpcVoiceExecutor(settings))
    registry.register(ClaudeLocalizeExecutor(settings))
    registry.register(ClaudeStoreLayoutExecutor(settings))
    registry.register(SpineReviewLoopExecutor(settings))
    registry.register(BlenderScriptExecutor(settings, blender))
    registry.register(Procedural3DExecutor(settings, blender))
    registry.register(BlenderAgentExecutor(settings))
    return [comfy, blender, codex, claude]
