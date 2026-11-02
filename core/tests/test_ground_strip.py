import numpy as np
from PIL import Image

from studio_core.ops.ground_strip import strip_ground


def _knight(grass: bool, green_body: bool = False) -> Image.Image:
    im = Image.new("RGBA", (64, 96), (0, 0, 0, 0))
    body = (40, 160, 50, 255) if green_body else (120, 120, 130, 255)
    im.paste(Image.new("RGBA", (20, 70), body), (22, 10))
    if grass:
        im.paste(Image.new("RGBA", (10, 6), (110, 190, 60, 255)), (6, 84))
        im.paste(Image.new("RGBA", (10, 6), (110, 190, 60, 255)), (48, 84))
    return im


def test_grass_at_feet_is_removed_body_kept():
    out, st = strip_ground(_knight(True))
    a = np.array(out)[..., 3]
    assert a[84:90, 6:16].max() == 0 and a[84:90, 48:58].max() == 0
    assert a[10:80, 22:42].min() == 255 and st["ground_px"] >= 120


def test_clean_sprite_is_untouched():
    src = _knight(False)
    out, st = strip_ground(src)
    assert list(out.getdata()) == list(src.getdata()) and st["ground_px"] == 0


def test_green_character_keeps_body():
    out, st = strip_ground(_knight(False, green_body=True))
    assert np.array(out)[10:80, 22:42, 3].min() == 255 and st["ground_px"] == 0


def test_loose_speck_is_dropped():
    im = _knight(False)
    im.putpixel((2, 2), (255, 0, 0, 255))
    out, st = strip_ground(im)
    assert out.getpixel((2, 2))[3] == 0 and st["specks_px"] == 1


def test_green_patch_above_feet_band_is_kept():
    im = _knight(False)
    im.paste(Image.new("RGBA", (6, 6), (110, 190, 60, 255)), (28, 52))
    out, st = strip_ground(im)
    assert out.getpixel((30, 54))[3] == 255 and st["ground_px"] == 0


def _shod_knight(grass: bool) -> Image.Image:
    im = _knight(False)
    im.paste(Image.new("RGBA", (30, 8), (30, 30, 36, 255)), (17, 78))
    if grass:
        for x in (8, 20, 32, 44):
            im.paste(Image.new("RGBA", (10, 5), (110, 190, 60, 255)), (x, 86))
    return im


def test_dark_shoe_touching_grass_is_kept():
    out, st = strip_ground(_shod_knight(True))
    a = np.array(out)[..., 3]
    assert a[78:86, 17:47].min() == 255
    assert a[86:91, 8:54].max() == 0
    assert st["ground_px"] >= 150


def test_large_grass_patch_on_small_sprite_is_removed():
    im = _knight(False)
    for x in (6, 18, 30, 42):
        im.paste(Image.new("RGBA", (12, 6), (110, 190, 60, 255)), (x, 86))
    out, _ = strip_ground(im)
    assert np.array(out)[86:92, 6:54, 3].max() == 0
