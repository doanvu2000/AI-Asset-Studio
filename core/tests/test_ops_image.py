from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.ops import image as im


def blob(w: int, h: int, box: tuple[int, int, int, int], color=(200, 30, 30, 255), canvas=(0, 0, 0, 0)) -> Image.Image:
    img = Image.new("RGBA", (w, h), canvas)
    img.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), color), box[:2])
    return img


def alpha_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    a = np.asarray(img)[..., 3]
    ys, xs = np.nonzero(a > 8)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def test_content_bbox_alpha_and_white_fallback():
    assert im.content_bbox(blob(100, 80, (10, 20, 40, 70))) == (10, 20, 40, 70)
    rgb = Image.new("RGB", (50, 50), (255, 255, 255))
    rgb.paste((0, 0, 0), (5, 6, 15, 26))
    assert im.content_bbox(rgb) == (5, 6, 15, 26)
    assert im.content_bbox(Image.new("RGBA", (10, 10))) is None


def test_trim_pad_resize_centers_and_keeps_alpha():
    src = blob(300, 200, (100, 20, 150, 120))
    out = im.trim_pad_resize(src, 128, pad=0.1)
    assert out.size == (128, 128) and out.mode == "RGBA"
    l, t, r, b = alpha_bbox(out)
    assert b - t == 128 - 2 * 13
    assert abs((l + r) / 2 - 64) <= 1 and abs((t + b) / 2 - 64) <= 1
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((64, 64)) == (200, 30, 30, 255)


def test_trim_pad_resize_not_square_and_nearest():
    out = im.trim_pad_resize(blob(64, 64, (0, 0, 32, 16)), 64, pad=0, square=False, resample="nearest")
    assert out.size == (64, 32)
    assert set(np.unique(np.asarray(out)[..., 3])) == {255}


def test_normalize_common_scale_and_baseline():
    frames = [blob(200, 200, (80, 40, 120, 180)), blob(200, 200, (60, 100, 140, 180))]
    out, pivot = im.normalize_frames(frames, 100, pad=0.1)
    assert pivot == (50, 90)
    boxes = [alpha_bbox(f) for f in out]
    assert all(b[3] == 90 for b in boxes)
    # union bbox height 140 -> 80px; scale shared, so frame 2 (height 80 src) becomes ~46px
    assert boxes[0][3] - boxes[0][1] == pytest.approx(80, abs=1)
    assert boxes[1][3] - boxes[1][1] == pytest.approx(80 * 80 / 140, abs=1)
    assert boxes[1][2] - boxes[1][0] == pytest.approx(80 * 80 / 140, abs=1)


def test_normalize_mixed_sizes_bottom_aligned():
    out, pivot = im.normalize_frames([blob(50, 50, (0, 0, 50, 50)), blob(80, 40, (0, 0, 80, 40))], 64, pad=0)
    boxes = [alpha_bbox(f) for f in out]
    assert all(b[3] == 64 for b in boxes)
    assert boxes[1][2] - boxes[1][0] == 64


def test_mirror_dirs_mapping():
    half = [blob(10, 10, (i, 0, i + 1, 1)) for i in range(5)]
    full = im.mirror_dirs(half, 8)
    assert len(full) == 8
    assert full[:5] == half
    for out_idx, src_idx in ((5, 3), (6, 2), (7, 1)):
        assert alpha_bbox(full[out_idx]) == (9 - src_idx, 0, 10 - src_idx, 1)
    with pytest.raises(ValueError):
        im.mirror_dirs(half[:4], 8)


def test_pack_sheet_layout_and_meta():
    frames = [blob(16, 16, (0, 0, 16, 16), color=(i * 40, 0, 0, 255)) for i in range(5)]
    sheet, meta = im.pack_sheet(frames, cols=3, padding=2, pivot=(8, 15))
    assert sheet.size == (3 * 16 + 4, 2 * 16 + 2)
    assert meta["cols"] == 3 and meta["rows"] == 2 and meta["count"] == 5
    assert meta["frame_w"] == 16 and meta["pivot"] == [8, 15] and meta["order"] == [0, 1, 2, 3, 4]
    assert sheet.getpixel((18 + 1, 1))[0] == 40
    assert sheet.getpixel((18 + 1, 18 + 1))[0] == 160
    assert sheet.getpixel((2 * 18 + 1, 18 + 1))[3] == 0


def test_downscale_and_pixelate():
    assert im.downscale(blob(64, 32, (0, 0, 64, 32)), 2).size == (32, 16)
    src = Image.new("RGBA", (64, 64))
    src.paste(Image.linear_gradient("L").resize((32, 32)).convert("RGBA"), (16, 16))
    px = im.pixelate(src, 16, palette_colors=4, outline="#000000")
    arr = np.asarray(px)
    assert px.size == (16, 16)
    assert set(np.unique(arr[..., 3])) <= {0, 255}
    solid = arr[arr[..., 3] == 255][:, :3]
    assert len({tuple(c) for c in solid}) <= 5
    assert tuple(arr[3, 8]) == (0, 0, 0, 255)


def test_thumbnail_webp_with_alpha(tmp_path: Path):
    src = tmp_path / "a.png"
    blob(400, 200, (100, 50, 300, 150)).save(src)
    dst = tmp_path / "t" / "a.webp"
    im.make_thumbnail(src, dst, 128)
    with Image.open(dst) as t:
        assert t.format == "WEBP" and t.size == (128, 64)
        assert t.convert("RGBA").getpixel((0, 0))[3] == 0
    jpg = tmp_path / "b.jpg"
    Image.new("RGB", (300, 300), (10, 200, 10)).save(jpg)
    im.make_thumbnail(jpg, tmp_path / "b.webp", 64)
    glb = tmp_path / "m.glb"
    glb.write_bytes(b"glTF\x02\x00\x00\x00" + b"\x00" * 32)
    with pytest.raises(ValueError):
        im.make_thumbnail(glb, tmp_path / "m.webp", 64)
