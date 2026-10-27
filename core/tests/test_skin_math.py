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


def _ring(center, axis_z, radius, n=12):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([center[0] + radius * np.cos(a), center[1] + radius * np.sin(a), np.full(n, axis_z)], 1)


def _tube(x, y, z0, z1, radius, rings=8, n=12):
    pts = np.concatenate([_ring((x, y), z, radius, n) for z in np.linspace(z0, z1, rings)])
    edges = []
    for r in range(rings):
        for k in range(n):
            i = r * n + k
            edges.append((i, r * n + (k + 1) % n))
            if r + 1 < rings:
                edges.append((i, i + n))
    return pts.astype(np.float32), np.array(edges)


def _strip(x, y0, y1, z0, z1, nz=10, ny=3):
    zz, yy = np.meshgrid(np.linspace(z0, z1, nz), np.linspace(y0, y1, ny), indexing="ij")
    pts = np.stack([np.full(zz.size, x), yy.ravel(), zz.ravel()], 1).astype(np.float32)
    edges = [(i * ny + j, i * ny + j + 1) for i in range(nz) for j in range(ny - 1)]
    edges += [(i * ny + j, (i + 1) * ny + j) for i in range(nz - 1) for j in range(ny)]
    return pts, np.array(edges)


def _merge(*parts):
    pts, edges, off = [], [], 0
    for p, e in parts:
        pts.append(p)
        edges.append(e + off)
        off += len(p)
    return np.concatenate(pts), np.concatenate(edges)


LEG_NAMES = NAMES + ["thigh_l"]
LEG_HEADS = np.concatenate([HEADS, [[0.1, 0, 0.9]]]).astype(np.float32)
LEG_TAILS = np.concatenate([TAILS, [[0.1, 0, 0.3]]]).astype(np.float32)


def _arm_weights(pts, edges):
    near, w = distance_weights(pts, LEG_HEADS, LEG_TAILS, LEG_NAMES, height=1.8, edges=edges)
    is_arm = np.array([n.startswith(("upper_arm_", "forearm_")) for n in LEG_NAMES])
    return (w * is_arm[near]).sum(1)


def test_surface_keeps_arm_skin_on_arm():
    arm, ae = _tube(0.2, 0.0, 0.95, 1.4, 0.03)
    w = _arm_weights(arm, ae)
    assert w.min() > 0.9


def test_loose_panel_beside_arm_stays_with_torso():
    # Separate cape panel hanging 6cm outside the arm and on down past the hip: euclidean skinning glues its upper
    # half to the arm.
    arm, ae = _tube(0.2, 0.0, 0.95, 1.4, 0.03)
    torso, te = _tube(0.0, 0.0, 0.9, 1.5, 0.08)
    panel, pe = _strip(0.26, -0.03, 0.03, 0.2, 1.4, nz=24)
    pts, edges = _merge((arm, ae), (torso, te), (panel, pe))
    w = _arm_weights(pts, edges)
    n_arm, n_torso = len(arm), len(torso)
    assert w[:n_arm].min() > 0.9
    assert w[n_arm + n_torso:].max() < 0.1
    near, _ = distance_weights(panel, LEG_HEADS, LEG_TAILS, LEG_NAMES, height=1.8)
    assert (np.array(LEG_NAMES)[near[:, 0]] == "upper_arm_l").any()


def test_fused_cape_reached_only_over_shoulder_leaves_arm():
    # Cape strip beside the arm, attached to the torso at the top only.
    arm, ae = _tube(0.2, 0.0, 0.95, 1.4, 0.03)
    torso, te = _tube(0.0, 0.0, 0.9, 1.5, 0.08)
    strip, se = _strip(0.27, 0.0, 0.04, 0.95, 1.5)
    pts, edges = _merge((arm, ae), (torso, te), (strip, se))
    top_strip = len(arm) + len(torso) + np.nonzero(np.isclose(strip[:, 2], 1.5))[0]
    top_torso = len(arm) + np.nonzero(np.isclose(torso[:, 2], 1.5))[0][:len(top_strip)]
    edges = np.concatenate([edges, np.stack([top_strip, top_torso], 1)])
    w = _arm_weights(pts, edges)
    lower = len(arm) + len(torso) + np.nonzero(strip[:, 2] < 1.25)[0]
    assert w[lower].max() < 0.1


HAND_NAMES = ["spine", "upper_arm_l", "forearm_l", "hand_l"]
HAND_HEADS = np.array([[0, 0, 0.9], [0.2, 0, 1.45], [0.2, 0, 1.15], [0.2, 0, 0.95]], np.float32)
HAND_TAILS = np.array([[0, 0, 1.5], [0.2, 0, 1.15], [0.2, 0, 0.95], [0.2, 0, 0.82]], np.float32)


def _shoulder_linked(arm, ae, torso, te):
    pts, edges = _merge((arm, ae), (torso, te))
    top_arm = np.nonzero(np.isclose(arm[:, 2], arm[:, 2].max()))[0]
    top_torso = len(arm) + np.nonzero(np.isclose(torso[:, 2], torso[:, 2].max()))[0][:len(top_arm)]
    return pts, np.concatenate([edges, np.stack([top_arm, top_torso], 1)])


def test_cape_edge_touching_the_hand_is_not_hand_skin():
    # Same shell as the arm (joined at the shoulder) and as close to the hand bone as the arm skin; only a detour over
    # the shoulder reaches it from the forearm.
    arm, ae = _tube(0.2, 0.0, 0.82, 1.45, 0.03, rings=14)
    torso, te = _tube(0.0, 0.0, 0.9, 1.5, 0.08, rings=24)
    body, be = _shoulder_linked(arm, ae, torso, te)
    edge, ee = _strip(0.232, -0.01, 0.01, 0.8, 1.1, nz=8)
    pts, edges = _merge((body, be), (edge, ee))
    ring = len(arm) + np.nonzero(np.isclose(torso[:, 2], np.unique(torso[:, 2])[10]))[0]
    top = len(body) + np.nonzero(np.isclose(edge[:, 2], 1.1))[0]
    edges = np.concatenate([edges, np.stack([top, ring[:len(top)]], 1)])
    near, w = distance_weights(pts, HAND_HEADS, HAND_TAILS, HAND_NAMES, height=1.8, edges=edges)
    on_arm = (w * (np.array(HAND_NAMES)[near] != "spine")).sum(1)
    assert on_arm[:len(arm)].min() > 0.9
    assert on_arm[len(body):].max() < 0.1


def test_shield_face_fused_to_the_hand_still_follows_it():
    arm, ae = _tube(0.2, 0.0, 0.82, 1.45, 0.03, rings=14)
    torso, te = _tube(0.0, 0.0, 0.9, 1.5, 0.08, rings=24)
    body, be = _shoulder_linked(arm, ae, torso, te)
    face, fe = _strip(0.2, -0.1, -0.034, 0.84, 0.94, nz=4, ny=4)
    pts, edges = _merge((body, be), (face, fe))
    grip = len(body) + np.nonzero(np.isclose(face[:, 1], -0.034))[0]
    front = np.nonzero((arm[:, 1] < -0.025) & (arm[:, 2] < 0.95))[0]
    hold = front[np.linalg.norm(pts[grip][:, None] - arm[front][None], axis=2).argmin(1)]
    edges = np.concatenate([edges, np.stack([grip, hold], 1)])
    near, w = distance_weights(pts, HAND_HEADS, HAND_TAILS, HAND_NAMES, height=1.8, edges=edges)
    on_arm = (w * (np.array(HAND_NAMES)[near] != "spine")).sum(1)
    assert on_arm[len(body):].min() > 0.9


def test_shield_shell_on_forearm_follows_arm_only():
    arm, ae = _tube(0.2, 0.0, 0.95, 1.4, 0.03)
    torso, te = _tube(0.0, 0.0, 0.9, 1.5, 0.08)
    shield, she = _strip(0.2, -0.16, -0.08, 0.85, 1.25, ny=4)
    shield[:, 0] += np.linspace(-0.08, 0.08, len(shield))
    pts, edges = _merge((arm, ae), (torso, te), (shield, she))
    w = _arm_weights(pts, edges)
    assert w[len(arm) + len(torso):].min() > 0.99
    near, wt = distance_weights(pts, LEG_HEADS, LEG_TAILS, LEG_NAMES, height=1.8, edges=edges)
    sh = slice(len(arm) + len(torso), None)
    assert np.allclose(wt[sh, 0], 1) and len(set(near[sh, 0])) == 1
    assert LEG_NAMES[near[sh, 0][0]] == "forearm_l"


def test_robe_between_legs_is_smoothed_without_touching_arm():
    names = ["thigh_l", "thigh_r", "upper_arm_l"]
    heads = np.array([[0.1, 0, 0.9], [-0.1, 0, 0.9], [0.3, 0, 1.4]], np.float32)
    tails = np.array([[0.1, 0, 0.4], [-0.1, 0, 0.4], [0.3, 0, 1.0]], np.float32)
    xs, zs = np.linspace(-0.2, 0.2, 21), np.linspace(0.45, 0.85, 5)
    xx, zz = np.meshgrid(xs, zs, indexing="ij")
    robe = np.stack([xx.ravel(), np.full(xx.size, -0.12), zz.ravel()], 1).astype(np.float32)
    ny = len(zs)
    edges = [(i * ny + j, i * ny + j + 1) for i in range(len(xs)) for j in range(ny - 1)]
    edges += [(i * ny + j, (i + 1) * ny + j) for i in range(len(xs) - 1) for j in range(ny)]
    arm, ae = _tube(0.3, 0.0, 1.05, 1.35, 0.03)
    pts, e = _merge((robe, np.array(edges)), (arm, ae))

    def left_share(near, w, rows):
        return np.array([sum(w[i, k] for k in range(near.shape[1]) if names[near[i, k]] == "thigh_l") for i in rows])

    centre = [i * ny + 2 for i in range(len(xs))]
    raw = left_share(*distance_weights(pts, heads, tails, names, height=1.8), centre)
    near, w = distance_weights(pts, heads, tails, names, height=1.8, edges=e)
    smooth = left_share(near, w, centre)
    assert np.abs(np.diff(smooth)).max() < 0.85 * np.abs(np.diff(raw)).max()
    arm_rows = np.arange(len(robe), len(pts))
    assert np.allclose([w[i, 0] for i in arm_rows], 1, atol=1e-3) and set(near[arm_rows, 0]) == {2}


def _top_bone(pts, edges, heads=HEADS, tails=TAILS, names=NAMES):
    near, w = distance_weights(pts, heads, tails, names, height=1.8, edges=edges)
    return np.array(names)[near[:, 0]], w


def test_detached_arm_tube_still_bends_at_elbow():
    torso, te = _tube(0.0, 0.0, 0.9, 1.5, 0.08)
    sleeve, se = _tube(0.2, 0.0, 0.95, 1.4, 0.03, rings=16)
    pts, edges = _merge((torso, te), (sleeve, se))
    top, _ = _top_bone(pts, edges)
    z = sleeve[:, 2]
    s = top[len(torso):]
    assert (s[z > 1.25] == "upper_arm_l").all() and (s[z < 1.05] == "forearm_l").all()


def test_arm_bone_without_own_vertices_keeps_plain_distance():
    from studio_core.adapters.blender import skin_math as sm
    names = NAMES + ["hand_l"]
    heads = np.concatenate([HEADS, [[0.2, 0, 0.9]]]).astype(np.float32)
    tails = np.concatenate([TAILS, [[0.2, 0, 0.85]]]).astype(np.float32)
    pts, edges = _tube(0.0, 0.0, 0.9, 1.5, 0.08)
    d, closest = sm._segment_distances(pts, heads, tails)
    arm = np.array([n != "spine" for n in names])
    out, _ = sm.surface_arm_distances(pts, sm._surface(pts, edges, 1.8), d.copy(), closest, heads, tails, names, arm,
                                      1.8)
    assert np.allclose(out[:, 3], d[:, 3])


def test_degenerate_bone_and_bad_coords_stay_finite():
    heads = HEADS.copy()
    tails = TAILS.copy()
    tails[2] = heads[2]
    pts, edges = _tube(0.2, 0.0, 0.95, 1.4, 0.03)
    pts[3] = np.nan
    pts[7, 1] = np.inf
    near, w = distance_weights(pts, heads, tails, NAMES, height=1.8, edges=edges)
    assert np.isfinite(w).all() and np.allclose(w.sum(1), 1)
