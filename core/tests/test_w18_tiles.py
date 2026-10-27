import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import ExecutorRegistry, StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.ops import register
from studio_core.ops.tile import (box_blur, height_normal, match_edges, seamless, soft_blur, tile_pixel, tile_report,
                                  wrap_resize)

from test_ops_executors import FakeCtx, load, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def _img(a: np.ndarray) -> Image.Image:
    a = (a - a.min()) / max(1e-9, a.max() - a.min())
    return Image.fromarray((a * 255).astype(np.uint8)).convert("RGBA")


def tileable_noise(n: int = 256, r: int = 3, seed: int = 1) -> Image.Image:
    return _img(soft_blur(np.random.default_rng(seed).random((n, n, 3)), r))


def plain_noise(n: int = 256, r: int = 6, seed: int = 2) -> Image.Image:
    """A crop of smooth noise: continuous inside, but its opposite edges do not meet."""
    return _img(soft_blur(np.random.default_rng(seed).random((n * 2, n * 2, 3)), r)[:n, :n])


def wrap_step(img: Image.Image) -> float:
    a = np.asarray(img, np.float64)[..., :3]
    return float(max(np.abs(a[:, 0] - a[:, -1]).mean(), np.abs(a[0] - a[-1]).mean()))


def inner_step(img: Image.Image) -> float:
    a = np.asarray(img, np.float64)[..., :3]
    return float(max(np.abs(np.diff(a, axis=1)).mean(), np.abs(np.diff(a, axis=0)).mean()))


def test_box_blur_wraps():
    a = np.zeros((16, 16))
    a[0, 0] = 1.0
    b = box_blur(a, 1)
    assert b[15, 15] == pytest.approx(1 / 9) and b[1, 1] == pytest.approx(1 / 9)
    assert b.sum() == pytest.approx(1.0)


def test_seamless_closes_the_wrap_seam():
    src = plain_noise()
    out = seamless(src)
    before, after = wrap_step(src), wrap_step(out)
    assert after < before * 0.25, (before, after)
    assert after < inner_step(out) * 1.6
    assert out.size == (224, 224)
    # Outside the overlap window every pixel is the source pixel at the same place: nothing blurred or shifted.
    a, b = np.asarray(src, np.int16), np.asarray(seamless(src, flatten=0))
    assert np.abs(a[32:224, 32:224] - b[32:224, 32:224]).max() <= 1
    assert tile_report(out)["duplicate"] < 0.01


def test_odd_sizes_and_alpha_input():
    rng = np.random.default_rng(7)
    a = np.zeros((301, 513, 4), np.uint8)
    a[..., :3] = (soft_blur(rng.random((301, 513, 3)), 4) * 255).astype(np.uint8)
    a[..., 3] = rng.choice([0, 255], (301, 513))
    out = seamless(Image.fromarray(a, "RGBA"))
    assert out.size == (513 - 64, 301 - 38) and set(np.unique(np.asarray(out)[..., 3])) == {255}
    assert wrap_step(out) < inner_step(out) * 1.8


def test_duplicated_tile_is_flagged():
    half = np.asarray(tileable_noise(128), np.uint8)
    dup = Image.fromarray(np.tile(half, (2, 2, 1)), "RGBA")
    rep = tile_report(dup)
    assert "duplicate" in rep["issues"] and rep["score"] < 20 and rep["duplicate"] > 0.9, rep
    assert tile_report(tileable_noise())["duplicate"] < 0.01


def test_tile_check_scores_noise_high_and_gradient_low():
    good = tile_report(tileable_noise())
    assert good["score"] >= 85 and good["passed"] and not good["issues"], good
    yy, xx = np.mgrid[0:256, 0:256] / 255
    ramp = _img(np.stack([xx, xx * 0.5 + 0.2, yy * 0.3], -1))
    bad = tile_report(ramp)
    assert bad["score"] < 20 and not bad["passed"] and {"seam", "gradient"} <= set(bad["issues"]), bad
    raw, fixed = tile_report(plain_noise()), tile_report(seamless(plain_noise()))
    assert raw["seam_score"] < 50 < fixed["seam_score"], (raw, fixed)


def test_derived_maps_stay_tileable():
    src = seamless(plain_noise())
    for out in (height_normal(src), wrap_resize(src, 128, 128), wrap_resize(src, 28, 28), wrap_resize(src, 300, 300),
                tile_pixel(src, 64, 12)[0]):
        assert wrap_step(out) < inner_step(out) * 1.8, out.size
    n = np.asarray(height_normal(tileable_noise()), np.float64)[..., :3] / 127.5 - 1
    assert np.allclose(np.linalg.norm(n, axis=-1), 1, atol=0.03)
    flat = np.asarray(height_normal(Image.new("RGBA", (32, 32), (90, 90, 90, 255))))
    assert (np.abs(flat[..., :3].astype(int) - [128, 128, 255]) <= 1).all()
    px, pal = tile_pixel(src, 32, 8)
    assert px.size == (32, 32) and len({tuple(c) for c in np.asarray(px)[..., :3].reshape(-1, 3)}) <= 8 >= len(pal)


def test_match_edges_variants_share_borders():
    base = seamless(plain_noise(seed=3))
    var = match_edges(plain_noise(seed=4), base)
    a, b = np.asarray(base, np.int16), np.asarray(var, np.int16)
    assert var.size == base.size
    assert np.abs(a[:, [0, -1]] - b[:, [0, -1]]).mean() < 1 and np.abs(a[[0, -1]] - b[[0, -1]]).mean() < 1
    assert np.abs(a[40:184, 40:184] - b[40:184, 40:184]).mean() > 10
    # Only the join windows come from the base: no wide identical strips repeating across variants.
    assert (np.abs(a - b).max(-1) <= 1).mean() < 0.2
    w = base.width
    pair = Image.new("RGBA", (w * 2, w))
    pair.paste(base, (0, 0))
    pair.paste(var, (w, 0))
    p = np.asarray(pair, np.float64)[..., :3]
    assert np.abs(p[:, w - 1] - p[:, w]).mean() < inner_step(base) * 1.8
    assert wrap_step(var) < inner_step(var) * 1.8


async def test_executors_and_report(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    reg = ExecutorRegistry()
    register(reg, None, None, None)
    refs = [await put_img(ctx, plain_noise(128, seed=s), f"z_{s}.png") for s in (5, 6)]
    seam = await reg.resolve("op.seamless").run(ctx, {"match_edges": True, "name": "tile"}, {"images": refs})
    assert [r.name for r in seam.outputs["images"]] == ["tile_01_seamless.png", "tile_02_seamless.png"]
    assert seam.meta["tileable"] and seam.meta["match_edges"]
    fit = await reg.resolve("op.tile_resize").run(ctx, {"size": 64, "levels": 2}, {"images": seam.outputs["images"]})
    assert [load(ctx, r).size for r in fit.outputs["images"]] == [(64, 64)] * 2
    assert [r.name for r in fit.outputs["sizes"]][:2] == ["tile_01_seamless_32.png", "tile_01_seamless_16.png"]
    nm = await reg.resolve("op.tile_normal").run(ctx, {}, {"images": fit.outputs["images"]})
    assert nm.outputs["images"][0].name == "tile_01_seamless_normal.png" and nm.meta["normal"]
    off = await reg.resolve("op.tile_normal").run(ctx, {"enabled": "false"}, {"images": fit.outputs["images"]})
    assert off.outputs["images"] == []
    assert (await reg.resolve("op.tile_pixel").run(ctx, {"size": 0}, {"images": refs})).outputs["images"] == []
    with pytest.raises(StepError):
        await reg.resolve("op.tile_pixel").run(ctx, {"size": 4}, {"images": refs})
    chk = await reg.resolve("op.tile_check").run(ctx, {"grid": 4}, {"images": fit.outputs["images"],
                                                                     "nmap": nm.outputs["images"]})
    assert chk.outputs["images"] == fit.outputs["images"] and chk.outputs["nmap"] == nm.outputs["images"]
    assert load(ctx, chk.outputs["preview"][0]).size == (256, 256)
    assert load(ctx, chk.outputs["atlas"][0]).size == (128, 64)
    rep = json.loads(ctx.blobs.path(chk.outputs["report"][0].hash).read_bytes())
    assert rep["tileable"] and len(rep["textures"]) == 2 and rep["score"] == chk.meta["score"]
    assert rep["normal"] == [r.hash for r in nm.outputs["images"]]
    with pytest.raises(StepError) as ei:
        await reg.resolve("op.tile_check").run(ctx, {"min_score": 100.1, "fail": True}, {"images": refs})
    assert ei.value.code == "tile_check_failed"


def test_catalog_and_recipes():
    for kind in ("op.seamless", "op.tile_resize", "op.tile_normal", "op.tile_pixel", "op.tile_check"):
        assert CATALOG[kind]["label"]
    assert {"preview", "report", "nmap"} <= set(CATALOG["op.tile_check"]["out"])
    for rid in ("texture-seamless", "tileset-seamless"):
        recipe, issues = validate((RECIPES / f"{rid}.yaml").read_text(encoding="utf-8"), None)
        assert recipe is not None and issues == [], (rid, issues)
        assert "seamless tileable texture" in recipe.step("gen").params["57:27.text"]


async def test_engine_and_bundle_export_of_tile_step(tmp_path: Path):
    import re

    import yaml

    from studio_core.contracts import Lane, StepRow, StepStatus
    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source, export_bundle, export_engine

    ctx = FakeCtx(tmp_path)
    reg = ExecutorRegistry()
    register(reg, None, None, None)
    tex = await put_img(ctx, seamless(plain_noise(64)), "texture_seamless.png")
    nm = (await reg.resolve("op.tile_normal").run(ctx, {}, {"images": [tex]})).outputs["images"]
    chk = await reg.resolve("op.tile_check").run(ctx, {}, {"images": [tex], "nmap": nm})
    step = StepRow(id="s" * 16, run_id="r", key="approve", kind="gate.approve", lane=Lane.NONE,
                   status=StepStatus.SUCCEEDED, outputs=chk.outputs)
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Moss")
    assert [(n, role) for n, _, role in src.textures] == [("texture_seamless.png", "color"),
                                                          ("texture_seamless_normal.png", "normal")]
    files = dict(unity.export(src))
    meta = files["Textures/texture_seamless.png.meta"].decode()
    doc = yaml.safe_load(meta)["TextureImporter"]
    assert doc["textureSettings"]["wrapU"] == 0 and doc["textureType"] == 0 and doc["spriteMode"] == 0
    assert doc["mipmaps"]["enableMipMap"] == 1
    ndoc = yaml.safe_load(files["Textures/texture_seamless_normal.png.meta"].decode())["TextureImporter"]
    assert ndoc["textureType"] == 1 and ndoc["textureSettings"]["wrapV"] == 0 and ndoc["mipmaps"]["sRGBTexture"] == 0
    g = dict(godot.export(src))
    mat = g["Moss_material.tres"].decode()
    assert "texture_repeat = true" in mat and 'path="Textures/texture_seamless_normal.png"' in mat
    assert re.search(r"stretch_mode = 1", g["Moss_tiled.tscn"].decode())
    out = export_engine(step, [], ctx.blobs, tmp_path / "exp", "Moss", "godot")
    assert any(f.endswith("Moss_material.tres") for f in out["files"])
    raw = export_bundle(step, ctx.blobs, tmp_path / "exp", "Moss", None)
    assert {Path(f).name for f in raw["files"]} >= {"texture_seamless.png", "tile_check.json", "tile_preview.png"}


async def test_tileset_atlas_exports_as_godot_tileset(tmp_path: Path):
    import yaml

    from studio_core.contracts import Lane, StepRow, StepStatus
    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_source

    ctx = FakeCtx(tmp_path)
    reg = ExecutorRegistry()
    register(reg, None, None, None)
    refs = [await put_img(ctx, plain_noise(96, seed=s), f"g{s}.png") for s in (1, 2, 3)]
    seam = await reg.resolve("op.seamless").run(ctx, {"match_edges": "true", "name": "tile"}, {"images": refs})
    chk = await reg.resolve("op.tile_check").run(ctx, {}, {"images": seam.outputs["images"]})
    step = StepRow(id="t" * 16, run_id="r", key="approve", kind="gate.approve", lane=Lane.NONE,
                   status=StepStatus.SUCCEEDED, outputs=chk.outputs)
    src = engine_source(step, [], ctx.blobs, "Grass")
    assert [role for _, _, role in src.textures].count("atlas") == 1
    g = dict(godot.export(src))
    ts = g["Grass_tileset.tres"].decode()
    assert "texture_region_size = Vector2i(84, 84)" in ts and "2:0/0 = 0" in ts and "3:0/0" not in ts
    assert "texture_repeat = 2" in g["Grass_tiled.tscn"].decode()
    u = dict(unity.export(src))
    atlas_meta = yaml.safe_load(u["Textures/tileset.png.meta"].decode())["TextureImporter"]
    assert atlas_meta["textureSettings"]["wrapU"] == 1
    # "false" from a recipe placeholder must not switch edge matching on.
    off = await reg.resolve("op.seamless").run(ctx, {"match_edges": "false"}, {"images": refs})
    assert not off.meta["match_edges"]
