from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import RunRow, RunStatus
from studio_core.engine.exports import recipe_exports
from studio_core.engine.recipe import load_recipes

TOKEN = "l5"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BUILTIN_DIR = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
LIMIT = 60_000

RECIPE = """
id: with-preview
name: With preview
preview: previews/with-preview.webp
inputs:
  image: {type: image}
steps:
  - {id: fit, kind: op.trim_pad_resize, in: {images: inputs.image}}
outputs:
  final: fit.images
"""


@pytest.fixture
async def ctx(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    rdir = tmp_path / "builtin"
    (rdir / "previews").mkdir(parents=True)
    (rdir / "with-preview.yaml").write_text(RECIPE, encoding="utf-8")
    (rdir / "no-file.yaml").write_text(RECIPE.replace("with-preview", "no-file"), encoding="utf-8")
    (rdir / "escape.yaml").write_text(
        RECIPE.replace("with-preview.webp", "../../secret.webp").replace("with-preview", "escape"), encoding="utf-8")
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(rdir / "previews" / "with-preview.webp", "WEBP")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "no-comfy")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield app, client


async def test_list_runs_filters_by_recipe_id(ctx) -> None:
    app, client = ctx
    now = time.time()
    for i, rid in enumerate(["a", "a", "b"]):
        await app.state.repo.create_run(RunRow(id=f"r{i}", recipe_id=rid, recipe_hash="h", status=RunStatus.SUCCEEDED,
                                               created_at=now + i))
    runs = (await client.get("/api/runs", params={"recipe_id": "a"}, headers=AUTH)).json()
    assert [r["id"] for r in runs] == ["r1", "r0"]
    assert [r["id"] for r in (await client.get("/api/runs", params={"recipe_id": "b"}, headers=AUTH)).json()] == ["r2"]
    assert (await client.get("/api/runs", params={"recipe_id": "zzz"}, headers=AUTH)).json() == []
    assert len((await client.get("/api/runs", params={"recipe_id": ""}, headers=AUTH)).json()) == 3


async def test_recipe_info_reports_preview_only_when_the_file_exists(ctx) -> None:
    _, client = ctx
    by_id = {r["id"]: r for r in (await client.get("/api/recipes", headers=AUTH)).json()}
    assert by_id["with-preview"]["preview"] == "file"
    assert by_id["no-file"]["preview"] is None
    assert by_id["escape"]["preview"] is None


async def test_recipe_list_carries_output_names(ctx) -> None:
    _, client = ctx
    by_id = {r["id"]: r for r in (await client.get("/api/recipes", headers=AUTH)).json()}
    assert by_id["with-preview"]["outputs"] == ["final"]
    detail = (await client.get("/api/recipes/with-preview", headers=AUTH)).json()
    assert detail["info"]["outputs"] == list(detail["doc"]["outputs"])


async def test_preview_endpoint_serves_the_image_and_404s_otherwise(ctx) -> None:
    _, client = ctx
    ok = await client.get("/api/recipes/with-preview/preview", params={"t": TOKEN})
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/webp" and ok.content[:4] == b"RIFF"
    assert (await client.get("/api/recipes/no-file/preview", params={"t": TOKEN})).status_code == 404
    assert (await client.get("/api/recipes/escape/preview", params={"t": TOKEN})).status_code == 404
    assert (await client.get("/api/recipes/ghost/preview", params={"t": TOKEN})).status_code == 404
    assert (await client.get("/api/recipes/with-preview/preview")).status_code == 401


def test_exports_come_from_step_kinds_not_the_description() -> None:
    assert recipe_exports(["comfy.t2i_zimage_turbo", "gate.pick"]) == []
    assert recipe_exports(["blender.render_dirs", "op.sheet"]) == ["unity", "godot"]
    assert recipe_exports(["op.spine_export"]) == ["unity", "godot", "spine"]
    assert recipe_exports(["blender.rig_export"]) == ["unity", "godot", "glb", "fbx"]
    assert recipe_exports(["op.audio_post"]) == ["unity", "godot", "audio"]


def test_builtin_recipes_declare_exports_through_their_steps() -> None:
    recipes = load_recipes(BUILTIN_DIR)
    assert recipes["spine-character"].info().exports[-1] == "spine"
    assert recipes["concept-2d"].info().exports == []
    assert recipes["sprite-8dir-glb"].info().exports == ["unity", "godot"]
    assert any("glb" in r.info().exports for r in recipes.values())


def test_shipped_previews_exist_are_small_webp_and_are_packaged() -> None:
    declared = {r.id: r.preview for r in load_recipes(BUILTIN_DIR).values() if r.preview}
    assert declared, "no recipe ships a demo image"
    for rid, name in declared.items():
        path = BUILTIN_DIR / name
        assert path.name == f"{rid}.webp", rid
        assert path.is_file(), f"{rid}: {name} missing"
        assert path.stat().st_size <= LIMIT, f"{rid}: {path.stat().st_size} bytes"
        with Image.open(path) as im:
            assert im.format == "WEBP" and max(im.size) <= 512
    on_disk = {p.name for p in (BUILTIN_DIR / "previews").iterdir()}
    assert on_disk == {f"{rid}.webp" for rid in declared}, "orphan or missing preview files"
    spec = (BUILTIN_DIR.parents[1] / "packaging" / "studio-core.spec").read_text(encoding="utf-8")
    assert re.search(r'"recipes" / "previews" / "\*".*"studio_core/recipes/previews"', spec)
