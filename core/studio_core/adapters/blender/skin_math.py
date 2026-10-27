"""Distance skinning math, numpy only, shared by the Blender worker and tests."""
from __future__ import annotations

import numpy as np

ARM_PREFIXES = ("upper_arm_", "forearm_", "hand_")
BEHIND_MARGIN = 0.04
BEHIND_PENALTY = 3.0
WELD_TOL = 1e-4
SEED_RADIUS = 1.3
ATTACH_FRACTION = 0.6
TUBE_SECTORS = 12
TUBE_COVERAGE = 0.75
SMOOTH_ITER = 12
SURFACE_CAP = 0.5
DETOUR_RATIO = 2.5


def _segment_distances(pts, heads, tails):
    seg = tails - heads
    seg_len2 = np.maximum((seg ** 2).sum(1), 1e-12)
    t = np.clip(((pts[:, None, :] - heads[None]) * seg[None]).sum(2) / seg_len2[None], 0.0, 1.0)
    closest = heads[None] + t[..., None] * seg[None]
    return np.linalg.norm(pts[:, None, :] - closest, axis=2), closest


def _weld(pts, tol):
    """Glb import splits vertices along UV seams; merge coincident ones so the surface graph is connected."""
    _, rep = np.unique(np.round(pts / tol).astype(np.int64), axis=0, return_inverse=True)
    return rep.ravel()


def _components(n, edges):
    lab = np.arange(n)
    while True:
        m = np.minimum(lab[edges[:, 0]], lab[edges[:, 1]])
        new = lab.copy()
        np.minimum.at(new, edges[:, 0], m)
        np.minimum.at(new, edges[:, 1], m)
        new = new[new]
        if np.array_equal(new, lab):
            return lab
        lab = new


def _geodesic(seed_d, edges, lengths, cap):
    """Multi-source shortest paths over the surface graph; only edges leaving vertices that improved last round are
    relaxed, and nothing propagates past `cap`."""
    n = len(seed_d)
    g = np.where(seed_d <= cap, seed_d, np.inf).astype(np.float32)
    if not len(edges):
        return g
    src = np.concatenate([edges[:, 0], edges[:, 1]])
    dst = np.concatenate([edges[:, 1], edges[:, 0]])
    ln = np.concatenate([lengths, lengths]).astype(np.float32)
    order = np.argsort(src, kind="stable")
    src, dst, ln = src[order], dst[order], ln[order]
    start = np.searchsorted(src, np.arange(n + 1))
    active = np.nonzero(np.isfinite(g).any(1))[0]
    while len(active):
        cnt = start[active + 1] - start[active]
        ids = np.repeat(start[active] - np.cumsum(cnt) + cnt, cnt) + np.arange(int(cnt.sum()))
        cand = g[src[ids]] + ln[ids, None]
        cand[cand > cap] = np.inf
        new = g.copy()
        np.minimum.at(new, dst[ids], cand)
        changed = (new < g).any(1)
        g = new
        active = np.nonzero(changed)[0]
    return g


def _coverage(offsets, axis, comp, n_comp):
    """Per component, the share of angular sectors around the bone its vertices occupy (1 = wraps all the way)."""
    norm = float(np.linalg.norm(axis))
    if norm < 1e-9:
        return np.ones(n_comp)
    axis = axis / norm
    u = np.cross(axis, [0.0, 0.0, 1.0] if abs(axis[2]) < 0.9 else [1.0, 0.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    sector = ((np.arctan2(offsets @ v, offsets @ u) + np.pi) / (2 * np.pi) * TUBE_SECTORS).astype(int) % TUBE_SECTORS
    hits = np.bincount(comp * TUBE_SECTORS + sector, minlength=n_comp * TUBE_SECTORS).reshape(n_comp, TUBE_SECTORS)
    return (hits >= 2).mean(1)


def _nearest_distance(a, b):
    out = np.empty(len(a), np.float32)
    b2 = (b ** 2).sum(1)
    chunk = max(1, 4_000_000 // max(len(b), 1))
    for i in range(0, len(a), chunk):
        q = a[i:i + chunk]
        d2 = (q ** 2).sum(1)[:, None] + b2[None] - 2.0 * q @ b.T
        out[i:i + chunk] = np.sqrt(np.maximum(d2.min(1), 0.0))
    return out


def _chain_rank(name):
    return next(i for i, p in enumerate(ARM_PREFIXES) if name.startswith(p))


def _chain_parent(name):
    i = _chain_rank(name)
    return ARM_PREFIXES[i - 1] + name[len(ARM_PREFIXES[i]):] if i else None


def _surface(pts, edges, height):
    rep = _weld(pts, WELD_TOL * float(height))
    n = int(rep.max()) + 1
    e = rep[np.asarray(edges, np.int64).reshape(-1, 2)]
    e = np.unique(np.sort(e[e[:, 0] != e[:, 1]], axis=1), axis=0)
    x = np.zeros((n, 3), np.float32)
    x[rep] = pts
    lab = _components(n, e) if len(e) else np.arange(n)
    return rep, n, e, x, np.unique(lab, return_inverse=True)[1].ravel()


def surface_arm_distances(pts, surf, d, closest, heads, tails, names, arm, height):
    """Bone distances with arm bones measured along the mesh surface from the arm's own skin, plus the bone each
    rigid attachment vertex is bound to (-1 elsewhere).

    A cape hanging beside the arm is close in space but only reachable over the shoulder, so it stops following the
    arm. Loose shells that sit mostly on an arm without wrapping it (shield, plate) are rigid attachments bound whole
    to one arm bone, otherwise a shield bends at the elbow; a loose shell that wraps the arm (detached sleeve, separate
    arm object) keeps plain distances so the elbow still bends. Other shells the arm skin does not reach (a separate
    cape panel) are pushed off the arm."""
    rep, n, e, x, comp = surf
    n_comp = int(comp.max()) + 1
    nb = d.shape[1]
    dw = np.zeros((n, nb), np.float32)
    dw[rep] = d
    cw = np.zeros((n,) + closest.shape[1:], np.float32)
    cw[rep] = closest
    nearest = dw.argmin(1)
    counts = np.bincount(comp * nb + nearest, minlength=n_comp * nb).reshape(n_comp, nb)
    arm_idx = np.nonzero(arm)[0]
    seeds = np.full((n, len(arm_idx)), np.inf, np.float32)
    wraps = np.zeros(n_comp, bool)
    lengths = np.linalg.norm(x[e[:, 0]] - x[e[:, 1]], axis=1).astype(np.float32)
    slot = {names[j]: k for k, j in enumerate(arm_idx)}
    for k in sorted(range(len(arm_idx)), key=lambda k: _chain_rank(names[arm_idx[k]])):
        j = arm_idx[k]
        own = nearest == j
        if not own.any():
            continue
        cover = _coverage(x[own] - cw[own, j], tails[j] - heads[j], comp[own], n_comp)
        present = counts[:, j] > 0
        wraps |= present & (cover >= TUBE_COVERAGE)
        skin = present & ((cover >= TUBE_COVERAGE) | (cover == cover[present].max()))
        core = own & skin[comp] & (dw[:, j] <= SEED_RADIUS * np.percentile(dw[own, j], 25))
        parent = slot.get(_chain_parent(names[j]))
        if parent is not None and core.any():
            # A cape edge touching the hanging hand sits as close to the hand bone as the glove does, but over the
            # surface it is only reached from the forearm skin via the shoulder; a shield face fused to the hand is
            # reached almost straight. So a seed is dropped when that surface path is a long detour.
            up = np.isfinite(seeds[:, parent])
            linked = np.zeros(n_comp, bool)
            linked[comp[up]] = True
            check = core & linked[comp]
            if up.any() and check.any():
                reach = _geodesic(np.where(up, 0.0, np.inf).astype(np.float32)[:, None], e, lengths,
                                  SURFACE_CAP * float(height))[:, 0]
                ids = np.nonzero(check)[0]
                straight = _nearest_distance(x[ids], x[up])
                core[ids] = reach[ids] <= DETOUR_RATIO * np.maximum(straight, float(np.median(dw[ids, j])))
        seeds[core, k] = dw[core, j]
    g = _geodesic(seeds, e, lengths, SURFACE_CAP * float(height))
    plain = dw[:, arm_idx]
    seedless = ~np.isfinite(seeds).any(0)
    g[:, seedless] = plain[:, seedless]
    frac = counts[:, arm_idx].sum(1) / np.maximum(counts.sum(1), 1)
    attach = (frac >= ATTACH_FRACTION)[comp]
    out = dw.copy()
    out[:, arm_idx] = np.where(attach[:, None], plain,
                               np.where(np.isfinite(g), np.maximum(g, plain), plain + float(height)))
    arm_counts = counts[:, arm_idx]
    prefer = np.array([names[j].startswith(("forearm_", "hand_")) for j in arm_idx])
    pick = np.where(prefer, arm_counts, -1).argmax(1)
    pick = np.where(np.where(prefer, arm_counts, 0).max(1) > 0, pick, arm_counts.argmax(1))
    rigid_comp = np.where((frac >= ATTACH_FRACTION) & ~wraps, arm_idx[pick], -1)
    return out[rep], rigid_comp[comp][rep]


def smooth_body_weights(weights, surf, arm, rigid, iterations=SMOOTH_ITER, factor=0.5):
    """Laplacian smoothing of the non-arm share of each vertex's weights over the welded surface.

    Neighbouring robe/cape vertices can land on opposite legs and tear apart mid-stride; smoothing turns that seam into
    a stretch. Arm weights stay as computed so the surface gating above survives, and rigid shells are left alone."""
    rep, n, e, _, _ = surf
    w = np.zeros((n, weights.shape[1]), np.float64)
    w[rep] = weights
    q = w[:, ~arm]
    mass = q.sum(1)
    rw = np.full(n, -1)
    rw[rep] = rigid
    move = (mass > 1e-6) & (rw < 0)
    if not len(e) or not move.any():
        return weights
    dist = np.where(move[:, None], q / np.maximum(mass, 1e-12)[:, None], 0.0)
    for _ in range(iterations):
        acc = np.zeros_like(q)
        den = np.zeros(n)
        np.add.at(acc, e[:, 0], q[e[:, 1]])
        np.add.at(acc, e[:, 1], q[e[:, 0]])
        np.add.at(den, e[:, 0], mass[e[:, 1]])
        np.add.at(den, e[:, 1], mass[e[:, 0]])
        has = move & (den > 1e-9)
        dist[has] = (1 - factor) * dist[has] + factor * acc[has] / den[has, None]
        q = np.where(move[:, None], dist * mass[:, None], q)
    w[:, ~arm] = q
    return w[rep].astype(np.float32)


def distance_weights(pts: np.ndarray, heads: np.ndarray, tails: np.ndarray, names: list[str], height: float,
                     power: float = 4.0, edges: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Nearest bone segments per vertex and their inverse-distance weights (rows sum to 1): two bones, or up to
    three after surface smoothing when `edges` are given.

    The model faces -Y. Vertices well behind an arm (capes, back banners) get pushed off arm bones, otherwise an
    attack swing drags the cape along with the arm. With `edges` arm distances follow the surface (see
    surface_arm_distances) and body weights are smoothed (see smooth_body_weights)."""
    pts = np.array(pts, np.float32)
    pts[~np.isfinite(pts)] = 0.0
    d, closest = _segment_distances(pts, heads, tails)
    arm = np.array([n.startswith(ARM_PREFIXES) for n in names])
    surf = _surface(pts, edges, height) if edges is not None and len(edges) else None
    rigid = np.full(len(pts), -1)
    if arm.any():
        if surf is not None:
            d, rigid = surface_arm_distances(pts, surf, d, closest, heads, tails, names, arm, height)
        behind = pts[:, None, 1] - closest[:, arm, 1]
        d[:, arm] += BEHIND_PENALTY * np.maximum(0.0, behind - BEHIND_MARGIN * float(height))
    near = np.argsort(d, axis=1)[:, :2]
    dn = np.take_along_axis(d, near, axis=1)
    w = 1.0 / np.maximum(dn, 1e-5) ** power
    w /= w.sum(1, keepdims=True)
    if surf is None:
        return near, w
    full = np.zeros(d.shape, np.float32)
    np.put_along_axis(full, near, w.astype(np.float32), 1)
    full = smooth_body_weights(full, surf, arm, rigid)
    stiff = rigid >= 0
    full[stiff] = 0.0
    full[stiff, rigid[stiff]] = 1.0
    near = np.argsort(-full, axis=1)[:, :3]
    w = np.take_along_axis(full, near, axis=1)
    w /= np.maximum(w.sum(1, keepdims=True), 1e-12)
    return near, w
