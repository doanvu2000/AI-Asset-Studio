from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.gdd import ManifestPlanner, build_prompt
from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.engine import manifest as mf
from studio_core.engine.manifest import ManifestRow

TOKEN = "tm"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

STYLED = """
id: styled
inputs:
  prompt: {type: text}
  size: {type: int, default: 512, options: [256, 512]}
  shadow: {type: enum, default: none, options: [none, drop]}
  image: {type: image, required: false}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""

BRIEF = """
id: brief
description: idea to concept
inputs:
  idea: {type: text}
  style: {type: text, default: ""}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""


@pytest.fixture
async def app_client(tmp_path: Path) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
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
            yield app, c


async def _project(c: httpx.AsyncClient, style: dict[str, Any] | None = None) -> str:
    r = await c.post("/api/projects", json={"name": "Goblin Raid", "description": "orc tower defense",
                                            "style": style or {}}, headers=AUTH)
    return r.json()["id"]


def test_row_status() -> None:
    assert mf.row_status(None) == "todo"
    assert mf.row_status({"status": "running", "approved": 0}) == "running"
    assert mf.row_status({"status": "awaiting_human", "approved": 0}) == "review"
    assert mf.row_status({"status": "succeeded", "approved": 0}) == "review"
    assert mf.row_status({"status": "succeeded", "approved": 2}) == "approved"
    assert mf.row_status({"status": "interrupted", "approved": 0}) == "failed"
    assert mf.row_status({"status": "cancelled", "approved": 0}) == "todo"


def test_csv_and_yaml_round_trip() -> None:
    rows = [ManifestRow(name="Rương báu", recipe_id="styled", inputs={"prompt": "chest, gold", "size": 256},
                        notes="loot"),
            ManifestRow(name="Goblin", recipe_id="brief", inputs={"idea": "goblin"}, run_id="r1")]
    text = mf.to_csv(rows, {rows[0].id: "todo"})
    assert text.splitlines()[0] == "name,recipe_id,notes,status,run_id,in:prompt,in:size,in:idea"
    back = mf.from_csv("﻿" + text)
    assert [(r.name, r.recipe_id, r.notes, r.run_id) for r in back] == [
        ("Rương báu", "styled", "loot", None), ("Goblin", "brief", "", "r1")]
    assert back[0].inputs == {"prompt": "chest, gold", "size": "256"} and back[1].inputs == {"idea": "goblin"}
    assert mf.from_csv('name,recipe_id,inputs\nX,styled,"{""prompt"": ""p""}"\n,,\n')[0].inputs == {"prompt": "p"}
    y = mf.from_yaml(mf.to_yaml(rows))
    assert [(r.name, r.inputs, r.run_id) for r in y] == [(r.name, r.inputs, r.run_id) for r in rows]
    assert y[0].id != rows[0].id
    assert mf.from_yaml("- {name: a, recipe_id: styled}")[0].name == "a"
    with pytest.raises(ValueError):
        mf.from_yaml("rows: 3")


def test_rows_from_proposal_cleans_model_output() -> None:
    specs = {"styled": {"prompt": {"type": "text"}, "size": {"type": "int", "options": [256, 512]},
                        "shadow": {"type": "enum", "options": ["none", "drop"]}, "image": {"type": "image"}}}
    raw = [
        {"name": "Chest", "recipe_id": "styled", "notes": "n", "inputs": [
            {"name": "prompt", "value": "wooden chest"}, {"name": "size", "value": "256"},
            {"name": "shadow", "value": "huge"}, {"name": "image", "value": "x.png"}, {"name": "bogus", "value": "1"}]},
        {"name": "Map", "recipe_id": "tilemap-9000", "inputs": []},
        {"name": "", "recipe_id": "styled", "inputs": []},
        {"name": "Sword", "recipe_id": "styled", "inputs": [{"name": "prompt", "value": "sword"}]},
        {"name": "Shield", "recipe_id": "styled", "inputs": []},
        "junk",
    ]
    rows, dropped = mf.rows_from_proposal(raw, specs, limit=2)
    assert [r.name for r in rows] == ["Chest", "Sword"]
    assert rows[0].inputs == {"prompt": "wooden chest", "size": 256} and rows[0].notes == "n"
    assert dropped == ["Map: unknown recipe 'tilemap-9000'", "Shield: over the 2-row limit"]


def test_prompt_lists_recipes_inputs_and_existing() -> None:
    recipes = [{"id": "styled", "name": "Styled", "description": "a\n b",
                "inputs": {"prompt": {"type": "text", "label": "Prompt"},
                           "size": {"type": "int", "default": 512, "options": [256, 512]},
                           "image": {"type": "image"}, "style": {"type": "text"}}}]
    p = build_prompt("A tower defense game.", recipes, 30, project="Goblin Raid", existing=["Chest"])
    assert "- styled: Styled - a b" in p and "prompt (text) - Prompt" in p
    assert "size (int, one of 256|512, default 512)" in p and "image (image file, owner attaches)" in p
    assert "style (" not in p and "Project: Goblin Raid" in p and "do not repeat): Chest" in p and "at most 30 assets" in p


async def test_manifest_crud_and_import_export(app_client: tuple[Any, httpx.AsyncClient]) -> None:
    _, c = app_client
    pid = await _project(c)
    url = f"/api/projects/{pid}/manifest"
    r = await c.get(url, headers=AUTH)
    assert r.status_code == 200 and r.json()["rows"] == [] and r.json()["updated_at"] is None
    assert (await c.get("/api/projects/nope/manifest", headers=AUTH)).status_code == 404

    rows = [{"id": "a", "name": "Chest", "recipe_id": "styled", "inputs": {"prompt": "chest"}},
            {"id": "a", "name": "Goblin", "recipe_id": "brief", "inputs": {"idea": "goblin"}, "notes": "boss"}]
    r = await c.put(url, json={"rows": rows}, headers=AUTH)
    body = r.json()
    assert r.status_code == 200 and [x["status"] for x in body["rows"]] == ["todo", "todo"]
    assert body["counts"] == {"todo": 2} and body["rows"][0]["id"] == "a" and body["rows"][1]["id"] != "a"
    assert (await c.get(url, headers=AUTH)).json()["rows"][1]["notes"] == "boss"

    csv_text = (await c.get(f"{url}/export?format=csv", headers=AUTH)).text
    assert csv_text.startswith("﻿name,recipe_id") and "Chest,styled,,todo" in csv_text
    yaml_text = (await c.get(f"{url}/export?format=yaml", headers=AUTH)).text
    assert "recipe_id: brief" in yaml_text

    r = await c.post(f"{url}/import", json={"format": "csv", "text": csv_text}, headers=AUTH)
    assert len(r.json()["rows"]) == 4 and len(r.json()["added"]) == 2
    r = await c.post(f"{url}/import", json={"format": "yaml", "text": yaml_text, "append": False}, headers=AUTH)
    assert [x["name"] for x in r.json()["rows"]] == ["Chest", "Goblin"]
    r = await c.post(f"{url}/import", json={"format": "yaml", "text": "rows: 3"}, headers=AUTH)
    assert r.status_code == 400


async def test_batch_with_per_item_recipe(app_client: tuple[Any, httpx.AsyncClient]) -> None:
    _, c = app_client
    body = {"items": [{"recipe_id": "styled", "inputs": {"prompt": "a"}, "meta": {"manifest_row": "r1"}},
                      {"recipe_id": "brief", "inputs": {"idea": "b"}}]}
    r = await c.post("/api/batches", json=body, headers=AUTH)
    assert r.status_code == 201
    runs = [(await c.get(f"/api/runs/{rid}", headers=AUTH)).json()["run"] for rid in r.json()["run_ids"]]
    assert [x["recipe_id"] for x in runs] == ["styled", "brief"]
    assert runs[0]["params"]["$meta"]["manifest_row"] == "r1" and runs[1]["params"]["$meta"]["batch_total"] == 2

    mixed = {"recipe_id": "brief", "items": [{"inputs": {"idea": "x"}}, {"recipe_id": "styled", "inputs": {"prompt": "y"}}]}
    r = await c.post("/api/batches", json=mixed, headers=AUTH)
    runs = [(await c.get(f"/api/runs/{rid}", headers=AUTH)).json()["run"] for rid in r.json()["run_ids"]]
    assert [x["recipe_id"] for x in runs] == ["brief", "styled"]

    before = len((await c.get("/api/runs", headers=AUTH)).json())
    assert (await c.post("/api/batches", json={"items": [{"inputs": {}}]}, headers=AUTH)).status_code == 400
    bad = {"items": [{"recipe_id": "styled", "inputs": {"prompt": "ok"}}, {"recipe_id": "zz"}]}
    r = await c.post("/api/batches", json=bad, headers=AUTH)
    assert r.status_code == 404 and "item 2" in r.json()["error"]["message"]
    assert len((await c.get("/api/runs", headers=AUTH)).json()) == before


async def test_manifest_run_applies_style_and_tracks_status(app_client: tuple[Any, httpx.AsyncClient]) -> None:
    _, c = app_client
    pid = await _project(c, {"prompt_prefix": "pixel art", "inputs": {"size": 256}})
    url = f"/api/projects/{pid}/manifest"
    rows = [{"id": "c", "name": "Chest", "recipe_id": "styled", "inputs": {"prompt": "chest"}},
            {"id": "g", "name": "Goblin", "recipe_id": "brief", "inputs": {"idea": "goblin"}},
            {"id": "x", "name": "Empty", "recipe_id": "", "inputs": {}}]
    await c.put(url, json={"rows": rows}, headers=AUTH)
    assert (await c.post(f"{url}/run", json={}, headers=AUTH)).status_code == 400
    assert (await c.post(f"{url}/run", json={"row_ids": ["nope"]}, headers=AUTH)).status_code == 404

    r = await c.post(f"{url}/run", json={"row_ids": ["c", "g"]}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert len(body["run_ids"]) == 2 and body["batch_id"]
    by_id = {x["id"]: x for x in body["rows"]}
    assert by_id["c"]["run_id"] == body["run_ids"][0] and by_id["x"]["run_id"] is None
    assert by_id["c"]["status"] in ("running", "review") and by_id["x"]["status"] == "todo"

    chest = (await c.get(f"/api/runs/{body['run_ids'][0]}", headers=AUTH)).json()["run"]
    assert chest["project_id"] == pid and chest["title"] == "Chest"
    assert chest["inputs"]["prompt"] == "pixel art, chest" and chest["inputs"]["size"] == 256
    assert chest["params"]["$meta"]["manifest_row"] == "c" and chest["params"]["$meta"]["source"] == "manifest"
    goblin = (await c.get(f"/api/runs/{body['run_ids'][1]}", headers=AUTH)).json()["run"]
    assert goblin["recipe_id"] == "brief" and goblin["inputs"]["style"] == "pixel art"

    await c.post(f"/api/runs/{body['run_ids'][0]}/cancel", headers=AUTH)
    got = {x["id"]: x for x in (await c.get(url, headers=AUTH)).json()["rows"]}
    assert got["c"]["run_id"] == body["run_ids"][0]


async def test_from_gdd_with_fake_claude(app_client: tuple[Any, httpx.AsyncClient], tmp_path: Path) -> None:
    app, c = app_client
    pid = await _project(c)
    url = f"/api/projects/{pid}/manifest"
    await c.put(url, json={"rows": [{"name": "Old chest", "recipe_id": "styled", "inputs": {"prompt": "c"}}]},
                headers=AUTH)
    prompts: list[str] = []
    options: list[Any] = []

    async def query(content: list[dict[str, Any]], opts: Any) -> ClaudeReply:
        prompts.append(next(b["text"] for b in content if b.get("type") == "text"))
        options.append(opts)
        return ClaudeReply({"rows": [
            {"name": "Goblin archer", "recipe_id": "brief", "notes": "enemy",
             "inputs": [{"name": "idea", "value": "goblin with bow"}, {"name": "style", "value": "watercolor"}]},
            {"name": "Tower", "recipe_id": "styled", "notes": "",
             "inputs": [{"name": "prompt", "value": "stone tower"}, {"name": "size", "value": "256"}]},
            {"name": "Song", "recipe_id": "audio-music", "notes": "", "inputs": []},
        ]}, "", {"cost_usd": 0.02, "model": "m"})

    app.state.manifest_planner = ManifestPlanner(tmp_path / "gdd", query)
    r = await c.post(f"{url}/from-gdd", json={"text": "Orcs attack towers.", "limit": 5}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert [x["name"] for x in body["rows"]] == ["Goblin archer", "Tower"]
    assert body["rows"][0]["inputs"] == {"idea": "goblin with bow"}
    assert body["rows"][1]["inputs"] == {"prompt": "stone tower", "size": 256}
    assert body["dropped"] == ["Song: unknown recipe 'audio-music'"] and body["cost_usd"] == 0.02
    assert "Orcs attack towers." in prompts[0] and "Old chest" in prompts[0] and "- brief:" in prompts[0]
    assert "Goblin Raid. orc tower defense" in prompts[0]
    assert options[0].output_format["type"] == "json_schema"
    assert len((await c.get(url, headers=AUTH)).json()["rows"]) == 1

    r = await c.post(f"{url}/from-gdd", json={"text": "x", "append": True}, headers=AUTH)
    assert [x["name"] for x in r.json()["rows"]] == ["Old chest", "Goblin archer", "Tower"]
    assert len(r.json()["added"]) == 2
    assert (await c.post(f"{url}/from-gdd", json={"text": "x", "limit": 31}, headers=AUTH)).status_code == 422
