from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate

TOKEN = "t7"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


@pytest.fixture
async def app_client(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    settings = Settings(data_dir=tmp_path / "data", comfy_workflows_dir=tmp_path / "no-comfy")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield app, client


async def test_every_registered_kind_has_catalog_entry(app_client) -> None:
    app, _ = app_client
    exact = [k for k in app.state.registry.kinds() if not k.endswith("*")]
    assert exact, "no kinds registered"
    missing = [k for k in exact if k not in CATALOG]
    assert not missing, f"kinds without catalog entry: {missing}"
    for kind in exact:
        spec = CATALOG[kind]
        assert spec.get("label") and spec["label"] != kind, kind
        assert isinstance(spec.get("in"), dict) and isinstance(spec.get("out"), dict), kind
        assert isinstance(spec.get("params"), dict), kind


async def test_rig_kinds_expose_ports(app_client) -> None:
    _, client = app_client
    kinds = {k["kind"]: k for k in (await client.get("/api/kinds", headers=AUTH)).json()}
    assert kinds["op.parts"]["out"] == {"meta": "json", "labels": "image", "preview": "image"}
    assert set(kinds["op.layers"]["in"]) == {"image", "labels", "parts", "bones"}
    assert {"skeleton", "atlas", "texture", "html"} <= set(kinds["op.spine_export"]["out"])
    assert not kinds["op.spine_export"]["free_params"]
    assert kinds["op.layers"]["params"]["rig"]["options"] == ["claude", "auto"]


def test_spine_recipe_lints_without_port_warnings() -> None:
    _, issues = validate((RECIPES / "spine-character.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
