from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from studio_core.contracts import BlobRef, StepResult, StepStatus
from studio_core.engine import runner
from studio_core.engine.autopick import AUTO_PICK_KEY, WARN_NO_SCORE, WARN_PARTIAL, pick_by_qa
from studio_core.engine.runner import META_KEY
from test_engine_runner import Env, FakeExec

PICK = """
id: pick
name: Pick Recipe
steps:
  - {id: gen, kind: t.gen, params: {count: 3}}
  - {id: pick, kind: gate.pick, in: {images: gen.images}, params: {k: 1}}
  - {id: fit, kind: t.op, in: {images: pick.images}}
"""


def _refs(n: int) -> list[BlobRef]:
    return [BlobRef(hash=f"h{i}", mime="image/png", size=1) for i in range(n)]


def _scorer(table: dict[str, float | None]):
    async def score(b: BlobRef) -> float | None:
        return table.get(b.hash)
    return score


async def test_pick_highest() -> None:
    picked, scores, warn = await pick_by_qa(_refs(3), 1, _scorer({"h0": 40, "h1": 88, "h2": 61}))
    assert picked == ["h1"] and scores == {"h0": 40, "h1": 88, "h2": 61} and warn is None


async def test_tie_keeps_first() -> None:
    picked, _, warn = await pick_by_qa(_refs(3), 1, _scorer({"h0": 70, "h1": 70, "h2": 70}))
    assert picked == ["h0"] and warn is None
    picked, _, _ = await pick_by_qa(_refs(4), 2, _scorer({"h0": 50, "h1": 90, "h2": 90, "h3": 10}))
    assert picked == ["h1", "h2"]


async def test_no_scores_first_with_warning() -> None:
    picked, scores, warn = await pick_by_qa(_refs(3), 2, _scorer({}))
    assert picked == ["h0", "h1"] and scores == {} and warn == WARN_NO_SCORE


async def test_partial_scores_prefer_scored() -> None:
    picked, _, warn = await pick_by_qa(_refs(3), 1, _scorer({"h2": 10}))
    assert picked == ["h2"] and warn == WARN_PARTIAL


def _env(tmp_path: Path) -> Env:
    return Env(tmp_path, [FakeExec("t.gen"), FakeExec("t.op")], {"pick": PICK})


async def _start(env: Env, meta: dict[str, Any] | None) -> str:
    params = {META_KEY: meta} if meta else None
    run_id = await env.engine.start_run("pick", {}, params)
    await env.engine.wait_idle(run_id)
    return run_id


async def test_gate_waits_for_human_by_default(tmp_path: Path) -> None:
    env = _env(tmp_path)
    run_id = await _start(env, None)
    assert env.steps(run_id)["pick"].status == StepStatus.AWAITING_HUMAN


async def test_engine_auto_picks_best(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(tmp_path)
    best: dict[str, str] = {}

    async def fake_qa(blobs: Any, steps: Any, step: Any, ref: BlobRef) -> dict[str, Any]:
        ranked = sorted(r.hash for s in steps if s.key == "gen" for r in s.outputs["images"])
        best["hash"] = ranked[1]
        return {"score": 90 if ref.hash == ranked[1] else 50}

    monkeypatch.setattr(runner, "version_qa", fake_qa)
    run_id = await _start(env, {AUTO_PICK_KEY: True})
    steps = env.steps(run_id)
    assert steps["pick"].status == StepStatus.SUCCEEDED
    assert [b.hash for b in steps["pick"].outputs["images"]] == [best["hash"]]
    res = steps["pick"].meta["resolution"]
    assert res["auto"] is True and res["by"] == "qa" and "warning" not in res
    assert steps["fit"].status == StepStatus.SUCCEEDED


async def test_engine_without_qa_scores_takes_first_and_warns(tmp_path: Path) -> None:
    env = _env(tmp_path)
    run_id = await _start(env, {AUTO_PICK_KEY: True})
    steps = env.steps(run_id)
    first = steps["gen"].outputs["images"][0].hash
    assert [b.hash for b in steps["pick"].outputs["images"]] == [first]
    assert steps["pick"].meta["resolution"]["warning"] == WARN_NO_SCORE


async def test_auto_pick_leaves_gate_when_too_few_candidates(tmp_path: Path) -> None:
    env = Env(tmp_path, [FakeExec("t.gen"), FakeExec("t.op")],
              {"pick": PICK.replace("count: 3", "count: 1").replace("k: 1", "k: 2")})
    run_id = await _start(env, {AUTO_PICK_KEY: True})
    assert env.steps(run_id)["pick"].status == StepStatus.AWAITING_HUMAN


SEEDED = """
id: sd
name: Seeded
inputs:
  prompt: {type: text, default: "a"}
steps:
  - {id: gen, kind: t.seed, params: {seed: -1, p: "${inputs.prompt}"}}
  - {id: plain, kind: t.plain, in: {images: gen.images}}
  - {id: other, kind: t.seed, params: {seed: 5, p: "x"}}
"""


class SeedExec(FakeExec):
    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        out = [await ctx.blobs.put_bytes(f"img:{params['seed']}:{params['p']}".encode())]
        return StepResult(outputs={"images": out}, meta={"seed": params["seed"]})


async def test_seed_override_per_run(tmp_path: Path) -> None:
    env = Env(tmp_path, [SeedExec("t.seed"), FakeExec("t.plain")], {"sd": SEEDED})
    ids = []
    for i in range(4):
        rid = await env.engine.start_run("sd", {}, {META_KEY: {"seed_override": 100 + i}})
        await env.engine.wait_idle(rid)
        ids.append(rid)
    seeds = [env.steps(r)["gen"].params["seed"] for r in ids]
    assert seeds == [100, 101, 102, 103]
    assert [env.steps(r)["other"].params["seed"] for r in ids] == seeds
    assert "seed" not in env.steps(ids[0])["plain"].params
    assert len({env.steps(r)["gen"].outputs["images"][0].hash for r in ids}) == 4


async def test_explicit_step_seed_beats_override(tmp_path: Path) -> None:
    env = Env(tmp_path, [SeedExec("t.seed"), FakeExec("t.plain")], {"sd": SEEDED})
    rid = await env.engine.start_run("sd", {}, {"gen": {"seed": 9}, META_KEY: {"seed_override": 100}})
    await env.engine.wait_idle(rid)
    st = env.steps(rid)
    assert st["gen"].params["seed"] == 9 and st["other"].params["seed"] == 100


async def test_batch_items_carry_distinct_seeds(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from studio_core.api.routes import BatchCreate, BatchItem, start_batch

    env = Env(tmp_path, [SeedExec("t.seed"), FakeExec("t.plain")], {"sd": SEEDED})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=env.engine, repo=env.repo)))
    body = BatchCreate(recipe_id="sd", title="Biến thể", items=[
        BatchItem(title=f"seed {1000 + i}", meta={"seed_override": 1000 + i}) for i in range(5)])
    res = await start_batch(request, body)
    for rid in res["run_ids"]:
        await env.engine.wait_idle(rid)
    assert len(res["run_ids"]) == 5
    assert [env.steps(r)["gen"].params["seed"] for r in res["run_ids"]] == [1000, 1001, 1002, 1003, 1004]
    assert [env.repo.runs[r].params[META_KEY]["batch_index"] for r in res["run_ids"]] == [0, 1, 2, 3, 4]


class ComfyLike(FakeExec):
    entry = {"seeds": ["9:3.seed", "9:4.noise_seed"]}


async def test_override_reaches_catalog_and_workflow_seed_nodes(tmp_path: Path) -> None:
    recipe = """
id: nodes
name: Nodes
steps:
  - {id: strips, kind: trim.strips}
  - {id: wf, kind: comfy.fake}
  - {id: plain, kind: t.plain}
"""
    env = Env(tmp_path, [FakeExec("trim.strips"), ComfyLike("comfy.fake"), FakeExec("t.plain")], {"nodes": recipe})
    rid = await env.engine.start_run("nodes", {}, {META_KEY: {"seed_override": 77}})
    steps = {s.key: s for s in env.repo.steps.values() if s.run_id == rid}
    assert steps["strips"].params["seed"] == 77
    assert steps["wf"].params["9:3.seed"] == 77 and steps["wf"].params["9:4.noise_seed"] == 77
    assert "seed" not in steps["plain"].params
    await env.engine.cancel_run(rid)
