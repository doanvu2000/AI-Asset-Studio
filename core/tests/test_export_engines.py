import io
import json
import re
import time
from pathlib import Path

import yaml
from PIL import Image

from studio_core.contracts import BlobRef, Lane, RunRow, StepRow, StepStatus
from studio_core.export import godot, unity
from studio_core.export.sheet import direction_vector, layout
from studio_core.export.source import Source
from studio_core.store.bundle import engine_source

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
    lay = layout(legacy, clip_hint="walk", directional_rows=True)
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


def test_layout_plain_grid_is_not_legacy_walk() -> None:
    vfx = {"frame_w": 64, "frame_h": 64, "cols": 4, "rows": 4, "count": 16, "order": list(range(16))}
    lay = layout(vfx, clip_hint="sheet")
    assert lay.tracks == [] and [c.name for c in lay.cells][:2] == ["frame_0", "frame_1"] and len(lay.cells) == 16
    assert len(layout({**vfx, "frames_per_dir": 4}).tracks) == 4


def test_unity_guid_seeded_by_source_uid(tmp_path: Path) -> None:
    def tex_guid(uid: str) -> str:
        src = _source(tmp_path)
        src.uid = uid
        return yaml.safe_load(_files(unity.export(src))["Knight.png.meta"])["guid"]
    assert tex_guid("step-a") == tex_guid("step-a") != tex_guid("step-b")


def test_godot_helper_angles_follow_layout(tmp_path: Path) -> None:
    angles = [i * 22.5 for i in range(16)]
    meta = {"frame_w": 16, "frame_h": 16, "cols": 2, "rows": 16, "count": 32, "row_angles": angles}
    gd = _files(godot.export(Source(asset="Bat", sheet=_png(tmp_path, "b.png", (32, 256)), meta=meta)))
    helper = gd["asset_studio_sprite.gd"].decode()
    assert "angle_difference" not in helper and "wrapf(" in helper
    consts = re.search(r"const DIR_ANGLES := \{(.*)\}", helper).group(1)
    assert consts.count(":") == 16 and '"A022": 22.5' in consts and '"S": 0.0' in consts
    plain = _files(godot.export(_source(tmp_path)))["asset_studio_sprite.gd"].decode()
    assert '"S": 0.0, "SW": 45.0' in plain and '"SE": 315.0}' in plain


class _Blobs:
    def __init__(self, root: Path) -> None:
        self.root, self.n = root, 0

    def put(self, data: bytes, name: str, mime: str) -> BlobRef:
        self.n += 1
        h = f"{self.n:064x}"
        (self.root / h).write_bytes(data)
        return BlobRef(hash=h, mime=mime, size=len(data), name=name)

    def path(self, h: str) -> Path:
        return self.root / h


def _row(key: str, kind: str, outputs, deps=(), inputs=None) -> StepRow:
    return StepRow(id=f"id-{key}", run_id="r", key=key, kind=kind, lane=Lane.CPU, status=StepStatus.SUCCEEDED,
                   deps=list(deps), inputs=inputs or {}, outputs=outputs)


def _sheet_row(b: _Blobs, key: str, pivot: BlobRef | None = None, deps=(), **meta) -> StepRow:
    m = b.put(json.dumps(_meta(**meta)).encode(), "sheet.json", "application/json")
    png = b.put(key.encode(), "sheet.png", "image/png")
    return _row(key, "op.sheet", {"sheet": [png], "meta": [m]}, deps, {"pivot": [pivot]} if pivot else None)


def test_normal_sheet_pairs_by_key_or_lineage_only(tmp_path: Path) -> None:
    b = _Blobs(tmp_path)
    piv = b.put(b"{}", "meta.json", "application/json")
    sheet = _sheet_row(b, "anim_sheet", piv)
    decoy = _sheet_row(b, "other_nsheet", normal=True)
    pair = _sheet_row(b, "anim_nsheet", normal=True)
    padded = _sheet_row(b, "anim_nsheet", normal=True, padding=2)
    got = lambda sibs: engine_source(sheet, [sheet, *sibs], b, "A").normal  # noqa: E731
    assert got([decoy, pair]) == b.path(pair.outputs["sheet"][0].hash)
    assert got([decoy]) is None and got([padded]) is None
    kin = _sheet_row(b, "whatever", piv, normal=True)
    assert got([decoy, kin]) == b.path(kin.outputs["sheet"][0].hash)
    assert got([kin, _sheet_row(b, "again", piv, normal=True)]) is None


def test_legacy_rows_only_from_animate_dirs(tmp_path: Path) -> None:
    b = _Blobs(tmp_path)
    anim = _row("anim", "blender.animate_dirs", {})
    down = _row("anim_down", "op.downscale", {}, ["anim"])
    sheet = _sheet_row(b, "walk", deps=["anim_down"])
    assert engine_source(sheet, [anim, down, sheet], b, "A").directional_rows
    assert not engine_source(sheet, [sheet], b, "A").directional_rows


def test_engine_names_dedupe_and_spine_page_case(tmp_path: Path) -> None:
    b = _Blobs(tmp_path)
    outs = {"skeleton": [b.put(b"{}", "Hero.json", "application/json")],
            "atlas": [b.put(b"\nHero.PNG\nsize: 4,4\nfilter: Linear,Linear\nhead\n  bounds: 0,0,4,4\n",
                            "Hero.atlas", "text/plain")],
            "texture": [b.put(_bytes_png(), "Hero.PNG", "image/png")],
            "glb": [b.put(b"a", "Hero.glb", "model/gltf-binary"), b.put(b"b", "hero.GLB", "model/gltf-binary")]}
    src = engine_source(_row("spine", "op.spine_export", outs), [], b, "Hero")
    assert [n for n, _ in src.spine_pages] == ["Hero.PNG"]
    assert [n for n, _ in src.glb] == ["Hero.glb", "hero-2.glb"]


def test_sheet_meta_only_for_plain_eight_dir_sheets(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from studio_core.store.bundle import sheet_meta

    def step(meta: dict) -> SimpleNamespace:
        p = tmp_path / f"m{len(list(tmp_path.iterdir()))}.json"
        p.write_text(json.dumps(meta), encoding="utf-8")
        return SimpleNamespace(kind="op.sheet", outputs={"meta": [BlobRef(hash=str(p), mime="application/json",
                                                                          size=0)]})

    blobs = SimpleNamespace(path=Path)
    base = {"frame_w": 16, "frame_h": 16, "order": ANGLES, "frames": [f"h{i}" for i in range(8)]}
    assert sheet_meta(step(base), blobs) is not None
    assert sheet_meta(step({**base, "frames": [*base["frames"][:7], None]}), blobs) is None
    assert sheet_meta(step({**base, "clips": [{"name": "walk", "row0": 0, "rows": 8, "frames": 1}]}), blobs) is None


def test_unity_lod_group_script_for_combined_lods(tmp_path: Path) -> None:
    glb = tmp_path / "chest_lods.glb"
    glb.write_bytes(b"glTF")
    files = dict(unity.export(Source(asset="Chest", uid="u1", glb=[("chest_lods.glb", glb)])))
    assert "Models/chest_lods.glb" in files and "Editor/AssetStudioLodGroup.cs" in files
    assert b"LODGroup" in files["Editor/AssetStudioLodGroup.cs"]
    plain = dict(unity.export(Source(asset="Chest", uid="u1", glb=[("chest_lod0.glb", glb)])))
    assert "Editor/AssetStudioLodGroup.cs" not in plain
