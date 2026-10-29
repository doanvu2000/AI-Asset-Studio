import json
import os
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps

from studio_core.contracts import StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.export import restyle as rs
from studio_core.ops.restyle import (ApplyRestyle, RestyleFit, RestylePlan, ScanProject, edit_canvas, fit_item,
                                     keep_slices, make_seamless, seam, unbox)
from test_ops_executors import FakeCtx

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"

SHEET_META = """fileFormatVersion: 2
guid: 0123456789abcdef0123456789abcdef
TextureImporter:
  serializedVersion: 13
  spriteMode: 2
  alignment: 9
  spritePivot: {x: 0.5, y: 0.125}
  spritePixelsToUnits: 32
  spriteBorder: {x: 0, y: 0, z: 0, w: 0}
  textureType: 8
  spriteSheet:
    serializedVersion: 2
    sprites:
    - serializedVersion: 2
      name: hero_0
      rect:
        serializedVersion: 2
        x: 0
        y: 32
        width: 32
        height: 32
      alignment: 9
      pivot: {x: 0.5, y: 0.125}
      border: {x: 0, y: 0, z: 0, w: 0}
      spriteID: aa
      internalID: -8595281748241699911
    - serializedVersion: 2
      name: hero_1
      rect:
        serializedVersion: 2
        x: 32
        y: 32
        width: 32
        height: 32
      alignment: 9
      pivot: {x: 0.5, y: 0.125}
      border: {x: 0, y: 0, z: 0, w: 0}
      spriteID: bb
      internalID: 5610502074193696207
    - serializedVersion: 2
      name: hero_2
      rect:
        serializedVersion: 2
        x: 0
        y: 0
        width: 32
        height: 32
      alignment: 9
      pivot: {x: 0.5, y: 0.125}
      border: {x: 0, y: 0, z: 0, w: 0}
      spriteID: cc
      internalID: 1
"""

PANEL_META = """fileFormatVersion: 2
guid: fedcba9876543210fedcba9876543210
TextureImporter:
  spriteMode: 1
  spritePivot: {x: 0.5, y: 0.5}
  spritePixelsToUnits: 100
  spriteBorder: {x: 12, y: 10, z: 14, w: 8}
  textureType: 8
  spriteSheet:
    serializedVersion: 2
    sprites: []
"""

NORMAL_META = "fileFormatVersion: 2\nguid: 11112222333344445555666677778888\nTextureImporter:\n  textureType: 1\n"


def hero_sheet() -> Image.Image:
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, (x, y) in enumerate([(0, 0), (32, 0), (0, 32)]):
        d.rectangle((x + 8, y + 6 + i, x + 23, y + 28), fill=(200, 60, 40, 255), outline=(20, 20, 20, 255))
    return im


def panel() -> Image.Image:
    im = Image.new("RGBA", (64, 48), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, 63, 47), 8, fill=(90, 70, 50, 255), outline=(30, 20, 10, 255), width=3)
    return im


def tile() -> Image.Image:
    yy, xx = np.mgrid[0:32, 0:32]
    v = (np.sin(xx / 32 * 2 * np.pi) * 40 + np.cos(yy / 32 * 2 * np.pi) * 40 + 120).astype(np.uint8)
    return Image.fromarray(np.dstack([v, v // 2 + 60, v // 3 + 30]), "RGB")


def unity_project(root: Path) -> Path:
    (root / "ProjectSettings").mkdir(parents=True)
    art = root / "Assets" / "Art"
    (art / "UI").mkdir(parents=True)
    hero_sheet().save(art / "hero.png")
    (art / "hero.png.meta").write_text(SHEET_META, encoding="utf-8")
    panel().save(art / "UI" / "panel.png")
    (art / "UI" / "panel.png.meta").write_text(PANEL_META, encoding="utf-8")
    tile().save(art / "grass_tile.png")
    (art / "grass_tile.png.meta").write_text("fileFormatVersion: 2\nguid: 9999\nTextureImporter:\n  textureType: 0\n",
                                             encoding="utf-8")
    tile().save(art / "rock_n.png")
    (art / "bump.png").write_bytes((art / "rock_n.png").read_bytes())
    (art / "bump.png.meta").write_text(NORMAL_META, encoding="utf-8")
    (root / "Library").mkdir()
    hero_sheet().save(root / "Library" / "cached.png")
    return art


def _hashes(art: Path, suffix: str) -> dict[str, str]:
    return {p.name: rs.file_hash(p) for p in art.rglob(f"*{suffix}")}


def test_parse_unity_meta_rect_border_pivot():
    info = rs.parse_unity_meta(SHEET_META, 64)
    assert info["guid"] == "0123456789abcdef0123456789abcdef" and info["sprite_mode"] == 2
    assert info["texture_type"] == 8 and info["ppu"] == 32 and info["pivot"] == (0.5, 0.125)
    r = info["rects"]
    # Unity rects are bottom-left based: y 32 in a 64 px texture is the top row.
    assert [(x.name, x.x, x.y, x.w, x.h) for x in r] == [("hero_0", 0, 0, 32, 32), ("hero_1", 32, 0, 32, 32),
                                                          ("hero_2", 0, 32, 32, 32)]
    assert r[0].pivot == (0.5, 0.125)
    p = rs.parse_unity_meta(PANEL_META, 48)
    # {x: left, y: bottom, z: right, w: top} -> (left, top, right, bottom)
    assert p["border"] == (12, 8, 14, 10) and p["sprite_mode"] == 1 and p["rects"] == []


def test_parse_godot_import_and_regions(tmp_path: Path):
    imp = '[remap]\n\nimporter="texture"\ntype="CompressedTexture2D"\nuid="uid://abc123"\n\n[params]\n\ncompress/normal_map=0\n'
    assert rs.parse_godot_import(imp) == {"importer": "texture", "uid": "uid://abc123", "type": "CompressedTexture2D",
                                          "normal_map": False}
    tres = tmp_path / "frames.tres"
    tres.write_text('[gd_resource type="SpriteFrames" load_steps=3 format=3]\n\n'
                    '[ext_resource type="Texture2D" uid="uid://abc123" path="res://art/hero.png" id="1_h"]\n\n'
                    '[sub_resource type="AtlasTexture" id="A_1"]\natlas = ExtResource("1_h")\nregion = Rect2(0, 0, 32, 32)\n\n'
                    '[sub_resource type="AtlasTexture" id="A_2"]\natlas = ExtResource("1_h")\nregion = Rect2(32, 0, 32, 32)\n',
                    encoding="utf-8")
    scn = tmp_path / "ui.tscn"
    scn.write_text('[gd_scene format=3]\n\n[ext_resource type="Texture2D" path="res://art/panel.png" id="2_p"]\n\n'
                   '[node name="Panel" type="NinePatchRect" parent="."]\ntexture = ExtResource("2_p")\n'
                   'patch_margin_left = 12\npatch_margin_top = 8\npatch_margin_right = 14\npatch_margin_bottom = 10\n',
                   encoding="utf-8")
    found = rs.godot_regions(tmp_path, [tres, scn])
    assert [(r.x, r.y, r.w, r.h) for r in found["res://art/hero.png"]] == [(0, 0, 32, 32), (32, 0, 32, 32)]
    assert found["res://art/panel.png#nine"][0].border == (12, 8, 14, 10)


def test_scan_unity_project(tmp_path: Path):
    art = unity_project(tmp_path / "Game")
    doc = rs.scan_folder(str(tmp_path / "Game" / "Assets"))
    assert doc["engine"] == "unity" and Path(doc["project"]) == (tmp_path / "Game").resolve()
    by = {i["rel"]: i for i in doc["items"]}
    assert set(by) == {"Art/hero.png", "Art/UI/panel.png", "Art/grass_tile.png"}
    assert by["Art/hero.png"]["kind"] == "sheet" and len(by["Art/hero.png"]["rects"]) == 3
    assert by["Art/UI/panel.png"]["kind"] == "ui" and by["Art/UI/panel.png"]["border"] == (12, 8, 14, 10)
    assert by["Art/grass_tile.png"]["kind"] == "tile"
    assert by["Art/hero.png"]["importer_hash"] == rs.file_hash(art / "hero.png.meta")
    why = {s["rel"]: s["why"] for s in doc["skipped"]}
    assert "normal" in why["Art/rock_n.png"] and "texture type 1" in why["Art/bump.png"]
    assert doc["counts"] == {"sprite": 0, "sheet": 1, "ui": 1, "tile": 1, "texture": 0}
    only = rs.scan_folder(str(tmp_path / "Game" / "Assets"), include="Art/UI/**")
    assert [i["rel"] for i in only["items"]] == ["Art/UI/panel.png"]
    with pytest.raises(rs.RestyleError):
        rs.scan_folder("relative/path")


def test_units_never_cut_a_sprite():
    rects = [{"x": c * 96, "y": r * 128, "w": 96, "h": 128} for r in range(12) for c in range(20)]
    item = {"kind": "sheet", "width": 1920, "height": 1536, "rects": rects}
    boxes = rs.units_for(item)
    assert len(boxes) > 1
    for r in rects:
        inside = [b for b in boxes if b[0] <= r["x"] and r["x"] + r["w"] <= b[2] and b[1] <= r["y"]
                  and r["y"] + r["h"] <= b[3]]
        assert len(inside) == 1
    for b in boxes:
        assert b[2] - b[0] <= rs.WHOLE_SHEET and b[3] - b[1] <= rs.WHOLE_SHEET
    assert rs.units_for({"kind": "sheet", "width": 64, "height": 64, "rects": rects[:3]}) == [[0, 0, 64, 64]]


def test_safe_target_blocks_escape(tmp_path: Path):
    root = tmp_path / "Assets"
    root.mkdir()
    Image.new("RGBA", (8, 8)).save(root / "a.png")
    (root / "a.png.meta").write_text("guid: x")
    Image.new("RGBA", (8, 8)).save(tmp_path / "outside.png")
    assert rs.safe_target(root, "a.png") == (root / "a.png").resolve()
    for bad in ("../outside.png", str(tmp_path / "outside.png"), "a.png.meta", "sub/../../outside.png",
                "C:/Windows/x.png", "missing.png"):
        with pytest.raises(rs.RestyleError):
            rs.safe_target(root, bad)
    link = root / "link"
    try:
        os.symlink(tmp_path, link, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(rs.RestyleError):
        rs.safe_target(root, "link/outside.png")


def test_fit_keeps_size_alpha_and_cells():
    src = hero_sheet()
    item = {"kind": "sheet", "alpha": True,
            "rects": [{"x": 0, "y": 0, "w": 32, "h": 32}, {"x": 32, "y": 0, "w": 32, "h": 32}]}
    recolor = Image.new("RGB", (64, 64), (40, 90, 200))
    img, info = fit_item(src, "RGBA", item, [([0, 0, 64, 64], recolor, None)], "auto", 0.9, False)
    a, b = np.asarray(src), np.asarray(img)
    assert img.size == src.size and info["size_ok"] and info["matte"] == "original"
    assert (a[..., 3] == b[..., 3]).all()
    vis = a[..., 3] > 0
    assert (b[:32][vis[:32]][:, :3] == (40, 90, 200)).all()
    # Outside every sprite rect (bottom-left frame here) nothing changes.
    assert (a[32:] == b[32:]).all()
    assert (b[~vis][:, :3] == a[~vis][:, :3]).all()


def test_fit_matte_when_silhouette_changes():
    src = hero_sheet()
    item = {"kind": "sprite", "alpha": True, "rects": []}
    matte = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(matte).ellipse((4, 4, 60, 60), fill=255)
    img, info = fit_item(src, "RGBA", item, [([0, 0, 64, 64], Image.new("RGB", (64, 64), (9, 9, 9)), matte)],
                         "auto", 0.9, False)
    assert info["matte"] == "birefnet" and info["alpha_iou"] < 0.9 and info["anchor_shift"] > 0
    assert (np.asarray(img)[..., 3] == np.asarray(matte)).all()
    _, keep = fit_item(src, "RGBA", item, [([0, 0, 64, 64], Image.new("RGB", (64, 64), (9, 9, 9)), matte)],
                       "original", 0.9, False)
    assert keep["matte"] == "original"


def test_nine_slice_strips_stay_flat():
    src = panel()
    noisy = np.random.default_rng(1).integers(0, 255, (48, 64, 3), dtype=np.uint8)
    item = {"kind": "ui", "alpha": True, "border": (12, 8, 14, 10), "rects": []}
    img, info = fit_item(src, "RGBA", item, [([0, 0, 64, 48], Image.fromarray(noisy, "RGB"), None)], "auto", 0.9,
                         False)
    out = np.asarray(img).astype(np.float32)
    assert {"top", "bottom", "left", "right"} <= set(info["slice_fixed"])
    assert out[0:8, 12:50].std(axis=1).max() == 0
    assert out[13:38, 0:12].std(axis=0).max() == 0
    assert (np.asarray(img)[..., 3] == np.asarray(src)[..., 3]).all()
    o = np.asarray(src).copy()
    assert keep_slices(o, o.copy(), (0, 0, 0, 0), (0, 0, 64, 48)) == []


def test_tile_stays_seamless():
    src = tile()
    assert seam(np.asarray(src.convert("RGBA"))) < 0.04
    ramp = np.linspace(0, 90, 32)[None, :, None]
    broken = np.clip(np.asarray(src).astype(np.float32) + ramp, 0, 255).astype(np.uint8)
    img, info = fit_item(src, "RGB", {"kind": "tile", "alpha": False, "rects": []},
                         [([0, 0, 32, 32], Image.fromarray(broken, "RGB"), None)], "auto", 0.9, False)
    assert "seamless" in info["slice_fixed"] and img.mode == "RGB" and img.size == (32, 32)
    assert info["seam"][1] < info["seam"][0] + 0.03
    assert seam(make_seamless(np.asarray(src.convert("RGBA")))) < 0.06


def test_edit_canvas_round_trip():
    unit = hero_sheet().crop((0, 0, 32, 32))
    canvas, box = edit_canvas(unit, True, False)
    assert box[2] == box[3] == 32 * 32 and canvas.width == canvas.height
    back = unbox(canvas, canvas.size, box, (32, 32), True)
    assert np.abs(np.asarray(back, np.int16) - np.asarray(Image.alpha_composite(
        Image.new("RGBA", (32, 32), "white"), unit).convert("RGB"), np.int16)).max() <= 1
    ref = Image.new("RGB", (100, 50), (10, 200, 10))
    dip, dbox = edit_canvas(unit, False, False, ref)
    assert dbox[0] > 0 and dip.getpixel((5, 5)) == (10, 200, 10)


def _restyle_edit(img: Image.Image) -> Image.Image:
    """Fake Klein: same layout, new colours."""
    return ImageOps.invert(img.convert("RGB")).resize((img.width // 2 * 2 + 16, img.height // 2 * 2 + 16))


async def _pipeline(ctx: FakeCtx, folder: Path, sample: int = 0):
    scan = await ScanProject().run(ctx, {"folder": str(folder), "sample": sample}, {})
    desc = await ctx.blobs.put_bytes(json.dumps({"items": [{"id": 0, "what": "hero knight", "group": "hero"}]}).encode(),
                                     name="result.json")
    plan = await RestylePlan().run(ctx, {"style": "ink wash"}, {"sources": scan.outputs["sources"],
                                                                "scan": scan.outputs["scan"], "describe": [desc]})
    lay = json.loads(ctx.blobs.path(plan.outputs["layout"][0].hash).read_bytes())
    edits = []
    for ref in plan.outputs["images"]:
        e = _restyle_edit(Image.open(ctx.blobs.path(ref.hash)))
        edits.append(await ctx.blobs.put_bytes(_png(e), name="edit.png", mime="image/png"))
    fit = await RestyleFit().run(ctx, {"matte": "auto"}, {"edits": edits, "sources": scan.outputs["sources"],
                                                          "scan": scan.outputs["scan"], "layout": plan.outputs["layout"]})
    return scan, lay, fit


def _png(img: Image.Image) -> bytes:
    import io
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def test_pipeline_apply_keeps_meta_bytes_and_undo(tmp_path: Path):
    art = unity_project(tmp_path / "Game")
    ctx = FakeCtx(tmp_path / "blobs")
    (tmp_path / "blobs").mkdir()
    folder = tmp_path / "Game" / "Assets"
    scan, lay, fit = await _pipeline(ctx, folder)
    assert len(lay["prompts"]) == len(lay["units"]) == 3
    assert "hero knight" in lay["prompts"][[u["item"] for u in lay["units"]].index(0)] or \
        any("hero knight" in p for p in lay["prompts"])
    assert all("ink wash" in p for p in lay["prompts"])
    report = json.loads(ctx.blobs.path(fit.outputs["restyle"][0].hash).read_bytes())
    assert report["type"] == rs.REPORT_TYPE and len(report["items"]) == 3 and fit.outputs["preview"]
    assert all(i["size_ok"] for i in report["items"]) and "summary" in report["qa"]
    for it in report["items"]:
        new = Image.open(ctx.blobs.path(it["new_hash"]))
        assert new.size == (it["width"], it["height"])

    metas, pngs = _hashes(art, ".meta"), _hashes(art, ".png")
    backups = tmp_path / "backups"
    dry = await ApplyRestyle(backups).run(ctx, {"output": "dry_run"},
                                          {"images": fit.outputs["images"], "restyle": fit.outputs["restyle"]})
    assert dry.meta["written"] == 0 and _hashes(art, ".png") == pngs and not backups.exists()

    mirror = await ApplyRestyle(backups).run(ctx, {"output": "mirror"},
                                             {"images": fit.outputs["images"], "restyle": fit.outputs["restyle"]})
    mdoc = json.loads(ctx.blobs.path(mirror.outputs["apply"][0].hash).read_bytes())
    assert mirror.meta["written"] == 3 and _hashes(art, ".png") == pngs
    assert Path(mdoc["mirror"]) == (tmp_path / "Game_restyled" / "Assets").resolve()
    assert (Path(mdoc["mirror"]) / "Art" / "UI" / "panel.png").is_file()

    applied = await ApplyRestyle(backups).run(ctx, {"output": "apply"},
                                              {"images": fit.outputs["images"], "restyle": fit.outputs["restyle"]})
    assert applied.meta["written"] == 3
    after = _hashes(art, ".png")
    assert after["hero.png"] != pngs["hero.png"] and after["rock_n.png"] == pngs["rock_n.png"]
    assert _hashes(art, ".meta") == metas
    bid = applied.meta["backup_id"]
    man = rs.load_manifest(backups, bid)
    assert man["importers_unchanged"] is True and man["source"]["run_id"] == "r"
    with zipfile.ZipFile(backups / f"{bid}.zip") as z:
        assert sorted(z.namelist()) == ["Art/UI/panel.png", "Art/grass_tile.png", "Art/hero.png"]
        assert rs.blake3(z.read("Art/hero.png")).hexdigest() == pngs["hero.png"]
    assert not list(art.rglob("*restyle~"))

    # A second apply of the same report finds every file changed since its scan and writes nothing.
    again = await ApplyRestyle(backups).run(ctx, {"output": "apply"},
                                            {"images": fit.outputs["images"], "restyle": fit.outputs["restyle"]})
    assert again.meta["written"] == 0 and again.meta["skipped"] == 3

    Image.new("RGBA", (64, 48), (1, 2, 3, 255)).save(art / "UI" / "panel.png")
    res = rs.undo(backups, bid)
    assert sorted(res["restored"]) == ["Art/grass_tile.png", "Art/hero.png"]
    assert [c["rel"] for c in res["conflicts"]] == ["Art/UI/panel.png"]
    now = _hashes(art, ".png")
    assert now["hero.png"] == pngs["hero.png"] and now["grass_tile.png"] == pngs["grass_tile.png"]
    assert _hashes(art, ".meta") == metas
    with pytest.raises(rs.RestyleError):
        rs.undo(backups, bid)
    with pytest.raises(rs.RestyleError):
        rs.load_manifest(backups, "../evil")
    assert rs.list_backups(backups)[0]["backup_id"] == bid


async def test_apply_refuses_tampered_report(tmp_path: Path):
    art = unity_project(tmp_path / "Game")
    ctx = FakeCtx(tmp_path / "blobs")
    (tmp_path / "blobs").mkdir()
    _, _, fit = await _pipeline(ctx, tmp_path / "Game" / "Assets")
    report = json.loads(ctx.blobs.path(fit.outputs["restyle"][0].hash).read_bytes())
    report["items"][0]["rel"] = "../../outside.png"
    report["items"][1]["rel"] = report["items"][1]["rel"] + ".meta"
    report["items"][2]["png_hash"] = "0" * 64
    (art / "hero.png.meta").write_text(SHEET_META + "# edited\n", encoding="utf-8")
    plan = rs.plan_writes(report, {})
    assert all(p["action"] == "skip" for p in plan)
    rep = await ctx.blobs.put_bytes(json.dumps(report).encode(), name="restyle.json")
    out = await ApplyRestyle(tmp_path / "bk").run(ctx, {"output": "apply"},
                                                  {"images": fit.outputs["images"], "restyle": [rep]})
    assert out.meta["written"] == 0
    with pytest.raises(StepError):
        await ApplyRestyle(None).run(ctx, {"output": "apply"}, {"images": [], "restyle": fit.outputs["restyle"]})


async def test_sample_and_plan_mismatch(tmp_path: Path):
    unity_project(tmp_path / "Game")
    ctx = FakeCtx(tmp_path / "blobs")
    (tmp_path / "blobs").mkdir()
    s = await ScanProject().run(ctx, {"folder": str(tmp_path / "Game" / "Assets"), "sample": 2}, {})
    doc = json.loads(ctx.blobs.path(s.outputs["scan"][0].hash).read_bytes())
    assert len(s.outputs["sources"]) == 2 and doc["total"] == 3 and len({i["kind"] for i in doc["items"]}) == 2
    assert s.outputs["thumbs"]
    with pytest.raises(StepError):
        await RestylePlan().run(ctx, {}, {"sources": s.outputs["sources"][:1], "scan": s.outputs["scan"]})
    with pytest.raises(StepError):
        await ScanProject().run(ctx, {"folder": str(tmp_path / "nope")}, {})


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "restyle-folder.yaml")
    assert [s.id for s in r.steps] == ["scan", "describe", "plan", "edit", "matte", "fit", "approve", "apply"]
    edit = next(s for s in r.steps if s.id == "edit")
    assert edit.map == "image" and edit.map_param == "75:74.text"
    assert r.inputs["output"]["default"] == "dry_run"
    _, issues = validate((RECIPES / "restyle-folder.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    for kind in ("op.scan_project", "op.restyle_plan", "op.restyle_fit", "op.apply_restyle"):
        assert CATALOG[kind]["label"]


async def test_plan_skips_when_importer_changed(tmp_path: Path):
    art = unity_project(tmp_path / "Game")
    ctx = FakeCtx(tmp_path / "blobs")
    (tmp_path / "blobs").mkdir()
    _, _, fit = await _pipeline(ctx, tmp_path / "Game" / "Assets")
    report = json.loads(ctx.blobs.path(fit.outputs["restyle"][0].hash).read_bytes())
    from studio_core.ops.restyle import restyled_files
    files = await restyled_files(ctx.blobs, report, fit.outputs["images"])
    (art / "hero.png.meta").write_text(SHEET_META + "# edited\n", encoding="utf-8")
    plan = {p["rel"]: p for p in rs.plan_writes(report, files)}
    assert plan["Art/hero.png"]["action"] == "skip" and "importer" in plan["Art/hero.png"]["why"]
    assert plan["Art/UI/panel.png"]["action"] == "write"
