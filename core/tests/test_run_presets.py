import sqlite3
from pathlib import Path

from studio_core.store import Database

from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)


async def _save(client, name="Rương gỗ", recipe="concept-2d", **extra):
    body = {"name": name, "recipe_id": recipe, "inputs": {"prompt": "wooden chest"}, "params": {"seed": 7}, **extra}
    return await client.post("/api/presets/runs", json=body, headers=AUTH)


async def _project(client) -> str:
    return (await client.post("/api/projects", json={"name": "Game"}, headers=AUTH)).json()["id"]


async def test_crud(app_ctx) -> None:  # noqa: F811
    _, client, _ = app_ctx
    r = await _save(client)
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["inputs"] == {"prompt": "wooden chest"} and r.json()["project_id"] is None
    got = (await client.get("/api/presets/runs?recipe_id=concept-2d", headers=AUTH)).json()
    assert [p["id"] for p in got] == [pid] and got[0]["params"] == {"seed": 7}
    assert (await client.get("/api/presets/runs?recipe_id=other", headers=AUTH)).json() == []
    assert (await client.delete(f"/api/presets/runs/{pid}", headers=AUTH)).status_code == 200
    assert (await client.delete(f"/api/presets/runs/{pid}", headers=AUTH)).status_code == 404
    assert (await client.get("/api/presets/runs", headers=AUTH)).json() == []


async def test_requires_token_and_name(app_ctx) -> None:  # noqa: F811
    _, client, _ = app_ctx
    assert (await client.get("/api/presets/runs")).status_code == 401
    assert (await _save(client, name="   ")).status_code in (400, 422)
    assert (await _save(client, name="x" * 81)).status_code in (400, 422)


async def test_project_scope(app_ctx) -> None:  # noqa: F811
    _, client, _ = app_ctx
    pid = await _project(client)
    glob = (await _save(client, name="Chung")).json()["id"]
    mine = (await _save(client, name="Của dự án", project_id=pid)).json()["id"]
    q = "/api/presets/runs?recipe_id=concept-2d"
    assert {p["id"] for p in (await client.get(q, headers=AUTH)).json()} == {glob}
    assert {p["id"] for p in (await client.get(f"{q}&project_id={pid}", headers=AUTH)).json()} == {glob, mine}
    assert (await _save(client, project_id="nope")).status_code == 404


async def test_archived_or_removed_project(app_ctx) -> None:  # noqa: F811
    app, client, _ = app_ctx
    pid = await _project(client)
    glob = (await _save(client, name="Chung")).json()["id"]
    mine = (await _save(client, name="Của dự án", project_id=pid)).json()["id"]
    assert (await client.patch(f"/api/projects/{pid}", json={"archived": True}, headers=AUTH)).status_code == 200
    q = f"/api/presets/runs?recipe_id=concept-2d&project_id={pid}"
    assert {p["id"] for p in (await client.get(q, headers=AUTH)).json()} == {glob, mine}
    # A project row removed behind the API's back must not break listing or leak its presets into other scopes.
    await app.state.repo.db.write("DELETE FROM projects WHERE id = ?", (pid,))
    assert {p["id"] for p in (await client.get(q, headers=AUTH)).json()} == {glob, mine}
    assert {p["id"] for p in (await client.get("/api/presets/runs?recipe_id=concept-2d", headers=AUTH)).json()} == {glob}
    assert (await client.delete(f"/api/presets/runs/{mine}", headers=AUTH)).status_code == 200


async def test_migration_twice_keeps_rows(tmp_path: Path) -> None:
    path = tmp_path / "studio.db"
    db = Database(path)
    await db.open()
    await db.write("INSERT INTO run_presets (id, name, recipe_id, project_id, inputs, params, created_at) "
                   "VALUES ('a', 'n', 'r', NULL, '{}', '{}', 1)")
    await db.close()
    db = Database(path)
    await db.open()
    await db.close()
    db = Database(path)
    await db.open()
    assert len(await db.fetchall("SELECT * FROM run_presets")) == 1
    await db.close()


async def test_migration_from_db_without_table(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', "
                  "style TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL, "
                  "archived INTEGER NOT NULL DEFAULT 0)")
    db = Database(path)
    await db.open()
    assert await db.fetchall("SELECT * FROM run_presets") == []
    await db.close()


async def test_patch(app_ctx) -> None:  # noqa: F811
    _, client, _ = app_ctx
    a = (await _save(client, name="A")).json()["id"]
    await _save(client, name="B")
    url = f"/api/presets/runs/{a}"
    r = await client.patch(url, json={"name": "  C  "}, headers=AUTH)
    assert r.status_code == 200 and r.json()["name"] == "C" and r.json()["inputs"] == {"prompt": "wooden chest"}
    r = await client.patch(url, json={"inputs": {"prompt": "x"}, "params": {"seed": 9}}, headers=AUTH)
    assert r.json()["name"] == "C" and r.json()["inputs"] == {"prompt": "x"} and r.json()["params"] == {"seed": 9}
    assert (await client.patch(url, json={"name": "b"}, headers=AUTH)).status_code == 409
    assert (await client.patch(url, json={"name": "c"}, headers=AUTH)).status_code == 200
    assert (await client.patch(url, json={"name": "   "}, headers=AUTH)).status_code in (400, 422)
    assert (await client.patch("/api/presets/runs/nope", json={"name": "Z"}, headers=AUTH)).status_code == 404
    assert (await client.patch(url, json={"name": "Z"})).status_code == 401
