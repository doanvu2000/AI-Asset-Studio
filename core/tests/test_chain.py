from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import GateResolution, RunStatus
from studio_core.engine.chain import map_inputs
from studio_core.engine.library import validate
from studio_core.engine.recipe import RecipeError
from test_engine_runner import Env, FakeExec, until

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"

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


def _env(tmp: Path) -> Env:
    return Env(tmp, [FakeExec("t.gen"), FakeExec("t.op")], {"gen": GEN, "genpick": GEN_PICK, "img": IMG})


async def _start(env: Env, recipe: str, then: list[dict[str, Any]], inputs: dict[str, Any] | None = None) -> str:
    return await env.engine.start_run(recipe, inputs or {}, {"$meta": {"source": "app", "then": then}}, title="Hero")


def _children(env: Env, parent: str) -> list[str]:
    return [r.id for r in env.repo.runs.values() if (r.params.get("$meta") or {}).get("chain_parent") == parent]


async def test_follow_up_starts_with_mapped_output_and_nests(tmp_path: Path) -> None:
    env = _env(tmp_path)
    then = [{"recipe_id": "img", "inputs": {"size": 32}, "map": {"image": "outputs.final[1]"},
             "then": [{"recipe_id": "img", "map": {"image": "inputs.image"}, "title": "Last"}]}]
    a = await _start(env, "gen", then)
    await asyncio.wait_for(env.engine.wait_idle(a), 5)
    assert env.status(a) == RunStatus.SUCCEEDED
    (b,) = _children(env, a)
    final = [h.hash for h in env.steps(a)["gen"].outputs["images"]]
    rb = env.repo.runs[b]
    assert rb.inputs["image"] == final[1] and rb.inputs["size"] == 32 and rb.title == "Hero"
    assert rb.params["$meta"]["source"] == "app"
    assert env.repo.runs[a].params["$meta"]["chain_runs"] == [b]
    await until(lambda: len(_children(env, b)) == 1 and env.status(_children(env, b)[0]) == RunStatus.SUCCEEDED)
    (c,) = _children(env, b)
    assert env.repo.runs[c].inputs["image"] == final[1] and env.repo.runs[c].title == "Last"
    assert "then" not in env.repo.runs[c].params["$meta"]


async def test_follow_up_waits_for_gate(tmp_path: Path) -> None:
    env = _env(tmp_path)
    a = await _start(env, "genpick", [{"recipe_id": "img", "map": {"image": "outputs.final"}}])
    await asyncio.wait_for(env.engine.wait_idle(a), 5)
    assert env.status(a) == RunStatus.AWAITING_HUMAN and _children(env, a) == []
    gate = env.steps(a)["pick"]
    cands = [b.hash for b in gate.inputs["images"]]
    await env.engine.resolve_gate(gate.id, GateResolution(picked=[cands[2]]))
    await asyncio.wait_for(env.engine.wait_idle(a), 5)
    (b,) = _children(env, a)
    assert env.repo.runs[b].inputs["image"] == cands[2]


async def test_failed_or_cancelled_run_starts_nothing(tmp_path: Path) -> None:
    env = _env(tmp_path)
    a = await _start(env, "genpick", [{"recipe_id": "img", "map": {"image": "outputs.final"}}])
    await asyncio.wait_for(env.engine.wait_idle(a), 5)
    await env.engine.cancel_run(a)
    assert env.status(a) == RunStatus.CANCELLED and _children(env, a) == []


async def test_bad_mapping_is_recorded_not_raised(tmp_path: Path) -> None:
    env = _env(tmp_path)
    a = await _start(env, "gen", [{"recipe_id": "img", "map": {"image": "outputs.final[5]"}},
                                  {"recipe_id": "img", "map": {"image": "outputs.final[0]"}}])
    await asyncio.wait_for(env.engine.wait_idle(a), 5)
    meta = env.repo.runs[a].params["$meta"]
    assert env.status(a) == RunStatus.SUCCEEDED
    assert len(meta["chain_runs"]) == 1 and "final[5] is empty" in meta["chain_error"]


@pytest.mark.parametrize("then, msg", [
    ([{"recipe_id": "nope", "map": {"image": "outputs.final"}}], "unknown recipe"),
    ([{"recipe_id": "img", "map": {"size": "outputs.final"}}], "not a file input"),
    ([{"recipe_id": "img", "map": {"image": "steps.gen.images"}}], "must be 'outputs"),
    ([{"recipe_id": "img", "map": {}}], "non-empty"),
    ({"recipe_id": "img"}, "must be a list"),
])
async def test_start_run_rejects_bad_chain(tmp_path: Path, then: Any, msg: str) -> None:
    env = _env(tmp_path)
    with pytest.raises(RecipeError, match=msg):
        await _start(env, "gen", then)
    assert env.repo.runs == {}


def test_map_inputs_single_list_and_inputs() -> None:
    outs = {"final": ["a", "b"], "sheet": ["s"]}
    assert map_inputs({"x": "outputs.final", "y": "outputs.sheet", "z": "outputs.final[-1]", "w": "inputs.image"},
                      outs, {"image": "i"}) == {"x": ["a", "b"], "y": "s", "z": "b", "w": "i"}
    with pytest.raises(ValueError):
        map_inputs({"x": "inputs.missing"}, outs, {})


def test_spine_character_image_recipe_lints() -> None:
    recipe, issues = validate((RECIPES / "spine-character-image.yaml").read_text(encoding="utf-8"), None)
    assert issues == [] and recipe is not None and recipe.inputs["image"]["type"] == "image"


async def test_blob_info_route(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", comfy_workflows_dir=tmp_path / "no-comfy")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, "t")
    auth = {"Authorization": "Bearer t"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/blobs", files={"file": ("hero.skel.json", b"{}", "application/json")},
                                  headers=auth)
            h = r.json()["hash"]
            info = (await client.get(f"/api/blobs/{h}/info", headers=auth)).json()
            assert info["hash"] == h and info["name"] == "hero.skel.json"
            assert (await client.get(f"/api/blobs/{'0' * 64}/info", headers=auth)).status_code == 404
            assert (await client.get(f"/api/blobs/{h}/info")).status_code == 401
