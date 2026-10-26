from __future__ import annotations

import asyncio
import io
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import RunStatus, StepResult
from studio_core.ops.executors import Sheet
from studio_core.ops.presets import COMPASS_ANGLE, ExportPreset, PresetStore, apply_preset
from test_engine_runner import Env, FakeExec
from test_p2_scheduler import GroupedExec

TOKEN = "t6"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]


def test_builtin_presets_order_and_names() -> None:
    store = PresetStore(None)
    unity, godot = store.get("unity"), store.get("godot")
    assert unity and godot and store.get("studio")
    slots, frames = apply_preset(unity, ANGLES, "hero")
    assert [ANGLES[i] for i in slots] == [COMPASS_ANGLE[d] for d in ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]]
    assert frames[0]["name"] == "hero_E"
    _, frames = apply_preset(godot, ANGLES, "hero")
    assert [f["name"] for f in frames[:3]] == ["hero_right", "hero_down_right", "hero_down"]
    slots, frames = apply_preset(store.get("studio"), ANGLES, "hero")  # type: ignore[arg-type]
    assert slots == list(range(8)) and frames[1]["name"] == "hero_045"
    with pytest.raises(ValueError):
        apply_preset(unity, [0, 90, 180, 270], "x")


def test_preset_validation_and_store(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ExportPreset(id="x", name="x", dir_order=["N", "S"])
    with pytest.raises(ValueError):
        ExportPreset(id="x", name="x", file_pattern="{asset}")
    with pytest.raises(ValueError):
        ExportPreset(id="Bad Id", name="x")
    store = PresetStore(tmp_path / "presets.json")
    with pytest.raises(ValueError):
        store.save(ExportPreset(id="unity", name="mine"))
    store.save(ExportPreset(id="my-game", name="Game", layout="grid", cols=4, dir_labels={"n": "back"}))
    p = store.get("my-game")
    assert p and p.dir_labels == {"N": "back"} and p.cols_for(8) == 4 and not p.builtin
    assert [x.id for x in store.list()][-1] == "my-game"
    store.delete("my-game")
    assert store.get("my-game") is None


class AngleFrames(FakeExec):
    """8 frames, each 4×4 with its angle encoded in the red channel."""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        out = []
        for a in ANGLES:
            buf = io.BytesIO()
            Image.new("RGBA", (4, 4), (a // 45 * 20, 0, 0, 255)).save(buf, "PNG")
            out.append(await ctx.blobs.put_bytes(buf.getvalue(), name=f"dir_{a:03d}.png", mime="image/png"))
        return StepResult(outputs={"images": out})


SHEET = """
id: sheet
inputs:
  preset: {type: preset, default: studio}
steps:
  - {id: frames, kind: t.frames}
  - {id: sheet, kind: op.sheet, in: {images: frames.images},
     params: {cols: 8, pivot: bottom, order: [0, 45, 90, 135, 180, 225, 270, 315], preset: "${inputs.preset}",
              asset: hero}}
outputs:
  final: sheet.sheet
"""


async def test_sheet_applies_preset(tmp_path: Path) -> None:
    env = Env(tmp_path, [AngleFrames("t.frames")], {"sheet": SHEET})
    env.registry.register(Sheet(PresetStore(env.settings.presets_path)))
    PresetStore(env.settings.presets_path).save(
        ExportPreset(id="col", name="Column", layout="column", dir_order=["N", "NE", "E", "SE", "S", "SW", "W", "NW"]))

    async def sheet_of(preset: str) -> tuple[Image.Image, dict[str, Any]]:
        run_id = await env.run("sheet", {"preset": preset})
        assert env.status(run_id) == RunStatus.SUCCEEDED
        s = env.steps(run_id)["sheet"]
        img = Image.open(env.blobs.path(s.outputs["sheet"][0].hash))
        meta = json.loads(env.blobs.path(s.outputs["meta"][0].hash).read_bytes())
        return img, meta

    img, meta = await sheet_of("godot")
    assert img.size == (32, 4) and meta["preset"] == "godot"
    assert [img.getpixel((x * 4 + 1, 1))[0] // 20 * 45 for x in range(8)] == [270, 315, 0, 45, 90, 135, 180, 225]
    assert meta["dirs"][:2] == ["right", "down_right"] and meta["frames_named"][1]["x"] == 4
    assert meta["pivot_origin"] == "top_left" and meta["pivot_norm"][1] == pytest.approx(meta["pivot"][1] / 4)

    img, meta = await sheet_of("col")
    assert img.size == (4, 32) and meta["cols"] == 1
    assert [img.getpixel((1, y * 4 + 1))[0] // 20 * 45 for y in range(8)][:2] == [180, 225]

    img, meta = await sheet_of("unity")
    assert meta["pivot_origin"] == "bottom_left" and meta["frames_named"][0]["name"] == "hero_E"

    run_id = await env.engine.start_run("sheet", {"preset": "nope"})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 5)
    assert env.status(run_id) == RunStatus.FAILED
    assert env.steps(run_id)["sheet"].error["code"] == "op_bad_params"  # type: ignore[index]


TWO_STAGE = """
id: two
inputs:
  prompt: {type: text}
steps:
  - {id: gen, kind: t.gen, params: {count: 1, p: "${inputs.prompt}"}}
  - {id: post, kind: t.post, in: {images: gen.images}}
"""


async def test_batch_runs_stage_major_on_gpu(tmp_path: Path) -> None:
    gen = GroupedExec("t.gen", "zimage", 0.02)
    post = GroupedExec("t.post", "birefnet", 0.02)
    env = Env(tmp_path, [gen, post], {"two": TWO_STAGE})
    runs = [await env.engine.start_run("two", {"prompt": f"p{i}"}) for i in range(4)]
    await asyncio.wait_for(asyncio.gather(*(env.engine.wait_idle(r) for r in runs)), 5)
    starts = sorted((s.started_at, s.key) for r in runs for s in env.steps(r).values())
    assert [k for _, k in starts] == ["gen"] * 4 + ["post"] * 4


IMG_RECIPE = """
id: img
inputs:
  pic: {type: image}
steps:
  - {id: ok, kind: gate.approve, in: {images: inputs.pic}}
"""


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    rdir = tmp_path / "builtin"
    rdir.mkdir()
    (rdir / "two.yaml").write_text(TWO_STAGE.replace("t.gen", "gate.approve").replace(
        "t.post", "gate.approve").replace("{count: 1, p: \"${inputs.prompt}\"}", "{k: 0}"), encoding="utf-8")
    (rdir / "img.yaml").write_text(IMG_RECIPE, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "none")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def test_batch_endpoint_tags_runs(client: httpx.AsyncClient) -> None:
    body = {"recipe_id": "two", "title": "Lô thử", "items": [{"inputs": {"prompt": "a"}, "title": "A"},
                                                              {"inputs": {"prompt": "b"}, "title": "B"}]}
    r = await client.post("/api/batches", json=body, headers=AUTH)
    assert r.status_code == 201
    data = r.json()
    assert len(data["run_ids"]) == 2
    for i, rid in enumerate(data["run_ids"]):
        run = (await client.get(f"/api/runs/{rid}", headers=AUTH)).json()["run"]
        meta = run["params"]["$meta"]
        assert meta == {"source": "app", "batch_id": data["batch_id"], "batch_index": i, "batch_total": 2,
                        "batch_title": "Lô thử"}
        assert run["inputs"]["prompt"] == "ab"[i] and run["title"] == "AB"[i]

    before = len((await client.get("/api/runs", headers=AUTH)).json())
    bad = {"recipe_id": "two", "items": [{"inputs": {"prompt": "ok"}}, {"inputs": {}}]}
    r = await client.post("/api/batches", json=bad, headers=AUTH)
    assert r.status_code == 400 and "item 2" in r.json()["error"]["message"]
    assert len((await client.get("/api/runs", headers=AUTH)).json()) == before
    assert (await client.post("/api/batches", json={"recipe_id": "zz", "items": [{}]}, headers=AUTH)).status_code == 404
    assert (await client.post("/api/batches", json={"recipe_id": "two", "items": []}, headers=AUTH)).status_code == 400


async def test_presets_api(client: httpx.AsyncClient) -> None:
    ids = [p["id"] for p in (await client.get("/api/presets", headers=AUTH)).json()]
    assert ids[:3] == ["studio", "unity", "godot"]
    body = {"name": "Game", "dir_order": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"], "layout": "grid", "cols": 4}
    r = await client.put("/api/presets/game", json=body, headers=AUTH)
    assert r.status_code == 200 and r.json()["id"] == "game" and r.json()["builtin"] is False
    assert (await client.put("/api/presets/unity", json=body, headers=AUTH)).status_code == 409
    r = await client.put("/api/presets/bad", json={**body, "dir_order": ["N"]}, headers=AUTH)
    assert r.status_code == 422 and "dir_order" in r.json()["error"]["message"]
    assert (await client.delete("/api/presets/game", headers=AUTH)).json() == {"ok": True}
    assert (await client.delete("/api/presets/game", headers=AUTH)).status_code == 404


async def test_batch_checks_every_blob_before_starting(client: httpx.AsyncClient) -> None:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (4, 4)).save(buf, "PNG")
    r = await client.post("/api/blobs", files={"file": ("a.png", buf.getvalue(), "image/png")}, headers=AUTH)
    good = r.json()["hash"]
    before = len((await client.get("/api/runs", headers=AUTH)).json())
    body = {"recipe_id": "img", "items": [{"inputs": {"pic": good}}, {"inputs": {"pic": "f" * 64}}]}
    r = await client.post("/api/batches", json=body, headers=AUTH)
    assert r.status_code == 400 and "item 2" in r.json()["error"]["message"]
    assert len((await client.get("/api/runs", headers=AUTH)).json()) == before
