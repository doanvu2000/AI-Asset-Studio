import json
import os
import re
from pathlib import Path

import pytest
from PIL import Image

from studio_core.export import android
from studio_core.export.android_fx import kt_str
from studio_core.export.android_fx_kotlin import FX_FILE
from studio_core.export.source import Source
from studio_core.store.bundle import export_engine
from studio_core.store.pack_mobile import AndroidPack
from test_export_flutter_fx import _autotile_src, _img, _screen_steps, _tile_anim_src, _vfx_src, _weather_src

PKG = "com.acme.fx"


def _export(src: Source) -> dict[str, bytes]:
    out = {n: (d if isinstance(d, bytes) else d.read_bytes()) for n, d in android.export(src, PKG)}
    if root := os.environ.get("FXA_OUT"):  # lets the Compose test project compile the generated tree
        for n, data in out.items():
            dst = Path(root) / src.asset.replace(" ", "_") / n
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
    return out


def _balanced(kt: str) -> None:
    for a, b in ("()", "[]", "{}"):
        assert kt.count(a) == kt.count(b), (a, kt)


def test_runtime_is_shared_and_package_substituted(tmp_path: Path) -> None:
    f = _export(_autotile_src(tmp_path))
    rt = f[FX_FILE].decode()
    assert rt.startswith(f"package {PKG}\n") and "@PKG@" not in rt
    for sym in ("class AutotileSet", "fun AutotileMap", "fun loadTileAnimation", "class VfxSimulation", "fun VfxView",
                "fun WeatherView", "fun MistView", "fun MinimapView", "fun CardFace", "fun StudioScreen"):
        assert sym in rt
    assert f"import {PKG}.R" not in rt
    assert "kotlin/AssetStudioAssets.kt" not in f and not any(k.startswith("res/") for k in f)


def test_tile_anim_single(tmp_path: Path) -> None:
    f = _export(_tile_anim_src(tmp_path, False))
    assert {"assets/tiles/Water_tile_anim.png", "assets/tiles/Water_tile_anim.json",
            "assets/tiles/Water_tile_anim_map.json", FX_FILE, "README_ANDROID.md"} <= set(f)
    doc = json.loads(f["assets/tiles/Water_tile_anim.json"])
    assert doc["sheet"] == "Water_tile_anim.png" and doc["frames"] == 4
    m = json.loads(f["assets/tiles/Water_tile_anim_map.json"])
    assert m["image"] == "Water_tile_anim.png" and (m["width"], m["height"]) == (8, 5)
    assert m["animation"] == {"frames": 4, "fps": 8.0, "step": 1}
    assert len(m["tiles"]) == 5 and all(len(r) == 8 and set(r) == {0} for r in m["tiles"])
    assert "assets/tiles/" + m["image"] in f


def test_tile_anim_blob_map_ids_in_sheet(tmp_path: Path) -> None:
    f = _export(_tile_anim_src(tmp_path, True, "Lake"))
    m = json.loads(f["assets/tiles/Lake_tile_anim_map.json"])
    assert m["animation"]["step"] == 6 * 8 and m["cols"] == 8 and m["rows"] == 24
    top = max(max(r) for r in m["tiles"]) + 3 * m["animation"]["step"]
    assert top < m["cols"] * m["rows"]
    assert len(m["tiles"]) == 8 and len(m["tiles"][0]) == 12


def test_autotile_json_and_map(tmp_path: Path) -> None:
    f = _export(_autotile_src(tmp_path))
    doc = json.loads(f["assets/tiles/Grass_Shore_autotile.json"])
    assert doc["atlas"] == "Grass_Shore_autotile.png" and len(doc["tiles"]) == 48
    assert sum(1 for e in doc["tiles"] if e["mask"] is None) == 1
    m = json.loads(f["assets/tiles/Grass_Shore_autotile_map.json"])
    assert m["animation"] is None and m["image"] == doc["atlas"] and "assets/tiles/" + m["image"] in f
    assert max(max(r) for r in m["tiles"]) < 8 * 6


def test_vfx_files_and_doc(tmp_path: Path) -> None:
    f = _export(_vfx_src(tmp_path))
    base = "assets/vfx/Fx_Pack"
    doc = json.loads(f[f"{base}/Fx_Pack_vfx.json"])
    assert doc["ppu"] == 100.0 and [p["name"] for p in doc["presets"]] == ["explosion", "heal", "projectile_trail"]
    assert all("trail" not in p for p in doc["presets"])
    assert all(f"{base}/{t['file']}" in f for t in doc["textures"].values())
    assert {"name", "texture", "blend", "delay", "burst", "rate", "lifetime", "speed", "shape", "size_curve",
            "colors"} <= set(doc["presets"][0]["emitters"][0])


@pytest.mark.asyncio
async def test_weather_files_doc(tmp_path: Path) -> None:
    f = _export(await _weather_src(tmp_path))
    base = "assets/weather/Sky_Fx"
    doc = json.loads(f[f"{base}/Sky_Fx_weather.json"])
    assert [m["name"] for m in doc["mists"]] == ["mist"] and doc["area"]
    assert all(f"{base}/{t['file']}" in f for t in doc["textures"].values())
    rain = next(p for p in doc["presets"] if p["name"] == "rain_light")["emitters"][0]
    assert rain["box"] and rain["direction"] and rain["rate"] > 0


def test_minimap_and_cards_kotlin(tmp_path: Path) -> None:
    mm = {"size": [64, 64], "pois": [{"name": "Town", "type": "town", "px": [16, 32]}]}
    f = _export(Source(asset="World", minimap=mm, minimap_files={"map": _img(tmp_path, "m.png", (64, 64))}))
    kt = f["kotlin/World_minimap.kt"].decode()
    assert f"package {PKG}" in kt and 'MinimapPoi("town", 0.2500f, 0.5000f)' in kt
    assert "assets/" + re.search(r'ASSET = "([^"]+)"', kt)[1] in f
    _balanced(kt)
    cards = {"size": [100, 140], "cards": [{"id": "1st Card", "name": 'Dragon "Roar" $5', "frame": "f", "texts": {}},
                                           {"id": "1st card", "name": "Twin", "frame": "f", "texts": {}},
                                           {"id": "gone", "name": "NoImage", "frame": "f", "texts": {}}]}
    cf = {"back": _img(tmp_path, "b.png", (10, 14)), "front:1st Card": _img(tmp_path, "c1.png", (10, 14)),
          "front:1st card": _img(tmp_path, "c2.png", (10, 14)), "art:1st Card": _img(tmp_path, "a1.png", (8, 8))}
    out = _export(Source(asset="9 Deck", cards=cards, cards_files=cf))
    kt = out["kotlin/_9_Deck_cards.kt"].decode()
    assert 'CardEntry("1st Card", "Dragon \\"Roar\\" \\$5"' in kt and "object A9DeckCards" in kt and "gone" not in kt
    _balanced(kt)
    for path in re.findall(r'"(cards/[^"]+\.png)"', kt):
        assert "assets/" + path in out, path
    assert json.loads(out["assets/cards/_9_Deck/_9_Deck_cards.json"])["cards"][0]["id"] == "1st Card"


@pytest.mark.asyncio
async def test_ui_screen_composable(tmp_path: Path) -> None:
    env, steps = await _screen_steps(tmp_path)
    out = export_engine(steps["approve"], list(steps.values()), env.blobs, tmp_path / "exp", "Pause Menu", "android")
    root = Path(out["dir"])
    rels = {Path(f).relative_to(root).as_posix() for f in out["files"]}
    kt = (root / "kotlin/Pause_Menu_screen.kt").read_text(encoding="utf-8")
    assert "fun PauseMenuScreen(" in kt and "StudioButton(" in kt and 'texts["Title"]' in kt and "NineSliceSpec(" in kt
    _balanced(kt)
    for path in re.findall(r'"(screens/[^"]+\.png)"', kt):
        assert "assets/" + path in rels, path
    assert FX_FILE in rels and "res/drawable-xxxhdpi/button_01.9.png" in rels
    if base := os.environ.get("FXA_OUT"):
        for rel in rels:
            dst = Path(base) / "Pause_Menu" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((root / rel).read_bytes())


def test_kt_str_escapes() -> None:
    assert kt_str('a"b\\c$d\ne') == '"a\\"b\\\\c\\$d\\ne"'


def test_no_fx_kind_output_unchanged(tmp_path: Path) -> None:
    p = tmp_path / "c.png"
    Image.new("RGBA", (8, 8), (200, 40, 40, 255)).save(p)
    f = _export(Source(asset="kit", ui=[("coin", p, (0, 0, 0, 0), "")]))
    assert set(f) == {*(f"res/drawable-{d}/coin.webp" for d, _ in android.DENSITIES), "kotlin/AssetStudioAssets.kt",
                      "README_ANDROID.md"}
    readme = f["README_ANDROID.md"].decode()
    assert "fx " not in readme and "AssetStudioFx" not in readme
    assert android.export(Source(asset="x", tile_anim={"frames": 2}, tile_anim_sheet=None), PKG) == []
    assert android.export(Source(asset="x", vfx={"presets": []}, vfx_files={}), PKG) == []


def test_missing_kind_files_skip(tmp_path: Path) -> None:
    src = Source(asset="Mua 9", weather={"layers": [{"kind": "particles", "name": "n", "emitter": None}], "area": [10, 10]},
                 weather_files={"x": _img(tmp_path, "w.png", (4, 4))})
    assert android.export(src, PKG) == []
    cards = Source(asset="d", cards={"cards": [{"id": "a"}]}, cards_files={"back": _img(tmp_path, "b.png", (4, 4))})
    assert android.export(cards, PKG) == []


def test_pack_merge_fx_only_and_mixed(tmp_path: Path) -> None:
    c = tmp_path / "c.png"
    Image.new("RGBA", (8, 8), (200, 40, 40, 255)).save(c)
    pack = AndroidPack(tmp_path / "pack", "Pack", PKG, [])
    srcs = [_autotile_src(tmp_path, "9 Grass"), _vfx_src(tmp_path, "Fx"),
            Source(asset="kit", ui=[("coin", c, (0, 0, 0, 0), "")])]
    for i, src in enumerate(srcs):
        d = tmp_path / f"s{i}"
        for n, data in android.export(src, PKG):
            dst = d / n
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data if isinstance(data, bytes) else data.read_bytes())
        pack.add(f"s{i}", d)
    pack.finish()
    root = tmp_path / "pack"
    assert pack.tree.warnings == []
    assert (root / FX_FILE).is_file() and (root / "assets/tiles/_9_Grass_autotile.json").is_file()
    assert "s2_coin" in (root / "kotlin/AssetStudioAssets.kt").read_text(encoding="utf-8")
    readme = (root / "README_ANDROID.md").read_text(encoding="utf-8")
    assert "fx autotile" in readme and "fx vfx" in readme

    only = AndroidPack(tmp_path / "pack2", "P2", PKG, [])
    only.add("s0", tmp_path / "s0")
    assert only.finish() == [] and not (tmp_path / "pack2/kotlin/AssetStudioAssets.kt").exists()
