import base64
import io
import json
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import Lane, RunStatus, StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.minimap import (MinimapBase, MinimapFinish, clean_style, draw_icon, load_source, parse_pois)
from test_ops_executors import FakeCtx, load, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def level_image() -> Image.Image:
    """800x400 'screenshot': green land, blue lake, sand road, with JPEG-ish noise."""
    rng = np.random.default_rng(0)
    a = np.zeros((400, 800, 3), np.float32)
    a[:] = (110, 170, 90)
    a[60:200, 80:300] = (60, 120, 200)
    a[250:290, :] = (210, 185, 130)
    a += rng.normal(0, 9, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


TMX = """<?xml version="1.0" encoding="UTF-8"?>
<map version="1.10" orientation="orthogonal" width="4" height="2" tilewidth="16" tileheight="16" infinite="0">
 <tileset firstgid="1" source="t.tsx"/>
 <layer id="1" name="Ground" width="4" height="2"><data encoding="csv">1,1,1,1,1,1,1,1</data></layer>
 <layer id="2" name="Water" width="4" height="2"><data encoding="base64" compression="zlib">{b64}</data></layer>
 <objectgroup id="3" name="POI">
  <object id="1" name="Town" type="town" x="8" y="8"><point/></object>
  <object id="2" name="Chest" class="chest" x="48" y="16" width="16" height="16"/>
 </objectgroup>
</map>"""


def tmx_bytes() -> bytes:
    gids = np.array([0, 0, 2, 2, 0, 0, 2 | 0x80000000, 2], "<u4").tobytes()
    return TMX.format(b64=base64.b64encode(zlib.compress(gids)).decode()).encode()


LDTK = {
    "defs": {"layers": [{"uid": 1, "identifier": "Terrain", "type": "IntGrid",
                         "intGridValues": [{"value": 1, "identifier": "water", "color": "#2050C0"},
                                           {"value": 2, "identifier": "wall", "color": "#404040"}]}]},
    "levels": [{"identifier": "Level_0", "pxWid": 64, "pxHei": 32, "__bgColor": "#70A050", "layerInstances": [
        {"__identifier": "Entities", "__type": "Entities", "__gridSize": 16, "__cWid": 4, "__cHei": 2,
         "entityInstances": [{"__identifier": "Boss", "px": [40, 24], "width": 16, "height": 16, "__pivot": [0.5, 1],
                              "fieldInstances": [{"__identifier": "name", "__value": "Rồng"}]}]},
        {"__identifier": "Terrain", "__type": "IntGrid", "__gridSize": 16, "__cWid": 4, "__cHei": 2,
         "layerDefUid": 1, "intGridCsv": [1, 1, 0, 0, 0, 0, 2, 2]}]}],
}


def test_load_tiled_and_ldtk():
    t = load_source(tmx_bytes(), "map.tmx")
    assert t.source_size == (64, 32) and t.kind == "tilemap"
    px = np.asarray(t.image)
    assert px[4, 40, 2] > px[4, 4, 2] + 40
    assert [(p.name, p.type, p.x, p.y) for p in t.pois] == [("Town", "town", 8, 8), ("Chest", "chest", 56, 24)]
    tmj = {"type": "map", "width": 2, "height": 1, "tilewidth": 8, "tileheight": 8,
           "layers": [{"type": "tilelayer", "name": "sea", "data": [1, 0]},
                      {"type": "objectgroup", "objects": [{"name": "Shop", "type": "shop", "x": 12, "y": 4}]}]}
    j = load_source(json.dumps(tmj).encode(), "m.tmj")
    assert j.source_size == (16, 8) and j.pois[0].type == "shop"
    ld = load_source(json.dumps(LDTK).encode(), "w.ldtk")
    assert ld.source_size == (64, 32)
    a = np.asarray(ld.image)
    assert tuple(a[2, 2, :3]) == (0x20, 0x50, 0xC0) and tuple(a[20, 50, :3]) == (0x40, 0x40, 0x40)
    assert tuple(a[2, 40, :3]) == (0x70, 0xA0, 0x50)
    assert [(p.name, p.type, p.x, p.y) for p in ld.pois] == [("Rồng", "boss", 40, 16)]
    with pytest.raises(StepError):
        load_source(b'{"a": 1}', "x.json")


def test_parse_pois():
    pois = parse_pois("Làng, 120, 340, town\n# note\n0.5; 0.25\nBoss|10|20|boss", (800, 400))
    assert [(p.name, p.type, p.x, p.y) for p in pois] == [("Làng", "town", 120, 340), ("poi_2", "poi_2", 400, 100),
                                                          ("Boss", "boss", 10, 20)]
    with pytest.raises(StepError):
        parse_pois("only name", (10, 10))


def test_clean_style_flattens_and_outlines():
    img = level_image().convert("RGBA").resize((256, 128))
    out = clean_style(img, 6)
    assert len(np.unique(np.asarray(out)[..., :3].reshape(-1, 3), axis=0)) <= 12
    lum = np.asarray(out, np.float32)[..., :3].sum(-1)
    edge = lum[25:40, 20:30].min(axis=1)
    assert (edge < lum[25:40, 45:55].mean(axis=1) * 0.75).all() and (edge < lum[25:40, 2:8].mean(axis=1) * 0.75).all()


def test_icons_are_centred():
    for kind in ("town", "boss", "quest", "player", "chest", "portal", "unknown"):
        a = np.asarray(draw_icon(kind, 21))[..., 3].astype(np.float32)
        ys, xs = np.nonzero(a > 0)
        w = a[ys, xs]
        assert abs((xs * w).sum() / w.sum() - 10) < 2.2 and abs((ys * w).sum() / w.sum() - 10) < 2.6


async def _run(ctx: FakeCtx, params: dict[str, Any], edits: list | None = None, name: str = "level.png",
               data: bytes | None = None):
    if data is None:
        src = await put_img(ctx, level_image(), name)
    else:
        src = await ctx.blobs.put_bytes(data, name=name, mime="application/octet-stream")
    base = await MinimapBase().run(ctx, params, {"source": [src]})
    fin = await MinimapFinish().run(ctx, {}, {"base": base.outputs["base"], "info": base.outputs["info"],
                                              "edits": edits or []})
    return base, fin


async def test_pois_land_on_exact_pixels(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    pois = "A, 0, 0, quest\nB, 400, 200, boss\nC, 799, 399, town\nD, 0.25, 0.75, shop"
    base, fin = await _run(ctx, {"size": 512, "pois": pois, "shape": "square", "asset": "World Map"})
    doc = json.loads(ctx.blobs.path(fin.outputs["minimap"][0].hash).read_bytes())
    assert doc["type"] == "asset-studio/minimap" and doc["size"] == [512, 512] and doc["map_rect"] == [0, 128, 512, 256]
    expect = {"A": (0, 128), "B": (256, 256), "C": (511, 384), "D": (128, 320)}
    for p in doc["pois"]:
        ex = expect[p["name"]]
        assert abs(p["px"][0] - ex[0]) <= 1 and abs(p["px"][1] - ex[1]) <= 1
        assert p["x"] == pytest.approx((p["px"][0] + 0.5) / 512, abs=1e-5)
        assert p["y"] == pytest.approx((p["px"][1] + 0.5) / 512, abs=1e-5)
    # The icon really is drawn around each POI: its alpha-weighted centre on (map with icons - base) within 2 px.
    before = np.asarray(load(ctx, base.outputs["base"][0]), np.float32)
    after = np.asarray(load(ctx, fin.outputs["images"][0]), np.float32)
    diff = np.abs(after - before)[..., :3].sum(-1)
    icon = doc["icon"]
    for p in doc["pois"]:
        x, y = p["px"]
        if p["name"] in ("A", "C"):
            continue
        r = icon // 2
        win = diff[y - r:y + r + 1, x - r:x + r + 1]
        ys, xs = np.nonzero(win > 30)
        w = win[ys, xs]
        assert abs((xs * w).sum() / w.sum() - r) <= 2 and abs((ys * w).sum() / w.sum() - r) <= 2.6
    assert [r.name for r in fin.outputs["images"]] == ["world_map_minimap.png"]


async def test_circle_shape_mask_and_outside_warning(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    base, fin = await _run(ctx, {"size": 256, "pois": "Corner, 0, 0, flag\nMid, 400, 200, npc"})
    assert fin.meta["outside_circle"] == ["Corner"]
    mask = np.asarray(load(ctx, fin.outputs["mask"][0]))[..., 3]
    assert mask[128, 128] == 255 and mask[0, 0] == 0
    framed = np.asarray(load(ctx, fin.outputs["framed"][0]))[..., 3]
    assert framed[2, 2] == 0 and framed[128, 128] == 255
    assert base.outputs["restyle_in"] == [] and base.meta["style"] == "clean"


async def test_klein_style_hands_over_and_uses_the_edit(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    src = await put_img(ctx, level_image(), "level.png")
    base = await MinimapBase().run(ctx, {"style": "parchment", "size": 256, "prompt": "red ink"}, {"source": [src]})
    lay = json.loads(ctx.blobs.path(base.outputs["layout"][0].hash).read_bytes())
    assert len(base.outputs["restyle_in"]) == 1 and load(ctx, base.outputs["restyle_in"][0]).size == (1024, 1024)
    assert "parchment" in lay["prompts"][0] and lay["prompts"][0].endswith("red ink")
    sepia = await put_img(ctx, Image.new("RGB", (1024, 1024), (180, 150, 100)), "edit.png")
    fin = await MinimapFinish().run(ctx, {}, {"base": base.outputs["base"], "info": base.outputs["info"],
                                              "edits": [sepia]})
    a = np.asarray(load(ctx, fin.outputs["images"][0]))
    assert fin.meta["restyled"] and tuple(a[10, 128, :3]) == (180, 150, 100)


async def test_icon_pack_icons_override_cpu_icons(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    src = await put_img(ctx, level_image(), "level.png")
    base = await MinimapBase().run(ctx, {"size": 256, "pois": "Shop, 400, 200, shop"}, {"source": [src]})
    ic = await put_img(ctx, Image.new("RGBA", (64, 64), (255, 0, 255, 255)), "shop_icon.png")
    fin = await MinimapFinish().run(ctx, {"icon": 15}, {"base": base.outputs["base"], "info": base.outputs["info"],
                                                        "icons": [ic]})
    a = np.asarray(load(ctx, fin.outputs["images"][0]))
    doc = json.loads(ctx.blobs.path(fin.outputs["minimap"][0].hash).read_bytes())
    x, y = doc["pois"][0]["px"]
    assert tuple(a[y, x, :3]) == (255, 0, 255) and tuple(a[y, x + 7, :3]) == (255, 0, 255)
    assert tuple(a[y, x + 9, :3]) != (255, 0, 255)


async def test_bad_params(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    src = await put_img(ctx, level_image(), "level.png")
    for p in ({"size": 10}, {"style": "neon"}, {"shape": "hex"}, {"colors": 99}):
        with pytest.raises(StepError):
            await MinimapBase().run(ctx, p, {"source": [src]})
    with pytest.raises(StepError):
        await MinimapBase().run(ctx, {}, {})


async def test_tiled_map_end_to_end(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    base, fin = await _run(ctx, {"size": 128}, name="map.tmx", data=tmx_bytes())
    doc = json.loads(ctx.blobs.path(fin.outputs["minimap"][0].hash).read_bytes())
    assert doc["source"]["kind"] == "tilemap" and doc["map_rect"] == [0, 32, 128, 64]
    assert [p["px"] for p in doc["pois"]] == [[16, 48], [112, 80]]


async def test_export_unity_and_godot(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.export.minimap import UGUI_IMAGE, UGUI_MASK, UGUI_RAW_IMAGE
    from studio_core.store.bundle import engine_ready, engine_source

    ctx = FakeCtx(tmp_path)
    _, fin = await _run(ctx, {"size": 256, "pois": "Town, 400, 200, town"})
    step = _row("approve", "gate.approve", {"images": fin.outputs["images"], "minimap": fin.outputs["minimap"],
                                            "framed": fin.outputs["framed"], "mask": fin.outputs["mask"]})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "World")
    assert src.minimap and set(src.minimap_files) == {"map", "mask", "framed"}
    u = dict(unity.export(src))
    assert {"Minimap/World_minimap.png", "Minimap/World_minimap_mask.png", "Minimap/World_minimap.json",
            "Minimap/World_minimap.prefab", "Minimap/World_minimap.prefab.meta"} <= set(u)
    prefab = u["Minimap/World_minimap.prefab"].decode()
    guid_of = {k: next(ln.split()[1] for ln in u[f"Minimap/World_minimap{s}.png.meta"].decode().splitlines()
                       if ln.startswith("guid:")) for k, s in (("map", ""), ("mask", "_mask"))}
    assert f"m_Texture: {{fileID: 2800000, guid: {guid_of['map']}, type: 3}}" in prefab
    assert f"m_Sprite: {{fileID: 21300000, guid: {guid_of['mask']}, type: 3}}" in prefab
    assert all(g in prefab for g in (UGUI_IMAGE, UGUI_MASK, UGUI_RAW_IMAGE)) and "m_ShowMaskGraphic: 0" in prefab
    idx = json.loads(u["Minimap/World_minimap.json"])
    assert idx["map"] == {"file": "World_minimap.png"} and idx["pois"][0]["name"] == "Town"
    guids = [ln for k, b in u.items() if k.endswith(".meta") for ln in b.decode().splitlines() if ln.startswith("guid:")]
    assert len(guids) == len(set(guids))
    g = dict(godot.export(src))
    scene = g["World_minimap.tscn"].decode()
    assert 'path="Minimap/World_minimap.png"' in scene and "ShaderMaterial" in scene and "TextureRect" in scene
    assert "shader_type canvas_item" in g["Minimap/minimap_circle.gdshader"].decode()


class FakeKlein:
    kind = "comfy.edit_flux2_klein4b"
    lane = Lane.GPU
    version = "1"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.prompts.append(params["75:74.text"])
        out = []
        for ref in inputs["image"]:
            with Image.open(ctx.blobs.path(ref.hash)) as im:
                g = im.convert("L").convert("RGB")
            buf = io.BytesIO()
            g.save(buf, "PNG")
            out.append(await ctx.blobs.put_bytes(buf.getvalue(), name="edit.png", mime="image/png"))
        return StepResult(outputs={"images": out})


@pytest.mark.parametrize("style,calls", [("clean", 0), ("stylized", 1)])
async def test_recipe_runs_with_fake_klein(tmp_path: Path, style: str, calls: int):
    from test_engine_runner import Env

    klein = FakeKlein()
    env = Env(tmp_path, [], {"minimap": (RECIPES / "minimap.yaml").read_text(encoding="utf-8")})
    env.registry.register(klein)
    for op in (MinimapBase(), MinimapFinish()):
        env.registry.register(op)
    buf = io.BytesIO()
    level_image().save(buf, "PNG")
    ref = await env.blobs.put_bytes(buf.getvalue(), name="level.png", mime="image/png")
    run_id = await env.engine.start_run("minimap", {"source": ref.hash, "style": style, "size": 128,
                                                    "pois": "Town, 400, 200, town", "review": False})
    import asyncio

    await asyncio.wait_for(env.engine.wait_idle(run_id), 20)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: (s.status, s.error) for k, s in env.steps(run_id).items()}
    steps = env.steps(run_id)
    assert len(klein.prompts) == calls
    if calls:
        assert "stylized" in klein.prompts[0] or "hand-painted" in klein.prompts[0]
    assert steps["finish"].meta["restyled"] is bool(calls)


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "minimap.yaml")
    assert [s.id for s in r.steps] == ["base", "restyle", "finish", "approve"]
    _, issues = validate((RECIPES / "minimap.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    for kind in ("op.minimap_base", "op.minimap_finish"):
        assert CATALOG[kind]["label"]


def test_tile_layers_without_terrain_meaning_are_not_painted():
    tmj = {"type": "map", "width": 2, "height": 1, "tilewidth": 8, "tileheight": 8,
           "layers": [{"type": "tilelayer", "name": "Ground", "data": [1, 1]},
                      {"type": "tilelayer", "name": "Shadow", "data": [0, 2]}]}
    img = np.asarray(load_source(json.dumps(tmj).encode(), "m.tmj").image)
    grass = np.asarray((124, 172, 92), np.float32)
    assert np.abs(img[4, 12, :3].astype(np.float32) - grass).max() < 20


def _tileset_png(colors: list[tuple[int, int, int]], alpha: int = 255) -> bytes:
    img = Image.new("RGBA", (16 * len(colors), 16))
    for i, c in enumerate(colors):
        img.paste((*c, alpha), (i * 16, 0, i * 16 + 16, 16))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


TILED_IMG = """<map orientation="orthogonal" width="3" height="1" tilewidth="16" tileheight="16" infinite="0">
 <tileset firstgid="1" name="t" tilewidth="16" tileheight="16" tilecount="3" columns="3">
  <image source="sub/t.png" width="48" height="16"/></tileset>
 <layer id="1" name="Layer A" width="3" height="1"><data encoding="csv">1,1,1</data></layer>
 <layer id="2" name="Layer B" width="3" height="1"><data encoding="csv">0,{road},3</data></layer>
</map>"""


def _tile_colors(png: bytes):
    from studio_core.ops.minimap import TileColors

    return TileColors({"t.png": np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))})


def test_tileset_colors_follow_tile_images():
    png = _tileset_png([(30, 160, 40), (200, 170, 90), (60, 60, 200)])
    out = load_source(TILED_IMG.format(road=2).encode(), "m.tmx", tiles=_tile_colors(png))
    a = np.asarray(out.image)
    assert [tuple(a[8, x * 16 + 8, :3]) for x in range(3)] == [(30, 160, 40), (200, 170, 90), (60, 60, 200)]
    flipped = TILED_IMG.format(road=2 | 0x80000000).encode()
    assert tuple(np.asarray(load_source(flipped, "m.tmx", tiles=_tile_colors(png)).image)[8, 24, :3]) == (200, 170, 90)


def test_transparent_tile_shows_layer_below_and_missing_image_falls_back():
    png = _tileset_png([(30, 160, 40), (200, 170, 90), (60, 60, 200)])
    clear = _tile_colors(_tileset_png([(30, 160, 40), (200, 170, 90), (60, 60, 200)], alpha=0))
    a = np.asarray(load_source(TILED_IMG.format(road=2).encode(), "m.tmx", tiles=clear).image)
    assert tuple(a[8, 24, :3]) == (36, 40, 44)
    from studio_core.ops.minimap import TileColors

    named = np.asarray(load_source(TILED_IMG.format(road=2).encode(), "m.tmx", tiles=TileColors({"x.png": np.zeros((16, 48, 4), np.uint8)})).image)
    assert tuple(named[8, 8, :3]) != (30, 160, 40) and tuple(named[8, 8, :3]) != (36, 40, 44)
    assert png


def test_reference_image_samples_cells_without_tileset():
    ref = Image.new("RGB", (48, 16))
    for i, c in enumerate([(10, 200, 10), (220, 40, 40), (40, 40, 220)]):
        ref.paste(c, (i * 16, 0, i * 16 + 16, 16))
    a = np.asarray(load_source(TILED_IMG.format(road=2).encode(), "m.tmx", reference=ref).image)
    assert [tuple(a[8, x * 16 + 8, :3]) for x in range(3)] == [(10, 200, 10), (220, 40, 40), (40, 40, 220)]


def test_ldtk_tiles_use_tileset_pixels():
    doc = {"defs": {"layers": []}, "levels": [{"identifier": "L", "pxWid": 32, "pxHei": 16, "layerInstances": [
        {"__identifier": "Ground", "__type": "Tiles", "__gridSize": 16, "__cWid": 2, "__cHei": 1,
         "__tilesetRelPath": "t.png", "gridTiles": [{"px": [0, 0], "src": [0, 0], "t": 0},
                                                      {"px": [16, 0], "src": [16, 0], "t": 1}]}]}]}
    png = _tileset_png([(30, 160, 40), (200, 170, 90)])
    a = np.asarray(load_source(json.dumps(doc).encode(), "w.ldtk", tiles=_tile_colors(png)).image)
    assert tuple(a[8, 8, :3]) == (30, 160, 40) and tuple(a[8, 24, :3]) == (200, 170, 90)


async def test_op_reads_tiles_input(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    tiles = await ctx.blobs.put_bytes(_tileset_png([(30, 160, 40), (200, 170, 90), (60, 60, 200)]), name="t.png",
                                      mime="image/png")
    src = await ctx.blobs.put_bytes(TILED_IMG.format(road=2).encode(), name="m.tmx", mime="application/xml")
    base = await MinimapBase().run(ctx, {"size": 96, "colors": 8}, {"source": [src], "tiles": [tiles]})
    a = np.asarray(load(ctx, base.outputs["base"][0]))
    assert len({tuple(p) for p in a[::8, ::8, :3].reshape(-1, 3)}) >= 3
