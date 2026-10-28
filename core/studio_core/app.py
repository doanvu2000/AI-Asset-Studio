from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from studio_core import __version__
from studio_core.api import ApiError, OrjsonResponse, mount_api
from studio_core.adapters.translate import Translator
from studio_core.config import Settings
from studio_core.contracts import (
    BlobStore,
    Engine,
    EventBus,
    ExecutorRegistry,
    GateResolution,
    RecipeInfo,
    Resource,
)
from studio_core.store import Database, FsBlobStore, MemoryEventBus, SqliteRepo

log = logging.getLogger(__name__)

PLUGIN_MODULES = ("studio_core.adapters", "studio_core.ops", "studio_core.engine")


class UnavailableEngine:
    """Mounted when studio_core.engine.create_engine is missing so the API still boots."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def _fail(self) -> ApiError:
        return ApiError(503, "engine_unavailable", self.reason)

    def list_recipes(self) -> list[RecipeInfo]:
        raise self._fail()

    async def start_run(self, recipe_id: str, inputs: dict[str, Any], params: dict[str, Any] | None = None,
                        project_id: str = "default", title: str = "") -> str:
        raise self._fail()

    async def cancel_run(self, run_id: str) -> None:
        raise self._fail()

    async def retry_step(self, step_id: str, keep: list[str] | None = None) -> None:
        raise self._fail()

    async def resolve_gate(self, step_id: str, res: GateResolution) -> None:
        raise self._fail()

    async def recover(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


def _register_plugins(registry: ExecutorRegistry, settings: Settings, bus: EventBus,
                      blobs: BlobStore) -> list[Resource]:
    resources: list[Resource] = []
    for name in PLUGIN_MODULES:
        try:
            mod = importlib.import_module(name)
        except ImportError as e:
            log.warning("plugin %s not importable: %s", name, e)
            continue
        register = getattr(mod, "register", None)
        if register is None:
            log.warning("plugin %s has no register()", name)
            continue
        try:
            resources.extend(register(registry, settings, bus, blobs) or [])
        except Exception:
            log.exception("plugin %s register() failed", name)
    return resources


async def _create_engine(**deps: Any) -> Engine:
    try:
        factory = importlib.import_module("studio_core.engine").create_engine
    except (ImportError, AttributeError) as e:
        log.warning("engine unavailable: %s", e)
        return UnavailableEngine(f"engine not available: {e}")
    engine = factory(**deps)
    if inspect.isawaitable(engine):
        engine = await engine
    return engine


def build_app(settings: Settings, token: str, *, engine: Engine | None = None,
              load_plugins: bool = True) -> FastAPI:
    """`engine` / `load_plugins=False` exist for tests that want a stub engine and an empty registry."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.db_path)
        await db.open()
        repo = SqliteRepo(db)
        blobs = FsBlobStore(settings.blobs_dir, settings.thumbs_dir, repo)
        bus = MemoryEventBus()
        bus.bind_loop(asyncio.get_running_loop())
        registry = ExecutorRegistry()
        resources = _register_plugins(registry, settings, bus, blobs) if load_plugins else []
        eng = engine or await _create_engine(settings=settings, repo=repo, blobs=blobs, bus=bus,
                                             registry=registry, resources=resources)
        app.state.db = db
        app.state.repo = repo
        app.state.blobs = blobs
        app.state.bus = bus
        app.state.registry = registry
        app.state.resources = {r.name: r for r in resources}
        app.state.engine = eng
        from studio_core.api.similar import start_index
        image_index = await start_index(app)
        try:
            await eng.recover()
            yield
        finally:
            await image_index.stop()
            await _shutdown(eng, resources, db)

    app = FastAPI(title="AI Asset Studio core", version=__version__, lifespan=lifespan,
                  default_response_class=OrjsonResponse, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.token = token
    app.state.translator = Translator(settings.data_dir / "tmp" / "translate")
    app.state.request_shutdown = lambda: log.warning("shutdown requested but no server is attached")
    mount_api(app, settings.allowed_origins)
    return app


async def _shutdown(engine: Engine, resources: list[Resource], db: Database) -> None:
    try:
        await engine.shutdown()
    except Exception:
        log.exception("engine shutdown failed")
    for r in resources:
        try:
            await r.stop()
        except Exception:
            log.exception("resource %s stop failed", getattr(r, "name", r))
    await db.close()


__all__ = ["UnavailableEngine", "build_app"]
