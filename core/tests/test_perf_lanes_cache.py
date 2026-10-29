from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx

from studio_core.adapters import claude as claude_mod
from studio_core.adapters.comfy.executor import ComfyExecutor
from studio_core.app import build_app
from studio_core.contracts import Lane, RunStatus, StepResult, StepStatus
from studio_core.engine.cache import cache_key, cacheable_meta, executor_cache_key
from studio_core.engine.runner import lane_limits
from test_adapters_comfy import _settings as comfy_settings
from test_api_basic import AUTH, TOKEN, StubEngine, _settings as api_settings
from test_engine_runner import Env, FakeExec, until

GPU_LONG = """
id: gpu-long
steps:
  - {id: gen, kind: t.gpu, params: {count: 1}}
"""

CPU_CLAUDE = """
id: cpu-claude
steps:
  - {id: brief, kind: t.cloud, params: {count: 1}}
  - {id: fit, kind: t.cpu, in: {images: brief.images}}
"""

BRANCHY = """
id: branchy
steps:
  - {id: gen, kind: t.gpu, params: {count: 1}}
  - {id: side, kind: t.cpu, params: {count: 1}}
  - {id: join, kind: t.cpu, in: {a: gen.images, b: side.images}}
"""


def _ended(env: Env, run_id: str) -> float:
    return max(s.ended_at or 0 for s in env.steps(run_id).values())


async def test_cpu_and_claude_run_do_not_wait_for_busy_gpu(tmp_path: Path) -> None:
    gpu = FakeExec("t.gpu", Lane.GPU, 0.6)
    env = Env(tmp_path, [gpu, FakeExec("t.cloud", Lane.CLOUD, 0.05), FakeExec("t.cpu", Lane.CPU, 0.05)],
              {"gpu-long": GPU_LONG, "cpu-claude": CPU_CLAUDE})
    t0 = time.monotonic()
    a = await env.engine.start_run("gpu-long", {})
    await until(lambda: env.engine.scheduler.in_use(Lane.GPU) == 1)
    b = await env.engine.start_run("cpu-claude", {})
    await asyncio.wait_for(env.engine.wait_idle(b), 5)
    assert env.status(b) == RunStatus.SUCCEEDED
    assert env.status(a) == RunStatus.RUNNING and env.engine.scheduler.in_use(Lane.GPU) == 1
    assert time.monotonic() - t0 < 0.45
    await asyncio.wait_for(env.engine.wait_idle(a), 5)
    assert env.status(a) == RunStatus.SUCCEEDED and _ended(env, b) < _ended(env, a)


async def test_second_gpu_run_queues_while_its_cpu_branch_runs(tmp_path: Path) -> None:
    env = Env(tmp_path, [FakeExec("t.gpu", Lane.GPU, 0.3), FakeExec("t.cpu", Lane.CPU, 0.02)],
              {"gpu-long": GPU_LONG, "branchy": BRANCHY})
    a = await env.engine.start_run("gpu-long", {})
    await until(lambda: env.engine.scheduler.in_use(Lane.GPU) == 1)
    b = await env.engine.start_run("branchy", {})
    await until(lambda: env.steps(b)["side"].status == StepStatus.SUCCEEDED, 2)
    assert env.steps(b)["gen"].status == StepStatus.WAITING_RESOURCE
    await asyncio.wait_for(asyncio.gather(env.engine.wait_idle(a), env.engine.wait_idle(b)), 5)
    assert env.status(b) == RunStatus.SUCCEEDED and FakeExec.peak[Lane.GPU] == 1


def test_gpu_and_blender_limits_are_capped() -> None:
    got = lane_limits({"gpu": 3, "blender": 4, "cloud": 2, "cpu": 8})
    assert got == {"gpu": 1, "blender": 1, "cloud": 2, "cpu": 8}
    assert lane_limits({"cpu": 4})["gpu"] == 1 and lane_limits({"gpu": 0})["gpu"] == 1


LIN = """
id: lin
inputs:
  size: {type: int, default: 64}
  model: {type: text, default: opus}
steps:
  - {id: gen, kind: t.gen, params: {count: 1, model: "${inputs.model}", _timeout_s: 30}}
  - {id: fit, kind: t.op, in: {images: gen.images}, params: {size: "${inputs.size}"}}
"""


class MetaExec(FakeExec):
    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        res = await super().run(ctx, params, inputs)
        return StepResult(outputs=res.outputs, meta={"score": 7.0, "cost_usd": 0.12,
                                                      "items": [{"seed": 3, "cost_usd": 0.1}]})


def _cache_env(tmp_path: Path, version: str = "1") -> tuple[Env, FakeExec, FakeExec]:
    gen = MetaExec("t.gen", Lane.CLOUD, cacheable=True, version=version)
    op = FakeExec("t.op", Lane.CPU, cacheable=True)
    return Env(tmp_path, [gen, op], {"lin": LIN}), gen, op


async def test_identical_rerun_hits_and_keeps_meta_without_cost(tmp_path: Path) -> None:
    env, gen, op = _cache_env(tmp_path)
    r1 = await env.run("lin")
    r2 = await env.run("lin")
    s1, s2 = env.steps(r1), env.steps(r2)
    assert s2["gen"].status == StepStatus.CACHED and s2["fit"].status == StepStatus.CACHED
    assert s2["fit"].outputs == s1["fit"].outputs and len(gen.calls) == 1 and len(op.calls) == 1
    assert s2["gen"].meta["score"] == 7.0 and "cost_usd" not in s2["gen"].meta
    assert s2["gen"].meta["items"] == [{"seed": 3}] and "spec" in s2["gen"].meta
    assert env.status(r2) == RunStatus.SUCCEEDED and len(env.repo.versions) == 0


async def test_param_model_or_input_change_misses(tmp_path: Path) -> None:
    env, gen, op = _cache_env(tmp_path)
    await env.run("lin")
    r = await env.run("lin", {"size": 128})
    assert env.steps(r)["gen"].status == StepStatus.CACHED and env.steps(r)["fit"].status == StepStatus.SUCCEEDED
    r = await env.run("lin", {"model": "sonnet"})
    assert env.steps(r)["gen"].status == StepStatus.SUCCEEDED and len(gen.calls) == 2
    # new upstream output = new input blob for fit
    assert env.steps(r)["fit"].status == StepStatus.SUCCEEDED and len(op.calls) == 3


async def test_default_model_repin_misses(tmp_path: Path, monkeypatch: Any) -> None:
    env, gen, _ = _cache_env(tmp_path)
    await env.run("lin")
    monkeypatch.setitem(claude_mod.MODEL_IDS, "opus", "claude-opus-next")
    r = await env.run("lin")
    assert env.steps(r)["gen"].status == StepStatus.SUCCEEDED and len(gen.calls) == 2


async def test_executor_version_bump_misses(tmp_path: Path) -> None:
    env, gen, _ = _cache_env(tmp_path)
    await env.run("lin")
    gen.version = "2"
    r = await env.run("lin")
    assert env.steps(r)["gen"].status == StepStatus.SUCCEEDED and len(gen.calls) == 2


async def test_no_cache_run_reexecutes_and_refreshes(tmp_path: Path) -> None:
    env, gen, op = _cache_env(tmp_path)
    r1 = await env.run("lin")
    r2 = await env.engine.start_run("lin", {}, {"$meta": {"no_cache": True}})
    await asyncio.wait_for(env.engine.wait_idle(r2), 5)
    s2 = env.steps(r2)
    assert s2["gen"].status == StepStatus.SUCCEEDED and s2["fit"].status == StepStatus.SUCCEEDED
    assert len(gen.calls) == 2 and len(op.calls) == 2
    assert s2["gen"].cache_key == env.steps(r1)["gen"].cache_key
    r3 = await env.run("lin")
    assert env.steps(r3)["gen"].status == StepStatus.CACHED and len(gen.calls) == 2


async def test_missing_blob_turns_hit_into_miss(tmp_path: Path) -> None:
    env, gen, _ = _cache_env(tmp_path)
    r1 = await env.run("lin")
    for b in env.steps(r1)["gen"].outputs["images"]:
        env.blobs.path(b.hash).unlink()
    r2 = await env.run("lin")
    assert env.steps(r2)["gen"].status == StepStatus.SUCCEEDED and len(gen.calls) == 2


def test_key_ignores_non_output_params_only() -> None:
    k = cache_key("k", "1", {"a": 1, "_timeout_s": 5, "cache": True}, {}, "")
    assert k == cache_key("k", "1", {"a": 1, "_timeout_s": 900, "_auto_retry": 2, "_free_before": True}, {}, "")
    assert k != cache_key("k", "1", {"a": 2}, {}, "")
    assert k != cache_key("k", "1", {"a": 1, "_drop_outputs": ["9"]}, {}, "")
    assert cacheable_meta({"cost_usd": 1, "spent_live_usd": 2, "x": 1}) == {"x": 1}


def test_comfy_seed_rules(tmp_path: Path) -> None:
    ex = ComfyExecutor("comfy.wf", comfy_settings(tmp_path), resource=None)
    params = {"57:27.text": "a"}
    assert executor_cache_key(ex, "comfy.wf", params, {}) is None
    k1 = executor_cache_key(ex, "comfy.wf", {**params, "57:3.seed": 1}, {})
    k2 = executor_cache_key(ex, "comfy.wf", {**params, "57:3.seed": 2}, {})
    assert k1 and k2 and k1 != k2
    assert k1 == executor_cache_key(ex, "comfy.wf", {**params, "57:3.seed": 1, "_timeout_s": 10}, {})


class RecordingEngine(StubEngine):
    async def start_run(self, recipe_id: str, inputs: dict[str, Any], params: dict[str, Any] | None = None,
                        project_id: str = "default", title: str = "") -> str:
        self.calls.append(("start", params))
        return "run-1"


async def test_api_no_cache_flag(tmp_path: Path) -> None:
    engine = RecordingEngine()
    app = build_app(api_settings(tmp_path), TOKEN, engine=engine, load_plugins=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            body = {"recipe_id": "x", "inputs": {}, "no_cache": True, "params": {"$meta": {"source": "app"}}}
            assert (await client.post("/api/runs", json=body, headers=AUTH)).status_code == 201
            await client.post("/api/runs", json={"recipe_id": "x", "inputs": {}}, headers=AUTH)
    assert engine.calls[0] == ("start", {"$meta": {"source": "app", "no_cache": True}})
    assert engine.calls[1] == ("start", None)


async def test_sqlite_cache_meta_and_old_schema_migration(tmp_path: Path) -> None:
    import sqlite3

    from studio_core.store import Database, SqliteRepo

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE cache ("key" TEXT PRIMARY KEY, outputs TEXT NOT NULL, created_at REAL NOT NULL)')
    con.execute("""INSERT INTO cache VALUES ('old', '{"images": []}', 1.0)""")
    con.commit()
    con.close()
    db = Database(path)
    await db.open()
    try:
        repo = SqliteRepo(db)
        assert await repo.cache_get("old") == {"images": []} and await repo.cache_get_meta("old") == {}
        await repo.cache_put("k", {}, {"score": 7})
        assert await repo.cache_get_meta("k") == {"score": 7} and await repo.cache_get_meta("nope") == {}
    finally:
        await db.close()
