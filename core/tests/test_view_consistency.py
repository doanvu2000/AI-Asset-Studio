from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.comfy import CheckedViewsExecutor
from studio_core.contracts import ExecutorRegistry, StepError
from test_adapters_fakes import FakeCtx
from test_p7_viewcheck import VIEWS, FakeQwen, figure, png


class FakeJudge:
    """Scripted Claude verdicts: per view name, one bool per call that includes it."""

    def __init__(self, script: dict[str, list[bool]], error: bool = False):
        self.script = script
        self.error = error
        self.calls: list[tuple[list[dict[str, Any]], Any]] = []
        self.seen: dict[str, int] = {}

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        if self.error:
            raise StepError("claude_failed", "no sdk", retryable=True)
        text = next(b["text"] for b in content if b["type"] == "text")
        out = []
        for v in VIEWS:
            if f": {v['prompt']}" not in text and f": {v.get('expect')}" not in text:
                continue
            i = self.seen.get(v["name"], 0)
            self.seen[v["name"]] = i + 1
            plan = self.script.get(v["name"], [True])
            ok = plan[min(i, len(plan) - 1)]
            out.append({"name": v["name"], "ok": ok, "reason": "ok" if ok else "staff switched hands"})
        return ClaudeReply({"views": out}, "", {"cost_usd": 0.001})


async def run(tmp_path: Path, judge: FakeJudge, qwen_script: dict[str, list[bool]] | None = None, **params: Any):
    qwen = FakeQwen(qwen_script or {})
    reg = ExecutorRegistry()
    reg.register(qwen)
    ex = CheckedViewsExecutor(reg.resolve, query=judge)
    ctx = FakeCtx(tmp_path)
    front = await ctx.blobs.put_bytes(png(figure()), name="front.png")
    res = await ex.run(ctx, {"workflow": "fake_qwen", "views": VIEWS, "consistency": True, **params},
                       {"image": [front]})
    report = json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_text(encoding="utf-8"))
    return qwen, res, report


async def test_inconsistent_view_is_regenerated(tmp_path: Path) -> None:
    judge = FakeJudge({"back": [False, True]})
    qwen, res, rep = await run(tmp_path, judge)
    assert len(qwen.calls) == 2 and len(judge.calls) == 2
    content, opts = judge.calls[0]
    assert sum(b["type"] == "image" for b in content) == 4
    assert opts.model == "claude-opus-5-5" and opts.output_format["type"] == "json_schema"
    assert sum(b["type"] == "image" for b in judge.calls[1][0]) == 2
    back = next(v for v in rep["views"] if v["name"] == "back")
    assert back["ok"] and back["attempts"][0]["reasons"] == ["inconsistent"]
    assert back["attempts"][0]["consistency"] == {"ok": False, "reason": "staff switched hands"}
    assert res.meta["attempts"] == {"left": 1, "back": 2, "right": 1}
    assert res.meta["claude_cost_usd"] == pytest.approx(0.002)


async def test_never_consistent_view_is_dropped(tmp_path: Path) -> None:
    _, res, rep = await run(tmp_path, FakeJudge({"right": [False]}), attempts=2)
    assert rep["dropped"] == ["right"] and rep["drop_inputs"] == ["330.right"]
    assert len(res.outputs["images"]) == 2


async def test_silhouette_failures_never_reach_the_judge(tmp_path: Path) -> None:
    judge = FakeJudge({})
    await run(tmp_path, judge, {"376": [False, True]})
    first = next(b["text"] for b in judge.calls[0][0] if b["type"] == "text")
    assert ". left: " not in first and "2. back: back view" in first


async def test_judge_error_fails_open(tmp_path: Path) -> None:
    _, res, rep = await run(tmp_path, FakeJudge({}, error=True))
    assert len(res.outputs["images"]) == 3 and rep["failed"] == []
    assert rep["views"][0]["attempts"][0]["consistency"] == {"error": "claude_failed"}


async def test_off_by_default(tmp_path: Path) -> None:
    judge = FakeJudge({"back": [False]})
    qwen = FakeQwen({})
    reg = ExecutorRegistry()
    reg.register(qwen)
    ctx = FakeCtx(tmp_path)
    front = await ctx.blobs.put_bytes(png(figure()), name="front.png")
    res = await CheckedViewsExecutor(reg.resolve, query=judge).run(
        ctx, {"workflow": "fake_qwen", "views": VIEWS}, {"image": [front]})
    assert judge.calls == [] and len(res.outputs["images"]) == 3 and "claude_cost_usd" not in res.meta
