from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.gdd_plan import GddPlanner, build_prompt, items_from_proposal, missing_inputs
from studio_core.app import build_app
from studio_core.config import Settings

TOKEN = "tg"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

STYLED = """
id: styled
inputs:
  prompt: {type: text}
  image: {type: image, required: false}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""

BRIEF = """
id: brief
description: idea to concept
inputs:
  idea: {type: text}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""


def _flow(fid: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {"id": fid, "title": f"Flow {fid}", "description": "demo pack", "inputs": inputs,
            "nodes": [{"id": "a", "recipe_id": "brief", "map": {"idea": "workflow.inputs.idea"}}]}


@pytest.fixture
async def app_client(tmp_path: Path) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    rdir = tmp_path / "builtin"
    rdir.mkdir()
    (rdir / "styled.yaml").write_text(STYLED, encoding="utf-8")
    (rdir / "brief.yaml").write_text(BRIEF, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "none",
                        flows_dir=tmp_path / "noflows")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            for f in (_flow("pack", {"idea": {"type": "text"}}),
                      _flow("imgpack", {"idea": {"type": "text"}, "shot": {"type": "image"}})):
                assert (await c.post("/api/flows", json=f, headers=AUTH)).status_code == 201
            yield app, c


def _reply(items: list[dict[str, Any]]) -> Any:
    prompts: list[str] = []

    async def query(content: list[dict[str, Any]], opts: Any) -> ClaudeReply:
        prompts.append(next(b["text"] for b in content if b.get("type") == "text"))
        return ClaudeReply({"items": items}, "", {"cost_usd": 0.03, "model": "m"})

    query.prompts = prompts  # type: ignore[attr-defined]
    return query


ITEMS = [
    {"name": "Hero", "kind": "flow", "target_id": "pack", "notes": "main",
     "inputs": [{"name": "idea", "value": "knight"}, {"name": "bogus", "value": "x"}]},
    {"name": "Cover", "kind": "flow", "target_id": "imgpack", "notes": "needs shot",
     "inputs": [{"name": "idea", "value": "box art"}]},
    {"name": "Icon", "kind": "recipe", "target_id": "styled", "notes": "",
     "inputs": [{"name": "prompt", "value": "potion"}]},
    {"name": "Ghost", "kind": "flow", "target_id": "nope", "notes": "", "inputs": []},
    {"name": "Odd", "kind": "tool", "target_id": "styled", "notes": "", "inputs": []},
]


async def test_plan_validates_and_flags_missing(app_client: tuple[Any, httpx.AsyncClient], tmp_path: Path) -> None:
    app, c = app_client
    query = _reply(ITEMS)
    app.state.gdd_planner = GddPlanner(tmp_path / "gdd", query)
    r = await c.post("/api/gdd/plan", json={"text": "A knight saves a village."}, headers=AUTH)
    assert r.status_code == 201
    body = r.json()
    by = {i["name"]: i for i in body["items"]}
    assert list(by) == ["Hero", "Cover", "Icon"]
    assert by["Hero"]["inputs"] == {"idea": "knight"} and by["Hero"]["missing"] == []
    assert by["Cover"]["missing"] == ["shot"]
    assert by["Icon"]["missing"] == []
    assert len(body["dropped"]) == 2 and body["cost_usd"] == 0.03
    assert "- pack:" in query.prompts[0] and "- styled:" in query.prompts[0] and "A knight" in query.prompts[0]
    got = await c.get(f"/api/gdd/plan/{body['id']}", headers=AUTH)
    assert got.status_code == 200 and len(got.json()["items"]) == 3
    assert (await c.get("/api/gdd/plan/nope", headers=AUTH)).status_code == 404


async def test_plan_input_errors(app_client: tuple[Any, httpx.AsyncClient], tmp_path: Path) -> None:
    app, c = app_client
    app.state.gdd_planner = GddPlanner(tmp_path / "gdd", _reply(ITEMS))
    assert (await c.post("/api/gdd/plan", json={}, headers=AUTH)).status_code == 400
    assert (await c.post("/api/gdd/plan", json={"blob_id": "0" * 64}, headers=AUTH)).status_code == 404
    r = await c.post("/api/gdd/plan", json={"text": "x", "project_id": "ghost"}, headers=AUTH)
    assert r.status_code == 404


async def test_plan_claude_failures(app_client: tuple[Any, httpx.AsyncClient], tmp_path: Path) -> None:
    app, c = app_client
    unknown = {"name": "X", "kind": "flow", "target_id": "zz", "notes": "", "inputs": []}
    app.state.gdd_planner = GddPlanner(tmp_path / "gdd", _reply([unknown]))
    r = await c.post("/api/gdd/plan", json={"text": "x"}, headers=AUTH)
    assert r.status_code == 502 and r.json()["error"]["code"] == "claude_empty_plan"

    async def bad(content: list[dict[str, Any]], opts: Any) -> ClaudeReply:
        return ClaudeReply({"oops": 1}, "", {})

    app.state.gdd_planner = GddPlanner(tmp_path / "gdd", bad)
    r = await c.post("/api/gdd/plan", json={"text": "x"}, headers=AUTH)
    assert r.status_code == 502 and r.json()["error"]["code"] == "claude_bad_plan"


async def test_plan_from_blob_file(app_client: tuple[Any, httpx.AsyncClient], tmp_path: Path) -> None:
    app, c = app_client
    query = _reply(ITEMS)
    app.state.gdd_planner = GddPlanner(tmp_path / "gdd", query)
    ref = await app.state.engine.blobs.put_bytes(b"# GDD\nDragon boss", name="gdd.md", mime="text/markdown")
    r = await c.post("/api/gdd/plan", json={"blob_id": ref.hash}, headers=AUTH)
    assert r.status_code == 201 and "Dragon boss" in query.prompts[0]
    bin_ref = await app.state.engine.blobs.put_bytes(b"\xff\xfe\x00\x80", name="x.pdf", mime="application/pdf")
    assert (await c.post("/api/gdd/plan", json={"blob_id": bin_ref.hash}, headers=AUTH)).status_code == 400


async def test_start_creates_batches_in_project(app_client: tuple[Any, httpx.AsyncClient], tmp_path: Path) -> None:
    app, c = app_client
    app.state.gdd_planner = GddPlanner(tmp_path / "gdd", _reply(ITEMS))
    pid = (await c.post("/api/projects", json={"name": "Knights"}, headers=AUTH)).json()["id"]
    plan = (await c.post("/api/gdd/plan", json={"text": "x", "project_id": pid}, headers=AUTH)).json()
    url = f"/api/gdd/plan/{plan['id']}/start"

    r = await c.post(url, json={}, headers=AUTH)
    assert r.status_code == 400 and "Cover" in r.json()["error"]["message"]

    ok = [i for i in plan["items"] if not i["missing"]]
    ok[0]["inputs"]["idea"] = "edited knight"
    ok.append({"name": "Sword", "kind": "recipe", "target_id": "styled", "inputs": {"prompt": "sword"}})
    r = await c.post(url, json={"items": ok}, headers=AUTH)
    assert r.status_code == 201, r.text
    out = r.json()
    assert len(out["flow_batch_ids"]) == 1 and len(out["run_ids"]) == 2 and out["recipe_batch_id"]
    fb = (await c.get(f"/api/flow-batches/{out['flow_batch_ids'][0]}", headers=AUTH)).json()
    assert fb["project_id"] == pid and fb["total"] == 1 and fb["runs"][0]["title"] == "Hero"
    run = (await c.get(f"/api/runs/{out['run_ids'][0]}", headers=AUTH)).json()
    assert run["run"]["project_id"] == pid

    bad = {"name": "Z", "kind": "recipe", "target_id": "ghost", "inputs": {}}
    assert (await c.post(url, json={"items": [bad]}, headers=AUTH)).status_code == 404
    assert (await c.post(url, json={"items": []}, headers=AUTH)).status_code == 400


def test_helpers() -> None:
    specs = {"a": {"type": "text"}, "b": {"type": "text", "default": "d"}, "c": {"type": "image", "required": False}}
    assert missing_inputs(specs, {}) == ["a"]
    items, dropped = items_from_proposal([{"name": "A", "kind": "recipe", "target_id": "r", "inputs": []}] * 3,
                                         {}, {"r": {}}, 2)
    assert len(items) == 2 and dropped == ["A: over the 2-item limit"]
    assert "(none)" in build_prompt("g", [], [], 5)
