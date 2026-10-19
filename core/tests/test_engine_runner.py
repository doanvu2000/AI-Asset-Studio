from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import pytest
from blake3 import blake3

from studio_core.config import Settings
from studio_core.contracts import (
    BlobRef,
    BlobRow,
    Event,
    ExecutorRegistry,
    GateResolution,
    Lane,
    Outputs,
    RunRow,
    RunStatus,
    StepError,
    StepResult,
    StepRow,
    StepStatus,
    VersionRow,
    VersionStatus,
)
from studio_core.engine import EngineStateError, StudioEngine, create_engine, register


class FakeRepo:
    def __init__(self) -> None:
        self.runs: dict[str, RunRow] = {}
        self.steps: dict[str, StepRow] = {}
        self.cache: dict[str, Outputs] = {}
        self.versions: dict[str, VersionRow] = {}
        self.blobs: dict[str, BlobRow] = {}

    async def create_run(self, run: RunRow) -> None:
        self.runs[run.id] = run.model_copy(deep=True)

    async def update_run(self, run_id: str, **fields: Any) -> None:
        self.runs[run_id] = RunRow.model_validate({**self.runs[run_id].model_dump(), **fields})

    async def get_run(self, run_id: str) -> RunRow | None:
        r = self.runs.get(run_id)
        return r.model_copy(deep=True) if r else None

    async def list_runs(self, limit: int = 50, before: float | None = None, status: RunStatus | None = None,
                        batch_id: str | None = None, batched: bool = False) -> list[RunRow]:
        rows = [r for r in self.runs.values() if status is None or r.status == status]
        return [r.model_copy(deep=True) for r in rows][:limit]

    async def create_steps(self, steps: list[StepRow]) -> None:
        for s in steps:
            self.steps[s.id] = s.model_copy(deep=True)

    async def update_step(self, step_id: str, **fields: Any) -> None:
        self.steps[step_id] = StepRow.model_validate({**self.steps[step_id].model_dump(), **fields})

    async def get_step(self, step_id: str) -> StepRow | None:
        s = self.steps.get(step_id)
        return s.model_copy(deep=True) if s else None

    async def get_steps(self, run_id: str) -> list[StepRow]:
        return sorted((s.model_copy(deep=True) for s in self.steps.values() if s.run_id == run_id),
                      key=lambda s: s.order)

    async def list_steps_by_status(self, statuses: list[StepStatus]) -> list[StepRow]:
        return [s.model_copy(deep=True) for s in self.steps.values() if s.status in statuses]

    async def cache_get(self, key: str) -> Outputs | None:
        return self.cache.get(key)

    async def cache_put(self, key: str, outputs: Outputs) -> None:
        self.cache[key] = outputs

    async def add_version(self, v: VersionRow) -> None:
        self.versions[v.id] = v

    async def set_version_status(self, version_id: str, status: VersionStatus) -> VersionRow | None:
        return None

    async def list_versions(self, run_id: str | None = None, status: VersionStatus | None = None,
                            limit: int = 200) -> list[VersionRow]:
        return [v for v in self.versions.values() if run_id is None or v.run_id == run_id]

    async def put_blob_row(self, b: BlobRow) -> None:
        self.blobs[b.hash] = b

    async def get_blob_row(self, hash: str) -> BlobRow | None:
        return self.blobs.get(hash)


class FakeBus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, type: str, data: dict[str, Any] | None = None, run_id: str | None = None,
                step_id: str | None = None) -> Event:
        e = Event(seq=len(self.events) + 1, ts=time.time(), type=type, run_id=run_id, step_id=step_id, data=data or {})
        self.events.append(e)
        return e

    def subscribe(self, after_seq: int | None = None) -> AsyncIterator[Event]:
        raise NotImplementedError

    def of(self, type: str) -> list[Event]:
        return [e for e in self.events if e.type == type]


class FakeBlobs:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.refs: dict[str, BlobRef] = {}

    async def put_bytes(self, data: bytes, name: str | None = None, mime: str | None = None) -> BlobRef:
        h = blake3(data).hexdigest()
        (self.root / h).write_bytes(data)
        ref = self.refs[h] = BlobRef(hash=h, mime=mime or "image/png", size=len(data), name=name)
        return ref

    async def put_file(self, src: Path, name: str | None = None, mime: str | None = None) -> BlobRef:
        return await self.put_bytes(src.read_bytes(), name or src.name, mime)

    async def get(self, hash: str) -> BlobRef | None:
        return self.refs.get(hash)

    def path(self, hash: str) -> Path:
        return self.root / hash

    async def thumbnail(self, hash: str, size: int = 256) -> Path:
        raise NotImplementedError


class FakeExec:
    """Generates `count` images (no inputs) or transforms each input image; records concurrency per lane."""

    active: dict[Lane, int] = defaultdict(int)
    peak: dict[Lane, int] = defaultdict(int)

    def __init__(self, kind: str, lane: Lane = Lane.CPU, delay: float = 0.0, cacheable: bool = False,
                 fail: Callable[[int], BaseException | None] | None = None, version: str = "1"):
        self.kind, self.lane, self.delay, self._cacheable, self.fail, self.version = \
            kind, lane, delay, cacheable, fail, version
        self.calls: list[Outputs] = []
        self.cancel_seen = False

    def cacheable(self, params: dict[str, Any]) -> bool:
        return self._cacheable

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Outputs) -> StepResult:
        self.calls.append(inputs)
        FakeExec.active[self.lane] += 1
        FakeExec.peak[self.lane] = max(FakeExec.peak[self.lane], FakeExec.active[self.lane])
        try:
            assert ctx.job_dir.is_dir()
            ctx.log(f"{self.kind} start")
            for i in range(4):
                ctx.progress(i, 4)
                try:
                    await asyncio.sleep(self.delay / 4)
                except asyncio.CancelledError:
                    self.cancel_seen = ctx.cancelled.is_set()
                    raise
            if self.fail and (err := self.fail(len(self.calls))):
                raise err
            srcs = [b for refs in inputs.values() for b in refs]
            if not srcs:
                datas = [f"{self.kind}:{params}:{i}".encode() for i in range(int(params.get("count", 1)))]
            else:
                datas = [f"{self.kind}<{b.hash}".encode() for b in srcs]
            out = [await ctx.blobs.put_bytes(d) for d in datas]
            return StepResult(outputs={"images": out}, meta={"n": len(out)})
        finally:
            FakeExec.active[self.lane] -= 1


@pytest.fixture(autouse=True)
def _reset_peaks() -> None:
    FakeExec.active.clear()
    FakeExec.peak.clear()


class Env:
    def __init__(self, tmp: Path, execs: list[FakeExec], recipes: dict[str, str],
                 lane_limits: dict[str, int] | None = None, repo: FakeRepo | None = None,
                 blobs: FakeBlobs | None = None):
        rdir = tmp / "recipes"
        rdir.mkdir(exist_ok=True)
        for name, text in recipes.items():
            (rdir / f"{name}.yaml").write_text(text, encoding="utf-8")
        self.settings = Settings(data_dir=tmp / "data", recipes_dir=rdir,
                                 lane_limits=lane_limits or {"gpu": 1, "blender": 1, "cloud": 3, "cpu": 4})
        self.repo = repo or FakeRepo()
        self.bus = FakeBus()
        self.blobs = blobs or FakeBlobs(tmp / "blobs")
        self.registry = ExecutorRegistry()
        register(self.registry, self.settings, self.bus, self.blobs)
        self.execs = {e.kind: e for e in execs}
        for e in execs:
            self.registry.register(e)
        self.engine: StudioEngine = create_engine(settings=self.settings, repo=self.repo, blobs=self.blobs,
                                                  bus=self.bus, registry=self.registry, resources=[])

    def restart(self) -> Env:
        clone = object.__new__(Env)
        clone.__dict__.update(self.__dict__)
        clone.bus = FakeBus()
        clone.engine = create_engine(settings=self.settings, repo=self.repo, blobs=self.blobs, bus=clone.bus,
                                     registry=self.registry, resources=[])
        return clone

    async def run(self, recipe: str, inputs: dict[str, Any] | None = None, title: str = "") -> str:
        run_id = await self.engine.start_run(recipe, inputs or {}, title=title)
        await asyncio.wait_for(self.engine.wait_idle(run_id), 5)
        return run_id

    def steps(self, run_id: str) -> dict[str, StepRow]:
        return {s.key: s for s in self.repo.steps.values() if s.run_id == run_id}

    def status(self, run_id: str) -> RunStatus:
        return self.repo.runs[run_id].status


async def until(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise TimeoutError("condition not reached")
        await asyncio.sleep(0.005)


LINEAR = """
id: lin
name: Linear
inputs:
  n: {type: int, default: 2}
steps:
  - {id: gen, kind: t.gen, params: {count: "${inputs.n}"}}
  - {id: fit, kind: t.op, in: {images: gen.images}, params: {size: 64}}
outputs:
  final: fit.images
"""


async def test_linear_success_creates_versions(tmp_path: Path) -> None:
    env = Env(tmp_path, [FakeExec("t.gen", Lane.GPU, 0.02), FakeExec("t.op")], {"lin": LINEAR})
    run_id = await env.run("lin", {"n": 3}, title="Sword")
    assert env.status(run_id) == RunStatus.SUCCEEDED
    steps = env.steps(run_id)
    assert all(s.status == StepStatus.SUCCEEDED for s in steps.values())
    assert steps["gen"].params == {"count": 3}
    assert len(steps["fit"].outputs["images"]) == 3
    assert steps["fit"].inputs["images"] == steps["gen"].outputs["images"]
    vs = list(env.repo.versions.values())
    assert len(vs) == 3 and {v.asset_name for v in vs} == {"Sword"}
    assert [v.blob for v in vs] == steps["fit"].outputs["images"]
    assert len(env.bus.of("version.updated")) == 3
    assert (env.settings.jobs_dir / run_id / "gen-1").is_dir()
    assert env.bus.of("step.progress") and env.bus.of("step.log")
    updates = [e.data["status"] for e in env.bus.of("step.updated") if e.step_id == steps["gen"].id]
    assert updates[0] == "ready" and "running" in updates and updates[-1] == "succeeded"
    assert env.bus.of("run.updated")[-1].data["status"] == "succeeded"
    assert env.engine.list_recipes()[0].steps[0]["lane"] == "gpu"


PARALLEL = """
id: par
steps:
  - {id: g1, kind: t.gpu, params: {count: 1}}
  - {id: g2, kind: t.gpu, params: {count: 1}}
  - {id: g3, kind: t.gpu, params: {count: 1}}
  - {id: c1, kind: t.cpu, params: {count: 1}}
  - {id: c2, kind: t.cpu, params: {count: 1}}
  - {id: c3, kind: t.cpu, params: {count: 1}}
  - {id: join, kind: t.cpu, in: {a: g1.images, b: c3.images}}
"""


async def test_parallel_branches_respect_lane_limits(tmp_path: Path) -> None:
    env = Env(tmp_path, [FakeExec("t.gpu", Lane.GPU, 0.05), FakeExec("t.cpu", Lane.CPU, 0.05)], {"par": PARALLEL},
              lane_limits={"gpu": 1, "cpu": 2})
    t0 = time.monotonic()
    run_id = await env.run("par")
    assert env.status(run_id) == RunStatus.SUCCEEDED
    assert FakeExec.peak[Lane.GPU] == 1
    assert FakeExec.peak[Lane.CPU] == 2
    assert time.monotonic() - t0 < 0.5
    waits = [e for e in env.bus.of("step.updated") if e.data["status"] == "waiting_resource"]
    assert waits and {e.data["meta"]["waiting_for"] for e in waits} <= {"gpu", "cpu"}
    assert "waiting_for" not in env.steps(run_id)["g3"].meta
    gpu_starts = sorted((s.started_at, s.key) for s in env.steps(run_id).values() if s.lane == Lane.GPU)
    assert [k for _, k in gpu_starts] == ["g1", "g2", "g3"]


async def test_cache_hit(tmp_path: Path) -> None:
    gen = FakeExec("t.gen", Lane.GPU, cacheable=True)
    env = Env(tmp_path, [gen, FakeExec("t.op")], {"lin": LINEAR})
    r1 = await env.run("lin")
    r2 = await env.run("lin")
    assert env.status(r2) == RunStatus.SUCCEEDED
    s1, s2 = env.steps(r1)["gen"], env.steps(r2)["gen"]
    assert s2.status == StepStatus.CACHED and s2.cache_key == s1.cache_key
    assert s2.outputs == s1.outputs
    assert len(gen.calls) == 1
    r3 = await env.run("lin", {"n": 5})
    assert env.steps(r3)["gen"].status == StepStatus.SUCCEEDED and len(gen.calls) == 2


FAILING = """
id: fail
steps:
  - {id: a, kind: t.op, params: {count: 1}}
  - {id: b, kind: t.bad, in: {images: a.images}}
  - {id: c, kind: t.op, in: {images: b.images}}
  - {id: slow, kind: t.slow, params: {count: 1}}
"""


async def test_failure_skips_rest_and_fails_run(tmp_path: Path) -> None:
    bad = FakeExec("t.bad", fail=lambda n: StepError("boom", "it broke", detail={"x": 1}))
    slow = FakeExec("t.slow", Lane.CLOUD, delay=2.0)
    env = Env(tmp_path, [FakeExec("t.op"), bad, slow], {"fail": FAILING})
    run_id = await env.run("fail")
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.FAILED
    assert steps["b"].status == StepStatus.FAILED
    assert steps["b"].error["code"] == "boom" and steps["b"].error["detail"] == {"x": 1}
    assert steps["c"].status == StepStatus.SKIPPED
    assert steps["slow"].status == StepStatus.CANCELLED and slow.cancel_seen
    assert "it broke" in env.repo.runs[run_id].error
    assert not env.repo.versions


async def test_unexpected_exception_is_internal(tmp_path: Path) -> None:
    bad = FakeExec("t.bad", fail=lambda n: RuntimeError("oops"))
    env = Env(tmp_path, [FakeExec("t.op"), bad, FakeExec("t.slow")], {"fail": FAILING})
    run_id = await env.run("fail")
    assert env.steps(run_id)["b"].error["code"] == "internal"


async def test_retry_step(tmp_path: Path) -> None:
    bad = FakeExec("t.bad", fail=lambda n: StepError("flaky", "first time") if n == 1 else None)
    env = Env(tmp_path, [FakeExec("t.op"), bad, FakeExec("t.slow", delay=1.0)], {"fail": FAILING})
    run_id = await env.run("fail")
    assert env.status(run_id) == RunStatus.FAILED
    b = env.steps(run_id)["b"]
    with pytest.raises(EngineStateError):
        await env.engine.retry_step(env.steps(run_id)["a"].id)
    await env.engine.retry_step(b.id)
    await asyncio.wait_for(env.engine.wait_idle(run_id), 5)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED
    assert steps["b"].attempt == 2 and steps["b"].error is None
    assert steps["c"].status == StepStatus.SUCCEEDED and steps["c"].attempt == 1
    assert steps["slow"].status == StepStatus.SUCCEEDED and steps["slow"].attempt == 2
    assert steps["a"].attempt == 1
    assert (env.settings.jobs_dir / run_id / "b-2").is_dir()
    assert env.repo.runs[run_id].error is None


async def test_cancel_run(tmp_path: Path) -> None:
    slow = FakeExec("t.gen", Lane.GPU, delay=5.0)
    env = Env(tmp_path, [slow, FakeExec("t.op")], {"lin": LINEAR})
    run_id = await env.engine.start_run("lin", {})
    await until(lambda: env.steps(run_id)["gen"].status == StepStatus.RUNNING)
    await asyncio.wait_for(env.engine.cancel_run(run_id), 2)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.CANCELLED
    assert steps["gen"].status == StepStatus.CANCELLED and slow.cancel_seen
    assert steps["fit"].status == StepStatus.SKIPPED
    assert FakeExec.active[Lane.GPU] == 0
    await env.engine.retry_step(steps["gen"].id)
    await until(lambda: env.steps(run_id)["gen"].status == StepStatus.RUNNING)
    assert env.steps(run_id)["gen"].attempt == 2
    await env.engine.cancel_run(run_id)


PICK = """
id: pick
name: Pick Recipe
steps:
  - {id: gen, kind: t.gen, params: {count: 3}}
  - {id: pick, kind: gate.pick, in: {images: gen.images}, params: {k: 1}}
  - {id: fit, kind: t.op, in: {images: pick.images}}
outputs:
  final: fit.images
"""

APPROVE = """
id: appr
steps:
  - {id: gen, kind: t.gen, params: {count: 2}}
  - {id: ok, kind: gate.approve, in: {images: gen.images}}
  - {id: fit, kind: t.op, in: {images: ok.images}}
"""


async def test_gate_pick(tmp_path: Path) -> None:
    fit = FakeExec("t.op")
    env = Env(tmp_path, [FakeExec("t.gen"), fit], {"pick": PICK})
    run_id = await env.run("pick")
    assert env.status(run_id) == RunStatus.AWAITING_HUMAN
    gate = env.steps(run_id)["pick"]
    assert gate.status == StepStatus.AWAITING_HUMAN and gate.lane == Lane.NONE
    ev = env.bus.of("gate.awaiting")[-1]
    assert ev.data["kind"] == "pick" and ev.data["k"] == 1 and len(ev.data["candidates"]) == 3
    cands = [c["hash"] for c in ev.data["candidates"]]
    with pytest.raises(ValueError):
        await env.engine.resolve_gate(gate.id, GateResolution(picked=["nope"]))
    with pytest.raises(ValueError):
        await env.engine.resolve_gate(gate.id, GateResolution(picked=cands[:2]))
    assert env.steps(run_id)["pick"].status == StepStatus.AWAITING_HUMAN
    await env.engine.resolve_gate(gate.id, GateResolution(picked=[cands[1]]))
    await asyncio.wait_for(env.engine.wait_idle(run_id), 5)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED
    assert [b.hash for b in steps["pick"].outputs["images"]] == [cands[1]]
    assert [b.hash for b in fit.calls[0]["images"]] == [cands[1]]
    assert [v.asset_name for v in env.repo.versions.values()] == ["Pick Recipe"]
    with pytest.raises(EngineStateError):
        await env.engine.resolve_gate(gate.id, GateResolution(picked=[cands[0]]))


async def test_regenerate_source_of_waiting_gate(tmp_path: Path) -> None:
    gen = FakeExec("t.gen")
    env = Env(tmp_path, [gen, FakeExec("t.op")], {"pick": PICK})
    run_id = await env.run("pick")
    steps = env.steps(run_id)
    with pytest.raises(EngineStateError):
        await env.engine.retry_step(steps["fit"].id)
    await env.engine.retry_step(steps["gen"].id)
    await until(lambda: env.steps(run_id)["pick"].status == StepStatus.AWAITING_HUMAN
                and env.steps(run_id)["gen"].attempt == 2)
    steps = env.steps(run_id)
    assert len(gen.calls) == 2 and steps["gen"].status == StepStatus.SUCCEEDED
    assert env.status(run_id) == RunStatus.AWAITING_HUMAN
    await env.engine.resolve_gate(steps["pick"].id, GateResolution(picked=[steps["pick"].inputs["images"][0].hash]))
    await asyncio.wait_for(env.engine.wait_idle(run_id), 5)
    assert env.status(run_id) == RunStatus.SUCCEEDED
    with pytest.raises(EngineStateError):
        await env.engine.retry_step(env.steps(run_id)["gen"].id)


async def test_gate_survives_restart(tmp_path: Path) -> None:
    env = Env(tmp_path, [FakeExec("t.gen"), FakeExec("t.op")], {"pick": PICK})
    run_id = await env.run("pick")
    await env.engine.shutdown()
    env2 = env.restart()
    await env2.engine.recover()
    assert env2.status(run_id) == RunStatus.AWAITING_HUMAN
    gate = env2.steps(run_id)["pick"]
    assert gate.status == StepStatus.AWAITING_HUMAN
    await env2.engine.resolve_gate(gate.id, GateResolution(picked=[gate.inputs["images"][0].hash]))
    await asyncio.wait_for(env2.engine.wait_idle(run_id), 5)
    assert env2.status(run_id) == RunStatus.SUCCEEDED
    assert len(env2.repo.versions) == 1


async def test_gate_approve(tmp_path: Path) -> None:
    env = Env(tmp_path, [FakeExec("t.gen"), FakeExec("t.op")], {"appr": APPROVE})
    r1 = await env.run("appr")
    await env.engine.resolve_gate(env.steps(r1)["ok"].id, GateResolution(approved=True))
    await asyncio.wait_for(env.engine.wait_idle(r1), 5)
    assert env.status(r1) == RunStatus.SUCCEEDED
    assert env.steps(r1)["ok"].outputs == env.steps(r1)["gen"].outputs
    r2 = await env.run("appr")
    await env.engine.resolve_gate(env.steps(r2)["ok"].id, GateResolution(approved=False))
    await asyncio.wait_for(env.engine.wait_idle(r2), 5)
    assert env.status(r2) == RunStatus.FAILED
    assert env.steps(r2)["ok"].error["code"] == "rejected"
    assert env.steps(r2)["fit"].status == StepStatus.SKIPPED


MAP = """
id: map
steps:
  - {id: gen, kind: t.gen, params: {count: 3}}
  - {id: each, kind: t.each, in: {image: gen.images}, map: image}
outputs:
  final: each.images
"""


async def test_map_step(tmp_path: Path) -> None:
    each = FakeExec("t.each", Lane.GPU, delay=0.01)
    env = Env(tmp_path, [FakeExec("t.gen"), each], {"map": MAP})
    run_id = await env.run("map")
    assert env.status(run_id) == RunStatus.SUCCEEDED
    steps = env.steps(run_id)
    gen_out = steps["gen"].outputs["images"]
    assert [c["image"] for c in each.calls] == [[b] for b in gen_out]
    expected = [blake3(f"t.each<{b.hash}".encode()).hexdigest() for b in gen_out]
    assert [b.hash for b in steps["each"].outputs["images"]] == expected
    assert steps["each"].meta["items"] == [{"n": 1}] * 3
    prog = [e.data for e in env.bus.of("step.progress") if e.step_id == steps["each"].id]
    assert prog[-1]["value"] == 3 and prog[-1]["max"] == 3
    assert all(p["max"] == 3 for p in prog)
    assert len(env.repo.versions) == 3


async def test_recover_marks_interrupted_and_retry_resumes(tmp_path: Path) -> None:
    slow = FakeExec("t.gen", Lane.GPU, delay=5.0)
    env = Env(tmp_path, [slow, FakeExec("t.op")], {"lin": LINEAR})
    run_id = await env.engine.start_run("lin", {})
    await until(lambda: env.steps(run_id)["gen"].status == StepStatus.RUNNING)
    # simulate a crash: state stays "running" in the repo, no shutdown hook ran
    st = env.engine._runs[run_id]
    st.driver.cancel()
    for t in list(st.tasks.values()):
        t.cancel()
    await asyncio.sleep(0.05)
    assert env.steps(run_id)["gen"].status == StepStatus.RUNNING

    slow.delay = 0.0
    env2 = env.restart()
    await env2.engine.recover()
    gen = env2.steps(run_id)["gen"]
    assert gen.status == StepStatus.FAILED and gen.error["code"] == "interrupted"
    assert env2.status(run_id) == RunStatus.INTERRUPTED
    assert env2.steps(run_id)["fit"].status == StepStatus.PENDING
    await env2.engine.retry_step(gen.id)
    await asyncio.wait_for(env2.engine.wait_idle(run_id), 5)
    assert env2.status(run_id) == RunStatus.SUCCEEDED
    assert env2.steps(run_id)["gen"].attempt == 2


async def test_shutdown_interrupts_running(tmp_path: Path) -> None:
    env = Env(tmp_path, [FakeExec("t.gen", Lane.GPU, delay=5.0), FakeExec("t.op")], {"lin": LINEAR})
    run_id = await env.engine.start_run("lin", {})
    await until(lambda: env.steps(run_id)["gen"].status == StepStatus.RUNNING)
    await asyncio.wait_for(env.engine.shutdown(), 2)
    assert env.status(run_id) == RunStatus.INTERRUPTED
    assert env.steps(run_id)["gen"].error["code"] == "interrupted"
    assert env.steps(run_id)["fit"].status == StepStatus.PENDING
    with pytest.raises(EngineStateError):
        await env.engine.start_run("lin", {})


async def test_image_input_binding(tmp_path: Path) -> None:
    recipe = """
id: img
inputs:
  src: {type: mesh}
steps:
  - {id: op, kind: t.op, in: {images: inputs.src}}
"""
    op = FakeExec("t.op")
    env = Env(tmp_path, [op], {"img": recipe})
    ref = await env.blobs.put_bytes(b"glb", name="a.glb", mime="model/gltf-binary")
    with pytest.raises(ValueError):
        await env.engine.start_run("img", {"src": "missing"})
    run_id = await env.run("img", {"src": ref.hash})
    assert env.status(run_id) == RunStatus.SUCCEEDED
    assert op.calls[0]["images"] == [ref]


FLAKY = """
id: flaky
steps:
  - {id: g, kind: t.flaky, params: {count: 1}}
"""


async def test_auto_retry_reruns_retryable_failure_once(tmp_path: Path) -> None:
    flaky = FakeExec("t.flaky", Lane.GPU, fail=lambda n: StepError("cuda", "CUDA error", retryable=True)
                     if n == 1 else None)
    flaky.auto_retry = 1  # type: ignore[attr-defined]
    env = Env(tmp_path, [flaky], {"flaky": FLAKY})
    run_id = await env.run("flaky")
    assert env.status(run_id) == RunStatus.SUCCEEDED
    step = env.steps(run_id)["g"]
    assert len(flaky.calls) == 2 and step.meta["auto_retries"] == 1 and step.attempt == 1


async def test_auto_retry_gives_up_on_non_retryable_and_after_limit(tmp_path: Path) -> None:
    hard = FakeExec("t.flaky", Lane.GPU, fail=lambda n: StepError("bad", "nope"))
    hard.auto_retry = 1  # type: ignore[attr-defined]
    env = Env(tmp_path, [hard], {"flaky": FLAKY})
    assert env.status(await env.run("flaky")) == RunStatus.FAILED
    assert len(hard.calls) == 1

    always = FakeExec("t.flaky", Lane.GPU, fail=lambda n: StepError("cuda", "CUDA error", retryable=True))
    always.auto_retry = 1  # type: ignore[attr-defined]
    (tmp_path / "b").mkdir()
    env2 = Env(tmp_path / "b", [always], {"flaky": FLAKY})
    assert env2.status(await env2.run("flaky")) == RunStatus.FAILED
    assert len(always.calls) == 2
