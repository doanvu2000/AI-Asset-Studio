from __future__ import annotations

from typing import Any

from ..config import Settings
from ..contracts import BlobStore, EventBus, ExecutorRegistry, Repo
from .gates import register_gates
from .recipe import Recipe, RecipeError, load_recipes, parse_recipe
from .runner import EngineStateError, NotFoundError, StudioEngine
from .scheduler import LaneScheduler

__all__ = ["StudioEngine", "create_engine", "register", "Recipe", "RecipeError", "NotFoundError",
           "EngineStateError", "LaneScheduler", "load_recipes", "parse_recipe"]


def create_engine(*, settings: Settings, repo: Repo, blobs: BlobStore, bus: EventBus,
                  registry: ExecutorRegistry, resources: Any = None) -> StudioEngine:
    register_gates(registry)
    return StudioEngine(settings=settings, repo=repo, blobs=blobs, bus=bus, registry=registry, resources=resources)


def register(registry: ExecutorRegistry, settings: Settings | None = None, bus: EventBus | None = None,
             blobs: BlobStore | None = None) -> list:
    register_gates(registry)
    return []
