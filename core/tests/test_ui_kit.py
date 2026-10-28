import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from studio_core.ops import ui


def button(w: int = 120, h: int = 48, radius: int = 10, frame: int = 4, gem: bool = True) -> Image.Image:
    """Rounded button: dark frame, vertical gradient fill, corner studs (detail that must stay out of the stretch)."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=(40, 20, 10, 255))
    fill = Image.new("RGBA", (w - 2 * frame, h - 2 * frame))
    for y in range(fill.height):
        v = 200 - y * 2
        ImageDraw.Draw(fill).line([(0, y), (fill.width, y)], fill=(v, v // 2, 30, 255))
    mask = Image.new("L", fill.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, fill.width - 1, fill.height - 1), radius - frame, fill=255)
    img.paste(fill, (frame, frame), mask)
    if gem:
        for cx in (14, w - 15):
            d.ellipse((cx - 4, h // 2 - 4, cx + 4, h // 2 + 4), fill=(80, 200, 255, 255))
    return img


def test_nine_slice_borders_keep_corners_and_studs():
    (l, t, r, b), info = ui.nine_slice_borders(button())
    assert info["stretch_x"] and info["stretch_y"]
    # Studs reach x = 18 from each side.
    assert 19 <= l <= 26 and 19 <= r <= 26
    # Without studs the rows only vary by the gradient step, so all but the rounded corners stretch.
    (l, t, r, b), info = ui.nine_slice_borders(button(gem=False))
    assert not info["off_center"]
    assert 8 <= l <= 14 and 8 <= r <= 14
    assert 6 <= t <= 14 and 6 <= b <= 14


def test_nine_slice_scale_keeps_corners_exact():
    img = button()
    border, _ = ui.nine_slice_borders(img)
    big = ui.nine_slice_scale(img, border, (360, 48))
    assert big.size == (360, 48)
    a, z = np.asarray(img), np.asarray(big)
    l, t, r, b = border
    assert np.array_equal(z[:, :l], a[:, :l])
    assert np.array_equal(z[:, -r:], a[:, -r:])
    # The stretched middle is the flat gradient, line for line.
    mid = z[:, l:-r].astype(int)
    assert np.abs(mid - mid[:, :1]).max() <= 2


def test_ornate_axis_is_flagged_not_stretchable():
    rng = np.random.default_rng(1)
    noise = Image.fromarray(rng.integers(0, 255, (40, 40, 4), np.uint8) | np.uint8(0), "RGBA")
    noise.putalpha(255)
    (l, t, r, b), info = ui.nine_slice_borders(noise)
    assert not info["stretch_x"] and (l, r) == (13, 13)


def test_split_elements_from_white_sheet_and_names():
    sheet = Image.new("RGB", (800, 600), (255, 255, 255))
    sheet.paste(button(160, 50).convert("RGB"), (20, 20), button(160, 50))
    sheet.paste(button(60, 60, gem=False).convert("RGB"), (260, 15), button(60, 60, gem=False))
    d = ImageDraw.Draw(sheet)
    d.rectangle((20, 120, 380, 140), fill=(30, 30, 30), outline=(200, 160, 40), width=3)
    d.rectangle((20, 170, 320, 390), fill=(250, 250, 250), outline=(90, 60, 20), width=6)
    d.point((790, 590), fill=(0, 0, 0))
    keyed = ui.key_background(sheet)
    assert keyed.getpixel((5, 5))[3] == 0
    # A white panel interior enclosed by its frame stays opaque.
    assert keyed.getpixel((120, 230))[3] == 255
    parts = ui.split_elements(keyed)
    names = [n for n, _, _ in parts]
    assert names == ["button_01", "slot_01", "bar_01", "panel_01"]
    btn = parts[0][1]
    assert btn.size == (164, 54)


async def test_nine_slice_op_writes_states_slices_and_preview(tmp_path: Path):
    from test_ops_executors import FakeCtx, put_img

    ctx = FakeCtx(tmp_path)
    refs = [await put_img(ctx, button(), "button_01.png"), await put_img(ctx, button(200, 160), "panel_01.png")]
    res = await ui.NineSlice().run(ctx, {}, {"images": refs})
    doc = json.loads(ctx.blobs.path(res.outputs["slices"][0].hash).read_bytes())
    names = [e["name"] for e in doc["elements"]]
    assert names == ["button_01", "button_01_hover", "button_01_pressed", "button_01_disabled", "panel_01"]
    assert doc["elements"][1]["border"] == doc["elements"][0]["border"]
    assert len(res.outputs["images"]) == 5 and res.outputs["preview"]
    icons = await ui.NineSlice().run(ctx, {"slice": False}, {"images": refs})
    doc = json.loads(ctx.blobs.path(icons.outputs["slices"][0].hash).read_bytes())
    assert [e["border"] for e in doc["elements"]] == [[0, 0, 0, 0]] * 2


async def test_ui_kit_exports_unity_border_and_godot_stylebox(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source
    from test_ops_executors import FakeCtx, put_img

    ctx = FakeCtx(tmp_path)
    refs = [await put_img(ctx, button(gem=False), "button_01.png")]
    res = await ui.NineSlice().run(ctx, {}, {"images": refs})
    step = _row("approve", "gate.approve", {"images": res.outputs["images"], "slices": res.outputs["slices"],
                                             "preview": res.outputs["preview"]})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Kit")
    (name, _, border, state), *rest = src.ui
    assert name == "button_01" and state == "" and [s for *_, s in rest] == ["hover", "pressed", "disabled"]
    l, t, r, b = border
    u = dict(unity.export(src))
    meta = u["UI/button_01.png.meta"].decode()
    assert f"spriteBorder: {{x: {l}, y: {b}, z: {r}, w: {t}}}" in meta
    assert "spriteMode: 1" in meta and "spriteMeshType: 0" in meta
    assert "Sprite Swap" in u["README.txt"].decode()
    gd = dict(godot.export(src))
    tres = gd["UI/button_01_hover.tres"].decode()
    assert 'type="StyleBoxTexture"' in tres and 'path="button_01_hover.png"' in tres
    assert f"texture_margin_left = {l}.0" in tres and f"texture_margin_bottom = {b}.0" in tres
    scene = gd["Kit_ui.tscn"].decode()
    assert scene.count("NinePatchRect") == 1 and f"patch_margin_top = {t}" in scene


async def test_icons_export_as_plain_sprites(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_source
    from test_ops_executors import FakeCtx, put_img

    ctx = FakeCtx(tmp_path)
    refs = [await put_img(ctx, button(64, 64), "x_fit.png"), await put_img(ctx, button(64, 64, gem=False), "y.png")]
    res = await ui.NineSlice().run(ctx, {"slice": False, "states": "none", "names": "a health potion\nGold Coin!"},
                                   {"images": refs})
    src = engine_source(_row("approve", "gate.approve", dict(res.outputs)), [], ctx.blobs, "Icons")
    assert [n for n, *_ in src.ui] == ["health_potion", "gold_coin"]
    u = dict(unity.export(src))
    assert "spriteBorder: {x: 0, y: 0, z: 0, w: 0}" in u["UI/gold_coin.png.meta"].decode()
    gd = dict(godot.export(src))
    assert "UI/gold_coin.png" in gd and not any(k.endswith(".tres") or k.endswith(".tscn") for k in gd)
