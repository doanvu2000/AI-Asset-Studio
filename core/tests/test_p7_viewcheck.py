from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from studio_core.adapters.comfy import CheckedViewsExecutor
from studio_core.contracts import ExecutorRegistry, GateResolution, Lane, Outputs, RunStatus, StepError, StepResult, \
    StepStatus
from studio_core.ops.viewcheck import ViewCheckConfig, check_views, silhouette
from test_adapters_fakes import FakeCtx
from test_engine_runner import Env, FakeExec

QWEN_OUTPUTS = ["373", "376", "379", "381"]
VIEWS = [
    {"name": "left", "output": "376", "prompt_key": "1:2.prompt", "prompt": "turn right", "slot": "330.left"},
    {"name": "back", "output": "379", "prompt_key": "1:3.prompt", "prompt": "back view", "slot": "330.back"},
    {"name": "right", "output": "381", "prompt_key": "1:4.prompt", "prompt": "turn left", "slot": "330.right"},
]


def figure(width: int = 120, lying: bool = False, alpha: bool = False, size: int = 512) -> Image.Image:
    """Chibi-ish silhouette: big head over a body, 330 px tall, centered on white (or transparent)."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, top = size // 2, 90
    d.ellipse((cx - 70, top, cx + 70, top + 140), fill=(120, 60, 160, 255))
    d.rectangle((cx - width // 2, top + 130, cx + width // 2, top + 300), fill=(90, 40, 140, 255))
    d.rectangle((cx - 40, top + 300, cx + 40, top + 330), fill=(40, 30, 30, 255))
    d.line((cx + width // 2 + 10, top + 60, cx + width // 2 + 10, top + 320), fill=(110, 70, 30, 255), width=8)
    if lying:
        layer = layer.rotate(90, resample=Image.Resampling.BICUBIC)
    if alpha:
        return layer
    bg = Image.new("RGBA", (size, size), (248, 248, 246, 255))
    bg.alpha_composite(layer)
    return bg


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_upright_views_pass_lying_fails() -> None:
    front = figure()
    res = check_views(front, [figure(width=70), figure(), figure(width=70, lying=True)])
    assert [r["ok"] for r in res] == [True, True, False]
    assert {"wide", "height", "rotated"} <= set(res[2]["reasons"])
    assert res[0]["scores"]["height"] == pytest.approx(1.0, abs=0.03)


def test_alpha_input_and_other_failures() -> None:
    front = figure(alpha=True)
    assert check_views(front, [figure(width=70, alpha=True)])[0]["ok"]
    empty = Image.new("RGBA", (512, 512), (255, 255, 255, 255))
    assert check_views(front, [empty])[0]["reasons"] == ["empty"]
    small = figure(width=70).resize((256, 256)).crop((-128, -128, 384, 384))
    assert "height" in check_views(figure(), [small])[0]["reasons"]
    clipped = figure(width=70).crop((0, 150, 512, 662))
    assert "clipped" in check_views(figure(), [clipped])[0]["reasons"]
    loose = ViewCheckConfig.from_params({"max_aspect": "10", "height_tol": 1, "rot_margin": 1})
    assert check_views(figure(), [figure(width=70, lying=True)], loose)[0]["ok"]
    with pytest.raises(ValueError):
        check_views(empty, [figure()])


def test_silhouette_ignores_specks() -> None:
    img = figure()
    ImageDraw.Draw(img).point([(3, 3), (508, 500)], fill=(0, 0, 0, 255))
    assert silhouette(img).bbox == silhouette(figure()).bbox


class FakeQwen:
    """Stands in for comfy.multi_angles_qwen2511: one image per kept output, good or lying per a script."""
    kind = "comfy.fake_qwen"
    lane = Lane.GPU
    version = "1"
    entry = {"outputs": [{"node": n} for n in QWEN_OUTPUTS]}

    def __init__(self, script: dict[str, list[bool]]):
        self.script = script
        self.calls: list[dict[str, Any]] = []

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Outputs) -> StepResult:
        self.calls.append(params)
        kept = [n for n in QWEN_OUTPUTS if n not in params["_drop_outputs"]]
        refs = []
        for node in kept:
            turn = sum(1 for c in self.calls if node not in c["_drop_outputs"]) - 1
            plan = self.script.get(node, [True])
            good = plan[min(turn, len(plan) - 1)]
            refs.append(await ctx.blobs.put_bytes(png(figure(width=70, lying=not good)), name=f"{node}.png"))
        return StepResult(outputs={"images": refs}, meta={"seeds": {"s": len(self.calls)}, "seconds": 0.1})


async def run_views(tmp_path: Path, script: dict[str, list[bool]], **params: Any
                    ) -> tuple[FakeQwen, FakeCtx, StepResult]:
    qwen = FakeQwen(script)
    reg = ExecutorRegistry()
    reg.register(qwen)
    ex = CheckedViewsExecutor(reg.resolve)
    ctx = FakeCtx(tmp_path)
    front = await ctx.blobs.put_bytes(png(figure()), name="front.png")
    res = await ex.run(ctx, {"workflow": "fake_qwen", "views": VIEWS, "_timeout_s": 60, **params}, {"image": [front]})
    return qwen, ctx, res


def report_of(ctx: FakeCtx, res: StepResult) -> dict[str, Any]:
    return json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_text(encoding="utf-8"))


async def test_all_good_first_try(tmp_path: Path) -> None:
    qwen, ctx, res = await run_views(tmp_path, {})
    assert len(qwen.calls) == 1
    assert qwen.calls[0]["_drop_outputs"] == ["373"] and qwen.calls[0]["_timeout_s"] == 60
    assert qwen.calls[0]["1:2.prompt"] == "turn right"
    assert [r.name for r in res.outputs["images"]] == ["376.png", "379.png", "381.png"]
    assert res.meta["regenerated"] == 0 and res.meta["failed"] == []
    assert report_of(ctx, res)["drop_inputs"] == []


async def test_bad_view_regenerated_until_good(tmp_path: Path) -> None:
    qwen, ctx, res = await run_views(tmp_path, {"376": [False, False, True]})
    assert len(qwen.calls) == 3
    retry = qwen.calls[1]
    assert sorted(retry["_drop_outputs"]) == ["373", "379", "381"]
    assert retry["1:2.prompt"].startswith("turn right") and "upright" in retry["1:2.prompt"]
    assert "1:3.prompt" not in retry
    assert len(res.outputs["images"]) == 3 and res.outputs["images"][0].name == "376.png"
    assert res.meta["attempts"] == {"left": 3, "back": 1, "right": 1} and res.meta["regenerated"] == 2
    left = report_of(ctx, res)["views"][0]
    assert left["ok"] and [a["ok"] for a in left["attempts"]] == [False, False, True]
    assert "rotated" in left["attempts"][0]["reasons"]


async def test_still_bad_view_is_dropped_with_its_slot(tmp_path: Path) -> None:
    qwen, ctx, res = await run_views(tmp_path, {"381": [False]}, attempts=3)
    assert len(qwen.calls) == 3
    assert [r.name for r in res.outputs["images"]] == ["376.png", "379.png"]
    rep = report_of(ctx, res)
    assert rep["dropped"] == ["right"] and rep["drop_inputs"] == ["330.right"] and rep["failed"] == ["right"]


async def test_fallback_keep_and_fail(tmp_path: Path) -> None:
    _, _, res = await run_views(tmp_path / "keep", {"379": [False]}, attempts=2, on_fail="keep")
    assert len(res.outputs["images"]) == 3 and res.meta["failed"] == ["back"]
    with pytest.raises(StepError) as e:
        await run_views(tmp_path / "fail", {"379": [False]}, attempts=2, on_fail="fail")
    assert e.value.code == "views_rejected" and e.value.detail["failed"] == ["back"]
    with pytest.raises(StepError) as e:
        await run_views(tmp_path / "min", {n: [False] for n in ("376", "379", "381")}, attempts=1)
    assert e.value.code == "views_rejected"


async def test_bad_params(tmp_path: Path) -> None:
    with pytest.raises(StepError, match="not outputs"):
        await run_views(tmp_path / "a", {}, views=[{**VIEWS[0], "output": "999"}])
    with pytest.raises(StepError, match="on_fail"):
        await run_views(tmp_path / "b", {}, on_fail="maybe")


OPTIONAL_GATE = """
id: og
inputs:
  review: {type: bool, default: false}
steps:
  - {id: gen, kind: t.gen, params: {count: 2}}
  - {id: ok, kind: gate.approve, in: {images: gen.images}, params: {enabled: "${inputs.review}"}}
  - {id: pick, kind: gate.pick, in: {images: ok.images}, params: {k: 1, enabled: "${inputs.review}"}}
  - {id: fit, kind: t.op, in: {images: pick.images}}
"""


async def test_disabled_gates_resolve_themselves(tmp_path: Path) -> None:
    env = Env(tmp_path, [FakeExec("t.gen"), FakeExec("t.op")], {"og": OPTIONAL_GATE})
    run_id = await env.run("og")
    assert env.status(run_id) == RunStatus.SUCCEEDED
    steps = env.steps(run_id)
    assert steps["ok"].status == StepStatus.SUCCEEDED and steps["ok"].meta["resolution"]["auto"] is True
    assert steps["ok"].outputs["images"] == steps["gen"].outputs["images"]
    assert steps["pick"].outputs["images"] == steps["gen"].outputs["images"][:1]
    assert not env.bus.of("gate.awaiting")

    run_id = await env.run("og", {"review": True})
    assert env.status(run_id) == RunStatus.AWAITING_HUMAN
    await env.engine.resolve_gate(env.steps(run_id)["ok"].id, GateResolution(approved=True))
    await env.engine.wait_idle(run_id)
    assert env.steps(run_id)["pick"].status == StepStatus.AWAITING_HUMAN


def test_mv_drop_inputs_param_rejects_bad_targets() -> None:
    from studio_core.adapters.comfy.executor import build_prompt

    wf = {"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
          "2": {"class_type": "LoadImage", "inputs": {"image": "b.png"}},
          "3": {"class_type": "Join", "inputs": {"a": ["1", 0], "b": ["2", 0]}},
          "4": {"class_type": "Save", "inputs": {"x": ["3", 0], "filename_prefix": "o"}}}
    entry = {"images": [{"key": "1.image"}, {"key": "2.image"}], "outputs": [{"node": "4"}]}
    prompt, _ = build_prompt(wf, entry, {"_drop_inputs": ["3.a"]}, ["studio/only.png"], "p")
    assert prompt["2"]["inputs"]["image"] == "studio/only.png" and prompt["1"]["inputs"]["image"] == "a.png"
    for bad in (["3.zz"], ["9.a"], "3.a"):
        with pytest.raises(StepError):
            build_prompt(wf, entry, {"_drop_inputs": bad}, ["studio/only.png"], "p")
