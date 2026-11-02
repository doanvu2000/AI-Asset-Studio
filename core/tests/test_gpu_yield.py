from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from studio_core.app import build_app
from studio_core.contracts import Lane, ResourceHealth, RunStatus
from studio_core.engine.scheduler import LaneScheduler
from test_api_basic import AUTH, TOKEN
from test_engine_runner import Env, FakeExec, until


class FakeComfy:
    name = "comfyui"

    def __init__(self, fail: bool = False) -> None:
        self.frees = 0
        self.fail = fail

    async def health(self) -> ResourceHealth:
        return ResourceHealth(name=self.name, status="up")

    async def ensure_started(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def free(self) -> None:
        self.frees += 1
        if self.fail:
            raise httpx.ConnectError("refused")


GEN = """
id: gen
steps:
  - {id: gen, kind: t.gen, params: {count: 1}}
"""

CPU = """
id: cpu
steps:
  - {id: work, kind: t.cpu, params: {count: 1}}
"""


def _env(tmp_path: Path, comfy: FakeComfy, delay: float = 0.2) -> Env:
    env = Env(tmp_path, [FakeExec("t.gen", Lane.GPU, delay), FakeExec("t.cpu", Lane.CPU, 0.01)],
              {"gen": GEN, "cpu": CPU})
    env.engine.resources = [comfy]
    return env


async def test_hold_blocks_new_grants_until_unhold() -> None:
    s = LaneScheduler({"gpu": 1})
    await s.acquire(Lane.GPU, 0, hint={"run_id": "a"})
    s.hold(Lane.GPU)
    idle = asyncio.create_task(s.wait_idle(Lane.GPU))
    waiter = asyncio.create_task(s.acquire(Lane.GPU, 1, hint={"run_id": "b"}))
    await asyncio.sleep(0.01)
    s.release(Lane.GPU)
    await asyncio.wait_for(idle, 1)
    await asyncio.sleep(0.01)
    assert not waiter.done() and s.queued_runs(Lane.GPU) == ["b"] and s.in_use(Lane.GPU) == 0
    s.unhold(Lane.GPU)
    await asyncio.wait_for(waiter, 1)
    assert s.in_use(Lane.GPU) == 1


async def test_priority_moves_run_to_front_and_keeps_fifo_otherwise() -> None:
    s = LaneScheduler({"gpu": 1})
    await s.acquire(Lane.GPU, 0, hint={"run_id": "x"})
    order: list[str] = []

    async def take(run: str, ready: float) -> None:
        await s.acquire(Lane.GPU, ready, hint={"run_id": run})
        order.append(run)
        s.release(Lane.GPU)

    tasks = [asyncio.create_task(take(r, i)) for i, r in enumerate(["a", "b", "c", "d"])]
    await asyncio.sleep(0.01)
    assert s.queued_runs(Lane.GPU) == ["a", "b", "c", "d"]
    s.set_priority(Lane.GPU, "c", position=0)
    assert s.queued_runs(Lane.GPU) == ["c", "a", "b", "d"]
    s.set_priority(Lane.GPU, "d", position=1)
    assert s.queued_runs(Lane.GPU) == ["c", "d", "a", "b"]
    s.release(Lane.GPU)
    await asyncio.gather(*tasks)
    assert order == ["c", "d", "a", "b"]
    for r in "abcd":
        s.forget(r)
    assert s.priority("c") == 0


async def test_cancelled_waiter_leaves_held_queue() -> None:
    s = LaneScheduler({"gpu": 1})
    s.hold(Lane.GPU)
    t = asyncio.create_task(s.acquire(Lane.GPU, 0, hint={"run_id": "a"}))
    await asyncio.sleep(0.01)
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    assert s.queued(Lane.GPU) == 0 and s.in_use(Lane.GPU) == 0
    s.unhold(Lane.GPU)
    assert s.in_use(Lane.GPU) == 0


async def test_yield_lets_running_finish_frees_comfy_then_resume(tmp_path: Path) -> None:
    comfy = FakeComfy()
    env = _env(tmp_path, comfy)
    eng = env.engine
    a = await eng.start_run("gen", {})
    await until(lambda: eng.scheduler.in_use(Lane.GPU) == 1)
    await eng.gpu.yield_for(5)
    b = await eng.start_run("gen", {})
    await until(lambda: eng.scheduler.queued(Lane.GPU) == 1)
    st = await eng.gpu.state()
    assert st.yielding and [s.run_id for s in st.running] == [a] and [s.run_id for s in st.queued] == [b]
    c = await env.run("cpu")
    assert env.status(c) == RunStatus.SUCCEEDED
    await asyncio.wait_for(eng.wait_idle(a), 5)
    await until(lambda: eng.gpu.freed)
    assert env.status(a) == RunStatus.SUCCEEDED and comfy.frees == 1
    await asyncio.sleep(0.05)
    assert eng.scheduler.queued(Lane.GPU) == 1 and eng.scheduler.in_use(Lane.GPU) == 0
    assert (await eng.gpu.state()).comfy_loaded is False
    await eng.gpu.resume()
    await asyncio.wait_for(eng.wait_idle(b), 5)
    assert env.status(b) == RunStatus.SUCCEEDED and not eng.gpu.yielding
    types = [e.data for e in env.bus.of("gpu.updated")]
    assert types[0]["yielding"] and types[-1] == {"yielding": False, "until": None, "freed": False,
                                                     "queued": []}
    await eng.shutdown()


async def test_yield_expires_on_its_own(tmp_path: Path) -> None:
    env = _env(tmp_path, FakeComfy())
    eng = env.engine
    await eng.gpu.yield_for(0.002)
    b = await eng.start_run("gen", {})
    await asyncio.wait_for(eng.wait_idle(b), 5)
    assert env.status(b) == RunStatus.SUCCEEDED and not eng.gpu.yielding
    assert not eng.scheduler.held(Lane.GPU)
    await eng.shutdown()


async def test_free_failure_is_ignored_and_reyield_does_not_refree(tmp_path: Path) -> None:
    comfy = FakeComfy(fail=True)
    env = _env(tmp_path, comfy)
    eng = env.engine
    await eng.gpu.yield_for(5)
    await until(lambda: eng.gpu.freed)
    await eng.gpu.yield_for(10)
    await asyncio.sleep(0.05)
    assert comfy.frees == 1 and eng.gpu.yielding
    await eng.gpu.resume()
    await eng.gpu.resume()
    assert not eng.scheduler.held(Lane.GPU)
    await eng.shutdown()


async def test_cancel_run_waiting_on_yielded_gpu(tmp_path: Path) -> None:
    env = _env(tmp_path, FakeComfy())
    eng = env.engine
    await eng.gpu.yield_for(5)
    b = await eng.start_run("gen", {})
    await until(lambda: eng.scheduler.queued(Lane.GPU) == 1)
    await eng.cancel_run(b)
    await asyncio.wait_for(eng.wait_idle(b), 5)
    assert env.status(b) == RunStatus.CANCELLED and eng.scheduler.queued(Lane.GPU) == 0
    assert (await eng.gpu.state()).queued == []
    await eng.shutdown()


async def test_gpu_api(tmp_path: Path) -> None:
    env = _env(tmp_path, FakeComfy())
    eng = env.engine
    app = build_app(env.settings, TOKEN, engine=eng, load_plugins=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/gpu", headers=AUTH)
            assert r.status_code == 200 and r.json()["yielding"] is False
            assert (await client.post("/api/gpu/yield", json={"minutes": 0}, headers=AUTH)).status_code in (400, 422)
            r = await client.post("/api/gpu/yield", json={"minutes": 15}, headers=AUTH)
            body: dict[str, Any] = r.json()
            assert r.status_code == 200 and body["yielding"] and body["until"] > 0
            b = await eng.start_run("gen", {})
            await until(lambda: eng.scheduler.queued(Lane.GPU) == 1)
            r = await client.post(f"/api/gpu/queue/{b}/priority", json={"position": 0}, headers=AUTH)
            assert r.status_code == 200 and r.json()["queued"][0]["run_id"] == b
            r = await client.post("/api/gpu/queue/nope/priority", json={"delta": 1}, headers=AUTH)
            assert r.status_code == 404
            r = await client.post(f"/api/gpu/queue/{b}/priority", json={}, headers=AUTH)
            assert r.status_code in (400, 422)
            r = await client.post("/api/gpu/resume", headers=AUTH)
            assert r.status_code == 200 and r.json()["yielding"] is False
            await asyncio.wait_for(eng.wait_idle(b), 5)
            assert env.status(b) == RunStatus.SUCCEEDED


class SlowFreeComfy(FakeComfy):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.cancelled = False

    async def free(self) -> None:
        self.frees += 1
        self.entered.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


BLEND = """
id: blend
steps:
  - {id: render, kind: t.blend, params: {count: 1}}
"""


async def _queue(s: LaneScheduler, runs: list[str]) -> tuple[list[str], list[asyncio.Task[None]]]:
    order: list[str] = []

    async def take(run: str, ready: float) -> None:
        await s.acquire(Lane.GPU, ready, hint={"run_id": run})
        order.append(run)
        s.release(Lane.GPU)

    tasks = [asyncio.create_task(take(r, i)) for i, r in enumerate(runs)]
    await asyncio.sleep(0.01)
    return order, tasks


async def test_negative_delta_and_move_to_last_touch_only_that_run() -> None:
    s = LaneScheduler({"gpu": 1})
    await s.acquire(Lane.GPU, 0, hint={"run_id": "x"})
    order, tasks = await _queue(s, ["a", "b", "c"])
    s.set_priority(Lane.GPU, "a", delta=-1)
    assert s.queued_runs(Lane.GPU) == ["b", "c", "a"]
    s.set_priority(Lane.GPU, "b", position=9)
    assert s.queued_runs(Lane.GPU) == ["c", "a", "b"]
    assert s.priority("c") == 0
    s.release(Lane.GPU)
    await asyncio.gather(*tasks)
    assert order == ["c", "a", "b"]


async def test_long_waiter_beats_priority() -> None:
    from studio_core.engine.scheduler import PRIORITY_MAX_WAIT_S
    clock = Clock()
    s = LaneScheduler({"gpu": 1}, clock=clock)
    await s.acquire(Lane.GPU, 0, hint={"run_id": "x"})
    order, tasks = await _queue(s, ["old"])
    clock.t = PRIORITY_MAX_WAIT_S - 10
    more, tasks2 = await _queue(s, ["vip"])
    s.set_priority(Lane.GPU, "vip", position=0)
    assert s.queued_runs(Lane.GPU) == ["vip", "old"]
    clock.t = PRIORITY_MAX_WAIT_S + 1
    assert s.queued_runs(Lane.GPU) == ["old", "vip"]
    s.release(Lane.GPU)
    await asyncio.gather(*tasks, *tasks2)
    assert order + more == ["old", "vip"]


async def test_yield_expires_while_gpu_still_busy(tmp_path: Path) -> None:
    comfy = FakeComfy()
    env = _env(tmp_path, comfy, delay=0.6)
    eng = env.engine
    a = await eng.start_run("gen", {})
    await until(lambda: eng.scheduler.in_use(Lane.GPU) == 1)
    await eng.gpu.yield_for(0.002)
    b = await eng.start_run("gen", {})
    await until(lambda: not eng.gpu.yielding)
    assert comfy.frees == 0 and env.status(a) == RunStatus.RUNNING
    await asyncio.wait_for(asyncio.gather(eng.wait_idle(a), eng.wait_idle(b)), 5)
    assert env.status(a) == env.status(b) == RunStatus.SUCCEEDED and comfy.frees == 0
    await eng.shutdown()


async def test_resume_while_free_is_running(tmp_path: Path) -> None:
    comfy = SlowFreeComfy()
    env = _env(tmp_path, comfy)
    eng = env.engine
    await eng.gpu.yield_for(5)
    await asyncio.wait_for(comfy.entered.wait(), 2)
    await eng.gpu.resume()
    await until(lambda: comfy.cancelled)
    assert not eng.gpu.yielding and not eng.gpu.freed and not eng.scheduler.held(Lane.GPU)
    b = await env.run("gen")
    assert env.status(b) == RunStatus.SUCCEEDED
    await eng.shutdown()


async def test_yield_holds_blender_lane_and_waits_for_it(tmp_path: Path) -> None:
    comfy = FakeComfy()
    env = Env(tmp_path, [FakeExec("t.blend", Lane.BLENDER, 0.3), FakeExec("t.gen", Lane.GPU, 0.01)],
              {"blend": BLEND, "gen": GEN})
    env.engine.resources = [comfy]
    eng = env.engine
    assert set(eng.gpu.lanes) == {Lane.GPU, Lane.BLENDER}
    a = await eng.start_run("blend", {})
    await until(lambda: eng.scheduler.in_use(Lane.BLENDER) == 1)
    await eng.gpu.yield_for(5)
    st = await eng.gpu.state()
    assert [(s.run_id, s.lane) for s in st.running] == [(a, "blender")]
    b = await eng.start_run("blend", {})
    await until(lambda: eng.scheduler.queued(Lane.BLENDER) == 1)
    assert comfy.frees == 0
    await asyncio.wait_for(eng.wait_idle(a), 5)
    await until(lambda: eng.gpu.freed)
    assert comfy.frees == 1 and eng.scheduler.queued(Lane.BLENDER) == 1
    await eng.gpu.resume()
    await asyncio.wait_for(eng.wait_idle(b), 5)
    assert env.status(b) == RunStatus.SUCCEEDED
    await eng.shutdown()


async def test_priority_kept_until_run_ends(tmp_path: Path) -> None:
    env = _env(tmp_path, FakeComfy())
    eng = env.engine
    await eng.gpu.yield_for(5)
    b = await eng.start_run("gen", {})
    await until(lambda: eng.scheduler.queued(Lane.GPU) == 1)
    eng.gpu.reprioritize(b, position=0)
    assert eng.scheduler.priority(b) == 1
    assert env.bus.of("gpu.updated")[-1].data["queued"] == [b]
    await eng.gpu.resume()
    await asyncio.wait_for(eng.wait_idle(b), 5)
    assert eng.scheduler.priority(b) == 0
    await eng.shutdown()
