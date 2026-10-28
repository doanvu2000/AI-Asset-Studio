from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from test_api_basic import StubEngine

from studio_core import paths
from studio_core.adapters.blender import client as blender_client
from studio_core.adapters.blender.executor import worker_hash
from studio_core.app import build_app
from studio_core.config import Settings, load_settings, save_overrides
from studio_core.main import _parse
from studio_core.mcp import link

TOKEN = "t7"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    meipass = tmp_path / "dist" / "studio-core" / "_internal"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable", str(meipass.parent / "studio-core.exe"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    return meipass


def test_dev_paths() -> None:
    assert not paths.is_frozen()
    assert paths.package_dir() == paths.PACKAGE_DIR
    assert (paths.recipes_dir() / "sprite-8dir-3d.yaml").is_file()
    assert (paths.blender_scripts_dir() / "worker.py").is_file()
    assert (paths.blender_scripts_dir() / "cleanup_math.py").is_file()
    assert paths.default_data_dir() == paths.PACKAGE_DIR.parents[1] / "data"
    assert paths.core_command("py.exe") == ["py.exe", "-m", "studio_core"]
    assert paths.core_command()[0] == sys.executable
    assert paths.core_cwd() == paths.PACKAGE_DIR.parent


def test_frozen_paths(frozen: Path, tmp_path: Path) -> None:
    assert paths.is_frozen()
    assert paths.package_dir() == frozen / "studio_core"
    assert paths.recipes_dir() == frozen / "studio_core" / "recipes"
    assert paths.blender_scripts_dir() == frozen / "studio_core" / "adapters" / "blender"
    assert paths.default_data_dir() == tmp_path / "Roaming" / "AI Asset Studio" / "data"
    assert paths.core_command("ignored-python") == [str(frozen.parent / "studio-core.exe")]
    assert paths.core_cwd() == frozen.parent


def test_frozen_settings_defaults(frozen: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STUDIO_DATA_DIR", raising=False)
    s = load_settings()
    assert s.data_dir == tmp_path / "Roaming" / "AI Asset Studio" / "data"
    assert s.data_dir.is_dir() and s.blobs_dir.is_dir()
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path / "custom"))
    assert load_settings().data_dir == tmp_path / "custom"


def test_worker_hash_reads_source_files_not_module_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from studio_core.adapters.blender import cleanup_math

    before = worker_hash()
    monkeypatch.setattr(cleanup_math, "__file__", "Z:/nope/cleanup_math.pyc")
    assert worker_hash() == before
    assert blender_client.WORKER_SCRIPT == paths.blender_scripts_dir() / "worker.py"


def test_frozen_spawn_uses_exe_launcher(frozen: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("windows launcher path")
    seen: list[list[str]] = []

    class FakePopen:
        def __init__(self, args: list[str], **kw: object) -> None:
            seen.append(args)
            assert kw["cwd"] == frozen.parent

    monkeypatch.setattr(link.subprocess, "Popen", FakePopen)
    cmd = [*paths.core_command(), "serve", "--port", "0"]
    link._popen_detached(cmd, {}, None)  # type: ignore[arg-type]
    exe = str(frozen.parent / "studio-core.exe")
    assert seen == [[exe, link.LAUNCH_CMD, json.dumps([exe, "serve", "--port", "0"])]]
    args = _parse([link.LAUNCH_CMD, seen[0][2]])
    assert args.cmd == link.LAUNCH_CMD and json.loads(args.argv_json)[0] == exe


def test_save_overrides_merges_and_applies(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(json.dumps({"comfy_port": 8190, "blender_exe": "C:/old.exe"}),
                                            encoding="utf-8")
    s = load_settings(tmp_path)
    assert s.comfy_port == 8190
    save_overrides(s, {"blender_exe": str(tmp_path / "b.exe"), "comfy_models_dir": str(tmp_path / "m")})
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved == {"comfy_port": 8190, "blender_exe": str(tmp_path / "b.exe"),
                     "comfy_models_dir": str(tmp_path / "m")}
    assert s.blender_exe == tmp_path / "b.exe" and isinstance(s.blender_exe, Path)
    again = load_settings(tmp_path)
    assert again.comfy_models_dir == tmp_path / "m" and again.comfy_port == 8190


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[Settings, httpx.AsyncClient]]:
    comfy = tmp_path / "comfy"
    comfy.mkdir()
    (comfy / "main.py").write_text("", encoding="utf-8")
    (comfy / "comfyui_version.py").write_text('__version__ = "0.41.0"\n', encoding="utf-8")
    wf = tmp_path / "wf"
    wf.mkdir()
    (wf / "a.json").write_text("{}", encoding="utf-8")
    s = Settings(data_dir=tmp_path / "data", comfy_dir=comfy, comfy_python=Path(sys.executable),
                 comfy_models_dir=tmp_path / "models-missing", comfy_workflows_dir=wf,
                 blender_exe=tmp_path / "no-blender.exe", codex_exe=tmp_path / "no-codex.exe")
    for d in (s.data_dir, s.blobs_dir, s.thumbs_dir, s.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(s, TOKEN, engine=StubEngine(), load_plugins=False)
    async with (app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c):
        yield s, c


async def test_tools_api_get(client: tuple[Settings, httpx.AsyncClient]) -> None:
    s, c = client
    assert (await c.get("/api/settings/tools")).status_code == 401
    r = await c.get("/api/settings/tools", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    tools = {t["key"]: t for t in body["tools"]}
    assert list(tools) == ["comfy_dir", "comfy_python", "comfy_models_dir", "comfy_workflows_dir", "blender_exe",
                           "codex_exe", "claude_exe", "ffmpeg_exe", "godot_exe", "unity_exe", "unity_project"]
    assert tools["comfy_dir"]["ok"] and tools["comfy_dir"]["version"] == "ComfyUI 0.41.0"
    assert tools["comfy_python"]["ok"] and tools["comfy_python"]["version"].startswith("Python 3.")
    assert not tools["comfy_models_dir"]["exists"]
    assert tools["comfy_workflows_dir"]["detail"] == "1 workflow"
    assert not tools["blender_exe"]["exists"] and not tools["blender_exe"]["ok"]
    assert body["settings_file"] == str(s.settings_path)
    quick = (await c.get("/api/settings/tools?versions=false", headers=AUTH)).json()
    assert {t["key"]: t for t in quick["tools"]}["comfy_python"]["version"] is None


async def test_tools_api_put(client: tuple[Settings, httpx.AsyncClient], tmp_path: Path) -> None:
    s, c = client
    models = tmp_path / "models"
    (models / "checkpoints").mkdir(parents=True)
    r = await c.put("/api/settings/tools", headers=AUTH, json={"paths": {"comfy_models_dir": f'"{models}"'}})
    assert r.status_code == 200
    t = {x["key"]: x for x in r.json()["tools"]}["comfy_models_dir"]
    assert t["ok"] and t["path"] == str(models) and t["detail"] == "1 thư mục con"
    assert s.comfy_models_dir == models
    assert json.loads(s.settings_path.read_text(encoding="utf-8")) == {"comfy_models_dir": str(models)}
    bad = await c.put("/api/settings/tools", headers=AUTH, json={"paths": {"data_dir": "C:/x"}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid"
    empty = await c.put("/api/settings/tools", headers=AUTH, json={"paths": {"blender_exe": "  "}})
    assert empty.status_code == 400
