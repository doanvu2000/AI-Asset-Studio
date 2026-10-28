import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import ExecutorRegistry, StepError
from studio_core.engine.library import validate
from studio_core.engine.recipe import RecipeError, load_recipe_file
from studio_core.ops import register
from studio_core.ops.parallax import (ParallaxLayers, clamp_height, cutout, ground_fill, layer_names, scroll_factors, seam_x,
                                      seamless_x)
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
H, W = 160, 384


def sky_img(seam: bool = True) -> Image.Image:
    y = np.mgrid[0:H, 0:W][0] / H
    a = np.stack([0.35 + 0.3 * y, 0.55 + 0.25 * y, np.full((H, W), 0.9)], -1)
    if seam:
        a[:, :30] += 0.15
    return Image.fromarray(np.clip(np.round(a * 255), 0, 255).astype(np.uint8), "RGB")


def layer_raw(k: int) -> Image.Image:
    """A band rising from the bottom on flat white, deliberately not wrapping (the ridge phase jumps at x = W)."""
    rng = np.random.default_rng(k)
    y, x = np.mgrid[0:H, 0:W]
    ridge = H * (0.3 + 0.12 * k) + 18 * np.sin(x / (17.0 + 5 * k)) + x * 0.05
    band = y > ridge
    col = np.array([0.15 + 0.1 * k, 0.35, 0.2]) + rng.normal(0, 0.02, (H, W, 3))
    a = np.where(band[..., None], col, 1.0)
    return Image.fromarray(np.clip(np.round(a * 255), 0, 255).astype(np.uint8), "RGB")


def birefnet(raw: Image.Image, empty: bool = False) -> Image.Image:
    a = np.asarray(raw.convert("RGB"), np.float32) / 255
    alpha = np.zeros(a.shape[:2]) if empty else (np.abs(a - 1).sum(-1) > 0.2).astype(np.float32)
    rgba = np.concatenate([a, alpha[..., None]], -1)
    return Image.fromarray(np.round(rgba * 255).astype(np.uint8), "RGBA")


def test_layer_names_and_scroll():
    assert layer_names("far, mountains\nmid: hills\nSky, x\nmid") == ["far", "mid", "layer_03", "mid_2"]
    assert layer_names("") == ["far", "mid", "near", "fg"]
    assert scroll_factors(["far", "mid", "near", "fg"]) == [0.05, 0.2, 0.5, 0.8, 1.25]
    assert scroll_factors(["far", "mid", "near"]) == [0.05, 0.2, 0.5, 0.8]
    assert scroll_factors(["a", "b"], "0 0.3, 0.9") == [0.0, 0.3, 0.9]
    with pytest.raises(StepError):
        scroll_factors(["a", "b"], "0.1 0.2")


def test_seamless_x_wraps_and_keeps_empty_top():
    raw = layer_raw(2)
    a, used = cutout(raw, None, "auto")
    assert used == "key" and a[0, :, 3].max() == 0 and a[-1, :, 3].min() > 0.9
    assert seam_x(a) > 1.5
    out = seamless_x(a, 512, 213)
    assert out.shape == (213, 512, 4)
    assert seam_x(out) <= 1.2 and out[:5, :, 3].max() == 0
    sky = np.asarray(sky_img(), np.float32) / 255
    sky = seamless_x(np.concatenate([sky, np.ones((H, W, 1), np.float32)], -1), 512, 213)
    assert seam_x(sky) <= 1.2 and sky[..., 3].min() > 0.99


def test_cutout_prefers_birefnet_unless_degenerate():
    raw = layer_raw(0)
    assert cutout(raw, birefnet(raw), "auto")[1] == "birefnet"
    assert cutout(raw, birefnet(raw, empty=True), "auto")[1] == "key"
    assert cutout(raw, birefnet(raw), "key")[1] == "key"
    a, _ = cutout(raw, birefnet(raw), "birefnet")
    edge = (a[..., 3] > 0.05) & (a[..., 3] < 0.95)
    assert not edge.any() or a[..., :3][edge].mean() < 0.9


def test_ground_fill_closes_gap_under_band():
    a = np.zeros((40, 10, 4), np.float32)
    a[10:25, :6] = (0.2, 0.5, 0.3, 1.0)
    a[24, :6, :3] = (0.6, 0.4, 0.2)
    out = ground_fill(a)
    assert (out[25:, :6, 3] == 1).all() and np.allclose(out[39, :6, :3], (0.6, 0.4, 0.2))
    assert (out[:10, :, 3] == 0).all() and (out[:, 6:, 3] == 0).all()


def test_clamp_height_fades_tall_foreground():
    a = np.ones((100, 4, 4), np.float32)
    out = clamp_height(a, 0.3)
    assert out[:62, :, 3].max() == 0 and (out[70:, :, 3] == 1).all() and 0 < out[66, 0, 3] < 1


async def _run(tmp_path: Path, **params):
    ctx = FakeCtx(tmp_path)
    sky = await put_img(ctx, sky_img(), "sky.png")
    raws = [await put_img(ctx, layer_raw(k), f"raw_{k}.png") for k in range(4)]
    cuts = [await put_img(ctx, birefnet(layer_raw(k)), f"cut_{k}.png") for k in range(4)]
    ref = await put_img(ctx, Image.new("RGB", (32, 32), (200, 120, 60)), "ref.png")
    res = await ParallaxLayers().run(ctx, {"names": "far, x\nmid\nnear\nfg", "width": 512, **params},
                                     {"sky": [sky], "images": cuts, "raw": raws, "ref": [ref]})
    return ctx, res


async def test_op_outputs(tmp_path: Path):
    ctx, res = await _run(tmp_path)
    assert [r.name for r in res.outputs["images"]] == ["parallax_0_sky.png", "parallax_1_far.png",
                                                       "parallax_2_mid.png", "parallax_3_near.png", "parallax_4_fg.png"]
    doc = json.loads(ctx.blobs.path(res.outputs["parallax"][0].hash).read_bytes())
    assert doc["type"] == "asset-studio/parallax" and doc["size"] == [512, 213]
    assert [la["scroll"] for la in doc["layers"]] == [0.05, 0.2, 0.5, 0.8, 1.25]
    assert doc["layers"][-1]["foreground"] and doc["layers"][0]["opaque"]
    assert doc["layers"][1]["haze"] > doc["layers"][2]["haze"] > doc["layers"][3]["haze"] == 0
    assert all(la["seam_x"] <= 1.2 for la in doc["layers"]), [la["seam_x"] for la in doc["layers"]]
    for i, r in enumerate(res.outputs["images"]):
        with Image.open(ctx.blobs.path(r.hash)) as im:
            a = np.asarray(im.convert("RGBA"))
        assert a.shape == (213, 512, 4)
        assert (a[..., 3].min() == 255) if i == 0 else (a[0, :, 3].max() == 0 and a[-1, :, 3].mean() > 200)
    with Image.open(ctx.blobs.path(res.outputs["preview"][0].hash)) as im:
        assert im.width > 900


async def test_op_bad_params(tmp_path: Path):
    with pytest.raises(StepError):
        await _run(tmp_path, cutout="magic")
    with pytest.raises(StepError):
        await _run(tmp_path, scroll="1 2")
    with pytest.raises(StepError):
        await _run(tmp_path, fg_height=2)


async def test_export_unity_and_godot(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx, res = await _run(tmp_path)
    step = _row("approve", "gate.approve", {k: res.outputs[k] for k in ("preview", "images", "parallax")})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Forest")
    assert [n for n, *_ in src.parallax] == ["sky", "far", "mid", "near", "fg"] and src.parallax_size == (512, 213)

    u = dict(unity.export(src))
    assert {"Parallax/Forest_0_sky.png", "Parallax/Forest_4_fg.png", "Parallax/AssetStudioParallax.cs",
            "Parallax/AssetStudioParallax.cs.meta", "Parallax/Forest_parallax.prefab"} <= set(u)
    meta = u["Parallax/Forest_1_far.png.meta"].decode()
    for want in ("wrapU: 0", "wrapV: 1", "spriteMeshType: 0", "spriteMode: 1", "textureType: 8"):
        assert want in meta, want
    prefab = u["Parallax/Forest_parallax.prefab"].decode()
    assert prefab.count("m_DrawMode: 2") == 5 and prefab.count("MonoBehaviour:") == 5
    assert "scroll: 1.25" in prefab and "m_SortingOrder: 100" in prefab
    from studio_core.export.parallax import SCRIPT_GUID
    assert SCRIPT_GUID in prefab and SCRIPT_GUID in u["Parallax/AssetStudioParallax.cs.meta"].decode()
    assert "class AssetStudioParallax : MonoBehaviour" in u["Parallax/AssetStudioParallax.cs"].decode()
    assert "Parallax/" in u["README.txt"].decode()

    g = dict(godot.export(src))
    scene = g["Forest_parallax.tscn"].decode()
    assert scene.count('type="Parallax2D"') == 5 and "repeat_size = Vector2(512.0, 0)" in scene
    assert "scroll_scale = Vector2(0.05, 1)" in scene and "scroll_scale = Vector2(1.25, 1)" in scene
    assert 'path="Parallax/Forest_2_mid.png"' in scene and "position = Vector2(0, -213.0)" in scene
    assert "Parallax/Forest_3_near.png" in g and "Parallax2D" in g["README.txt"].decode()


def test_recipe_loads_and_lints():
    path = RECIPES / "parallax-layers.yaml"
    r = load_recipe_file(path)
    assert [s.id for s in r.steps] == ["sky", "gen", "cut", "layers", "approve"]
    assert r.step("gen").map_param == "57:27.text" and r.step("cut").map == "image"
    reg = ExecutorRegistry()
    register(reg)
    _, issues = validate(path.read_text(encoding="utf-8"), None)
    assert issues == []
    assert reg.resolve("op.parallax_layers")
    inputs = r.coerce_inputs({"prompt": "pine forest"})
    prompts = r.resolve_params(r.step("gen"), inputs)["57:27.text"]
    assert len(prompts) == 4 and "pine forest" in prompts[0] and "far, tall distant mountain" in prompts[0]
    with pytest.raises(RecipeError):
        r.coerce_inputs({"prompt": "x", "layers": "far\nnear"})
    assert len(r.resolve_params(r.step("gen"), r.coerce_inputs(
        {"prompt": "x", "layers": "a\nb\nc\nd\ne"}))["57:27.text"]) == 4
