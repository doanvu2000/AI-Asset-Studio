import json
import os
import re
from pathlib import Path

import pytest
from PIL import Image

from studio_core.export import ios
from studio_core.export.ios_fx import MAX_VIEWS, _grouped, sw_str
from studio_core.export.ios_fx_swift import FX_FILE
from studio_core.export.ios_swift import SWIFT_KEYWORDS
from studio_core.export.source import Source
from studio_core.store.bundle import export_engine
from studio_core.store.pack_mobile import IosPack
from test_export_flutter_fx import _autotile_src, _img, _screen_steps, _tile_anim_src, _vfx_src, _weather_src

RES = "Resources/AssetStudioFx"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PUBLIC_API = ("TileAnimation", "TileAnimationImage", "AutotileSet", "AutotileMap", "TileMap", "TileMapView", "VfxLibrary",
              "VfxView", "WeatherView", "MistView", "MinimapView", "MinimapPoi", "CardEntry", "CardFace")

USAGE = """import SwiftUI

struct FxUsage: View {
    @State private var anim = TileAnimation.load("Water")
    @State private var tiles = AutotileSet.load("Grass_Shore")
    @State private var map = TileMap.load("Water_tile_anim")
    @State private var vfx = VfxLibrary.load("Fx_Pack")
    @State private var weather = VfxLibrary.loadWeather("Sky_Fx")
    let cells = [[true, false], [false, true]]

    var body: some View {
        ZStack {
            if let anim { TileAnimationImage(anim, col: 1, row: 2).frame(width: 64, height: 64) }
            if let tiles { AutotileMap(tiles, cells: cells).frame(height: 200) }
            if let map { TileMapView(map).frame(height: 200) }
            if let vfx { VfxView(vfx, "explosion", origin: CGPoint(x: 160, y: 240), onFinished: { }) }
            if let weather {
                WeatherView(weather, "rain_light")
                MistView(weather, "mist")
            }
            MinimapView(asset: "minimap/World/World_minimap.png", pois: [MinimapPoi("town", 0.25, 0.5)]) { poi in
                Circle().fill(poi.kind == "town" ? Color.red : Color.blue)
            }
            .frame(width: 160, height: 160)
            CardFace(CardEntry("a", "A", "cards/x/a.png", nil).front).frame(width: 180)
        }
    }
}
"""


def _swift_code(text: str) -> str:
    """Swift source with comments and string literals blanked, so bracket counts only see code."""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else len(text)
        elif text.startswith('"""', i):
            raise AssertionError("multi-line string literal")
        elif c == '"':
            i += 1
            while text[i] != '"':
                assert text[i] != "\n", "unterminated string"
                i += 2 if text[i] == "\\" else 1
            i += 1
            out.append('""')
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _balanced(swift: str) -> None:
    code = _swift_code(swift)
    stack = []
    for ch in code:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            assert stack and "([{".index(stack.pop()) == ")]}".index(ch), swift
    assert not stack, swift


def _export(src: Source) -> dict[str, bytes]:
    out = {n: (d if isinstance(d, bytes) else d.read_bytes()) for n, d in ios.export(src)}
    if root := os.environ.get("FXI_OUT"):  # dump the tree so a Mac can compile it with the Usage sample
        for n, data in out.items():
            dst = Path(root) / src.asset.replace(" ", "_") / n
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
    return out


def _declared(swift: str) -> set[str]:
    return set(re.findall(r"^(?:private |final )*(?:struct|class|enum) (\w+)", swift, re.M))


def _check_identifiers(swift: str) -> None:
    for name in _declared(swift):
        assert IDENT.match(name) and name not in SWIFT_KEYWORDS and not name[0].isdigit(), name


def test_runtime_is_shared_and_complete(tmp_path: Path) -> None:
    f = _export(_autotile_src(tmp_path))
    rt = f[FX_FILE].decode()
    assert rt.startswith("// GENERATED") and "@PKG@" not in rt
    declared = _declared(rt)
    assert set(PUBLIC_API) <= declared
    assert {"StudioScreen", "StudioPlace", "StudioSkin", "StudioButton", "StudioText", "StudioImage", "NineSliceSpec",
            "VfxSimulation", "StudioRandom"} <= declared
    _balanced(rt)
    _check_identifiers(rt)
    assert "UIKit" not in rt and "AppKit" not in rt and "import SpriteKit" not in rt
    assert "Sources/AssetStudioAssets.swift" not in f and not any(k.startswith("AssetStudio.xcassets") for k in f)


def test_tile_anim_single(tmp_path: Path) -> None:
    f = _export(_tile_anim_src(tmp_path, False))
    base = f"{RES}/tiles"
    assert {f"{base}/Water_tile_anim.png", f"{base}/Water_tile_anim.json", f"{base}/Water_tile_anim_map.json", FX_FILE,
            "README_IOS.md"} <= set(f)
    doc = json.loads(f[f"{base}/Water_tile_anim.json"])
    assert doc["sheet"] == "Water_tile_anim.png" and doc["frames"] == 4 and doc["layout"]["frame_offset"] == [1, 0]
    m = json.loads(f[f"{base}/Water_tile_anim_map.json"])
    assert m["image"] == "Water_tile_anim.png" and (m["width"], m["height"]) == (8, 5)
    assert m["animation"] == {"frames": 4, "fps": 8.0, "step": 1}
    assert len(m["tiles"]) == 5 and all(len(r) == 8 and set(r) == {0} for r in m["tiles"])
    assert f"{base}/{m['image']}" in f


def test_tile_anim_blob_map_ids_in_sheet(tmp_path: Path) -> None:
    f = _export(_tile_anim_src(tmp_path, True, "Lake"))
    m = json.loads(f[f"{RES}/tiles/Lake_tile_anim_map.json"])
    assert m["animation"]["step"] == 6 * 8 and m["cols"] == 8 and m["rows"] == 24
    assert max(max(r) for r in m["tiles"]) + 3 * m["animation"]["step"] < m["cols"] * m["rows"]
    assert len(m["tiles"]) == 8 and len(m["tiles"][0]) == 12


def test_autotile_json_and_map(tmp_path: Path) -> None:
    f = _export(_autotile_src(tmp_path))
    base = f"{RES}/tiles"
    doc = json.loads(f[f"{base}/Grass_Shore_autotile.json"])
    assert doc["atlas"] == "Grass_Shore_autotile.png" and len(doc["tiles"]) == 48 and isinstance(doc["tile_size"], int)
    assert sum(1 for e in doc["tiles"] if e["mask"] is None) == 1
    m = json.loads(f[f"{base}/Grass_Shore_autotile_map.json"])
    assert m["animation"] is None and m["image"] == doc["atlas"] and f"{base}/{m['image']}" in f
    assert max(max(r) for r in m["tiles"]) < 8 * 6


def test_vfx_files_and_doc(tmp_path: Path) -> None:
    f = _export(_vfx_src(tmp_path))
    base = f"{RES}/vfx/Fx_Pack"
    doc = json.loads(f[f"{base}/Fx_Pack_vfx.json"])
    assert doc["ppu"] == 100.0 and [p["name"] for p in doc["presets"]] == ["explosion", "heal", "projectile_trail"]
    assert all("trail" not in p for p in doc["presets"])
    assert all(f"{base}/{t['file']}" in f for t in doc["textures"].values())
    need = {"texture", "blend", "delay", "burst", "rate", "lifetime", "speed", "shape", "gravity", "drag", "size",
            "size_curve", "rotation", "spin", "align", "colors"}
    for p in doc["presets"]:
        assert {"name", "loop", "duration", "emitters"} <= set(p)
        for e in p["emitters"]:
            assert need <= set(e) and len(e["lifetime"]) == 2 and e["texture"] in doc["textures"]
            assert all(len(c) == 2 and re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", c[1]) for c in e["colors"])


@pytest.mark.asyncio
async def test_weather_files_doc(tmp_path: Path) -> None:
    f = _export(await _weather_src(tmp_path))
    base = f"{RES}/weather/Sky_Fx"
    doc = json.loads(f[f"{base}/Sky_Fx_weather.json"])
    assert [m["name"] for m in doc["mists"]] == ["mist"] and doc["area"]
    assert all(f"{base}/{t['file']}" in f for t in doc["textures"].values())
    rain = next(p for p in doc["presets"] if p["name"] == "rain_light")["emitters"][0]
    assert rain["box"] and rain["direction"] and rain["rate"] > 0 and rain["colors"]
    assert all(re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", c[1]) for c in rain["colors"])


def test_minimap_and_cards_swift(tmp_path: Path) -> None:
    mm = {"size": [64, 64], "pois": [{"name": "Town", "type": "town", "px": [16, 32]}, {"name": "bad"},
                                     {"type": 'a"b\\c', "px": [64, 0]}]}
    f = _export(Source(asset="World", minimap=mm, minimap_files={"map": _img(tmp_path, "m.png", (64, 64))}))
    sw = f["Sources/World_minimap.swift"].decode()
    assert 'MinimapPoi("town", 0.2500, 0.5000)' in sw and 'MinimapPoi("a\\"b\\\\c", 1.0000, 0.0000)' in sw
    assert "enum WorldMinimap" in sw and sw.count("MinimapPoi(") == 2
    assert RES + "/" + re.search(r'asset = "([^"]+)"', sw)[1] in f
    _balanced(sw)
    cards = {"size": [100, 140], "cards": [{"id": "1st Card", "name": 'Dragon "Roar" \\(x) $5\n', "frame": "f", "texts": {}},
                                           {"id": "1st card", "name": "Twin", "frame": "f", "texts": {}},
                                           {"id": "gone", "name": "NoImage", "frame": "f", "texts": {}}]}
    cf = {"back": _img(tmp_path, "b.png", (10, 14)), "front:1st Card": _img(tmp_path, "c1.png", (10, 14)),
          "front:1st card": _img(tmp_path, "c2.png", (10, 14)), "art:1st Card": _img(tmp_path, "a1.png", (8, 8))}
    out = _export(Source(asset="9 Deck", cards=cards, cards_files=cf))
    sw = out["Sources/_9_Deck_cards.swift"].decode()
    assert 'CardEntry("1st Card", "Dragon \\"Roar\\" \\\\(x) $5\\n"' in sw and "enum A9DeckCards" in sw and "gone" not in sw
    assert "static let back: String? =" in sw
    _balanced(sw)
    _check_identifiers(sw)
    for path in re.findall(r'"(cards/[^"]+\.png)"', sw):
        assert f"{RES}/{path}" in out, path
    assert json.loads(out[f"{RES}/cards/_9_Deck/_9_Deck_cards.json"])["cards"][0]["id"] == "1st Card"
    assert f"{RES}/cards/_9_Deck/card_back.png" in out and f"{RES}/cards/_9_Deck/art__1st_Card.png" in out


@pytest.mark.asyncio
async def test_ui_screen_view(tmp_path: Path) -> None:
    env, steps = await _screen_steps(tmp_path)
    out = export_engine(steps["approve"], list(steps.values()), env.blobs, tmp_path / "exp", "Pause Menu", "ios")
    root = Path(out["dir"])
    rels = {Path(f).relative_to(root).as_posix() for f in out["files"]}
    sw = (root / "Sources/Pause_Menu_screen.swift").read_text(encoding="utf-8")
    assert "struct PauseMenuScreen: View" in sw and "StudioButton(" in sw and 'texts["Title"]' in sw
    assert "NineSliceSpec(" in sw and "private static let s0" in sw and "Self.s0" in sw
    _balanced(sw)
    _check_identifiers(sw)
    for path in re.findall(r'"(screens/[^"]+\.png)"', sw):
        assert f"{RES}/{path}" in rels, path
    assert FX_FILE in rels and "AssetStudio.xcassets/button_01.imageset/Contents.json" in rels
    if base := os.environ.get("FXI_OUT"):
        for rel in rels:
            dst = Path(base) / "Pause_Menu" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((root / rel).read_bytes())


def test_screen_wide_levels_are_grouped_and_hostile_names_escaped(tmp_path: Path) -> None:
    assert len(_grouped([f"V{i}" for i in range(25)], "")) <= MAX_VIEWS
    assert _grouped(["a", "b"], "") == ["a", "b"]
    nodes = [{"name": f'L"{i}\\', "type": "label", "rect": [0, 0.1, 1, 0.1], "text": f"t\n{i}\"", "color": "#zz"}
             for i in range(23)]
    doc = {"screen": "Evil\n// x*/ name", "ref": [100, 200], "nodes": nodes, "sprites": {}, "background": "gone"}
    f = _export(Source(asset="Hud", ui_screen=doc, ui=[("btn", _img(tmp_path, "b.png", (8, 8)), (1, 1, 1, 1), "")]))
    sw = f["Sources/Hud_screen.swift"].decode()
    _balanced(sw)
    lines = sw.splitlines()
    doc_at = next(i for i, ln in enumerate(lines) if ln.startswith("/// Screen"))
    assert lines[doc_at + 1].startswith("struct HudScreen") and sw.count("Group {") >= 2
    assert 'color: Color(studioHex: 0xFFFFFF)' in sw and 'texts["L\\"0\\\\"]' in sw
    assert "background:" not in sw and "refWidth: 100.0" in sw


def test_sw_str_escapes() -> None:
    assert sw_str('a"b\\c$d\ne\tf\x01') == '"a\\"b\\\\c$d\\ne\\tf\\u{1}"'
    assert sw_str("\\(x)") == '"\\\\(x)"'


def test_readme_apis_exist_and_usage_sample(tmp_path: Path) -> None:
    mm = {"size": [8, 8], "pois": []}
    srcs = [_tile_anim_src(tmp_path, True), _autotile_src(tmp_path), _vfx_src(tmp_path),
            Source(asset="World", minimap=mm, minimap_files={"map": _img(tmp_path, "m.png", (8, 8))}),
            Source(asset="Deck", cards={"cards": [{"id": "a"}]}, cards_files={"front:a": _img(tmp_path, "f.png", (4, 4))})]
    rt, readme, extra = "", "", ""
    for s in srcs:
        f = _export(s)
        rt = f[FX_FILE].decode()
        readme += f["README_IOS.md"].decode()
        extra += "".join(v.decode() for k, v in f.items() if k.startswith("Sources/") and k != FX_FILE)
    declared = _declared(rt) | _declared(extra)
    notes = "\n".join(ln for ln in readme.splitlines() if ln.startswith(("- Resources/AssetStudioFx/", "- Sources/")))
    called = set(re.findall(r"\b([A-Z][A-Za-z0-9]+)(?=[(.])", notes))
    assert {"TileAnimation", "TileAnimationImage", "AutotileSet", "AutotileMap", "TileMap", "TileMapView", "VfxLibrary",
            "VfxView", "MinimapView", "CardFace"} <= called
    for name in called - {"CGPoint", "Circle", "Xcode", "Create", "AssetStudioFx"}:
        assert name in declared, name
    for name in PUBLIC_API:
        assert name in declared and name in USAGE, name
    assert "loadWeather" in rt and "loadWeather" in USAGE
    _balanced(USAGE)
    if base := os.environ.get("FXI_OUT"):
        (Path(base) / "Usage.swift").write_text(USAGE, encoding="utf-8")


def test_no_fx_kind_output_unchanged(tmp_path: Path) -> None:
    p = tmp_path / "c.png"
    Image.new("RGBA", (8, 8), (200, 40, 40, 255)).save(p)
    f = _export(Source(asset="kit", ui=[("coin", p, (0, 0, 0, 0), "")]))
    assert not any(k.startswith(("Resources/", "Sources/AssetStudioFx")) for k in f)
    assert "AssetStudioFx" not in f["README_IOS.md"].decode()
    assert ios.export(Source(asset="x", tile_anim={"frames": 2}, tile_anim_sheet=None)) == []
    assert ios.export(Source(asset="x", vfx={"presets": []}, vfx_files={})) == []


def test_missing_kind_files_skip(tmp_path: Path) -> None:
    src = Source(asset="Mua 9", weather={"layers": [{"kind": "particles", "name": "n", "emitter": None}], "area": [10, 10]},
                 weather_files={"x": _img(tmp_path, "w.png", (4, 4))})
    assert ios.export(src) == []
    cards = Source(asset="d", cards={"cards": [{"id": "a"}]}, cards_files={"back": _img(tmp_path, "b.png", (4, 4))})
    assert ios.export(cards) == []


def test_pack_merge_fx_only_and_mixed(tmp_path: Path) -> None:
    c = tmp_path / "c.png"
    Image.new("RGBA", (8, 8), (200, 40, 40, 255)).save(c)
    warnings: list[str] = []
    pack = IosPack(tmp_path / "pack", "Pack", warnings)
    srcs = [_autotile_src(tmp_path, "9 Grass"), _vfx_src(tmp_path, "Fx"), Source(asset="kit", ui=[("coin", c, (0, 0, 0, 0), "")])]
    for i, src in enumerate(srcs):
        d = tmp_path / f"s{i}"
        for n, data in ios.export(src):
            dst = d / n
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data if isinstance(data, bytes) else data.read_bytes())
        pack.add(f"s{i}", d)
    pack.finish()
    root = tmp_path / "pack"
    assert warnings == []
    assert (root / FX_FILE).is_file() and (root / f"{RES}/tiles/_9_Grass_autotile.json").is_file()
    assert (root / f"{RES}/vfx/Fx/Fx_vfx.json").is_file()
    assert 'static let coin = "coin"' in (root / "Sources/AssetStudioAssets.swift").read_text(encoding="utf-8")
    readme = (root / "README_IOS.md").read_text(encoding="utf-8")
    assert "AssetStudioFx" in readme and "AutotileSet.load" in readme and "VfxLibrary.load" in readme

    only = IosPack(tmp_path / "pack2", "P2", [])
    only.add("s0", tmp_path / "s0")
    assert only.finish() == [] and not (tmp_path / "pack2/Sources/AssetStudioAssets.swift").exists()
    assert (tmp_path / "pack2" / FX_FILE).is_file()
