"""Machine configuration and the green / yellow / red fit of every recipe and workflow."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from studio_core import hardware
from studio_core.api.auth import require_token
from studio_core.api.errors import OrjsonResponse
from studio_core.bootstrap import plan
from studio_core.config import Settings
from studio_core.contracts import ExecutorRegistry

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _lane_of(registry: ExecutorRegistry):
    def lane(kind: str) -> str | None:
        try:
            return registry.resolve(kind).lane.value
        except Exception:
            return None
    return lane


def _compute(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    env = hardware.environment(settings)
    engine = request.app.state.engine
    lib = getattr(engine, "library", None)
    flow_lib = getattr(getattr(engine, "flows", None), "library", None)
    lane = _lane_of(request.app.state.registry)
    recipes = {} if lib is None else {
        rid: hardware.recipe_needs(r, lib.text(rid) or "", lane) for rid, r in list(lib.recipes.items())}
    flows = {} if flow_lib is None else {d.id: [n.recipe_id for n in d.nodes] for d in flow_lib.list()}
    return hardware.fit_all(env, recipes, flows)


@router.get("/hardware")
async def get_hardware(request: Request) -> Response:
    models = plan.default_dirs(request.app.state.settings)[1]
    hw = await asyncio.to_thread(hardware.probe, models)
    return OrjsonResponse(hw.to_json())


@router.get("/fit")
async def get_fit(request: Request) -> Response:
    return OrjsonResponse(await asyncio.to_thread(_compute, request))
