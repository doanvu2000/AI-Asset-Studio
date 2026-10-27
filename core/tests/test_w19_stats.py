from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import Lane, RunRow, RunStatus, StepError, StepResult, StepRow, StepStatus
from studio_core.store import SqliteRepo
from studio_core.store.stats import meta_cost

TOKEN = "t19"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DAY = 86400.0
BASE = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()


@pytest.fixture
async def ctx(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, SqliteRepo]]:
    rdir = tmp_path / "builtin"
    rdir.mkdir()
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "none")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c, app.state.repo


_n = 0


async def _run(repo: SqliteRepo, recipe: str, status: RunStatus, t0: float, dur: float | None,
               steps: list[dict[str, Any]] = (), project: str = "default") -> str:
    global _n
    _n += 1
    rid = f"r{_n}"
    await repo.create_run(RunRow(id=rid, project_id=project, recipe_id=recipe, recipe_hash="h", status=status,
                                 created_at=t0, ended_at=None if dur is None else t0 + dur))
    rows = []
    for i, s in enumerate(steps):
        start, end = s.get("t") or (None, None)
        rows.append(StepRow(id=f"{rid}s{i}", run_id=rid, key=f"k{i}", kind=s["kind"], lane=s.get("lane", Lane.GPU),
                            status=s.get("status", StepStatus.SUCCEEDED), meta=s.get("meta", {}), order=i,
                            started_at=None if start is None else t0 + start,
                            ended_at=None if end is None else t0 + end))
    await repo.create_steps(rows)
    return rid


async def _seed(repo: SqliteRepo) -> None:
    a0 = BASE + 2 * DAY
    await _run(repo, "a", RunStatus.SUCCEEDED, a0, 100, [
        {"kind": "comfy.gen", "t": (10, 60), "meta": {"cost_usd": 0.5}},
        {"kind": "gate.pick", "lane": Lane.NONE, "t": (60, 90)},
    ], project="p1")
    await _run(repo, "a", RunStatus.SUCCEEDED, a0 + 3600, 40, [
        {"kind": "claude.task", "lane": Lane.CLOUD, "t": (0, 20), "meta": {"cost_usd": 0.25}},
        {"kind": "comfy.gen", "status": StepStatus.CACHED, "t": (20, 20)},
    ], project="p1")
    await _run(repo, "a", RunStatus.FAILED, a0 + 7200, 10, [
        {"kind": "comfy.gen", "status": StepStatus.FAILED, "t": (0, 10), "meta": {"cost_usd": 0.1}},
        {"kind": "claude.task", "lane": Lane.CLOUD, "status": StepStatus.FAILED, "meta": {"cost_usd": "abc"}},
    ], project="p1")
    await _run(repo, "a", RunStatus.CANCELLED, a0 + 9000, 5, project="p1")
    await _run(repo, "a", RunStatus.RUNNING, a0 + 9500, None, [
        {"kind": "comfy.gen", "status": StepStatus.RUNNING, "t": (0, None)}], project="p1")
    b0 = BASE + 5 * DAY
    await _run(repo, "b", RunStatus.SUCCEEDED, b0, 20, [
        {"kind": "views.qwen_checked", "t": (0, 20),
         "meta": {"claude_cost_usd": 0.05, "items": [{"cost_usd": 0.1}, {"claude_cost_usd": 0.2}, "junk", 3]}},
    ])
    await _run(repo, "b", RunStatus.INTERRUPTED, b0 + 60, None)
    await _run(repo, "c", RunStatus.SUCCEEDED, b0 + 120, None)


async def _get(c: httpx.AsyncClient, path: str, **params: Any) -> Any:
    r = await c.get(path, params=params, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


async def test_recipe_aggregates(ctx: tuple[httpx.AsyncClient, SqliteRepo]) -> None:
    c, repo = ctx
    await _seed(repo)
    body = await _get(c, "/api/stats/recipes")
    by = {r["recipe_id"]: r for r in body["recipes"]}
    assert [r["recipe_id"] for r in body["recipes"]] == ["a", "b", "c"]

    a = by["a"]
    assert (a["runs"], a["succeeded"], a["failed"], a["cancelled"], a["active"]) == (5, 2, 1, 1, 1)
    assert a["failure_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert a["cost_usd"] == pytest.approx(0.85) and a["avg_cost_usd"] == pytest.approx(0.17)
    assert a["runs_with_cost"] == 3
    # Active time is the union of non-gate step intervals: run 1 = 50 s (gate wait excluded), run 2 = 20 s.
    assert a["duration_s"] == {"avg": 35.0, "p50": 20.0, "p90": 50.0}
    assert a["wall_avg_s"] == 70.0
    assert a["work_s"] == pytest.approx(50 + 20 + 10) and a["gpu_s"] == pytest.approx(60)
    assert a["cache_hit_ratio"] == pytest.approx(1 / 3, abs=1e-4)
    assert a["last_run_at"] == pytest.approx(BASE + 2 * DAY + 9500)

    b = by["b"]
    assert b["cost_usd"] == pytest.approx(0.35) and b["failure_rate"] == 0.5 and b["interrupted"] == 1

    cc = by["c"]
    assert cc["duration_s"] == {"avg": None, "p50": None, "p90": None}
    assert cc["cost_usd"] == 0 and cc["cache_hit_ratio"] is None and cc["failure_rate"] == 0.0

    t = body["totals"]
    assert t["runs"] == 8 and t["cost_usd"] == pytest.approx(1.2)
    assert t["duration_s"]["p50"] == 20.0 and t["duration_s"]["p90"] == 50.0
    assert t["failure_rate"] == pytest.approx(2 / 6, abs=1e-4)


async def test_percentiles_nearest_rank(ctx: tuple[httpx.AsyncClient, SqliteRepo]) -> None:
    c, repo = ctx
    for i in range(10):
        await _run(repo, "d", RunStatus.SUCCEEDED, BASE + i, 100.0, [{"kind": "x", "t": (0, float(10 - i))}])
    d = (await _get(c, "/api/stats/recipes"))["recipes"][0]
    assert d["duration_s"] == {"avg": 5.5, "p50": 5.0, "p90": 9.0}


async def test_filters(ctx: tuple[httpx.AsyncClient, SqliteRepo]) -> None:
    c, repo = ctx
    await _seed(repo)
    p1 = await _get(c, "/api/stats/recipes", project_id="p1")
    assert [r["recipe_id"] for r in p1["recipes"]] == ["a"] and p1["totals"]["runs"] == 5
    late = await _get(c, "/api/stats/recipes", since=BASE + 4 * DAY)
    assert {r["recipe_id"] for r in late["recipes"]} == {"b", "c"}
    early = await _get(c, "/api/stats/recipes", until=BASE + 2 * DAY + 3600)
    assert early["totals"]["runs"] == 1 and early["totals"]["cost_usd"] == pytest.approx(0.5)
    r = await c.get("/api/stats/recipes", params={"since": 10, "until": 5}, headers=AUTH)
    assert r.status_code == 400

    steps = (await _get(c, "/api/stats/steps", project_id="p1"))["steps"]
    by = {s["kind"]: s for s in steps}
    assert "gate.pick" not in by
    assert [s["kind"] for s in steps][:2] == ["comfy.gen", "claude.task"]
    assert by["comfy.gen"]["cost_usd"] == pytest.approx(0.6) and by["comfy.gen"]["cached"] == 1
    assert by["comfy.gen"]["failed"] == 1 and by["comfy.gen"]["total_s"] == pytest.approx(60)
    assert by["comfy.gen"]["avg_s"] == 50.0
    only_b = (await _get(c, "/api/stats/steps", recipe_id="b"))["steps"]
    assert [s["kind"] for s in only_b] == ["views.qwen_checked"]


async def test_daily_and_overview(ctx: tuple[httpx.AsyncClient, SqliteRepo]) -> None:
    c, repo = ctx
    await _seed(repo)
    until = BASE + 7 * DAY
    body = await _get(c, "/api/stats/overview", since=BASE, until=until, days=7, tz_offset_s=0)
    days = body["daily"]["days"]
    assert days[0]["day"] == "2026-10-01" and days[-1]["day"] == "2026-10-07" and len(days) == 7
    by = {d["day"]: d for d in days}
    assert by["2026-10-03"]["runs"] == 5 and by["2026-10-03"]["cost_usd"] == pytest.approx(0.85)
    assert by["2026-10-03"]["failed"] == 1 and by["2026-10-03"]["duration_avg_s"] == 35.0
    assert by["2026-10-06"]["runs"] == 3 and by["2026-10-06"]["failed"] == 1
    assert by["2026-10-04"] == {"day": "2026-10-04", "runs": 0, "succeeded": 0, "failed": 0, "cost_usd": 0.0,
                                "work_s": 0.0, "duration_avg_s": None}
    assert body["totals"]["runs"] == 8 and body["steps"][0]["kind"] == "comfy.gen"

    # +7 h shifts the 23:00 UTC run onto the next local day.
    await _run(repo, "e", RunStatus.SUCCEEDED, BASE + 23 * 3600, 1)
    shifted = await _get(c, "/api/stats/overview", since=BASE, until=until, days=7, tz_offset_s=7 * 3600)
    assert {d["day"]: d["runs"] for d in shifted["daily"]["days"]}["2026-10-02"] == 1

    daily = await _get(c, "/api/stats/daily", days=3)
    assert len(daily["days"]) == 3 and all(d["runs"] == 0 for d in daily["days"])


async def test_empty_db(ctx: tuple[httpx.AsyncClient, SqliteRepo]) -> None:
    c, _ = ctx
    body = await _get(c, "/api/stats/overview")
    assert body["recipes"] == [] and body["steps"] == []
    t = body["totals"]
    assert t["runs"] == 0 and t["cost_usd"] == 0 and t["failure_rate"] is None
    assert t["duration_s"] == {"avg": None, "p50": None, "p90": None}
    assert len(body["daily"]["days"]) == 30
    assert (await c.get("/api/stats/recipes")).status_code == 401


async def test_active_time_is_union_of_step_intervals(ctx: tuple[httpx.AsyncClient, SqliteRepo]) -> None:
    c, repo = ctx
    # Parallel lanes overlap (0-10, 5-20) and a retry ran two days later (2 d .. 2 d + 10): 20 + 10 s, not 2 days.
    await _run(repo, "u", RunStatus.SUCCEEDED, BASE, 2 * DAY + 10, [
        {"kind": "x", "t": (0, 10)}, {"kind": "y", "lane": Lane.CPU, "t": (5, 20)},
        {"kind": "z", "t": (2 * DAY, 2 * DAY + 10)},
        {"kind": "gate.approve", "lane": Lane.NONE, "t": (20, 2 * DAY)},
        {"kind": "w", "status": StepStatus.CANCELLED, "t": (30, 500)},
    ])
    u = (await _get(c, "/api/stats/recipes"))["recipes"][0]
    assert u["duration_s"] == {"avg": 30.0, "p50": 30.0, "p90": 30.0}
    assert u["wall_avg_s"] == pytest.approx(2 * DAY + 10)


async def test_prior_cost_and_aligned_overview(ctx: tuple[httpx.AsyncClient, SqliteRepo]) -> None:
    c, repo = ctx
    now = datetime.now(timezone.utc).timestamp()
    await _run(repo, "p", RunStatus.SUCCEEDED, now - 3600, 10, [
        {"kind": "claude.task", "lane": Lane.CLOUD, "t": (0, 5), "meta": {"cost_usd": 0.1, "prior_cost_usd": 0.05}},
        {"kind": "claude.task", "lane": Lane.CLOUD, "status": StepStatus.PENDING, "meta": {"prior_cost_usd": 0.02}},
    ])
    await _run(repo, "p", RunStatus.SUCCEEDED, now - 6 * DAY - 600, 10)
    t = (await _get(c, "/api/stats/recipes"))["totals"]
    assert t["cost_usd"] == pytest.approx(0.17)
    steps = (await _get(c, "/api/stats/steps"))["steps"]
    assert steps == [{"kind": "claude.task", "steps": 1, "failed": 0, "cached": 0, "cost_usd": 0.17, "total_s": 5.0,
                      "avg_s": 5.0}]
    for tz in (0, 7 * 3600, -5 * 3600):
        body = await _get(c, "/api/stats/overview", since=now - 7 * DAY, days=7, tz_offset_s=tz)
        assert body["totals"]["runs"] == sum(d["runs"] for d in body["daily"]["days"])
        assert body["totals"]["cost_usd"] == pytest.approx(sum(d["cost_usd"] for d in body["daily"]["days"]))
    for bad in ("nan", "inf"):
        assert (await c.get("/api/stats/overview", params={"since": bad}, headers=AUTH)).status_code == 400


class CostExec:
    """Spends `cost` per call; `fail(n)` may return a StepError for call n (1-based)."""

    def __init__(self, kind: str, cost: float, fail: Any = None) -> None:
        self.kind, self.lane, self.cost, self.fail, self.calls = kind, Lane.CLOUD, cost, fail, 0
        self.version = "1"

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.calls += 1
        if self.fail and (err := self.fail(self.calls)):
            raise err
        ref = await ctx.blobs.put_bytes(f"{self.kind}:{self.calls}:{inputs}".encode())
        return StepResult(outputs={"images": [ref]}, meta={"cost_usd": self.cost})


def _claude_err(cost: float, retryable: bool = True) -> StepError:
    return StepError("claude_error", "bad", retryable=retryable, detail={"cost_usd": cost})


ONE = """
id: one
steps:
  - {id: ask, kind: t.ask, params: {_auto_retry: 1}}
"""

PICKED = """
id: picked
steps:
  - {id: ask, kind: t.ask}
  - {id: pick, kind: gate.pick, in: {images: ask.images}, params: {k: 1}}
"""


async def test_runner_keeps_cost_of_failed_auto_retry(tmp_path: Path) -> None:
    from test_engine_runner import Env

    env = Env(tmp_path, [CostExec("t.ask", 0.1, lambda n: _claude_err(0.05) if n == 1 else None)], {"one": ONE})
    run_id = await env.run("one")
    ask = env.steps(run_id)["ask"]
    assert ask.status == StepStatus.SUCCEEDED and ask.meta["cost_usd"] == 0.1
    assert ask.meta["prior_cost_usd"] == pytest.approx(0.05) and meta_cost(ask.meta) == pytest.approx(0.15)

    (tmp_path / "b").mkdir()
    env2 = Env(tmp_path / "b", [CostExec("t.ask", 0.1, lambda n: _claude_err(0.05))], {"one": ONE})
    run_id = await env2.run("one")
    ask = env2.steps(run_id)["ask"]
    assert ask.status == StepStatus.FAILED and ask.meta["prior_cost_usd"] == pytest.approx(0.1)


async def test_runner_keeps_cost_across_retry_and_regenerate(tmp_path: Path) -> None:
    from test_engine_runner import Env, until

    ex = CostExec("t.ask", 0.1, lambda n: _claude_err(0.05, retryable=False) if n == 1 else None)
    env = Env(tmp_path, [ex], {"picked": PICKED})
    run_id = await env.run("picked")
    assert env.status(run_id) == RunStatus.FAILED
    await env.engine.retry_step(env.steps(run_id)["ask"].id)
    await until(lambda: env.steps(run_id)["pick"].status == StepStatus.AWAITING_HUMAN)
    ask = env.steps(run_id)["ask"]
    assert meta_cost(ask.meta) == pytest.approx(0.15)
    # Regenerating the gate's source resets its meta; the 0.15 already paid must survive and add the new 0.1.
    await env.engine.retry_step(ask.id)
    await until(lambda: ex.calls == 3 and env.steps(run_id)["pick"].status == StepStatus.AWAITING_HUMAN)
    assert meta_cost(env.steps(run_id)["ask"].meta) == pytest.approx(0.25)
    await env.engine.cancel_run(run_id)


MAPPED = """
id: mapped
steps:
  - {id: gen, kind: t.gen, params: {count: 3}}
  - {id: each, kind: t.each, in: {image: gen.images}, map: image}
"""


class SlowThird(CostExec):
    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        if self.calls == 2:
            self.calls += 1
            await asyncio.sleep(10)
        return await super().run(ctx, params, inputs)


async def test_cancelled_map_step_keeps_cost_of_finished_items(tmp_path: Path) -> None:
    from test_engine_runner import Env, FakeExec, until

    each = SlowThird("t.each", 0.1)
    env = Env(tmp_path, [FakeExec("t.gen"), each], {"mapped": MAPPED})
    run_id = await env.engine.start_run("mapped", {})
    await until(lambda: each.calls == 3)
    await env.engine.cancel_run(run_id)
    await asyncio.wait_for(env.engine.wait_idle(run_id), 5)
    step = env.steps(run_id)["each"]
    assert step.status == StepStatus.CANCELLED and step.meta["prior_cost_usd"] == pytest.approx(0.2)
    await env.engine.retry_step(step.id)
    await asyncio.wait_for(env.engine.wait_idle(run_id), 5)
    step = env.steps(run_id)["each"]
    assert step.status == StepStatus.SUCCEEDED and meta_cost(step.meta) == pytest.approx(0.5)
