from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from studio_core.contracts import Lane, RunStatus
from studio_core.engine.runner import model_group
from studio_core.engine.scheduler import AFFINITY_MAX_WAIT_S, LaneScheduler
from test_engine_runner import Env, FakeExec, until


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def _drain(s: LaneScheduler, lane: Lane, waiters: list[tuple[str, str, float]],
                 before_release: Any = None) -> list[str]:
    order: list[str] = []

    async def take(name: str, group: str, ready: float) -> None:
        await s.acquire(lane, ready, hint={"group": group})
        order.append(name)
        s.release(lane)

    tasks = []
    for name, group, ready in waiters:
        tasks.append(asyncio.create_task(take(name, group, ready)))
        await asyncio.sleep(0)
    if before_release:
        before_release()
    s.release(lane)
    await asyncio.gather(*tasks)
    return order


async def test_gpu_prefers_last_model_group() -> None:
    s = LaneScheduler({"gpu": 1})
    await s.acquire(Lane.GPU, 0, hint={"group": "A"})
    assert s.last_group(Lane.GPU) == "A"
    order = await _drain(s, Lane.GPU, [("b1", "B", 1), ("a2", "A", 2), ("b3", "B", 3), ("a4", "A", 4)])
    assert order == ["a2", "a4", "b1", "b3"]
    assert s.last_group(Lane.GPU) == "B" and s.in_use(Lane.GPU) == 0


async def test_starving_waiter_beats_affinity() -> None:
    clock = Clock()
    s = LaneScheduler({"gpu": 1}, clock=clock)
    await s.acquire(Lane.GPU, 0, hint={"group": "A"})

    def age() -> None:
        clock.t = AFFINITY_MAX_WAIT_S + 1

    order = await _drain(s, Lane.GPU, [("b1", "B", 1), ("a2", "A", 2), ("b3", "B", 3), ("a4", "A", 4)], age)
    assert order == ["b1", "a2", "b3", "a4"]


async def test_only_old_waiters_starve() -> None:
    clock = Clock()
    s = LaneScheduler({"gpu": 1}, clock=clock)
    await s.acquire(Lane.GPU, 0, hint={"group": "A"})
    order: list[str] = []

    async def take(name: str, group: str, ready: float) -> None:
        await s.acquire(Lane.GPU, ready, hint={"group": group})
        order.append(name)
        s.release(Lane.GPU)

    old = asyncio.create_task(take("b_old", "B", 1))
    await asyncio.sleep(0)
    clock.t = AFFINITY_MAX_WAIT_S - 1
    young = asyncio.create_task(take("a_young", "A", 0.5))
    await asyncio.sleep(0)
    s.release(Lane.GPU)
    await asyncio.gather(old, young)
    assert order == ["a_young", "b_old"]


async def test_other_lanes_stay_fifo() -> None:
    s = LaneScheduler({"cpu": 1})
    await s.acquire(Lane.CPU, 0, hint={"group": "A"})
    order = await _drain(s, Lane.CPU, [("b1", "B", 1), ("a2", "A", 2)])
    assert order == ["b1", "a2"]


class GroupedExec(FakeExec):
    def __init__(self, kind: str, group: str, delay: float = 0.0):
        super().__init__(kind, Lane.GPU, delay)
        self.model_group = group


ONE_GEN = """
id: one-gen
steps:
  - {id: gen, kind: t.gen, params: {count: 1}}
"""

ONE_RMBG = """
id: one-rmbg
steps:
  - {id: rmbg, kind: t.rmbg, params: {count: 1}}
"""


async def test_engine_passes_model_group(tmp_path: Path) -> None:
    gen = GroupedExec("t.gen", "zimage", 0.05)
    rmbg = FakeExec("t.rmbg", Lane.GPU, 0.01)
    assert model_group(gen, "t.gen") == "zimage" and model_group(rmbg, "t.rmbg") == "t.rmbg"
    env = Env(tmp_path, [gen, rmbg], {"one-gen": ONE_GEN, "one-rmbg": ONE_RMBG})
    a = await env.engine.start_run("one-gen", {})
    await until(lambda: env.engine.scheduler.in_use(Lane.GPU) == 1)
    b = await env.engine.start_run("one-rmbg", {})
    await until(lambda: env.engine.scheduler.queued(Lane.GPU) == 1)
    c = await env.engine.start_run("one-gen", {})
    await until(lambda: env.engine.scheduler.queued(Lane.GPU) == 2)
    await asyncio.wait_for(asyncio.gather(*(env.engine.wait_idle(r) for r in (a, b, c))), 5)
    assert all(env.status(r) == RunStatus.SUCCEEDED for r in (a, b, c))
    starts = sorted((s.started_at, s.run_id) for r in (a, b, c) for s in env.steps(r).values())
    # FIFO would run b before c; affinity keeps the loaded zimage model busy first.
    assert [r for _, r in starts] == [a, c, b]
    assert env.engine.scheduler.last_group(Lane.GPU) == "t.rmbg"
