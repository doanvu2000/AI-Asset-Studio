from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from test_engine_runner import Env
from test_r6_item_fx import coin, _png

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import GateResolution, RunStatus
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.hue_variants import HueVariants, rotate_hue
from studio_core.paths import recipes_dir

TOKEN = "t18b"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RECIPE = recipes_dir() / "ui-icon-variants.yaml"


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


async def test_import_from_blob_hashes(ctx) -> None:
    app, c = ctx
    ref = await app.state.blobs.put_bytes(_png(coin()), name="cand_02.png", mime="image/png")
    r = await c.post("/api/library/import", json={"hashes": [ref.hash, ref.hash]}, headers=AUTH)
    assert r.status_code == 201, r.text
    vs = r.json()["versions"]
    assert len(vs) == 1 and vs[0]["hash"] == ref.hash and vs[0]["status"] == "approved" and vs[0]["name"] == "cand_02.png"
    assert vs[0]["asset_name"] == "cand_02"


@pytest.mark.parametrize("hashes,status", [(["../../etc/passwd"], 400), (["C:/Windows/win.ini"], 400),
                                           (["A" * 64], 400), (["0" * 64], 404), ([], 400)])
async def test_import_rejects_bad_hashes(ctx, hashes: list[str], status: int) -> None:
    _, c = ctx
    r = await c.post("/api/library/import", json={"hashes": hashes}, headers=AUTH)
    assert r.status_code == status
    assert (await c.get("/api/versions", headers=AUTH)).json() == []


async def test_import_mixes_paths_and_hashes(ctx, tmp_path: Path) -> None:
    app, c = ctx
    ref = await app.state.blobs.put_bytes(_png(coin()), name="b.png", mime="image/png")
    p = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), "red").save(p)
    r = await c.post("/api/library/import", json={"paths": [str(p)], "hashes": [ref.hash], "approve": False, "group": "single"}, headers=AUTH)
    assert r.status_code == 201 and {v["name"] for v in r.json()["versions"]} == {"a.png", "b.png"}


def test_hue_variants_keep_alpha_and_differ() -> None:
    src = coin()
    a, b = rotate_hue(src, 0), rotate_hue(src, 120)
    assert a.getchannel("A").tobytes() == b.getchannel("A").tobytes() == src.getchannel("A").tobytes()
    assert a.convert("RGB").tobytes() != b.convert("RGB").tobytes()


def test_recipe_lints() -> None:
    assert validate(RECIPE.read_text(encoding="utf-8"), None)[1] == []
    load_recipe_file(RECIPE)
    assert CATALOG["op.hue_variants"]["label"]


async def test_ui_icon_variants_runs_on_cpu_with_pick_gate(tmp_path: Path) -> None:
    env = Env(tmp_path, [], {"ui-icon-variants": RECIPE.read_text(encoding="utf-8")})
    env.registry.register(HueVariants())
    ref = await env.blobs.put_bytes(_png(coin()), name="coin.png", mime="image/png")
    run_id = await env.engine.start_run("ui-icon-variants", {"image": ref.hash, "count": 4})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert steps["pick"].status.value == "awaiting_human", {k: s.error for k, s in steps.items() if s.error}
    cands = steps["variants"].outputs["images"]
    assert [c.name for c in cands] == [f"coin_hue{d:03d}.png" for d in (0, 90, 180, 270)]
    await env.engine.resolve_gate(steps["pick"].id, GateResolution(picked=[cands[2].hash]))
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    assert env.status(run_id) == RunStatus.SUCCEEDED
    assert [b.hash for b in env.steps(run_id)["pick"].outputs["images"]] == [cands[2].hash]


async def test_import_mixed_per_file_names_and_dedupes(ctx, tmp_path: Path) -> None:
    app, c = ctx
    ref = await app.state.blobs.put_bytes(_png(coin()), name="a.png", mime="image/png")
    p = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), "red").save(p)
    r = await c.post("/api/library/import", json={"paths": [str(p)], "hashes": [ref.hash]}, headers=AUTH)
    assert r.status_code == 201, r.text
    assert [i["asset_name"] for i in r.json()["items"]] == ["a", "a 2"]
