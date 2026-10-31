import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.contracts import StepError
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.upscale import (UpscalePost, UpscalePrep, bleed_rgb, looks_pixel_art, premultiply,
                                     recombine, scale_atlas, unpremultiply)
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def disc(size: int = 64, color=(220, 40, 30)) -> Image.Image:
    """Anti-aliased disc on a transparent background whose hidden RGB is black (what most tools write)."""
    big = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse((size // 2, size // 2, size * 7 // 2, size * 7 // 2), fill=(*color, 255))
    return big.resize((size, size), Image.Resampling.LANCZOS)


def sprite() -> Image.Image:
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((3, 3, 12, 12), fill=(30, 120, 200, 255))
    d.rectangle((6, 6, 9, 9), fill=(250, 220, 40, 255))
    return img


def fake_model(rgb: Image.Image) -> Image.Image:
    return rgb.resize((rgb.width * 4, rgb.height * 4), Image.Resampling.BICUBIC)


def test_bleed_fills_hidden_rgb_with_visible_colour():
    src = disc()
    out = np.asarray(bleed_rgb(src)).astype(int)
    a = np.asarray(src)
    assert (out[a[..., 3] > 0] == a[..., :3][a[..., 3] > 0]).all()
    corner = out[0, 0]
    assert abs(corner[0] - 220) < 20 and corner[2] < 60


def test_recombine_keeps_alpha_clean_and_edges_not_dark():
    src = disc()
    res = recombine(src, fake_model(bleed_rgb(src)), 4)
    assert res.size == (256, 256) and res.mode == "RGBA"
    a = np.asarray(res).astype(int)
    assert a[0, 0, 3] == 0 and a[128, 128, 3] == 255
    edge = (a[..., 3] > 20) & (a[..., 3] < 235)
    assert edge.any()
    # A black-under-alpha source would pull these towards 0; the bled colour keeps the red.
    assert a[edge][:, 0].mean() > 180
    naive = recombine(src, fake_model(src.convert("RGB")), 4)
    assert np.asarray(naive).astype(int)[edge][:, 0].mean() < a[edge][:, 0].mean()


def test_recombine_two_x_downsizes_model_output():
    src = disc(40)
    res = recombine(src, fake_model(bleed_rgb(src)), 2)
    assert res.size == (80, 80)
    assert np.asarray(res)[..., 3].max() == 255


def test_opaque_image_stays_opaque():
    src = Image.new("RGB", (20, 10), (10, 200, 10))
    res = recombine(src, fake_model(src), 4)
    assert res.getextrema()[3] == (255, 255)


def test_pixel_art_detection():
    assert looks_pixel_art(sprite())
    assert not looks_pixel_art(disc())
    noise = Image.fromarray(np.random.default_rng(0).integers(0, 255, (32, 32, 3), dtype=np.uint8), "RGB")
    assert not looks_pixel_art(noise)


def test_premultiply_round_trip():
    src = disc()
    back = np.asarray(unpremultiply(premultiply(src))).astype(int)
    a = np.asarray(src).astype(int)
    solid = a[..., 3] > 128
    assert np.abs(back[solid] - a[solid]).max() <= 2


def test_scale_atlas_scales_pixel_fields_only():
    text = ("hero.png\nsize: 512,256\nfilter: Linear,Linear\npma: true\n"
            "arm\n  bounds: 10,20,30,40\n  offsets: 1,2,32,44\n  rotate: 90\n"
            "old\n  xy: 5, 6\n  size: 7, 8\n  orig: 9, 10\n  offset: 0, 1\n  index: -1\n")
    out = scale_atlas(text, 4)
    assert "size: 2048,1024" in out and "filter: Linear,Linear" in out and "pma: true" in out
    assert "bounds: 40,80,120,160" in out and "offsets: 4,8,128,176" in out and "rotate: 90" in out
    assert "xy: 20,24" in out and "size: 28,32" in out and "orig: 36,40" in out and "index: -1" in out


async def test_prep_and_post_route_pixel_art_to_nearest(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    smooth, px = await put_img(ctx, disc(), "orb.png"), await put_img(ctx, sprite(), "coin.png")
    prep = await UpscalePrep().run(ctx, {"mode": "auto"}, {"images": [smooth, px]})
    assert len(prep.outputs["rgb"]) == 1 and [p["ai"] for p in prep.meta["plan"]] == [True, False]
    rgb = Image.open(ctx.blobs.path(prep.outputs["rgb"][0].hash))
    assert rgb.mode == "RGB"
    up = await put_img(ctx, fake_model(rgb), "up.png")
    post = await UpscalePost().run(ctx, {"scale": 4, "mode": "auto"},
                                   {"images": [smooth, px], "upscaled": [up]})
    names = [r.name for r in post.outputs["images"]]
    assert names == ["orb_x4.png", "coin_x4.png"] and len(post.outputs["previews"]) == 2
    coin = np.asarray(Image.open(ctx.blobs.path(post.outputs["images"][1].hash)))
    assert coin.shape == (64, 64, 4) and set(np.unique(coin[..., 3])) == {0, 255}
    assert [r["method"] for r in post.meta["results"]] == ["esrgan", "nearest"]
    with pytest.raises(StepError):
        await UpscalePost().run(ctx, {"scale": 4}, {"images": [smooth, px], "upscaled": []})


async def test_atlas_pages_keep_names_and_atlas_is_rescaled(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    atlas = await ctx.blobs.put_bytes(b"page.png\nsize: 64,64\nfilter: Linear,Linear\norb\n  bounds: 0,0,64,64\n",
                                      name="hero.atlas", mime="text/plain")
    page = await put_img(ctx, disc(), "whatever.png")
    prep = await UpscalePrep().run(ctx, {}, {"atlas": [atlas, page]})
    up = await put_img(ctx, fake_model(Image.open(ctx.blobs.path(prep.outputs["rgb"][0].hash))), "u.png")
    post = await UpscalePost().run(ctx, {"scale": 2}, {"atlas": [atlas, page], "upscaled": [up]})
    out_atlas, out_page = post.outputs["atlas"]
    assert out_atlas.name == "hero.atlas" and out_page.name == "page.png"
    assert "size: 128,128" in ctx.blobs.path(out_atlas.hash).read_text() and post.outputs["images"] == []
    assert Image.open(ctx.blobs.path(out_page.hash)).size == (128, 128)


async def test_prep_needs_some_input(tmp_path: Path):
    with pytest.raises(StepError, match="image or an atlas"):
        await UpscalePrep().run(FakeCtx(tmp_path), {}, {"images": [], "atlas": []})


def test_recipe_loads():
    r = load_recipe_file(RECIPES / "upscale.yaml")
    assert [s.id for s in r.steps] == ["prep", "gen", "post", "approve"]


class FakeComfy:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, list]] = []

    def cache_material(self, params: dict) -> str:
        return "wf"

    async def run(self, ctx, params, inputs):
        from studio_core.contracts import StepResult
        self.calls.append((params, inputs["image"]))
        return StepResult(outputs={"images": list(inputs["image"])}, meta={"seconds": 1.0})


def _seedvr2_settings(tmp_path: Path, node: bool = True, models: bool = True):
    from studio_core.adapters.comfy.upscale import seedvr2_models
    from studio_core.config import Settings
    from studio_core.paths import package_dir

    comfy, mdir = tmp_path / "comfy", tmp_path / "models"
    (comfy / "custom_nodes").mkdir(parents=True)
    if node:
        (comfy / "custom_nodes" / "ComfyUI-SeedVR2_VideoUpscaler").mkdir()
    wf = package_dir() / "workflows"
    if models:
        for m in seedvr2_models(wf):
            (mdir / "SEEDVR2").mkdir(parents=True, exist_ok=True)
            (mdir / "SEEDVR2" / m).write_bytes(b"x")
    return Settings(data_dir=tmp_path / "data", comfy_dir=comfy, comfy_models_dir=mdir, comfy_workflows_dir=wf)


def test_seedvr2_detection_and_auto_engine(tmp_path: Path):
    from studio_core.adapters.comfy.upscale import resolve_engine, seedvr2_installed, seedvr2_models
    from studio_core.paths import package_dir

    assert set(seedvr2_models(package_dir() / "workflows")) == {"seedvr2_ema_3b_fp8_e4m3fn.safetensors",
                                                                "ema_vae_fp16.safetensors"}
    ok = _seedvr2_settings(tmp_path / "a")
    assert seedvr2_installed(ok) and resolve_engine("auto", ok) == "seedvr2"
    no_models = _seedvr2_settings(tmp_path / "b", models=False)
    assert not seedvr2_installed(no_models) and resolve_engine("auto", no_models) == "esrgan"
    assert not seedvr2_installed(_seedvr2_settings(tmp_path / "c", node=False))
    assert resolve_engine(None, no_models) == "esrgan" and resolve_engine("ESRGAN", ok) == "esrgan"
    with pytest.raises(StepError):
        resolve_engine("waifu2x", ok)


async def test_upscale_model_routes_engine_and_sets_factor(tmp_path: Path):
    from studio_core.adapters.comfy.upscale import SEEDVR2_FACTOR_KEY, UpscaleModelExecutor

    gens = {"comfy.upscale_esrgan": FakeComfy(), "comfy.upscale_seedvr2": FakeComfy()}
    ctx = FakeCtx(tmp_path)
    ref = await put_img(ctx, disc(), "a.png")
    ex = UpscaleModelExecutor(gens.__getitem__, _seedvr2_settings(tmp_path / "s"))
    res = await ex.run(ctx, {"engine": "seedvr2", "scale": 2, "_timeout_s": 60}, {"image": [ref]})
    params, imgs = gens["comfy.upscale_seedvr2"].calls[0]
    assert params[SEEDVR2_FACTOR_KEY] == "2 * round(min(a, b) * 2 / 2)" and params["_timeout_s"] == 60
    assert imgs == [ref] and res.meta["engine"] == "seedvr2" and res.outputs["images"] == [ref]
    await ex.run(ctx, {"engine": "esrgan", "scale": 4}, {"image": [ref]})
    assert gens["comfy.upscale_esrgan"].calls[0][0] == {}
    assert ex.cache_material({"engine": "esrgan"}) != ex.cache_material({"engine": "seedvr2"})

    bare = UpscaleModelExecutor(gens.__getitem__, _seedvr2_settings(tmp_path / "n", node=False))
    with pytest.raises(StepError, match="SeedVR2 is not installed"):
        await bare.run(ctx, {"engine": "seedvr2"}, {"image": [ref]})
    with pytest.raises(StepError, match="scale"):
        await ex.run(ctx, {"engine": "esrgan", "scale": 3}, {"image": [ref]})


def test_seedvr2_workflow_targets_the_short_edge():
    import json

    from studio_core.paths import package_dir

    wf = json.loads((package_dir() / "workflows" / "upscale_seedvr2.api.json").read_text(encoding="utf-8"))
    up = wf["6"]["inputs"]
    assert up["resolution"] == ["5", 1] and up["batch_size"] == 1
    from studio_core.adapters.comfy.upscale import seedvr2_expression
    a, b, s = 300, 513, 4
    assert eval(seedvr2_expression(s), {"min": min, "round": round, "a": a, "b": b}) == 1200
    assert eval(seedvr2_expression(2), {"min": min, "round": round, "a": 257, "b": 999}) == 514


def test_seedvr2_stays_an_optional_pack_for_upscale():
    from studio_core.bootstrap import plan

    text = (RECIPES / "upscale.yaml").read_text(encoding="utf-8")
    assert plan.recipe_packs(text) == {"upscale"}
    seed = next(p for p in plan.manifest()["packs"] if p["id"] == "seedvr2")
    assert not seed["required"] and seed["custom_nodes"][0]["name"] == "seedvr2_videoupscaler"
    assert not any(d.startswith("torch") for d in seed["custom_nodes"][0]["pip"])
    from studio_core.store.model_licenses import model_license
    assert model_license("seedvr2_ema_3b_fp8_e4m3fn.safetensors")["license"] == "Apache-2.0"


async def test_post_labels_the_engine(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    smooth = await put_img(ctx, disc(), "orb.png")
    prep = await UpscalePrep().run(ctx, {}, {"images": [smooth]})
    up = await put_img(ctx, fake_model(Image.open(ctx.blobs.path(prep.outputs["rgb"][0].hash))), "u.png")
    post = await UpscalePost().run(ctx, {"scale": 4, "engine": "seedvr2"}, {"images": [smooth], "upscaled": [up]})
    assert post.meta["results"][0]["method"] == "seedvr2"


async def test_post_records_the_engine_auto_resolved_to(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from studio_core.adapters.comfy import upscale as adapter

    ctx = FakeCtx(tmp_path)
    src = await put_img(ctx, disc(), "orb.png")
    prep = await UpscalePrep().run(ctx, {"mode": "ai"}, {"images": [src]})
    up = await put_img(ctx, fake_model(Image.open(ctx.blobs.path(prep.outputs["rgb"][0].hash))), "u.png")
    for installed, want in ((True, "seedvr2"), (False, "esrgan")):
        monkeypatch.setattr(adapter, "seedvr2_installed", lambda _s, v=installed: v)
        post = await UpscalePost(object()).run(ctx, {"scale": 4, "mode": "ai", "engine": "auto"},
                                               {"images": [src], "upscaled": [up]})
        assert [r["method"] for r in post.meta["results"]] == [want]
