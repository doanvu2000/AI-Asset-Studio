import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp import Client

from studio_core.mcp.link import CoreLink
from studio_core.mcp.server import build_server
from studio_core.paths import recipes_dir
from test_p4b_mcp import make_data_dir, make_png, payload, spawn_core, stop_core


@pytest.fixture(scope="module")
def core(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    data = make_data_dir(tmp_path_factory.mktemp("r12"))
    recipes = Path(json.loads((data / "settings.json").read_text())["recipes_dir"])
    shutil.copy(recipes_dir() / "library-import.yaml", recipes)
    proc, _ = spawn_core(data)
    try:
        yield data
    finally:
        stop_core(proc, data)


async def test_auto_export_fields(core: Path, tmp_path: Path) -> None:
    async with Client(build_server(CoreLink(core))) as c:
        pid = payload(await c.call_tool("create_project", {"name": "AE"}))["project_id"]
        dest = tmp_path / "game"
        p = payload(await c.call_tool("update_project", {"project_id": pid, "auto_export": True,
                                                         "auto_export_dir": str(dest)}))
        assert p["auto_export"] is True and p["auto_export_dir"] == str(dest)
        p = payload(await c.call_tool("update_project", {"project_id": pid, "auto_export": False}))
        assert p["auto_export"] is False and p["auto_export_dir"] == str(dest)
        p = payload(await c.call_tool("update_project", {"project_id": pid, "auto_export_dir": ""}))
        assert p["auto_export_dir"] == ""


async def test_rerun_run(core: Path, tmp_path: Path) -> None:
    png = make_png(tmp_path / "in.png")
    async with Client(build_server(CoreLink(core))) as c:
        rid = payload(await c.call_tool("start_run", {"recipe": "mcp-cpu", "inputs": {"image": str(png)},
                                                      "gate_policy": "auto_first"}))["run_id"]
        assert payload(await c.call_tool("wait_run", {"run_id": rid, "timeout_s": 60,
                                                      "include_thumbnails": False}))["status"] == "succeeded"
        new = payload(await c.call_tool("rerun_run", {"run_id": rid}))["run_id"]
        assert new and new != rid
        assert payload(await c.call_tool("wait_run", {"run_id": new, "timeout_s": 60,
                                                      "include_thumbnails": False}))["status"] in (
            "awaiting_human", "succeeded")
        same = await c.call_tool("rerun_run", {"run_id": rid, "seed": "same"})
        if same.is_error:
            assert "seed_unknown" in same.content[0].text
        bad = await c.call_tool("rerun_run", {"run_id": "nope"})
        assert bad.is_error
        bad = await c.call_tool("rerun_run", {"run_id": rid, "seed": "maybe"})
        assert bad.is_error


async def test_theme_tool_and_pack(core: Path, tmp_path: Path) -> None:
    png = make_png(tmp_path / "hero.png")
    async with Client(build_server(CoreLink(core))) as c:
        pid = payload(await c.call_tool("create_project", {"name": "Themed"}))["project_id"]
        t = payload(await c.call_tool("get_ui_theme", {"project_id": pid}))
        assert t["target"] == "flutter" and t["source"] == "fallback"
        assert any(f["path"].endswith("app_theme.dart") and "ThemeData" in f["content"] for f in t["files"])
        k = payload(await c.call_tool("get_ui_theme", {"project_id": pid, "target": "android",
                                                       "package": "com.example.game"}))
        assert any("package com.example.game" in f["content"] for f in k["files"])
        assert (await c.call_tool("get_ui_theme", {"project_id": pid, "target": "unity"})).is_error
        assert (await c.call_tool("get_ui_theme", {"project_id": pid, "target": "android",
                                                   "package": "Bad Pkg"})).is_error

        payload(await c.call_tool("import_to_library", {"paths": [str(png)], "project_id": pid}))
        out = tmp_path / "packs"
        res = payload(await c.call_tool("build_asset_pack", {"project_id": pid, "engines": ["flutter"],
                                                             "out_dir": str(out), "theme": True}))
        assert list(Path(out).rglob("app_theme.dart")), res


async def test_cancel_scan_tool(core: Path) -> None:
    async with Client(build_server(CoreLink(core))) as c:
        assert payload(await c.call_tool("cancel_scan", {"kind": "audit"})) == {"cancelled": 0}
        assert payload(await c.call_tool("cancel_scan", {"job": "nope"})) == {"cancelled": 0}
        assert (await c.call_tool("cancel_scan", {})).is_error
