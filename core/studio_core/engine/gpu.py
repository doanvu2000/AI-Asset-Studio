from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ..contracts import GpuSlot, GpuState, Lane

if TYPE_CHECKING:
    from .runner import StudioEngine

log = logging.getLogger(__name__)

COMFY = "comfyui"
MAX_YIELD_MIN = 24 * 60
HEALTH_TIMEOUT_S = 2.0


class GpuControl:
    """Lends the GPU to other apps: no new step starts on a GPU-using lane, running ones finish, then ComfyUI drops
    its models. State is in memory only, so a core restart ends a yield."""

    def __init__(self, engine: StudioEngine, lanes: list[str] | tuple[str, ...] = ("gpu",)):
        self._engine = engine
        self._sched = engine.scheduler
        self.lanes = tuple(Lane(x) for x in lanes if x != Lane.NONE.value) or (Lane.GPU,)
        self._task: asyncio.Task[None] | None = None
        self.until: float | None = None
        self.freed = False
        self.holders: dict[str, tuple[str, str, Lane]] = {}  # step_id -> (run_id, step_key, lane) on a yield lane

    @property
    def yielding(self) -> bool:
        return self.until is not None

    def _publish(self) -> None:
        self._engine.bus.publish("gpu.updated", {"yielding": self.yielding, "until": self.until, "freed": self.freed,
                                                 "queued": self._sched.queued_runs(Lane.GPU)})

    async def yield_for(self, minutes: float) -> None:
        if not 0 < minutes <= MAX_YIELD_MIN:
            raise ValueError(f"minutes must be in (0, {MAX_YIELD_MIN}]")
        self._cancel_task()
        seconds = minutes * 60
        self.until = time.time() + seconds
        for lane in self.lanes:
            self._sched.hold(lane)
        self._task = asyncio.create_task(self._hold(asyncio.get_running_loop().time() + seconds), name="gpu-yield")
        self._publish()

    async def resume(self) -> None:
        self._cancel_task()
        if self.yielding:
            self._end()

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def reprioritize(self, run_id: str, *, delta: int | None = None, position: int | None = None) -> None:
        self._sched.set_priority(Lane.GPU, run_id, delta=delta, position=position)
        self._publish()

    def _cancel_task(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()

    def _end(self) -> None:
        self.until = None
        self.freed = False
        for lane in self.lanes:
            self._sched.unhold(lane)
        self._publish()

    async def _hold(self, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        try:
            idle = asyncio.gather(*(self._sched.wait_idle(lane) for lane in self.lanes))
            await asyncio.wait_for(idle, max(0.0, deadline - loop.time()))
        except TimeoutError:
            pass
        else:
            if not self.freed:
                await self._free()
                self.freed = True
                self._publish()
            await asyncio.sleep(max(0.0, deadline - loop.time()))
        if self._task is asyncio.current_task():
            self._task = None
            self._end()

    def _comfy(self) -> Any:
        res = self._engine.resources
        items = res.values() if isinstance(res, dict) else (res or [])
        return next((r for r in items if getattr(r, "name", None) == COMFY), None)

    async def _free(self) -> None:
        comfy = self._comfy()
        if comfy is None or not hasattr(comfy, "free"):
            return
        try:
            await comfy.free()
        except Exception as e:  # a dead or unreachable ComfyUI has nothing loaded to free
            log.warning("gpu yield: comfy /free failed: %r", e)

    async def state(self) -> GpuState:
        eng = self._engine

        def slot(run_id: str, step_id: str, key: str, lane: Lane, waiting: float | None = None) -> GpuSlot:
            run = eng.live_run(run_id)
            step = eng.live_step(run_id, key)
            return GpuSlot(run_id=run_id, step_id=step_id, step_key=key, lane=lane.value,
                           kind=step.kind if step else "", title=run.title if run else "",
                           recipe_id=run.recipe_id if run else "", priority=self._sched.priority(run_id),
                           waiting_s=waiting)

        now = self._sched.now()
        running = [slot(r, s, k, lane) for s, (r, k, lane) in self.holders.items()]
        queued = [slot(w.run_id, w.hint.get("step_id", ""), w.hint.get("step_key", ""), Lane.GPU,
                       waiting=now - w.enqueued_at)
                  for w in self._sched.waiters(Lane.GPU) if w.run_id]
        comfy = self._comfy()
        status = None
        if comfy is not None:
            try:
                status = (await asyncio.wait_for(comfy.health(), HEALTH_TIMEOUT_S)).status
            except Exception as e:  # TimeoutError included: a hung ComfyUI must not stall the panel
                log.warning("gpu state: comfy health failed: %r", e)
        return GpuState(yielding=self.yielding, until=self.until, freed=self.freed, running=running, queued=queued,
                        comfy_status=status,
                        comfy_loaded=None if status is None else status in ("up", "busy") and not self.freed)
