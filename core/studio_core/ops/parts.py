"""Character part segmentation for cutout rigs: SAM seed masks -> full partition -> joints -> layers with joint fill."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

BODY_ROLES = ("head", "torso", "arm", "hand", "leg", "cape", "prop")
# Overlapping SAM masks resolve to the earlier role; "hand" only hints the wrist and merges into the arm chain.
ROLE_PRIORITY = ("head", "hand", "arm", "leg", "foot", "torso", "cape", "prop")
REQUIRED = ("head", "torso", "arm_l", "arm_r", "leg_l", "leg_r")
SIDES = ("l", "r")

# Fractions along the limb chain (root -> tip) used when no better evidence exists.
ARM_WRIST_T = 0.78
ARM_ELBOW_OF_WRIST = 0.52
LEG_ANKLE_T = 0.9
LEG_KNEE_OF_ANKLE = 0.5
SHOULDER_MIN_OF_HEAD_W = 0.75
PROP_MAX_OF_FIGURE = 0.35

JOINT_NAMES = ("head_top", "neck", "pelvis",
               "shoulder_l", "elbow_l", "wrist_l", "hand_tip_l", "shoulder_r", "elbow_r", "wrist_r", "hand_tip_r",
               "hip_l", "knee_l", "ankle_l", "foot_tip_l", "hip_r", "knee_r", "ankle_r", "foot_tip_r")

LIMB_SEGMENTS = {
    "arm": (("shoulder", "elbow", "upper_arm"), ("elbow", "wrist", "lower_arm"), ("wrist", "hand_tip", "hand")),
    "leg": (("hip", "knee", "thigh"), ("knee", "ankle", "shin"), ("ankle", "foot_tip", "foot")),
}

# Back-to-front; the character's left limbs (image right) draw last so both arms stay readable.
DRAW_ORDER = ("cape", "thigh_r", "thigh_l", "shin_r", "shin_l", "foot_r", "foot_l", "torso", "head",
              "upper_arm_r", "lower_arm_r", "hand_r", "upper_arm_l", "lower_arm_l", "hand_l")

# (under part, over part, joint): when `over` rotates about `joint` it uncovers part of `under`.
JOINT_OVERLAPS = (("torso", "head", "neck"),
                  ("torso", "upper_arm_l", "shoulder_l"), ("torso", "upper_arm_r", "shoulder_r"),
                  ("upper_arm_l", "lower_arm_l", "elbow_l"), ("upper_arm_r", "lower_arm_r", "elbow_r"),
                  ("lower_arm_l", "hand_l", "wrist_l"), ("lower_arm_r", "hand_r", "wrist_r"),
                  ("thigh_l", "torso", "hip_l"), ("thigh_r", "torso", "hip_r"),
                  ("thigh_l", "shin_l", "knee_l"), ("thigh_r", "shin_r", "knee_r"),
                  ("shin_l", "foot_l", "ankle_l"), ("shin_r", "foot_r", "ankle_r"))

PALETTE = [(230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48), (145, 30, 180),
           (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212), (0, 128, 128), (220, 190, 255),
           (170, 110, 40), (128, 0, 0), (170, 255, 195), (128, 128, 0), (255, 215, 180), (0, 0, 128)]


class PartsError(ValueError):
    def __init__(self, code: str, message: str, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.detail = detail


Point = tuple[float, float]


@dataclass
class Partition:
    labels: np.ndarray  # int16, 0 = background, i+1 = names[i]
    names: list[str]
    joints: dict[str, Point]
    center_x: float
    hints: dict[str, Any] = field(default_factory=dict)

    def mask(self, name: str) -> np.ndarray:
        return self.labels == self.names.index(name) + 1 if name in self.names else np.zeros(self.labels.shape, bool)


def _shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    out[max(dy, 0):h + min(dy, 0), max(dx, 0):w + min(dx, 0)] = a[max(-dy, 0):h - max(dy, 0), max(-dx, 0):w - max(dx, 0)]
    return out


def grow_labels(labels: np.ndarray, allowed: np.ndarray, max_iter: int = 4096) -> np.ndarray:
    """Multi-source geodesic growth: every allowed unlabeled pixel takes the label that reaches it first."""
    lab = labels.copy()
    ys, xs = np.nonzero(allowed | (lab > 0))
    if not len(ys):
        return lab
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    sub, ok = lab[y0:y1, x0:x1], allowed[y0:y1, x0:x1]
    todo = ok & (sub == 0)
    for _ in range(max_iter):
        if not todo.any():
            break
        cand = np.zeros_like(sub)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            s = _shift(sub, dy, dx)
            take = (cand == 0) & (s > 0)
            cand[take] = s[take]
        new = todo & (cand > 0)
        if not new.any():
            break
        sub[new] = cand[new]
        todo &= ~new
    lab[y0:y1, x0:x1] = sub
    return lab


def _centroid(mask: np.ndarray) -> Point | None:
    ys, xs = np.nonzero(mask)
    return (float(xs.mean()), float(ys.mean())) if len(xs) else None


def _boundary(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pixels of `a` 4-adjacent to `b`."""
    near = np.zeros_like(b)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        near |= _shift(b, dy, dx)
    return a & near


def _bbox(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None


@dataclass
class Chain:
    root: Point
    tip: Point
    length: float

    def at(self, t: float, mask: np.ndarray) -> Point:
        """Center of the limb's cross-section at fraction t, so joints sit mid-limb even on bent limbs."""
        rx, ry = self.root
        ux, uy = (self.tip[0] - rx) / self.length, (self.tip[1] - ry) / self.length
        ys, xs = np.nonzero(mask)
        proj = (xs - rx) * ux + (ys - ry) * uy
        band = np.abs(proj - t * self.length) <= max(1.5, self.length * 0.01)
        if band.sum() >= 3:
            return float(xs[band].mean()), float(ys[band].mean())
        return rx + ux * t * self.length, ry + uy * t * self.length

    def t_of(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        ux, uy = (self.tip[0] - self.root[0]) / self.length, (self.tip[1] - self.root[1]) / self.length
        return ((xs - self.root[0]) * ux + (ys - self.root[1]) * uy) / self.length


def limb_chain(limb: np.ndarray, torso: np.ndarray) -> Chain:
    ys, xs = np.nonzero(limb)
    contact = _boundary(limb, torso)
    root = _centroid(contact)
    if root is None:
        tc = _centroid(torso)
        assert tc is not None
        i = int(np.argmin((xs - tc[0]) ** 2 + (ys - tc[1]) ** 2))
        root = float(xs[i]), float(ys[i])
    d2 = (xs - root[0]) ** 2 + (ys - root[1]) ** 2
    i = int(np.argmax(d2))
    tip = float(xs[i]), float(ys[i])
    return Chain(root, tip, max(math.sqrt(float(d2[i])), 1.0))


def partition(alpha: np.ndarray, masks: dict[str, np.ndarray]) -> Partition:
    """alpha: HxW uint8; masks: role -> HxW bool SAM mask (roles from BODY_ROLES or free accessory names)."""
    h, w = alpha.shape
    sil = alpha > 0
    solid = alpha > 127
    if solid.sum() < 64:
        raise PartsError("parts_empty", "the character image has no opaque pixels")
    seeds = {r: m & sil for r, m in masks.items() if m is not None}
    # With nothing to find, SAM answers a weapon prompt with the whole character.
    if "prop" in seeds and seeds["prop"].sum() > PROP_MAX_OF_FIGURE * sil.sum():
        seeds["prop"] = np.zeros_like(sil)
    torso = seeds.get("torso")
    center = _centroid(torso) if torso is not None and torso.any() else None
    center_x = center[0] if center else _centroid(solid)[0]  # type: ignore[index]

    names: list[str] = []
    order = [r for r in ROLE_PRIORITY if r in seeds] + [r for r in seeds if r not in ROLE_PRIORITY]
    labels = np.zeros((h, w), np.int16)
    left = np.zeros((h, w), bool)
    left[:, int(round(center_x)):] = True  # image right = character's left (front view)
    for role in order:
        m = seeds[role] & (labels == 0)
        if role == "hand":
            continue
        if role == "foot":
            role = "leg"
        if role in ("arm", "leg"):
            if role == "arm" and "hand" in seeds:
                m = (m | seeds["hand"]) & (labels == 0)
            for side, sm in (("l", m & left), ("r", m & ~left)):
                name = f"{role}_{side}"
                if name not in names:
                    names.append(name)
                labels[sm & (labels == 0)] = names.index(name) + 1
        else:
            if role not in names:
                names.append(role)
            labels[m] = names.index(role) + 1
    present = [n for n in names if (labels == names.index(n) + 1).sum() >= 16]
    missing = [r for r in REQUIRED if r not in present]
    if missing:
        raise PartsError("parts_missing", f"segmentation found no {', '.join(missing)}",
                         detail={"found": present})
    labels[~sil] = 0
    labels = grow_labels(labels, sil)
    if (sil & (labels == 0)).any():
        labels = grow_labels(labels, np.ones_like(sil))
        labels[~sil] = 0
    if _drop_stray_arms(labels, names, center):
        labels = grow_labels(labels, sil)
    _claim_sleeves(labels, names)
    _shed_coat_tails(labels, names, center, center_x)
    if _attach_props(labels, names, center_x):
        labels = grow_labels(labels, sil)

    part = Partition(labels, names, {}, center_x)
    hand = seeds.get("hand")
    part.joints, part.hints = estimate_joints(part, hand)
    return part


def _drop_stray_arms(labels: np.ndarray, names: list[str], torso_center: Point | None) -> bool:
    """SAM sometimes adds a boot or a strand of hair to the arm mask. A T-pose arm reaches out at shoulder
    height, so arm pixels below the torso center that do not connect to the arm's upper part are unlabeled
    for their neighbors to take."""
    if torso_center is None:
        return False
    below = np.arange(labels.shape[0])[:, None] > torso_center[1]
    changed = False
    for side in SIDES:
        name = f"arm_{side}"
        if name not in names:
            continue
        arm = labels == names.index(name) + 1
        anchor = arm & ~below
        if not anchor.any():
            continue
        stray = arm & ~(grow_labels(anchor.astype(np.int16), arm) > 0)
        if stray.any():
            labels[stray] = 0
            changed = True
    return changed


TORSO_CORE_COL = 0.25


def _claim_sleeves(labels: np.ndarray, names: list[str]) -> None:
    """Wide sleeves often come back from SAM as cape or torso. Body-part pixels outside the torso's width at arm
    height that touch the arm and end above the waist are a sleeve, so they join the arm; a cape that falls
    past the waist stays a cape."""
    if "torso" not in names:
        return
    torso = labels == names.index("torso") + 1
    tys = np.nonzero(torso.any(axis=1))[0]
    if not len(tys):
        return
    waist = int(tys[-1])
    # A cuff or strap SAM put in the torso would otherwise stretch the torso's width out along the arm.
    col_h = torso.sum(axis=0)
    core_cols = col_h >= TORSO_CORE_COL * col_h.max()
    body = [names.index(n) + 1 for n in ("torso", "cape") if n in names]
    cols = np.arange(labels.shape[1])[None, :]
    for side in SIDES:
        name = f"arm_{side}"
        if name not in names:
            continue
        arm = labels == names.index(name) + 1
        rows = np.nonzero(arm.any(axis=1))[0]
        if not len(rows):
            continue
        txs = np.nonzero(torso[rows[0]:rows[-1] + 1].any(axis=0) & core_cols)[0]
        if not len(txs):
            continue
        outside = cols > txs[-1] if np.nonzero(arm)[1].mean() > txs.mean() else cols < txs[0]
        cand = np.isin(labels, body) & outside
        touch = _boundary(cand, arm)
        if not touch.any():
            continue
        sleeve = grow_labels(touch.astype(np.int16), cand) > 0
        if np.nonzero(sleeve.any(axis=1))[0][-1] < waist:
            labels[sleeve] = names.index(name) + 1


def _attach_props(labels: np.ndarray, names: list[str], center_x: float) -> bool:
    """A held weapon or tool becomes `prop_l` / `prop_r` after the hand it touches, so it rides that hand; prop
    pixels that touch no hand (a sheathed sword, a quiver, a strap SAM took for a weapon where it meets the
    shoulder) are unlabeled for the body around them to take."""
    if "prop" not in names:
        return False
    i = names.index("prop")
    prop = labels == i + 1
    names[i] = "prop_r"
    if not prop.any():
        return False
    seeds = np.zeros(labels.shape, np.int16)
    for k, side in enumerate(SIDES, 1):
        if f"arm_{side}" not in names:
            continue
        arm = labels == names.index(f"arm_{side}") + 1
        xs = np.nonzero(arm)[1]
        if not len(xs):
            continue
        reach = np.abs(np.arange(labels.shape[1]) - center_x)[None, :]
        hand = arm & (reach >= ARM_OUTER_REACH * np.abs(xs - center_x).max())
        seeds[_boundary(prop, hand)] = k
    held = grow_labels(seeds, prop)
    labels[prop] = 0
    if (held == 1).any():
        names.append("prop_l")
        labels[held == 1] = len(names)
    labels[held == 2] = i + 1
    return True


ARM_OUTER_REACH = 0.6


def _shed_coat_tails(labels: np.ndarray, names: list[str], torso_center: Point | None, center_x: float) -> None:
    """SAM often takes a long coat's skirts as part of the sleeve. Arm pixels under the outstretched part of the
    arm that lean on the body and hang past the torso center are coat, so they go to the cape (or torso)."""
    if torso_center is None:
        return
    body = [names.index(n) + 1 for n in ("torso", "cape", "leg_l", "leg_r") if n in names]
    target = names.index("cape" if "cape" in names else "torso") + 1
    rows = np.arange(labels.shape[0])[:, None]
    for side in SIDES:
        name = f"arm_{side}"
        if name not in names:
            continue
        arm = labels == names.index(name) + 1
        ys, xs = np.nonzero(arm)
        if not len(xs):
            continue
        reach = np.abs(xs - center_x)
        outer = reach >= ARM_OUTER_REACH * reach.max()
        low = arm & (rows > ys[outer].max())
        touch = _boundary(low, np.isin(labels, body))
        if not touch.any():
            continue
        tail = grow_labels(touch.astype(np.int16), low) > 0
        if np.nonzero(tail.any(axis=1))[0][-1] > torso_center[1]:
            labels[tail] = target


def _shoulder(ch: Chain, arm: np.ndarray, neck: Point, min_dx: float) -> Point:
    """Pauldrons/sleeves make the arm-torso contact sit almost at the neck; push the pivot out to at least
    `min_dx` from the neck along the arm so both shoulders land at a plausible, symmetric width."""
    if abs(ch.root[0] - neck[0]) >= min_dx:
        return ch.root
    for i in range(1, 41):
        q = ch.at(i / 100, arm)
        if abs(q[0] - neck[0]) >= min_dx:
            return q
    return ch.root


def estimate_joints(p: Partition, hand: np.ndarray | None = None) -> tuple[dict[str, Point], dict[str, Any]]:
    head, torso = p.mask("head"), p.mask("torso")
    j: dict[str, Point] = {}
    hints: dict[str, Any] = {}
    neck = _centroid(_boundary(head, torso))
    hb = _bbox(head)
    assert hb is not None
    if neck is None:
        neck = ((hb[0] + hb[2]) / 2, float(hb[3]))
    j["neck"] = neck
    hc = _centroid(head)
    assert hc is not None
    j["head_top"] = (hc[0], float(hb[1]))
    for side in SIDES:
        arm = p.mask(f"arm_{side}")
        ch = limb_chain(arm, torso)
        wrist_t, src = ARM_WRIST_T, "ratio"
        if hand is not None:
            hm = hand & arm
            if hm.sum() >= 16:
                ys, xs = np.nonzero(hm)
                t = float(np.percentile(ch.t_of(xs, ys), 5))
                if 0.55 <= t <= 0.92:
                    wrist_t, src = t, "sam_hand"
        hints[f"wrist_{side}"] = src
        j[f"shoulder_{side}"] = _shoulder(ch, arm, neck, (hb[2] - hb[0]) * SHOULDER_MIN_OF_HEAD_W)
        j[f"elbow_{side}"] = ch.at(wrist_t * ARM_ELBOW_OF_WRIST, arm)
        j[f"wrist_{side}"] = ch.at(wrist_t, arm)
        j[f"hand_tip_{side}"] = ch.tip
        leg = p.mask(f"leg_{side}")
        lc = limb_chain(leg, torso)
        j[f"hip_{side}"] = lc.root
        j[f"knee_{side}"] = lc.at(LEG_ANKLE_T * LEG_KNEE_OF_ANKLE, leg)
        j[f"ankle_{side}"] = lc.at(LEG_ANKLE_T, leg)
        j[f"foot_tip_{side}"] = lc.tip
    j["pelvis"] = ((j["hip_l"][0] + j["hip_r"][0]) / 2, (j["hip_l"][1] + j["hip_r"][1]) / 2)
    return {k: (round(v[0], 1), round(v[1], 1)) for k, v in j.items()}, hints


def merge_joints(auto: dict[str, Point], proposed: dict[str, Any] | None, labels: np.ndarray, names: list[str],
                 center_x: float, tolerance: float) -> tuple[dict[str, Point], dict[str, str]]:
    """Accept a proposed joint only when it lies near its own limb; a whole-figure L/R swap is undone first."""
    out = dict(auto)
    sources = {k: "auto" for k in auto}
    if not proposed:
        return out, sources
    prop: dict[str, Point] = {}
    for k, v in proposed.items():
        if k in auto and isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(c, (int, float)) for c in v):
            prop[k] = (float(v[0]), float(v[1]))
    swapped = [k for k in prop if k.endswith(("_l", "_r"))]
    wrong = sum(1 for k in swapped if (prop[k][0] >= center_x) != k.endswith("_l"))
    if swapped and wrong > len(swapped) / 2:
        prop = {(k[:-1] + ("r" if k.endswith("l") else "l") if k in swapped else k): v for k, v in prop.items()}
    h, w = labels.shape
    owner = {"head_top": "head", "neck": "head", "pelvis": "torso"}
    for k, (x, y) in prop.items():
        if not (0 <= x < w and 0 <= y < h):
            continue
        side = k[-1] if k.endswith(("_l", "_r")) else None
        region = owner.get(k) or (f"arm_{side}" if k.split("_")[0] in ("shoulder", "elbow", "wrist", "hand")
                                  else f"leg_{side}")
        if region not in names:
            continue
        ys, xs = np.nonzero(labels == names.index(region) + 1)
        if not len(xs):
            continue
        d = math.sqrt(float(((xs - x) ** 2 + (ys - y) ** 2).min()))
        if d <= tolerance and math.dist((x, y), auto[k]) <= tolerance * 4:
            out[k] = (round(x, 1), round(y, 1))
            sources[k] = "claude"
    _keep_limbs_sane(out, sources, auto, tolerance)
    return out, sources


MID_JOINT_SPAN = (0.2, 0.8)


def _keep_limbs_sane(out: dict[str, Point], sources: dict[str, str], auto: dict[str, Point], tolerance: float
                     ) -> None:
    """Hips and shoulders sit level, at the proposed height when only one side was accepted; a limb chain whose
    middle joint lands near either end (a knee at the hip) falls back to the estimate whole."""
    def level_pairs() -> None:
        for root in ("hip", "shoulder"):
            a, b = (f"{root}_{s}" for s in SIDES)
            if a not in out or b not in out:
                continue
            ya, yb = out[a][1], out[b][1]
            if sources.get(a) != sources.get(b):
                y = ya if sources.get(a) == "claude" else yb
            elif abs(ya - yb) > tolerance / 2:
                y = round((ya + yb) / 2, 1)
            else:
                continue
            out[a], out[b] = (out[a][0], y), (out[b][0], y)

    level_pairs()
    for side in SIDES:
        for segs in LIMB_SEGMENTS.values():
            keys = [f"{segs[0][0]}_{side}", f"{segs[0][1]}_{side}", f"{segs[1][1]}_{side}", f"{segs[2][1]}_{side}"]
            if not all(k in out for k in keys[:3]):
                continue
            a, m, b = (out[k] for k in keys[:3])
            upper, lower = math.dist(a, m), math.dist(m, b)
            if not MID_JOINT_SPAN[0] <= upper / ((upper + lower) or 1.0) <= MID_JOINT_SPAN[1]:
                for k in keys:
                    if k in auto:
                        out[k], sources[k] = auto[k], "auto"
    level_pairs()


def _seg_dist(xs: np.ndarray, ys: np.ndarray, a: Point, b: Point) -> np.ndarray:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy or 1.0
    t = np.clip(((xs - ax) * dx + (ys - ay) * dy) / L2, 0.0, 1.0)
    return np.hypot(xs - (ax + t * dx), ys - (ay + t * dy))


def final_parts(p: Partition, joints: dict[str, Point]) -> dict[str, np.ndarray]:
    """Split every limb chain into its three bones by nearest joint segment."""
    out: dict[str, np.ndarray] = {}
    for name in p.names:
        m = p.mask(name)
        if not m.any():
            continue
        limb, _, side = name.partition("_")
        if limb not in LIMB_SEGMENTS:
            out[name] = m
            continue
        ys, xs = np.nonzero(m)
        segs = LIMB_SEGMENTS[limb]
        d = np.stack([_seg_dist(xs, ys, joints[f"{a}_{side}"], joints[f"{b}_{side}"]) for a, b, _ in segs])
        best = d.argmin(axis=0)
        for i, (_, _, part) in enumerate(segs):
            pm = np.zeros_like(m)
            pm[ys[best == i], xs[best == i]] = True
            out[f"{part}_{side}"] = pm
    return out


SPECK_CORE = 3


def absorb_specks(parts: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Hand slivers that hold no solid core (thinner than 2*SPECK_CORE+1 px and not attached to one) to the
    neighboring part, so they move with it instead of floating once limbs rotate."""
    names = list(parts)
    lab = np.zeros(next(iter(parts.values())).shape, np.int16)
    loose = np.zeros(lab.shape, bool)
    for i, n in enumerate(names):
        m = parts[n]
        core = m.copy()
        for _ in range(SPECK_CORE):
            e = core.copy()
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                e &= _shift(core, dy, dx)
            core = e
        kept = grow_labels(core.astype(np.int16), m) > 0
        lab[kept] = i + 1
        loose |= m & ~kept
    if not loose.any():
        return parts
    grown = grow_labels(lab, loose)
    return {n: (grown == i + 1) | (parts[n] & loose & (grown == 0)) for i, n in enumerate(names)}


def draw_order(names: list[str]) -> list[str]:
    known = [n for n in DRAW_ORDER if n in names]
    return known + [n for n in names if n not in DRAW_ORDER]


def thickness(mask: np.ndarray, a: Point, b: Point) -> float:
    length = max(math.dist(a, b), 1.0)
    return float(mask.sum()) / length


UNDER_WIDTH_SLACK = 1.2
ARM_PARTS = tuple(f"{p}_{s}" for s in SIDES for p in ("upper_arm", "lower_arm", "hand"))
BEHIND_ARMS = ("head", "cape", "torso")
BEHIND_REACH = 4
BEHIND_GAP = 32


def behind_arms(parts: dict[str, np.ndarray], max_run: int) -> dict[str, np.ndarray]:
    """Pixels where a T-pose arm crosses hair, a cape or the body: the same part shows right above and right
    below the arm in that column, so that part continues underneath and is exposed once the arm lowers.
    Where the part shows on one side only, the fill tapers off so its edge runs diagonally between the two."""
    h, w = next(iter(parts.values())).shape
    cover = np.zeros((h, w), bool)
    for n in ARM_PARTS:
        if n in parts:
            cover |= parts[n]
    lab = np.zeros((h, w), np.int8)
    names = [n for n in BEHIND_ARMS if n in parts]
    for i, n in enumerate(names):
        lab[parts[n] & ~cover] = i + 1
    both = {n: np.zeros((h, w), bool) for n in names}
    # depth from the side the part shows on: 0 at that edge of the arm, 1 at the far edge
    depth = {n: np.full((h, w), np.inf, np.float32) for n in names}
    for x in np.nonzero(cover.any(axis=0))[0]:
        ys = np.nonzero(cover[:, x])[0]
        for run in np.split(ys, np.nonzero(np.diff(ys) > 1)[0] + 1):
            y0, y1 = int(run[0]), int(run[-1])
            if y1 - y0 + 1 > max_run:
                continue
            above = lab[max(y0 - BEHIND_REACH, 0):y0, x][::-1]
            below = lab[y1 + 1:y1 + 1 + BEHIND_REACH, x]
            a = int(above[above > 0][0]) if (above > 0).any() else 0
            b = int(below[below > 0][0]) if (below > 0).any() else 0
            rel = (np.arange(y0, y1 + 1) - y0 + 0.5) / (y1 - y0 + 1)
            if a and a == b:
                both[names[a - 1]][y0:y1 + 1, x] = True
                continue
            if a:
                depth[names[a - 1]][y0:y1 + 1, x] = rel
            if b:
                depth[names[b - 1]][y0:y1 + 1, x] = 1 - rel
    out = {}
    for n in names:
        if not both[n].any():
            continue
        full = _close_rows(both[n], cover, BEHIND_GAP)
        out[n] = full | _taper(full, depth[n])
    return out


def _taper(full: np.ndarray, depth: np.ndarray) -> np.ndarray:
    """One-sided pixels next to `full` along a row: keep those shallower than 1 - (distance / one-sided span)."""
    w = full.shape[1]
    idx = np.arange(w)[None, :]
    one = np.isfinite(depth) & ~full
    left = np.maximum.accumulate(np.where(full, idx, -1), axis=1)
    right = np.minimum.accumulate(np.where(full, idx, w)[:, ::-1], axis=1)[:, ::-1]
    gap_l = np.maximum.accumulate(np.where(~one, idx, -1), axis=1)
    gap_r = np.minimum.accumulate(np.where(~one, idx, w)[:, ::-1], axis=1)[:, ::-1]
    from_left = (left >= 0) & (left == gap_l)
    from_right = (right < w) & (right == gap_r)
    dist = np.where(from_left, idx - left, np.where(from_right, right - idx, 0))
    span = np.where(from_left, gap_r - left, right - gap_l)
    reach = 1 - dist / np.maximum(span, 1)
    return one & (from_left | from_right) & (depth <= reach)


def _close_rows(m: np.ndarray, within: np.ndarray, gap: int) -> np.ndarray:
    """Fill horizontal gaps of at most `gap` pixels between set pixels, staying inside `within`."""
    w = m.shape[1]
    idx = np.arange(w)[None, :]
    left = np.maximum.accumulate(np.where(m, idx, -1), axis=1)
    right = np.minimum.accumulate(np.where(m, idx, w)[:, ::-1], axis=1)[:, ::-1]
    return m | (within & (left >= 0) & (right < w) & (right - left <= gap + 1))


def hull_mask(mask: np.ndarray) -> np.ndarray:
    from PIL import ImageDraw

    ys, xs = np.nonzero(mask)
    if len(xs) < 3:
        return mask.copy()
    rows = np.unique(ys)
    lo = np.full(rows.max() + 1, np.iinfo(np.int64).max)
    hi = np.full(rows.max() + 1, -1)
    np.minimum.at(lo, ys, xs)
    np.maximum.at(hi, ys, xs)
    pts = sorted({(int(lo[y]), int(y)) for y in rows} | {(int(hi[y]), int(y)) for y in rows})

    def half(seq: list[tuple[int, int]]) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]

    poly = half(pts) + half(pts[::-1])
    img = Image.new("1", (mask.shape[1], mask.shape[0]), 0)
    ImageDraw.Draw(img).polygon(poly, fill=1, outline=1)
    return np.asarray(img, bool) | mask


def joint_fills(parts: dict[str, np.ndarray], joints: dict[str, Point], overlap: float
                ) -> dict[str, np.ndarray]:
    """under part -> extra pixels (taken from the over part near the shared joint) it must also cover."""
    h, w = next(iter(parts.values())).shape
    yy, xx = np.mgrid[0:h, 0:w]
    fills: dict[str, np.ndarray] = {}
    bone_end = {f"{s}_{side}": f"{e}_{side}" for side in SIDES for segs in LIMB_SEGMENTS.values()
                for s, e, _ in segs}
    bone_of = {f"{part}_{side}": (f"{s}_{side}", f"{e}_{side}") for side in SIDES
               for segs in LIMB_SEGMENTS.values() for s, e, part in segs}
    for under, over, joint in JOINT_OVERLAPS:
        if under not in parts or over not in parts or joint not in joints:
            continue
        jx, jy = joints[joint]
        if over in ("head", "torso"):
            ref = parts[under] if over == "torso" else parts[over]
            size = math.sqrt(float(ref.sum()) / math.pi) * 0.9
        else:
            size = thickness(parts[over], joints[joint], joints[bone_end.get(joint, joint)])
        r = max(6.0, overlap * size)
        region = parts[over] & ((xx - jx) ** 2 + (yy - jy) ** 2 <= r * r)
        # The covered area must keep the under part's own shape, or a pauldron or skirt edge gets baked into it
        # and shows as a blob once the limb swings away.
        if under == "torso":
            region &= hull_mask(parts["torso"])
        elif under in bone_of:
            a, b = (joints.get(j) for j in bone_of[under])
            if a is not None and b is not None:
                half = thickness(parts[under], a, b) / 2 * UNDER_WIDTH_SLACK
                region &= _seg_dist(xx, yy, a, b) <= max(half, 3.0)
        if region.any():
            fills[under] = fills.get(under, np.zeros((h, w), bool)) | region
    return fills


def colorize(labels: np.ndarray, rgba: np.ndarray, names: list[str]) -> np.ndarray:
    base = rgba[..., :3].astype(np.float32)
    a = rgba[..., 3:4].astype(np.float32) / 255
    out = base * a + 255 * (1 - a)
    out = out * 0.35 + 255 * 0.65
    for i, _ in enumerate(names):
        m = labels == i + 1
        out[m] = base[m] * 0.45 + np.array(PALETTE[i % len(PALETTE)], np.float32) * 0.55
    return out.clip(0, 255).astype(np.uint8)


def overlay(rgba: Image.Image, labels: np.ndarray, names: list[str], joints: dict[str, Point],
            bones: list[tuple[str, str]] | None = None) -> Image.Image:
    from PIL import ImageDraw

    img = Image.fromarray(colorize(labels, np.asarray(rgba.convert("RGBA")), names))
    d = ImageDraw.Draw(img)
    r = max(3, round(max(img.size) / 160))
    for a, b in bones or []:
        if a in joints and b in joints:
            d.line([joints[a], joints[b]], fill=(20, 20, 20), width=max(2, r // 2))
    for name, (x, y) in joints.items():
        col = (0, 90, 255) if name.endswith("_l") else (255, 60, 0) if name.endswith("_r") else (0, 160, 0)
        d.ellipse((x - r, y - r, x + r, y + r), fill=col, outline=(0, 0, 0))
    for i, name in enumerate(names):
        c = _centroid(labels == i + 1)
        if c:
            d.text((c[0] + r, c[1]), name, fill=(0, 0, 0))
    return img


SKELETON_LINES = [("head_top", "neck"), ("neck", "pelvis")] + [
    (a, b) for side in SIDES for a, b in ((f"neck", f"shoulder_{side}"), (f"shoulder_{side}", f"elbow_{side}"),
                                          (f"elbow_{side}", f"wrist_{side}"), (f"wrist_{side}", f"hand_tip_{side}"),
                                          ("pelvis", f"hip_{side}"), (f"hip_{side}", f"knee_{side}"),
                                          (f"knee_{side}", f"ankle_{side}"), (f"ankle_{side}", f"foot_tip_{side}"))]


def round16(n: int, minimum: int = 64) -> int:
    return max(minimum, (n + 15) // 16 * 16)


def fill_request(rgba: np.ndarray, part: np.ndarray, hole: np.ndarray, margin: int = 24
                 ) -> tuple[Image.Image, list[int]]:
    """Inpaint input: the part alone on white, hole made transparent (ComfyUI LoadImage mask = 1 - alpha).
    Box sides are multiples of 16 for Flux2 latents; it may extend past the canvas (padded white)."""
    h, w = part.shape
    box = _bbox(part | hole)
    assert box is not None
    x0, y0, x1, y1 = box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin
    bw, bh = round16(x1 - x0), round16(y1 - y0)
    x0 -= (bw - (x1 - x0)) // 2
    y0 -= (bh - (y1 - y0)) // 2
    x1, y1 = x0 + bw, y0 + bh
    canvas = np.full((bh, bw, 4), 255, np.uint8)
    sx0, sy0, sx1, sy1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    src = rgba[sy0:sy1, sx0:sx1].astype(np.float32)
    keep = part[sy0:sy1, sx0:sx1]
    a = src[..., 3:4] / 255 * keep[..., None]
    rgb = src[..., :3] * a + 255 * (1 - a)
    region = canvas[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0]
    hole_c = hole[sy0:sy1, sx0:sx1]
    # A white hole reads as background to the model; pre-filling it with the part's own edge colors keeps the
    # silhouette and lets partial denoise only re-texture it.
    region[..., :3] = smear(rgb.clip(0, 255).astype(np.uint8), keep & (src[..., 3] > 127), hole_c)
    region[..., 3] = np.where(hole_c, 0, 255)
    return Image.fromarray(canvas, "RGBA"), [x0, y0, x1, y1]


def smear(rgb: np.ndarray, known: np.ndarray, target: np.ndarray, max_iter: int = 512) -> np.ndarray:
    """Propagate colors from `known` pixels into `target` pixels (mean of known 4-neighbors, ring by ring)."""
    out = rgb.astype(np.float32)
    known = known.copy()
    todo = target & ~known
    for _ in range(max_iter):
        if not todo.any() or not known.any():
            break
        acc = np.zeros_like(out)
        cnt = np.zeros(known.shape, np.float32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            k = _shift(known, dy, dx)
            acc += _shift(out * known[..., None], dy, dx)
            cnt += k
        new = todo & (cnt > 0)
        if not new.any():
            break
        out[new] = acc[new] / cnt[new][:, None]
        known |= new
        todo &= ~new
    return out.clip(0, 255).astype(np.uint8)
