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
    r = await c.post("/api/library/import", json={"paths": [str(a), str(b), str(a)], "project_id": pid, "group": "single"}, headers=AUTH)
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


async def test_import_per_file_default_one_asset_each(ctx, tmp_path: Path) -> None:
    _, c = ctx
    pid = (await c.post("/api/projects", json={"name": "Game"}, headers=AUTH)).json()["id"]
    (tmp_path / "d").mkdir()
    paths = [_png(tmp_path / "hero.png"), _png(tmp_path / "d" / "hero.png", "blue"), _png(tmp_path / "tree.png", "green")]
    r = await c.post("/api/library/import", json={"paths": [str(p) for p in paths], "project_id": pid}, headers=AUTH)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["group"] == "per_file" and len(body["items"]) == 3
    assert [i["asset_name"] for i in body["items"]] == ["hero", "hero 2", "tree"]
    assert len({i["run_id"] for i in body["items"]}) == 3
    assert body["run_id"] == body["items"][0]["run_id"] and body["versions"] == body["items"][0]["versions"]
    assert all(len(i["versions"]) == 1 and i["versions"][0]["asset_name"] == i["asset_name"] for i in body["items"])
    vs = (await c.get(f"/api/versions?project_id={pid}", headers=AUTH)).json()
    assert len(vs) == 3 and len({v["run_id"] for v in vs}) == 3


async def test_import_single_for_one_file_or_asset_name(ctx, tmp_path: Path) -> None:
    _, c = ctx
    a, b = _png(tmp_path / "a.png"), _png(tmp_path / "b.png", "green")
    one = (await c.post("/api/library/import", json={"paths": [str(a)]}, headers=AUTH)).json()
    assert one["group"] == "single" and len(one["items"]) == 1
    named = (await c.post("/api/library/import", json={"paths": [str(a), str(b)], "asset_name": "Boss"}, headers=AUTH)).json()
    assert named["group"] == "single" and len(named["items"]) == 1 and len(named["versions"]) == 2
    assert {v["asset_name"] for v in named["versions"]} == {"Boss"}
    r = await c.post("/api/library/import", json={"paths": [str(a), str(b)], "asset_name": "Boss", "group": "per_file"},
                     headers=AUTH)
    assert r.status_code == 400


async def test_import_per_file_midway_failure_reports_created(ctx, tmp_path: Path, monkeypatch) -> None:
    from studio_core.api import library
    app, c = ctx
    real = library._import_run
    calls = 0

    async def flaky(state, refs, title, project_id, approve):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise library.ApiError(409, "import_failed", "boom")
        return await real(state, refs, title, project_id, approve)

    monkeypatch.setattr(library, "_import_run", flaky)
    paths = [str(_png(tmp_path / f"{n}.png", col)) for n, col in (("a", "red"), ("b", "green"), ("c", "blue"))]
    r = await c.post("/api/library/import", json={"paths": paths}, headers=AUTH)
    assert r.status_code == 409
    assert "after 1 of 3 assets were already created" in r.text
    assert len((await c.get("/api/versions", headers=AUTH)).json()) == 1
