"""Distance skinning math, numpy only, shared by the Blender worker and tests."""
from __future__ import annotations

import numpy as np

ARM_PREFIXES = ("upper_arm_", "forearm_", "hand_")
BEHIND_MARGIN = 0.04
BEHIND_PENALTY = 3.0


def distance_weights(pts: np.ndarray, heads: np.ndarray, tails: np.ndarray, names: list[str], height: float,
                     power: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """Two nearest bone segments per vertex and their inverse-distance weights (rows sum to 1).

    The model faces -Y. Vertices well behind an arm (capes, back banners) get pushed off arm bones, otherwise an
    attack swing drags the cape along with the arm."""
    pts = np.asarray(pts, np.float32)
    seg = tails - heads
    seg_len2 = np.maximum((seg ** 2).sum(1), 1e-12)
    t = np.clip(((pts[:, None, :] - heads[None]) * seg[None]).sum(2) / seg_len2[None], 0.0, 1.0)
    closest = heads[None] + t[..., None] * seg[None]
    d = np.linalg.norm(pts[:, None, :] - closest, axis=2)
    arm = np.array([n.startswith(ARM_PREFIXES) for n in names])
    if arm.any():
        behind = pts[:, None, 1] - closest[:, arm, 1]
        d[:, arm] += BEHIND_PENALTY * np.maximum(0.0, behind - BEHIND_MARGIN * float(height))
    near = np.argsort(d, axis=1)[:, :2]
    dn = np.take_along_axis(d, near, axis=1)
    w = 1.0 / np.maximum(dn, 1e-5) ** power
    w /= w.sum(1, keepdims=True)
    return near, w
