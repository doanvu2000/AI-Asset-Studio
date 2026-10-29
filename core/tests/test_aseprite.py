import io
import json
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import Lane, RunRow, StepError, StepRow, StepStatus
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.export.aseprite import (AseDoc, AseLayer, AseTag, AsepriteError, export_files, import_ase,
                                         import_json, palette_of, read, write)
from studio_core.export.sheet import layout
from studio_core.export.source import Source
from studio_core.ops.aseprite import AsepriteImport
from studio_core.ops.executors import Sheet

from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)
from test_ops_executors import FakeCtx, reg  # noqa: F401

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
W, H = 16, 20
DIRS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]


def _frame(seed: int, w: int = W, h: int = H) -> np.ndarray:
    rng = np.random.default_rng(seed)
    a = np.zeros((h, w, 4), np.uint8)
    a[3:h - 2, 4:w - 3, :3] = rng.integers(0, 255, (h - 5, w - 7, 3))
    a[3:h - 2, 4:w - 3, 3] = 255
    return a


def _doc(frames: int = 4) -> AseDoc:
    cels = [[_frame(i), _frame(100 + i) if i % 2 else None] for i in range(frames)]
    return AseDoc(W, H, [AseLayer("body"), AseLayer("hat", opacity=200)], cels, [100, 125, 83, 83][:frames],
                  [AseTag("walk_S", 0, 1), AseTag("walk_N", 2, 3, loop=False)], (8, 19), palette_of([cels[0][0]]))


def test_written_file_parses_with_independent_reader() -> None:
    aseprite = pytest.importorskip("aseprite")
    doc = _doc()
    s = aseprite.Sprite.from_bytes(write(doc))
    assert s.size == (W, H) and [f.duration_ms for f in s.frames] == [100, 125, 83, 83]
    assert [la.name for la in s.layers] == ["body", "hat"] and s.layers[1].opacity == 200
    assert [(t.name, t.from_frame, t.to_frame, t.repeat) for t in s.tags] == [("walk_S", 0, 1, 0), ("walk_N", 2, 3, 1)]
    assert [(sl.name, sl.keys[0].pivot) for sl in s.slices] == [("pivot", (8, 19))]
    assert len(s.palette) == len(doc.palette)
    for i in range(4):
        theirs = np.frombuffer(s.flatten(i), np.uint8).reshape(H, W, 4).astype(int)
        assert np.abs(theirs - doc.flatten(i).astype(int)).max() <= 1


def test_reader_matches_independent_renderer_on_foreign_files() -> None:
    aseprite = pytest.importorskip("aseprite")
    s = aseprite.Sprite(12, 10, empty=True)
    base = s.add_layer("base")
    grp = s.add_layer("grp", kind=aseprite.LayerType.GROUP)
    s.add_layer("inner", parent=grp, opacity=128)
    hidden = s.add_layer("hidden")
    hidden.visible = False
    for _ in range(3):
        s.add_frame(90)
    rng = np.random.default_rng(3)
    for fi in (0, 1):
        px = rng.integers(0, 256, (4, 5, 4), dtype=np.uint8)
        s.frames[fi].set_cel(base, aseprite.Pixels(5, 4, px.tobytes(), aseprite.ColorMode.RGBA), 2 + fi, 3)
        s.frames[fi].set_cel(s.layers[2], aseprite.Pixels(3, 3, bytes([200, 10, 10, 255] * 9),
                                                          aseprite.ColorMode.RGBA), 1, 1, opacity=180)
        s.frames[fi].set_cel(hidden, aseprite.Pixels(2, 2, bytes([0, 255, 0, 255] * 4), aseprite.ColorMode.RGBA))
    s.frames[2].set_linked_cel(base, 0)
    s.add_tag("idle", 0, 2, direction=aseprite.LoopDirection.PING_PONG)
    doc = read(s.to_bytes())
    assert [la.name for la in doc.layers] == ["base", "inner", "hidden"] and not doc.layers[2].visible
    for i in range(3):
        theirs = np.frombuffer(s.flatten(i), np.uint8).reshape(10, 12, 4).astype(int)
        assert np.abs(theirs - doc.flatten(i).astype(int)).max() <= 2, i
    assert doc.tags[0].frames() == [0, 1, 2, 1]


def test_indexed_files_use_palette_and_transparent_index() -> None:
    aseprite = pytest.importorskip("aseprite")
    s = aseprite.Sprite(4, 2, aseprite.ColorMode.INDEXED, empty=True)
    s.palette = aseprite.Palette([aseprite.Color(0, 0, 0, 0), aseprite.Color(255, 0, 0), aseprite.Color(0, 0, 255)])
    s.num_colors = 3
    la = s.add_layer("ink")
    s.add_frame()
    s.frames[0].set_cel(la, aseprite.Pixels(4, 2, bytes([0, 1, 2, 1, 2, 0, 0, 1]), aseprite.ColorMode.INDEXED))
    got = read(s.to_bytes()).flatten(0)
    assert got[0, 0, 3] == 0 and tuple(got[0, 1]) == (255, 0, 0, 255) and tuple(got[0, 2]) == (0, 0, 255, 255)


def test_bad_files_raise() -> None:
    with pytest.raises(AsepriteError):
        read(b"\0" * 200)
    with pytest.raises(AsepriteError):
        read(write(_doc())[:150])


def _anim_meta() -> dict:
    """op.sheet meta of a 2-clip × 8-direction sheet (walk 3 frames @10fps, attack 2 frames @12fps one-shot)."""
    return {"frame_w": W, "frame_h": H, "cols": 3, "rows": 16, "count": 48, "padding": 0, "width": 3 * W,
            "height": 16 * H, "pivot": [8, 19], "row_angles": [d * 45 for d in range(8)] * 2,
            "clips": [{"name": "walk", "row0": 0, "rows": 8, "frames": 3, "fps": 10, "loop": True},
                      {"name": "attack", "row0": 8, "rows": 8, "frames": 2, "fps": 12, "loop": False}]}


def _anim_src(tmp: Path) -> Source:
    meta = _anim_meta()
    sheet = np.zeros((meta["height"], meta["width"], 4), np.uint8)
    for r in range(16):
        for c in range(3):
            if r < 8 or c < 2:
                sheet[r * H:(r + 1) * H, c * W:(c + 1) * W] = _frame(r * 3 + c)
    p = tmp / "hero_sheet.png"
    Image.fromarray(sheet, "RGBA").save(p)
    return Source(asset="hero", sheet=p, meta=meta, clip_hint="walk", directional_rows=True)


def test_export_tags_per_clip_direction_and_json_sheet(tmp_path: Path) -> None:
    files, note = export_files(_anim_src(tmp_path))
    by = dict(files)
    assert set(by) == {"aseprite/hero.aseprite", "aseprite/hero.png", "aseprite/hero.json"} and "Aseprite" in note
    doc = read(by["aseprite/hero.aseprite"])
    assert (doc.width, doc.height, len(doc.frames)) == (W, H, 8 * 3 + 8 * 2)
    assert [t.name for t in doc.tags][:2] == ["walk_S", "walk_SW"] and doc.tags[8].name == "attack_S"
    assert doc.durations[0] == 100 and doc.durations[-1] == 83 and not doc.tags[8].loop and doc.pivot == (8, 19)
    js = json.loads(by["aseprite/hero.json"])
    assert js["meta"]["image"] == "hero.png" and len(js["frames"]) == 40
    assert js["meta"]["frameTags"][9] == {"name": "attack_SW", "from": 26, "to": 27, "direction": "forward",
                                          "color": "#000000ff", "repeat": "1"}
    assert js["frames"][3]["frame"] == {"x": 0, "y": H, "w": W, "h": H}
    assert js["meta"]["slices"][0]["keys"][0]["pivot"] == {"x": 8, "y": 19}


def test_json_and_ase_import_round_trip_back_to_the_same_clips(tmp_path: Path) -> None:
    src = _anim_src(tmp_path)
    by = dict(export_files(src)[0])
    sheet = np.asarray(Image.open(src.sheet).convert("RGBA"))
    for got in (import_json(json.loads(by["aseprite/hero.json"]), sheet), import_ase(by["aseprite/hero.aseprite"])):
        a = got.anim
        assert a["angles"] == [d * 45 for d in range(8)] and a["pivot"] == [8, 19]
        assert [(c["name"], c["frames"], c["loop"]) for c in a["clips"]] == [("walk", 3, True), ("attack", 2, False)]
        assert a["clips"][0]["fps"] == 10 and abs(a["clips"][1]["fps"] - 12.048) < 0.01
        assert got.names[:4] == ["walk_S_00", "walk_S_01", "walk_S_02", "walk_SW_00"]
        assert np.array_equal(got.frames[3], sheet[H:2 * H, 0:W])


def test_json_import_untrims_hash_format_and_falls_back_to_one_clip_per_tag() -> None:
    sheet = np.zeros((8, 16, 4), np.uint8)
    sheet[:, :, 3] = 255
    sheet[:, 8:, 0] = 200
    doc = {"frames": {"a 0": {"frame": {"x": 0, "y": 0, "w": 8, "h": 8}, "spriteSourceSize": {"x": 2, "y": 4, "w": 8,
                                                                                              "h": 8},
                              "sourceSize": {"w": 12, "h": 12}, "duration": 50},
                      "a 1": {"frame": {"x": 8, "y": 0, "w": 8, "h": 8}, "spriteSourceSize": {"x": 0, "y": 0, "w": 8,
                                                                                              "h": 8},
                              "sourceSize": {"w": 12, "h": 12}, "duration": 50}},
           "meta": {"frameTags": [{"name": "Jump", "from": 0, "to": 1, "direction": "pingpong"},
                                  {"name": "hurt", "from": 1, "to": 1, "repeat": "1"}]}}
    got = import_json(doc, sheet)
    assert got.frames[0].shape == (12, 12, 4) and got.frames[0][4, 2, 3] == 255 and got.frames[0][0, 0, 3] == 0
    assert [(c["name"], c["frames"], c["fps"], c["loop"]) for c in got.anim["clips"]] == [
        ("Jump", 2, 20.0, True), ("hurt", 1, 20.0, False)]
    assert got.anim["angles"] == [0] and got.anim["pivot"] == [6, 12] and got.names == ["Jump_00", "Jump_01", "hurt_00"]
    with pytest.raises(AsepriteError):
        import_json({"frames": [{"frame": {"x": 0, "y": 0, "w": 1, "h": 1}, "rotated": True}]}, sheet)


def test_image_layers_export_one_frame_many_layers(tmp_path: Path) -> None:
    paths = []
    for i in range(3):
        p = tmp_path / f"l{i}.png"
        Image.fromarray(_frame(i, 24, 24), "RGBA").save(p)
        paths.append((f"layer{i}", p))
    src = Source(asset="scene", image_layers=paths, image_layers_size=(24, 24))
    by = dict(export_files(src)[0])
    doc = read(by["aseprite/scene.aseprite"])
    assert [la.name for la in doc.layers] == ["layer0", "layer1", "layer2"] and len(doc.frames) == 1
    assert json.loads(by["aseprite/scene.json"])["meta"]["layers"][2]["name"] == "layer2"
    assert export_files(Source(asset="x")) == ([], None)


async def test_import_op_feeds_op_sheet_with_clip_layout(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    src = _anim_src(tmp_path)
    by = dict(export_files(src)[0])
    ase = await ctx.blobs.put_bytes(by["aseprite/hero.aseprite"], name="hero.aseprite", mime="application/octet-stream")
    res = await AsepriteImport().run(ctx, {}, {"files": [ase]})
    assert len(res.outputs["images"]) == 40 and res.meta["clips"] == ["walk", "attack"]
    sheet = await Sheet().run(ctx, {}, {"images": res.outputs["images"], "pivot": res.outputs["anim"]})
    lay = layout(sheet.meta, "anim")
    assert [t.name for t in lay.tracks][:2] == ["walk_S", "walk_SW"] and len(lay.tracks) == 16
    assert lay.pivot == (8, 19) and sheet.meta["rows"] == 16 and sheet.meta["cols"] == 3

    js = await ctx.blobs.put_bytes(by["aseprite/hero.json"], name="hero.json", mime="application/json")
    png = await ctx.blobs.put_bytes(src.sheet.read_bytes(), name="hero.png", mime="image/png")
    res2 = await AsepriteImport().run(ctx, {}, {"files": [js, png]})
    assert [r.hash for r in res2.outputs["images"]] == [r.hash for r in res.outputs["images"]]
    with pytest.raises(StepError):
        await AsepriteImport().run(ctx, {}, {"files": [js]})


def test_recipe_loads_lints_and_op_is_registered(reg) -> None:  # noqa: F811
    r = load_recipe_file(RECIPES / "aseprite-import.yaml")
    assert [s.id for s in r.steps] == ["frames", "sheet", "approve"]
    _, issues = validate((RECIPES / "aseprite-import.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    assert reg.resolve("op.aseprite_import")


async def test_api_engine_export_with_aseprite(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    repo, blobs = app.state.repo, app.state.blobs
    src = _anim_src(tmp_path)
    await repo.create_run(RunRow(id="r1", recipe_id="sprite-8dir-anim", recipe_hash="h", created_at=time.time()))
    meta = await blobs.put_bytes(json.dumps(src.meta).encode(), name="sheet.json", mime="application/json")
    sheet = await blobs.put_bytes(src.sheet.read_bytes(), name="sheet.png", mime="image/png")
    await repo.create_steps([StepRow(id="anim-1", run_id="r1", key="anim_sheet", kind="op.sheet", lane=Lane.CPU,
                                     status=StepStatus.SUCCEEDED, params={"asset": "hero"},
                                     outputs={"sheet": [sheet], "meta": [meta]})])
    opts = (await client.get("/api/steps/anim-1/export", headers=AUTH)).json()
    assert opts["aseprite"] is True
    out = (await client.post("/api/steps/anim-1/export", json={"engine": "godot", "aseprite": True},
                             headers=AUTH)).json()
    d = Path(out["dir"])
    assert len(read((d / "aseprite" / "hero.aseprite").read_bytes()).tags) == 16
    assert "Aseprite" in (d / "README.txt").read_text(encoding="utf-8")
    off = (await client.post("/api/steps/anim-1/export", json={"engine": "unity"}, headers=AUTH)).json()
    assert not (Path(off["dir"]) / "aseprite").exists()


def test_png_written_matches_sheet_bytes(tmp_path: Path) -> None:
    src = _anim_src(tmp_path)
    by = dict(export_files(src)[0])
    assert by["aseprite/hero.png"] == src.sheet


def test_paperdoll_layers_and_normal_ride_along_on_the_same_cells(tmp_path: Path) -> None:
    src = _anim_src(tmp_path)
    sheet = np.asarray(Image.open(src.sheet))
    hat = np.zeros_like(sheet)
    hat[::H, :, :] = (255, 0, 0, 255)
    Image.fromarray(hat, "RGBA").save(tmp_path / "hat.png")
    src.paperdoll_layers = [("hat", tmp_path / "hat.png")]
    src.normal = tmp_path / "hat.png"
    by = dict(export_files(src)[0])
    doc = read(by["aseprite/hero.aseprite"])
    assert [(la.name, la.visible) for la in doc.layers] == [("sprite", True), ("hat", True), ("normal", False)]
    assert tuple(doc.flatten(0)[0, 0]) == (255, 0, 0, 255)
    png = np.asarray(Image.open(io.BytesIO(by["aseprite/hero.png"])))
    assert tuple(png[0, 0]) == (255, 0, 0, 255)
    assert json.loads(by["aseprite/hero.json"])["meta"]["layers"][1]["name"] == "hat"
