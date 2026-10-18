import json
import os
import time
from pathlib import Path

from studio_core.adapters import register
from studio_core.adapters.blender.executor import render_params
from studio_core.adapters.claude import ClaudeTaskExecutor, build_content, build_options
from studio_core.adapters.codex import (ALPHA_INSTRUCTION, PROMPT_SUFFIX, CodexImageExecutor, build_args,
                                        collect_images, parse_events)
from studio_core.config import Settings
from studio_core.contracts import ExecutorRegistry, Lane

CODEX_JSONL = [
    '{"type":"thread.started","thread_id":"019a-thread"}',
    '{"type":"turn.started"}',
    '',
    'not json',
    '{"type":"item.completed","item":{"id":"i0","type":"reasoning","text":"..."}}',
    '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"DONE"}}',
    '{"type":"turn.completed","usage":{"input_tokens":10}}',
]


def test_codex_args_put_prompt_after_double_dash():
    args = build_args(Path("codex.exe"), Path("J"), "red potion", [Path("a.png"), Path("b.png")], True)
    assert args[:6] == ["codex.exe", "exec", "-i", "a.png", "-i", "b.png"]
    for flag in ("--json", "--skip-git-repo-check", "--ephemeral"):
        assert flag in args
    assert args[args.index("--sandbox") + 1] == "read-only" and args[args.index("-C") + 1] == "J"
    assert args[-2] == "--"
    assert args[-1].startswith("red potion") and ALPHA_INSTRUCTION in args[-1] and args[-1].endswith(PROMPT_SUFFIX)
    assert ALPHA_INSTRUCTION not in build_args(Path("c"), Path("J"), "x", [], False)[-1]


def test_codex_jsonl_parsing():
    info = parse_events(CODEX_JSONL)
    assert info.thread_id == "019a-thread" and info.last_message == "DONE" and not info.errors
    err = parse_events(['{"type":"thread.started","thread_id":"t"}', '{"type":"error","message":"quota"}',
                        '{"type":"turn.failed","error":{"message":"boom"}}'])
    assert err.errors == ["quota", "boom"]


def test_codex_collect_images(tmp_path):
    d = tmp_path / "generated_images" / "th1"
    d.mkdir(parents=True)
    (d / "exec-b.png").write_bytes(b"b")
    time.sleep(0.01)
    (d / "exec-a.png").write_bytes(b"a")
    os.utime(d / "exec-b.png", (1, 1))
    (d / "note.txt").write_text("x")
    assert [p.name for p in collect_images(tmp_path, "th1")] == ["exec-b.png", "exec-a.png"]
    assert collect_images(tmp_path, "missing") == [] and collect_images(tmp_path, None) == []


def test_codex_executor_flags():
    ex = CodexImageExecutor(Settings())
    assert ex.kind == "codex.image" and ex.lane == Lane.CLOUD and not ex.cacheable({"prompt": "x"})


def test_claude_options_and_content():
    opts = build_options({"model": "haiku", "schema": {"type": "object"}}, "C:/job")
    assert opts.setting_sources == [] and opts.strict_mcp_config is True and opts.tools == []
    assert opts.model == "haiku" and opts.output_format["type"] == "json_schema" and opts.cwd == "C:/job"
    assert build_options({}, ".").model == "sonnet" and build_options({}, ".").output_format is None
    content = build_content("describe", [(b"\x89PNG", "image/png")])
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert content[-1] == {"type": "text", "text": "describe"}
    ex = ClaudeTaskExecutor(Settings())
    assert ex.cacheable({"cache": True}) and not ex.cacheable({})


def test_claude_content_reencodes_unsupported_images():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "BMP")
    block = build_content("x", [(buf.getvalue(), "image/bmp")])[0]
    assert block["source"]["media_type"] == "image/png"


def test_blender_render_params():
    p = render_params({"elevation": 45, "count": 8, "engine": "cycles", "foo": 1})
    assert p == {"elevation": 45, "count": 8, "engine": "CYCLES"}
    assert render_params({})["engine"] == "BLENDER_EEVEE"


class _Bus:
    def __init__(self):
        self.events = []

    def publish(self, type, data=None, run_id=None, step_id=None):
        self.events.append((type, data))


def test_register_wires_kinds_and_resources():
    reg = ExecutorRegistry()
    resources = register(reg, Settings(), _Bus(), None)
    assert [r.name for r in resources] == ["comfyui", "blender", "codex", "claude"]
    assert {"codex.image", "claude.task", "blender.render_dirs", "comfy.*"} <= set(reg.kinds())
    ex = reg.resolve("comfy.t2i_zimage_turbo")
    assert ex.lane == Lane.GPU and ex.cacheable({"57:3.seed": 1}) and not ex.cacheable({})
    assert reg.resolve("blender.render_dirs").lane == Lane.BLENDER
    json.dumps([r.name for r in resources])
