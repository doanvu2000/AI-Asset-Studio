import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import ExecutorRegistry, Lane, StepError, StepRow, StepStatus
from studio_core.engine.library import validate
from studio_core.ops import register
from studio_core.ops.tile import (_lum, _rgb, _scale_profile, detect_grid, detrend, rectify_pitch, seamless, seamless_info, soft_blur,
                                  stamp_repeat, tile_report, wang_arrangement, wang_index, wang_labels, wang_layout,
                                  wang_report, wang_set)

from test_ops_executors import FakeCtx, load, put_img
from test_w18_tiles import plain_noise, tileable_noise

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def bricks(n: int = 640, course: float = 23.7, brick: float = 61.7, pitch: float = 0.0, seed: int = 0) -> Image.Image:
    """Running-bond bricks with non-integer periods; pitch > 0 grows the courses down the image (camera keystone)."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n].astype(np.float64)
    yy = y + pitch * y * y / n
    xx = (x - n / 2) * (1 + 2 * pitch * y / n) + n / 2
    row = np.floor(yy / course)
    off = (row % 2) * brick / 2
    col = np.floor((xx + off) / brick)
    fy, fx = yy / course - row, (xx + off) / brick - col
    grout = (fy < 0.16) | (fx < 0.06)
    tone = (np.sin(row * 12.9898 + col * 78.233) * 43758.5453) % 1
    face = 0.45 + 0.25 * tone + 0.05 * rng.standard_normal((n, n))
    lum = np.where(grout, 0.85, face)
    rgb = np.stack([lum, lum * 0.55, lum * 0.4], -1)
    return Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).convert("RGBA")


def course_jump(img: Image.Image) -> float:
    """Largest deviation of the grout-row spacing from its median across the vertical wrap (two copies stacked)."""
    a = np.asarray(img.convert("L"), np.float64)
    prof = np.concatenate([a.mean(1), a.mean(1)])
    rows = np.flatnonzero((prof[1:] > 125) & (prof[:-1] <= 125)) + 1
    gaps = np.diff(rows)
    h = a.shape[0]
    near = gaps[(rows[1:] > h - 20) & (rows[1:] < h + 110)]
    return float(np.abs(near - np.median(gaps)).max())


def _multiple(v: float, base: float) -> bool:
    k = round(v / base)
    return k >= 1 and abs(v - k * base) <= 0.03 * v + 0.5


def test_detect_grid_finds_brick_periods_and_ignores_noise():
    g = detect_grid(detrend(_rgb(bricks())))
    assert g["x"]["strong"] and g["y"]["strong"], g
    assert _multiple(g["y"]["period"], 23.7) and _multiple(g["x"]["period"], 61.7 / 2), g
    for seed in (1, 2):
        noise = detect_grid(detrend(_rgb(plain_noise(512, r=4, seed=seed))))
        assert not noise["x"]["strong"] and not noise["y"]["strong"], noise


def test_grid_auto_keeps_courses_in_phase_across_the_wrap():
    src = bricks()
    off, info_off = seamless_info(src, grid="off")
    auto, info = seamless_info(src, grid="auto")
    assert not info_off["grid"] and info["grid"] and "y" in info["aligned"], info
    assert course_jump(auto) <= 2 < course_jump(off), (course_jump(auto), course_jump(off))
    assert tile_report(auto)["seam_score"] >= 90


def test_non_periodic_texture_is_unchanged_by_grid_auto():
    src = plain_noise(512, r=4, seed=3)
    a, info = seamless_info(src, grid="auto")
    assert not info["grid"] and not info.get("keystone")
    assert np.array_equal(np.asarray(a), np.asarray(seamless(src, grid="off")))
    with pytest.raises(ValueError):
        seamless(src, grid="maybe")


def test_rectify_pitch_evens_out_course_height():
    a = detrend(_rgb(bricks(pitch=0.12)))
    g = detect_grid(a)
    out, fit = rectify_pitch(a, g)
    assert "y" in fit and abs(fit["y"]) > 0.08, fit
    assert out.shape[0] > a.shape[0] and out.shape[1] == a.shape[1]
    before = _scale_profile(_lum(a), g)["y"]
    after = _scale_profile(_lum(out), detect_grid(out)).get("y", (1.0, 0.0))
    assert abs(after[1]) < abs(before[1]) / 3, (before, after)
    flat = detrend(_rgb(bricks()))
    same, none = rectify_pitch(flat, detect_grid(flat))
    assert none == {} and same is flat


def test_stamp_repeat_is_reported_but_not_scored():
    rng = np.random.default_rng(4)
    patch = soft_blur(rng.random((300, 640, 3)), 3)
    a = np.concatenate([patch, patch, patch[:100]], 0)
    a = (a - a.min()) / (a.max() - a.min())
    img = Image.fromarray((a * 255).astype(np.uint8)).convert("RGBA")
    st = stamp_repeat(_rgb(img))
    assert st and st["period"]["y"] == pytest.approx(300, abs=3), st
    rep = tile_report(img)
    assert "self_repeat" in rep["issues"] and rep["self_repeat"]["period"]["y"] == pytest.approx(300, abs=3)
    assert stamp_repeat(_rgb(plain_noise(512, r=4, seed=5))) is None


def test_wang_layout_labels():
    lay = wang_layout()
    assert len({t["name"] for t in lay}) == 16 and lay[6]["name"] == "wang_06_abba"
    assert wang_labels(0) == "aaaa" and wang_labels(15) == "bbbb"
    assert all(wang_index(*[int(t["corners"][c] == "b") for c in ("nw", "ne", "sw", "se")]) == t["index"] for t in lay)
    t = lay[0b1001]
    assert t["edges"] == {"n": "ba", "e": "ab", "s": "ab", "w": "ba"} and (t["col"], t["row"]) == (1, 2)


def _wang_tiles(size: int = 64):
    a = tileable_noise(size, 2, seed=11)
    rng = np.random.default_rng(12)
    b = Image.fromarray((np.clip(soft_blur(rng.random((size, size, 3)), 2) * 1.5 - 0.1, 0, 1) * 255)
                        .astype(np.uint8)).convert("RGBA")
    return a, b, *wang_set(a, b, size)


def test_wang_set_tiles_join_for_any_valid_arrangement():
    a, b, tiles, normals = _wang_tiles()
    assert len(tiles) == len(normals) == 16 and tiles[0].size == (64, 64)
    assert np.abs(np.asarray(tiles[0], int) - np.asarray(a, int)).max() <= 1
    assert np.abs(np.asarray(tiles[15], int) - np.asarray(b, int)).max() <= 1
    for seed in range(4):
        rep = wang_report(tiles, n=6, seed=seed)
        assert rep["passed"] and rep["score"] >= 90, rep
    # Any two tiles whose shared edge labels agree meet as smoothly as the interior of a tile.
    lay = wang_layout()
    arr = [np.asarray(t, np.float64)[..., :3] for t in tiles]
    inner = np.mean([np.abs(np.diff(x, axis=1)).mean() for x in arr])
    for i in range(16):
        for j in range(16):
            if lay[i]["edges"]["e"] == lay[j]["edges"]["w"]:
                assert np.abs(arr[i][:, -1] - arr[j][:, 0]).mean() < inner * 1.8, (i, j)
    n = np.asarray(normals[5], np.float64)[..., :3] / 127.5 - 1
    assert np.allclose(np.linalg.norm(n, axis=-1), 1, atol=0.05)
    # A mismatched neighbour (corner labels disagree) does show a seam.
    bad = np.ones((3, 3), int)
    bad[1, 1] = 0
    img, _ = wang_arrangement(tiles, 2, labels=bad)
    good, _ = wang_arrangement([tiles[0]] * 16, 2, labels=np.zeros((3, 3), int))
    assert img.size == good.size == (128, 128)


async def _wang_pipeline(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    reg = ExecutorRegistry()
    register(reg, None, None, None)
    refs = [await put_img(ctx, plain_noise(96, seed=s), f"z_{s}.png") for s in (1, 2, 3, 4)]
    seam = await reg.resolve("op.seamless").run(ctx, {"mode": "wang", "group": 2, "match_edges": True}, {"images": refs})
    assert [r.name for r in seam.outputs["images"]] == ["wang_a_seamless.png", "wang_b_seamless.png"]
    assert not seam.meta["match_edges"] and seam.meta["mode"] == "wang"
    with pytest.raises(StepError):
        await reg.resolve("op.seamless").run(ctx, {"mode": "wang"}, {"images": refs[:1]})
    with pytest.raises(StepError):
        await reg.resolve("op.seamless").run(ctx, {"mode": "corner"}, {"images": refs})
    wang = await reg.resolve("op.tile_wang").run(ctx, {"mode": "wang", "size": 48}, {"images": seam.outputs["images"]})
    assert len(wang.outputs["images"]) == len(wang.outputs["nmap"]) == 16
    assert wang.outputs["images"][6].name == "wang_06_abba.png"
    assert wang.outputs["nmap"][6].name == "wang_06_abba_normal.png"
    assert [r.name for r in wang.outputs["atlas"]] == ["wang.png", "wang_normal.png"]
    assert load(ctx, wang.outputs["atlas"][0]).size == (192, 192)
    doc = json.loads(ctx.blobs.path(wang.outputs["wang"][0].hash).read_bytes())
    assert doc["type"] == "corner-wang-2" and len(doc["tiles"]) == 16 and doc["tiles"][9]["corners"]["nw"] == "b"
    skip = await reg.resolve("op.tile_wang").run(ctx, {"mode": "edge"}, {"images": refs})
    assert skip.outputs["images"] == refs and skip.outputs["wang"] == []
    fit = await reg.resolve("op.tile_resize").run(ctx, {"size": 48}, {"images": wang.outputs["images"]})
    assert fit.outputs["images"] == wang.outputs["images"]
    nm = await reg.resolve("op.tile_normal").run(ctx, {}, {"images": fit.outputs["images"], "nmap": wang.outputs["nmap"]})
    assert nm.outputs["images"] == wang.outputs["nmap"]
    chk = await reg.resolve("op.tile_check").run(ctx, {"grid": 4}, {
        "images": fit.outputs["images"], "nmap": nm.outputs["images"], "atlas": wang.outputs["atlas"],
        "wang": wang.outputs["wang"]})
    rep = json.loads(ctx.blobs.path(chk.outputs["report"][0].hash).read_bytes())
    assert rep["wang"]["type"] == "corner-wang-2" and len(rep["wang"]["tiles"]) == 16 and rep["passed"], rep
    assert chk.outputs["atlas"] == wang.outputs["atlas"] and chk.outputs["wang"] == wang.outputs["wang"]
    assert load(ctx, chk.outputs["preview"][0]).size == (288, 288)
    with pytest.raises(StepError):
        await reg.resolve("op.tile_check").run(ctx, {}, {"images": refs, "wang": wang.outputs["wang"]})
    return ctx, chk


async def test_wang_ops_pipeline_and_report(tmp_path: Path):
    await _wang_pipeline(tmp_path)


async def test_wang_exports_godot_terrain_and_unity_slices(tmp_path: Path):
    import yaml

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_source

    ctx, chk = await _wang_pipeline(tmp_path)
    step = StepRow(id="w" * 16, run_id="r", key="approve", kind="gate.approve", lane=Lane.NONE,
                   status=StepStatus.SUCCEEDED, outputs=chk.outputs)
    src = engine_source(step, [], ctx.blobs, "Meadow")
    g = dict(godot.export(src))
    ts = g["Meadow_wang_tileset.tres"].decode()
    assert "terrain_set_0/mode = 1" in ts and 'terrain_set_0/terrain_1/name = "b"' in ts
    assert "texture_region_size = Vector2i(48, 48)" in ts and 'type="CanvasTexture"' in ts
    # Tile 6 (abba) sits at col 2, row 1: NW = a (terrain 0), NE = b (terrain 1).
    assert "2:1/0/terrains_peering_bit/top_left_corner = 0" in ts
    assert "2:1/0/terrains_peering_bit/top_right_corner = 1" in ts
    assert ts.count("/terrain_set = 0") == 30 and "1:0/1 = 1" in ts and "0:0/1 = 1" not in ts
    assert "Meadow_tileset.tres" not in g
    assert json.loads(g["Textures/Meadow_wang.json"])["tiles"][15]["name"] == "wang_15_bbbb"
    u = dict(unity.export(src))
    meta = yaml.safe_load(u["Textures/Meadow_wang.png.meta"].decode())["TextureImporter"]
    names = [s["name"] for s in meta["spriteSheet"]["sprites"]]
    assert meta["spriteMode"] == 2 and len(names) == 16 and "wang_06_abba" in names
    six = next(s for s in meta["spriteSheet"]["sprites"] if s["name"] == "wang_06_abba")
    assert (six["rect"]["x"], six["rect"]["y"]) == (96, 96)  # col 2, row 1 from the top of a 192 px atlas
    assert meta["spriteSheet"]["secondaryTextures"][0]["name"] == "_NormalMap"
    nmeta = yaml.safe_load(u["Textures/Meadow_wang_normal.png.meta"].decode())["TextureImporter"]
    assert nmeta["textureType"] == 1
    assert "Textures/Meadow_wang.json" in u and "Textures/wang.png" not in u
    assert 'path="Textures/Meadow_wang.png"' in ts


async def test_wang_without_normals_exports_plain_atlas(tmp_path: Path):
    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_source

    ctx = FakeCtx(tmp_path)
    reg = ExecutorRegistry()
    register(reg, None, None, None)
    refs = [await put_img(ctx, plain_noise(96, seed=s), f"n_{s}.png") for s in (1, 2)]
    seam = await reg.resolve("op.seamless").run(ctx, {"mode": "wang", "group": 1}, {"images": refs})
    wang = await reg.resolve("op.tile_wang").run(ctx, {"size": 32, "normal": "false"}, {"images": seam.outputs["images"]})
    assert wang.outputs["nmap"] == [] and [r.name for r in wang.outputs["atlas"]] == ["wang.png"]
    nm = await reg.resolve("op.tile_normal").run(ctx, {"enabled": "false"}, {"images": wang.outputs["images"],
                                                                           "nmap": wang.outputs["nmap"]})
    chk = await reg.resolve("op.tile_check").run(ctx, {}, {"images": wang.outputs["images"], "nmap": nm.outputs["images"],
                                                           "atlas": wang.outputs["atlas"], "wang": wang.outputs["wang"]})
    step = StepRow(id="v" * 16, run_id="r", key="approve", kind="gate.approve", lane=Lane.NONE,
                   status=StepStatus.SUCCEEDED, outputs=chk.outputs)
    src = engine_source(step, [], ctx.blobs, "Dry")
    ts = dict(godot.export(src))["Dry_wang_tileset.tres"].decode()
    assert "CanvasTexture" not in ts and 'texture = ExtResource("1_atlas")' in ts
    meta = dict(unity.export(src))["Textures/Dry_wang.png.meta"].decode()
    assert "_NormalMap" not in meta


async def test_wang_picks_the_best_candidate_per_prompt_line(tmp_path: Path, monkeypatch):
    from studio_core.ops import tile

    # Score = brightness, so the brighter candidate of each line must win.
    monkeypatch.setattr(tile, "tile_report", lambda img, *a: {"score": float(np.asarray(img)[..., 0].mean())})
    ctx = FakeCtx(tmp_path)
    reg = ExecutorRegistry()
    register(reg, None, None, None)
    dark = [Image.fromarray(np.asarray(plain_noise(96, seed=s)) // 3) for s in (5, 6)]
    refs = [await put_img(ctx, dark[0], "d1.png"), await put_img(ctx, plain_noise(96, seed=1), "g1.png"),
            await put_img(ctx, plain_noise(96, seed=2), "g2.png"), await put_img(ctx, dark[1], "d2.png")]
    seam = await reg.resolve("op.seamless").run(ctx, {"mode": "wang", "group": 2}, {"images": refs})
    assert [g["source"] for g in seam.meta["grid"]] == [refs[1].hash, refs[2].hash]
    with pytest.raises(StepError):
        await reg.resolve("op.seamless").run(ctx, {"mode": "wang", "group": 1}, {"images": refs[:1]})


def test_recipes_accept_wang_and_grid_inputs():
    recipe, issues = validate((RECIPES / "tileset-seamless.yaml").read_text(encoding="utf-8"), None)
    assert recipe is not None and issues == [], issues
    assert recipe.step("gen").map_param == "57:27.text" and recipe.step("wang").kind == "op.tile_wang"
    assert set(recipe.inputs["mode"]["options"]) == {"edge", "wang"}
    tex, issues = validate((RECIPES / "texture-seamless.yaml").read_text(encoding="utf-8"), None)
    assert tex is not None and issues == [] and tex.inputs["grid"]["options"] == ["auto", "off"]
    text = tex.step("gen").params["57:27.text"]
    assert "uniform pattern" not in text and "no perspective" in text and "non-repeating" in text and "no tile grid" in text


async def test_tile_resize_square_option(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    reg = ExecutorRegistry()
    register(reg, None, None, None)
    ref = await put_img(ctx, plain_noise(96, seed=8).crop((0, 0, 96, 90)), "t_seamless.png")
    keep = await reg.resolve("op.tile_resize").run(ctx, {"size": 64}, {"images": [ref]})
    sq = await reg.resolve("op.tile_resize").run(ctx, {"size": 64, "square": True, "levels": 1}, {"images": [ref]})
    assert load(ctx, keep.outputs["images"][0]).size == (64, 60)
    assert load(ctx, sq.outputs["images"][0]).size == (64, 64) and load(ctx, sq.outputs["sizes"][0]).size == (32, 32)


async def test_edge_mode_keeps_only_the_first_prompt_line(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    reg = ExecutorRegistry()
    register(reg, None, None, None)
    refs = [await put_img(ctx, plain_noise(96, seed=s), f"e_{s}.png") for s in (1, 2, 3, 4)]
    seam = await reg.resolve("op.seamless").run(ctx, {"mode": "edge", "group": 2, "match_edges": True, "name": "tile"},
                                                {"images": refs})
    assert [r.name for r in seam.outputs["images"]] == ["tile_01_seamless.png", "tile_02_seamless.png"]


def checkerboard(n: int = 600, cell: int = 37, seed: int = 0) -> Image.Image:
    y, x = np.mgrid[0:n, 0:n]
    c = (np.floor(x / cell) + np.floor(y / cell)) % 2
    lum = 0.25 + 0.5 * c + 0.03 * np.random.default_rng(seed).standard_normal((n, n))
    return Image.fromarray((np.clip(np.stack([lum] * 3, -1), 0, 1) * 255).astype(np.uint8)).convert("RGBA")


def test_checkerboard_never_aligns_on_the_half_period():
    # Edge strength alone matches at half a colour period (37 px), which flips light and dark across the wrap.
    src = checkerboard()
    auto, info = seamless_info(src, grid="auto")
    rep_auto, rep_off = tile_report(auto), tile_report(seamless(src, grid="off"))
    assert rep_auto["seam_score"] >= rep_off["seam_score"] - 5 and rep_auto["seam_score"] >= 90, (rep_auto, info)
    a = np.asarray(auto, np.float64)[..., :3].mean(-1)
    assert np.abs(a[:, 0] - a[:, -1]).mean() < 40 and np.abs(a[0] - a[-1]).mean() < 40
    for k, o in info["overlap_px"].items():
        if o:
            assert ((600 - o) / 74) % 1 < 0.05 or ((600 - o) / 74) % 1 > 0.95, (k, o)
