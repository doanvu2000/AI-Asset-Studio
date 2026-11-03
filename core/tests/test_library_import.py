from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.export.shaders import PRESETS, catalog
from studio_core.paths import recipes_dir

TOKEN = "tli"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
async def ctx(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    rdir = tmp_path / "recipes"
    rdir.mkdir()
    shutil.copy(recipes_dir() / "library-import.yaml", rdir)
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "no-comfy")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield app, client


def _png(path: Path, color: str = "red") -> Path:
    Image.new("RGBA", (8, 8), color).save(path)
    return path


async def test_shader_catalog_matches_export_catalog(ctx) -> None:
    _, c = ctx
    assert (await c.get("/api/shaders")).status_code == 401
    r = await c.get("/api/shaders", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == catalog() and {p["id"] for p in r.json()} == set(PRESETS)


async def test_import_creates_approved_versions_in_project(ctx, tmp_path: Path) -> None:
    _, c = ctx
    pid = (await c.post("/api/projects", json={"name": "Game"}, headers=AUTH)).json()["id"]
    a, b = _png(tmp_path / "hero.png"), _png(tmp_path / "tree.png", "green")
    r = await c.post("/api/library/import", json={"paths": [str(a), str(b), str(a)], "project_id": pid}, headers=AUTH)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["project_id"] == pid and len(body["versions"]) == 2
    assert {v["status"] for v in body["versions"]} == {"approved"}
    assert {v["name"] for v in body["versions"]} == {"hero.png", "tree.png"}
    vs = (await c.get(f"/api/versions?project_id={pid}", headers=AUTH)).json()
    assert {v["run_id"] for v in vs} == {body["run_id"]} and len(vs) == 2


async def test_import_draft_and_asset_name_default_project(ctx, tmp_path: Path) -> None:
    _, c = ctx
    r = await c.post("/api/library/import", json={"paths": [str(_png(tmp_path / "x.png"))], "approve": False,
                                                  "asset_name": "Boss"}, headers=AUTH)
    assert r.status_code == 201
    v = r.json()["versions"][0]
    assert v["status"] == "draft" and v["asset_name"] == "Boss" and r.json()["project_id"] == "default"


@pytest.mark.parametrize("paths,status", [([], 400), (["relative.png"], 400), (["Z:/nope/missing.png"], 404)])
async def test_import_rejects_bad_paths(ctx, paths: list[str], status: int) -> None:
    _, c = ctx
    r = await c.post("/api/library/import", json={"paths": paths}, headers=AUTH)
    assert r.status_code == status


async def test_import_rejects_extension_folder_and_unknown_project(ctx, tmp_path: Path) -> None:
    _, c = ctx
    exe = tmp_path / "run.exe"
    exe.write_bytes(b"MZ")
    assert (await c.post("/api/library/import", json={"paths": [str(exe)]}, headers=AUTH)).status_code == 400
    assert (await c.post("/api/library/import", json={"paths": [str(tmp_path)]}, headers=AUTH)).status_code == 404
    png = _png(tmp_path / "a.png")
    r = await c.post("/api/library/import", json={"paths": [str(png)], "project_id": "nope"}, headers=AUTH)
    assert r.status_code == 404
    assert (await c.get("/api/versions", headers=AUTH)).json() == []
    assert (await c.post("/api/library/import", json={"paths": [str(png)]})).status_code == 401
