from __future__ import annotations

import io
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from PIL import Image

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import PixelStyle, ProjectStyle
from studio_core.engine.projects import apply_style
from studio_core.engine.recipe import parse_recipe
from studio_core.store import Database, SqliteRepo

TOKEN = "t8"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

STYLED = """
id: styled
inputs:
  prompt: {type: text}
  preset: {type: preset, default: studio}
  palette: {type: enum, default: auto, options: [auto, pico8]}
  colors: {type: int, default: 16, min: 2, max: 64}
  image: {type: image, required: false}
  elevation: {type: int, default: 30}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""

BRIEF = """
id: brief
inputs:
  idea: {type: text}
  style: {type: text, default: ""}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""

STYLE = ProjectStyle(prompt_prefix="pixel art", prompt_suffix="clean outline", preset="unity",
                     pixel=PixelStyle(palette="pico8", colors=12), reference_image="b" * 64,
                     inputs={"elevation": 45, "res": 512})


def test_apply_style_fills_only_empty_declared_inputs() -> None:
    recipe = parse_recipe(STYLED.encode())
    out, applied = apply_style(recipe, {"prompt": "knight", "preset": "godot"}, STYLE)
    assert out["prompt"] == "pixel art, knight, clean outline"
    assert out["preset"] == "godot"
    assert out["palette"] == "pico8" and out["colors"] == 12 and out["image"] == "b" * 64
    assert out["elevation"] == 45 and "res" not in out
    assert sorted(applied) == ["colors", "elevation", "image", "palette", "prompt"]

    again, applied2 = apply_style(recipe, out, STYLE)
    assert again["prompt"] == out["prompt"] and applied2 == []


def test_apply_style_skips_values_the_recipe_rejects() -> None:
    recipe = parse_recipe(STYLED.encode())
    style = ProjectStyle(pixel=PixelStyle(palette="endesga32", colors=999), inputs={"elevation": "high"})
    out, applied = apply_style(recipe, {"prompt": "x", "elevation": None}, style)
    assert applied == [] and out == {"prompt": "x", "elevation": None}


def test_apply_style_brief_recipe_gets_look_in_style_input() -> None:
    recipe = parse_recipe(BRIEF.encode())
    out, applied = apply_style(recipe, {"idea": "a goblin"}, STYLE)
    assert out == {"idea": "a goblin", "style": "pixel art, clean outline"} and applied == ["style"]
    out, applied = apply_style(recipe, {"idea": "a goblin", "style": "watercolor"}, STYLE)
    assert out["style"] == "watercolor" and applied == []


async def test_migration_adds_project_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL, recipe_hash TEXT NOT NULL, "
                "title TEXT NOT NULL DEFAULT '', inputs TEXT NOT NULL DEFAULT '{}', params TEXT NOT NULL DEFAULT '{}', "
                "status TEXT NOT NULL, error TEXT, created_at REAL NOT NULL, ended_at REAL)")
    con.execute("INSERT INTO runs (id, recipe_id, recipe_hash, status, created_at) VALUES ('r1', 'x', 'h', 'succeeded', 1)")
    con.commit()
    con.close()
    db = Database(path)
    await db.open()
    try:
        repo = SqliteRepo(db)
        assert [r.project_id for r in await repo.list_runs(project_id="default")] == ["default"]
        idx = {r["name"] for r in await db.fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_runs_project", "idx_versions_project"} <= idx
    finally:
        await db.close()


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    rdir = tmp_path / "builtin"
    rdir.mkdir()
    (rdir / "styled.yaml").write_text(STYLED, encoding="utf-8")
    (rdir / "brief.yaml").write_text(BRIEF, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "none")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def _upload(c: httpx.AsyncClient) -> str:
    buf = io.BytesIO()
    Image.new("RGBA", (4, 4), (200, 10, 10, 255)).save(buf, "PNG")
    r = await c.post("/api/blobs", files={"file": ("ref.png", buf.getvalue(), "image/png")}, headers=AUTH)
    return r.json()["hash"]


async def test_projects_crud(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/projects", json={"name": "  Goblin Raid ", "style": {"preset": "unity"}}, headers=AUTH)
    assert r.status_code == 201
    p = r.json()
    assert p["name"] == "Goblin Raid" and p["style"]["preset"] == "unity" and p["archived"] is False
    pid = p["id"]
    assert (await client.post("/api/projects", json={"name": " "}, headers=AUTH)).status_code == 422
    bad = {"name": "x", "style": {"reference_image": "f" * 64}}
    assert (await client.post("/api/projects", json=bad, headers=AUTH)).status_code == 400
    assert (await client.post("/api/projects", json={"name": "x", "style": {"preset": "nope"}},
                              headers=AUTH)).status_code == 400

    r = await client.patch(f"/api/projects/{pid}", json={"description": "orcs", "style": {"prompt_prefix": "pixel"}},
                           headers=AUTH)
    assert r.json()["description"] == "orcs" and r.json()["style"]["preset"] is None
    assert r.json()["style"]["prompt_prefix"] == "pixel" and r.json()["updated_at"] >= p["updated_at"]
    assert (await client.get(f"/api/projects/{pid}", headers=AUTH)).json()["name"] == "Goblin Raid"
    assert [x["id"] for x in (await client.get("/api/projects", headers=AUTH)).json()] == [pid]

    assert (await client.delete(f"/api/projects/{pid}", headers=AUTH)).json()["archived"] is True
    assert (await client.get("/api/projects", headers=AUTH)).json() == []
    assert len((await client.get("/api/projects?archived=true", headers=AUTH)).json()) == 1
    r = await client.patch(f"/api/projects/{pid}", json={"archived": False}, headers=AUTH)
    assert r.json()["archived"] is False
    assert (await client.get("/api/projects/nope", headers=AUTH)).status_code == 404
    assert (await client.patch("/api/projects/nope", json={}, headers=AUTH)).status_code == 404


async def test_run_and_batch_with_project(client: httpx.AsyncClient) -> None:
    ref = await _upload(client)
    style = {"prompt_prefix": "pixel art", "preset": "unity", "reference_image": ref,
             "pixel": {"palette": "pico8"}, "inputs": {"elevation": 45}}
    pid = (await client.post("/api/projects", json={"name": "P", "style": style}, headers=AUTH)).json()["id"]

    body = {"recipe_id": "styled", "inputs": {"prompt": "knight", "elevation": 10}, "project_id": pid,
            "params": {"$meta": {"source": "app"}}}
    r = await client.post("/api/runs", json=body, headers=AUTH)
    assert r.status_code == 201
    run = (await client.get(f"/api/runs/{r.json()['run_id']}", headers=AUTH)).json()["run"]
    assert run["project_id"] == pid
    assert run["inputs"]["prompt"] == "pixel art, knight" and run["inputs"]["elevation"] == 10
    assert run["inputs"]["preset"] == "unity" and run["inputs"]["image"] == ref
    meta = run["params"]["$meta"]
    assert meta["source"] == "app" and meta["project_id"] == pid
    assert sorted(meta["project_applied"]) == ["image", "palette", "preset", "prompt"]

    r = await client.post("/api/batches", json={"recipe_id": "styled", "project_id": pid,
                                                "items": [{"inputs": {"prompt": "a"}}, {"inputs": {"prompt": "b"}}]},
                          headers=AUTH)
    assert r.status_code == 201
    for rid, prompt in zip(r.json()["run_ids"], "ab"):
        run = (await client.get(f"/api/runs/{rid}", headers=AUTH)).json()["run"]
        assert run["inputs"]["prompt"] == f"pixel art, {prompt}" and run["inputs"]["elevation"] == 45
        assert run["params"]["$meta"]["batch_total"] == 2 and run["params"]["$meta"]["project_id"] == pid

    await client.post("/api/runs", json={"recipe_id": "styled", "inputs": {"prompt": "plain"}}, headers=AUTH)
    mine = (await client.get(f"/api/runs?project_id={pid}", headers=AUTH)).json()
    assert len(mine) == 3 and all(x["project_id"] == pid for x in mine)
    plain = (await client.get("/api/runs?project_id=default", headers=AUTH)).json()
    assert [x["inputs"]["prompt"] for x in plain] == ["plain"] and "$meta" not in plain[0]["params"]
    assert (await client.get(f"/api/versions?project_id={pid}", headers=AUTH)).json() == []

    summary = (await client.get(f"/api/projects/{pid}/summary", headers=AUTH)).json()
    assert summary["runs_total"] == 3 and summary["approved_versions"] == 0
    assert summary["last_activity"] > 0 and set(summary) >= {"runs", "versions", "cover_hash"}
    listed = (await client.get("/api/projects?summary=true", headers=AUTH)).json()
    assert listed[0]["summary"]["runs_total"] == 3

    before = len((await client.get("/api/runs", headers=AUTH)).json())
    r = await client.post("/api/batches", json={"recipe_id": "styled", "project_id": "nope",
                                                "items": [{"inputs": {"prompt": "a"}}]}, headers=AUTH)
    assert r.status_code == 404
    r = await client.post("/api/runs", json={"recipe_id": "styled", "inputs": {"prompt": "a"}, "project_id": "nope"},
                          headers=AUTH)
    assert r.status_code == 404
    assert len((await client.get("/api/runs", headers=AUTH)).json()) == before


class _FakeLink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method: str, path: str, **kw: object) -> object:
        self.calls.append((method, path, kw))
        if path == "/api/recipes":
            return [{"id": "styled", "name": "S", "inputs": {"prompt": {"type": "text"}}, "steps": []}]
        if path == "/api/projects":
            return [{"id": "p1", "name": "P", "style": {"preset": "unity"}, "archived": False,
                     "summary": {"runs_total": 4, "approved_versions": 2}}]
        return {"run_id": "r1"}


async def test_mcp_projects_tools() -> None:
    from studio_core.mcp.server import StudioTools

    link = _FakeLink()
    tools = StudioTools(link)  # type: ignore[arg-type]
    listed = await tools.list_projects(False)
    assert listed["projects"][0] == {"project_id": "p1", "name": "P", "description": "", "archived": False,
                                     "style": {"preset": "unity"}, "runs_total": 4, "approved_versions": 2}
    out = await tools.start_run("styled", {"prompt": "x"}, None, "human", "p1")
    assert out["project_id"] == "p1"
    assert link.calls[-1][2]["json"]["project_id"] == "p1"
    await tools.start_run("styled", {"prompt": "x"}, None, "human")
    assert "project_id" not in link.calls[-1][2]["json"]
