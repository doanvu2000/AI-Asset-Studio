from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from studio_core.adapters import register as register_adapters
from studio_core.adapters.blender import BlenderAgentExecutor, BlenderScriptExecutor, Procedural3DExecutor
from studio_core.adapters.blender.agent import allowed_tools
from studio_core.adapters.blender.procedural import contract_issues, pick_best, write_prompt
from studio_core.adapters.claude import ClaudeReply
from studio_core.config import Settings
from studio_core.contracts import ExecutorRegistry, Lane, StepError
from studio_core.engine.gates import register_gates
from studio_core.engine.recipe import load_recipes
from studio_core.ops import register as register_ops

from test_p3_3d import make_glb
from test_ops_executors import FakeCtx
from test_p4_refs_brief import FakeQuery

GOOD = {"bbox_min": [-0.3, -0.3, 0.0], "bbox_max": [0.3, 0.3, 1.0], "tris": 2000, "mesh_objects": 3,
        "materials": ["wood"], "no_material": []}


class ScriptBlender:
    """Fake worker: `fail` maps a code substring to a worker error code; renders 2 tiny frames when asked."""

    version = "5.2"

    def __init__(self, reports: list[dict[str, Any]] | None = None, fail: dict[str, str] | None = None):
        self.calls: list[dict[str, Any]] = []
        self.reports = reports or []
        self.fail = fail or {}

    async def call(self, cmd: str, params: dict[str, Any], on_event: Any = None, timeout: float = 0) -> Any:
        assert cmd == "script"
        self.calls.append(params)
        for needle, code in self.fail.items():
            if needle in params["code"]:
                raise StepError(code, "ZeroDivisionError: division by zero",
                                detail={"trace": 'File "<studio_script>", line 3\nZeroDivisionError'})
        on_event({"value": 1, "max": 3, "label": "export"})
        Path(params["out"]).write_bytes(make_glb())
        res: dict[str, Any] = {**(self.reports.pop(0) if self.reports else GOOD), "out": params["out"],
                               "stdout": "", "total_s": 0.1}
        if params.get("render"):
            d = Path(params["render_dir"])
            d.mkdir(parents=True, exist_ok=True)
            frames = []
            for a in (0, 180):
                f = d / f"dir_{a:03d}.png"
                Image.new("RGBA", (8, 8), (200, 100, 50, 255)).save(f)
                frames.append({"angle": a, "file": str(f)})
            res["render"] = {"frames": frames, "res": 8, "engine": "BLENDER_EEVEE"}
        return res


def test_script_executor(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        fake = ScriptBlender()
        ex = BlenderScriptExecutor(Settings(data_dir=tmp_path), fake)  # type: ignore[arg-type]
        assert ex.lane == Lane.BLENDER and ex.cacheable({})
        res = await ex.run(ctx, {"code": "x = 1", "params": {"height": 2}, "render": {"count": 2, "res": 8},
                                 "name": "barrel"}, {})
        p = fake.calls[0]
        assert p["params"] == {"height": 2} and p["render"]["engine"] == "BLENDER_EEVEE" and "glb_in" not in p
        assert res.outputs["meshes"][0].name == "barrel.glb" and len(res.outputs["images"]) == 2
        report = json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
        assert report["tris"] == 2000 and "out" not in report
        assert res.meta["tris"] == 2000

        code_blob = await ctx.blobs.put_bytes(b"y = 2", name="s.py", mime="text/x-python")
        mesh = await ctx.blobs.put_bytes(make_glb(), name="m.glb")
        res = await ex.run(ctx, {}, {"code": [code_blob], "mesh": [mesh]})
        assert fake.calls[-1]["code"] == "y = 2" and fake.calls[-1]["glb_in"].endswith("in.glb")
        assert "images" not in res.outputs

        with pytest.raises(StepError, match="blender_bad_params"):
            await ex.run(ctx, {"code": "  "}, {})
        with pytest.raises(StepError, match="blender_bad_input"):
            await ex.run(ctx, {"code": "x"}, {"mesh": [mesh, mesh]})
        bad = BlenderScriptExecutor(Settings(), ScriptBlender(fail={"1/0": "blender_script_error"}))  # type: ignore[arg-type]
        with pytest.raises(StepError) as e:
            await bad.run(ctx, {"code": "x = 1/0"}, {})
        assert e.value.code == "blender_script_error" and not e.value.retryable
        assert "<studio_script>" in e.value.message

    asyncio.run(go())


def test_contract_issues() -> None:
    assert contract_issues(GOOD, 1.0) == []
    bad = {**GOOD, "bbox_min": [0.2, -0.3, -0.2], "bbox_max": [0.8, 0.3, 2.0], "no_material": ["Cube"]}
    issues = contract_issues(bad, 1.0)
    assert any("z=-0.200" in i for i in issues) and any("height is 2.200" in i for i in issues)
    assert any("center x" in i for i in issues) and any("Cube" in i for i in issues)
    assert contract_issues({}, 1.0) == ["no bounding box reported"]


def test_write_prompt_feeds_back_errors() -> None:
    assert "Write the script" in write_prompt("barrel", "", 1.0, None)
    p = write_prompt("barrel", "toon", 1.0, {"code": "CODE", "error": "Traceback boom"})
    assert "CODE" in p and "Traceback boom" in p and "toon" in p
    p = write_prompt("barrel", "", 1.0, {"code": "C", "contract": ["lowest point"],
                                         "qa": {"issues": ["no lid"], "fix_hint": "add a lid"}})
    assert "lowest point" in p and "reviewer: no lid" in p and "add a lid" in p


def test_pick_best() -> None:
    rounds = [{"round": 1, "accepted": False, "glb": "a", "qa": {"score": 6}},
              {"round": 2, "accepted": False, "error": "x"},
              {"round": 3, "accepted": False, "glb": "c", "qa": {"score": 4}}]
    assert pick_best(rounds)["round"] == 1  # type: ignore[index]
    rounds.append({"round": 4, "accepted": True, "glb": "d", "qa": {"score": 3}})
    assert pick_best(rounds)["round"] == 4  # type: ignore[index]
    assert pick_best([{"round": 1, "accepted": False}]) is None


def code(c: str) -> ClaudeReply:
    return ClaudeReply({"code": c, "notes": "n"}, "", {"cost_usd": 0.01, "seconds": 1})


def verdict(ok: bool, score: int, issues: list[str] | None = None) -> ClaudeReply:
    return ClaudeReply({"ok": ok, "score": score, "issues": issues or [], "fix_hint": "fix it"}, "",
                       {"cost_usd": 0.02, "seconds": 2})


def test_procedural_loop(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        floating = {**GOOD, "bbox_min": [-0.3, -0.3, 0.3], "bbox_max": [0.3, 0.3, 1.3]}
        fake = ScriptBlender(reports=[floating, GOOD], fail={"1/0": "blender_script_error"})
        q = FakeQuery(code("x = 1/0"), code("```python\nv = 2\n```"), verdict(True, 8), code("v = 3"),
                      verdict(True, 9))
        ex = Procedural3DExecutor(Settings(data_dir=tmp_path), fake, q)  # type: ignore[arg-type]
        res = await ex.run(ctx, {"idea": "barrel", "rounds": 3, "views": 4, "res": 64}, {})
        assert [c["code"] for c in fake.calls] == ["x = 1/0", "v = 2", "v = 3"]
        assert fake.calls[0]["render"]["count"] == 4 and fake.calls[0]["params"] == {"height": 1.0}
        h = res.meta["history"]
        assert h[0]["error"] and h[1]["contract"] and not h[1]["accepted"] and h[2]["accepted"]
        assert res.meta["best_round"] == 3 and res.meta["accepted"] and res.meta["score"] == 9
        assert res.meta["cost_usd"] == pytest.approx(0.07)
        writer_r2 = q.calls[1][0][-1]["text"]
        assert "x = 1/0" in writer_r2 and "<studio_script>" in writer_r2
        writer_r3 = q.calls[3][0][-1]["text"]
        assert "lowest point" in writer_r3 and "fix it" in writer_r3
        qa_content, qa_options = q.calls[2]
        assert sum(1 for b in qa_content if b["type"] == "image") == 2 and qa_options.model == "claude-opus-5-5"
        assert "Automatic checks already failed" in qa_content[-1]["text"]
        assert ctx.blobs.path(res.outputs["code"][0].hash).read_text() == "v = 3"
        assert len(res.outputs["images"]) == 2 and res.outputs["meshes"][0].name == "procedural.glb"
        rounds = json.loads(ctx.blobs.path(res.outputs["json"][0].hash).read_bytes())["rounds"]
        assert len(rounds) == 3 and "frames" not in rounds[1]

    asyncio.run(go())


def test_procedural_stops_on_first_accept_and_fails_without_mesh(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        q = FakeQuery(code("a = 1"), verdict(True, 7))
        ex = Procedural3DExecutor(Settings(), ScriptBlender(), q)  # type: ignore[arg-type]
        res = await ex.run(ctx, {"idea": "crate"}, {})
        assert res.meta["rounds"] == 1 and res.meta["accepted"]
        q = FakeQuery(code("a = 1"), verdict(False, 6), code("a = 2"), verdict(False, 7))
        ex = Procedural3DExecutor(Settings(), ScriptBlender(), q)  # type: ignore[arg-type]
        res = await ex.run(ctx, {"idea": "crate", "rounds": 3, "accept_score": 7}, {})
        assert res.meta["rounds"] == 2 and res.meta["accepted"] and res.meta["best_round"] == 2
        q = FakeQuery(code("1/0"), code("1/0 again"))
        ex = Procedural3DExecutor(Settings(), ScriptBlender(fail={"1/0": "blender_script_timeout"}), q)  # type: ignore[arg-type]
        with pytest.raises(StepError) as e:
            await ex.run(ctx, {"idea": "crate", "rounds": 2}, {})
        assert e.value.code == "procedural_failed" and len(e.value.detail["rounds"]) == 2
        with pytest.raises(StepError, match="procedural_bad_params"):
            await ex.run(ctx, {}, {})

    asyncio.run(go())


def test_blender_agent(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        seen: dict[str, Any] = {}

        async def query(content: list[dict[str, Any]], options: Any) -> ClaudeReply:
            seen["options"], seen["prompt"] = options, content[-1]["text"]
            out = re.search(r'filepath=r"([^"]+)"', options.system_prompt).group(1)  # type: ignore[union-attr]
            Path(out).write_bytes(make_glb())
            return ClaudeReply({"summary": "made a chair", "exported": True, "objects": ["Chair"]}, "", {})

        ex = BlenderAgentExecutor(Settings(data_dir=tmp_path), query)
        mesh = await ctx.blobs.put_bytes(make_glb(), name="in.glb")
        res = await ex.run(ctx, {"task": "make a chair", "tools": ["generate_3d", "rm_rf"],
                                 "mcp_command": "uvx-fake", "mcp_args": ["mcp-for-blender"]}, {"mesh": [mesh]})
        o = seen["options"]
        assert o.mcp_servers == {"blender": {"type": "stdio", "command": "uvx-fake", "args": ["mcp-for-blender"]}}
        assert o.tools == [] and o.strict_mcp_config and o.setting_sources == []
        assert o.allowed_tools == allowed_tools(["generate_3d"])
        assert "mcp__blender__generate_3d" in o.allowed_tools and not any("rm_rf" in t for t in o.allowed_tools)
        assert "input.glb" in seen["prompt"]
        assert res.outputs["meshes"][0].name == "agent.glb" and res.meta["summary"] == "made a chair"

        async def lazy(content: list[dict[str, Any]], options: Any) -> ClaudeReply:
            return ClaudeReply({"summary": "Blender not reachable", "exported": False, "objects": []}, "", {})

        with pytest.raises(StepError, match="blender_agent_no_output"):
            await BlenderAgentExecutor(Settings(), lazy).run(ctx, {"task": "x"}, {})
        res = await BlenderAgentExecutor(Settings(), lazy).run(ctx, {"task": "x", "require_mesh": False}, {})
        assert "meshes" not in res.outputs

    asyncio.run(go())


async def test_blender_agent_runs_one_job_at_a_time(tmp_path: Path) -> None:
    active = peak = 0

    async def query(content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return ClaudeReply({"summary": "", "exported": False, "objects": []}, "", {})

    ex = BlenderAgentExecutor(Settings(), query)
    ctxs = [FakeCtx(tmp_path / str(i)) for i in range(3)]
    for c in ctxs:
        c.job_dir.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.gather(*(ex.run(c, {"task": "t", "require_mesh": False}, {}) for c in ctxs))
    assert peak == 1


def test_p4_recipes_load_and_resolve(tmp_path: Path) -> None:
    recipes = load_recipes(Settings().recipes_dir)
    reg = ExecutorRegistry()
    register_adapters(reg, Settings(data_dir=tmp_path), None)
    register_ops(reg)
    register_gates(reg)
    for rid in ("concept-2d-brief", "sprite-8dir-3d-brief", "procedural-3d"):
        for s in recipes[rid].steps:
            reg.resolve(s.kind)

    c = recipes["concept-2d-brief"]
    assert c.step("gen").deps == ["brief"]
    vals = c.coerce_inputs({"idea": "potion"})
    assert vals["style"] == ""
    gen = c.resolve_params(c.step("gen"), vals)
    assert gen["57:27.text"].startswith("${steps.brief.text}, ")
    assert c.resolve_params(c.step("brief"), vals)["target"] == "concept-2d"

    s = recipes["sprite-8dir-3d-brief"]
    base = recipes["sprite-8dir-3d"]
    assert [x.id for x in s.steps] == ["brief"] + [x.id for x in base.steps]
    assert s.step("concept").deps == ["brief"]

    r7 = recipes["procedural-3d"]
    assert [x.id for x in r7.steps] == ["build", "validate", "turntable", "approve"]
    v7 = r7.coerce_inputs({"idea": "barrel", "rounds": 2})
    b = r7.resolve_params(r7.step("build"), v7)
    assert b["rounds"] == 2 and b["views"] == 6 and b["code_model"] == "opus"
    assert reg.resolve("claude.procedural_3d").lane == Lane.CLOUD
    assert r7.resolve_params(r7.step("validate"), v7)["require_textures"] is False
    with pytest.raises(ValueError):
        r7.coerce_inputs({"idea": "barrel", "rounds": 4})
