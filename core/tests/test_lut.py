import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import StepError
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.lut import (FitLut, LutPrep, apply_lut, cube_text, delta_e, detect_seamless, fit_lut,
                                 identity_lut, parse_cube, strip_image, strip_to_lut, variant_names)
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def night(c: np.ndarray) -> np.ndarray:
    """A smooth, non-linear grade: darker, gamma, blue-shifted channel mix."""
    m = np.array([[0.8, 0.1, 0.05], [0.05, 0.75, 0.1], [0.1, 0.15, 1.05]])
    return np.clip((c ** 1.3) @ m.T * np.array([0.7, 0.75, 1.0]) + np.array([0.02, 0.03, 0.08]), 0, 1)


def tileable(n: int = 192) -> Image.Image:
    """Opaque texture built from whole-period sines, so it wraps without a seam."""
    y, x = np.mgrid[0:n, 0:n] / n * 2 * np.pi
    a = np.stack([0.5 + 0.3 * np.sin(2 * x) * np.cos(y), 0.5 + 0.25 * np.sin(3 * y + x),
                  0.4 + 0.3 * np.cos(x + 2 * y)], -1)
    return Image.fromarray(np.clip(np.round(a * 255), 0, 255).astype(np.uint8), "RGB")


def graded(img: Image.Image, scale: int = 4) -> Image.Image:
    """What Klein returns: the night look at ~1MP, a little noise, and a wrap seam."""
    a = night(np.asarray(img.convert("RGB"), np.float64) / 255.0)
    big = Image.fromarray(np.round(a * 255).astype(np.uint8), "RGB").resize(
        (img.width * scale, img.height * scale), Image.Resampling.BICUBIC)
    b = np.asarray(big, np.float64) / 255.0
    b += np.random.default_rng(1).normal(0, 0.01, b.shape)
    b[:, :scale * 6] += 0.08
    return Image.fromarray(np.clip(np.round(b * 255), 0, 255).astype(np.uint8), "RGB")


def test_fit_recovers_known_transform():
    rng = np.random.default_rng(0)
    src = rng.random((120000, 3))
    lut = fit_lut(src, night(src))
    test = rng.random((20000, 3))
    out = apply_lut(lut, test)
    assert np.abs(out - night(test)).mean() < 0.003
    assert delta_e(out, night(test))["mean"] < 0.5


def test_fit_fills_unseen_cells_from_neighbours():
    rng = np.random.default_rng(2)
    src = rng.random((60000, 3))
    src[:, 2] = np.clip(src[:, 0] * 0.8 + 0.1 + rng.normal(0, 0.02, len(src)), 0, 1)
    lut = fit_lut(src, night(src))
    assert np.isfinite(lut).all()
    test = rng.random((20000, 3))
    assert np.abs(apply_lut(lut, test) - night(test)).mean() < 0.06
    grey = apply_lut(lut, np.repeat(np.linspace(0, 1, 33)[:, None], 3, 1)).mean(1)
    assert (np.diff(grey) > -1e-3).all()


def test_fit_ignores_added_content():
    rng = np.random.default_rng(3)
    src = rng.random((80000, 3)) * 0.8
    dst = night(src)
    dst[:4000] = 0.97
    lut = fit_lut(src, dst)
    test = rng.random((5000, 3)) * 0.8
    assert np.abs(apply_lut(lut, test) - night(test)).mean() < 0.01


def test_cube_format_roundtrip():
    lut = fit_lut(np.random.default_rng(4).random((20000, 3)), night(np.random.default_rng(4).random((20000, 3))))
    text = cube_text(lut, "tile_night")
    lines = text.splitlines()
    assert lines[0] == 'TITLE "tile_night"' and "LUT_3D_SIZE 33" in lines
    assert "DOMAIN_MIN 0.0 0.0 0.0" in lines and "DOMAIN_MAX 1.0 1.0 1.0" in lines
    data = [ln for ln in lines if ln and ln[0].isdigit()]
    assert len(data) == 33 ** 3
    ident = cube_text(identity_lut(), "id").splitlines()
    rows = [ln for ln in ident if ln and ln[0].isdigit()]
    assert rows[0] == "0.000000 0.000000 0.000000" and rows[1] == "0.031250 0.000000 0.000000"
    assert rows[33] == "0.000000 0.031250 0.000000" and rows[33 * 33] == "0.000000 0.000000 0.031250"
    assert np.allclose(parse_cube(text), lut, atol=1e-6)


def test_strip_layout():
    strip = strip_image(identity_lut())
    assert strip.size == (1024, 32)
    a = np.asarray(strip).astype(int)
    for r, g, b in ((0, 0, 0), (31, 0, 0), (0, 31, 0), (0, 0, 31), (5, 17, 9)):
        assert np.abs(a[g, b * 32 + r] - np.round(np.array([r, g, b]) / 31 * 255)).max() <= 1
    back = strip_to_lut(strip)
    assert back.shape == (32, 32, 32, 3) and np.allclose(back, identity_lut(32), atol=1 / 255)


def test_variant_names():
    assert variant_names("") == ["dusk", "night", "winter", "autumn"]
    assert variant_names("Night, blue moon\nnight\n\n  rainy day: grey  ") == ["night", "night_2", "rainy_day"]
    assert variant_names("!!!") == ["variant_01"]


def test_detect_seamless():
    assert detect_seamless(tileable())
    a = np.asarray(tileable(), np.float32).copy()
    a[:, :96] *= 0.4
    assert not detect_seamless(Image.fromarray(a.astype(np.uint8), "RGB"))
    assert not detect_seamless(tileable().convert("RGBA").resize((40, 40)))


async def test_prep_flattens_alpha_and_passes_opaque(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    opaque = await put_img(ctx, tileable(64), "tile.png")
    res = await LutPrep().run(ctx, {}, {"images": [opaque]})
    assert res.outputs["images"] == [opaque]
    prop = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    prop.paste((200, 40, 40, 255), (16, 16, 48, 48))
    res = await LutPrep().run(ctx, {}, {"images": [await put_img(ctx, prop, "prop.png")]})
    with Image.open(ctx.blobs.path(res.outputs["images"][0].hash)) as im:
        assert im.mode == "RGB" and im.getpixel((2, 2)) == (128, 128, 128) and im.getpixel((30, 30)) == (200, 40, 40)


async def _fit(tmp_path: Path, **params):
    ctx = FakeCtx(tmp_path)
    src = tileable()
    orig = await put_img(ctx, src, "tile.png")
    edits = [await put_img(ctx, graded(src), "edit_0.png"), await put_img(ctx, graded(src, 3), "edit_1.png")]
    res = await FitLut().run(ctx, {"variants": "night, moon\nlate night", **params},
                             {"images": [orig], "edits": edits})
    return ctx, res


async def test_fit_lut_op_outputs(tmp_path: Path):
    ctx, res = await _fit(tmp_path)
    assert [r.name for r in res.outputs["images"]] == ["tile_night.png", "tile_late_night.png"]
    assert [r.name for r in res.outputs["cube"]] == ["tile_night.cube", "tile_late_night.cube"]
    assert [r.name for r in res.outputs["strip"]] == ["tile_night_lut.png", "tile_late_night_lut.png"]
    doc = json.loads(ctx.blobs.path(res.outputs["lut"][0].hash).read_bytes())
    assert doc["type"] == "asset-studio/color-lut" and doc["seamless"] is True
    v = doc["variants"][0]
    assert v["delta_e"]["mean"] < 3 and v["seam"]["before"] > 1.5 and v["seam"]["after"] <= 1.2
    with Image.open(ctx.blobs.path(res.outputs["images"][0].hash)) as im:
        assert im.size == (192, 192)
    with Image.open(ctx.blobs.path(res.outputs["strip"][0].hash)) as im:
        assert im.size == (1024, 32)
    with Image.open(ctx.blobs.path(res.outputs["preview"][0].hash)) as im:
        assert im.width > im.height
    lut = parse_cube(ctx.blobs.path(res.outputs["cube"][0].hash).read_text())
    probe = np.asarray(tileable(), np.float64)[::7, ::7].reshape(-1, 3) / 255
    assert np.abs(apply_lut(lut, probe) - night(probe)).mean() < 0.02


async def test_fit_lut_final_lut_and_bad_params(tmp_path: Path):
    ctx, res = await _fit(tmp_path, final="lut", seamless="off")
    doc = json.loads(ctx.blobs.path(res.outputs["lut"][0].hash).read_bytes())
    assert doc["seamless"] is False and doc["variants"][0]["source"] == "lut"
    assert res.outputs["images"][0].hash == res.outputs["applied"][0].hash
    with pytest.raises(StepError):
        await _fit(tmp_path, final="nope")


async def test_export_unity_and_godot(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx, res = await _fit(tmp_path)
    step = _row("approve", "gate.approve", {k: res.outputs[k] for k in ("preview", "images", "applied", "cube",
                                                                         "strip", "lut")})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Grass")
    assert [n for n, *_ in src.luts] == ["night", "late_night"] and src.lut_seamless

    u = dict(unity.export(src))
    assert {"Seasons/Grass_night.png", "Seasons/LUT/Grass_night_lut.png", "Seasons/LUT/Grass_night.cube"} <= set(u)
    assert "wrapU: 0" in u["Seasons/Grass_night.png.meta"].decode()
    meta = u["Seasons/LUT/Grass_night_lut.png.meta"].decode()
    for want in ("sRGBTexture: 0", "enableMipMap: 0", "wrapU: 1", "textureType: 0", "spriteMode: 0",
                 "textureCompression: 0"):
        assert want in meta, want
    import io

    flipped = np.asarray(Image.open(io.BytesIO(u["Seasons/LUT/Grass_night_lut.png"])))
    raw = np.asarray(Image.open(ctx.blobs.path(res.outputs["strip"][0].hash)).convert("RGB"))
    assert np.array_equal(flipped, raw[::-1])
    assert "Color Lookup" in u["README.txt"].decode()

    g = dict(godot.export(src))
    imp = g["Seasons/Grass_night_lut.png.import"].decode()
    assert 'importer="3d_texture"' in imp and "slices/horizontal=32" in imp and "slices/vertical=1" in imp
    env = g["Seasons/Grass_night_environment.tres"].decode()
    assert env.startswith('[gd_resource type="Environment"')
    assert 'type="Texture3D" path="Grass_night_lut.png"' in env and "adjustment_enabled = true" in env
    assert "Seasons/Grass_late_night.png" in g and "Texture3D" in g["README.txt"].decode()


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "time-season-pack.yaml")
    assert [s.id for s in r.steps] == ["prep", "edit", "fit", "approve"]
    assert r.step("edit").map_param == "75:74.text"
    _, issues = validate((RECIPES / "time-season-pack.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
