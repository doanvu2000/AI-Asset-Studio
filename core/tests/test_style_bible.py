from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.style_bible import StyleBibleWriter
from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import VersionRow, VersionStatus
from studio_core.engine import style_bible as sb
from studio_core.engine.recipe import parse_recipe
from tests.test_project_qa import png, sprite

TOKEN = "tsb"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

STYLED = """
id: styled
inputs:
  image: {type: image, label: Ảnh style tham chiếu, default: null}
  prompt: {type: text, multiline: true}
  negative: {type: text, default: ""}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""

BRIEF = """
id: brief
inputs:
  idea: {type: text}
  style: {type: text, default: ""}
  image: {type: image}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""

BIBLE = sb.StyleBible(version=3, suffix="flat cel shading, thick dark outline", negative="photorealistic",
                      refs=[sb.BibleRef(hash="a" * 64)])


def pixel_sprite() -> Image.Image:
    rng = np.random.default_rng(1)
    small = rng.choice(np.array([[30, 30, 40], [200, 60, 60], [240, 200, 80]], np.uint8), size=(24, 24))
    return Image.fromarray(small, "RGB").resize((192, 192), Image.Resampling.NEAREST)


def test_pixel_score_and_aggregate() -> None:
    assert sb.pixel_score(pixel_sprite()) > 0.7
    assert sb.pixel_score(pixel_sprite().resize((24, 24), Image.Resampling.NEAREST)) == 1.0
    painted = sprite((60, 170, 70), outline=0)
    assert sb.pixel_score(painted) < 0.55
    imgs = [(f"v{i}", sprite((60, 170 + i * 5, 70), outline=5, light="left")) for i in range(3)]
    measures, pix = sb.measure_all(imgs + [("bad", None)])
    stats = sb.aggregate(measures, pix)
    assert 1 <= len(stats["palette"]) <= sb.PALETTE_K and all(c.startswith("#") for c in stats["palette"])
    assert stats["outlined"] is True and stats["outline_weight"] and stats["light_label"] == "left"
    assert stats["pixel_art"] is False
    refs = sb.pick_refs(measures, stats["palette"], {"v0": "r1", "v1": "r1", "v2": "r2"})
    assert len(refs) == 2 and "bad" not in refs
    suffix, negative = sb.fallback_text(stats)
    assert "outline" in suffix and "left" in suffix and "photorealistic" in negative


def test_apply_bible_inputs_and_overrides() -> None:
    styled = parse_recipe(STYLED.encode())
    out, used = sb.apply_bible(styled, {"prompt": "a chest\n\na potion"}, {"prompt": "a chest\n\na potion"}, BIBLE)
    assert out["prompt"] == "a chest, flat cel shading, thick dark outline\n\na potion, flat cel shading, thick dark outline"
    assert out["negative"] == "photorealistic" and out["image"] == "a" * 64
    assert set(used) == {"prompt", "negative", "image"}
    user = {"prompt": "a chest", "negative": "blurry", "image": "b" * 64}
    out, used = sb.apply_bible(styled, user, user, BIBLE)
    assert out["negative"] == "blurry" and out["image"] == "b" * 64 and used == ["prompt"]
    # Already there (project style or a previous pass) -> no duplicate.
    same = {"prompt": "a chest, flat cel shading, thick dark outline"}
    assert "prompt" not in sb.apply_bible(styled, same, same, BIBLE)[1]

    brief = parse_recipe(BRIEF.encode())
    out, used = sb.apply_bible(brief, {"idea": "a knight", "style": "watercolor"}, {"idea": "a knight"}, BIBLE)
    assert out["idea"] == "a knight" and out["style"] == "watercolor, flat cel shading, thick dark outline"
    assert "image" not in out, "a plain image input is not a style reference"
    out, used = sb.apply_bible(brief, {"idea": "a knight", "style": "mine"}, {"style": "mine"}, BIBLE)
    assert out["style"] == "mine" and used == []


async def test_writer_parses_reply(tmp_path: Path) -> None:
    async def query(content: list[dict[str, Any]], opts: Any) -> ClaudeReply:
        assert content[0]["type"] == "image" and "pixel art" in content[-1]["text"]
        return ClaudeReply({"suffix": " soft pastel ", "negative": "noise", "summary": "Pastel."}, "",
                           {"cost_usd": 0.02, "model": opts.model})

    res = await StyleBibleWriter(tmp_path, query).write(png(sprite((1, 2, 3))), {"pixel_art": True}, 1)
    assert res["suffix"] == "soft pastel" and res["cost_usd"] == 0.02 and res["model"] == "claude-opus-5-5"


@pytest.fixture
async def app_client(tmp_path: Path) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    rdir = tmp_path / "builtin"
    rdir.mkdir()
    (rdir / "styled.yaml").write_text(STYLED, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "none")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield app, c


async def _run_meta(c: httpx.AsyncClient, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    r = await c.post("/api/runs", json={"recipe_id": "styled", **body}, headers=AUTH)
    assert r.status_code == 201, r.text
    run = (await c.get(f"/api/runs/{r.json()['run_id']}", headers=AUTH)).json()["run"]
    return run["inputs"], run["params"].get("$meta") or {}


async def test_api_build_edit_and_apply(app_client: tuple[Any, httpx.AsyncClient], tmp_path: Path) -> None:
    app, c = app_client
    pid = (await c.post("/api/projects", json={"name": "Forest"}, headers=AUTH)).json()["id"]
    url = f"/api/projects/{pid}/style"
    assert (await c.get(url, headers=AUTH)).json()["bible"] is None
    assert (await c.post(f"{url}/build", json={"vision": False}, headers=AUTH)).status_code == 400
    assert (await c.post("/api/projects/default/style/build", json={}, headers=AUTH)).status_code == 400
    assert (await c.post(url, json={"active": False}, headers=AUTH)).status_code == 404

    for i in range(4):
        ref = await app.state.blobs.put_bytes(png(sprite((60, 170 + i * 5, 70))), name=f"a{i}.png", mime="image/png")
        await app.state.repo.add_version(VersionRow(
            id=f"v{i}", project_id=pid, asset_name=f"asset {i}", run_id=f"r{i}", step_id="s", blob=ref,
            created_at=time.time() + i, status=VersionStatus.APPROVED if i < 3 else VersionStatus.DRAFT))

    calls: list[Any] = []

    async def query(content: list[dict[str, Any]], opts: Any) -> ClaudeReply:
        calls.append(content)
        return ClaudeReply({"suffix": "soft painted shading, dark outline", "negative": "pixel art, text",
                            "summary": "Tông xanh lá."}, "", {"cost_usd": 0.03, "model": opts.model})

    app.state.style_bible_writer = StyleBibleWriter(tmp_path / "w", query)
    r = await c.post(f"{url}/build", json={}, headers=AUTH)
    assert r.status_code == 200, r.text
    bible = r.json()["bible"]
    assert bible["version"] == 1 and bible["active"] and bible["source_count"] == 3
    assert bible["suffix"] == "soft painted shading, dark outline" and bible["cost_usd"] == 0.03
    assert bible["palette"] and bible["outlined"] is True and 1 <= len(bible["refs"]) <= 3
    assert {x["version_id"] for x in bible["refs"]} <= {"v0", "v1", "v2"} and len(calls) == 1
    assert (await c.get(url, headers=AUTH)).json()["bible"]["version"] == 1

    inputs, meta = await _run_meta(c, {"project_id": pid, "inputs": {"prompt": "a chest"}})
    assert inputs["prompt"] == "a chest, soft painted shading, dark outline"
    assert inputs["negative"] == "pixel art, text" and inputs["image"] == bible["refs"][0]["hash"]
    assert meta["style_bible"] == {"version": 1, "applied": ["prompt", "negative", "image"]}

    inputs, meta = await _run_meta(c, {"project_id": pid, "inputs": {"prompt": "a chest"},
                                       "params": {"$meta": {"use_style_bible": False}}})
    assert inputs["prompt"] == "a chest" and "style_bible" not in meta
    inputs, meta = await _run_meta(c, {"inputs": {"prompt": "a chest"}})
    assert inputs["prompt"] == "a chest" and "style_bible" not in meta

    r = await c.post(url, json={"suffix": "chunky ink lines"}, headers=AUTH)
    assert r.json()["bible"]["version"] == 2 and r.json()["bible"]["edited"]
    inputs, meta = await _run_meta(c, {"project_id": pid, "inputs": {"prompt": "a chest"}})
    assert inputs["prompt"] == "a chest, chunky ink lines" and meta["style_bible"]["version"] == 2

    r = await c.post(url, json={"active": False}, headers=AUTH)
    assert r.json()["bible"]["active"] is False and r.json()["bible"]["version"] == 2
    inputs, meta = await _run_meta(c, {"project_id": pid, "inputs": {"prompt": "a chest"}})
    assert inputs["prompt"] == "a chest" and "style_bible" not in meta
    assert (await c.post(url, json={"refs": ["0" * 64]}, headers=AUTH)).status_code == 400

    r = await c.post(f"{url}/build", json={"vision": False}, headers=AUTH)
    rebuilt = r.json()["bible"]
    assert rebuilt["version"] == 3 and rebuilt["active"] is False and "outline" in rebuilt["suffix"]
    assert len(calls) == 1
