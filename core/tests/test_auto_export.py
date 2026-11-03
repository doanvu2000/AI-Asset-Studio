import asyncio
import sqlite3
import time
from pathlib import Path

from studio_core.contracts import VersionRow, VersionStatus
from studio_core.store import Database, SqliteRepo

from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)
from test_step_export import _frame, _sprite  # noqa: F401


async def _project(client, **patch) -> str:
    pid = (await client.post("/api/projects", json={"name": "Game"}, headers=AUTH)).json()["id"]
    if patch:
        r = await client.patch(f"/api/projects/{pid}", json=patch, headers=AUTH)
        assert r.status_code == 200, r.text
    return pid


async def _version(app, pid: str, vid: str = "v1") -> str:
    blob = await app.state.blobs.put_bytes(_frame(0), name="sheet.png", mime="image/png")
    await app.state.repo.add_version(VersionRow(id=vid, project_id=pid, asset_name="knight", run_id="r1",
                                                step_id="sheet-1", blob=blob, created_at=time.time()))
    return vid


async def _events(app, version_id: str, n: int = 1, timeout: float = 15) -> list[dict]:
    got: list[dict] = []

    async def collect() -> None:
        async for ev in app.state.bus.subscribe(after_seq=0):
            if ev.type == "project.auto_export" and ev.data["version_id"] == version_id:
                got.append(ev.data)
                if len(got) >= n:
                    return

    await asyncio.wait_for(collect(), timeout)
    return got


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


async def test_approve_exports_flutter_and_repeat_is_stable(app_ctx, tmp_path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _sprite(app)
    dest = tmp_path / "Dự án game" / "my app"
    dest.mkdir(parents=True)
    pid = await _project(client, engine_target="flutter", auto_export=True, auto_export_dir=str(dest))
    await _version(app, pid)

    assert (await client.post("/api/versions/v1/approve", headers=AUTH)).status_code == 200
    (ev,) = await _events(app, "v1")
    assert ev["ok"] is True and ev["engine"] == "flutter" and ev["project_id"] == pid
    out = Path(ev["path"])
    assert out.parent == dest and out.is_dir()
    first = _files(dest)
    assert any(f.endswith("knight_sheet.png") or f.endswith(".png") for f in first) and len(first) >= 2

    await client.post("/api/versions/v1/reject", headers=AUTH)
    await asyncio.gather(*(client.post("/api/versions/v1/approve", headers=AUTH) for _ in range(2)))
    evs = await _events(app, "v1", n=3)
    assert all(e["ok"] for e in evs) and {e["path"] for e in evs} == {ev["path"]}
    assert _files(dest) == first
    assert [p.name for p in dest.iterdir()] == [out.name]
    assert not [p for p in dest.rglob("*") if p.name.endswith(".tmp")]


async def test_godot_target_inside_project(app_ctx, tmp_path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _sprite(app)
    game = tmp_path / "godot game"
    (game / "art").mkdir(parents=True)
    (game / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    pid = await _project(client, engine_target="godot", auto_export=True, auto_export_dir=str(game / "art"))
    await _version(app, pid)
    await client.post("/api/versions/v1/approve", headers=AUTH)
    (ev,) = await _events(app, "v1")
    assert ev["ok"] is True, ev
    assert _files(game / "art") and Path(ev["path"]).parent == game / "art"


async def test_godot_target_outside_project_reports_error(app_ctx, tmp_path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _sprite(app)
    pid = await _project(client, engine_target="godot", auto_export=True, auto_export_dir=str(tmp_path))
    await _version(app, pid)
    assert (await client.post("/api/versions/v1/approve", headers=AUTH)).status_code == 200
    (ev,) = await _events(app, "v1")
    assert ev["ok"] is False and "Godot" in ev["error"]


async def test_off_by_default_exports_nothing(app_ctx, tmp_path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _sprite(app)
    dest = tmp_path / "game"
    dest.mkdir()
    pid = await _project(client)
    assert (await client.get(f"/api/projects/{pid}", headers=AUTH)).json()["auto_export"] is False
    r = await client.patch(f"/api/projects/{pid}", json={"engine_target": "flutter", "auto_export_dir": str(dest)},
                           headers=AUTH)
    assert r.json()["auto_export"] is False
    await _version(app, pid)
    assert (await client.post("/api/versions/v1/approve", headers=AUTH)).status_code == 200
    await asyncio.sleep(0.5)
    assert not _files(dest)
    assert not [e for e in app.state.bus._buffer if e.type == "project.auto_export"]


async def test_bad_folder_does_not_break_approve(app_ctx, tmp_path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _sprite(app)
    pid = await _project(client, engine_target="flutter", auto_export=True, auto_export_dir=str(tmp_path / "nope"))
    await _version(app, pid)
    r = await client.post("/api/versions/v1/approve", headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == VersionStatus.APPROVED.value
    (ev,) = await _events(app, "v1")
    assert ev["ok"] is False and "nope" in ev["error"]

    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "knight").write_text("a file where the export folder should go", encoding="utf-8")
    await client.patch(f"/api/projects/{pid}", json={"auto_export_dir": str(dest)}, headers=AUTH)
    await client.post("/api/versions/v1/reject", headers=AUTH)
    assert (await client.post("/api/versions/v1/approve", headers=AUTH)).status_code == 200
    evs = await _events(app, "v1", n=2)
    assert evs[-1]["ok"] is False and evs[-1]["error"]


async def test_missing_engine_or_dir_reports(app_ctx) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _sprite(app)
    pid = await _project(client, auto_export=True)
    await _version(app, pid)
    await client.post("/api/versions/v1/approve", headers=AUTH)
    (ev,) = await _events(app, "v1")
    assert ev["ok"] is False and "thư mục" in ev["error"]


async def test_migration_twice_adds_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', "
                "style TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL, "
                "archived INTEGER NOT NULL DEFAULT 0)")
    con.execute("INSERT INTO projects (id, name, created_at, updated_at) VALUES ('p1', 'Old', 1, 1)")
    con.commit()
    con.close()
    for _ in range(2):
        db = Database(path)
        await db.open()
        try:
            p = await SqliteRepo(db).get_project("p1")
            assert p is not None and p.auto_export is False and p.auto_export_dir == ""
        finally:
            await db.close()
