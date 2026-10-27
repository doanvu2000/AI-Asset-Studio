import asyncio
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.contracts import ExecutorRegistry, StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.ops import register
from studio_core.ops.pixel import PALETTES, kmeans_palette, parse_hex, pixel_art
from studio_core.ops.presets import BUILTIN, annotate_sheet_meta, apply_preset

from test_ops_executors import FakeCtx, load, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def figure(w: int = 256, shade: int = 0, shift: int = 0) -> Image.Image:
    """Shaded, anti-aliased blob figure so quantization has gradients and soft edges to deal with."""
    big = Image.new("RGBA", (w * 4, w * 4))
    d = ImageDraw.Draw(big)
    for i in range(12):
        c = (40 + i * 15 + shade, 90 + i * 8, 200 - i * 10, 255)
        d.ellipse((w + shift * 4 + i * 8, w // 2 + i * 8, w * 3 + shift * 4 - i * 8, w * 3 - i * 8), fill=c)
    d.rectangle((w * 2 - 40 + shift * 4, w * 3 - 20, w * 2 + 40 + shift * 4, w * 4 - 30), fill=(120, 60, 30, 255))
    return big.resize((w, w), Image.Resampling.LANCZOS)


def frames(n: int = 4) -> list[Image.Image]:
    return [figure(shade=i * 6, shift=i * 3) for i in range(n)]


def colors_of(img: Image.Image) -> set[tuple[int, int, int]]:
    a = np.array(img)
    return {tuple(int(v) for v in c) for c in a[a[..., 3] > 0][:, :3]}


def test_palette_deterministic_and_bounded():
    a = pixel_art(frames(), size=48, colors=12, outline="none")
    b = pixel_art(frames(), size=48, colors=12, outline="none")
    assert a.palette == b.palette and 2 <= len(a.palette) <= 12
    for fa, fb in zip(a.frames, b.frames):
        assert np.array_equal(np.array(fa), np.array(fb))
    px = np.random.default_rng(0).integers(0, 255, (5000, 3), dtype=np.uint8)
    assert np.array_equal(kmeans_palette(px, 8), kmeans_palette(px.copy(), 8))


def test_one_palette_shared_by_all_frames():
    res = pixel_art(frames(), size=48, colors=10, outline="dark")
    allowed = set(res.palette)
    used = set()
    for f in res.frames:
        used |= colors_of(f)
        assert colors_of(f) <= allowed
    assert used == allowed


def test_sizes_equal_alpha_binary_and_scale_shared():
    src = frames(3) + [figure(w=256).crop((0, 0, 256, 256))]
    res = pixel_art(src, size=64, colors=8)
    assert {f.size for f in res.frames} == {(64, 64)} and res.scale == pytest.approx(0.25)
    for f in res.frames:
        assert set(np.unique(np.array(f)[..., 3])) <= {0, 255}
    assert pixel_art(src, factor=8, size=None, colors=8).size == (32, 32)


def test_outline_is_outer_ring_only():
    plain = pixel_art(frames(1), size=48, colors=7, outline="none").frames[0]
    lined = pixel_art(frames(1), size=48, colors=8, outline="#ff00ff").frames[0]
    p, q = np.array(plain), np.array(lined)
    solid = p[..., 3] > 0
    assert np.array_equal(q[solid], p[solid]), "outline must not repaint the sprite itself"
    ring = (q[..., 3] > 0) & ~solid
    assert ring.any() and (q[ring][:, :3] == (255, 0, 255)).all()
    grown = np.zeros_like(solid)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        grown |= np.roll(solid, (dy, dx), (0, 1))
    assert not (ring & ~grown).any(), "outline is 1px (4-neighbour) wide"


def test_hex_outline_counts_toward_colors():
    for palette in ("auto", "pico8"):
        res = pixel_art(frames(2), size=48, colors=8, palette=palette, outline="#ff00ff")
        used = set().union(*(colors_of(f) for f in res.frames))
        assert (255, 0, 255) in used and len(used) <= 8, palette


def test_palette_planning_can_stop():
    from studio_core.ops.pixel import plan_palette, shrink
    smalls = [shrink(f, 0.25) for f in frames(2)]
    with pytest.raises(asyncio.CancelledError):
        plan_palette(smalls, 0.25, colors=8, stop=lambda: True)


def test_dark_outline_stays_in_preset_palette():
    res = pixel_art(frames(2), size=48, palette="pico8", colors=16, outline="dark")
    pico = {parse_hex(c) for c in PALETTES["pico8"]}
    for f in res.frames:
        assert colors_of(f) <= pico


def test_palette_presets_valid():
    for name, cols in PALETTES.items():
        assert len(cols) in (16, 32) and len(set(cols)) == len(cols), name
        for c in cols:
            assert len(parse_hex(c)) == 3
    res = pixel_art(frames(2), size=32, palette="db32", colors=8, outline="none")
    assert len(res.palette) <= 8
    with pytest.raises(ValueError):
        pixel_art(frames(1), size=32, palette="nope")


def test_palette_from_reference_image():
    ref = Image.new("RGBA", (4, 1))
    for i, c in enumerate([(10, 10, 10), (200, 30, 30), (30, 200, 30), (240, 240, 240)]):
        ref.putpixel((i, 0), (*c, 255))
    res = pixel_art(frames(2), size=32, colors=8, palette_image=ref, outline="none")
    assert set(res.palette) <= {(10, 10, 10), (200, 30, 30), (30, 200, 30), (240, 240, 240)}
    assert res.source == "image"


def test_bayer_dither_is_position_stable():
    a = pixel_art(frames(2), size=48, colors=6, dither="bayer", outline="none")
    b = pixel_art(frames(2), size=48, colors=6, dither="bayer", outline="none")
    assert np.array_equal(np.array(a.frames[1]), np.array(b.frames[1]))


async def test_executor_outputs(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    reg = ExecutorRegistry()
    register(reg, None, None, None)
    refs = [await put_img(ctx, f, f"dir_{i * 90:03d}.png") for i, f in enumerate(frames(4))]
    ex = reg.resolve("op.pixel_art")
    res = await ex.run(ctx, {"size": 40, "colors": 8, "outline": "dark"}, {"images": refs})
    imgs = res.outputs["images"]
    assert [r.name for r in imgs] == [r.name for r in refs]
    assert {load(ctx, r).size for r in imgs} == {(40, 40)}
    info = json.loads(ctx.blobs.path(res.outputs["palette"][0].hash).read_bytes())
    assert info["colors"] == res.meta["palette"] and info["frame_w"] == 40
    with Image.open(io.BytesIO(ctx.blobs.path(res.outputs["swatch"][0].hash).read_bytes())) as sw:
        assert sw.width == 8 * len(info["colors"])
    with pytest.raises(StepError):
        await ex.run(ctx, {"size": 40, "colors": 1}, {"images": refs})


def test_catalog_pixel_preset_and_recipes():
    spec = CATALOG["op.pixel_art"]
    assert "palette_image" in spec["in"] and set(spec["out"]) == {"images", "palette", "swatch"}
    assert set(spec["params"]["palette"]["options"]) == {"auto", *PALETTES}
    preset = next(p for p in BUILTIN if p.id == "pixel")
    assert preset.pixel and not next(p for p in BUILTIN if p.id == "unity").pixel
    slots, named = apply_preset(preset, [a * 45 for a in range(8)], "hero")
    meta = {"frame_w": 64, "frame_h": 64, "cols": 8, "padding": 0}
    annotate_sheet_meta(meta, named, preset)
    assert meta["pixel"] is True and meta["filter"] == "point"
    for rid in ("sprite-8dir-pixel", "sprite-8dir-3d-pixel"):
        _, issues = validate((RECIPES / f"{rid}.yaml").read_text(encoding="utf-8"), None)
        assert [i for i in issues if i.get("level") == "error"] == [], (rid, issues)


def test_dark_outline_counts_toward_colors():
    noisy = []
    for i, f in enumerate(frames(4)):
        a = np.array(f).astype(np.int16)
        a[..., :3] += np.random.default_rng(i).integers(-30, 30, a[..., :3].shape, dtype=np.int16)
        noisy.append(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA"))
    for colors in (2, 4, 16, 32):
        res = pixel_art(noisy, size=96, colors=colors, outline="dark")
        used = set().union(*(colors_of(f) for f in res.frames))
        assert len(res.palette) <= colors and len(used) <= colors, (colors, len(res.palette), len(used))
        assert res.outline_colors or colors == 2


def test_two_colors_keep_both_fills_and_still_outline():
    res = pixel_art(frames(2), size=48, colors=2, outline="dark")
    assert len(res.palette) == 2 and not res.outline_colors
    plain = pixel_art(frames(2), size=48, colors=2, outline="none")
    a, b = np.array(res.frames[0]), np.array(plain.frames[0])
    ring = (a[..., 3] > 0) & (b[..., 3] == 0)
    assert ring.any() and colors_of(res.frames[0]) <= set(res.palette)


def test_existing_darks_do_not_cost_fill_colors():
    from studio_core.ops.pixel import from_oklab, to_oklab

    hues = np.linspace(0, 2 * np.pi, 7, endpoint=False)
    light_lab = np.stack([np.full(7, 0.42), 0.06 * np.cos(hues), 0.06 * np.sin(hues)], 1)
    lights = from_oklab(light_lab)
    lab = to_oklab(lights)
    darks = from_oklab(np.stack([lab[:, 0] * 0.45, lab[:, 1] * 0.7, lab[:, 2] * 0.7], 1))
    img = np.zeros((64, 64, 4), np.uint8)
    for i, (lc, dc) in enumerate(zip(lights, darks)):
        y = 4 + i * 8
        img[y:y + 8, 4:32, :3], img[y:y + 8, 32:60, :3] = lc, dc
        img[y:y + 8, 4:60, 3] = 255
    res = pixel_art([Image.fromarray(img, "RGBA")], size=64, colors=16, outline="dark", cleanup=False)
    want = {tuple(int(v) for v in c) for c in np.concatenate([lights, darks])}
    assert len(want) == 14 and set(res.palette) == want and not res.outline_colors


async def test_pixel_size_bounds_and_normal_downscale(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    reg = ExecutorRegistry()
    register(reg, None, None, None)
    refs = [await put_img(ctx, f, f"dir_{i * 90:03d}_n.png") for i, f in enumerate(frames(2))]
    for size in (4, 513):
        with pytest.raises(StepError) as ei:
            await reg.resolve("op.pixel_art").run(ctx, {"size": size}, {"images": refs})
        assert ei.value.code == "op_bad_params"
    res = await reg.resolve("op.downscale").run(ctx, {"factor": 2, "normal": True}, {"images": refs})
    assert [r.name for r in res.outputs["images"]] == ["dir_000_down_n.png", "dir_090_down_n.png"]
    assert res.meta == {"normal": True}
    for rid in ("sprite-8dir-3d", "sprite-8dir-3d-mv", "sprite-8dir-3d-brief", "sprite-8dir-anim"):
        _, issues = validate((RECIPES / f"{rid}.yaml").read_text(encoding="utf-8"), None)
        assert not [i for i in issues if "normal" in str(i.get("message", ""))], (rid, issues)
