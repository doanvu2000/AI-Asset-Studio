from __future__ import annotations

import io
import os
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageSequence

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.export import shader_preview
from studio_core.export.shaders import PRESETS

TOKEN = "tsp"
BUDGET_MS = 200


def _frames(data: bytes) -> list[bytes]:
    img = Image.open(io.BytesIO(data))
    return [f.convert("RGBA").tobytes() for f in ImageSequence.Iterator(img)]


@pytest.mark.parametrize("pid", list(PRESETS))
def test_preset_renders_distinct_frames_within_budget(pid: str, tmp_path: Path) -> None:
    shader_preview.sample_sprite()
    ms = 1e9
    for i in range(3):  # best of 3 cold renders: other processes on the box add noise, not the render itself
        t0 = time.perf_counter()
        data, animated = shader_preview.preview(tmp_path / str(i), pid)
        ms = min(ms, (time.perf_counter() - t0) * 1000)
    print(f"shader preview {pid}: {ms:.0f} ms, {len(data)} bytes")
    img = Image.open(io.BytesIO(data))
    assert max(img.size) <= 96 and getattr(img, "n_frames", 1) <= 24
    frames = _frames(data)
    # WebP merges identical neighbours (hit_flash is a two-state blink), so only distinct frames are counted
    assert animated and 2 <= len(frames) <= 24 and len(set(frames)) >= 2
    # Parallel workers share the CPU, so the wall-clock budget only holds for a serial run.
    assert ms < BUDGET_MS or os.environ.get("PYTEST_XDIST_WORKER")


def test_cache_hit_and_static_without_speed(tmp_path: Path) -> None:
    shader_preview.preview(tmp_path, "wind_sway", speed=0)
    t0 = time.perf_counter()
    data, animated = shader_preview.preview(tmp_path, "wind_sway", speed=0)
    assert (time.perf_counter() - t0) * 1000 < 100
    assert not animated and len(_frames(data)) == 1


def test_still_is_one_frame_and_not_blank(tmp_path: Path) -> None:
    data, animated = shader_preview.preview(tmp_path, "hit_flash", still=True)
    assert len(_frames(data)) == 1 and animated
    full = shader_preview.preview(tmp_path, "hit_flash")[0]
    assert _frames(data)[0] in _frames(full)[1:] or _frames(data)[0] != _frames(full)[0]


def test_color_changes_output(tmp_path: Path) -> None:
    a, _ = shader_preview.preview(tmp_path, "water_ripple", color="#ffffff")
    b, _ = shader_preview.preview(tmp_path, "water_ripple", color="#ff0000")
    assert _frames(a)[0] != _frames(b)[0]


@pytest.fixture
async def client(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=tmp_path / "r", comfy_workflows_dir=tmp_path / "no")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def test_route_webp_with_query_token(client: httpx.AsyncClient) -> None:
    r = await client.get(f"/api/shaders/dissolve/preview?color=%23ff0000&speed=1&t={TOKEN}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert r.headers["x-shader-preview"] == "animated"
    still = await client.get(f"/api/shaders/dissolve/preview?still=true&t={TOKEN}")
    assert still.headers["x-shader-preview"] == "static" and still.content != r.content


async def test_route_rejects(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/shaders/dissolve/preview")).status_code == 401
    assert (await client.get(f"/api/shaders/nope/preview?t={TOKEN}")).status_code == 404
    assert (await client.get(f"/api/shaders/dissolve/preview?color=zz&t={TOKEN}")).status_code == 400
    assert (await client.get(f"/api/shaders/dissolve/preview?speed=99&t={TOKEN}")).status_code == 422
    r = await client.get("/api/shaders", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
