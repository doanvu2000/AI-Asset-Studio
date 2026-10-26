import io
import json
import re
import time
from pathlib import Path

import yaml
from PIL import Image

from studio_core.contracts import Lane, RunRow, StepRow, StepStatus
from studio_core.export import godot, unity
from studio_core.export.sheet import direction_vector, layout
from studio_core.export.source import Source

from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)

ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
DIRS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]
FW, FH, COLS, ROWS = 32, 48, 4, 16


def _meta(**extra):
    return {"frame_w": FW, "frame_h": FH, "cols": COLS, "rows": ROWS, "count": COLS * ROWS, "padding": 0,
            "order": list(range(COLS * ROWS)), "width": COLS * FW, "height": ROWS * FH, "pivot": [16, 45],
            "row_angles": ANGLES + ANGLES,
            "clips": [{"name": "walk", "row0": 0, "rows": 8, "frames": 4, "fps": 10, "loop": True},
                      {"name": "attack", "row0": 8, "rows": 8, "frames": 3, "fps": 12, "loop": False}], **extra}


def _png(tmp: Path, name: str, size=(COLS * FW, ROWS * FH)) -> Path:
    p = tmp / name
    Image.new("RGBA", size, (10, 20, 30, 255)).save(p)
    return p


def _source(tmp: Path, asset="Knight", normal=True, meta=None) -> Source:
    return Source(asset=asset, sheet=_png(tmp, "sheet.png"), meta=meta or _meta(),
                  normal=_png(tmp, "normal.png") if normal else None, ppu=48)


def _unity_docs(text: str) -> list[dict]:
    """Unity YAML minus its !u! class tags, which plain YAML loaders reject."""
    plain = re.sub(r"^--- !u!(\d+) &(-?\d+)", r"---", text.split("\n", 2)[2], flags=re.M)
    return [d for d in yaml.safe_load_all(plain) if d]


def _files(files) -> dict[str, bytes | Path]:
    return dict(files)


def test_layout_clips_rows_and_rects() -> None:
    lay = layout(_meta())
    assert lay.clips() == ["walk", "attack"]
    assert [t.name for t in lay.tracks[:8]] == [f"walk_{d}" for d in DIRS]
    assert len(lay.cells) == 8 * 4 + 8 * 3
    atk_s = next(t for t in lay.tracks if t.name == "attack_S")
    assert [c.name for c in atk_s.cells] == ["attack_S_0", "attack_S_1", "attack_S_2"]
    assert (atk_s.cells[1].x, atk_s.cells[1].y) == (FW, 8 * FH)
    assert atk_s.fps == 12 and atk_s.loop is False
    assert lay.pivot_norm == (0.5, 0.0625)
    assert direction_vector(0) == (0.0, -1.0) and direction_vector(90) == (-1.0, 0.0)
    assert direction_vector(270) == (1.0, 0.0)


def test_layout_static_eight_dirs_and_legacy_walk() -> None:
    static = {"frame_w": 16, "frame_h": 20, "cols": 8, "rows": 1, "count": 8, "order": ANGLES, "pivot": [8, 18]}
    lay = layout(static)
    assert [t.name for t in lay.tracks] == [f"idle_{d}" for d in DIRS]
    assert lay.tracks[6].cells[0].x == 6 * 16
    column = {**static, "cols": 1, "rows": 8, "angles": ANGLES, "row_angles": ANGLES}
    lay = layout(column, clip_hint="sheet")
    assert [t.name for t in lay.tracks][:2] == ["idle_S", "idle_SW"] and lay.tracks[1].cells[0].y == 20
    legacy = {"frame_w": 16, "frame_h": 20, "cols": 6, "rows": 8, "count": 48, "order": list(range(48))}
    lay = layout(legacy, clip_hint="walk")
    assert [t.name for t in lay.tracks][:2] == ["walk_S", "walk_SW"] and len(lay.tracks[0].cells) == 6
    assert lay.pivot == (8, 20)


def test_unity_texture_meta_slices_and_normal(tmp_path: Path) -> None:
    files = _files(unity.export(_source(tmp_path)))
    meta = yaml.safe_load(files["Knight.png.meta"])
    imp = meta["TextureImporter"]
    sprites = imp["spriteSheet"]["sprites"]
    assert imp["spriteMode"] == 2 and imp["textureType"] == 8 and len(sprites) == 56
    assert imp["textureSettings"]["filterMode"] == 0  # 32×48 frames count as pixel art
    assert imp["spritePixelsToUnits"] == 48 and imp["maxTextureSize"] >= ROWS * FH
    assert all(p["textureCompression"] == 0 for p in imp["platformSettings"])
    s0 = next(s for s in sprites if s["name"] == "walk_S_0")
    assert s0["rect"]["y"] == ROWS * FH - FH and s0["pivot"] == {"x": 0.5, "y": 0.0625}
    table = imp["spriteSheet"]["nameFileIdTable"]
    assert table == {s["name"]: s["internalID"] for s in sprites}
    assert len(set(table.values())) == len(table)
    normal = yaml.safe_load(files["Knight_normal.png.meta"])
    sec = imp["spriteSheet"]["secondaryTextures"]
    assert sec == [{"texture": {"fileID": 2800000, "guid": normal["guid"], "type": 3}, "name": "_NormalMap"}]
    assert normal["TextureImporter"]["textureType"] == 1


def test_unity_clips_controller_and_determinism(tmp_path: Path) -> None:
    files = _files(unity.export(_source(tmp_path)))
    tex = yaml.safe_load(files["Knight.png.meta"])
    ids = set(tex["TextureImporter"]["spriteSheet"]["nameFileIdTable"].values())
    anims = sorted(k for k in files if k.endswith(".anim"))
    assert len(anims) == 16
    clip = _unity_docs(files["Animations/attack_W.anim"].decode())[0]["AnimationClip"]
    keys = clip["m_PPtrCurves"][0]["curve"]
    assert clip["m_Name"] == "attack_W" and clip["m_SampleRate"] == 12 and len(keys) == 3
    assert all(k["value"]["fileID"] in ids and k["value"]["guid"] == tex["guid"] for k in keys)
    assert clip["m_AnimationClipSettings"]["m_LoopTime"] == 0
    assert _unity_docs(files["Animations/walk_W.anim"].decode())[0]["AnimationClip"][
        "m_AnimationClipSettings"]["m_LoopTime"] == 1

    docs = _unity_docs(files["Knight.controller"].decode())
    trees = [d["BlendTree"] for d in docs if "BlendTree" in d]
    anim_guids = {yaml.safe_load(files[f"{a}.meta"])["guid"] for a in anims}
    assert sorted(t["m_Name"] for t in trees) == ["attack", "walk"]
    for t in trees:
        assert t["m_BlendType"] == 1 and len(t["m_Childs"]) == 8
        assert {c["m_Motion"]["guid"] for c in t["m_Childs"]} <= anim_guids
    ctl = next(d["AnimatorController"] for d in docs if "AnimatorController" in d)
    assert [p["m_Name"] for p in ctl["m_AnimatorParameters"]] == ["MoveX", "MoveY"]

    again = _files(unity.export(_source(tmp_path)))
    assert {k: v for k, v in again.items() if isinstance(v, bytes)} == \
        {k: v for k, v in files.items() if isinstance(v, bytes)}
    other = _files(unity.export(_source(tmp_path, asset="Mage")))
    assert yaml.safe_load(other["Mage.png.meta"])["guid"] != tex["guid"]


def test_godot_sprite_frames_and_scene(tmp_path: Path) -> None:
    files = _files(godot.export(_source(tmp_path)))
    tres = files["Knight.tres"].decode()
    assert tres.startswith('[gd_resource type="SpriteFrames" format=3]')
    assert tres.count('[sub_resource type="AtlasTexture"') == 56
    assert 'normal_texture = ExtResource("2_normal")' in tres
    names = re.findall(r'"name": &"(\w+)"', tres)
    assert len(names) == 16 and "walk_NE" in names and "attack_SE" in names
    atk = tres[tres.index('"name": &"attack_S"') - 400:tres.index('"name": &"attack_S"') + 40]
    assert '"loop": false' in atk and '"speed": 12.0' in atk
    assert "region = Rect2(32, 384, 32, 48)" in tres
    scene = files["Knight.tscn"].decode()
    assert "offset = Vector2(0.0, -21.0)" in scene and "texture_filter = 1" in scene
    assert 'animation = &"walk_S"' in scene
    plain = _files(godot.export(_source(tmp_path, normal=False)))["Knight.tres"].decode()
    assert "CanvasTexture" not in plain and 'atlas = ExtResource("1_sheet")' in plain


def _bytes_png(size=(16, 20), color=(200, 10, 10, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


async def _step(app, step_id: str, key: str, kind: str, outputs) -> None:
    repo = app.state.repo
    if await repo.get_run("r1") is None:
        await repo.create_run(RunRow(id="r1", recipe_id="x", recipe_hash="h", created_at=time.time()))
    await repo.create_steps([StepRow(id=step_id, run_id="r1", key=key, kind=kind, lane=Lane.CPU,
                                     status=StepStatus.SUCCEEDED, params={"asset": "knight"}, outputs=outputs)])


async def test_api_engine_export_with_sibling_normal(app_ctx) -> None:  # noqa: F811
    app, client, _ = app_ctx
    blobs = app.state.blobs
    size = (COLS * FW, ROWS * FH)
    for sid, key, extra in (("walk-1", "walk", {}), ("walkn-1", "walk_normal", {"normal": True})):
        meta = await blobs.put_bytes(json.dumps(_meta(**extra)).encode(), name="sheet.json", mime="application/json")
        sheet = await blobs.put_bytes(_bytes_png(size) if not extra else _bytes_png(size)[:-1] + b"\x82",
                                      name="sheet.png", mime="image/png")
        await _step(app, sid, key, "op.sheet", {"sheet": [sheet], "meta": [meta]})

    opts = (await client.get("/api/steps/walk-1/export", headers=AUTH)).json()
    assert opts["engines"] == ["unity", "godot"]
    r = await client.post("/api/steps/walk-1/export", json={"engine": "unity", "name": "Hiệp sĩ"}, headers=AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    root = Path(out["dir"])
    assert root.name == "Hiệp sĩ" and out["engine"] == "unity"
    assert (root / "Hiệp_sĩ.png.meta").is_file() and (root / "Hiệp_sĩ_normal.png").is_file()
    assert len(list((root / "Animations").glob("*.anim"))) == 16

    g = (await client.post("/api/steps/walk-1/export", json={"engine": "godot"}, headers=AUTH)).json()
    assert (Path(g["dir"]) / "knight.tres").is_file()

    bad = await client.post("/api/steps/walk-1/export", json={"engine": "unity", "preset": "godot"}, headers=AUTH)
    assert bad.status_code == 400
    assert (await client.post("/api/steps/walk-1/export", json={"engine": "unreal"}, headers=AUTH)).status_code == 422


async def test_api_engine_export_spine(app_ctx) -> None:  # noqa: F811
    app, client, _ = app_ctx
    blobs = app.state.blobs
    outs = {
        "skeleton": [await blobs.put_bytes(b"{}", name="hero.json", mime="application/json")],
        "atlas": [await blobs.put_bytes(b"hero.png\nsize: 4,4\n", name="hero.atlas", mime="text/plain")],
        "texture": [await blobs.put_bytes(_bytes_png(), name="hero.png", mime="image/png")],
    }
    await _step(app, "spine-1", "spine", "op.spine_export", outs)
    out = (await client.post("/api/steps/spine-1/export", json={"engine": "unity"}, headers=AUTH)).json()
    names = sorted(Path(p).relative_to(out["dir"]).as_posix() for p in out["files"])
    assert names == ["README.txt", "Spine/hero.atlas.txt", "Spine/hero.json", "Spine/hero.png"]
    g = (await client.post("/api/steps/spine-1/export", json={"engine": "godot"}, headers=AUTH)).json()
    assert any(p.endswith("hero.spine-json") for p in g["files"])
