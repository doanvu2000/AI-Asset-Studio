from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from blake3 import blake3

from studio_core.config import Settings
from studio_core.contracts import BlobRef, ExecutorRegistry, Lane
from studio_core.engine import register
from studio_core.engine.cache import cache_key
from studio_core.engine.recipe import RecipeError, load_recipes, parse_recipe, substitute
from studio_core.engine.scheduler import LaneScheduler

BASE = b"""
id: r
inputs:
  prompt: {type: text}
  n: {type: int, default: 2, min: 1, max: 4}
  flag: {type: bool, default: false}
  mode: {type: enum, options: [a, b], default: a}
steps:
  - id: gen
    kind: t.gen
    params: {p: "${inputs.prompt}, x", count: "${inputs.n}", nested: {f: "${inputs.flag}"}}
  - id: fit
    kind: t.op
    in: {images: gen.images}
outputs:
  final: fit.images
"""


def test_parse_and_substitute() -> None:
    r = parse_recipe(BASE)
    assert [s.id for s in r.steps] == ["gen", "fit"]
    assert r.step("fit").deps == ["gen"]
    assert r.hash == blake3(BASE).hexdigest()
    vals = r.coerce_inputs({"prompt": "cat", "n": "3", "flag": "true"})
    assert vals == {"prompt": "cat", "n": 3, "flag": True, "mode": "a"}
    p = r.resolve_params(r.step("gen"), vals)
    assert p == {"p": "cat, x", "count": 3, "nested": {"f": True}}
    assert substitute("${inputs.n}-${inputs.n}", {"n": 3}) == "3-3"


@pytest.mark.parametrize("given, msg", [
    ({}, "required"),
    ({"prompt": "x", "n": 9}, "max"),
    ({"prompt": "x", "n": "abc"}, "expected int"),
    ({"prompt": "x", "mode": "z"}, "not in"),
    ({"prompt": "x", "flag": "maybe"}, "expected bool"),
])
def test_coercion_errors(given: dict, msg: str) -> None:
    r = parse_recipe(BASE)
    with pytest.raises(RecipeError, match=msg):
        r.coerce_inputs(given)


@pytest.mark.parametrize("doc, msg", [
    (b"id: r\nsteps:\n  - {id: a, kind: k, in: {x: b.o}}\n  - {id: b, kind: k, in: {x: a.o}}\n", "cycle"),
    (b"id: r\nsteps:\n  - {id: a, kind: k, in: {x: zz.o}}\n", "unknown step"),
    (b"id: r\nsteps:\n  - {id: a, kind: k, map: y}\n", "map"),
    (b"id: r\nsteps:\n  - {id: a, kind: k, params: {p: '${inputs.nope}'}}\n", "unknown inputs"),
    (b"id: r\nsteps:\n  - {id: a, kind: k}\n  - {id: a, kind: k}\n", "duplicate"),
    (b"id: r\nsteps:\n  - {id: a, kind: k}\noutputs: {f: q.o}\n", "unknown step"),
    (b"id: r\ninputs: {t: {type: text}}\nsteps:\n  - {id: a, kind: k, in: {x: inputs.t}}\n", "image/mesh"),
])
def test_invalid_recipes(doc: bytes, msg: str) -> None:
    with pytest.raises(RecipeError, match=msg):
        parse_recipe(doc)


def test_shipped_recipes_load() -> None:
    recipes = load_recipes(Settings().recipes_dir)
    assert {"concept-2d", "concept-2d-codex", "sprite-8dir-glb"} <= recipes.keys()
    c = recipes["concept-2d"]
    assert [s.id for s in c.steps] == ["gen", "pick", "rmbg", "fit"]
    assert c.step("rmbg").map == "image"
    assert str(c.outputs["final"]) == "fit.images"
    s = recipes["sprite-8dir-glb"]
    assert s.coerce_inputs({"mesh": "abc"})["elevation"] == 30.0
    reg = ExecutorRegistry()
    register(reg)
    info = c.info(reg)
    assert {x["id"]: x["lane"] for x in info.steps}["pick"] == Lane.NONE.value
    assert {x["id"]: x["lane"] for x in info.steps}["gen"] is None


def test_cache_key() -> None:
    a = [BlobRef(hash="h1", mime="image/png", size=1)]
    b = [BlobRef(hash="h2", mime="image/png", size=1)]
    k1 = cache_key("k", "1", {"x": 1, "y": 2}, {"a": a, "b": b}, "")
    assert k1 == cache_key("k", "1", {"y": 2, "x": 1}, {"b": b, "a": a}, "")
    assert k1 != cache_key("k", "2", {"x": 1, "y": 2}, {"a": a, "b": b}, "")
    assert k1 != cache_key("k", "1", {"x": 1, "y": 2}, {"a": a, "b": b}, "m")
    assert k1 != cache_key("k", "1", {"x": 1, "y": 2}, {"a": b, "b": a}, "")


async def test_scheduler_fifo_and_none_lane() -> None:
    s = LaneScheduler({"gpu": 1})
    await s.acquire(Lane.GPU, 0)
    order: list[str] = []

    async def take(name: str, t: float) -> None:
        await s.acquire(Lane.GPU, t)
        order.append(name)
        s.release(Lane.GPU)

    late = asyncio.create_task(take("late", 2.0))
    await asyncio.sleep(0)
    early = asyncio.create_task(take("early", 1.0))
    await asyncio.sleep(0)
    await s.acquire(Lane.NONE, 0)
    s.release(Lane.GPU)
    await asyncio.gather(late, early)
    assert order == ["early", "late"]
    assert s.in_use(Lane.GPU) == 0

    await s.acquire(Lane.GPU, 0)
    waiter = asyncio.create_task(s.acquire(Lane.GPU, 1))
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert s.queued(Lane.GPU) == 0
    s.release(Lane.GPU)
    assert s.in_use(Lane.GPU) == 0


def test_load_recipes_skips_invalid(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("id: x\nsteps: []\n")
    (tmp_path / "ok.yaml").write_bytes(BASE)
    assert list(load_recipes(tmp_path)) == ["r"]
