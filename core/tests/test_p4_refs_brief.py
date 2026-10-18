from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from studio_core.adapters.claude import BRIEF_SCHEMA, ClaudeBriefExecutor, ClaudeReply, ClaudeTaskExecutor
from studio_core.config import Settings
from studio_core.contracts import Lane, Outputs, RunStatus, StepError, StepResult, StepStatus
from studio_core.engine.recipe import RecipeError, find_step_refs, parse_recipe, ref_value, substitute_refs

from test_engine_runner import Env
from test_ops_executors import FakeCtx


def test_find_and_substitute_refs() -> None:
    params = {"a": "${steps.brief.json.prompt}, white bg", "b": ["${steps.brief.json.n}"], "c": 3,
              "d": "${steps.w.text}"}
    found = find_step_refs(params)
    assert [(s, o, p) for _, s, o, p in found] == [("brief", "json", ("prompt",)), ("brief", "json", ("n",)),
                                                   ("w", "text", ())]
    values = {"${steps.brief.json.prompt}": "red potion", "${steps.brief.json.n}": 4, "${steps.w.text}": "hi"}
    assert substitute_refs(params, values) == {"a": "red potion, white bg", "b": [4], "c": 3, "d": "hi"}
    assert substitute_refs("n=${steps.brief.json.n}", values) == "n=4"


def test_ref_value() -> None:
    raw = json.dumps({"prompt": "p", "palette": ["red", "gold"], "deep": {"x": 1}}).encode()
    assert ref_value(raw, ("prompt",)) == "p"
    assert ref_value(raw, ("palette", "1")) == "gold"
    assert ref_value(raw, ("deep", "x")) == 1
    assert ref_value(b"plain text", ()) == "plain text"
    with pytest.raises(KeyError):
        ref_value(raw, ("missing",))


def _recipe(params: str, extra: str = "") -> bytes:
    return f"""
id: r
inputs:
  idea: {{type: text}}
steps:
  - {{id: brief, kind: t.brief, params: {{idea: "${{inputs.idea}}"}}}}
  - {{id: use, kind: t.use, params: {params}}}
{extra}
""".encode()


def test_recipe_refs_add_deps_and_validate() -> None:
    r = parse_recipe(_recipe('{text: "${steps.brief.json.prompt}, x"}'))
    assert r.step("use").deps == ["brief"]
    assert [s.id for s in r.steps] == ["brief", "use"]
    with pytest.raises(RecipeError, match="unknown step 'nope'"):
        parse_recipe(_recipe('{text: "${steps.nope.json.prompt}"}'))
    with pytest.raises(RecipeError, match="unknown step 'use'"):
        parse_recipe(_recipe('{text: "${steps.use.text}"}'))


class BriefFake:
    kind = "t.brief"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self) -> None:
        self.prompt = "a glowing red potion"

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Outputs) -> StepResult:
        j = await ctx.blobs.put_bytes(json.dumps({"prompt": self.prompt, "count": 2}).encode(), mime="application/json")
        t = await ctx.blobs.put_bytes(self.prompt.encode(), mime="text/plain")
        return StepResult(outputs={"json": [j], "text": [t]})


class UseFake(BriefFake):
    kind = "t.use"

    def __init__(self) -> None:
        self.params: list[dict[str, Any]] = []

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Outputs) -> StepResult:
        self.params.append(params)
        return StepResult(outputs={"images": [await ctx.blobs.put_bytes(str(params).encode())]})


REF_RECIPE = """
id: refs
inputs:
  idea: {type: text}
steps:
  - {id: brief, kind: t.brief, params: {idea: "${inputs.idea}"}}
  - id: use
    kind: t.use
    params: {text: "${steps.brief.text}, white bg", n: "${steps.brief.json.count}", raw: "${inputs.idea}"}
outputs:
  final: use.images
"""


async def test_engine_binds_text_output_into_params(tmp_path: Path) -> None:
    brief, use = BriefFake(), UseFake()
    env = Env(tmp_path, [brief, use], {"refs": REF_RECIPE})  # type: ignore[list-item]
    run_id = await env.run("refs", {"idea": "potion"})
    assert env.status(run_id) == RunStatus.SUCCEEDED
    assert use.params == [{"text": "a glowing red potion, white bg", "n": 2, "raw": "potion"}]
    step = env.steps(run_id)["use"]
    assert step.params["text"] == "${steps.brief.text}, white bg"
    assert step.meta["refs"]["${steps.brief.text}"] == "a glowing red potion"

    run2 = await env.run("refs", {"idea": "potion"})
    assert env.steps(run2)["use"].status == StepStatus.CACHED
    brief.prompt = "a blue potion"
    run3 = await env.run("refs", {"idea": "potion"})
    assert env.steps(run3)["use"].status == StepStatus.SUCCEEDED
    assert use.params[-1]["text"] == "a blue potion, white bg"


async def test_engine_ref_to_missing_field_fails_step(tmp_path: Path) -> None:
    recipe = REF_RECIPE.replace("${steps.brief.json.count}", "${steps.brief.json.nope}")
    env = Env(tmp_path, [BriefFake(), UseFake()], {"refs": recipe})  # type: ignore[list-item]
    run_id = await env.run("refs", {"idea": "x"})
    assert env.status(run_id) == RunStatus.FAILED
    err = env.steps(run_id)["use"].error
    assert err is not None and err["code"] == "ref_unresolved" and "nope" in err["message"]


class FakeQuery:
    def __init__(self, *replies: ClaudeReply) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[list[dict[str, Any]], Any]] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def test_claude_task_text_output(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        q = FakeQuery(ClaudeReply(None, "hello", {"cost_usd": 0.001}))
        res = await ClaudeTaskExecutor(Settings(data_dir=tmp_path), q).run(ctx, {"prompt": "hi"}, {})
        assert ctx.blobs.path(res.outputs["text"][0].hash).read_text() == "hello"
        assert json.loads(ctx.blobs.path(res.outputs["json"][0].hash).read_bytes()) == {"text": "hello"}
        q2 = FakeQuery(ClaudeReply({"a": 1}, "", {}))
        res = await ClaudeTaskExecutor(Settings(), q2).run(ctx, {"prompt": "hi", "schema": {"type": "object"}}, {})
        assert "text" not in res.outputs
        assert q2.calls[0][1].output_format["type"] == "json_schema"

    asyncio.run(go())


def test_claude_brief(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        brief = {"subject": "potion", "style": "painterly", "view": "front", "palette": ["red"],
                 "negative": "text", "prompt": "a red potion bottle, painterly"}
        q = FakeQuery(ClaudeReply(brief, "", {"model": "haiku"}))
        ex = ClaudeBriefExecutor(Settings(data_dir=tmp_path), q)
        res = await ex.run(ctx, {"idea": "healing potion", "target": "concept-2d", "style": "pixel art"}, {})
        content, options = q.calls[0]
        assert options.model == "haiku" and options.output_format["schema"] == BRIEF_SCHEMA
        assert options.tools == [] and options.setting_sources == [] and options.strict_mcp_config
        assert "healing potion" in content[-1]["text"] and "pixel art" in content[-1]["text"]
        assert ctx.blobs.path(res.outputs["text"][0].hash).read_text() == "a red potion bottle, painterly"
        saved = json.loads(ctx.blobs.path(res.outputs["json"][0].hash).read_bytes())
        assert saved["idea"] == "healing potion" and saved["target"] == "concept-2d"
        assert res.meta["prompt"] == brief["prompt"]
        with pytest.raises(StepError, match="claude_bad_params"):
            await ex.run(ctx, {"idea": " "}, {})
        bad = ClaudeBriefExecutor(Settings(), FakeQuery(ClaudeReply({**brief, "prompt": ""}, "", {})))
        with pytest.raises(StepError, match="claude_bad_output"):
            await bad.run(ctx, {"idea": "x"}, {})

    asyncio.run(go())


def test_build_options_uses_native_claude_exe(tmp_path: Path) -> None:
    from studio_core.adapters import claude as cl
    from studio_core.config import Settings

    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    cl.ClaudeResource(Settings(data_dir=tmp_path, claude_exe=exe))
    assert cl.build_options({}, str(tmp_path)).cli_path == str(exe)
    cl.ClaudeResource(Settings(data_dir=tmp_path, claude_exe=tmp_path / "missing.exe"))
    assert cl.build_options({}, str(tmp_path)).cli_path is None
