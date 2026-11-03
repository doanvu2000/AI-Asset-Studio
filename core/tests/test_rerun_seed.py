from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import httpx
import pytest

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import StepResult, StepStatus
from studio_core.engine import EngineStateError
from studio_core.engine.runner import SeedUnknownError
from test_engine_runner import Env, FakeExec

RECIPE = """
id: sd
name: Seeded
inputs:
  prompt: {type: text, default: "a"}
steps:
  - {id: gen, kind: t.seed, params: {seed: -1, p: "${inputs.prompt}"}}
  - {id: plain, kind: t.plain, in: {images: gen.images}}
"""


class SeedExec(FakeExec):
    """Draws a seed for -1 like retexture/trim; cacheable only once the seed is pinned (>= 0)."""

    def cacheable(self, params: dict[str, Any]) -> bool:
        return int(params.get("seed", -1)) >= 0

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        seed = int(params["seed"])
        if seed < 0:
            seed = random.randint(0, 2**31)
        out = [await ctx.blobs.put_bytes(f"img:{seed}:{params['p']}".encode())]
        return StepResult(outputs={"images": out}, meta={"seed": seed})


class CometExec(SeedExec):
    """Comfy-style: raw node.seed param key, recorded under meta.seeds."""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        seed = params.get("7.seed", random.randint(0, 2**31))
        out = [await ctx.blobs.put_bytes(f"c:{seed}".encode())]
        return StepResult(outputs={"images": out}, meta={"seeds": {"7.seed": seed}})

    def cacheable(self, params: dict[str, Any]) -> bool:
        return "7.seed" in params


class EachSeedExec(SeedExec):
    """Mapped, seeded: every item draws its own seed for -1 and records it in its item meta."""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        seed = int(params["seed"])
        if seed < 0:
            seed = random.randint(0, 2**31)
        src = inputs["image"][0].hash
        return StepResult(outputs={"images": [await ctx.blobs.put_bytes(f"m:{src}:{seed}".encode())]},
                          meta={"seed": seed})


MAP_RECIPE = """
id: sd
name: Mapped
steps:
  - {id: gen, kind: t.plain, params: {count: 3}}
  - {id: each, kind: t.each, in: {image: gen.images}, map: image, params: {seed: -1}}
"""


def _env(tmp_path: Path, recipe: str = RECIPE, execs: list[FakeExec] | None = None) -> Env:
    return Env(tmp_path, execs or [SeedExec("t.seed"), FakeExec("t.plain")], {"sd": recipe})


async def test_seed_used_recorded_for_random_seed(tmp_path: Path) -> None:
    env = _env(tmp_path)
    run = await env.run("sd")
    gen = env.steps(run)["gen"]
    assert isinstance(gen.meta["seed_used"], int) and gen.meta["seed_used"] == gen.meta["seed"] >= 0
    assert gen.params["seed"] == -1
    assert "seed_used" not in env.steps(run)["plain"].meta


async def test_fixed_seed_param_is_recorded(tmp_path: Path) -> None:
    env = _env(tmp_path, RECIPE.replace("seed: -1", "seed: 42"), [FakeExec("t.seed"), FakeExec("t.plain")])
    run = await env.run("sd")
    assert env.steps(run)["gen"].meta["seed_used"] == 42


async def test_rerun_same_reuses_seed_and_cache(tmp_path: Path) -> None:
    env = _env(tmp_path)
    r1 = await env.run("sd")
    used = env.steps(r1)["gen"].meta["seed_used"]
    r2 = await env.engine.rerun_run(r1, "same")
    await env.engine.wait_idle(r2)
    assert r2 != r1
    g1, g2 = env.steps(r1)["gen"], env.steps(r2)["gen"]
    assert g2.params["seed"] == used and g2.meta["seed_used"] == used
    assert g2.outputs == g1.outputs
    r3 = await env.engine.rerun_run(r2, "same")
    await env.engine.wait_idle(r3)
    g3 = env.steps(r3)["gen"]
    assert g3.status == StepStatus.CACHED and g3.cache_key == g2.cache_key and g3.meta["seed_used"] == used


async def test_rerun_new_gets_other_seed_and_no_cache(tmp_path: Path) -> None:
    env = _env(tmp_path)
    r1 = await env.run("sd")
    r2 = await env.engine.rerun_run(r1, "new")
    await env.engine.wait_idle(r2)
    g1, g2 = env.steps(r1)["gen"], env.steps(r2)["gen"]
    assert g2.status == StepStatus.SUCCEEDED and g2.cache_key is None
    assert g2.meta["seed_used"] != g1.meta["seed_used"]
    assert g2.outputs != g1.outputs


async def test_rerun_keeps_inputs_and_title(tmp_path: Path) -> None:
    env = _env(tmp_path)
    r1 = await env.run("sd", {"prompt": "castle"}, title="T")
    r2 = await env.engine.rerun_run(r1)
    await env.engine.wait_idle(r2)
    run2 = env.repo.runs[r2]
    assert run2.inputs == env.repo.runs[r1].inputs and run2.title == "T" and run2.recipe_id == "sd"
    assert env.steps(r2)["gen"].params["p"] == "castle"


async def test_rerun_without_seed_steps(tmp_path: Path) -> None:
    recipe = "id: sd\nname: N\nsteps:\n  - {id: a, kind: t.plain, params: {count: 1}}\n"
    env = _env(tmp_path, recipe, [FakeExec("t.plain")])
    r1 = await env.run("sd")
    for mode in ("new", "same"):
        r = await env.engine.rerun_run(r1, mode)
        await env.engine.wait_idle(r)
        assert env.steps(r)["a"].status in (StepStatus.SUCCEEDED, StepStatus.CACHED)


async def test_rerun_same_comfy_style_seeds(tmp_path: Path) -> None:
    recipe = "id: sd\nname: N\nsteps:\n  - {id: a, kind: t.comet}\n"
    env = _env(tmp_path, recipe, [CometExec("t.comet")])
    r1 = await env.run("sd")
    seed = env.steps(r1)["a"].meta["seeds"]["7.seed"]
    assert env.steps(r1)["a"].meta["seed_used"] == seed
    r2 = await env.engine.rerun_run(r1, "same")
    await env.engine.wait_idle(r2)
    a2 = env.steps(r2)["a"]
    assert a2.params["7.seed"] == seed and a2.outputs == env.steps(r1)["a"].outputs
    r3 = await env.engine.rerun_run(r2, "same")
    await env.engine.wait_idle(r3)
    assert env.steps(r3)["a"].status == StepStatus.CACHED


async def test_rerun_same_pins_each_item_seed_of_mapped_step(tmp_path: Path) -> None:
    env = _env(tmp_path, MAP_RECIPE, [FakeExec("t.plain"), EachSeedExec("t.each")])
    r1 = await env.run("sd")
    e1 = env.steps(r1)["each"]
    seeds = [i["seed"] for i in e1.meta["items"]]
    assert len(set(seeds)) == 3
    r2 = await env.engine.rerun_run(r1, "same")
    await env.engine.wait_idle(r2)
    e2 = env.steps(r2)["each"]
    assert [i["seed"] for i in e2.meta["items"]] == seeds
    assert e2.outputs == e1.outputs


async def test_rerun_same_on_legacy_run_without_seed_errors(tmp_path: Path) -> None:
    env = _env(tmp_path)
    r1 = await env.run("sd")
    gen = env.steps(r1)["gen"]
    meta = {k: v for k, v in gen.meta.items() if k not in ("seed", "seed_used")}
    await env.repo.update_step(gen.id, meta=meta)
    with pytest.raises(SeedUnknownError, match="gen"):
        await env.engine.rerun_run(r1, "same")
    r2 = await env.engine.rerun_run(r1, "new")
    await env.engine.wait_idle(r2)


async def test_rerun_rejects_running_unknown_and_bad_mode(tmp_path: Path) -> None:
    env = _env(tmp_path, execs=[SeedExec("t.seed", delay=0.2), FakeExec("t.plain")])
    run = await env.engine.start_run("sd", {})
    with pytest.raises(EngineStateError):
        await env.engine.rerun_run(run)
    await env.engine.wait_idle(run)
    with pytest.raises(LookupError):
        await env.engine.rerun_run("nope")
    with pytest.raises(ValueError):
        await env.engine.rerun_run(run, "bogus")


TOKEN = "t0k"


class _Stub:
    def __init__(self, exc: Exception | None) -> None:
        self.exc = exc
        self.calls: list[tuple[str, str]] = []

    async def recover(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def rerun_run(self, run_id: str, seed: str = "new") -> str:
        self.calls.append((run_id, seed))
        if self.exc:
            raise self.exc
        return "new-id"


@pytest.mark.parametrize("exc,status,code", [
    (None, 201, None), (SeedUnknownError("x"), 409, "seed_unknown"),
    (EngineStateError("x"), 409, "conflict"), (LookupError("gone"), 404, "not_found")])
async def test_rerun_route(tmp_path: Path, exc: Exception | None, status: int, code: str | None) -> None:
    s = Settings(data_dir=tmp_path)
    for d in (s.blobs_dir, s.thumbs_dir, s.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    stub = _Stub(exc)
    app = build_app(s, TOKEN, engine=stub, load_plugins=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            h = {"Authorization": f"Bearer {TOKEN}"}
            r = await c.post("/api/runs/abc/rerun", json={"seed": "same"}, headers=h)
            assert r.status_code == status
            assert stub.calls == [("abc", "same")]
            if code:
                assert r.json()["error"]["code"] == code
            else:
                assert r.json() == {"run_id": "new-id"}
                assert (await c.post("/api/runs/abc/rerun", headers=h)).status_code == 201
                assert stub.calls[-1] == ("abc", "new")
                assert (await c.post("/api/runs/abc/rerun", json={"seed": "x"}, headers=h)).status_code == 422


async def test_rerun_keeps_gate_policy_and_source(tmp_path: Path) -> None:
    from studio_core.engine.runner import META_KEY

    env = _env(tmp_path)
    r1 = await env.engine.start_run("sd", {}, {META_KEY: {"source": "mcp", "gate_policy": "auto_first"}})
    await env.engine.wait_idle(r1)
    r2 = await env.engine.rerun_run(r1)
    await env.engine.wait_idle(r2)
    meta = env.repo.runs[r2].params[META_KEY]
    assert meta["gate_policy"] == "auto_first" and meta["source"] == "mcp"
