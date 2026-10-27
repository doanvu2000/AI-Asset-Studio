import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import ImageContent
from PIL import Image, ImageDraw

from studio_core.mcp.link import CORE_DIR, CoreLink
from studio_core.mcp.lockfile import lock_path, pid_alive, read_lock, write_lock
from studio_core.mcp.server import build_server

RECIPE = """
id: mcp-cpu
name: MCP test (CPU)
description: input image -> two sizes -> pick -> sheet -> approve
inputs:
  image: {type: image, label: Ảnh}
  size: {type: int, default: 64}
steps:
  - id: fit
    kind: op.trim_pad_resize
    in: {images: inputs.image}
    params: {size: "${inputs.size}", pad: 0.1}
  - id: small
    kind: op.downscale
    in: {images: fit.images}
    params: {factor: 2}
  - id: pick
    kind: gate.pick
    in: {images: [fit.images, small.images]}
    params: {k: 1}
  - id: sheet
    kind: op.sheet
    in: {images: [pick.images, small.images]}
  - id: review
    kind: gate.approve
    in: {images: sheet.sheet}
outputs:
  final: pick.images
  sheet: review.images
  meta: sheet.meta
"""


def make_data_dir(root: Path) -> Path:
    data = root / "data"
    recipes = root / "recipes"
    data.mkdir(parents=True)
    recipes.mkdir()
    (recipes / "mcp-cpu.yaml").write_text(RECIPE, encoding="utf-8")
    (data / "settings.json").write_text(json.dumps({"recipes_dir": str(recipes)}), encoding="utf-8")
    return data


def make_png(path: Path) -> Path:
    img = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((20, 16, 76, 80), fill=(200, 40, 40, 255))
    img.save(path)
    return path


def spawn_core(data: Path) -> tuple[subprocess.Popen[bytes], int]:
    proc = subprocess.Popen([sys.executable, "-m", "studio_core", "serve", "--port", "0", "--data-dir", str(data)],
                            cwd=CORE_DIR, env={**os.environ, "STUDIO_TOKEN": secrets.token_hex(16)},
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    for raw in proc.stdout:
        if raw.startswith(b"STUDIO_CORE_READY"):
            return proc, json.loads(raw.split(b" ", 1)[1])["pid"]
    raise RuntimeError("core died before READY")


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def stop_core(proc: subprocess.Popen[bytes], data: Path) -> None:
    info = read_lock(data)
    if info and proc.poll() is None:
        try:
            httpx.post(f"http://127.0.0.1:{info['port']}/api/shutdown",
                       headers={"Authorization": f"Bearer {info['token']}"}, timeout=5)
            proc.wait(20)
        except (httpx.HTTPError, subprocess.TimeoutExpired):
            kill_tree(proc)
    elif proc.poll() is None:
        kill_tree(proc)


@pytest.fixture(scope="module")
def core(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, int]]:
    data = make_data_dir(tmp_path_factory.mktemp("p4b"))
    proc, pid = spawn_core(data)
    try:
        yield data, pid
    finally:
        stop_core(proc, data)


def payload(res: Any) -> dict[str, Any]:
    assert not res.is_error, res.content
    if res.structured_content is not None:
        sc = res.structured_content
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


def test_core_writes_lock(core: tuple[Path, int]) -> None:
    data, pid = core
    info = read_lock(data)
    assert info is not None and info["pid"] == pid and info["token"] and info["port"] > 0
    assert pid_alive(pid)


async def test_stale_lock_is_ignored(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    write_lock(data, port=1, token="x")
    info = json.loads(lock_path(data).read_text())
    info["pid"] = 0x7FFFFFF0
    lock_path(data).write_text(json.dumps(info))
    link = CoreLink(data)
    assert await link._attach() is None


async def test_agent_flow(core: tuple[Path, int], tmp_path: Path) -> None:
    data, _ = core
    png = make_png(tmp_path / "in.png")
    link = CoreLink(data)
    progress: list[float] = []

    async def on_progress(p: float, total: float | None, msg: str | None) -> None:
        progress.append(p)

    async with Client(build_server(link)) as c:
        recipes = payload(await c.call_tool("list_recipes", {}))["recipes"]
        assert [r["id"] for r in recipes] == ["mcp-cpu"]
        desc = payload(await c.call_tool("describe_recipe", {"recipe_id": "mcp-cpu"}))
        assert desc["inputs"]["image"]["type"] == "image" and desc["gates"] == ["pick", "review"]

        started = payload(await c.call_tool("start_run", {"recipe": "mcp-cpu", "inputs": {"image": str(png)},
                                                          "title": "mcp test", "gate_policy": "agent"}))
        run_id = started["run_id"]
        assert link.spawned_pid is None

        res = await c.call_tool("wait_run", {"run_id": run_id, "timeout_s": 60}, progress_callback=on_progress)
        view = payload(res)
        assert view["status"] == "awaiting_human" and view["source"] == "mcp" and view["gate_policy"] == "agent"
        gate = view["pending_gates"][0]
        assert gate["key"] == "pick" and gate["kind"] == "pick" and len(gate["candidates"]) == 2
        assert all(Path(cnd["path"]).is_file() for cnd in gate["candidates"])
        assert any(isinstance(x, ImageContent) for x in res.content)

        payload(await c.call_tool("resolve_gate", {"step_id": gate["step_id"], "pick": ["2"]}))
        view = payload(await c.call_tool("wait_run", {"run_id": run_id, "timeout_s": 60}))
        gate = view["pending_gates"][0]
        assert gate["key"] == "review" and gate["kind"] == "approve"
        payload(await c.call_tool("resolve_gate", {"step_id": gate["step_id"], "approve": True}))
        view = payload(await c.call_tool("wait_run", {"run_id": run_id, "timeout_s": 60}))
        assert view["status"] == "succeeded", view

        res = await c.call_tool("get_outputs", {"run_id": run_id})
        out = payload(res)
        by_name = {f["output"]: f for f in out["files"]}
        assert set(by_name) == {"final", "sheet", "meta"}
        for f in out["files"]:
            p = Path(f["path"])
            assert p.is_file() and p.parent == data.resolve() / "exports" / "mcp" / run_id
        assert by_name["final"]["width"] == 32 and by_name["final"]["path"].endswith("final.png")
        assert "frames" in by_name["meta"]["json"]
        assert any(isinstance(x, ImageContent) for x in res.content)

        vid = by_name["final"]["version_id"]
        assert payload(await c.call_tool("set_version_status", {"version_id": vid, "status": "approved"}))["status"] == "approved"
        versions = payload(await c.call_tool("list_versions", {"run_id": run_id, "status": "approved"}))["versions"]
        assert [v["version_id"] for v in versions] == [vid]
        runs = payload(await c.call_tool("list_runs", {"only_mcp": True}))["runs"]
        assert run_id in [r["run_id"] for r in runs]
        stats = payload(await c.call_tool("recipe_stats", {"days": 1}))
        assert stats["totals"]["succeeded"] >= 1 and "mcp-cpu" in [r["recipe_id"] for r in stats["recipes"]]
    assert progress


async def test_auto_first_and_errors(core: tuple[Path, int], tmp_path: Path) -> None:
    data, _ = core
    png = make_png(tmp_path / "in.png")
    async with Client(build_server(CoreLink(data))) as c:
        run_id = payload(await c.call_tool("start_run", {"recipe": "mcp-cpu", "inputs": {"image": str(png), "size": 48},
                                                         "gate_policy": "auto_first"}))["run_id"]
        view = payload(await c.call_tool("wait_run", {"run_id": run_id, "timeout_s": 60}))
        assert view["status"] == "succeeded", view
        out = payload(await c.call_tool("get_outputs", {"run_id": run_id, "include_images": False}))
        final = next(f for f in out["files"] if f["output"] == "final")
        assert final["width"] == 48

        bad = await c.call_tool("start_run", {"recipe": "mcp-cpu", "inputs": {"image": "in.png"}})
        assert bad.is_error and "absolute" in bad.content[0].text
        bad = await c.call_tool("start_run", {"recipe": "mcp-cpu", "inputs": {"image": str(png), "nope": 1}})
        assert bad.is_error and "nope" in bad.content[0].text
        bad = await c.call_tool("describe_recipe", {"recipe_id": "missing"})
        assert bad.is_error
        bad = await c.call_tool("get_run", {"run_id": "../x"})
        assert bad.is_error


async def test_stdio_spawns_and_stops_core(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    params = StdioServerParameters(command=sys.executable, args=["-m", "studio_core", "mcp", "--data-dir", str(data)],
                                   cwd=str(CORE_DIR))
    async with Client(params, read_timeout_seconds=90) as c:
        recipes = payload(await c.call_tool("list_recipes", {}))["recipes"]
        assert [r["id"] for r in recipes] == ["mcp-cpu"]
        info = read_lock(data)
        assert info is not None
        core_pid = info["pid"]
        assert pid_alive(core_pid)
    deadline = time.monotonic() + 30
    while pid_alive(core_pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
    assert not pid_alive(core_pid)
    assert read_lock(data) is None
