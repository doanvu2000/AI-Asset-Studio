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
