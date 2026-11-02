import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from studio_core.export import flutter
from studio_core.export.flutter_dart import assets_dart, dart_names, pubspec_snippet
from studio_core.export.source import Source

FW, FH = 6, 9


def _png(tmp: Path, name: str, size=(30, 30)) -> Path:
    p = tmp / name
    Image.new("RGBA", size, (10, 20, 30, 255)).save(p)
    return p


def _sheet(tmp: Path) -> Source:
    meta = {"frame_w": FW, "frame_h": FH, "cols": 2, "rows": 8, "count": 16, "padding": 0, "width": 2 * FW,
            "height": 8 * FH, "pivot": [3, 8], "row_angles": [0, 45, 90, 135, 180, 225, 270, 315],
            "clips": [{"name": "walk", "row0": 0, "rows": 8, "frames": 2, "fps": 10, "loop": True}]}
    return Source(asset="Hero Knight", sheet=_png(tmp, "s.png", (2 * FW, 8 * FH)), meta=meta)


def _img(data) -> Image.Image:
    return Image.open(io.BytesIO(data) if isinstance(data, bytes) else data)


def test_empty_source_has_no_files() -> None:
    assert flutter.export(Source(asset="x")) == []


def test_sheet_files_and_clip_json(tmp_path: Path) -> None:
    files = dict(flutter.export(_sheet(tmp_path)))
    base = "assets/images/sprites/Hero_Knight"
    assert {f"{base}.png", f"{base}.json", f"{base}_anim.json"} <= set(files)
    anim = json.loads(files[f"{base}_anim.json"])
    assert [c["name"] for c in anim["clips"]][:2] == ["walk_S", "walk_SW"]
    c = anim["clips"][0]
    assert c["stepTime"] == 0.1 and c["loop"] is True and len(c["frames"]) == 2
    assert anim["frameSize"] == [FW, FH] and anim["pivot"]["anchorX"] == 0.5
    atlas = json.loads(files[f"{base}.json"])
    assert atlas["frames"]["walk_S_1"]["frame"] == {"x": FW, "y": 0, "w": FW, "h": FH}
    assert "Hero_Knight" in files["README_FLUTTER.md"].decode()
    assert "lib/gen/asset_studio_flame.dart" in files
    assert "loadAsAnimations" in files["lib/gen/asset_studio_flame.dart"].decode()


def test_icons_densities_and_collision(tmp_path: Path) -> None:
    ui = [("Play Btn", _png(tmp_path, "a.png", (30, 30)), (0, 0, 0, 0), ""),
          ("play btn", _png(tmp_path, "b.png", (31, 2)), (0, 0, 0, 0), "")]
    files = dict(flutter.export(Source(asset="kit", ui=ui)))
    assert _img(files["assets/images/Play_Btn.png"]).size == (10, 10)
    assert _img(files["assets/images/2.0x/Play_Btn.png"]).size == (20, 20)
    assert _img(files["assets/images/3.0x/Play_Btn.png"]).size == (30, 30)
    assert _img(files["assets/images/play_btn_2.png"]).size == (31, 2)
    assert _img(files["assets/images/3.0x/play_btn_2.png"]).size == (93, 6)
    dart = files["lib/gen/asset_studio_assets.dart"].decode()
    assert "static const String playBtn = 'assets/images/Play_Btn.png';" in dart
    assert "2.0x" not in dart


def test_pixel_art_scales_up_and_webp(tmp_path: Path) -> None:
    ui = [("coin", _png(tmp_path, "c.png", (16, 16)), (0, 0, 0, 0), "")]
    files = dict(flutter.export(Source(asset="kit", ui=ui, pixel=True), webp=True))
    assert _img(files["assets/images/coin.webp"]).size == (16, 16)
    assert _img(files["assets/images/3.0x/coin.webp"]).size == (48, 48)


def test_nine_slice_json_and_dart(tmp_path: Path) -> None:
    ui = [("panel", _png(tmp_path, "p.png", (64, 48)), (8, 6, 10, 4), "")]
    files = dict(flutter.export(Source(asset="kit", ui=ui)))
    doc = json.loads(files["assets/ui/ui_9slice.json"])
    assert doc == [{"name": "panel", "l": 8, "t": 6, "r": 10, "b": 4, "w": 64, "h": 48}]
    dart = files["lib/gen/asset_studio_ui.dart"].decode()
    assert "static const NineSliceSpec panel = NineSliceSpec('assets/ui/panel.png', 8, 6, 10, 4, 64, 48);" in dart
    assert "centerSlice: spec.center" in dart
    assert "- assets/ui/" in files["pubspec_assets.yaml"].decode()


def test_parallax_font_spine(tmp_path: Path) -> None:
    font = {"face": "F", "size": 16, "line_height": 18, "base": 14, "scale_w": 32, "scale_h": 32,
            "chars": [{"id": 65, "x": 0, "y": 0, "width": 8, "height": 10, "xoffset": 0, "yoffset": 2, "xadvance": 9}],
            "kernings": [[65, 65, -1]]}
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "a.atlas").write_text("x")
    src = Source(asset="Scene", parallax=[("sky", _png(tmp_path, "l0.png"), 0.2), ("fg", _png(tmp_path, "l1.png"), 1.5)],
                 parallax_size=(30, 30), font=font, font_atlas=_png(tmp_path, "f.png"),
                 spine_json=("a.json", tmp_path / "a.json"), spine_atlas=("a.atlas", tmp_path / "a.atlas"),
                 spine_pages=[("a.png", _png(tmp_path, "pg.png"))])
    files = dict(flutter.export(src))
    px = json.loads(files["assets/images/parallax/Scene/parallax.json"])
    assert [(layer["image"], layer["velocityMultiplier"]) for layer in px["layers"]] == [
        ("parallax/Scene/0_sky.png", 0.2), ("parallax/Scene/1_fg.png", 1.5)]
    assert "char id=65" in files["assets/fonts/Scene.fnt"].decode()
    assert json.loads(files["assets/fonts/Scene_font.json"])["kernings"] == [[65, 65, -1]]
    assert "assets/spine/Scene/a.atlas" in files and "assets/spine/Scene/a.png" in files
    yaml = files["pubspec_assets.yaml"].decode()
    for d in ("assets/images/parallax/Scene/", "assets/fonts/", "assets/images/fonts/", "assets/spine/Scene/"):
        assert f"    - {d}\n" in yaml
    flame = files["lib/gen/asset_studio_flame.dart"].decode()
    assert "loadParallax" in flame and "class BitmapFont" in flame and "loadAsAnimations" not in flame


def test_dart_names_valid_unique() -> None:
    paths = ["assets/images/class.png", "assets/images/9lives.png", "assets/ui/a.png", "assets/images/a.png",
             "assets/images/a.json", "assets/images/it's.png"]
    names = dart_names(paths)
    assert len(set(names)) == len(names)
    assert all(n[0].islower() and n.isalnum() and n not in ("class",) for n in names)
    assert "$" not in assets_dart(paths)


def test_pubspec_fonts_and_density_skipped() -> None:
    y = pubspec_snippet(["assets/images/a.png", "assets/images/2.0x/a.png", "assets/fonts/Fancy-Bold.ttf"])
    assert "2.0x" not in y and "family: Fancy" in y and "- asset: assets/fonts/Fancy-Bold.ttf" in y


def test_dart_syntax_when_available(tmp_path: Path) -> None:
    dart = shutil.which("dart")
    if not dart:
        pytest.skip("dart not installed")
    ui = [("panel", _png(tmp_path, "p.png", (64, 48)), (8, 6, 10, 4), "")]
    font = {"face": "F", "size": 16, "line_height": 18, "base": 14, "scale_w": 32, "scale_h": 32, "chars": [], "kernings": []}
    files = flutter.export(Source(asset="Scene", ui=ui, font=font, font_atlas=_png(tmp_path, "f.png"),
                                  parallax=[("sky", _png(tmp_path, "l0.png"), 0.2)], parallax_size=(30, 30),
                                  **{k: v for k, v in _sheet(tmp_path).__dict__.items() if k in ("sheet", "meta")}))
    out = tmp_path / "out"
    for name, data in files:
        if name.endswith(".dart"):
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            (out / name).write_bytes(data if isinstance(data, bytes) else data.read_bytes())
    r = subprocess.run([dart, "format", "--output=none", "--set-exit-if-changed=false", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert "Could not format" not in r.stderr + r.stdout, r.stdout + r.stderr


def test_bitmap_text_does_not_shadow_component_scale(tmp_path: Path) -> None:
    font = {"face": "F", "size": 16, "line_height": 18, "base": 14, "scale_w": 32, "scale_h": 32, "chars": [], "kernings": []}
    files = dict(flutter.export(Source(asset="Scene", font=font, font_atlas=_png(tmp_path, "f.png"))))
    flame = files["lib/gen/asset_studio_flame.dart"].decode()
    assert "this.textScale = 1" in flame and "final double scale;" not in flame
    assert "flame/extensions.dart" not in flame


def _tiled_source(tmp: Path, monkeypatch) -> Source:
    from studio_core.export import playable_scene

    tiles = _png(tmp, "dg_tiles.png", (32, 32))
    monkeypatch.setattr(playable_scene, "extra_files", lambda s: [
        ("Tiled/dg.tmx", b"<map/>"), ("Tiled/dg.ldtk", b"{}"), ("Tiled/dg_tiles.png", tiles),
        ("Tiled/Props/barrel.png", tiles)])
    return Source(asset="dg", playable={"x": 1}, playable_files={"atlas": tiles})


def test_app_icon_yaml_and_adaptive_foreground(tmp_path: Path) -> None:
    ui = [("App Icon", _png(tmp_path, "i.png", (200, 100)), (0, 0, 0, 0), "")]
    files = dict(flutter.export(Source(asset="kit", ui=ui)))
    yaml = files["flutter_launcher_icons.yaml"].decode()
    assert 'image_path: "assets/icon/app_icon.png"' in yaml and "remove_alpha_ios: true" in yaml and "ios: true" in yaml
    assert _img(files["assets/icon/app_icon.png"]).size == (1024, 1024)
    fg = _img(files["assets/icon/app_icon_foreground.png"]).convert("RGBA")
    assert fg.getpixel((0, 0))[3] == 0 and fg.getpixel((512, 512))[3] == 255
    assert "assets/icon/" not in files["pubspec_assets.yaml"].decode() and "icon/" not in files["lib/gen/asset_studio_assets.dart"].decode()
    assert "flutter_launcher_icons" in files["README_FLUTTER.md"].decode()


def test_plain_icon_has_no_launcher_yaml(tmp_path: Path) -> None:
    files = dict(flutter.export(Source(asset="kit", ui=[("coin", _png(tmp_path, "c.png"), (0, 0, 0, 0), "")])))
    assert "flutter_launcher_icons.yaml" not in files and "assets/icon/app_icon.png" not in files


def test_tiled_map_files_helper_and_dependency(tmp_path: Path, monkeypatch) -> None:
    files = dict(flutter.export(_tiled_source(tmp_path, monkeypatch)))
    assert {"assets/tiles/dg.tmx", "assets/tiles/dg_tiles.png", "assets/tiles/Props/barrel.png"} <= set(files)
    assert not any(n.endswith(".ldtk") for n in files)
    yaml = files["pubspec_assets.yaml"].decode()
    assert "dependencies:\n  flame_tiled: ^" in yaml and "    - assets/tiles/\n" in yaml and "    - assets/tiles/Props/\n" in yaml
    flame = files["lib/gen/asset_studio_flame.dart"].decode()
    assert "import 'package:flame_tiled/flame_tiled.dart';" in flame and "TiledComponent.load" in flame
    assert "flame_tiled" in files["README_FLUTTER.md"].decode()


def test_svg_icon_copied_not_rasterised(tmp_path: Path) -> None:
    svg = tmp_path / "logo.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    files = dict(flutter.export(Source(asset="kit", ui=[("Logo", svg, (0, 0, 0, 0), "")])))
    assert files["assets/svg/Logo.svg"] == svg and not any("images/" in n for n in files)
    assert "static const String logo = 'assets/svg/Logo.svg';" in files["lib/gen/asset_studio_assets.dart"].decode()
    assert "flutter_svg" in files["README_FLUTTER.md"].decode() and "flutter_svg: ^" in files["pubspec_assets.yaml"].decode()


def test_tiled_dart_analyzes_when_flutter_available(tmp_path: Path, monkeypatch) -> None:
    dart = shutil.which("dart")
    if not dart:
        pytest.skip("dart not installed")
    flame = dict(flutter.export(_tiled_source(tmp_path, monkeypatch)))["lib/gen/asset_studio_flame.dart"].decode()
    f = tmp_path / "flame.dart"
    f.write_text(flame, encoding="utf-8")
    r = subprocess.run([dart, "format", "--output=none", str(f)], capture_output=True, text=True, timeout=120)
    assert "Could not format" not in r.stderr + r.stdout, r.stdout + r.stderr
