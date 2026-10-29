import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.contracts import StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.export.sheet import layout
from studio_core.ops.enemy_variants import (REF_SIZE, EnemyPrep, EnemyVariants, accessory_layer, find_regions,
                                           match_shift, parse_variants, recolor_sheet, slug, unique_colors, _premul)
from studio_core.ops.rarity import LUM
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
FW = FH = 48
ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
FRAMES = 4


def bob(f: int) -> int:
    return [0, 2, 4, 2][f]


def figure(dy: int = 0) -> Image.Image:
    """Red body, blue cloth, grey blade, dark outline; `dy` moves it down like a walk bob."""
    im = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((14, 14 + dy, 33, 41 + dy), fill=(20, 20, 24, 255))
    d.rectangle((15, 15 + dy, 32, 27 + dy), fill=(200, 50, 40, 255))
    d.rectangle((15, 28 + dy, 32, 40 + dy), fill=(40, 70, 190, 255))
    d.rectangle((34, 24 + dy, 36, 38 + dy), fill=(150, 150, 155, 255))
    return im


def anim_sheet() -> tuple[Image.Image, dict]:
    clips = [("idle", 6), ("walk", 10)]
    rows = len(clips) * 8
    sheet = Image.new("RGBA", (FRAMES * FW, rows * FH), (0, 0, 0, 0))
    for r in range(rows):
        for f in range(FRAMES):
            sheet.alpha_composite(figure(bob(f) if r >= 8 else 0), (f * FW, r * FH))
    meta = {"frame_w": FW, "frame_h": FH, "cols": FRAMES, "rows": rows, "count": rows * FRAMES, "padding": 0,
            "width": sheet.width, "height": sheet.height, "pivot": [24, 44], "order": list(range(rows * FRAMES)),
            "frames": ["x"] * (rows * FRAMES),
            "clips": [{"name": n, "row0": i * 8, "rows": 8, "frames": FRAMES, "fps": fps, "loop": True}
                      for i, (n, fps) in enumerate(clips)],
            "row_angles": ANGLES * len(clips)}
    return sheet, meta


def horned(white: Image.Image, box: list[int], area: list[int]) -> Image.Image:
    """Fake Klein edit: two yellow horns above the head, drawn in the 1024 reference space."""
    out = white.copy()
    s = box[2] / (area[2] - area[0])
    d = ImageDraw.Draw(out)
    for hx in (17, 28):
        x = box[0] + (hx - area[0]) * s
        y = box[1] + (8 - area[1]) * s
        d.rectangle((x, y, x + 3 * s, y + 6 * s), fill=(230, 200, 40))
    return out


async def _put(ctx, sheet, meta):
    s = await put_img(ctx, sheet, "sheet.png")
    m = await ctx.blobs.put_bytes(json.dumps(meta).encode(), name="sheet.json", mime="application/json")
    return s, m


def test_slug_and_parse_variants():
    assert slug("Lửa") == "lua" and slug("bóng tối") == "bong_toi" and slug("Độc") == "doc"
    vs = parse_variants("lửa, băng\nhoàng gia: #3a2aa0 #e0b030")
    assert [v["slug"] for v in vs] == ["lua", "bang", "hoang_gia"]
    assert vs[2]["colors"] == [(0x3a, 0x2a, 0xa0), (0xe0, 0xb0, 0x30)] and len(vs[0]["colors"]) == 3
    assert parse_variants("quái lạ")[0]["colors"] == parse_variants("quái lạ")[0]["colors"]
    with pytest.raises(StepError):
        parse_variants("lửa, Lửa")
    with pytest.raises(StepError):
        parse_variants("x: #zzzzzz")


def test_recolor_sheet_is_one_table_keeps_outline_luma_and_colour_count():
    sheet, _ = anim_sheet()
    _, _, cols, counts = unique_colors(np.asarray(sheet))
    peaks = find_regions(cols, counts)
    assert len(peaks) == 2
    out, before, after = recolor_sheet(sheet, peaks, [(60, 200, 80), (160, 60, 200)], 0.9, 0.25)
    a, b = np.asarray(sheet), np.asarray(out)
    assert after <= before and np.array_equal(a[..., 3], b[..., 3])
    assert tuple(b[14, 14, :3]) == (20, 20, 24)
    vis = a[..., 3] > 0
    la, lb = a[..., :3].astype(np.float32) @ LUM, b[..., :3].astype(np.float32) @ LUM
    assert np.abs(la - lb)[vis].max() < 6
    # Every frame changes identically: two frames with the same source pixels stay equal.
    assert np.array_equal(b[:FH, :FW], b[FH:2 * FH, :FW])
    body, cloth = b[20, 20, :3].astype(int), b[35, 20, :3].astype(int)
    assert body[1] > body[0] and cloth[2] > cloth[1]


def test_match_shift_finds_the_bob():
    key, moved = _premul(figure(0)), _premul(figure(3))
    w = np.ones((20, 24), np.float32)
    dx, dy, err = match_shift(key, moved, (12, 10, 36, 30), w, 8)
    assert (dx, dy) == (0, 3) and err < 0.02


def test_accessory_layer_keeps_only_the_added_part():
    base = figure()
    edit = Image.new("RGB", base.size, (255, 255, 255))
    edit.paste(base, mask=base.getchannel("A"))
    noisy = np.asarray(edit).astype(np.int16) + np.random.default_rng(0).integers(-20, 20, (FH, FW, 3))
    edit = Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8))
    ImageDraw.Draw(edit).rectangle((18, 6, 22, 13), fill=(230, 200, 40))
    layer, erase, info = accessory_layer(base, edit)
    alpha = np.asarray(layer)[..., 3] > 0
    assert alpha[8:12, 19:22].all() and not alpha[20:40].any() and not erase.any()
    assert info["painted"] < 60


async def test_variants_with_tracked_accessory(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    sheet, meta = anim_sheet()
    s, m = await _put(ctx, sheet, meta)
    params = {"variants": "lửa, elite", "accessory": "two horns", "asset": "Goblin", "accessory_on": "elite"}
    p = await EnemyPrep().run(ctx, params, {"sheet": [s], "meta": [m]})
    lay_doc = json.loads(ctx.blobs.path(p.outputs["layout"][0].hash).read_bytes())
    assert len(p.outputs["keys"]) == 8 and lay_doc["pixel"] is True
    assert Image.open(ctx.blobs.path(p.outputs["keys"][0].hash)).size == (REF_SIZE, REF_SIZE)
    edits = []
    for ref, k in zip(p.outputs["keys"], lay_doc["keys"]):
        edits.append(await put_img(ctx, horned(Image.open(ctx.blobs.path(ref.hash)).convert("RGB"), k["box"],
                                               k["area"]), "edit.png"))
    r = await EnemyVariants().run(ctx, params, {"sheet": [s], "meta": [m], "edits": edits,
                                                "layout": p.outputs["layout"]})
    doc = json.loads(ctx.blobs.path(r.outputs["variants"][0].hash).read_bytes())
    assert [v["asset"] for v in doc["variants"]] == ["goblin_lua", "goblin_elite"]
    assert [v["accessory"] for v in doc["variants"]] == [False, True]
    assert [ref.name for ref in r.outputs["sheets"]] == ["goblin_lua_sheet.png", "goblin_elite_sheet.png"]
    assert doc["accessory"]["flagged"] == [] and not doc["accessory"]["empty"]
    walk = [f for d in doc["accessory"]["directions"] for f in d["frames"] if f["cell"].startswith("walk")]
    assert {(f["dx"], f["dy"]) for f in walk} == {(0, bob(i)) for i in range(FRAMES)}
    elite = np.asarray(Image.open(ctx.blobs.path(r.outputs["sheets"][1].hash)))
    lua = np.asarray(Image.open(ctx.blobs.path(r.outputs["sheets"][0].hash)))
    assert elite.shape == lua.shape == np.asarray(sheet).shape
    for f in range(FRAMES):
        y = 8 * FH + bob(f)
        assert elite[y + 9, f * FW + 18, 3] == 255 and lua[y + 9, f * FW + 18, 3] == 0
    out_meta = json.loads(ctx.blobs.path(r.outputs["meta"][0].hash).read_bytes())
    assert "frames" not in out_meta and out_meta["clips"] == meta["clips"]
    assert r.outputs["accessory_preview"] and Image.open(ctx.blobs.path(r.outputs["preview"][0].hash)).width > 400


async def test_no_accessory_skips_klein_and_needs_directions(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    sheet, meta = anim_sheet()
    s, m = await _put(ctx, sheet, meta)
    p = await EnemyPrep().run(ctx, {"variants": "băng"}, {"sheet": [s], "meta": [m]})
    assert p.outputs["keys"] == []
    r = await EnemyVariants().run(ctx, {"variants": "băng"}, {"sheet": [s], "meta": [m],
                                                              "layout": p.outputs["layout"]})
    assert r.outputs["accessory_preview"] == [] and r.meta["accessory"] is False
    flat = {k: v for k, v in meta.items() if k not in ("clips", "row_angles")}
    flat["flipbook"] = {"fps": 24, "loop": True}
    _, m2 = await _put(ctx, sheet, flat)
    with pytest.raises(StepError):
        await EnemyPrep().run(ctx, {"variants": "băng"}, {"sheet": [s], "meta": [m2]})


async def test_export_unity_and_godot(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx = FakeCtx(tmp_path)
    sheet, meta = anim_sheet()
    s, m = await _put(ctx, sheet, meta)
    p = await EnemyPrep().run(ctx, {"variants": "lửa, băng"}, {"sheet": [s], "meta": [m]})
    r = await EnemyVariants().run(ctx, {"variants": "lửa, băng"}, {"sheet": [s], "meta": [m],
                                                                    "layout": p.outputs["layout"]})
    step = _row("approve", "gate.approve", {k: r.outputs[k] for k in ("preview", "sheets", "meta", "variants")})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Goblin")
    assert [n for n, _ in src.enemy_variants] == ["lua", "bang"] and src.sheet is not None
    u = dict(unity.export(src))
    assert "Goblin.png" in u and "Goblin.controller" in u
    for v in ("lua", "bang"):
        base = f"Variants/Goblin_{v}/"
        assert {f"{base}Goblin_{v}.png", f"{base}Goblin_{v}.controller", f"{base}Goblin_{v}.prefab",
                f"{base}Animations/walk_S.anim"} <= set(u)
        assert f"{base}README.txt" not in u
    guids = [ln for k, b in u.items() if k.endswith(".meta") for ln in b.decode().splitlines() if ln.startswith("guid:")]
    assert len(guids) == len(set(guids))
    idx = json.loads(u["Goblin_variants.json"])
    assert [v["asset"] for v in idx["variants"]] == ["Goblin_lua", "Goblin_bang"]
    assert "Variants/Goblin_<biến thể>/" in u["README.txt"].decode()
    g = dict(godot.export(src))
    assert {"Goblin.tres", "Variants/Goblin_lua/Goblin_lua.tres", "Variants/Goblin_lua/Goblin_lua.tscn",
            "Variants/Goblin_bang/asset_studio_sprite.gd"} <= set(g)
    tres = g["Variants/Goblin_lua/Goblin_lua.tres"].decode()
    assert 'path="Goblin_lua.png"' in tres and '"walk_S"' in tres


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "enemy-variants.yaml")
    assert [s.id for s in r.steps] == ["prep", "accessory", "variants", "approve"]
    _, issues = validate((RECIPES / "enemy-variants.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    for kind in ("op.enemy_prep", "op.enemy_variants"):
        assert CATALOG[kind]["label"]
    lay = layout(anim_sheet()[1], directional_rows=True)
    assert len(lay.tracks) == 16 and lay.facing
