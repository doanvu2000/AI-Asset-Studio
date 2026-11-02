from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.engine.recipe import load_recipes
from studio_core.ops.fx import FxParams
from studio_core.ops.shadow_outline import shadow_outline

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def sprite(w: int, h: int, box: tuple[int, int, int, int]) -> Image.Image:
    a = np.zeros((h, w, 4), np.uint8)
    a[box[1]:box[3], box[0]:box[2]] = (200, 60, 60, 255)
    return Image.fromarray(a, "RGBA")


def fx(**kw) -> FxParams:
    return FxParams.from_params({"shadow": "blob", "outline": "none", **kw})


def test_canvas_grows_and_is_uniform():
    frames = [sprite(32, 32, (8, 4, 24, 32)), sprite(32, 32, (6, 2, 26, 32))]
    res = shadow_outline(frames, fx())
    assert len({im.size for im in res.images}) == 1
    assert res.canvas == res.images[0].size
    l, t, r, b = res.pad
    assert res.canvas == (32 + l + r, 32 + t + b)
    assert b > 0


def test_outline_not_clipped_when_sprite_touches_edge():
    full = sprite(16, 16, (0, 0, 16, 16))
    f = FxParams.from_params({"shadow": "none", "outline": "#112233", "width": 2})
    res = shadow_outline([full], f)
    assert all(p >= 2 for p in res.pad)
    arr = np.asarray(res.images[0])
    assert arr[0, res.pad[0] + 5, 3] == 255
    assert tuple(arr[0, res.pad[0] + 5, :3]) == (0x11, 0x22, 0x33)


def test_pivot_is_foot_plus_pad_and_consistent():
    frames = [sprite(32, 32, (8, 4, 24, 32)), sprite(32, 32, (8, 6, 24, 32))]
    res = shadow_outline(frames, fx())
    assert res.pivot == (16 + res.pad[0], 32 + res.pad[1])
    arr = np.asarray(res.images[0])
    below = arr[int(res.pivot[1]):, :, 3]
    assert below.max() > 0


def test_blob_shadow_same_across_frames():
    a, b = sprite(32, 32, (8, 4, 24, 32)), sprite(32, 32, (8, 10, 24, 32))
    res = shadow_outline([a, b], fx())
    sa = np.asarray(res.images[0])[..., 3].copy()
    sb = np.asarray(res.images[1])[..., 3].copy()
    sa[np.asarray(res.images[0])[..., 3] == 255] = 0
    sb[np.asarray(res.images[1])[..., 3] == 255] = 0
    below = int(res.pivot[1])
    assert (sa[below:] == sb[below:]).all()


def test_mixed_sizes_share_canvas_and_record_offsets():
    res = shadow_outline([sprite(20, 20, (4, 2, 16, 19)), sprite(30, 40, (9, 5, 21, 39))], fx())
    assert len({im.size for im in res.images}) == 1
    assert res.sources == [(20, 20), (30, 40)]
    assert res.offsets[0] == (5, 20) and res.offsets[1] == (0, 0)


def test_margin_adds_to_every_side():
    base = shadow_outline([sprite(24, 24, (6, 4, 18, 22))], fx())
    more = shadow_outline([sprite(24, 24, (6, 4, 18, 22))], fx(), margin=3)
    assert more.pad == tuple(p + 3 for p in base.pad)


def test_pixel_mode_hard_alpha():
    res = shadow_outline([sprite(24, 24, (6, 4, 18, 22))], fx(pixel=True, outline="#000000"))
    assert set(np.unique(np.asarray(res.images[0])[..., 3])) <= {0, 255}


def test_no_frames():
    with pytest.raises(ValueError):
        shadow_outline([], fx())


def test_recipe_loads_and_uses_registered_kind():
    r = load_recipes(RECIPES)["shadow-outline"]
    assert r.step("fx").kind == "op.shadow_outline"
    assert set(r.outputs) == {"final", "meta", "atlas"}


def test_cut_and_repack_atlas_roundtrip():
    from studio_core.ops.shadow_outline import cut_atlas, repack_atlas
    sheet = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    sheet.paste(sprite(24, 24, (4, 2, 20, 24)), (0, 0))
    sheet.paste(sprite(24, 24, (6, 2, 18, 24)).crop((4, 0, 20, 24)), (30, 4))
    doc = {"frames": {
        "a": {"frame": {"x": 0, "y": 0, "w": 24, "h": 24}, "rotated": False, "sourceSize": {"w": 24, "h": 24},
              "spriteSourceSize": {"x": 0, "y": 0, "w": 24, "h": 24}, "pivot": {"x": 0.5, "y": 1}},
        "b": {"frame": {"x": 30, "y": 4, "w": 16, "h": 24}, "rotated": False, "sourceSize": {"w": 24, "h": 24},
              "spriteSourceSize": {"x": 4, "y": 0, "w": 16, "h": 24}, "pivot": {"x": 0.5, "y": 1}}},
        "meta": {"image": "s.png"}}
    names, frames, entries = cut_atlas(sheet, doc)
    assert names == ["a", "b"] and all(f.size == (24, 24) for f in frames)
    res = shadow_outline(frames, fx())
    out, new = repack_atlas(doc, names, entries, res, "s_fx.png")
    assert set(new["frames"]) == {"a", "b"} and new["meta"]["image"] == "s_fx.png"
    fa = new["frames"]["a"]["frame"]
    assert (fa["w"], fa["h"]) == res.canvas
    fb = new["frames"]["b"]["frame"]
    assert np.asarray(out.crop((fb["x"], fb["y"], fb["x"] + fb["w"], fb["y"] + fb["h"]))).tobytes() \
        == np.asarray(res.images[1]).tobytes()


def test_recipe_accepts_sheet_and_atlas():
    r = load_recipes(RECIPES)["shadow-outline"]
    assert {"sheet", "atlas"} <= set(r.inputs) and "atlas" in r.outputs
