from __future__ import annotations

from typing import Any

from ..contracts import ExecutorRegistry, Resource
from .executors import ALL_OPS


def register(registry: ExecutorRegistry, settings: Any = None, bus: Any = None, blobs: Any = None) -> list[Resource]:
    for cls in ALL_OPS:
        registry.register(cls())
    return []
