import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import StepError
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.pbr import ROLES, Pbr, PbrParams, derive
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def mortar_mask(size: int = 128) -> np.ndarray:
    yy, xx = np.mgrid[:size, :size]
    return (yy % 16 < 2) | ((xx + (yy // 16 % 2) * 16) % 32 < 2)


def bricks(size: int = 128) -> Image.Image:
    """Tileable brick pattern: light bricks, dark mortar lines, periodic so the wrap is exact."""
    mortar = mortar_mask(size)
    noise = np.random.default_rng(1).normal(0, 6, (size, size))
    base = np.where(mortar, 60, 170) + noise
    rgb = np.stack([base * 1.0, base * 0.55, base * 0.4], -1)
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")


def test_maps_have_expected_modes_and_ranges():
    maps = derive(bricks(), PbrParams())
    assert set(maps) == set(ROLES)
    assert maps["albedo"].mode == "RGB" and maps["normal"].mode == "RGB"
    for role in ("height", "roughness", "metallic", "ao"):
        assert maps[role].mode == "L" and maps[role].size == (128, 128)
    assert np.asarray(maps["metallic"]).max() == 0
    n = np.asarray(maps["normal"]).astype(np.float32) / 127.5 - 1
    assert np.allclose(np.linalg.norm(n, axis=-1), 1, atol=0.02) and n[..., 2].min() > 0


def test_mortar_is_low_rough_and_occluded():
    img = bricks()
    maps = derive(img, PbrParams())
    mortar = mortar_mask()
    brick = ~mortar
    h, ao, r = (np.asarray(maps[k]).astype(float) for k in ("height", "ao", "roughness"))
    assert h[mortar].mean() < h[brick].mean()
    assert ao[mortar].mean() < ao[brick].mean()
    assert r[mortar].mean() > r[brick].mean()
    inv = np.asarray(derive(img, PbrParams(invert_height=True))["height"]).astype(float)
    assert inv[mortar].mean() > inv[brick].mean()


def test_seamless_maps_do_not_care_where_the_tile_is_cut():
    img = bricks()
    shifted = Image.fromarray(np.roll(np.asarray(img), (37, 53), (0, 1)))
    for seamless in (True, False):
        p = PbrParams(seamless=seamless, metallic="auto")
        a, b = derive(img, p), derive(shifted, p)
        diffs = [np.abs(np.roll(np.asarray(a[r]).astype(int), (37, 53), (0, 1)) - np.asarray(b[r])).max()
                 for r in ("normal", "height", "roughness", "metallic", "ao")]
        assert (max(diffs) <= 1) == seamless, (seamless, diffs)


def test_metallic_auto_picks_bright_grey_not_paint():
    a = np.zeros((64, 64, 3), np.uint8)
    a[:, :32] = (215, 215, 220)
    a[:, 32:] = (220, 40, 30)
    met = np.asarray(derive(Image.fromarray(a), PbrParams(metallic="auto"))["metallic"]).astype(float)
    assert met[:, 4:28].mean() > 150 and met[:, 36:60].mean() < 30


def test_bad_params_are_rejected():
    with pytest.raises(StepError):
        PbrParams.parse({"metallic": "chrome"})
    with pytest.raises(StepError):
        PbrParams.parse({"green": "sideways"})


async def test_op_writes_maps_preview_and_material(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    ref = await put_img(ctx, bricks(), "stone.png")
    res = await Pbr().run(ctx, {"metallic": "auto"}, {"images": [ref]})
    assert [r.name for r in res.outputs["maps"]] == [f"stone_{r}.png" for r in ROLES]
    grid = Image.open(ctx.blobs.path(res.outputs["preview"][0].hash))
    assert grid.width > grid.height
    doc = json.loads(ctx.blobs.path(res.outputs["material"][0].hash).read_bytes())
    assert doc["materials"][0]["name"] == "stone" and set(doc["materials"][0]["maps"]) == set(ROLES)
    off = await Pbr().run(ctx, {"enabled": False}, {"images": [ref]})
    assert off.outputs == {"maps": [], "preview": [], "material": []}


async def test_export_unity_mat_and_godot_tres(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx = FakeCtx(tmp_path)
    ref = await put_img(ctx, bricks(), "stone.png")
    res = await Pbr().run(ctx, {}, {"images": [ref]})
    step = _row("approve", "gate.approve", {"preview": res.outputs["preview"], "maps": res.outputs["maps"],
                                             "material": res.outputs["material"]})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Stone")
    assert [n for n, _ in src.pbr] == ["stone"]

    u = dict(unity.export(src))
    mat = u["PBR/Stone.mat"].decode()
    assert "933532a4fcc9baf4fa0491de14d08ed7" in mat and "_NORMALMAP" in mat and "_METALLICSPECGLOSSMAP" in mat
    normal_guid = u["PBR/Stone_normal.png.meta"].decode().split("guid: ")[1].split("\n")[0]
    assert f"_BumpMap:\n        m_Texture: {{fileID: 2800000, guid: {normal_guid}, type: 3}}" in mat
    assert "textureType: 1" in u["PBR/Stone_normal.png.meta"].decode()
    assert "sRGBTexture: 0" in u["PBR/Stone_ao.png.meta"].decode()
    assert "sRGBTexture: 1" in u["PBR/Stone_albedo.png.meta"].decode()
    assert "mainObjectFileID: 2100000" in u["PBR/Stone.mat.meta"].decode()
    packed = Image.open(__import__("io").BytesIO(u["PBR/Stone_metallic_smoothness.png"]))
    assert packed.mode == "RGBA" and np.asarray(packed)[..., 0].max() == 0
    assert "PBR/Stone.mat" in u["README.txt"].decode()

    g = dict(godot.export(src))
    tres = g["PBR/Stone.tres"].decode()
    assert 'type="StandardMaterial3D"' in tres and 'path="Stone_normal.png"' in tres
    assert "metallic = 1.0" in tres and "normal_enabled = true" in tres and "ao_enabled = true" in tres
    assert {f"PBR/Stone_{r}.png" for r in ROLES} <= set(g)


def test_recipes_load_and_lint():
    r = load_recipe_file(RECIPES / "pbr-material.yaml")
    assert [s.id for s in r.steps] == ["pbr", "approve"]
    for name in ("pbr-material.yaml", "texture-seamless.yaml"):
        _, issues = validate((RECIPES / name).read_text(encoding="utf-8"), None)
        assert issues == [], (name, issues)
