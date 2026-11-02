from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import (
    GateResolution,
    Lane,
    RunRow,
    RunStatus,
    StepError,
    WorkflowDef,
    WorkflowNodeState,
    WorkflowNodeStatus as NS,
    WorkflowRunRow,
    WorkflowStatus as WS,
)
from studio_core.engine.flows import (FLOW_NODE_KEY, FLOW_RUN_KEY, FlowInvalid, FlowLibrary, FlowStateError,
                                      valid_flow_id, validate_def)
from studio_core.engine.recipe import RecipeError
from test_engine_runner import Env, FakeExec, FakeRepo, until

GEN = """
id: gen
name: Gen
inputs:
  n: {type: int, default: 2}
steps:
  - {id: gen, kind: t.gen, params: {count: "${inputs.n}"}}
outputs:
  final: gen.images
"""

GEN_PICK = """
id: genpick
name: Gen pick
steps:
  - {id: gen, kind: t.gen, params: {count: 3}}
  - {id: pick, kind: gate.pick, in: {images: gen.images}}
outputs:
  final: pick.images
"""

IMG = """
id: img
name: From image
inputs:
  image: {type: image}
  size: {type: int, default: 64}
steps:
  - {id: fit, kind: t.op, in: {images: inputs.image}, params: {size: "${inputs.size}"}}
outputs:
  final: fit.images
"""

BAD = """
id: bad
name: Flaky
inputs:
  image: {type: image}
steps:
  - {id: fit, kind: t.bad, in: {images: inputs.image}}
outputs:
  final: fit.images
"""

SLOW = """
id: slow
name: Slow
steps:
  - {id: gen, kind: t.slow, params: {count: 1}}
outputs:
  final: gen.images
"""

MERGE = """
id: merge
name: Merge
inputs:
  a: {type: image}
  b: {type: image}
steps:
  - {id: fit, kind: t.op, in: {images: [inputs.a, inputs.b]}}
outputs:
  final: fit.images
"""

RECIPES = {"gen": GEN, "genpick": GEN_PICK, "img": IMG, "bad": BAD, "slow": SLOW, "merge": MERGE}


class FlowRepo(FakeRepo):
    def __init__(self) -> None:
        super().__init__()
        self.wruns: dict[str, WorkflowRunRow] = {}

    async def create_workflow_run(self, wr: WorkflowRunRow) -> None:
        self.wruns[wr.id] = wr.model_copy(deep=True)

    async def update_workflow_run(self, wr_id: str, **fields: Any) -> None:
        self.wruns[wr_id] = WorkflowRunRow.model_validate({**self.wruns[wr_id].model_dump(), **fields})

    async def get_workflow_run(self, wr_id: str) -> WorkflowRunRow | None:
        wr = self.wruns.get(wr_id)
        return wr.model_copy(deep=True) if wr else None

    async def list_workflow_runs(self, limit: int = 50, workflow_id: str | None = None, project_id: str | None = None,
                                 statuses: list[WS] | None = None) -> list[WorkflowRunRow]:
        rows = [w for w in self.wruns.values() if (workflow_id is None or w.workflow_id == workflow_id)
                and (project_id is None or w.project_id == project_id) and (not statuses or w.status in statuses)]
        return [w.model_copy(deep=True) for w in rows][:limit]

    async def runs_of_workflow_run(self, wr_id: str) -> list[RunRow]:
        return [r.model_copy(deep=True) for r in self.runs.values()
                if (r.params.get("$meta") or {}).get(FLOW_RUN_KEY) == wr_id]


def _flake(n: int) -> BaseException | None:
    return StepError("boom", "first try fails") if n == 1 else None


def _env(tmp: Path, flows: dict[str, dict[str, Any]] | None = None, slow: float = 2.0, op_delay: float = 0.0) -> Env:
    env = Env(tmp, [FakeExec("t.gen"), FakeExec("t.op", delay=op_delay), FakeExec("t.bad", fail=_flake),
                    FakeExec("t.slow", Lane.GPU, slow)], RECIPES, repo=FlowRepo())
    fdir = tmp / "flows"
    fdir.mkdir(exist_ok=True)
    env.settings.flows_dir = fdir
    env.engine.flows.library = FlowLibrary(fdir, env.settings.user_flows_dir)
    for d in (flows or {}).values():
        env.engine.flows.library.save(WorkflowDef.model_validate(d))
    return env


def _repo(env: Env) -> FlowRepo:
    assert isinstance(env.repo, FlowRepo)
    return env.repo


def _wr(env: Env, wr_id: str) -> WorkflowRunRow:
    return _repo(env).wruns[wr_id]


async def _settle(env: Env, wr_id: str, *statuses: WS, timeout: float = 5.0) -> WorkflowRunRow:
    want = statuses or (WS.SUCCEEDED, WS.FAILED, WS.CANCELLED)
    await until(lambda: _wr(env, wr_id).status in want, timeout)
    await env.engine.flows.wait_idle()
    return _wr(env, wr_id)


def _flow(fid: str, nodes: list[dict[str, Any]], inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": fid, "title": fid.title(), "inputs": inputs or {}, "nodes": nodes}


LINEAR = _flow("lin", [{"id": "a", "recipe_id": "gen", "map": {"n": "workflow.inputs.n"}},
                       {"id": "b", "recipe_id": "img", "title": "Fit", "inputs": {"size": 32},
                        "map": {"image": "a.outputs.final[1]"}}], {"n": {"type": "int", "default": 2}})
DIAMOND = _flow("dia", [{"id": "a", "recipe_id": "gen"},
                        {"id": "b", "recipe_id": "img", "map": {"image": "a.outputs.final[0]"}},
                        {"id": "c", "recipe_id": "img", "map": {"image": "a.outputs.final[1]"}},
                        {"id": "d", "recipe_id": "merge", "map": {"a": "b.outputs.final", "b": "c.outputs.final"}}])
FLAKY = _flow("flaky", [{"id": "a", "recipe_id": "gen", "inputs": {"n": 1}},
                        {"id": "b", "recipe_id": "bad", "map": {"image": "a.outputs.final"}},
                        {"id": "c", "recipe_id": "img", "map": {"image": "b.outputs.final"}}])
SLOWF = _flow("slowf", [{"id": "a", "recipe_id": "slow"},
                        {"id": "b", "recipe_id": "img", "map": {"image": "a.outputs.final"}}])
GATED = _flow("gated", [{"id": "a", "recipe_id": "genpick"},
                        {"id": "b", "recipe_id": "img", "map": {"image": "a.outputs.final"}}])


async def test_linear_flow_maps_outputs_and_tags_child_runs(tmp_path: Path) -> None:
    env = _env(tmp_path, {"lin": LINEAR})
    wr = await env.engine.flows.start("lin", {"n": 3}, title="Hero")
    wr = await _settle(env, wr.id)
    assert wr.status == WS.SUCCEEDED and wr.ended_at and wr.error is None
    a, b = wr.nodes["a"], wr.nodes["b"]
    assert a.status == b.status == NS.SUCCEEDED and a.attempts == b.attempts == 1
    ra, rb = env.repo.runs[a.run_id], env.repo.runs[b.run_id]
    assert ra.inputs["n"] == 3 and rb.inputs["size"] == 32
    assert rb.inputs["image"] == [h.hash for h in env.steps(ra.id)["gen"].outputs["images"]][1]
    assert rb.title == "Hero · Fit" and ra.title == "Hero · a"
    assert rb.params["$meta"][FLOW_RUN_KEY] == wr.id and rb.params["$meta"][FLOW_NODE_KEY] == "b"
    assert wr.def_snapshot["id"] == "lin" and "source" not in wr.def_snapshot
    seen = [e.data["status"] for e in env.bus.of("workflow.updated")]
    assert seen[0] == "queued" and seen[-1] == "succeeded" and "running" in seen


async def test_parallel_branches_start_together_and_join(tmp_path: Path) -> None:
    env = _env(tmp_path, {"dia": DIAMOND}, op_delay=0.1)
    wr = await env.engine.flows.start("dia")
    await until(lambda: _wr(env, wr.id).nodes["b"].run_id is not None)
    w = _wr(env, wr.id)
    assert w.nodes["c"].run_id is not None and w.nodes["d"].status == NS.PENDING
    wr = await _settle(env, wr.id)
    assert wr.status == WS.SUCCEEDED
    assert FakeExec.peak[Lane.CPU] >= 2
    assert len(env.repo.runs) == 4 and all(ns.attempts == 1 for ns in wr.nodes.values())
    rd = env.repo.runs[wr.nodes["d"].run_id]
    assert isinstance(rd.inputs["a"], str) and isinstance(rd.inputs["b"], str) and rd.inputs["a"] != rd.inputs["b"]


async def test_concurrent_advances_never_start_a_node_twice(tmp_path: Path) -> None:
    env = _env(tmp_path, {"dia": DIAMOND})
    wr = await env.engine.flows.start("dia")
    await asyncio.gather(*(env.engine.flows.advance(wr.id) for _ in range(20)))
    wr = await _settle(env, wr.id)
    assert wr.status == WS.SUCCEEDED and len(env.repo.runs) == 4


async def test_failed_node_blocks_downstream_then_retry_resumes(tmp_path: Path) -> None:
    env = _env(tmp_path, {"flaky": FLAKY})
    wr = await env.engine.flows.start("flaky")
    wr = await _settle(env, wr.id)
    assert wr.status == WS.FAILED and wr.error and wr.error.startswith("b:")
    assert wr.nodes["b"].status == NS.FAILED and wr.nodes["c"].status == NS.BLOCKED and wr.nodes["c"].run_id is None
    first_a, first_b = wr.nodes["a"].run_id, wr.nodes["b"].run_id
    with pytest.raises(ValueError, match="only failed"):
        await env.engine.flows.retry_node(wr.id, "a")
    with pytest.raises(LookupError):
        await env.engine.flows.retry_node(wr.id, "zz")
    out = await env.engine.flows.retry_node(wr.id, "b")
    assert out.status in (WS.RUNNING, WS.SUCCEEDED)
    wr = await _settle(env, wr.id)
    assert wr.status == WS.SUCCEEDED and wr.error is None
    assert wr.nodes["a"].run_id == first_a and wr.nodes["b"].run_id != first_b and wr.nodes["b"].attempts == 2
    assert env.repo.runs[first_b].status == RunStatus.FAILED and wr.nodes["c"].status == NS.SUCCEEDED


async def test_step_retry_inside_child_run_revives_the_workflow(tmp_path: Path) -> None:
    env = _env(tmp_path, {"flaky": FLAKY})
    wr = await _settle(env, (await env.engine.flows.start("flaky")).id)
    assert wr.status == WS.FAILED
    step = env.steps(wr.nodes["b"].run_id)["fit"]
    await env.engine.retry_step(step.id)
    wr = await _settle(env, wr.id, WS.SUCCEEDED)
    assert wr.nodes["b"].attempts == 1 and wr.nodes["c"].status == NS.SUCCEEDED


async def test_cancel_stops_children_and_starts_nothing_more(tmp_path: Path) -> None:
    env = _env(tmp_path, {"slowf": SLOWF})
    wr = await env.engine.flows.start("slowf")
    await until(lambda: env.repo.runs and env.steps(_wr(env, wr.id).nodes["a"].run_id)["gen"].status.value
                == "running")
    out = await env.engine.flows.cancel(wr.id)
    assert out.status == WS.CANCELLED and out.ended_at
    wr = await _settle(env, wr.id)
    assert wr.nodes["a"].status == NS.CANCELLED and wr.nodes["b"].status == NS.CANCELLED
    assert env.repo.runs[wr.nodes["a"].run_id].status == RunStatus.CANCELLED and len(env.repo.runs) == 1
    assert (await env.engine.flows.cancel(wr.id)).status == WS.CANCELLED
    env.execs["t.slow"].delay = 0.0
    await env.engine.flows.retry_node(wr.id, "a")
    wr = await _settle(env, wr.id, WS.SUCCEEDED)
    assert wr.nodes["a"].attempts == 2 and wr.nodes["b"].status == NS.SUCCEEDED


async def test_gate_in_child_run_makes_workflow_wait(tmp_path: Path) -> None:
    env = _env(tmp_path, {"gated": GATED})
    wr = await env.engine.flows.start("gated")
    wr = await _settle(env, wr.id, WS.AWAITING_HUMAN)
    assert wr.nodes["a"].status == NS.AWAITING_HUMAN and wr.nodes["b"].status == NS.PENDING
    gate = env.steps(wr.nodes["a"].run_id)["pick"]
    pick = gate.inputs["images"][2].hash
    await env.engine.resolve_gate(gate.id, GateResolution(picked=[pick]))
    wr = await _settle(env, wr.id, WS.SUCCEEDED)
    assert env.repo.runs[wr.nodes["b"].run_id].inputs["image"] == pick


async def test_restart_mid_node_reruns_the_interrupted_node(tmp_path: Path) -> None:
    env = _env(tmp_path, {"slowf": SLOWF})
    wr = await env.engine.flows.start("slowf")
    first = _wr(env, wr.id).nodes["a"].run_id
    await until(lambda: env.steps(first)["gen"].status.value == "running")
    await env.engine.shutdown()
    assert _wr(env, wr.id).status == WS.RUNNING
    env.execs["t.slow"].delay = 0.0
    env2 = env.restart()
    await env2.engine.recover()
    wr2 = await _settle(env2, wr.id)
    assert wr2.status == WS.SUCCEEDED and wr2.nodes["a"].attempts == 2 and wr2.nodes["a"].run_id != first
    assert env.repo.runs[first].status == RunStatus.INTERRUPTED and wr2.nodes["b"].status == NS.SUCCEEDED
    await env2.engine.shutdown()


async def test_unexpected_start_error_fails_the_node_not_the_driver(tmp_path: Path) -> None:
    env = _env(tmp_path, {"lin": LINEAR})
    real = env.engine.start_run

    async def broken(recipe_id: str, *a: Any, **kw: Any) -> str:
        if recipe_id == "img":
            raise OSError("disk full")
        return await real(recipe_id, *a, **kw)

    env.engine.start_run = broken  # type: ignore[method-assign]
    wr = await _settle(env, (await env.engine.flows.start("lin")).id)
    assert wr.status == WS.FAILED and wr.nodes["b"].status == NS.FAILED and "disk full" in (wr.error or "")
    env.engine.start_run = real  # type: ignore[method-assign]
    await env.engine.flows.retry_node(wr.id, "b")
    assert (await _settle(env, wr.id)).status == WS.SUCCEEDED


async def test_retry_refusal_still_saves_the_synced_state(tmp_path: Path) -> None:
    env = _env(tmp_path, {"lin": LINEAR})
    wr = await _settle(env, (await env.engine.flows.start("lin")).id)
    stale = wr.nodes["b"].model_copy(update={"status": NS.FAILED})
    await _repo(env).update_workflow_run(wr.id, nodes={**wr.nodes, "b": stale}, status=WS.FAILED)
    with pytest.raises(FlowStateError):
        await env.engine.flows.retry_node(wr.id, "b")
    assert _wr(env, wr.id).status == WS.SUCCEEDED and _wr(env, wr.id).nodes["b"].status == NS.SUCCEEDED


def _row(wid: str, flow: dict[str, Any], nodes: dict[str, WorkflowNodeState]) -> WorkflowRunRow:
    now = time.time()
    snap = WorkflowDef.model_validate(flow).model_dump(mode="json", exclude={"source"})
    return WorkflowRunRow(id=wid, workflow_id=flow["id"], status=WS.RUNNING, def_snapshot=snap, nodes=nodes,
                          created_at=now, updated_at=now)


async def test_cancel_adopts_and_stops_an_orphan_child(tmp_path: Path) -> None:
    env = _env(tmp_path, {"slowf": SLOWF})
    orphan = await env.engine.start_run("slow", {}, flow_meta={FLOW_RUN_KEY: "w3", FLOW_NODE_KEY: "a",
                                                               "workflow_attempt": 1})
    await _repo(env).create_workflow_run(_row("w3", SLOWF, {"a": WorkflowNodeState(), "b": WorkflowNodeState()}))
    out = await env.engine.flows.cancel("w3")
    assert out.status == WS.CANCELLED and out.nodes["a"].run_id == orphan
    assert env.repo.runs[orphan].status == RunStatus.CANCELLED and len(env.repo.runs) == 1


async def test_user_runs_cannot_pose_as_workflow_nodes(tmp_path: Path) -> None:
    env = _env(tmp_path, {"lin": LINEAR})
    tags = {FLOW_RUN_KEY: "w4", FLOW_NODE_KEY: "a", "workflow_attempt": 1}
    rid = await env.engine.start_run("gen", {}, {"$meta": {**tags, "source": "app"}})
    await asyncio.wait_for(env.engine.wait_idle(rid), 5)
    assert env.repo.runs[rid].params["$meta"] == {"source": "app"}
    # a tagged run whose steps were never written (crash inside start_run) is not adopted either
    stepless = RunRow(id="ghost", recipe_id="gen", recipe_hash="x", params={"$meta": tags}, created_at=time.time())
    await env.repo.create_run(stepless)
    await _repo(env).create_workflow_run(_row("w4", LINEAR, {"a": WorkflowNodeState(), "b": WorkflowNodeState()}))
    wr = await env.engine.flows.advance("w4")
    assert wr is not None and wr.nodes["a"].run_id not in (rid, "ghost")
    assert (await _settle(env, "w4")).status == WS.SUCCEEDED


async def test_recover_starts_ready_nodes_and_adopts_orphan_runs(tmp_path: Path) -> None:
    env = _env(tmp_path, {"lin": LINEAR, "dia": DIAMOND})
    done = await env.run("gen", {"n": 2})
    now = time.time()
    snap = WorkflowDef.model_validate(LINEAR).model_dump(mode="json", exclude={"source"})
    wr = WorkflowRunRow(id="w1", workflow_id="lin", title="W", status=WS.RUNNING, def_snapshot=snap, inputs={"n": 2},
                        nodes={"a": WorkflowNodeState(status=NS.RUNNING, run_id=done, attempts=1),
                               "b": WorkflowNodeState()}, created_at=now, updated_at=now)
    await _repo(env).create_workflow_run(wr)
    # second row: the crash hit after start_run but before node c was saved
    snap2 = WorkflowDef.model_validate(DIAMOND).model_dump(mode="json", exclude={"source"})
    img = env.steps(done)["gen"].outputs["images"][0].hash
    orphan = await env.run("img", {"image": img})
    env.repo.runs[orphan].params = {"$meta": {FLOW_RUN_KEY: "w2", FLOW_NODE_KEY: "c", "workflow_attempt": 1}}
    b_run = await env.run("img", {"image": img})
    wr2 = WorkflowRunRow(id="w2", workflow_id="dia", status=WS.RUNNING, def_snapshot=snap2,
                         nodes={"a": WorkflowNodeState(status=NS.SUCCEEDED, run_id=done, attempts=1),
                                "b": WorkflowNodeState(status=NS.SUCCEEDED, run_id=b_run, attempts=1),
                                "c": WorkflowNodeState(), "d": WorkflowNodeState()},
                         created_at=now, updated_at=now)
    await _repo(env).create_workflow_run(wr2)
    count = len(env.repo.runs)
    env2 = env.restart()
    await env2.engine.recover()
    w1 = await _settle(env2, "w1")
    w2 = await _settle(env2, "w2")
    assert w1.status == WS.SUCCEEDED and w1.nodes["b"].attempts == 1
    assert w2.status == WS.SUCCEEDED and w2.nodes["c"].run_id == orphan and w2.nodes["c"].attempts == 1
    assert len(env.repo.runs) == count + 2  # w1.b and w2.d only


async def test_start_rejects_bad_inputs_and_unknown_flow(tmp_path: Path) -> None:
    req = _flow("req", [{"id": "a", "recipe_id": "img", "map": {"image": "workflow.inputs.pic"}}],
                {"pic": "image"})
    env = _env(tmp_path, {"req": req})
    with pytest.raises(RecipeError, match="required"):
        await env.engine.flows.start("req", {})
    with pytest.raises(RecipeError, match="unknown blob"):
        await env.engine.flows.start("req", {"pic": "0" * 64})
    with pytest.raises(RecipeError, match="has no input"):
        await env.engine.flows.start("req", {"other": 1})
    with pytest.raises(LookupError):
        await env.engine.flows.start("nope")
    assert _repo(env).wruns == {}


def _issues(env: Env, d: dict[str, Any]) -> list[str]:
    return [i["message"] for i in validate_def(WorkflowDef.model_validate(d), env.engine.library.get)
            if i["level"] == "error"]


@pytest.mark.parametrize("nodes, inputs, msg", [
    ([{"id": "a", "recipe_id": "nope"}], {}, "unknown recipe"),
    ([{"id": "a", "recipe_id": "img", "map": {"zz": "workflow.inputs.p"}}], {"p": "image"}, "not an input"),
    ([{"id": "a", "recipe_id": "gen"}, {"id": "b", "recipe_id": "gen", "map": {"n": "a.outputs.final"}}], {},
     "only file inputs"),
    ([{"id": "a", "recipe_id": "img", "map": {"image": "x.outputs.final"}}], {}, "unknown node"),
    ([{"id": "a", "recipe_id": "img", "map": {"image": "a.outputs.final"}}], {}, "cannot feed itself"),
    ([{"id": "a", "recipe_id": "gen"}, {"id": "b", "recipe_id": "img", "map": {"image": "a.outputs.nope"}}], {},
     "no output 'nope'"),
    ([{"id": "a", "recipe_id": "img", "map": {"image": "b.outputs.final"}},
      {"id": "b", "recipe_id": "img", "map": {"image": "a.outputs.final"}}], {}, "cycle"),
    ([{"id": "a", "recipe_id": "img"}], {}, "required input 'image'"),
    ([{"id": "a", "recipe_id": "img", "map": {"image": "workflow.inputs.p"}}], {}, "no input 'p'"),
    ([{"id": "a", "recipe_id": "img", "map": {"image": "workflow.inputs.p"}}], {"p": "text"}, "is text"),
    ([{"id": "a", "recipe_id": "img", "map": {"image": "steps.a.images"}}], {}, "must be"),
    ([{"id": "a", "recipe_id": "gen"}, {"id": "a", "recipe_id": "gen"}], {}, "duplicate"),
    ([{"id": f"n{i}", "recipe_id": "gen"} for i in range(25)], {}, "at most 24"),
    ([], {}, "at least one"),
])
def test_validation_errors(tmp_path: Path, nodes: list[dict[str, Any]], inputs: dict[str, Any], msg: str) -> None:
    env = _env(tmp_path)
    errs = _issues(env, _flow("f", nodes, inputs))
    assert any(msg in e for e in errs), errs


@pytest.mark.parametrize("d, code, params", [
    (_flow("f", [{"id": "a", "recipe_id": "nope"}]), "unknown_recipe", {"recipe": "nope"}),
    (_flow("f", [{"id": "a", "recipe_id": "img", "map": {"zz": "workflow.inputs.p"}}], {"p": "image"}),
     "map_target_unknown", {"target": "zz", "recipe": "img"}),
    (_flow("f", [{"id": "a", "recipe_id": "gen"}, {"id": "b", "recipe_id": "gen", "map": {"n": "a.outputs.final"}}]),
     "outputs_need_file", {"target": "n"}),
    (_flow("f", [{"id": "a", "recipe_id": "img", "map": {"image": "x.outputs.final"}}]), "unknown_node", {"ref": "x"}),
    (_flow("f", [{"id": "a", "recipe_id": "img", "map": {"image": "a.outputs.final"}}]), "self_feed", {}),
    (_flow("f", [{"id": "a", "recipe_id": "gen"}, {"id": "b", "recipe_id": "img", "map": {"image": "a.outputs.nope"}}]),
     "recipe_no_output", {"name": "nope", "recipe": "gen"}),
    (_flow("f", [{"id": "a", "recipe_id": "img", "map": {"image": "b.outputs.final"}},
                 {"id": "b", "recipe_id": "img", "map": {"image": "a.outputs.final"}}]), "cycle", {"nodes": ["a", "b"]}),
    (_flow("f", [{"id": "a", "recipe_id": "img"}]), "required_unset", {"name": "image", "recipe": "img"}),
    (_flow("f", [{"id": "a", "recipe_id": "img", "map": {"image": "workflow.inputs.p"}}]), "workflow_no_input",
     {"name": "p"}),
    (_flow("f", [{"id": "a", "recipe_id": "img", "map": {"image": "workflow.inputs.p"}}], {"p": "text"}),
     "type_mismatch", {"source_type": "text", "target": "image"}),
    (_flow("f", [{"id": "a", "recipe_id": "img", "map": {"image": "steps.a.images"}}]), "bad_source",
     {"target": "image", "source": "steps.a.images"}),
    (_flow("f", [{"id": "a", "recipe_id": "gen"}, {"id": "a", "recipe_id": "gen"}]), "duplicate_node_id", {"id": "a"}),
    (_flow("f", [{"id": f"n{i}", "recipe_id": "gen"} for i in range(25)]), "too_many_nodes", {"max": 24, "count": 25}),
    (_flow("f", []), "no_nodes", {}),
    (_flow("Bad Id", [{"id": "a", "recipe_id": "gen"}]), "invalid_flow_id", {"id": "Bad Id"}),
    ({**_flow("f", [{"id": "a", "recipe_id": "gen"}]), "title": " "}, "title_empty", {}),
])
def test_validation_issue_codes(tmp_path: Path, d: dict[str, Any], code: str, params: dict[str, Any]) -> None:
    env = _env(tmp_path)
    issues = validate_def(WorkflowDef.model_validate(d), env.engine.library.get)
    hit = [i for i in issues if i["code"] == code]
    assert hit, issues
    assert all(i["message"] and isinstance(i["params"], dict) for i in issues)
    assert params.items() <= hit[0]["params"].items(), hit[0]


def test_valid_flows_and_builtin_sample(tmp_path: Path) -> None:
    env = _env(tmp_path)
    for d in (LINEAR, DIAMOND, FLAKY, GATED):
        assert _issues(env, d) == []
    builtin = FlowLibrary(Settings().flows_dir, tmp_path / "none")
    sample = builtin.get("concept-to-sprite")
    assert sample is not None and sample.source == "builtin"
    from studio_core.engine.library import RecipeLibrary
    lib = RecipeLibrary(Settings().recipes_dir, tmp_path / "none")
    assert validate_def(sample, lib.get) == []


def test_flow_ids_reject_newline_and_windows_names(tmp_path: Path) -> None:
    lib = FlowLibrary(tmp_path / "b", tmp_path / "u")
    for bad in ("abc\n", "con", "NUL", "com1", "Lpt9", "a.b", "1x"):
        assert not valid_flow_id(bad)
        with pytest.raises(RecipeError):
            lib.save(WorkflowDef.model_validate({**DIAMOND, "id": bad}))
    assert valid_flow_id("console") and valid_flow_id("com10")
    assert list((tmp_path / "u").glob("*")) == []


def test_loader_skips_misnamed_and_yml_files(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "x.yaml").write_text("id: y\ntitle: T\nnodes: [{id: a, recipe_id: gen}]\n", encoding="utf-8")
    (bdir / "z.yml").write_text("id: z\ntitle: T\nnodes: [{id: a, recipe_id: gen}]\n", encoding="utf-8")
    (bdir / "ok.yaml").write_text("id: ok\ntitle: T\nnodes: [{id: a, recipe_id: gen}]\n", encoding="utf-8")
    assert list(FlowLibrary(bdir, tmp_path / "u").flows) == ["ok"]
    assert "does not match the file name" in caplog.text


def test_validation_warnings(tmp_path: Path) -> None:
    env = _env(tmp_path)
    d = _flow("w", [{"id": "a", "recipe_id": "img", "inputs": {"size": 8},
                     "map": {"image": "workflow.inputs.p", "size": "workflow.inputs.s"}},
                    {"id": "b", "recipe_id": "gen", "map": {"n": "a.inputs.image"}}],
              {"p": {"type": "image", "required": False}, "s": {"type": "int", "default": "x"}})
    issues = validate_def(WorkflowDef.model_validate(d), env.engine.library.get)
    warn = [i["message"] for i in issues if i["level"] == "warning"]
    err = [i["message"] for i in issues if i["level"] == "error"]
    assert any("optional without a default" in m for m in warn) and any("both set and mapped" in m for m in warn)
    assert any("a.inputs.image is image but 'n' is int" in m for m in err) and any("default:" in m for m in err)


def test_library_save_override_delete(tmp_path: Path) -> None:
    bdir, udir = tmp_path / "b", tmp_path / "u"
    bdir.mkdir()
    (bdir / "x.yaml").write_text("id: x\ntitle: Built\nnodes:\n  - {id: a, recipe_id: gen}\n", encoding="utf-8")
    lib = FlowLibrary(bdir, udir)
    assert lib.get("x").source == "builtin"  # type: ignore[union-attr]
    saved = lib.save(WorkflowDef.model_validate({**DIAMOND, "id": "x", "inputs": {"p": "text"}}))
    assert saved.source == "override" and saved.inputs["p"].type == "text" and len(saved.nodes) == 4
    assert lib.delete("x") is True and lib.get("x").title == "Built"  # type: ignore[union-attr]
    with pytest.raises(LookupError):
        lib.delete("x")


def test_save_def_rejects_invalid(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with pytest.raises(FlowInvalid) as ei:
        env.engine.flows.save_def(WorkflowDef.model_validate(_flow("bad", [{"id": "a", "recipe_id": "nope"}])))
    assert ei.value.issues[0]["node"] == "a"


async def test_flows_api_end_to_end(tmp_path: Path) -> None:
    rdir, fdir = tmp_path / "recipes", tmp_path / "flows"
    rdir.mkdir()
    fdir.mkdir()
    for name, text in RECIPES.items():
        (rdir / f"{name}.yaml").write_text(text, encoding="utf-8")
    (fdir / "builtin-one.yaml").write_text("id: builtin-one\ntitle: B\nnodes:\n  - {id: a, recipe_id: gen}\n",
                                           encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, flows_dir=fdir,
                        comfy_workflows_dir=tmp_path / "no-comfy")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, "t", load_plugins=False)
    auth = {"Authorization": "Bearer t"}
    async with app.router.lifespan_context(app):
        for ex in (FakeExec("t.gen"), FakeExec("t.op"), FakeExec("t.bad", fail=_flake)):
            app.state.registry.register(ex)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers=auth) as c:
            assert (await c.get("/api/flows", headers={"Authorization": ""})).status_code == 401
            ids = {d["id"]: d["source"] for d in (await c.get("/api/flows")).json()}
            assert ids == {"builtin-one": "builtin"}
            r = await c.post("/api/flows", json=FLAKY)
            assert r.status_code == 201 and r.json()["source"] == "user" and r.json()["issues"] == []
            assert (await c.post("/api/flows", json=FLAKY)).status_code == 409
            bad = await c.post("/api/flows", json=_flow("broken", [{"id": "a", "recipe_id": "nope"}]))
            assert bad.status_code == 400 and bad.json()["error"]["detail"]["issues"][0]["field"] == "recipe_id"
            assert (await c.put("/api/flows/other", json=FLAKY)).status_code == 400
            assert (await c.put("/api/flows/flaky", json={**FLAKY, "title": "Flaky 2"})).json()["title"] == "Flaky 2"
            v = (await c.post("/api/flows/flaky/validate")).json()
            assert v == {"ok": True, "issues": []}
            v = (await c.post("/api/flows/flaky/validate", json=_flow("flaky", [{"id": "a", "recipe_id": "x"}]))).json()
            assert v["ok"] is False
            assert (await c.get("/api/flows/flaky")).json()["nodes"][1]["recipe_id"] == "bad"
            assert (await c.get("/api/flows/zz")).status_code == 404

            r = await c.post("/api/flows/flaky/runs", json={"title": "Run 1"})
            assert r.status_code == 201
            rid = r.json()["id"]

            async def status() -> str:
                return (await c.get(f"/api/flow-runs/{rid}")).json()["status"]

            for _ in range(500):
                if await status() == "failed":
                    break
                await asyncio.sleep(0.01)
            d = (await c.get(f"/api/flow-runs/{rid}")).json()
            assert d["status"] == "failed" and d["nodes"]["c"]["status"] == "blocked"
            kids = {k["node_id"]: k for k in d["children"]}
            assert kids["a"]["progress"] == 1.0 and "preview" in kids["a"] and kids["c"]["run_id"] is None
            assert (await c.post(f"/api/flow-runs/{rid}/nodes/a/retry")).status_code == 409
            assert (await c.post("/api/flows", json={**FLAKY, "id": "abc\n"})).status_code == 400
            assert (await c.post("/api/flows", json={**FLAKY, "id": "Con"})).status_code == 400
            assert (await c.post(f"/api/flow-runs/{rid}/nodes/b/retry")).status_code == 200
            for _ in range(500):
                if await status() == "succeeded":
                    break
                await asyncio.sleep(0.01)
            assert await status() == "succeeded"
            assert (await c.post(f"/api/flow-runs/{rid}/cancel")).json()["status"] == "succeeded"
            runs = (await c.get("/api/flow-runs", params={"workflow_id": "flaky"})).json()
            assert [x["id"] for x in runs] == [rid] and runs[0]["nodes"]["b"]["attempts"] == 2
            assert (await c.get("/api/flow-runs", params={"workflow_id": "other"})).json() == []
            assert (await c.get("/api/flow-runs/nope")).status_code == 404
            assert (await c.post("/api/flows/zz/runs", json={})).status_code == 404

            assert (await c.delete("/api/flows/builtin-one")).status_code == 400
            assert (await c.delete("/api/flows/flaky")).json() == {"ok": True, "restored_builtin": False}
            assert (await c.delete("/api/flows/flaky")).status_code == 404
