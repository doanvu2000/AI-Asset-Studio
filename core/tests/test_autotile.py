import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import ExecutorRegistry, StepError
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import register
from studio_core.ops.autotile import (AUTOTILE_FILE, BIT, Autotile, AutotileOptions, blob_masks, edge_profiles, layout,
                                      mask_at, palette_limit, parse, random_cells, reduce_mask, render_set, seam_check,
                                      terrain_names, textures, tile_map)
from studio_core.ops.tile import soft_blur, tile_report
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def texture(rgb: tuple[float, float, float], seed: int, n: int = 128) -> Image.Image:
    noise = soft_blur(np.random.default_rng(seed).random((n, n)).astype(np.float32), 4)
    noise = (noise - noise.mean()) / noise.std()
    a = np.clip(np.array(rgb)[None, None] * (1 + 0.15 * noise[..., None]), 0, 1)
    return Image.fromarray(np.round(a * 255).astype(np.uint8), "RGB")


def test_blob_table():
    masks = blob_masks()
    assert len(masks) == 47 and masks[0] == 0 and masks[-1] == 255
    assert reduce_mask(BIT["ne"]) == 0 and reduce_mask(BIT["n"] | BIT["e"] | BIT["ne"]) == 0b111
    tiles = layout(("grass", "dirt"))
    assert len(tiles) == 48 and tiles[-1]["name"] == "dirt_fill" and tiles[-1]["mask"] is None
    assert {(t["col"], t["row"]) for t in tiles} == {(c, r) for c in range(8) for r in range(6)}
    full = next(t for t in tiles if t["mask"] == 255)
    assert set(full["godot_peering"].values()) == {0} and full["unity_neighbors"] == [1] * 8
    alone = next(t for t in tiles if t["mask"] == 0)
    # Sides say NotThis, corners are don't-care when a side next to them is not A.
    assert alone["unity_neighbors"] == [0, 2, 0, 2, 2, 0, 2, 0] and set(alone["godot_peering"].values()) == {1}
    inner = next(t for t in tiles if t["mask"] == 255 & ~BIT["ne"])
    assert inner["unity_neighbors"][2] == 2 and inner["godot_peering"]["top_right_corner"] == 1
    assert len({tuple(t["unity_neighbors"]) for t in tiles[:47]}) == 47
    cells = np.array([[1, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
    assert mask_at(cells, 1, 1) == reduce_mask(sum(BIT[d] for d in ("n", "nw", "w", "e", "s")))
    assert mask_at(cells, 0, 0) == BIT["e"] | BIT["s"] | BIT["se"]


@pytest.mark.parametrize("opts", [AutotileOptions(size=32), AutotileOptions(size=16, pixel=True, edge="rough"),
                                  AutotileOptions(size=64, edge="smooth", border=0.4, round=0.25, rim=0.15)])
def test_neighbours_meet_without_seams(opts):
    a, b = textures(texture((0.3, 0.6, 0.25), 1), texture((0.55, 0.4, 0.25), 2), opts)
    tiles = render_set(a, b, opts)
    assert np.allclose(tiles[-1], b) and np.allclose(tiles[46], a)
    for seed in range(3):
        cells = random_cells(10, 7, seed)
        assert edge_profiles(cells, opts) == 0.0
        ratio = seam_check(cells, tiles)["seam_ratio"]
        assert max(ratio.values()) < 1.2, ratio
    cells = random_cells(8, 6, 7)
    img = tile_map(cells, tiles)
    t = opts.size
    b_cells = np.argwhere(~cells)
    y, x = b_cells[0]
    assert np.allclose(img[y * t:(y + 1) * t, x * t:(x + 1) * t], b)


def test_palette_limit_is_pointwise():
    opts = AutotileOptions(size=16, pixel=True, colors=8)
    a, b = textures(texture((0.3, 0.6, 0.25), 1), texture((0.55, 0.4, 0.25), 2), opts)
    tiles = render_set(a, b, opts)
    sheet = np.round(np.concatenate(tiles, 1) * 255).astype(np.uint8)
    out, pal = palette_limit(sheet, 8)
    assert len(pal) <= 8 and len(np.unique(out.reshape(-1, 3), axis=0)) <= 8
    same = (sheet.reshape(-1, 3)[:, None] == sheet.reshape(-1, 3)[None, :200]).all(-1)
    mapped = (out.reshape(-1, 3)[:, None] == out.reshape(-1, 3)[None, :200]).all(-1)
    assert (mapped | ~same).all()


def test_parse_and_names():
    assert parse({"size": "32", "pixel": "true"}).pixel and parse({}).size == 64
    for bad in ({"size": 48}, {"edge": "zigzag"}, {"border": 0.6}, {"colors": 1}):
        with pytest.raises(StepError):
            parse(bad)
    assert terrain_names("Grass, Deep Water") == ("grass", "deep_water")
    assert terrain_names("") == ("a", "b") and terrain_names("x, x") == ("x", "x_2")


async def _run(tmp_path: Path, a_img: Image.Image | None = None, **params):
    ctx = FakeCtx(tmp_path)
    a = await put_img(ctx, a_img or texture((0.3, 0.6, 0.25), 1), "grass.png")
    b = await put_img(ctx, texture((0.55, 0.4, 0.25), 2), "dirt.png")
    res = await Autotile().run(ctx, {"terrains": "grass, dirt", "size": 32, **params}, {"tile_a": [a], "tile_b": [b]})
    return ctx, res


async def test_op_outputs_json(tmp_path: Path):
    ctx, res = await _run(tmp_path)
    doc = json.loads(ctx.blobs.path(res.outputs["autotile"][0].hash).read_bytes())
    assert res.outputs["autotile"][0].name == AUTOTILE_FILE
    assert doc["layout"] == "blob47" and doc["grid"] == [8, 6] and doc["terrains"] == ["grass", "dirt"]
    assert len(doc["tiles"]) == 48 and doc["tiles"][0]["name"] == "grass_000"
    assert doc["seam_check"]["passed"] and doc["seam_check"]["edge_mismatch"] == 0
    assert doc["atlas"]["hash"] == res.outputs["images"][0].hash
    with Image.open(ctx.blobs.path(res.outputs["images"][0].hash)) as im:
        assert im.size == (256, 192)
    assert len(res.outputs["preview"]) == 2


async def test_op_fixes_non_seamless_input(tmp_path: Path):
    y, x = np.mgrid[0:128, 0:128]
    ramp = np.stack([x / 128, 0.5 + 0 * x, y / 256], -1)
    img = Image.fromarray(np.round(ramp * 255).astype(np.uint8), "RGB")
    assert tile_report(img)["seam_score"] < 70
    _, res = await _run(tmp_path, img, pixel=True, colors=8, size=16)
    assert res.meta["made_seamless"] == {"a": True, "b": False} and len(res.meta["palette"]) <= 8


async def test_export_unity_and_godot(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.export.autotile import RULE_TILE_GUID
    from studio_core.store.bundle import engine_ready, engine_source

    ctx, res = await _run(tmp_path, pixel=True)
    step = _row("approve", "gate.approve", {k: res.outputs[k] for k in ("images", "preview", "autotile")})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Meadow")
    assert src.autotile is not None and src.autotile_atlas is not None and not src.textures

    g = dict(godot.export(src))
    tres = g["Meadow_autotile_tileset.tres"].decode()
    assert 'path="Autotile/Meadow_autotile.png"' in tres and "terrain_set_0/mode = 0" in tres
    assert tres.count("/terrain_set = 0") == 48 and 'terrain_set_0/terrain_1/name = "dirt"' in tres
    assert "7:5/0/terrain = 1" in tres and "texture_region_size = Vector2i(32, 32)" in tres
    assert "Autotile/Meadow_autotile.png" in g and "Autotile/Meadow_autotile.json" in g
    assert "Match Corners and Sides" in g["README.txt"].decode()

    u = dict(unity.export(src))
    rule = u["Autotile/Meadow_grass_rule.asset"].decode()
    assert RULE_TILE_GUID in rule and rule.count("- m_Id:") == 47 and rule.count("m_NeighborPositions:") == 47
    assert "m_Neighbors: 0200000002000000020000000200000002000000020000000200000002000000" not in rule
    assert "m_Neighbors: 0100000001000000010000000100000001000000010000000100000001000000" in rule
    meta = u["Autotile/Meadow_autotile.png.meta"].decode()
    assert "spritePixelsToUnits: 32" in meta and "filterMode: 0" in meta and meta.count("internalID:") == 49
    assert "fileID: 13312" in u["Autotile/Meadow_dirt.asset"].decode()
    assert "mainObjectFileID: 11400000" in u["Autotile/Meadow_grass_rule.asset.meta"].decode()
    assert "2D Tilemap Extras" in u["README.txt"].decode()


@pytest.mark.parametrize("name", ["autotile-terrain", "autotile-terrain-prompt"])
def test_recipes_load_and_lint(name):
    path = RECIPES / f"{name}.yaml"
    r = load_recipe_file(path)
    assert r.step("tiles").kind == "op.autotile"
    _, issues = validate(path.read_text(encoding="utf-8"), None)
    assert issues == []
    reg = ExecutorRegistry()
    register(reg)
    assert reg.resolve("op.autotile")
