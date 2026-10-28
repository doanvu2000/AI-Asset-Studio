import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import StepError
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.rarity import (LUM, RecolorTiers, RaritySet, alpha_iou, glow, parse_colors, parse_tiers,
                                    ramp_lut, recolor, reference, shimmer, unref)
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def sword(w: int = 96, h: int = 128) -> Image.Image:
    """Reddish blade with a grey hilt and a dark outline on a transparent canvas."""
    a = np.zeros((h, w, 4), np.uint8)
    a[10:100, 40:56] = (200, 60, 50, 255)
    a[100:120, 30:66] = (130, 130, 135, 255)
    a[10:100, 40] = a[10:100, 55] = (20, 20, 20, 255)
    return Image.fromarray(a, "RGBA")


def _luma(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), np.float32) / 255.0 @ LUM


def test_ramp_lut_is_luma_exact():
    for col in ((63, 191, 79), (58, 123, 213), (255, 154, 31)):
        lut = ramp_lut(col)
        assert np.allclose(lut @ LUM, np.linspace(0, 1, 256), atol=2e-3)


def test_recolor_keeps_luma_alpha_and_moves_hue():
    src = sword()
    out = recolor(src, (58, 123, 213))
    assert np.array_equal(np.asarray(out)[..., 3], np.asarray(src)[..., 3])
    mask = np.asarray(src)[..., 3] > 0
    assert np.abs(_luma(out) - _luma(src))[mask].max() < 0.02
    blade = np.asarray(out)[50, 48, :3].astype(int)
    assert blade[2] > blade[0], blade
    hilt_shift = np.abs(np.asarray(out)[110, 48, :3].astype(int) - (130, 130, 135)).max()
    blade_shift = np.abs(blade - (200, 60, 50)).max()
    assert hilt_shift < blade_shift


def test_reference_roundtrip_and_iou():
    src = sword()
    white, box = reference(src)
    assert white.size == (1024, 1024) and white.getpixel((2, 2)) == (255, 255, 255)
    rgba = Image.new("RGBA", white.size, (0, 0, 0, 0))
    rgba.alpha_composite(src.resize((box[2], box[3]), Image.Resampling.NEAREST), (box[0], box[1]))
    back = unref(rgba.resize((900, 900)), {"ref_size": 1024, "box": box, "canvas": list(src.size)})
    assert back.size == src.size and alpha_iou(src, back) > 0.9
    blank = Image.new("RGBA", src.size, (0, 0, 0, 0))
    assert alpha_iou(src, blank) == 0.0


def test_glow_and_shimmer():
    src = sword()
    g = glow(src, (255, 154, 31), 3)
    ga = np.asarray(g)[..., 3]
    assert ga[50, 37] > 0 and np.asarray(src)[50, 37, 3] == 0
    frames = shimmer(src, 4)
    assert len(frames) == 4 and all(np.array_equal(np.asarray(f)[..., 3], np.asarray(src)[..., 3]) for f in frames)
    assert len({np.asarray(f).tobytes() for f in frames}) == 4


def test_params():
    assert parse_tiers("") == ["common", "uncommon", "rare", "epic", "legendary"]
    assert parse_tiers("c, u, r, e, l")[4] == "l"
    with pytest.raises(StepError):
        parse_tiers("a, b, c")
    with pytest.raises(StepError):
        parse_tiers("a, a, b, c, d")
    cols = parse_colors("#ff0000, #00ff00")
    assert cols[0] == (255, 0, 0) and len(cols) == 5
    with pytest.raises(StepError):
        parse_colors("nope")


async def _run_set(tmp_path: Path, shimmer_on: bool = True, distort_last: bool = False):
    ctx = FakeCtx(tmp_path)
    ref = await put_img(ctx, sword(), "sword.png")
    a = await RecolorTiers().run(ctx, {}, {"images": [ref]})
    assert [r.name for r in a.outputs["images"]] == ["sword_common.png", "sword_uncommon.png", "sword_rare.png"]
    lay = json.loads(ctx.blobs.path(a.outputs["layout"][0].hash).read_bytes())
    # Fake Klein + BiRefNet: the rare tier placed back on the 1024 reference canvas, transparent around it.
    rare = Image.open(ctx.blobs.path(a.outputs["images"][2].hash)).convert("RGBA")
    x, y, bw, bh = lay["box"]
    edits = []
    for i in range(2):
        canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        im = rare.resize((bw, bh), Image.Resampling.NEAREST)
        if distort_last and i == 1:
            im = Image.new("RGBA", (bw, bh), (255, 200, 0, 255))
        canvas.alpha_composite(im, (x, y))
        edits.append(await put_img(ctx, canvas, f"edit_{i}.png"))
    b = await RaritySet().run(ctx, {"shimmer": shimmer_on}, {"images": a.outputs["images"], "edits": edits,
                                                            "layout": a.outputs["layout"]})
    return ctx, b


async def test_rarity_set_outputs(tmp_path: Path):
    ctx, b = await _run_set(tmp_path)
    names = [r.name for r in b.outputs["images"]]
    assert names == [f"sword_{t}.png" for t in ("common", "uncommon", "rare", "epic", "legendary")]
    doc = json.loads(ctx.blobs.path(b.outputs["rarity"][0].hash).read_bytes())
    assert doc["type"] == "asset-studio/item-rarity" and doc["flagged"] == []
    assert [t["source"] for t in doc["tiers"]] == ["original", "recolor", "recolor", "klein", "klein"]
    assert all(t["iou"] > 0.9 for t in doc["tiers"][1:])
    assert len(b.outputs["shimmer"]) == 4 and len(doc["tiers"][-1]["shimmer"]) == 4
    leg = Image.open(ctx.blobs.path(b.outputs["images"][4].hash))
    assert leg.size == (96, 128) and np.asarray(leg)[50, 37, 3] > 0
    prev = Image.open(ctx.blobs.path(b.outputs["preview"][0].hash))
    assert prev.width > prev.height * 3
    assert b.outputs["palette"][0].name == "sword_palette.png"


async def test_rarity_set_flags_changed_silhouette(tmp_path: Path):
    ctx, b = await _run_set(tmp_path, shimmer_on=False, distort_last=True)
    doc = json.loads(ctx.blobs.path(b.outputs["rarity"][0].hash).read_bytes())
    assert doc["flagged"] == ["legendary"] and b.meta["flagged"] == ["legendary"]
    assert b.outputs["shimmer"] == []


async def test_export_unity_and_godot(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx, b = await _run_set(tmp_path)
    step = _row("approve", "gate.approve", {k: b.outputs[k] for k in ("preview", "images", "palette", "rarity",
                                                                        "shimmer")})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Sword")
    assert [t for t, _ in src.rarity] == ["common", "uncommon", "rare", "epic", "legendary"]
    assert len(src.rarity_shimmer) == 4

    u = dict(unity.export(src))
    assert {f"Rarity/Sword_{t}.png" for t in ("common", "legendary")} <= set(u)
    assert "spriteMode: 1" in u["Rarity/Sword_epic.png.meta"].decode()
    idx = json.loads(u["Rarity/Sword_rarity.json"])
    assert [t["sprite"] for t in idx["tiers"]][0] == "Sword_common.png" and len(idx["shimmer"]) == 4
    assert "Rarity/" in u["README.txt"].decode()

    g = dict(godot.export(src))
    tres = g["Rarity/Sword_rarity.tres"].decode()
    assert tres.startswith('[gd_resource type="Resource" load_steps=10 format=3]')
    assert 'path="Sword_legendary.png" id="5_legendary"' in tres
    assert '"legendary": ExtResource("5_legendary")' in tres and "metadata/order = PackedStringArray(" in tres
    assert 'Color("#ff9a1f")' in tres and "metadata/shimmer = [" in tres
    assert "Rarity/Sword_legendary_shimmer_3.png" in g


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "item-rarity.yaml")
    assert [s.id for s in r.steps] == ["recolor", "epic", "legendary", "rmbg", "tiers", "approve"]
    _, issues = validate((RECIPES / "item-rarity.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
