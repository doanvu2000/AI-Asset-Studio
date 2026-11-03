import asyncio
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import websockets
from mcp import Client

from studio_core.mcp.activity import ActivityReporter, summarize
from studio_core.mcp.catalog import generated_catalog, load_yaml_recipes, recipe_markdown
from studio_core.mcp.followup import follow_ups
from studio_core.mcp.link import CoreError, CoreLink
from studio_core.mcp.lockfile import read_lock
from studio_core.mcp.server import build_server
from studio_core.paths import recipes_dir
from test_p4b_mcp import make_data_dir, make_png, payload, spawn_core, stop_core

REPO = Path(__file__).resolve().parents[2]
SKILL_REFS = [REPO / "integrations" / "claude-plugin" / "skills" / "asset-studio" / "references" / "recipes.md",
              REPO / "integrations" / "codex" / "skills" / "asset-studio" / "references" / "recipes.md"]


@pytest.fixture(scope="module")
def core(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    data = make_data_dir(tmp_path_factory.mktemp("agent"))
    recipes = Path(json.loads((data / "settings.json").read_text())["recipes_dir"])
    shutil.copy(recipes_dir() / "library-import.yaml", recipes)
    proc, _ = spawn_core(data)
    try:
        yield data
    finally:
        stop_core(proc, data)


def _http(data: Path) -> httpx.Client:
    info = read_lock(data)
    assert info
    return httpx.Client(base_url=f"http://127.0.0.1:{info['port']}",
                        headers={"Authorization": f"Bearer {info['token']}"}, timeout=30)


async def test_activity_api_and_ws(core: Path) -> None:
    info = read_lock(core)
    assert info
    async with websockets.connect(f"ws://127.0.0.1:{info['port']}/ws?t={info['token']}") as ws:
        assert json.loads(await ws.recv())["type"] == "hello"
        with _http(core) as c:
            r = c.post("/api/agent/activity", json={"session": "s1", "client": "claude-code", "phase": "start",
                                                    "tool": "start_run", "summary": "recipe=x", "run_id": "r1"})
            assert r.status_code == 200
            assert c.post("/api/agent/activity", json={"session": "s1", "phase": "bogus"}).status_code in (400, 422)
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if ev["type"] == "agent.activity":
                break
        assert ev["run_id"] == "r1" and ev["data"]["tool"] == "start_run" and ev["data"]["client"] == "claude-code"
    with _http(core) as c:
        snap = c.get("/api/agent/activity").json()
        s1 = next(s for s in snap["sessions"] if s["session"] == "s1")
        assert s1["active"] and s1["current"]["tool"] == "start_run" and s1["calls"] == 1
        c.post("/api/agent/activity", json={"session": "s1", "phase": "disconnect"})
        s1 = next(s for s in c.get("/api/agent/activity").json()["sessions"] if s["session"] == "s1")
        assert not s1["active"] and "current" not in s1
        status = c.get("/api/agent/status").json()
        assert status["core"]["version"] and "lanes" in status and "engines" in status and "open_gates" in status


async def test_new_tools_and_activity(core: Path, tmp_path: Path) -> None:
    png = make_png(tmp_path / "hero.png")
    link = CoreLink(core)
    async with Client(build_server(link)) as c:
        st = payload(await c.call_tool("system_status", {}))
        assert st["core"]["version"] and st["mcp"]["data_dir"] == str(core.resolve())

        assert payload(await c.call_tool("gpu_status", {}))["yielding"] is False
        y = payload(await c.call_tool("yield_gpu", {"minutes": 5}))
        assert y["yielding"] is True and y["until"]
        assert payload(await c.call_tool("resume_gpu", {}))["yielding"] is False
        for tool, args, word in (("yield_gpu", {"minutes": 0}, "minutes"), ("yield_gpu", {"minutes": 99999}, "minutes"),
                                 ("prioritize_run", {"run_id": "nope"}, "exactly one")):
            res = await c.call_tool(tool, args)
            assert res.is_error and word in res.content[0].text

        proj = payload(await c.call_tool("create_project", {"name": "Agent game", "prompt_suffix": "cel shaded",
                                                            "reference_image": str(png)}))
        pid = proj["project_id"]
        assert proj["style"]["prompt_suffix"] == "cel shaded" and len(proj["style"]["reference_image"]) == 64
        proj = payload(await c.call_tool("update_project", {"project_id": pid, "negative": "blurry"}))
        assert proj["style"]["prompt_suffix"] == "cel shaded" and proj["style"]["negative"] == "blurry"
        proj = payload(await c.call_tool("update_project", {"project_id": pid, "replace_style": True,
                                                            "notes": "reset"}))
        assert proj["style"]["prompt_suffix"] == "" and proj["style"]["notes"] == "reset"

        imp = payload(await c.call_tool("import_to_library", {"paths": [str(png)], "project_id": pid}))
        assert [v["status"] for v in imp["versions"]] == ["approved"]
        assert imp["versions"][0]["asset_name"] == "hero"
        again = payload(await c.call_tool("import_to_library", {"hashes": [imp["versions"][0]["hash"]],
                                                                  "project_id": pid, "asset_name": "Hero B"}))
        assert again["versions"][0]["hash"] == imp["versions"][0]["hash"]
        assert imp["group"] == "single" and len(imp["items"]) == 1
        bad = await c.call_tool("import_to_library", {"hashes": ["../x"]})
        assert bad.is_error
        assert (await c.call_tool("import_to_library", {})).is_error

        res = await c.call_tool("generate", {"recipe": "mcp-cpu", "inputs": {"image": str(png), "size": 40},
                                             "project_id": pid, "include_images": False})
        out = payload(res)
        assert out["status"] == "succeeded" and out["recipe"] == "mcp-cpu"
        assert {f["output"] for f in out["files"]} == {"final", "sheet", "meta"}
        assert all(Path(f["path"]).is_file() for f in out["files"])

        fu = payload(await c.call_tool("follow_up_options", {"run_id": out["run_id"]}))
        opt = next(o for o in fu["options"] if o["recipe"] == "mcp-cpu")
        assert opt["inputs"]["image"] == fu["output"]["hash"] and not opt["missing"]
        chained = payload(await c.call_tool("start_run", {"recipe": "mcp-cpu", "inputs": opt["inputs"],
                                                          "gate_policy": "auto_first"}))
        assert payload(await c.call_tool("wait_run", {"run_id": chained["run_id"], "timeout_s": 60,
                                                      "include_thumbnails": False}))["status"] == "succeeded"

        bad = await c.call_tool("export_to_engine", {"engine": "unity"})
        assert bad.is_error and "exactly one" in bad.content[0].text
        bad = await c.call_tool("export_to_engine", {"engine": "godot", "run_id": out["run_id"],
                                                     "dest_dir": str(tmp_path)})
        assert bad.is_error and "inside a Godot project" in bad.content[0].text
        assert "backups" in payload(await c.call_tool("restyle_undo", {}))

        cat = await c.read_resource("asset-studio://recipes")
        text = cat.contents[0].text
        assert "## `mcp-cpu`" in text and "`library-import`" in text

    with _http(core) as h:
        snap = h.get("/api/agent/activity", params={"limit": 300}).json()
    mine = [e for e in snap["events"] if e["tool"] in ("generate", "import_to_library", "wait")]
    assert any(e["phase"] == "start" and e["tool"] == "generate" for e in mine)
    assert any(e["phase"] == "end" and e["tool"] == "generate" and e["run_id"] == out["run_id"] for e in mine)
    assert any(e["phase"] == "wait" and e["run_id"] == out["run_id"] for e in mine)
    assert not any(str(tmp_path) in json.dumps(e) for e in snap["events"])
    sessions = [s for s in snap["sessions"] if s["calls"] >= 8]
    assert sessions and not sessions[0]["connected"]


async def test_reporter_never_breaks_tools(tmp_path: Path) -> None:
    link = CoreLink(tmp_path / "data")
    rep = ActivityReporter(link)
    await rep.connect(spawn=False)
    assert not rep.announced

    async def boom(*a: Any, **kw: Any) -> Any:
        raise CoreError(0, "core_unreachable", "down")

    link.request = boom  # type: ignore[method-assign]
    await rep.post("start", tool="x")
    await rep.connect(spawn=True)
    await rep.disconnect()


def test_summarize_hides_paths_and_text() -> None:
    s = summarize({"recipe": "concept-2d", "inputs": {"image": r"C:\Users\me\secret\hero.png", "prompt": "a knight"},
                   "gdd_text": "x" * 500, "run_id": "a" * 32, "title": None})
    assert "hero.png" in s and "secret" not in s and "gdd_text=<500 chars>" in s and "title" not in s
    assert len(summarize({"k": "y" * 1000})) <= 300


def test_follow_ups_port() -> None:
    recipes = [
        {"id": "spine-skin", "name": "Skin", "inputs": {
            "skeleton": {"type": "file", "ext": ["json"]},
            "atlas": {"type": "file", "ext": ["atlas"], "siblings": "atlas_pages"}}},
        {"id": "upscale", "name": "Upscale", "inputs": {"image": {"type": "image", "required": False}}},
        {"id": "sprite", "name": "Sprite", "inputs": {"image": {"type": "image"}, "prompt": {"type": "text"}}},
    ]
    png = {"hash": "p" * 64, "name": "hero.png", "mime": "image/png"}
    assert [f["recipe"] for f in follow_ups(recipes, png)] == ["sprite"]
    skel = {"hash": "s" * 64, "name": "hero.json", "mime": "application/json"}
    atlas = {"hash": "a" * 64, "name": "hero.atlas", "mime": "text/plain"}
    page = {"hash": "g" * 64, "name": "hero.png", "mime": "image/png"}
    out = follow_ups(recipes, skel, {"skeleton": [skel], "atlas": [atlas], "texture": [page]})
    skin = next(f for f in out if f["recipe"] == "spine-skin")
    assert skin["inputs"] == {"skeleton": [skel["hash"]], "atlas": [atlas["hash"], page["hash"]]}


def test_catalog_covers_every_recipe() -> None:
    recipes = load_yaml_recipes()
    text = generated_catalog()
    for r in recipes:
        assert f"## `{r['id']}`" in text
    assert "required" in recipe_markdown({"id": "x", "inputs": {"a": {"type": "image"}}})


@pytest.mark.parametrize("ref", SKILL_REFS, ids=lambda p: p.parts[-5])
def test_skill_reference_is_current(ref: Path) -> None:
    assert ref.is_file(), f"run: uv run python ../scripts/gen_agent_docs.py (missing {ref})"
    assert ref.read_text(encoding="utf-8") == generated_catalog(), \
        "recipe catalog drifted from the YAML recipes; run: cd core && uv run python ../scripts/gen_agent_docs.py"


@pytest.mark.slow
def test_agent_cli_run_wait_outputs(core: Path, tmp_path: Path) -> None:
    import subprocess
    import sys

    from studio_core.mcp.link import CORE_DIR

    png = make_png(tmp_path / "cli.png")
    base = [sys.executable, "-m", "studio_core", "agent", "--data-dir", str(core), "--json"]
    r = subprocess.run([*base, "run", "mcp-cpu", "-i", f"image={png}", "-i", "size=36", "--wait", "--no-cache"],
                       cwd=CORE_DIR, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout)
    assert res["status"] == "succeeded" and all(Path(f["path"]).is_file() for f in res["files"])
    assert next(f for f in res["files"] if f["output"] == "final")["width"] == 36
    r = subprocess.run([*base, "recipes"], cwd=CORE_DIR, capture_output=True, text=True, encoding="utf-8",
                       timeout=120)
    assert "mcp-cpu" in [x["id"] for x in json.loads(r.stdout)["recipes"]]
    r = subprocess.run([*base, "run", "nope"], cwd=CORE_DIR, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and "unknown recipe" in r.stderr


def test_codex_skill_matches_claude_skill() -> None:
    claude = REPO / "integrations" / "claude-plugin" / "skills" / "asset-studio" / "SKILL.md"
    codex = REPO / "integrations" / "codex" / "skills" / "asset-studio" / "SKILL.md"
    assert codex.read_text(encoding="utf-8") == claude.read_text(encoding="utf-8"), \
        "run: cd core && uv run python ../scripts/gen_agent_docs.py"
