import io
import json
import time
from pathlib import Path

from PIL import Image

from studio_core.contracts import Lane, RunRow, StepRow, StepStatus

from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)

ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]


def _frame(i: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (16, 20), (i * 30, 10, 200, 255)).save(buf, "PNG")
    return buf.getvalue()


async def _step(app, step_id: str, kind: str, outputs, params=None, status=StepStatus.SUCCEEDED) -> None:
    repo = app.state.repo
    if await repo.get_run("r1") is None:
        await repo.create_run(RunRow(id="r1", recipe_id="x", recipe_hash="h", created_at=time.time()))
    await repo.create_steps([StepRow(id=step_id, run_id="r1", key=step_id.split("-")[0], kind=kind, lane=Lane.CPU,
                                     status=status, params=params or {}, outputs=outputs)])


async def _sprite(app) -> list[str]:
    blobs = app.state.blobs
    frames = [await blobs.put_bytes(_frame(i), name=f"dir_{a:03d}.png", mime="image/png") for i, a in enumerate(ANGLES)]
    meta = {"frame_w": 16, "frame_h": 20, "cols": 8, "rows": 1, "count": 8, "padding": 0, "order": ANGLES,
            "pivot": [8, 18], "frames": [f.hash for f in frames]}
    meta_ref = await blobs.put_bytes(json.dumps(meta).encode(), name="sheet.json", mime="application/json")
    sheet_ref = await blobs.put_bytes(_frame(0), name="sheet.png", mime="image/png")
    await _step(app, "sheet-1", "op.sheet", {"sheet": [sheet_ref], "meta": [meta_ref]}, {"asset": "knight"})
    return [f.hash for f in frames]


async def test_sprite_bundle_with_godot_preset(app_ctx) -> None:  # noqa: F811
    app, client, _ = app_ctx
    hashes = await _sprite(app)

    opts = (await client.get("/api/steps/sheet-1/export", headers=AUTH)).json()
    assert opts["sprite_sheet"] is True

    r = await client.post("/api/steps/sheet-1/export", json={"preset": "godot", "name": "Hiệp sĩ"}, headers=AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    root = Path(out["dir"])
    assert root.name == "Hiệp sĩ" and out["preset"] == "godot"
    meta = json.loads((root / "Hiệp sĩ_sheet.json").read_text(encoding="utf-8"))
    assert meta["dirs"][:3] == ["right", "down_right", "down"]
    assert meta["order"][:3] == [270, 315, 0]
    assert meta["frames"][0] == hashes[ANGLES.index(270)]
    assert (root / "frames" / "Hiệp sĩ_right.png").read_bytes() == _frame(ANGLES.index(270))
    assert Image.open(root / "Hiệp sĩ_sheet.png").size == (16 * 8, 20)
    assert len(out["files"]) == 2 + 8 and all(Path(p).is_file() for p in out["files"])

    again = (await client.post("/api/steps/sheet-1/export", json={"preset": "godot", "name": "Hiệp sĩ"},
                               headers=AUTH)).json()
    assert again["dir"] == out["dir"] and Path(again["files"][0]).is_file()


async def test_spine_bundle_keeps_names(app_ctx) -> None:  # noqa: F811
    app, client, _ = app_ctx
    blobs = app.state.blobs
    outs = {
        "skeleton": [await blobs.put_bytes(b"{}", name="hero.json", mime="application/json")],
        "atlas": [await blobs.put_bytes(b"hero.png\nsize: 4,4\n", name="hero.atlas", mime="text/plain")],
        "texture": [await blobs.put_bytes(_frame(1), name="hero.png", mime="image/png")],
    }
    await _step(app, "spine-1", "op.spine_export", outs)
    out = (await client.post("/api/steps/spine-1/export", json={}, headers=AUTH)).json()
    assert sorted(Path(p).name for p in out["files"]) == ["hero.atlas", "hero.json", "hero.png"]

    r = await client.post("/api/steps/spine-1/export", json={"preset": "unity"}, headers=AUTH)
    assert r.status_code == 422


async def test_step_export_rejects_unfinished_and_unknown(app_ctx) -> None:  # noqa: F811
    app, client, _ = app_ctx
    ref = await app.state.blobs.put_bytes(_frame(0), name="a.png", mime="image/png")
    await _step(app, "gen-1", "comfy.txt2img", {"images": [ref]}, status=StepStatus.RUNNING)
    assert (await client.post("/api/steps/gen-1/export", json={}, headers=AUTH)).status_code == 409
    assert (await client.post("/api/steps/nope/export", json={}, headers=AUTH)).status_code == 404
    await _sprite(app)
    assert (await client.post("/api/steps/sheet-1/export", json={"preset": "zzz"}, headers=AUTH)).status_code == 404
