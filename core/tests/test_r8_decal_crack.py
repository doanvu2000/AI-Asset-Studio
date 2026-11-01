from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from studio_core.ops.decal import DecalPack, make_decal


def _edt(m: np.ndarray, reach: int = 16) -> np.ndarray:
    h, w = m.shape
    pad = np.pad(~m, reach, constant_values=True)
    best = np.full(m.shape, reach * reach, np.float32)
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            d2 = dy * dy + dx * dx
            if 0 < d2 <= reach * reach:
                best = np.where(pad[reach + dy:reach + dy + h, reach + dx:reach + dx + w] & (d2 < best), d2, best)
    return np.sqrt(best)


def _skeleton(m: np.ndarray) -> np.ndarray:
    m = m.copy()
    changed = True
    while changed:
        changed = False
        for phase in (0, 1):
            p = np.pad(m, 1)
            n = [p[0:-2, 1:-1], p[0:-2, 2:], p[1:-1, 2:], p[2:, 2:], p[2:, 1:-1], p[2:, :-2], p[1:-1, :-2], p[0:-2, :-2]]
            count = sum(x.astype(int) for x in n)
            trans = sum(((~n[i]) & n[(i + 1) % 8]).astype(int) for i in range(8))
            keep = (~(n[0] & n[2] & n[4]) & ~(n[2] & n[4] & n[6])) if phase == 0 else (
                ~(n[0] & n[2] & n[6]) & ~(n[0] & n[4] & n[6]))
            kill = m & (count >= 2) & (count <= 6) & (trans == 1) & keep
            if kill.any():
                m, changed = m & ~kill, True
    return m


def width_std(alpha: np.ndarray) -> float:
    solid = alpha > 0.5
    return float((2 * _edt(solid)[_skeleton(solid)]).std())


def _crack() -> Image.Image:
    img = Image.new("RGB", (1024, 1024), (232, 233, 230))
    d = ImageDraw.Draw(img)
    d.line([(40, 500), (260, 530), (500, 500), (740, 540), (980, 490)], fill=(30, 28, 26), width=2)
    d.line([(500, 500), (540, 700)], fill=(40, 38, 36), width=2)
    return img


def _run(vary: bool, seed: int = 7):
    return make_decal(_crack(), None, "key", 512, 0.06, 4.0, False, "wall cracks", "auto", seed, vary)


def test_crack_width_varies_more_and_branches() -> None:
    old, _, m_old = _run(False)
    new, _, m_new = _run(True)
    s_old = width_std(np.asarray(old)[..., 3] / 255)
    s_new = width_std(np.asarray(new)[..., 3] / 255)
    assert s_new > s_old * 1.3
    assert m_old["branches"] == 0 and m_new["branches"] >= 2
    assert m_new["coverage"] > 0.003


def test_crack_variation_is_deterministic_and_seeded() -> None:
    a1 = np.asarray(_run(True, 7)[0])
    assert np.array_equal(a1, np.asarray(_run(True, 7)[0]))
    assert not np.array_equal(a1, np.asarray(_run(True, 8)[0]))


def test_crack_variation_leaves_organic_and_tiny_inputs_alone() -> None:
    _, _, dirt = make_decal(_crack(), None, "key", 512, 0.06, 4.0, False, "dirt stain", "auto", 7, True)
    assert dirt["branches"] == 0
    tiny = Image.new("RGB", (48, 48), (232, 233, 230))
    ImageDraw.Draw(tiny).line([(2, 24), (45, 22)], fill=(30, 28, 26), width=1)
    _, _, meta = make_decal(tiny, None, "key", 256, 0.06, 4.0, False, "crack", "auto", 3, True)
    assert meta["coverage"] > 0


def test_decal_op_version_bumped() -> None:
    assert DecalPack.version == "2"


def test_crack_twigs_are_ink_coloured() -> None:
    new = np.asarray(_run(True)[0], np.float32)
    old = np.asarray(_run(False)[0], np.float32)
    ink_old = old[old[..., 3] > 128][:, :3].max()
    seen = new[new[..., 3] > 40][:, :3]
    assert seen.max() <= max(ink_old, 100) + 25
