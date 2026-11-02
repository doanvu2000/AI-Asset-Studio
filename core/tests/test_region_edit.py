from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import VersionRow
from studio_core.engine.recipe import load_recipes
from studio_core.ops.region_composite import region_composite

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
AUTH = {"Authorization": "Bearer t"}


def solid(color, size=(32, 32)) -> Image.Image:
    return Image.new("RGBA", size, color)


def box_mask(box, size=(32, 32)) -> Image.Image:
    m = Image.new("L", size, 0)
    m.paste(255, box)
    return m


def test_outside_mask_is_original_and_inside_is_edit():
    orig, edit = solid((10, 20, 30, 255)), solid((200, 0, 0, 255))
    out = np.asarray(region_composite(orig, edit, box_mask((8, 8, 24, 24)), feather=0))
    assert (out[0, 0] == (10, 20, 30, 255)).all() and (out[31, 31] == (10, 20, 30, 255)).all()
    assert (out[16, 16] == (200, 0, 0, 255)).all()


def test_feather_blends_edge_but_far_pixels_stay_exact():
    orig, edit = solid((0, 0, 0, 255), (64, 64)), solid((255, 255, 255, 255), (64, 64))
    out = np.asarray(region_composite(orig, edit, box_mask((24, 24, 40, 40), (64, 64)), feather=3))
    assert 0 < out[24, 32, 0] < 255
    assert (out[0:10, 0:10, :3] == 0).all()
    assert out[32, 32, 0] > 240


def test_deterministic_and_keeps_alpha():
    orig = solid((10, 20, 30, 0))
    edit = solid((200, 0, 0, 255))
    a = region_composite(orig, edit, box_mask((4, 4, 20, 20)), feather=2)
    b = region_composite(orig, edit, box_mask((4, 4, 20, 20)), feather=2)
    assert a.tobytes() == b.tobytes()
    assert np.asarray(a)[..., 3].max() == 0
    assert np.asarray(region_composite(orig, edit, box_mask((4, 4, 20, 20)), 2, keep_alpha=False))[10, 10, 3] == 255


def test_edit_resized_to_original_and_empty_mask_rejected():
    orig, edit = solid((1, 2, 3, 255)), solid((9, 9, 9, 255), (40, 48))
    assert region_composite(orig, edit, box_mask((0, 0, 32, 32)), 0).size == (32, 32)
    with pytest.raises(ValueError):
        region_composite(orig, edit, Image.new("L", (32, 32), 0))
    with pytest.raises(ValueError):
        region_composite(orig, edit, box_mask((0, 0, 8, 8)), feather=-1)


def test_recipe_loads_with_registered_kinds():
    r = load_recipes(RECIPES)["region-edit"]
    assert r.step("edit").kind == "comfy.edit_flux2_klein4b"
    assert r.step("composite").kind == "op.region_composite"
    assert set(r.inputs) >= {"image", "mask", "prompt", "feather"}


class StubEngine:
    def __init__(self) -> None:
        self.started: list[tuple[Any, ...]] = []

    def list_recipes(self) -> list:
        return []

    async def start_run(self, recipe_id, inputs, params=None, project_id="default", title=""):
        self.started.append((recipe_id, inputs, params, project_id, title))
        return "run-x"

    async def recover(self) -> None: ...
    async def shutdown(self) -> None: ...


def _png_b64() -> str:
    buf = io.BytesIO()
    box_mask((0, 0, 4, 4), (8, 8)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def test_endpoint_starts_run_with_parent(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path)
    for d in (s.blobs_dir, s.thumbs_dir, s.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    engine = StubEngine()
    app = build_app(s, "t", engine=engine, load_plugins=False)
    async with app.router.lifespan_context(app):
        buf = io.BytesIO()
        solid((1, 2, 3, 255), (8, 8)).save(buf, "PNG")
        ref = await app.state.blobs.put_bytes(buf.getvalue(), name="a.png", mime="image/png")
        await app.state.repo.add_version(VersionRow(id="v1", asset_name="shield", run_id="r", step_id="s", blob=ref,
                                                    created_at=1.0))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            body = {"mask": _png_b64(), "prompt": "đổi màu khiên sang đỏ", "feather": 4}
            r = await c.post("/api/versions/v1/region-edit", json=body, headers=AUTH)
            assert r.status_code == 201 and r.json() == {"run_id": "run-x", "parent_version": "v1"}
            recipe, inputs, params, project, title = engine.started[0]
            assert recipe == "region-edit" and inputs["image"] == ref.hash and inputs["feather"] == 4
            assert params == {"$meta": {"parent_version": "v1"}} and title == "shield"
            assert (await c.post("/api/versions/zz/region-edit", json=body, headers=AUTH)).status_code == 404
            bad = await c.post("/api/versions/v1/region-edit", json={**body, "mask": "bm90cG5n"}, headers=AUTH)
            assert bad.status_code == 422 and bad.json()["error"]["code"] == "bad_mask"
            vs = (await c.get("/api/versions", headers=AUTH)).json()
            assert vs[0]["parent_id"] is None
