"""LOD chain math, shared by the core (params, report, engine export) and Blender (lod_bpy imports it). No bpy."""
import math

import numpy as np

PRESETS = {
    "pc": {"ratios": (1.0, 0.5, 0.25, 0.1), "heights": (0.5, 0.25, 0.1, 0.03), "lod0_max": 0},
    "mobile": {"ratios": (1.0, 0.5, 0.25, 0.1), "heights": (0.6, 0.3, 0.12, 0.04), "lod0_max": 5000},
}
COLLISIONS = ("auto", "box", "hull", "multi_hull", "none")
MIN_TRIS = 48
# A LOD that keeps more than this share of the previous one's triangles is not worth a draw-call variant.
MIN_GAIN = 0.85
HULL_RATIO_LIMIT = 1.4
IMPOSTOR_VIEWS = 8
IMPOSTOR_COLS = 4
CAMERA_FOV_DEG = 60.0


def _numbers(raw, name):
    items = raw if isinstance(raw, (list, tuple)) else str(raw or "").replace(";", ",").split(",")
    try:
        return [float(str(s).strip()) for s in items if str(s).strip()]
    except ValueError as e:
        raise ValueError(f"{name} must be numbers: {raw!r}") from e


def normalize_params(p):
    preset = str(p.get("preset") or "pc").lower()
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of {sorted(PRESETS)}")
    base = PRESETS[preset]
    ratios = _numbers(p.get("ratios"), "ratios") or list(base["ratios"])
    if not 2 <= len(ratios) <= 6 or any(not 0 < r <= 1 for r in ratios):
        raise ValueError("ratios needs 2..6 values in (0, 1], first = LOD0 (usually 1)")
    if any(b >= a for a, b in zip(ratios, ratios[1:])):
        raise ValueError("ratios must strictly decrease")
    targets = [int(t) for t in _numbers(p.get("targets"), "targets")]
    if targets and (len(targets) < 2 or any(t < MIN_TRIS for t in targets) or any(b >= a for a, b in zip(targets, targets[1:]))):
        raise ValueError(f"targets: >= 2 strictly decreasing triangle counts, each >= {MIN_TRIS}")
    collision = str(p.get("collision") or "auto").lower()
    if collision not in COLLISIONS:
        raise ValueError(f"collision must be one of {COLLISIONS}")
    hulls = int(p.get("hulls", 6) or 6)
    if not 2 <= hulls <= 16:
        raise ValueError("hulls must be 2..16")
    raw_cap = p.get("lod0_max_tris")
    lod0_max = int(raw_cap) if raw_cap not in (None, "", 0) else base["lod0_max"]
    if lod0_max < 0:
        raise ValueError("lod0_max_tris must be >= 0 (0 = keep the source mesh as LOD0)")
    res = int(p.get("impostor_res", 256) or 256)
    if res not in (64, 128, 256, 512):
        raise ValueError("impostor_res must be 64, 128, 256 or 512")
    return {"preset": preset, "ratios": ratios, "targets": targets, "collision": collision, "hulls": hulls,
            "lod0_max_tris": lod0_max, "impostor": bool(p.get("impostor", False)), "impostor_res": res,
            "heights": list(base["heights"]), "qa_res": int(p.get("qa_res", 128) or 128)}


def plan_lods(source_tris, cfg):
    """(kept, skipped): kept = [{level, target, ratio}] decreasing; LODs below MIN_TRIS or not clearly lighter than
    the previous one are dropped with the reason."""
    source_tris = int(source_tris)
    lod0 = source_tris
    if cfg["lod0_max_tris"] and source_tris > cfg["lod0_max_tris"]:
        lod0 = cfg["lod0_max_tris"]
    goals = [min(g, source_tris) for g in cfg["targets"]] or [max(1, round(lod0 * r)) for r in cfg["ratios"]]
    lod0 = goals[0]
    kept = [{"level": 0, "target": lod0, "ratio": round(lod0 / max(1, source_tris), 4)}]
    skipped = []
    for goal in goals[1:]:
        prev = kept[-1]["target"]
        if goal < MIN_TRIS:
            skipped.append({"target": goal, "reason": f"under {MIN_TRIS} triangles"})
        elif goal > prev * MIN_GAIN:
            skipped.append({"target": goal, "reason": f"not under {MIN_GAIN:.0%} of LOD{kept[-1]['level']} ({prev})"})
        else:
            kept.append({"level": len(kept), "target": goal, "ratio": round(goal / max(1, source_tris), 4)})
    return kept, skipped


def keep_lod(prev_tris, tris):
    return tris <= prev_tris * MIN_GAIN and tris >= MIN_TRIS


def transition_heights(count, cfg):
    """Screen-relative height at which LOD i switches to LOD i+1 (Unity LODGroup), decreasing; the last entry is the
    cull threshold (the impostor, when baked, takes the slot before it)."""
    base = list(cfg["heights"])
    while len(base) < count:
        base.append(base[-1] * 0.5)
    return [round(h, 4) for h in base[:count]]


def lod_distances(size, heights, fov=CAMERA_FOV_DEG):
    """Camera distance (m) where an object `size` metres tall fills `height` of the view: Godot visibility ranges."""
    k = 2.0 * math.tan(math.radians(fov) / 2.0)
    return [round(float(size) / (k * h), 3) for h in heights]


def volume_ratio(hull_volume, mesh_volume):
    if not mesh_volume or mesh_volume <= 0:
        return None
    return round(float(hull_volume) / float(mesh_volume), 4)


def pick_collision(mode, ratio, limit=HULL_RATIO_LIMIT):
    """auto: a single hull while it hugs the mesh, several hulls once it bloats (an L / ring / arch)."""
    if mode != "auto":
        return mode
    return "hull" if ratio is None or ratio <= limit else "multi_hull"


def mask_iou(a, b):
    a, b = np.asarray(a, dtype=bool), np.asarray(b, dtype=bool)
    union = int(np.logical_or(a, b).sum())
    return 1.0 if union == 0 else round(int(np.logical_and(a, b).sum()) / union, 4)


def cluster_points(points, k, voxel):
    """Labels 0..k-1 for point samples: voxel-quantised so a dense region weighs like a sparse one, then k-means from
    farthest-point seeds over the occupied voxel centres."""
    pts = np.asarray(points, dtype=np.float64)
    cells = np.floor(pts / max(voxel, 1e-9)).astype(np.int64)
    uniq, inverse = np.unique(cells, axis=0, return_inverse=True)
    centres = (uniq + 0.5) * voxel
    k = max(1, min(int(k), len(centres)))
    seeds = [0]
    d = np.linalg.norm(centres - centres[0], axis=1)
    for _ in range(1, k):
        i = int(np.argmax(d))
        seeds.append(i)
        d = np.minimum(d, np.linalg.norm(centres - centres[i], axis=1))
    c = centres[seeds].copy()
    lab = np.zeros(len(centres), dtype=np.int64)
    for _ in range(10):
        lab = np.argmin(((centres[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
        for j in range(k):
            m = centres[lab == j]
            if len(m):
                c[j] = m.mean(0)
    return lab[inverse.reshape(-1)]


def box_from_bounds(lo, hi):
    lo, hi = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)
    return {"center": [round(float(x), 5) for x in (lo + hi) / 2], "size": [round(float(x), 5) for x in hi - lo]}


def silhouette_views():
    """(azimuth, elevation) of the QA renders: front, side, back-quarter, top."""
    return [(0.0, 0.0), (90.0, 0.0), (45.0, 30.0), (0.0, 89.9)]


def impostor_views():
    return [(i * 360.0 / IMPOSTOR_VIEWS, 15.0) for i in range(IMPOSTOR_VIEWS)]
