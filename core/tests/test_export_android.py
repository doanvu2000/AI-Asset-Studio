import io
import re
import xml.etree.ElementTree as ET

from PIL import Image

from studio_core.export import android
from studio_core.export.source import Source

BLACK = (0, 0, 0, 255)
NS = "{http://schemas.android.com/apk/res/android}"


def _png(tmp_path, name, size, color=(200, 40, 40, 255)):
    p = tmp_path / name
    Image.new("RGBA", size, color).save(p)
    return p


def _by_path(files):
    return {k: v for k, v in files}


def _img(data):
    return Image.open(io.BytesIO(data))


def test_empty_source_gives_no_files():
    assert android.export(Source(asset="x")) == []


def test_resource_names_valid_and_unique():
    used: set[str] = set()
    got = [android.res_name(n, used) for n in ["Btn Primary!", "btn_primary", "9lives", "class", "", "BTN-PRIMARY"]]
    assert len(set(got)) == len(got)
    for n in got:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", n), n
    assert "class" not in got


def test_density_dirs_and_sizes(tmp_path):
    src = Source(asset="kit", ui=[("coin", _png(tmp_path, "c.png", (64, 32)), (0, 0, 0, 0), "")])
    f = _by_path(android.export(src))
    sizes = {d: _img(f[f"res/drawable-{d}/coin.webp"]).size for d, _ in android.DENSITIES}
    assert sizes == {"mdpi": (16, 8), "hdpi": (24, 12), "xhdpi": (32, 16), "xxhdpi": (48, 24), "xxxhdpi": (64, 32)}
    assert all(_img(f[f"res/drawable-{d}/coin.webp"]).format == "WEBP" for d, _ in android.DENSITIES)


def test_tiny_image_never_below_one_pixel(tmp_path):
    src = Source(asset="kit", ui=[("dot", _png(tmp_path, "d.png", (2, 2)), (0, 0, 0, 0), "")])
    f = _by_path(android.export(src))
    assert _img(f["res/drawable-mdpi/dot.webp"]).size == (1, 1)


def test_nine_patch_markers(tmp_path):
    src = Source(asset="kit", ui=[("panel", _png(tmp_path, "p.png", (40, 24)), (8, 4, 12, 6), "")])
    f = _by_path(android.export(src))
    assert _img(f["res/drawable-xxxhdpi/panel.9.png"]).format == "PNG"
    im = _img(f["res/drawable-xxxhdpi/panel.9.png"]).convert("RGBA")
    assert im.size == (42, 26)
    black_x = [x for x in range(42) if im.getpixel((x, 0)) == BLACK]
    black_y = [y for y in range(26) if im.getpixel((0, y)) == BLACK]
    assert black_x == list(range(1 + 8, 1 + 40 - 12)) and black_y == list(range(1 + 4, 1 + 24 - 6))
    assert [x for x in range(42) if im.getpixel((x, 25)) == BLACK] == black_x
    assert [y for y in range(26) if im.getpixel((41, y)) == BLACK] == black_y
    assert im.getpixel((0, 0))[3] == 0 and im.getpixel((5, 5)) == (200, 40, 40, 255)
    mdpi = _img(f["res/drawable-mdpi/panel.9.png"]).convert("RGBA")
    assert mdpi.size == (12, 8)
    assert "res/drawable-xxxhdpi/panel.webp" not in f


def test_nine_patch_border_larger_than_image_keeps_stretch_pixel():
    im = android.nine_patch(Image.new("RGBA", (6, 6), (1, 2, 3, 255)), (20, 20, 20, 20))
    assert any(im.getpixel((x, 0)) == BLACK for x in range(8))
    assert any(im.getpixel((0, y)) == BLACK for y in range(8))


def test_sheet_frames_and_clip_constants(tmp_path):
    sheet = _png(tmp_path, "s.png", (32, 16))
    meta = {"frame_w": 16, "frame_h": 16, "cols": 2, "rows": 1, "count": 2,
            "clips": [{"name": "walk", "row0": 0, "rows": 1, "frames": 2, "fps": 10, "loop": False}], "row_angles": [0]}
    f = _by_path(android.export(Source(asset="hero", sheet=sheet, meta=meta)))
    frames = [k for k in f if k.startswith("res/drawable-nodpi/")]
    assert len(frames) == 2 and all(k.endswith(".webp") for k in frames)
    assert _img(f[frames[0]]).size == (16, 16)
    kt = f["kotlin/AssetStudioAssets.kt"].decode()
    assert "object Clips" in kt and "AssetStudioClip(listOf(" in kt and ", 10f, false)" in kt
    for k in frames:
        assert f"R.drawable.{k.split('/')[-1][:-5]}" in kt
    assert not any(k.startswith("res/drawable/") for k in f)


def test_many_frames_warning(tmp_path):
    n = android.MANY_FRAMES + 1
    sheet = _png(tmp_path, "s.png", (n * 4, 4))
    meta = {"frame_w": 4, "frame_h": 4, "cols": n, "rows": 1, "count": n}
    f = _by_path(android.export(Source(asset="vfx", sheet=sheet, meta=meta)))
    assert "CẢNH BÁO" in f["README_ANDROID.md"].decode()


def test_kotlin_file_names_package_and_helpers(tmp_path):
    src = Source(asset="kit", ui=[("btn", _png(tmp_path, "b.png", (24, 24)), (6, 6, 6, 6), "hover"),
                                  ("object", _png(tmp_path, "o.png", (8, 8)), (0, 0, 0, 0), "")])
    f = _by_path(android.export(src, package="com.acme.app"))
    kt = f["kotlin/AssetStudioAssets.kt"].decode()
    assert kt.startswith("package com.acme.app") and "import com.acme.app.R" in kt
    assert "object NinePatches" in kt and "val btn_hover: Int = R.drawable.btn_hover" in kt
    assert "object Images" in kt and "R.drawable.object_ui" in kt
    for fn in ("fun Modifier.ninePatchBackground", "fun AssetStudioAnimation", "class AssetStudioClip"):
        assert fn in kt
    assert kt.count("{") == kt.count("}")
    readme = f["README_ANDROID.md"].decode()
    assert "app/src/main/java/com/acme/app/" in readme and "ninePatchBackground" in readme


def test_app_icon_files(tmp_path):
    logo = _png(tmp_path, "logo.png", (200, 200), (10, 120, 200, 255))
    f = _by_path(android.app_icon_files(logo, "#112233"))
    for d, s in android.DENSITIES:
        assert _img(f[f"res/mipmap-{d}/ic_launcher.webp"]).size == (round(48 * s),) * 2
        assert _img(f[f"res/mipmap-{d}/ic_launcher_round.webp"]).size == (round(48 * s),) * 2
        fg = _img(f[f"res/mipmap-{d}/ic_launcher_foreground.webp"]).convert("RGBA")
        assert fg.size == (round(108 * s),) * 2
        assert fg.getpixel((0, 0))[3] == 0
        mono = _img(f[f"res/mipmap-{d}/ic_launcher_monochrome.webp"]).convert("RGBA")
        c = mono.size[0] // 2
        assert mono.getpixel((c, c)) == (255, 255, 255, 255)
    big = _img(f["res/mipmap-xxxhdpi/ic_launcher_foreground.webp"]).convert("RGBA")
    box = big.getchannel("A").getbbox()
    assert box[2] - box[0] <= round(432 * android.SAFE_ZONE) + 1
    assert "#112233" in f["res/values/ic_launcher_background.xml"].decode()
    for name in ("ic_launcher", "ic_launcher_round"):
        root = ET.fromstring(f[f"res/mipmap-anydpi-v26/{name}.xml"])
        assert root.tag == "adaptive-icon"
        assert [c.tag for c in root] == ["background", "foreground", "monochrome"]
        assert root.find("background").get(f"{NS}drawable") == "@color/ic_launcher_background"


def test_app_icon_from_ui_item(tmp_path):
    src = Source(asset="kit", ui=[("app_icon", _png(tmp_path, "i.png", (64, 64)), (0, 0, 0, 0), "")])
    f = _by_path(android.export(src))
    assert "res/mipmap-xxxhdpi/ic_launcher.webp" in f and "res/mipmap-anydpi-v26/ic_launcher.xml" in f
