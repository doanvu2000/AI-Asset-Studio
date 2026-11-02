import io
import json
import re
from pathlib import Path

from PIL import Image

from studio_core.export import ios
from studio_core.export.ios_swift import plist_fonts_snippet, swift_names
from studio_core.export.source import Source

FW, FH = 6, 9
CAT = "AssetStudio.xcassets"


def _png(tmp: Path, name: str, size=(30, 30), color=(10, 20, 30, 255)) -> Path:
    p = tmp / name
    Image.new("RGBA", size, color).save(p)
    return p


def _sheet(tmp: Path) -> Source:
    meta = {"frame_w": FW, "frame_h": FH, "cols": 2, "rows": 8, "count": 16, "padding": 0, "width": 2 * FW,
            "height": 8 * FH, "pivot": [3, 8], "row_angles": [0, 45, 90, 135, 180, 225, 270, 315],
            "clips": [{"name": "walk", "row0": 0, "rows": 8, "frames": 2, "fps": 10, "loop": True}]}
    return Source(asset="Hero Knight", sheet=_png(tmp, "s.png", (2 * FW, 8 * FH)), meta=meta)


def _size(data) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


def _doc(files: dict, path: str) -> dict:
    return json.loads(files[path])


def test_empty_source_has_no_files() -> None:
    assert ios.export(Source(asset="x")) == []


def test_icons_three_scales_and_catalog(tmp_path: Path) -> None:
    ui = [("Play Btn", _png(tmp_path, "a.png", (30, 30)), (0, 0, 0, 0), ""),
          ("play btn", _png(tmp_path, "b.png", (31, 2)), (0, 0, 0, 0), "")]
    files = dict(ios.export(Source(asset="kit", ui=ui)))
    assert _doc(files, f"{CAT}/Contents.json")["info"] == {"author": "xcode", "version": 1}
    doc = _doc(files, f"{CAT}/Play_Btn.imageset/Contents.json")
    assert [(i["scale"], i["idiom"]) for i in doc["images"]] == [("1x", "universal"), ("2x", "universal"),
                                                                  ("3x", "universal")]
    assert all("resizing" not in i for i in doc["images"])
    for i in doc["images"]:
        assert f"{CAT}/Play_Btn.imageset/{i['filename']}" in files
    assert _size(files[f"{CAT}/Play_Btn.imageset/Play_Btn@1x.png"]) == (10, 10)
    assert _size(files[f"{CAT}/Play_Btn.imageset/Play_Btn@3x.png"]) == (30, 30)
    assert _size(files[f"{CAT}/play_btn_2.imageset/play_btn_2@3x.png"]) == (93, 6)
    swift = files["Sources/AssetStudioAssets.swift"].decode()
    assert 'static let playBtn = "Play_Btn"' in swift and 'static let playBtn2 = "play_btn_2"' in swift
    assert "Sources/AssetStudioKit.swift" not in files


def test_pixel_art_scales_up(tmp_path: Path) -> None:
    ui = [("coin", _png(tmp_path, "c.png", (16, 16)), (0, 0, 0, 0), "")]
    files = dict(ios.export(Source(asset="kit", ui=ui, pixel=True)))
    base = f"{CAT}/coin.imageset/"
    assert [_size(files[f"{base}coin@{s}x.png"]) for s in (1, 2, 3)] == [(16, 16), (32, 32), (48, 48)]


def test_nine_slice_cap_insets_per_scale(tmp_path: Path) -> None:
    ui = [("panel", _png(tmp_path, "p.png", (60, 45)), (9, 6, 12, 3), "")]
    files = dict(ios.export(Source(asset="kit", ui=ui)))
    imgs = _doc(files, f"{CAT}/panel.imageset/Contents.json")["images"]
    got = {i["scale"]: i["resizing"] for i in imgs}
    assert got["3x"]["mode"] == "9-part"
    assert got["3x"]["cap-insets"] == {"top": 6, "left": 9, "bottom": 3, "right": 12}
    assert got["3x"]["center"] == {"mode": "stretch", "width": 39, "height": 36}
    assert got["1x"]["cap-insets"] == {"top": 2, "left": 3, "bottom": 1, "right": 4}
    assert got["2x"]["cap-insets"] == {"top": 4, "left": 6, "bottom": 2, "right": 8}
    kit = files["Sources/AssetStudioKit.swift"].decode()
    assert "EdgeInsets(top: 2, leading: 3, bottom: 1, trailing: 4)" in kit
    assert ".resizable(capInsets: insets, resizingMode: .stretch)" in kit


def test_nine_slice_modes_and_clamp(tmp_path: Path) -> None:
    ui = [("bar", _png(tmp_path, "b.png", (60, 30)), (10, 0, 10, 0), ""),
          ("tall", _png(tmp_path, "t.png", (30, 60)), (0, 9, 0, 9), ""),
          ("big", _png(tmp_path, "g.png", (30, 30)), (40, 5, 40, 5), "")]
    files = dict(ios.export(Source(asset="kit", ui=ui)))
    bar = _doc(files, f"{CAT}/bar.imageset/Contents.json")["images"][2]["resizing"]
    assert bar["mode"] == "3-part-horizontal" and bar["center"] == {"mode": "stretch", "width": 40}
    tall = _doc(files, f"{CAT}/tall.imageset/Contents.json")["images"][2]["resizing"]
    assert tall["mode"] == "3-part-vertical" and tall["center"]["height"] == 42
    for i in _doc(files, f"{CAT}/big.imageset/Contents.json")["images"]:
        c, size = i["resizing"]["cap-insets"], _size(files[f"{CAT}/big.imageset/{i['filename']}"])
        assert c["left"] + c["right"] < size[0] and i["resizing"]["center"]["width"] >= 1


def test_sprite_atlas_matches_anim_json(tmp_path: Path) -> None:
    files = dict(ios.export(_sheet(tmp_path)))
    atlas = f"{CAT}/Hero_Knight.spriteatlas"
    assert _doc(files, f"{atlas}/Contents.json")["info"]["version"] == 1
    anim = _doc(files, "Resources/Hero_Knight_anim.json")
    assert anim["atlas"] == "Hero_Knight" and anim["frameSize"] == [FW, FH]
    assert [c["name"] for c in anim["clips"]][:2] == ["walk_S", "walk_SW"]
    assert anim["clips"][0]["fps"] == 10 and anim["clips"][0]["loop"] is True
    names = {n for c in anim["clips"] for n in c["frames"]}
    assert len(names) == 16
    for n in names:
        assert _size(files[f"{atlas}/{n}.imageset/{n}.png"]) == (FW, FH)
        assert _doc(files, f"{atlas}/{n}.imageset/Contents.json")["images"][0]["filename"] == f"{n}.png"
    kit = files["Sources/AssetStudioKit.swift"].decode()
    assert "TimelineView(.animation)" in kit and "SKTextureAtlas(named: set.atlas)" in kit
    swift = files["Sources/AssetStudioAssets.swift"].decode()
    assert 'static let heroKnight = "Hero_Knight"' in swift
    assert 'static let heroKnightAnim = "Hero_Knight_anim"' in swift


def test_parallax_layers(tmp_path: Path) -> None:
    src = Source(asset="Forest", parallax=[("sky", _png(tmp_path, "a.png"), 0.0), ("trees", _png(tmp_path, "b.png"), 0.5)],
                 parallax_size=(30, 30))
    files = dict(ios.export(src))
    assert f"{CAT}/Forest_sky.imageset/Contents.json" in files
    doc = _doc(files, "Resources/Forest_parallax.json")
    assert [(m["image"], m["scroll"]) for m in doc["layers"]] == [("Forest_sky", 0.0), ("Forest_trees", 0.5)]
    kit = files["Sources/AssetStudioKit.swift"].decode()
    assert 'AssetStudioParallaxLayer(name: "Forest_trees", factor: 0.5)' in kit
    assert "struct AssetStudioParallax: View" in kit


def test_swift_names_keywords_and_clashes() -> None:
    assert swift_names(["class", "Class", "9lives", "a-b", "a_b", "self"]) == [
        "classAsset", "classAsset2", "a9lives", "aB", "aB2", "selfAsset"]


def test_swift_braces_balanced(tmp_path: Path) -> None:
    src = _sheet(tmp_path)
    src.ui = [("panel", _png(tmp_path, "p.png", (60, 45)), (9, 6, 12, 3), "")]
    src.parallax = [("sky", _png(tmp_path, "a.png"), 0.0)]
    files = dict(ios.export(src))
    for n in ("Sources/AssetStudioKit.swift", "Sources/AssetStudioAssets.swift"):
        text = files[n].decode()
        assert text.count("{") == text.count("}") and text.count("(") == text.count(")")
    assert re.search(r"^import SwiftUI", files["Sources/AssetStudioKit.swift"].decode(), re.M)


def test_app_icon_opaque_single_size(tmp_path: Path) -> None:
    p = tmp_path / "icon.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 0)).save(p)
    files = dict(ios.app_icon_files(p))
    base = f"{CAT}/AppIcon.appiconset/"
    doc = _doc(files, base + "Contents.json")
    assert doc["images"] == [{"filename": "AppIcon.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"}]
    img = Image.open(io.BytesIO(files[base + "AppIcon.png"]))
    assert img.size == (1024, 1024) and img.mode == "RGB"
    full = dict(ios.app_icon_files(p, variants=True))
    values = [i["appearances"][0]["value"] for i in _doc(full, base + "Contents.json")["images"][1:]]
    assert values == ["dark", "tinted"] and base + "AppIcon-tinted.png" in full


def test_plist_snippet() -> None:
    xml = plist_fonts_snippet(["A.ttf", "B.otf"])
    assert "<key>UIAppFonts</key>" in xml and "<string>B.otf</string>" in xml
