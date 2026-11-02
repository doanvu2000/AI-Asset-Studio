import asyncio
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from studio_core.adapters.ui_layout import ClaudeUiLayoutExecutor
from studio_core.contracts import RunStatus
from studio_core.export import flutter
from studio_core.export.flutter_fx import pascal
from studio_core.export.source import Source
from studio_core.ops import autotile as at
from studio_core.ops import ui_screen as us
from studio_core.ops import vfx_particles as vp
from studio_core.ops import weather as wx
from studio_core.ops.ui import SLICES_TYPE, make_states, nine_slice_borders
from studio_core.store.bundle import export_engine
from studio_core.store.pack_mobile import FlutterPack
from test_engine_runner import Env
from test_ops_executors import FakeCtx
from test_r8_weather import SMALL
from test_ui_kit import button
from test_ui_screen import RECIPES, FakeQuery, _png


def _img(tmp: Path, name: str, size: tuple[int, int], rgba=(90, 160, 80, 255)) -> Path:
    p = tmp / name
    Image.new("RGBA", size, rgba).save(p)
    return p


def _dump(name: str, files: list) -> dict[str, bytes]:
    """FLV_OUT set: also write the generated tree under <FLV_OUT>/<name> so the Flutter test project can use it."""
    out = {n: (d if isinstance(d, bytes) else d.read_bytes()) for n, d in files}
    if root := os.environ.get("FLV_OUT"):
        for n, data in out.items():
            dst = Path(root) / name / n
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
    return out


def _autotile_src(tmp: Path, asset: str = "Grass Shore") -> Source:
    t = 16
    doc = {"type": at.AUTOTILE_TYPE, "tile_size": t, "grid": [8, 6], "terrains": ["grass", "dirt"], "pixel": False,
           "tiles": at.layout(("grass", "dirt"))}
    return Source(asset=asset, autotile=doc, autotile_atlas=_img(tmp, "atlas.png", (8 * t, 6 * t)))


def _tile_anim_src(tmp: Path, blob: bool, asset: str = "Water") -> Source:
    t, n = 16, 4
    if blob:
        doc: dict[str, Any] = {"mode": "blob47", "frames": n, "fps": 8.0, "tile_size": t, "pixel": False,
                               "layout": {"cols": 8, "rows": 6 * n, "frame_offset": [0, 6]},
                               "autotile": {"terrains": ["grass", "water"], "grid": [8, 6],
                                            "tiles": at.layout(("grass", "water"))}}
        sheet = _img(tmp, "sheet.png", (8 * t, 6 * n * t))
    else:
        doc = {"mode": "single", "frames": n, "fps": 8.0, "tile_size": t, "pixel": False,
               "layout": {"cols": n, "rows": 1, "frame_offset": [1, 0]}}
        sheet = _img(tmp, "sheet.png", (n * t, t))
    return Source(asset=asset, tile_anim=doc, tile_anim_sheet=sheet)


def _vfx_src(tmp: Path, asset: str = "Fx Pack") -> Source:
    tex = vp.procedural(64)
    files = {}
    for k, im in tex.items():
        files[k] = tmp / f"{k}.png"
        im.save(files[k])
    presets = vp.build_presets(["explosion", "heal", "projectile_trail"], None, [], False)
    doc = {"texture_size": 64, "presets": presets,
           "textures": {k: {"cols": vp.SHEETS.get(k, (1, 1))[0], "rows": vp.SHEETS.get(k, (1, 1))[1]} for k in files}}
    return Source(asset=asset, vfx=doc, vfx_files=files)


async def _weather_src(tmp: Path) -> Source:
    ctx = FakeCtx(tmp)
    res = await wx.WeatherOverlay().run(ctx, {**SMALL, "asset": "Sky Fx"}, {})
    doc = json.loads(ctx.blobs.path(res.outputs["weather"][0].hash).read_bytes())
    files = {k: ctx.blobs.path(t["hash"]) for k, t in doc["textures"].items()}
    return Source(asset="Sky Fx", weather=doc, weather_files=files)


def test_tile_anim_single_files_and_tmx(tmp_path: Path) -> None:
    files = _dump("tile_anim", flutter.export(_tile_anim_src(tmp_path, False)))
    assert {"assets/tiles/Water_tile_anim.png", "assets/tiles/Water_tile_anim.json", "assets/tiles/Water_tile_anim.tmx",
            "lib/gen/asset_studio_tiles.dart", "assets/tiles/Water_tile_anim.tmx"} <= set(files)
    tmx = ET.fromstring(files["assets/tiles/Water_tile_anim.tmx"])
    ts = tmx.find("tileset")
    assert ts is not None and ts.get("columns") == "4" and ts.get("tilecount") == "4"
    frames = [(f.get("tileid"), f.get("duration")) for f in tmx.iter("frame")]
    assert frames == [("0", "125"), ("1", "125"), ("2", "125"), ("3", "125")]
    csv = tmx.find("layer/data").text.replace("\n", "").strip(",")
    assert set(csv.split(",")) == {"1"} and len(csv.split(",")) == 8 * 5
    assert json.loads(files["assets/tiles/Water_tile_anim.json"])["sheet"] == "Water_tile_anim.png"
    assert "flame_tiled" in files["pubspec_assets.yaml"].decode()
    assert "loadTiledMap" in files["lib/gen/asset_studio_flame.dart"].decode()
    assert "Future<SpriteAnimation> loadTileAnimation" in files["lib/gen/asset_studio_tiles.dart"].decode()


def test_tile_anim_blob_tmx_animates_every_base_tile(tmp_path: Path) -> None:
    files = _dump("tile_anim_blob", flutter.export(_tile_anim_src(tmp_path, True, "Lake")))
    tmx = ET.fromstring(files["assets/tiles/Lake_tile_anim.tmx"])
    ts = tmx.find("tileset")
    assert ts.get("columns") == "8" and ts.get("tilecount") == str(8 * 24)
    anims = ts.findall("tile")
    assert len(anims) == 48
    first = anims[0].findall("animation/frame")
    assert [f.get("tileid") for f in first] == [str(int(anims[0].get("id")) + k * 48) for k in range(4)]
    assert len(ts.findall("wangsets/wangset/wangtile")) == 48
    gids = [int(g) for g in tmx.find("layer/data").text.replace("\n", "").strip(",").split(",")]
    assert len(gids) == 12 * 8 and max(gids) <= 48


def test_autotile_files_tmx_and_wang(tmp_path: Path) -> None:
    files = _dump("autotile", flutter.export(_autotile_src(tmp_path)))
    a = "assets/tiles/Grass_Shore_autotile"
    assert {f"{a}.png", f"{a}.json", f"{a}.tmx"} <= set(files)
    doc = json.loads(files[f"{a}.json"])
    assert doc["atlas"] == "Grass_Shore_autotile.png" and len(doc["tiles"]) == 48
    tmx = ET.fromstring(files[f"{a}.tmx"])
    wang = tmx.findall("tileset/wangsets/wangset/wangtile")
    assert len(wang) == 48
    full = next(e for e in doc["tiles"] if e["mask"] == 255)
    assert next(w for w in wang if w.get("tileid") == str(full["row"] * 8 + full["col"])).get("wangid") == "1,1,1,1,1,1,1,1"
    fill = next(e for e in doc["tiles"] if e["mask"] is None)
    assert next(w for w in wang if w.get("tileid") == str(fill["row"] * 8 + fill["col"])).get("wangid") == "2,2,2,2,2,2,2,2"
    dart = files["lib/gen/asset_studio_tiles.dart"].decode()
    assert "class AutotileSet" in dart and "class AutotileComponent" in dart


def test_vfx_files_and_doc(tmp_path: Path) -> None:
    files = _dump("vfx", flutter.export(_vfx_src(tmp_path)))
    base = "assets/vfx/Fx_Pack"
    doc = json.loads(files[f"{base}/Fx_Pack_vfx.json"])
    assert doc["ppu"] == 100.0 and [p["name"] for p in doc["presets"]] == ["explosion", "heal", "projectile_trail"]
    assert doc["textures"]["smoke"] == {"file": "Fx_Pack_smoke.png", "cols": 2, "rows": 2}
    assert all("trail" not in p for p in doc["presets"])
    assert f"{base}/Fx_Pack_spark.png" in files and "class VfxComponent" in files["lib/gen/asset_studio_vfx.dart"].decode()
    assert f"    - {base}/\n" in files["pubspec_assets.yaml"].decode()


async def test_weather_files_doc(tmp_path: Path) -> None:
    files = _dump("weather", flutter.export(await _weather_src(tmp_path)))
    base = "assets/weather/Sky_Fx"
    doc = json.loads(files[f"{base}/Sky_Fx_weather.json"])
    assert doc["ppu"] == 1 and [m["name"] for m in doc["mists"]] == ["mist"]
    assert {p["name"] for p in doc["presets"]} == {"rain_light", "snow", "leaves", "fireflies"}
    assert not any(k.endswith("_sheet") for k in doc["textures"])
    assert all((Path(base) / v["file"]).as_posix() in files for v in doc["textures"].values())
    rain = next(p for p in doc["presets"] if p["name"] == "rain_light")["emitters"][0]
    assert rain["box"] and rain["direction"] and rain["rate"] > 0


def test_minimap_and_cards(tmp_path: Path) -> None:
    mm = {"size": [64, 64], "pois": [{"name": "Town", "type": "town", "px": [16, 32]}]}
    src = Source(asset="World", minimap=mm, minimap_files={"map": _img(tmp_path, "m.png", (64, 64)),
                                                           "framed": _img(tmp_path, "f.png", (64, 64))})
    files = _dump("minimap", flutter.export(src))
    dart = files["lib/gen/minimap/world_minimap.dart"].decode()
    assert "MinimapPoi('town', 0.2500, 0.5000)," in dart and "class WorldMinimap" in dart
    assert "assets/minimap/World/World_minimap.png" in files
    cards = {"size": [100, 140], "cards": [{"id": "1st Card", "name": "Dragon's Roar", "frame": "f", "texts": {}},
                                           {"id": "1st card", "name": "Twin", "frame": "f", "texts": {}}]}
    cf = {"back": _img(tmp_path, "b.png", (10, 14)), "front:1st Card": _img(tmp_path, "c1.png", (10, 14)),
          "front:1st card": _img(tmp_path, "c2.png", (10, 14)), "art:1st Card": _img(tmp_path, "a1.png", (8, 8))}
    out = _dump("cards", flutter.export(Source(asset="Deck", cards=cards, cards_files=cf)))
    assert {"assets/cards/Deck/_1st_Card.png", "assets/cards/Deck/_1st_card_2.png", "assets/cards/Deck/card_back.png",
            "assets/cards/Deck/art__1st_Card.png"} <= set(out)
    d = out["lib/gen/cards/deck_cards.dart"].decode()
    assert "CardEntry('1st Card', 'Dragon\\'s Roar'" in d and "class DeckCards" in d


def test_pascal_names() -> None:
    assert pascal("Hero_Knight") == "HeroKnight" and pascal("9lives") == "A9lives"
    assert pascal("") == "Asset"


def test_edge_cases_missing_metadata(tmp_path: Path) -> None:
    assert flutter.export(Source(asset="x", tile_anim={"frames": 2}, tile_anim_sheet=None)) == []
    assert flutter.export(Source(asset="x", vfx={"presets": []}, vfx_files={})) == []
    src = Source(asset="Mưa 9", weather={"layers": [{"kind": "particles", "name": "n", "emitter": None}], "area": [10, 10]},
                 weather_files={"x": _img(tmp_path, "w.png", (4, 4))})
    assert flutter.export(src) == []


async def _screen_steps(tmp_path: Path):
    reply = us.template("pause")
    reply["nodes"].append({"name": "Sound", "parent": "Window", "type": "bar", "rect": [0.1, 0.9, 0.8, 0.05],
                           "value": 0.5})
    q = FakeQuery({**reply, "reason": "pause"})
    env = Env(tmp_path, [], {"ui-screen": (RECIPES / "ui-screen.yaml").read_text(encoding="utf-8")})
    env.registry.register(ClaudeUiLayoutExecutor(env.settings, q))
    for cls in us.UI_SCREEN_OPS:
        env.registry.register(cls())
    doc: dict[str, Any] = {"type": SLICES_TYPE, "elements": []}
    for name, img in (("button_01", button(160, 56)), ("panel_01", button(200, 200, gem=False))):
        border, _ = nine_slice_borders(img)
        h = await _png(env, img, f"{name}.png")
        doc["elements"].append({"name": name, "border": list(border), "hash": h, "states": []})
        if name == "button_01":
            for st, simg in make_states(img).items():
                doc["elements"].append({"name": f"{name}_{st}", "state": st, "state_of": name,
                                        "border": list(border), "hash": await _png(env, simg, f"{name}_{st}.png")})
    kit = await env.blobs.put_bytes(json.dumps(doc).encode(), name="ui_slices.json", mime="application/json")
    run_id = await env.engine.start_run("ui-screen", {"screen": "pause", "kit": kit.hash, "planner": "template",
                                                      "title": "Paused", "review": False})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    return env, steps


@pytest.mark.asyncio
async def test_ui_screen_exports_a_widget(tmp_path: Path) -> None:
    env, steps = await _screen_steps(tmp_path)
    out = export_engine(steps["approve"], list(steps.values()), env.blobs, tmp_path / "exp", "Pause Menu", "flutter")
    root = Path(out["dir"])
    rels = {Path(f).relative_to(root).as_posix() for f in out["files"]}
    assert {"lib/gen/screens/pause_menu_screen.dart", "lib/gen/asset_studio_ui.dart",
            "assets/screens/Pause_Menu/button_01.png", "assets/screens/Pause_Menu/panel_01.png"} <= rels
    dart = (root / "lib/gen/screens/pause_menu_screen.dart").read_text(encoding="utf-8")
    assert "class PauseMenuScreen extends StatelessWidget" in dart and "NineSliceSpec('assets/screens/Pause_Menu/" in dart
    assert "_Button(" in dart and "onTap!('" in dart and "texts['Title']" in dart
    assert "assets/ui/" not in dart
    assert dart.count("(") == dart.count(")") and dart.count("[") == dart.count("]") and dart.count("{") == dart.count("}")
    if base := os.environ.get("FLV_OUT"):
        for rel in rels:
            dst = Path(base) / "screen" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((root / rel).read_bytes())


def test_pack_merges_shared_runtime_and_keeps_namespaces(tmp_path: Path) -> None:
    pack = FlutterPack(tmp_path / "pack", "Pack", [])
    warnings: list[str] = pack.tree.warnings
    for i, src in enumerate((_autotile_src(tmp_path, "A One"), _autotile_src(tmp_path, "A Two"),
                             _tile_anim_src(tmp_path, False, "Wet"), _vfx_src(tmp_path, "Fx"))):
        d = tmp_path / f"s{i}"
        for n, data in flutter.export(src):
            dst = d / n
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data if isinstance(data, bytes) else data.read_bytes())
        pack.add(f"s{i}", d)
    pack.finish()
    root = tmp_path / "pack"
    assert (root / "lib/gen/asset_studio_tiles.dart").is_file() and (root / "lib/gen/asset_studio_vfx.dart").is_file()
    assert (root / "assets/tiles/A_One_autotile.tmx").is_file() and (root / "assets/tiles/A_Two_autotile.tmx").is_file()
    assert warnings == []
    yaml = (root / "pubspec_assets.yaml").read_text(encoding="utf-8")
    assert "flame_tiled" in yaml and "    - assets/vfx/Fx/\n" in yaml
    assert re.search(r"loadTiledMap", (root / "lib/gen/asset_studio_flame.dart").read_text(encoding="utf-8"))
