import numpy as np

from studio_core.adapters.blender.skin_math import distance_weights

NAMES = ["spine", "upper_arm_l", "forearm_l"]
HEADS = np.array([[0, 0, 0.9], [0.2, 0, 1.45], [0.2, 0, 1.15]], np.float32)
TAILS = np.array([[0, 0, 1.5], [0.2, 0, 1.15], [0.2, 0, 0.9]], np.float32)


def arm_weight(pt):
    near, w = distance_weights(np.array([pt], np.float32), HEADS, TAILS, NAMES, height=1.8)
    return sum(float(w[0, k]) for k in range(2) if NAMES[near[0, k]] != "spine")


def test_arm_surface_still_follows_arm():
    assert arm_weight([0.23, 0.03, 1.2]) > 0.9


def test_cape_behind_arm_goes_to_torso():
    assert arm_weight([0.16, 0.25, 1.2]) < 0.1


def test_weights_normalized():
    pts = np.random.default_rng(0).uniform(-0.5, 0.5, (200, 3)).astype(np.float32) + [0, 0, 1.2]
    near, w = distance_weights(pts, HEADS, TAILS, NAMES, height=1.8)
    assert near.shape == (200, 2) and np.allclose(w.sum(1), 1)
