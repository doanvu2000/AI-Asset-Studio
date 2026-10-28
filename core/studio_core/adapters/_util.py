from __future__ import annotations

import logging
from typing import Any

from ..contracts import ResourceHealth

log = logging.getLogger("studio_core.adapters")

IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MESH_EXT = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".obj": "model/obj", ".ply": "application/ply",
            ".fbx": "application/octet-stream", ".stl": "model/stl"}
AUDIO_EXT = {".flac": "audio/flac", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
             ".opus": "audio/ogg"}
MIME_EXT = {v: k for k, v in IMAGE_EXT.items()} | {"model/gltf-binary": ".glb"}


def publish_health(bus: Any, health: ResourceHealth) -> None:
    if bus is None:
        return
    try:
        bus.publish("resource.updated", health.model_dump(mode="json"))
    except Exception:
        log.exception("publish resource.updated failed")
