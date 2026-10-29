from __future__ import annotations

import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from studio_core.contracts import Lane, RunRow, StepRow, StepStatus
from studio_core.export import godot, unity
from studio_core.export.hitfx import HitOptions, Vfx, analyze, apply, vfx_name
from studio_core.export.source import Source

from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)

ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
CELL = 64
PIVOT = (32, 60)
STRIKE = 3
FRAMES = 6


def _facing_px(angle: float) -> tuple[int, int]:
    import math

    r = math.radians(angle)
    return round(-math.sin(r)), round(math.cos(r))


def _sheet(tmp: Path) -> tuple[Path, dict]:
    """idle (1 clip, still) + attack: body box; the arm blob swings out along the facing on frame STRIKE."""
    rows = 16
    img = np.zeros((rows * CELL, FRAMES * CELL, 4), np.uint8)
    for r in range(rows):
        angle = ANGLES[r % 8]
        dx, dy = _facing_px(angle)
        for f in range(FRAMES):
            y0, x0 = r * CELL, f * CELL
            img[y0 + 20:y0 + 60, x0 + 26:x0 + 38] = (90, 90, 200, 255)
            if r < 8:
                continue
            reach = {STRIKE: 20, STRIKE + 1: 16}.get(f, 4)
            # Facing up/down the screen the arm swings beside the body, not hidden in front of it.
            cx, cy = 32 + dx * reach + (10 if dx == 0 else 0), 32 + dy * reach // 2
            img[y0 + cy - 4:y0 + cy + 4, x0 + max(0, cx - 4):x0 + min(CELL, cx + 4)] = (230, 200, 60, 255)
    path = tmp / "sheet.png"
    Image.fromarray(img, "RGBA").save(path)
    meta = {"frame_w": CELL, "frame_h": CELL, "cols": FRAMES, "rows": rows, "count": rows * FRAMES, "padding": 0,
            "pivot": list(PIVOT), "row_angles": ANGLES * 2,
            "clips": [{"name": "idle", "row0": 0, "rows": 8, "frames": FRAMES, "fps": 6, "loop": True},
                      {"name": "attack", "row0": 8, "rows": 8, "frames": FRAMES, "fps": 12, "loop": False}]}
    return path, meta


def _vfx(tmp: Path) -> Vfx:
    path = tmp / "vfx.png"
    Image.new("RGBA", (32 * 4, 32), (255, 160, 40, 200)).save(path)
    meta = {"frame_w": 32, "frame_h": 32, "cols": 4, "rows": 1, "count": 4, "padding": 0, "pivot": [16, 16],
            "flipbook": {"fps": 24.0, "loop": False}}
    return Vfx("fire_burst", path, meta)


def test_analyze_picks_the_strike_frame_and_reach_point(tmp_path: Path) -> None:
    sheet, meta = _sheet(tmp_path)
    lay, hits = analyze(sheet, meta)
    assert [h.track for h in hits] == [f"attack_{d}" for d in ("S", "SW", "W", "NW", "N", "NE", "E", "SE")]
    assert all(h.frame == STRIKE and h.source == "motion" and h.point_source == "silhouette" for h in hits)
    by = {h.dir: h for h in hits}
    # W faces image-left, E image-right: the point sits past the swung arm on that side.
    assert by["W"].point[0] < PIVOT[0] - 20 and by["E"].point[0] > PIVOT[0] + 20
    assert by["W"].row == 8 + 2 and len(by["W"].points) == FRAMES and by["W"].fps == 12


def test_impact_sources_meta_rig_user(tmp_path: Path) -> None:
    sheet, meta = _sheet(tmp_path)
    _, rig = analyze(sheet, meta, directional_rows=True)
    assert {(h.frame, h.source) for h in rig} == {(int(0.5 * (FRAMES - 1) + 0.5), "rig")}
    meta["clips"][1]["impact"] = 2
    _, m = analyze(sheet, meta, directional_rows=True)
    assert {(h.frame, h.source) for h in m} == {(2, "meta")}
    _, u = analyze(sheet, meta, frames={"attack_E": 5}, points_in={"attack_E": (10, 11)})
    e = next(h for h in u if h.dir == "E")
    assert (e.frame, e.source, e.auto_frame, e.point, e.point_source) == (5, "user", 2, (10.0, 11.0), "user")
    _, idle = analyze(sheet, meta, clips=("idle",))
    assert idle == [] or all(h.clip == "idle" for h in idle)


def test_socket_points_win(tmp_path: Path) -> None:
    sheet, meta = _sheet(tmp_path)
    frames = [{"cell": f"attack_{d}_{f}", "sockets": [{"name": "hand_r", "x": 40 + f, "y": 30}]}
              for d in ("S", "SW", "W", "NW", "N", "NE", "E", "SE") for f in range(FRAMES)]
    _, hits = analyze(sheet, meta, sockets={"frames": frames})
    assert all(h.point_source == "socket" and h.point == (40.0 + h.frame, 30.0) for h in hits)


def _src(tmp: Path) -> Source:
    sheet, meta = _sheet(tmp)
    return Source(asset="Hero", uid="step1", sheet=sheet, meta=meta, ppu=64)


def test_unity_events_prefab_and_files(tmp_path: Path) -> None:
    src = _src(tmp_path)
    files = apply(unity.export(src), "unity", src, HitOptions(_vfx(tmp_path), frames={"attack_N": 4}))
    names = dict(files)
    clip = names["Animations/attack_E.anim"].decode()
    assert "functionName: OnHit" in clip and f"time: {STRIKE / 12:.6g}" in clip and "data: fire_burst|" in clip
    assert "time: 0.333333" in names["Animations/attack_N.anim"].decode()
    assert "m_Events: []" in names["Animations/idle_S.anim"].decode()
    prefab = names["Hero.prefab"].decode()
    assert "AssetStudioHitFx" not in prefab and "guid: 5c0e7d1a9b2f4c63a8e4d17f0b9a6e25" in prefab
    assert prefab.count("- component:") == 4 and "  - name: fire_burst" in prefab
    assert "namespace AssetStudio" in names["AssetStudioHitFx.cs"].decode()
    meta = names["HitFx/fire_burst.png.meta"].decode()
    assert meta.count("name: fire_burst_") == 4 and "pivot: {x: 0.5, y: 0.5}" in meta
    doc = json.loads(names["Hero_hits.json"])
    e = next(h for h in doc["hits"] if h["dir"] == "E")
    assert e["frame"] == STRIKE and e["offset"][0] > 20 and doc["vfx"][0]["frames"] == 4
    assert e["scale"] == round(0.5 * CELL / 32, 4)
    assert "Hit VFX" in names["README.txt"].decode()


def test_godot_scene_and_script(tmp_path: Path) -> None:
    src = _src(tmp_path)
    names = dict(apply(godot.export(src), "godot", src, HitOptions(_vfx(tmp_path), size=1.0)))
    scene = names["Hero_hits.tscn"].decode()
    assert 'instance=ExtResource("1_body")' in scene and 'Array[SpriteFrames]([ExtResource("4_vfx")])' in scene
    assert 'path="Hero.tscn"' in scene and 'path="HitFx/fire_burst.tres"' in scene
    tres = names["HitFx/fire_burst.tres"].decode()
    assert '"name": &"fire_burst"' in tres and 'path="fire_burst.png"' in tres and '"loop": false' in tres
    assert "frame_changed.connect" in names["asset_studio_hitfx.gd"].decode()
    doc = json.loads(names["Hero_hits.json"])
    assert len(doc["hits"]) == 8 and doc["hits"][0]["scale"] == 2.0


def test_sheet_without_attacks_is_untouched(tmp_path: Path) -> None:
    sheet, meta = _sheet(tmp_path)
    meta["clips"] = meta["clips"][:1]
    meta["rows"], meta["row_angles"] = 8, ANGLES
    src = Source(asset="Hero", uid="s", sheet=sheet, meta=meta)
    files = unity.export(src)
    assert apply(files, "unity", src, HitOptions(_vfx(tmp_path))) == files


def test_vfx_name() -> None:
    assert vfx_name("a burst of orange magic fire exploding into sparks", "x") == "burst_orange_magic"
    assert vfx_name("", "vfx_ab12") == "vfx_ab12"


async def _put(app, data: bytes, name: str, mime: str):
    return await app.state.blobs.put_bytes(data, name=name, mime=mime)


async def test_api_hits_and_export(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    repo = app.state.repo
    sheet, meta = _sheet(tmp_path)
    v = _vfx(tmp_path)
    now = time.time()
    await repo.create_run(RunRow(id="r1", recipe_id="sprite-8dir-anim", recipe_hash="h", created_at=now))
    await repo.create_run(RunRow(id="r2", recipe_id="vfx-flipbook", recipe_hash="h", created_at=now,
                                 inputs={"prompt": "a burst of fire sparks", "mode": "oneshot"}))
    s_ref = await _put(app, sheet.read_bytes(), "sheet.png", "image/png")
    m_ref = await _put(app, json.dumps(meta).encode(), "sheet.json", "application/json")
    vs = await _put(app, v.sheet.read_bytes(), "sheet.png", "image/png")
    vm = await _put(app, json.dumps(v.meta).encode(), "sheet.json", "application/json")
    await repo.create_steps([
        StepRow(id="anim-1", run_id="r1", key="anim_sheet", kind="op.sheet", lane=Lane.CPU,
                status=StepStatus.SUCCEEDED, outputs={"sheet": [s_ref], "meta": [m_ref]}),
        StepRow(id="vfx-1", run_id="r2", key="sheet", kind="op.sheet", lane=Lane.CPU,
                status=StepStatus.SUCCEEDED, outputs={"sheet": [vs], "meta": [vm]})])

    opts = (await client.get("/api/steps/anim-1/export", headers=AUTH)).json()
    assert opts["hits"] is True
    lib = (await client.get("/api/hitfx/vfx", headers=AUTH)).json()["vfx"]
    assert [(x["step_id"], x["name"], x["frames"]) for x in lib] == [("vfx-1", "burst_fire_sparks", 4)]
    hits = (await client.get("/api/steps/anim-1/hits", headers=AUTH)).json()
    assert len(hits["tracks"]) == 8 and hits["sheet"]["hash"] == s_ref.hash
    assert all(t["frame"] == STRIKE for t in hits["tracks"])

    body = {"engine": "godot", "name": "Hero", "hits": {"vfx_step": "vfx-1", "frames": {"attack_S": 1}}}
    r = await client.post("/api/steps/anim-1/export", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    root = Path(r.json()["dir"])
    doc = json.loads((root / "Hero_hits.json").read_text(encoding="utf-8"))
    assert next(h for h in doc["hits"] if h["dir"] == "S")["frame"] == 1
    assert (root / "HitFx" / "burst_fire_sparks.png").is_file() and (root / "Hero_hits.tscn").is_file()

    bad = await client.post("/api/steps/anim-1/export", json={**body, "hits": {"vfx_step": "anim-1"}}, headers=AUTH)
    assert bad.status_code == 422
