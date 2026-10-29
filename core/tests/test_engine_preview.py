import asyncio
import io
import json
from pathlib import Path

from PIL import Image

from studio_core.export import preview
from studio_core.export.preview import ProcResult

from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)
from test_step_export import _sprite


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


def _export(tmp: Path, name: str = "Knight") -> Path:
    d = tmp / "exports" / name
    (d / "Models").mkdir(parents=True)
    (d / f"{name}.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    (d / f"{name}_tiled.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    (d / "Models" / "m.glb").write_bytes(b"glTF")
    (d / f"{name}.png").write_bytes(_png_bytes())
    return d


def _value_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_godot_issues_strip_ansi_and_dedupe() -> None:
    log = ("\x1b[1;31mERROR:\x1b[0m Failed loading resource: res://K/K.png\n   at: load (core/io)\n"
           "ERROR: Failed loading resource: res://K/K.png\nWARNING: ObjectDB instances leaked\nPREVIEW_OK x\n")
    errors, warnings = preview.godot_issues(log)
    assert errors == ["ERROR: Failed loading resource: res://K/K.png"]
    assert warnings == ["WARNING: ObjectDB instances leaked"]
    assert "\x1b" not in preview._tail(log) and "[  50% ]" not in preview._tail("[  50% ] reimport\nok")


def test_godot_target_prefers_shortest_top_level_scene(tmp_path: Path) -> None:
    d = _export(tmp_path)
    assert preview.godot_target(d).name == "Knight.tscn"
    for p in d.glob("*.tscn"):
        p.unlink()
    assert preview.godot_target(d).name == "m.glb"
    (d / "Models" / "m.glb").unlink()
    assert preview.godot_target(d).name == "Knight.png"


def test_unity_log_issues_keep_own_folder_and_fatal_lines() -> None:
    log = ("Some unrelated warning in Packages/foo\n"
           "Failed to import Assets/AssetStudio/Knight/Knight.png: bad header\n"
           "Assets/AssetStudio/Knight/Knight.prefab warning: missing script\n"
           "It looks like another Unity instance is running with this project open.\n")
    errors, warnings = preview.unity_log_issues(log, "Assets/AssetStudio/Knight")
    assert errors == ["Failed to import Assets/AssetStudio/Knight/Knight.png: bad header",
                      "It looks like another Unity instance is running with this project open."]
    assert warnings == ["Assets/AssetStudio/Knight/Knight.prefab warning: missing script"]


async def test_preview_godot_imports_then_renders(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake(args, cwd, env, timeout_s, cancel):
        calls.append(args)
        if "--import" in args:
            return ProcResult(0, "Godot Engine v4\nWARNING: texture is large\n")
        Path(args[-2]).write_bytes(_png_bytes())
        return ProcResult(0, "PREVIEW_OK AnimatedSprite2D 640x640\n")

    monkeypatch.setattr(preview, "run_proc", fake)
    res = await preview.preview_godot(_export(tmp_path), tmp_path / "work", Path("godot.exe"), asyncio.Event())
    assert res.ok and res.png and res.png.read_bytes() == _png_bytes()
    assert res.target == "Knight.tscn" and res.warnings == ["WARNING: texture is large"] and not res.errors
    imp, run = calls
    assert imp[:2] == ["godot.exe", "--headless"]
    assert "--headless" not in run and _value_after(run, "--rendering-driver") == "opengl3"
    assert run[run.index("--") + 1] == "res://Knight/Knight.tscn"
    project = Path(_value_after(run, "--path"))
    assert (project / "project.godot").is_file() and (project / "_asset_studio_preview.gd").is_file()
    assert (project / "Knight" / "Models" / "m.glb").is_file()


async def test_preview_godot_reports_load_failure(tmp_path: Path, monkeypatch) -> None:
    async def fake(args, cwd, env, timeout_s, cancel):
        if "--import" in args:
            return ProcResult(0, "ERROR: Failed loading resource: res://Knight/Knight.png\n")
        return ProcResult(3, "PREVIEW_FAIL cannot load res://Knight/Knight.tscn\n")

    monkeypatch.setattr(preview, "run_proc", fake)
    res = await preview.preview_godot(_export(tmp_path), tmp_path / "work", Path("godot.exe"), asyncio.Event())
    assert not res.ok and res.png is None
    assert res.errors == ["ERROR: Failed loading resource: res://Knight/Knight.png",
                          "PREVIEW_FAIL cannot load res://Knight/Knight.tscn"]


def _unity_project(tmp: Path) -> Path:
    p = tmp / "UnityProj"
    (p / "Assets").mkdir(parents=True)
    (p / "ProjectSettings").mkdir()
    old = p / "Assets" / "AssetStudio" / "Knight"
    old.mkdir(parents=True)
    (old / "stale.png").write_bytes(b"x")
    return p


async def test_preview_unity_stages_export_and_reads_report(tmp_path: Path, monkeypatch) -> None:
    project = _unity_project(tmp_path)
    seen: dict = {}

    async def fake(args, cwd, env, timeout_s, cancel):
        seen["args"], seen["env"] = args, env
        Path(env["ASSET_STUDIO_PREVIEW_OUT"]).write_bytes(_png_bytes())
        Path(env["ASSET_STUDIO_PREVIEW_REPORT"]).write_text(json.dumps(
            {"ok": True, "target": "Assets/AssetStudio/Knight/Knight.prefab", "kind": "prefab", "note": "",
             "errors": [], "warnings": ["Sprite outside atlas"]}), encoding="utf-8")
        Path(_value_after(args, "-logFile")).write_text(
            "Refresh\nFailed to import Assets/AssetStudio/Knight/Knight.png\nASPREVIEW DONE code=0\n", encoding="utf-8")
        return ProcResult(0, "")

    monkeypatch.setattr(preview, "run_proc", fake)
    res = await preview.preview_unity(_export(tmp_path), tmp_path / "work", Path("Unity.exe"), project,
                                      asyncio.Event())
    args, env = seen["args"], seen["env"]
    assert "-batchmode" in args and "-nographics" not in args and "-quit" not in args
    assert _value_after(args, "-executeMethod") == "AssetStudio.AssetStudioPreview.Run"
    assert env["ASSET_STUDIO_PREVIEW_DIR"] == "Assets/AssetStudio/Knight"
    staged = project / "Assets" / "AssetStudio" / "Knight"
    assert (staged / "Knight.tscn").is_file() and not (staged / "stale.png").exists()
    assert (project / "Assets" / "AssetStudioPreview" / "Editor" / "AssetStudioPreview.cs").is_file()
    assert res.ok and res.png and res.target == "Knight.prefab"
    assert res.errors == ["Failed to import Assets/AssetStudio/Knight/Knight.png"]
    assert res.warnings == ["Sprite outside atlas"]


async def test_preview_unity_refuses_open_project(tmp_path: Path, monkeypatch) -> None:
    project = _unity_project(tmp_path)
    monkeypatch.setattr(preview, "project_locked", lambda p: True)

    async def fake(*a, **k):
        raise AssertionError("Unity must not start")

    monkeypatch.setattr(preview, "run_proc", fake)
    res = await preview.preview_unity(_export(tmp_path), tmp_path / "work", Path("Unity.exe"), project,
                                      asyncio.Event())
    assert not res.ok and "đang mở" in res.errors[0]


def test_availability_flags_missing_tools(tmp_path: Path) -> None:
    exe = tmp_path / "godot.exe"
    exe.write_bytes(b"")

    class S:
        godot_exe = exe
        unity_exe = tmp_path / "missing.exe"
        unity_project = tmp_path

    a = preview.availability(S())
    assert a["godot"]["available"] and not a["unity"]["available"] and a["unity"]["reason"]


async def _wait(client, job_id: str) -> dict:
    for _ in range(200):
        job = (await client.get(f"/api/engine-preview/jobs/{job_id}", headers=AUTH)).json()
        if job["status"] not in ("queued", "running"):
            return job
        await asyncio.sleep(0.02)
    raise AssertionError("preview job did not finish")


async def test_api_exports_runs_preview_and_stores_screenshot(app_ctx, tmp_path: Path, monkeypatch) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _sprite(app)
    exe = tmp_path / "godot.exe"
    exe.write_bytes(b"")
    app.state.settings.godot_exe = exe
    app.state.settings.unity_exe = tmp_path / "missing.exe"

    async def fake(args, cwd, env, timeout_s, cancel):
        if "--import" not in args:
            Path(args[-2]).write_bytes(_png_bytes())
        return ProcResult(0, "")

    monkeypatch.setattr(preview, "run_proc", fake)
    engines = (await client.get("/api/engine-preview", headers=AUTH)).json()["engines"]
    assert engines["godot"]["available"] and not engines["unity"]["available"]
    r = await client.post("/api/steps/sheet-1/engine-preview", json={"engine": "unity"}, headers=AUTH)
    assert r.status_code == 409 and r.json()["error"]["code"] == "engine_unavailable"

    r = await client.post("/api/steps/sheet-1/engine-preview", json={"engine": "godot", "ppu": 32}, headers=AUTH)
    assert r.status_code == 202, r.text
    job = await _wait(client, r.json()["id"])
    assert job["status"] == "succeeded" and job["target"] == "knight.tscn", job
    assert Path(job["export_dir"]).is_dir()
    blob = await client.get(f"/api/blobs/{job['image']['hash']}", headers=AUTH)
    assert blob.status_code == 200 and blob.content == _png_bytes()
    assert not (Path(app.state.settings.data_dir) / "engine_preview" / job["id"]).exists()


async def test_api_cancel_stops_running_engine(app_ctx, tmp_path: Path, monkeypatch) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _sprite(app)
    exe = tmp_path / "godot.exe"
    exe.write_bytes(b"")
    app.state.settings.godot_exe = exe

    async def fake(args, cwd, env, timeout_s, cancel):
        await cancel.wait()
        return ProcResult(None, "", "cancelled")

    monkeypatch.setattr(preview, "run_proc", fake)
    job = (await client.post("/api/steps/sheet-1/engine-preview", json={"engine": "godot"}, headers=AUTH)).json()
    await asyncio.sleep(0.05)
    r = await client.post(f"/api/engine-preview/jobs/{job['id']}/cancel", headers=AUTH)
    assert r.status_code == 200
    assert (await _wait(client, job["id"]))["status"] == "cancelled"
    assert (await client.get("/api/engine-preview/jobs/nope", headers=AUTH)).status_code == 404
