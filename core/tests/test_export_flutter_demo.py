import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from test_ops_executors import FakeCtx
from test_playable_scene import build

from studio_core.export import flutter, flutter_demo as demo
from studio_core.export.flutter_shaders import frag_files
from studio_core.export.playable_scene import scene_files, source_from
from studio_core.store.pack_mobile import FlutterPack

SPR = "assets/images/sprites/"
TMX = """<?xml version="1.0" encoding="UTF-8"?>
<map version="1.10" orientation="orthogonal" width="6" height="4" tilewidth="16" tileheight="16">
 <properties><property name="style" value="{style}" /></properties>
 <tileset firstgid="1" name="t" tilewidth="16" tileheight="16" tilecount="4" columns="2">
  <image source="t_tiles.png" width="32" height="32" />
  <tile id="1"><properties><property name="solid" type="bool" value="true" /></properties></tile>
 </tileset>
 <layer id="1" name="Ground" width="6" height="4"><data encoding="csv">
1,1,1,1,1,1,
1,2,1,1,1,1,
1,1,1,1,1,1,
1,1,1,1,1,1
</data></layer>
 <layer id="2" name="Solid" width="6" height="4"><properties><property name="collision" type="bool" value="true" /></properties>
<data encoding="csv">
0,0,0,0,0,3,
0,0,0,0,0,0,
0,0,0,0,0,0,
0,0,0,0,0,0
</data></layer>
 <objectgroup id="3" name="Objects">
  <object id="1" name="Hero" type="player" x="40" y="48"><point /></object>
  <object id="2" name="Crate" type="prop" gid="5" x="60" y="40" width="20" height="30">
   <properties><property name="sprite" type="file" value="Props/Crate.png" /></properties></object>
  <object id="3" name="Ghost" type="prop" gid="5" x="1" y="1" width="20" height="30">
   <properties><property name="sprite" type="file" value="Props/Missing.png" /></properties></object>
 </objectgroup>
</map>
"""


def png(w: int, h: int, box: tuple[int, int, int, int] | None = None) -> bytes:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle(box or (0, 0, w - 1, h - 1), fill=(200, 80, 60, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def anim_json(frame: int, clips: dict[str, int], anchor_y: float = 0.9) -> bytes:
    out = []
    for name, n in clips.items():
        frames = [{"name": f"{name}_{i}", "x": i * frame, "y": 0, "w": frame, "h": frame} for i in range(n)]
        out.append({"name": name, "frames": frames, "stepTime": 0.1, "loop": True})
    return json.dumps({"clips": out, "frameSize": [frame, frame],
                       "pivot": {"x": frame / 2, "y": frame * anchor_y, "anchorX": 0.5, "anchorY": anchor_y}}).encode()


def sheet(name: str, frame: int, clips: dict[str, int], box: tuple[int, int, int, int] | None = None) -> list:
    n = max(clips.values())
    return [(f"{SPR}{name}.png", png(frame * n, frame, box)), (f"{SPR}{name}_anim.json", anim_json(frame, clips))]


def tree(style: str = "topdown", tmx: bool = True, parallax: bool = False) -> list:
    files = sheet("Anim_di_bo", 64, {"walk_E": 6}, (16, 8, 48, 60))
    files += sheet("Sprite_8_huong", 128, {f"idle_{d}": 1 for d in ("S", "SW", "W", "NW", "N", "NE", "E", "SE")},
                   (32, 16, 96, 120))
    files += sheet("Anim_tan_cong", 80, {"slash_E": 5})
    if tmx:
        files += [("assets/tiles/Map.tmx", TMX.format(style=style).encode()), ("assets/tiles/t_tiles.png", png(32, 32)),
                  ("assets/tiles/Props/Crate.png", png(20, 30))]
    if parallax:
        files += [("assets/images/parallax/Sky/0_sky.png", png(64, 32)),
                  ("assets/images/parallax/Sky/parallax.json", json.dumps({"size": [64, 32], "layers": []}).encode())]
    files.append(("assets/ui/button.png", png(8, 8)))
    return files


def cfg_of(files: list, title: str = "Demo") -> dict:
    return json.loads(dict(demo.demo_project(files, title))["assets/demo/demo.json"])


def test_clip_parts_normalises_states_and_directions():
    assert demo.clip_parts("walk_E") == ("walk", "E")
    assert demo.clip_parts("Slash_NW") == ("attack", "NW")
    assert demo.clip_parts("idle") == ("idle", "")
    assert demo.clip_parts("run_SE") == ("walk", "SE")
    assert demo.clip_parts("sprite_walk") == ("sprite_walk", "")


def test_config_reads_tmx_sheets_and_scale_metadata():
    cfg = cfg_of(tree(parallax=True))
    assert (cfg["tile"], cfg["cols"], cfg["rows"], cfg["style"], cfg["tmx"]) == (16, 6, 4, "topdown", "Map")
    assert cfg["spawn"] == [40.0, 48.0]
    assert cfg["solid"] == [[1, 1], [5, 0]], "tile property solid + collision layer cells, in cells not pixels"
    assert cfg["props"] == [{"sprite": "tiles/Props/Crate.png", "x": 70.0, "y": 40.0, "w": 20.0, "h": 30.0}], \
        "prop x is the centre, y the foot; props with a missing image are dropped"
    assert cfg["parallax"] == "Sky" and not cfg["lit"] and cfg["ground"] is None
    assert cfg["charHeight"] == round(1.7 * 16, 2) and cfg["speed"] == 4.5 * 16
    by = {s["name"]: s for s in cfg["sheets"]}
    assert set(by) == {"Anim_di_bo", "Anim_tan_cong", "Sprite_8_huong"}
    assert by["Anim_di_bo"]["height"] == 53 and by["Sprite_8_huong"]["height"] == 105, "visible alpha height"
    assert by["Sprite_8_huong"]["anchor"] == [0.5, 0.9] and by["Sprite_8_huong"]["frame"] == [128, 128]
    states = {(c["state"], c["dir"]) for s in cfg["sheets"] for c in s["clips"]}
    assert ("walk", "E") in states and ("attack", "E") in states and ("idle", "NW") in states


def test_sideview_gets_gravity_and_jump():
    cfg = cfg_of(tree("sideview"))
    assert cfg["style"] == "sideview" and cfg["gravity"] > 0 and cfg["jump"] > 0 and cfg["viewRows"] == 4


def test_without_tmx_falls_back_to_tileset_floor_or_grid():
    files = tree(tmx=False) + [("assets/tiles/Floor.png", png(32, 32))]
    cfg = cfg_of(files)
    assert cfg["tmx"] is None and cfg["ground"] == "tiles/Floor.png" and cfg["props"] == [] and cfg["solid"] == []
    assert cfg["spawn"] == [20 * 32, 12 * 32] and (cfg["cols"], cfg["rows"]) == (40, 24)
    assert cfg_of(tree(tmx=False))["ground"] is None


def test_no_character_sheet_means_no_demo():
    assert demo.demo_project([("assets/tiles/Map.tmx", TMX.format(style="topdown").encode())], "x") == []
    assert demo.demo_project([(f"{SPR}A_anim.json", anim_json(8, {"walk_E": 2}))], "x") == [], "sheet png missing"


def test_project_files_pubspec_and_lit_shader():
    files = dict(demo.demo_project(tree(parallax=True), "Level One"))
    for rel in ("pubspec.yaml", "lib/main.dart", "lib/game.dart", "lib/player.dart", "lib/world.dart",
                "lib/demo_config.dart", "lib/gen/asset_studio_flame.dart", "test/demo_test.dart", "README_DEMO.md"):
        assert rel in files, rel
    assert not any(r.startswith("assets/ui/") for r in files), "the UI kit is not needed by the demo"
    pub = files["pubspec.yaml"].decode()
    assert "flame: ^1.38.2" in pub and "flame_tiled: ^3.1.2" in pub and "name: asset_studio_demo" in pub
    assert "- assets/tiles/Props/" in pub and "- assets/demo/" in pub and "- assets/images/parallax/Sky/" in pub
    assert "shaders:" not in pub
    flame = files["lib/gen/asset_studio_flame.dart"].decode()
    assert "loadParallax" in flame and "loadTiledMap" in flame and "loadAsAnimations" in flame
    assert "asset_studio_ui" not in flame

    lit = tree() + [(f"{SPR}Sprite_8_huong_normal.png", png(1024, 128))] + [f for f in frag_files() if "lit_sprite" in f[0]]
    lit_files = dict(demo.demo_project(lit, "Lit"))
    assert "- shaders/asset_studio_lit_sprite.frag" in lit_files["pubspec.yaml"].decode()
    assert "shaders/asset_studio_lit_sprite.frag" in lit_files and json.loads(
        lit_files["assets/demo/demo.json"])["lit"] is True
    no_frag = cfg_of(tree() + [(f"{SPR}Sprite_8_huong_normal.png", png(1024, 128))])
    assert no_frag["lit"] is False, "a normal map without the lit shader stays unlit"


def test_dart_sources_are_plain_and_cover_the_inputs():
    from studio_core.export import flutter_demo_dart as d

    game, player, world = d.GAME, d.PLAYER, d.WORLD
    assert "JoystickComponent" in game and "FpsTextComponent" in game and "KeyboardEvents" in game
    assert "LogicalKeyboardKey.arrowRight" in game
    assert "LitSpriteEffect" in player and "LightSource" in player and "_mirror" in player
    assert "loadTiledMap" in world and "_blockers" in world and "Anchor.bottomCenter" in world
    assert all(s.count("{") == s.count("}") for s in (game, player, world, d.CONFIG, d.MAIN, d.TEST))
    for src in (game, player, world):
        assert src.count("//") + src.count("/*") <= 6, "few comments"


async def test_playable_scene_exports_a_demo_under_flutter(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    _, _, _, scene = await build(ctx)
    src = source_from(scene, scene_files(scene, ctx.blobs.path), scene["asset"], "t/1")
    files = dict(flutter.export(src))
    assert "assets/images/sprites/Hero_anim.json" in files and "assets/tiles/TestLevel.tmx" in files
    assert "demo/pubspec.yaml" in files and "demo/assets/images/sprites/Hero.png" in files
    cfg = json.loads(files["demo/assets/demo/demo.json"])
    assert cfg["tmx"] == "TestLevel" and len(cfg["props"]) == len(scene["props"]) > 0
    assert cfg["charHeight"] == round(1.7 * scene["tile"], 2)
    assert {c["dir"] for s in cfg["sheets"] for c in s["clips"]} == {"S", "SW", "W", "NW", "N", "NE", "E", "SE"}
    assert "demo/" in files["README_FLUTTER.md"].decode()
    assert not any(n.startswith("demo/") for n, _ in flutter.export(src, demo=False))


async def test_flutter_pack_demo_option(tmp_path: Path):
    (tmp_path / "blobs").mkdir()
    ctx = FakeCtx(tmp_path / "blobs")
    _, _, _, scene = await build(ctx)
    src = source_from(scene, scene_files(scene, ctx.blobs.path), scene["asset"], "t/1")
    staged = tmp_path / "staged"
    for rel, data in flutter.export(src):
        p = staged / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else Path(data).read_bytes())
    for flag in (False, True):
        root, warnings = tmp_path / f"pack{flag}", []
        pack = FlutterPack(root, "My Pack", warnings, demo=flag)
        got = pack.add("Level", staged)
        pack.finish()
        assert not any(g.startswith("demo/") for g in got), "a per-asset demo is not merged into the pack tree"
        assert (root / "demo" / "pubspec.yaml").is_file() is flag
        assert not (root / "demo" / "demo").exists() and not warnings
        assert ("demo/" in (root / "README_FLUTTER.md").read_text(encoding="utf-8")) is flag
    assert (tmp_path / "packTrue" / "demo" / "assets" / "images" / "sprites" / "Hero.png").is_file()
    assert (tmp_path / "packTrue" / "demo" / "lib" / "gen" / "asset_studio_flame.dart").is_file()


def test_pack_without_character_warns(tmp_path: Path):
    staged = tmp_path / "s"
    (staged / "assets/tiles").mkdir(parents=True)
    (staged / "assets/tiles/Map.tmx").write_bytes(TMX.format(style="topdown").encode())
    warnings: list[str] = []
    pack = FlutterPack(tmp_path / "p", "x", warnings, demo=True)
    pack.add("Map", staged)
    pack.finish()
    assert not (tmp_path / "p" / "demo").exists() and any("no character" in w for w in warnings)


@pytest.mark.skipif(not os.environ.get("ASSET_STUDIO_FLUTTER_TESTS") or shutil.which("flutter") is None,
                    reason="set ASSET_STUDIO_FLUTTER_TESTS=1 with flutter on PATH (needs pub.dev)")
def test_demo_project_passes_dart_analyze_and_flutter_test(tmp_path: Path):
    for rel, data in demo.demo_project(tree(parallax=True), "Smoke"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else Path(data).read_bytes())
    for cmd in (["flutter", "pub", "get"], ["dart", "analyze"], ["flutter", "test"]):
        r = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, shell=os.name == "nt", timeout=600)
        assert r.returncode == 0, f"{cmd}: {r.stdout[-2000:]}{r.stderr[-1000:]}"
