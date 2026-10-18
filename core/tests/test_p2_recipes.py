from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from studio_core.config import Settings
from studio_core.contracts import Lane, RunStatus, StepResult, StepStatus
from studio_core.engine.recipe import RecipeError, load_recipes, parse_recipe, substitute
from test_engine_runner import Env, FakeExec, until

RECIPES_DIR = Settings.model_fields["recipes_dir"].default
WORKFLOWS_DIR = Settings.model_fields["comfy_workflows_dir"].default


def test_lines_filter_substitution() -> None:
    inputs = {"t": "make it blue\n\n  add trim  \r\n", "n": 3}
    assert substitute("${inputs.t | lines}", inputs) == ["make it blue", "add trim"]
    assert substitute("${inputs.t|lines}. x${inputs.n}", inputs) == ["make it blue. x3", "add trim. x3"]
    assert substitute("${inputs.t | lines}", {"t": None}) == []
    assert substitute("${inputs.n}", inputs) == 3


MAP_PARAM = """
id: mp
inputs:
  img: {type: image}
  lines: {type: text}
steps:
  - id: edit
    kind: t.edit
    in: {image: inputs.img}
    params: {prompt: "${inputs.lines | lines}, keep shape", steps: 4}
    map_param: prompt
  - {id: fit, kind: t.op, in: {images: edit.images}}
outputs:
  final: fit.images
"""


@pytest.mark.parametrize("bad, msg", [
    ("map_param: nope", "not one of its params"),
    ("map_param: steps", "must be a list"),
    ("map: image", "both map and map_param"),
])
def test_map_param_validation(bad: str, msg: str) -> None:
    text = MAP_PARAM.replace("map_param: prompt", bad if bad.startswith("map_param") else f"map_param: prompt\n    {bad}")
    with pytest.raises(RecipeError, match=msg):
        parse_recipe(text.encode())


@pytest.mark.parametrize("params, msg", [
    ('{p: "${inputs.lines | lines}"}', "only allowed once"),
    ('{prompt: "${inputs.lines | upper}"}', "unknown filter"),
    ('{prompt: "${inputs.n | lines}"}', "needs a text input"),
    ('{prompt: "${inputs.lines | lines} ${inputs.lines | lines}"}', "must be a list or one"),
])
def test_list_filter_validation(params: str, msg: str) -> None:
    text = MAP_PARAM.replace('{prompt: "${inputs.lines | lines}, keep shape", steps: 4}', params) \
        .replace("  lines: {type: text}", "  lines: {type: text}\n  n: {type: int, default: 1}")
    if not params.startswith("{prompt"):
        text = text.replace("    map_param: prompt\n", "")
    with pytest.raises(RecipeError, match=msg):
        parse_recipe(text.encode())


def test_map_param_literal_list_ok() -> None:
    r = parse_recipe(MAP_PARAM.replace('"${inputs.lines | lines}, keep shape"', "[a, b]").encode())
    assert r.step("edit").map_param == "prompt"
    assert r.info().steps[0]["map_param"] == "prompt"


class ParamExec(FakeExec):
    def __init__(self, kind: str, lane: Lane = Lane.GPU, delay: float = 0.0):
        super().__init__(kind, lane, delay)
        self.params: list[dict[str, Any]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        res = await super().run(ctx, params, inputs)
        out = [await ctx.blobs.put_bytes(f"{params['prompt']}<{b.hash}".encode()) for b in res.outputs["images"]]
        return StepResult(outputs={"images": out}, meta={"prompt": params["prompt"]})


async def test_map_param_runs_once_per_line(tmp_path: Path) -> None:
    edit = ParamExec("t.edit")
    env = Env(tmp_path, [edit, FakeExec("t.op")], {"mp": MAP_PARAM})
    img = await env.blobs.put_bytes(b"src")
    run_id = await env.run("mp", {"img": img.hash, "lines": "blue\nred\n\ngreen"})
    assert env.status(run_id) == RunStatus.SUCCEEDED
    steps = env.steps(run_id)
    assert steps["edit"].params["prompt"] == ["blue, keep shape", "red, keep shape", "green, keep shape"]
    assert [p["prompt"] for p in edit.params] == ["blue, keep shape", "red, keep shape", "green, keep shape"]
    assert all(p["steps"] == 4 for p in edit.params)
    assert all(c["image"][0].hash == img.hash for c in edit.calls)
    assert len(steps["edit"].outputs["images"]) == 3 and len(steps["fit"].outputs["images"]) == 3
    assert [m["prompt"] for m in steps["edit"].meta["items"]] == ["blue, keep shape", "red, keep shape",
                                                                  "green, keep shape"]
    item_done = [e.data for e in env.bus.of("step.progress")
                 if e.step_id == steps["edit"].id and e.data["label"] and "/" in e.data["label"]]
    assert [d["value"] for d in item_done] == [1, 2, 3]


async def test_map_param_rejects_empty_lines(tmp_path: Path) -> None:
    env = Env(tmp_path, [ParamExec("t.edit"), FakeExec("t.op")], {"mp": MAP_PARAM})
    img = await env.blobs.put_bytes(b"src")
    with pytest.raises(RecipeError, match="no items"):
        await env.engine.start_run("mp", {"img": img.hash, "lines": " \n \n"})


MAP_ITEMS = """
id: mi
steps:
  - {id: gen, kind: t.gen, params: {count: 3}}
  - {id: each, kind: t.gpu, in: {image: gen.images}, map: image}
"""


async def test_map_progress_and_cancel_per_item(tmp_path: Path) -> None:
    each = FakeExec("t.gpu", Lane.GPU, delay=0.4)
    env = Env(tmp_path, [FakeExec("t.gen"), each], {"mi": MAP_ITEMS})
    run_id = await env.engine.start_run("mi", {})
    await until(lambda: len(each.calls) == 2)
    await asyncio.sleep(0.15)
    step_id = env.steps(run_id)["each"].id
    prog = [e.data for e in env.bus.of("step.progress") if e.step_id == step_id]
    assert all(d["max"] == 3 for d in prog)
    assert any(1 < d["value"] < 2 for d in prog)
    assert env.engine.scheduler.in_use(Lane.GPU) == 1
    await env.engine.cancel_run(run_id)
    assert env.status(run_id) == RunStatus.CANCELLED
    assert env.steps(run_id)["each"].status == StepStatus.CANCELLED
    assert len(each.calls) == 2 and each.cancel_seen
    assert env.engine.scheduler.in_use(Lane.GPU) == 0


def _real_recipes() -> dict[str, Any]:
    return load_recipes(RECIPES_DIR)


def test_shipped_recipes_load() -> None:
    rs = _real_recipes()
    assert {"concept-2d", "concept-2d-codex", "variants-2d", "sprite-8dir-2d", "sprite-8dir-2d-prompt"} <= set(rs)
    v = rs["variants-2d"]
    assert v.step("edit").map_param == "75:74.text"
    assert v.step("rmbg").map == "image"
    vals = v.coerce_inputs({"image": "h", "instructions": "make the potion blue\nadd golden trim"})
    prompts = v.resolve_params(v.step("edit"), vals)["75:74.text"]
    assert len(prompts) == 2 and prompts[0].startswith("make the potion blue.")
    assert set(v.outputs) == {"final", "sheet"}
    for rid in ("sprite-8dir-2d", "sprite-8dir-2d-prompt"):
        r = rs[rid]
        assert r.step("sheet").params["cols"] == 8 and r.step("rmbg").map == "image"
        assert "gần đúng" in r.description


QWEN_ANGLE_PROMPTS = [
    "same front view", "45 degrees to the right", "90 degrees to the right", "135 degrees to the right",
    "180 degrees", "135 degrees to the left", "90 degrees to the left", "45 degrees to the left",
]


@pytest.mark.skipif(not (WORKFLOWS_DIR / "index.json").is_file(), reason="ComfyUI workflows not installed")
def test_comfy_params_address_real_nodes_and_angle_order() -> None:
    from studio_core.adapters.comfy.executor import build_prompt, load_index

    index = load_index(WORKFLOWS_DIR)
    for r in _real_recipes().values():
        for s in r.steps:
            if not s.kind.startswith("comfy."):
                continue
            entry = index[s.kind.removeprefix("comfy.")]
            wf = json.loads((WORKFLOWS_DIR / entry["file"]).read_text(encoding="utf-8"))
            params = {k: (v[0] if isinstance(v, list) else v) for k, v in s.params.items()}
            params = {k: ("x" if isinstance(v, str) and "${" in v else v) for k, v in params.items()}
            names = ["studio/x.png"] if s.bindings else []
            build_prompt(wf, entry, params, names, "p")

    entry = index["multi_angles_qwen2511"]
    wf = json.loads((WORKFLOWS_DIR / entry["file"]).read_text(encoding="utf-8"))
    for rid in ("sprite-8dir-2d", "sprite-8dir-2d-prompt"):
        params = _real_recipes()[rid].step("angles").params
        ordered = []
        for out in entry["outputs"]:
            branch = wf[out["node"]]["inputs"]["images"][0].split(":")[1]
            ordered.append(next(v for k, v in params.items() if k.split(":")[1] == branch))
        assert len(ordered) == 8
        for text, want in zip(ordered, QWEN_ANGLE_PROMPTS):
            assert want in text, (rid, ordered)
