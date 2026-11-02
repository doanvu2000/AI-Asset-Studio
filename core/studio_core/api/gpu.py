"""GPU lane control: lend the GPU to other apps for a while, and reorder runs waiting for it."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.engine.gpu import MAX_YIELD_MIN, GpuControl

router = APIRouter(prefix="/api/gpu", dependencies=[Depends(require_token)])


class YieldBody(BaseModel):
    minutes: float = Field(15, gt=0, le=MAX_YIELD_MIN)


class PriorityBody(BaseModel):
    delta: int | None = Field(None, ge=-1000, le=1000)
    position: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _one(self) -> PriorityBody:
        if (self.delta is None) == (self.position is None):
            raise ValueError("give exactly one of delta or position")
        return self


def _gpu(request: Request) -> GpuControl:
    gpu = getattr(request.app.state.engine, "gpu", None)
    if gpu is None:
        raise ApiError(503, "engine_unavailable", "GPU control needs the studio engine")
    return gpu


async def _state(gpu: GpuControl) -> Response:
    return OrjsonResponse((await gpu.state()).model_dump(mode="json"))


@router.get("")
async def get_gpu(request: Request) -> Response:
    return await _state(_gpu(request))


@router.post("/yield")
async def yield_gpu(request: Request, body: YieldBody) -> Response:
    gpu = _gpu(request)
    await gpu.yield_for(body.minutes)
    return await _state(gpu)


@router.post("/resume")
async def resume_gpu(request: Request) -> Response:
    gpu = _gpu(request)
    await gpu.resume()
    return await _state(gpu)


@router.post("/queue/{run_id}/priority")
async def prioritize(request: Request, run_id: str, body: PriorityBody) -> Response:
    gpu = _gpu(request)
    try:
        gpu.reprioritize(run_id, delta=body.delta, position=body.position)
    except LookupError as e:
        raise ApiError(404, "not_found", str(e.args[0]) if e.args else "not queued") from e
    return await _state(gpu)

