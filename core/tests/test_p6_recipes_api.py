from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi import FastAPI

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import RecipeError, parse_recipe

TOKEN = "t6"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

BUILTIN = """
id: demo
name: Demo
inputs:
  size: {type: int, default: 64}
steps:
  - {id: fit, kind: op.trim_pad_resize, in: {images: inputs.image}, params: {size: "${inputs.size}"}}
outputs:
  final: fit.images
""".replace("inputs:\n", "inputs:\n  image: {type: image}\n", 1)


@pytest.fixture
async def ctx(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, Settings]]:
    rdir = tmp_path / "builtin"
    rdir.mkdir()
    (rdir / "demo.yaml").write_text(BUILTIN, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "no-comfy")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield app, client, settings


def test_recipe_errors_carry_step_id() -> None:
    bad = BUILTIN.replace("in: {images: inputs.image}", "in: {images: ghost.images}")
    with pytest.raises(RecipeError) as e:
        parse_recipe(bad.encode())
    assert e.value.step == "fit" and "ghost" in str(e.value)
    dup = BUILTIN.replace("outputs:", "  - {id: fit, kind: op.downscale}\noutputs:")
    with pytest.raises(RecipeError) as e:
        parse_recipe(dup.encode())
    assert e.value.step == "fit"


def test_validate_lints_ports_without_registry() -> None:
    doc: dict[str, Any] = yaml.safe_load(BUILTIN)
    doc["steps"].append({"id": "sheet", "kind": "op.sheet", "in": {"pics": "fit.imgs"}})
    recipe, issues = validate(doc, None)
    assert recipe is not None
    msgs = {(i["step"], i["field"], i["level"]) for i in issues}
    assert ("sheet", "in.pics", "warning") in msgs
    assert any(i["step"] == "sheet" and "no output 'imgs'" in i["message"] for i in issues)


async def test_kinds_describe_ports_and_params(ctx) -> None:
    _, client, _ = ctx
    kinds = {k["kind"]: k for k in (await client.get("/api/kinds", headers=AUTH)).json()}
    sheet = kinds["op.sheet"]
    assert sheet["lane"] == "cpu" and sheet["in"] == CATALOG["op.sheet"]["in"]
    assert sheet["params"]["preset"]["type"] == "preset"
    assert kinds["gate.pick"]["lane"] == "none"
    assert kinds["blender.render_dirs"]["out"] == {"images": "image[]", "meta": "json"}
    assert not any(k.startswith("comfy.") and k != "comfy.*" for k in kinds)


async def test_kinds_include_comfy_workflows(tmp_path: Path) -> None:
    wf = tmp_path / "wf"
    wf.mkdir()
    (wf / "index.json").write_text('{"t2i": {"file": "t2i.json", "images": [], "texts": [{"key": "6.text"}],'
                                   ' "outputs": [{"node": "9", "class": "SaveImage"}]},'
                                   ' "mesh": {"file": "m.json", "images": [{"key": "1.image"}],'
                                   ' "outputs": [{"node": "3", "class": "SaveGLB"}]}}', encoding="utf-8")
    for n in ("t2i.json", "m.json"):
        (wf / n).write_text("{}", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=tmp_path / "none", comfy_workflows_dir=wf)
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            kinds = {k["kind"]: k for k in (await client.get("/api/kinds", headers=AUTH)).json()}
    assert kinds["comfy.t2i"]["lane"] == "gpu" and kinds["comfy.t2i"]["out"] == {"images": "image[]"}
    assert "6.text" in kinds["comfy.t2i"]["params"] and kinds["comfy.t2i"]["free_params"]
    assert kinds["comfy.mesh"]["in"] == {"image": "image"} and kinds["comfy.mesh"]["out"] == {"meshes": "mesh"}


async def test_validate_endpoint_reports_step_ids(ctx) -> None:
    _, client, _ = ctx
    r = (await client.post("/api/recipes/validate", json={"yaml": BUILTIN}, headers=AUTH)).json()
    assert r["ok"] and r["issues"] == [] and r["info"]["id"] == "demo"
    bad = BUILTIN.replace("op.trim_pad_resize", "op.nope")
    r = (await client.post("/api/recipes/validate", json={"yaml": bad}, headers=AUTH)).json()
    assert not r["ok"] and r["issues"][0]["step"] == "fit" and "unknown step kind" in r["issues"][0]["message"]
    r = (await client.post("/api/recipes/validate", json={"yaml": "id: [x"}, headers=AUTH)).json()
    assert not r["ok"] and "invalid YAML" in r["issues"][0]["message"]
    doc = yaml.safe_load(BUILTIN)
    doc["steps"][0]["map_param"] = "nope"
    r = (await client.post("/api/recipes/validate", json={"doc": doc}, headers=AUTH)).json()
    assert not r["ok"] and r["issues"][0]["step"] == "fit"


async def test_save_shadows_builtin_and_delete_reverts(ctx) -> None:
    _, client, settings = ctx
    builtin_file = settings.recipes_dir / "demo.yaml"
    before = builtin_file.read_bytes()
    got = (await client.get("/api/recipes/demo", headers=AUTH)).json()
    assert got["source"] == "builtin" and got["has_builtin"] and got["doc"]["id"] == "demo"
    doc = got["doc"]
    doc["name"] = "Demo (của tôi)"
    doc["ui"] = {"positions": {"fit": [10, 20]}}
    r = await client.put("/api/recipes/demo", json={"doc": doc}, headers=AUTH)
    assert r.status_code == 200 and r.json()["source"] == "override"
    assert builtin_file.read_bytes() == before
    user_file = settings.user_recipes_dir / "demo.yaml"
    assert yaml.safe_load(user_file.read_text(encoding="utf-8"))["name"] == "Demo (của tôi)"
    listed = {x["id"]: x for x in (await client.get("/api/recipes", headers=AUTH)).json()}
    assert listed["demo"]["name"] == "Demo (của tôi)" and listed["demo"]["source"] == "override"
    again = (await client.get("/api/recipes/demo", headers=AUTH)).json()
    assert again["doc"]["ui"]["positions"]["fit"] == [10, 20]

    r = await client.delete("/api/recipes/demo", headers=AUTH)
    assert r.json() == {"ok": True, "restored_builtin": True} and not user_file.exists()
    listed = {x["id"]: x for x in (await client.get("/api/recipes", headers=AUTH)).json()}
    assert listed["demo"]["name"] == "Demo" and listed["demo"]["source"] == "builtin"
    assert (await client.delete("/api/recipes/demo", headers=AUTH)).status_code == 404


async def test_save_new_user_recipe_and_reject_invalid(ctx) -> None:
    _, client, _ = ctx
    text = BUILTIN.replace("id: demo", "id: mine")
    r = await client.put("/api/recipes/mine", json={"yaml": text}, headers=AUTH)
    assert r.status_code == 200 and r.json()["source"] == "user" and not r.json()["has_builtin"]
    run = await client.post("/api/runs", json={"recipe_id": "mine", "inputs": {"image": "0" * 64}}, headers=AUTH)
    assert run.status_code == 400 and "unknown blob" in run.json()["error"]["message"]

    r = await client.put("/api/recipes/other", json={"yaml": text}, headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["issues"][0]["field"] == "id"
    r = await client.put("/api/recipes/mine", json={"yaml": text.replace("op.trim_pad_resize", "op.zzz")},
                         headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["issues"][0]["step"] == "fit"
    r = await client.delete("/api/recipes/mine", headers=AUTH)
    assert r.json()["restored_builtin"] is False
    assert (await client.get("/api/recipes/mine", headers=AUTH)).status_code == 404
