from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import StepError, WorkflowRunRow, WorkflowStatus as WS
from studio_core.engine.flow_batch import MAX_FLOW_BATCH, FlowBatchCreate, FlowBatchItem
from studio_core.engine.flows import FlowInvalid, FlowNotFound
from test_engine_runner import FakeExec, until
from test_w9_flows import FLAKY, GATED, LINEAR, RECIPES, SLOWF, FlowRepo, _env, _flake, _flow


class BatchRepo(FlowRepo):
    def __init__(self) -> None:
        super().__init__()
        self.batches: dict[str, dict[str, Any]] = {}

    async def get_project(self, project_id: str) -> None:
        return None

    async def create_workflow_batch(self, batch: dict[str, Any]) -> None:
        self.batches[batch["id"]] = dict(batch)

    async def get_workflow_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self.batches.get(batch_id)

    async def list_workflow_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        return sorted(self.batches.values(), key=lambda b: -b["created_at"])[:limit]

    async def workflow_runs_by_ids(self, ids: list[str]) -> list[WorkflowRunRow]:
        return [self.wruns[i].model_copy(deep=True) for i in ids if i in self.wruns]


def _benv(tmp: Path, flows: dict[str, dict[str, Any]], **kw: Any) -> Any:
    env = _env(tmp, flows, **kw)
    env.repo.__class__ = BatchRepo
    env.repo.batches = {}
    return env


def _body(fid: str, n: int, **kw: Any) -> FlowBatchCreate:
    return FlowBatchCreate(flow_id=fid, items=[FlowBatchItem(inputs={"n": i + 1}) for i in range(n)], **kw)


def _items(n: int) -> list[FlowBatchItem]:
    return [FlowBatchItem() for _ in range(n)]


async def _batch_done(env: Any, bid: str, status: str) -> dict[str, Any]:
    async def poll() -> None:
        while (await env.engine.flows.batches.detail(bid))["status"] != status:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), 5)
    await env.engine.flows.wait_idle()
    return await env.engine.flows.batches.detail(bid)


async def test_batch_starts_one_run_per_item_with_shared_inputs(tmp_path: Path) -> None:
    env = _benv(tmp_path, {"lin": LINEAR})
    d = await env.engine.flows.batches.start(
        FlowBatchCreate(flow_id="lin", title="Pack", shared_params={"n": 3},
                        items=[FlowBatchItem(), FlowBatchItem(inputs={"n": 2}, title="Two")]))
    assert d["total"] == 2 and [r["title"] for r in d["runs"]] == ["Pack #1", "Two"]
    d = await _batch_done(env, d["id"], "succeeded")
    assert d["counts"] == {"succeeded": 2}
    assert [env.repo.wruns[r["id"]].inputs["n"] for r in d["runs"]] == [3, 2]
    assert (await env.engine.flows.batches.list_all())[0]["id"] == d["id"]


async def test_batch_rejects_bad_requests_before_starting_anything(tmp_path: Path) -> None:
    img = _flow("imgf", [{"id": "a", "recipe_id": "img"}], {"image": {"type": "image"}})
    img["nodes"][0]["map"] = {"image": "workflow.inputs.image"}
    env = _benv(tmp_path, {"lin": LINEAR, "imgf": img})
    b = env.engine.flows.batches
    with pytest.raises(ValueError, match="at least one"):
        await b.start(FlowBatchCreate(flow_id="lin", items=[]))
    with pytest.raises(ValueError, match="limited to"):
        await b.start(_body("lin", MAX_FLOW_BATCH + 1))
    with pytest.raises(FlowNotFound):
        await b.start(_body("nope", 1))
    with pytest.raises(ValueError, match="item 2: .*no input zz"):
        await b.start(FlowBatchCreate(flow_id="lin", items=[FlowBatchItem(), FlowBatchItem(inputs={"zz": 1})]))
    with pytest.raises(ValueError, match="item 1: input 'image' is required"):
        await b.start(FlowBatchCreate(flow_id="imgf", items=_items(1)))
    with pytest.raises(FlowNotFound):
        await b.start(_body("lin", 1, project_id="nope"))
    assert not env.repo.wruns and not env.repo.batches and not env.repo.runs
    env.engine.flows.library.flows["lin"].nodes[0].recipe_id = "missing"
    with pytest.raises(FlowInvalid):
        await b.start(_body("lin", 1))


async def test_retry_failed_reruns_only_failed_runs(tmp_path: Path) -> None:
    env = _benv(tmp_path, {"flaky": FLAKY})
    env.execs["t.bad"].fail = lambda n: StepError("boom", "fails twice") if n <= 2 else None
    d = await env.engine.flows.batches.start(FlowBatchCreate(flow_id="flaky", items=_items(2)))
    await until(lambda: all(w.status == WS.FAILED for w in env.repo.wruns.values()) and len(env.repo.wruns) == 2)
    d = await _batch_done(env, d["id"], "failed")
    assert d["counts"] == {"failed": 2}
    skipped = d["runs"][0]["id"]
    await env.repo.update_workflow_run(skipped, status=WS.CANCELLED)
    out = await env.engine.flows.batches.retry_failed(d["id"])
    assert out["retried"] == 1
    await until(lambda: env.repo.wruns[d["runs"][1]["id"]].status == WS.SUCCEEDED)
    assert env.repo.wruns[skipped].status == WS.CANCELLED


async def test_cancel_stops_runs_and_is_idempotent(tmp_path: Path) -> None:
    env = _benv(tmp_path, {"slowf": SLOWF}, slow=5.0)
    d = await env.engine.flows.batches.start(FlowBatchCreate(flow_id="slowf", items=_items(3)))
    await until(lambda: any(s.status.value == "running" for s in env.repo.steps.values()))
    out = await env.engine.flows.batches.cancel(d["id"])
    assert out["status"] == "cancelled" and out["counts"] == {"cancelled": 3}
    again = await env.engine.flows.batches.cancel(d["id"])
    assert again["counts"] == {"cancelled": 3}
    await env.engine.flows.wait_idle()
    assert all(r.status.value == "cancelled" for r in env.repo.runs.values())


async def test_awaiting_gate_is_reported(tmp_path: Path) -> None:
    env = _benv(tmp_path, {"gated": GATED})
    d = await env.engine.flows.batches.start(FlowBatchCreate(flow_id="gated", items=_items(1)))
    d = await _batch_done(env, d["id"], "awaiting_human")
    assert d["counts"] == {"awaiting_human": 1}


async def test_batch_survives_core_restart(tmp_path: Path) -> None:
    env = _benv(tmp_path, {"slowf": SLOWF}, slow=5.0)
    d = await env.engine.flows.batches.start(FlowBatchCreate(flow_id="slowf", items=_items(2)))
    await until(lambda: any(s.status.value == "running" for s in env.repo.steps.values()))
    await env.engine.shutdown()
    env.execs["t.slow"].delay = 0.0
    env2 = env.restart()
    await env2.engine.recover()
    out = await _batch_done(env2, d["id"], "succeeded")
    assert out["counts"] == {"succeeded": 2}
    await env2.engine.shutdown()


async def test_flow_batches_api(tmp_path: Path) -> None:
    rdir, fdir = tmp_path / "recipes", tmp_path / "flows"
    rdir.mkdir()
    fdir.mkdir()
    for name, text in RECIPES.items():
        (rdir / f"{name}.yaml").write_text(text, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, flows_dir=fdir,
                        comfy_workflows_dir=tmp_path / "no-comfy")
    for dd in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        dd.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, "t", load_plugins=False)
    async with app.router.lifespan_context(app):
        for ex in (FakeExec("t.gen"), FakeExec("t.op"), FakeExec("t.bad", fail=_flake)):
            app.state.registry.register(ex)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Authorization": "Bearer t"}) as c:
            assert (await c.post("/api/flows", json=FLAKY)).status_code == 201
            assert (await c.post("/api/flow-batches", json={"flow_id": "flaky", "items": []})).status_code == 400
            assert (await c.post("/api/flow-batches", json={"flow_id": "zz", "items": [{}]})).status_code == 404
            r = await c.post("/api/flow-batches", json={"flow_id": "flaky", "title": "B", "items": [{}, {}]})
            assert r.status_code == 201
            bid = r.json()["id"]

            async def wait(status: str) -> dict[str, Any]:
                d: dict[str, Any] = {}
                for _ in range(500):
                    d = (await c.get(f"/api/flow-batches/{bid}")).json()
                    if d["status"] == status:
                        return d
                    await asyncio.sleep(0.01)
                raise AssertionError(d)

            d = await wait("failed")
            assert d["title"] == "B" and len(d["runs"]) == 2 and d["counts"].get("failed") == 1
            assert [b["id"] for b in (await c.get("/api/flow-batches")).json()] == [bid]
            r = await c.post(f"/api/flow-batches/{bid}/retry-failed")
            assert r.status_code == 200 and r.json()["retried"] == 1
            assert (await wait("succeeded"))["counts"] == {"succeeded": 2}
            assert (await c.post(f"/api/flow-batches/{bid}/cancel")).json()["status"] == "succeeded"
            assert (await c.get("/api/flow-batches/nope")).status_code == 404
