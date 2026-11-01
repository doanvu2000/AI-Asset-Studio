from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
from test_r7_spine_face import FEATURES, HAIR, _work, head_image, located

from studio_core.ops import spine_face as sf

SKIN_RGB = np.array([240.0, 200.0, 160.0])


def _canvas(size: tuple[int, int] = (200, 100)) -> tuple[np.ndarray, np.ndarray]:
    edit = np.tile(SKIN_RGB, (size[1], size[0], 1))
    hole = np.zeros((size[1], size[0]), bool)
    hole[20:80, 20:180] = True
    return edit, hole


def _stroke(edit: np.ndarray, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    edit[y0:y1, x0:x1] = (20, 15, 10)


def test_detached_bit_beside_the_brow_is_a_stray() -> None:
    edit, hole = _canvas()
    _stroke(edit, (50, 40, 150, 52))
    _stroke(edit, (30, 25, 42, 28))
    bad = sf.stray_strokes(edit, hole, [(40.0, 30.0, 160.0, 70.0)], np.zeros(hole.shape, bool), np.ones(hole.shape, bool))
    assert bad[25:28, 30:42].all() and not bad[40:52, 50:150].any()


def test_both_brows_are_kept_and_each_owns_its_bits() -> None:
    edit, hole = _canvas()
    _stroke(edit, (25, 40, 85, 50))
    _stroke(edit, (115, 40, 175, 50))
    _stroke(edit, (120, 60, 128, 63))
    bad = sf.stray_strokes(edit, hole, [(20.0, 30.0, 90.0, 70.0), (110.0, 30.0, 180.0, 70.0)],
                           np.zeros(hole.shape, bool), np.ones(hole.shape, bool))
    assert bad[60:63, 120:128].all() and bad.sum() == 24


def test_line_art_continuing_from_outside_the_hole_is_not_a_stray() -> None:
    edit, hole = _canvas()
    ink = np.zeros(hole.shape, bool)
    _stroke(edit, (50, 40, 150, 52))
    _stroke(edit, (20, 25, 30, 28))
    ink[25:28, 10:20] = True
    edit[25:28, 10:20] = (20, 15, 10)
    bad = sf.stray_strokes(edit, hole, [(40.0, 30.0, 160.0, 70.0)], ink, np.ones(hole.shape, bool))
    assert not bad.any()


def test_clean_edit_has_no_strays() -> None:
    edit, hole = _canvas()
    _stroke(edit, (50, 40, 150, 52))
    assert not sf.stray_strokes(edit, hole, [(40.0, 30.0, 160.0, 70.0)], np.zeros(hole.shape, bool),
                                np.ones(hole.shape, bool)).any()
    assert not sf.stray_strokes(np.tile(SKIN_RGB, (100, 200, 1)), hole, [], np.zeros(hole.shape, bool),
                                np.ones(hole.shape, bool)).any()


def test_brow_tip_past_claudes_box_is_repainted_not_kept_as_hair() -> None:
    """Live "sad": the old brow's end ran out of Claude's box into the fringe, was kept as hair and stayed as a black
    bit beside the new brow."""
    img = head_image()
    d = ImageDraw.Draw(img)
    x0, y0, _, y1 = FEATURES["brow_l"]
    d.polygon([(x0 - 24, y0 - 8), (x0 - 6, y0 - 8), (x0 - 6, y1 + 6), (x0 - 24, y1 + 6)], fill=HAIR)
    d.rectangle((x0 - 6, y0, x0 - 1, y1 - 1), fill=(70, 40, 20, 255))
    work, s = _work(img)
    fh = sf.feature_holes(work, located(s), s)
    tip = np.zeros(fh.ink.shape, bool)
    tip[int((y0 + 1) * s):int((y1 - 1) * s), int((x0 - 5) * s):int((x0 - 2) * s)] = True
    assert "brow" in fh.holes and (fh.holes["brow"] & tip).sum() > 0.8 * tip.sum()
    assert not (fh.keep.get("brow", np.zeros_like(tip)) & tip).any()


def test_brow_hole_reaches_the_faint_line_under_the_brow() -> None:
    """The old brow's faint underside left a loose dash in the strip between the brow and eye holes."""
    img = head_image()
    x0, _, x1, _ = FEATURES["brow_l"]
    ImageDraw.Draw(img).line((x0 + 4, 47, x1 - 4, 47), fill=(218, 172, 132, 255), width=1)
    work, s = _work(img)
    loc = located(s) | {k: [v * s for v in (FEATURES[k][0], 36, FEATURES[k][2], 46)] for k in ("brow_l", "brow_r")}
    fh = sf.feature_holes(work, loc, s)
    cols = slice(int((x0 + 8) * s), int((x1 - 8) * s))
    assert fh.holes["brow"][int(47.2 * s), cols].all()
    assert not (fh.holes["brow"] & fh.holes["eyes"]).any()
