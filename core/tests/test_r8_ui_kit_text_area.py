from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from studio_core.ops import ui
from test_ui_kit import button


def runed(w: int = 160, h: int = 56) -> Image.Image:
    img = button(w, h, gem=False)
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(3)
    for _ in range(40):
        x, y = int(rng.integers(w * 0.25, w * 0.75)), int(rng.integers(h * 0.35, h * 0.6))
        d.line([(x, y), (x + 6, y + int(rng.integers(-5, 5)))], fill=(90, 255, 255, 255), width=2)
    return img


def test_runes_in_text_zone_are_flattened_and_frame_kept():
    img = runed()
    assert ui.text_zone_std(img) > ui.TEXT_ZONE_STD_MAX
    out, changed = ui.clear_text_zone(img)
    assert changed and ui.text_zone_std(out) < ui.TEXT_ZONE_STD_MAX / 2
    a, z = np.asarray(img), np.asarray(out)
    x0, y0, x1, y1 = ui.text_zone(img.size)
    assert np.array_equal(a[:, :x0 - 20], z[:, :x0 - 20])
    assert np.array_equal(a[:, :, 3], z[:, :, 3])


def test_plain_gradient_button_and_small_or_unlabelled_sizes_untouched():
    plain = button(160, 56, gem=False)
    assert ui.text_zone_std(plain) <= ui.TEXT_ZONE_STD_MAX
    out, changed = ui.clear_text_zone(plain)
    assert not changed and out is plain
    tiny = runed(40, 16)
    assert ui.text_zone(tiny.size) is None and not ui.clear_text_zone(tiny)[1]


async def test_nine_slice_clears_buttons_only(tmp_path: Path):
    from test_ops_executors import FakeCtx, put_img

    ctx = FakeCtx(tmp_path)
    refs = [await put_img(ctx, runed(), "button_01.png"), await put_img(ctx, runed(), "slot_01.png")]
    res = await ui.NineSlice().run(ctx, {}, {"images": refs})
    first, slot = (Image.open(ctx.blobs.path(r.hash)) for r in (res.outputs["images"][0], res.outputs["images"][4]))
    assert ui.text_zone_std(first) < ui.TEXT_ZONE_STD_MAX / 2 and ui.text_zone_std(slot) > ui.TEXT_ZONE_STD_MAX
    off = await ui.NineSlice().run(ctx, {"clear_text": False}, {"images": refs[:1]})
    assert ui.text_zone_std(Image.open(ctx.blobs.path(off.outputs["images"][0].hash))) > ui.TEXT_ZONE_STD_MAX


def test_flattened_zone_blends_into_surface_without_a_step():
    rng = np.random.default_rng(5)
    img = button(200, 80, gem=False)
    a = np.asarray(img).copy()
    x0, y0, x1, y1 = ui.text_zone(img.size)
    a[y0:y1, x0:x1, :3] = np.clip(a[y0:y1, x0:x1, :3].astype(int) + rng.integers(-60, 60, (y1 - y0, x1 - x0, 1)), 0, 255)
    img = Image.fromarray(a, "RGBA")
    out, changed = ui.clear_text_zone(img)
    assert changed
    z = np.asarray(out)[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    ring = z[y0 - 12:y1 + 12, x0 - 12:x1 + 12]
    assert max(np.abs(np.diff(ring, axis=0)).max(), np.abs(np.diff(ring, axis=1)).max()) <= 12
