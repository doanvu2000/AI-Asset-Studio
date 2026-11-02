from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageFilter

from studio_core.ops.pixel_cleanup import (
    detect_pixel_size, pixel_cleanup, read_palette_text, remove_stray, upscale)

SCALE = 4


def base_art() -> np.ndarray:
    rng = np.random.default_rng(3)
    art = np.zeros((20, 20, 4), np.uint8)
    cols = np.array([[200, 40, 40], [40, 160, 60], [50, 70, 200], [230, 200, 60]], np.uint8)
    art[..., :3] = cols[rng.integers(0, 4, (20, 20))]
    art[..., 3] = 255
    art[0:2, :, 3] = 0
    return art


def noisy_scaled(offset: int = 0) -> tuple[Image.Image, np.ndarray]:
    art = base_art()
    big = np.asarray(Image.fromarray(art, "RGBA").resize((20 * SCALE, 20 * SCALE), Image.Resampling.NEAREST))
    rng = np.random.default_rng(1)
    rgb = np.clip(big[..., :3].astype(int) + rng.integers(-6, 7, big[..., :3].shape), 0, 255).astype(np.uint8)
    img = Image.fromarray(np.dstack([rgb, big[..., 3]]), "RGBA").filter(ImageFilter.GaussianBlur(0.5))
    if offset:
        canvas = Image.new("RGBA", (img.width + offset, img.height + offset), (0, 0, 0, 0))
        canvas.paste(img, (offset, offset))
        img = canvas
    return img, art


def test_detects_scale_four():
    img, _ = noisy_scaled()
    size, off = detect_pixel_size(np.asarray(img))
    assert (size, off) == (4, (0, 0))


def test_detects_scale_with_offset():
    img, _ = noisy_scaled(offset=2)
    size, off = detect_pixel_size(np.asarray(img))
    assert size == 4 and off == (2, 2)


def test_cleanup_limits_colors_and_snaps_grid():
    img, art = noisy_scaled()
    res = pixel_cleanup([img], colors=4)[0]
    assert res.pixel_size == 4
    assert res.grid.size == (20, 20)
    assert res.colors_before > 50
    assert res.colors_after <= 4
    got = np.asarray(res.grid)
    assert (got[..., 3] == art[..., 3]).all()
    want = remove_stray(art)[0]
    err = np.abs(got[2:, :, :3].astype(int) - want[2:, :, :3].astype(int)).max()
    assert err <= 12


def test_stray_pixel_removed():
    art = np.zeros((9, 9, 4), np.uint8)
    art[..., :3] = (10, 120, 200)
    art[..., 3] = 255
    art[4, 4, :3] = (250, 40, 40)
    out, n = remove_stray(art)
    assert n == 1
    assert tuple(out[4, 4, :3]) == (10, 120, 200)


def test_edge_pixel_next_to_transparent_kept():
    art = np.zeros((5, 5, 4), np.uint8)
    art[2, 2] = (255, 0, 0, 255)
    out, n = remove_stray(art)
    assert n == 0 and tuple(out[2, 2, :3]) == (255, 0, 0)


def test_not_pixel_art_warns_size_one():
    yy, xx = np.mgrid[0:64, 0:64]
    rgb = np.dstack([xx * 4, yy * 4, (xx + yy) * 2]).astype(np.uint8)
    img = Image.fromarray(np.dstack([rgb, np.full((64, 64), 255, np.uint8)]), "RGBA")
    res = pixel_cleanup([img], colors=8)[0]
    assert res.pixel_size == 1
    assert res.warnings and res.colors_after <= 8


def test_fully_transparent():
    res = pixel_cleanup([Image.new("RGBA", (16, 16), (0, 0, 0, 0))], colors=4)[0]
    assert res.colors_after == 0 and "transparent" in res.warnings[0]


def test_given_palette_and_outline():
    img, _ = noisy_scaled()
    pal = read_palette_text("#c82828\n28a03c\n3246c8\ne6c83c\n000000\n")
    res = pixel_cleanup([img], palette_colors=pal, outline="#000000")[0]
    used = {tuple(p) for p in np.asarray(res.grid)[..., :3][np.asarray(res.grid)[..., 3] > 0]}
    assert used <= {tuple(c) for c in pal}
    assert (np.asarray(res.grid)[1, 5, 3]) == 255


def test_empty_palette_rejected():
    with pytest.raises(ValueError):
        read_palette_text("; nothing\n")
    img, _ = noisy_scaled()
    with pytest.raises(ValueError):
        pixel_cleanup([img], palette_colors=np.zeros((0, 3), np.uint8))


def test_gpl_palette_and_upscale():
    pal = read_palette_text("GIMP Palette\nName: t\n#\n255 0 0 red\n0 0 255 blue\n")
    assert len(pal) == 2
    assert upscale(Image.new("RGBA", (3, 2)), 4).size == (12, 8)
