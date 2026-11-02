from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.engine.recipe import load_recipes
from studio_core.ops.anim_stabilize import anim_stabilize, clip_name

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def sprite(box: tuple[int, int, int, int], size: tuple[int, int] = (64, 64)) -> Image.Image:
    a = np.zeros((size[1], size[0], 4), np.uint8)
    a[box[1]:box[3], box[0]:box[2]] = (200, 60, 60, 255)
    return Image.fromarray(a, "RGBA")


def foot(img: Image.Image) -> tuple[float, int]:
    a = np.asarray(img.getchannel("A")) > 0
    ys, xs = np.nonzero(a)
    return (xs.min() + xs.max() + 1) / 2, int(ys.max()) + 1


def test_feet_pinned_to_same_pivot():
    frames = [sprite((20, 10, 36, 60)), sprite((24, 14, 40, 56)), sprite((18, 8, 34, 62))]
    res = anim_stabilize(frames, smooth_window=0)
    assert len({im.size for im in res.images}) == 1
    feet = [foot(im) for im in res.images]
    assert len({f[1] for f in feet}) == 1
    assert len({f[0] for f in feet}) == 1
    assert feet[0][1] == res.pivot[1]


def test_jitter_smoothed_but_slow_drift_kept():
    xs = [10, 11, 12, 13, 22, 15, 16, 17, 18]
    frames = [sprite((x, 10, x + 10, 60)) for x in xs]
    res = anim_stabilize(frames, smooth_window=5, outlier=5)
    fx = [foot(im)[0] for im in res.images]
    assert abs(fx[4] - (fx[3] + fx[5]) / 2) <= 1.5
    assert fx[-1] - fx[0] >= 6


def test_ema_runs():
    frames = [sprite((10 + (i % 2) * 4, 10, 30, 60)) for i in range(6)]
    res = anim_stabilize(frames, smooth_window=4, method="ema")
    assert len(res.images) == 6


def test_outlier_flagged_and_dropped():
    frames = [sprite((20, 10, 36, 60)) for _ in range(5)] + [sprite((10, 10, 54, 60))]
    kept = anim_stabilize(frames)
    assert kept.frames[5].flagged and len(kept.images) == 6
    dropped = anim_stabilize(frames, drop_outliers=True)
    assert dropped.kept == [0, 1, 2, 3, 4] and len(dropped.images) == 5


def test_scale_matches_reference_height():
    frames = [sprite((20, 20, 36, 40)), sprite((20, 21, 36, 41))]
    res = anim_stabilize(frames, ref_height=40, smooth_window=0, pixel=True)
    assert all(abs((foot(im)[1] - np.nonzero(np.asarray(im.getchannel("A")).any(axis=1))[0].min()) - 40) <= 1
               for im in res.images)
    assert res.frames[0].scale == pytest.approx(2.0)


def test_clips_grouped_by_name_and_empty_frame_flagged():
    names = ["walk_00.png", "walk_01.png", "idle_00.png", "idle_01.png"]
    assert clip_name("walk_01.png") == "walk" and clip_name("7.png") == "clip"
    frames = [sprite((20, 10, 36, 60)), sprite((20, 10, 36, 60)), sprite((20, 30, 36, 60)),
              Image.new("RGBA", (64, 64))]
    res = anim_stabilize(frames, names)
    assert {f.clip for f in res.frames} == {"walk", "idle"}
    assert res.frames[3].flagged == "empty"


def test_no_frames_and_bad_params():
    with pytest.raises(ValueError):
        anim_stabilize([])
    with pytest.raises(ValueError):
        anim_stabilize([sprite((1, 1, 5, 5))], method="x")
    with pytest.raises(ValueError):
        anim_stabilize([Image.new("RGBA", (8, 8))])


def test_recipe_loads_and_uses_registered_kind():
    r = load_recipes(RECIPES)["anim-stabilize"]
    assert r.step("stab").kind == "op.anim_stabilize"
    assert set(r.outputs) == {"final", "meta"}
