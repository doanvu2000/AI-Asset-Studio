"""Spine 4.2 physics for secondary parts (recipe spine-physics): hair, capes, skirts, tails, ropes and ears found by
bone / slot name get `physics` constraints with a per-kind preset. Bone runs already in the rig (hair1>hair2…) are
used as they are; a rigid region part (the auto-rig's cape layer) gets a new 2–4 bone chain hanging from its bone and
becomes a mesh weighted along that chain. Only `bones`, `skins` and `physics` of the file are rewritten, appended
bones keep existing weighted-mesh bone indices valid."""
from __future__ import annotations

import asyncio
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _put_png
from .rig import _put_json
from .spine_anim import _member_spans, driven_bones
from .spine_pose import Pose
from .spine_runtime import _region_corners
from .spine_weld import _deformed

# Spine 4.2 PhysicsConstraintData fields (SkeletonJson.readPhysics defaults: inertia 1, strength 100, damping 1,
# mass 1, wind 0, gravity 0, mix 1, limit 5000, fps 60). Damping multiplies velocity every 1/60 s, so < 1 settles.
PRESETS: dict[str, dict[str, float]] = {
    "hair": {"inertia": 0.5, "strength": 160.0, "damping": 0.85, "mass": 1.0},
    # Unity (spine-unity 4.2.121) run-stop sweep at 1.5 heights/s: 1.0 / 35 / 0.95 swung a 4-bone cape 104° and rang
    # 5.6 s; 0.8 / 90 / 0.9 with the taper below gives 65° and 2.0 s (3+ bone chains; see CLOTH_SHORT).
    "cloth": {"inertia": 0.8, "strength": 90.0, "damping": 0.9, "mass": 1.0},
    "tail": {"inertia": 0.6, "strength": 120.0, "damping": 0.88, "mass": 1.5},
    "rope": {"inertia": 0.9, "strength": 60.0, "damping": 0.92, "mass": 3.0},
    "ear": {"inertia": 0.4, "strength": 220.0, "damping": 0.8, "mass": 0.8},
}
KEYWORDS: dict[str, tuple[str, ...]] = {
    "hair": ("hair", "ponytail", "pigtail", "braid", "bang", "bangs", "toc", "tóc", "ahoge"),
    "cloth": ("cape", "cloak", "skirt", "dress", "scarf", "sash", "ribbon", "banner", "flag", "robe", "mantle",
              "tassel", "cloth", "coattail", "apron", "veil", "shawl", "tie", "necktie", "loincloth", "tabard"),
    "tail": ("tail", "duoi", "đuôi"),
    "rope": ("rope", "chain", "string", "cord", "leash", "whip", "strap", "dây"),
    "ear": ("ear", "ears", "tai"),
}
MIN_SIZE = 16.0
CELL = 24.0


def _tokens(name: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return [t for t in re.split(r"[^0-9a-zà-ỹđ]+", re.sub(r"\d+", " ", s.lower())) if t]


def classify(name: str) -> str | None:
    # Whole long words first: camelCase "ponyTail" would otherwise split into a tail.
    compact = re.sub(r"[^a-zà-ỹđ]", "", name.lower())
    for kind, words in KEYWORDS.items():
        if any(len(w) >= 6 and w in compact for w in words):
            return kind
    toks = _tokens(name)
    for kind, words in KEYWORDS.items():
        if any(t in words or (t.endswith("s") and t[:-1] in words) for t in toks):
            return kind
    return None


def spine_version_ok(doc: dict[str, Any]) -> bool:
    v = str((doc.get("skeleton") or {}).get("spine") or "")
    m = re.match(r"(\d+)\.(\d+)", v)
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (4, 2)


def _wanted(name: str, parts: list[str] | None) -> bool:
    return parts is None or any(p in name.lower() for p in parts)


def parse_parts(raw: Any) -> list[str] | None:
    """'auto' = every part the keywords find; otherwise comma names matched as substrings of bone / slot names."""
    s = str(raw or "auto").strip().lower()
    if s in ("", "auto", "all"):
        return None
    if s == "none":
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def bone_chains(doc: dict[str, Any], parts: list[str] | None = None) -> list[tuple[list[str], str]]:
    """Runs of bones of one kind (by name), root first, that no IK / transform / path / physics already drives."""
    by = {b["name"]: b for b in doc["bones"]}
    kids: dict[str, list[str]] = {}
    for b in doc["bones"]:
        if b.get("parent"):
            kids.setdefault(b["parent"], []).append(b["name"])
    taken = driven_bones(doc) | {n for c in doc.get("ik") or [] for n in (c.get("bones") or []) + [c.get("target")]}
    def same(parent: str, kind: str) -> list[str]:
        return [c for c in kids.get(parent, []) if classify(c) == kind and c not in taken]

    out = []
    for b in doc["bones"]:
        n = b["name"]
        kind = classify(n)
        if kind is None or n in taken or not _wanted(n, parts):
            continue
        parent = b.get("parent")
        # A same-kind parent continues this run unless it forks (hair_front > hair2…, hair_front1…: two strands).
        if parent and classify(parent) == kind and parent not in taken and len(same(parent, kind)) == 1:
            continue
        run = [n]
        while len(nxt := same(run[-1], kind)) == 1:
            run.append(nxt[0])
        # Zero-length holder bones only group the strands; swinging them would move every strand as one.
        while run and not float(by[run[0]].get("length", 0) or 0) > 0:
            run.pop(0)
        if run:
            out.append((run, kind))
    return out


def _descendants(doc: dict[str, Any], root: str) -> set[str]:
    out = {root}
    for b in doc["bones"]:
        if b.get("parent") in out:
            out.add(b["name"])
    return out


def hangs(corners: np.ndarray, a: np.ndarray, b: np.ndarray, kind: str, min_out: float = 0.25) -> bool:
    """The part reaches `min_out` of its height below its host bone (above it for ears): a cape below the torso, long
    hair below the head; a bodice on the chest or a belt does not."""
    ys = corners[:, 1]
    lo, hi = float(ys.min()), float(ys.max())
    if hi - lo <= 1e-6:
        return False
    if kind == "ear":
        return (hi - max(a[1], b[1])) / (hi - lo) >= min_out
    return (min(a[1], b[1]) - lo) / (hi - lo) >= min_out


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    d = b - a
    dd = float(d @ d)
    t = 0.0 if dd == 0 else min(1.0, max(0.0, float((p - a) @ d) / dd))
    return float(np.linalg.norm(p - a - t * d))


def anchor_axis(corners: np.ndarray, bone_a: np.ndarray, bone_b: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """(root, tip, width): the quad edge nearest the host bone is where the part is attached (ties: the higher one,
    parts hang); the chain runs from its midpoint to the opposite edge's."""
    ul, ur, br, bl = corners
    edges = [(ul, ur), (ur, br), (bl, br), (ul, bl)]
    mids = [(e[0] + e[1]) / 2 for e in edges]
    scored = sorted(range(4), key=lambda i: (round(_seg_dist(mids[i], bone_a, bone_b) / 2), -mids[i][1]))
    k = scored[0]
    opp = (k + 2) % 4
    width = float(np.linalg.norm(edges[k][1] - edges[k][0]))
    return mids[k], mids[opp], width


def auto_segments(length: float, width: float) -> int:
    return int(min(4, max(2, round(1 + length / max(width, 1e-6)))))


def _world_angle(m: np.ndarray) -> float:
    return math.degrees(math.atan2(m[1, 0], m[0, 0]))


def _unique(name: str, used: set[str]) -> str:
    out, k = name, 2
    while out in used:
        out, k = f"{name}_{k}", k + 1
    used.add(out)
    return out


def add_chain(doc: dict[str, Any], pose: Pose, host: str, root: np.ndarray, tip: np.ndarray, n: int,
              stem: str) -> list[str]:
    """Appends n bones from root to tip (world, y-up) under `host`, each the parent of the next."""
    used = {b["name"] for b in doc["bones"]}
    seg = (tip - root) / n
    seg_len = float(np.linalg.norm(seg))
    angle = math.degrees(math.atan2(seg[1], seg[0]))
    names = []
    m, o = pose.world[host]
    local = np.linalg.solve(m, root - o)
    scale = float(np.linalg.norm(m @ np.array([1.0, 0.0]))) or 1.0
    parent_angle = _world_angle(m)
    for k in range(n):
        name = _unique(f"{stem}_phys{k + 1}", used)
        bone: dict[str, Any] = {"name": name, "parent": names[-1] if names else host}
        if k == 0:
            bone["x"], bone["y"] = round(float(local[0]), 3), round(float(local[1]), 3)
            bone["rotation"] = round((angle - parent_angle + 180) % 360 - 180, 4)
            bone["length"] = round(seg_len / scale, 3)
        else:
            bone["x"] = round(seg_len / scale, 3)
            bone["length"] = round(seg_len / scale, 3)
        names.append(name)
        doc["bones"].append(bone)
    return names


def chain_weights(t: float, host: str, chain: list[str]) -> list[tuple[str, float]]:
    """Linear skinning along the chain: node 0 (t = 0, the attached edge) is the host, node k (t = k/n) the bone that
    ends there, so the attached edge stays put and every joint bends the cloth past it."""
    n = len(chain)
    nodes = [host, *chain]
    x = min(1.0, max(0.0, t)) * n
    k = min(n - 1, math.floor(x))
    f = x - k
    out = [(nodes[k], 1 - f), (nodes[k + 1], f)]
    return [(b, w) for b, w in out if w > 1e-4]


def chain_mesh(att: dict[str, Any], corners: np.ndarray, root: np.ndarray, tip: np.ndarray, host: str,
               chain: list[str], pose: Pose, index: dict[str, int]) -> dict[str, Any]:
    ul, ur, br, bl = corners
    nu = int(min(16, max(2, math.ceil(float(np.linalg.norm(ur - ul)) / CELL))))
    nv = int(min(16, max(2, math.ceil(float(np.linalg.norm(bl - ul)) / CELL))))
    ring = ([(i, 0) for i in range(nu)] + [(nu, k) for k in range(nv)] + [(i, nv) for i in range(nu, 0, -1)]
            + [(0, k) for k in range(nv, 0, -1)])
    order = ring + [(i, k) for k in range(1, nv) for i in range(1, nu)]
    at = {p: j for j, p in enumerate(order)}
    tris: list[int] = []
    for k in range(nv):
        for i in range(nu):
            q = [at[(i, k)], at[(i + 1, k)], at[(i + 1, k + 1)], at[(i, k + 1)]]
            tris += [q[0], q[1], q[2], q[0], q[2], q[3]]
    axis = tip - root
    ll = float(axis @ axis) or 1.0
    verts: list[float] = []
    uvs: list[float] = []
    for i, k in order:
        u, v = i / nu, k / nv
        p = (1 - u) * (1 - v) * ul + u * (1 - v) * ur + (1 - u) * v * bl + u * v * br
        infl = chain_weights(float((p - root) @ axis) / ll, host, chain)
        tot = sum(w for _, w in infl)
        verts.append(len(infl))
        for b, w in infl:
            m, o = pose.world[b]
            x, y = np.linalg.solve(m, p - o)
            verts += [index[b], round(float(x), 3), round(float(y), 3), round(w / tot, 4)]
        uvs += [round(u, 6), round(v, 6)]
    mesh: dict[str, Any] = {"type": "mesh", "uvs": uvs, "triangles": tris, "vertices": verts, "hull": len(ring),
                            "width": att.get("width", 0), "height": att.get("height", 0)}
    for key in ("name", "path", "color"):
        if key in att:
            mesh[key] = att[key]
    return mesh


# Each link rides on its parent's swing, so a 4-bone cape at the 2-bone damping spun round in spine-core (tip 270°);
# 0.015 less damping-factor per link kept it at 42° on walk while a 2-bone cape still swings 10°.
LINK_DAMPING = 0.015


# Cloth links past the second ride on a moving parent: Unity (spine-unity 4.2.121) showed 3-4 bone capes swinging
# >100° and ringing 5-7 s after a run-stop, while 2-bone cloth and hair were fine. Per extra link: less `inertia`
# (the share of the host's motion kicked into the swing), stiffer `strength` (spring pulls back faster, ω² =
# strength / mass), lower `damping` (velocity multiplier per 1/60 s, so a smaller number settles sooner) and a
# lower `limit` (the cap, in skeleton units / s, on the per-frame displacement that feeds the rotation).
# A 1-2 bone cloth has no parent swing to amplify, so it keeps the soft pre-D7 preset (35 / 0.95 swung a 2-bone cape
# ~9° on walk; the stiff one gives ~2.6°). Spine-core measured both; only 3+ bone chains need the stiff one.
CLOTH_SHORT = {"inertia": 1.0, "strength": 35.0, "damping": 0.95, "mass": 1.0}
CLOTH_SHORT_MAX_BONES = 2
CLOTH_TAPER = {"inertia": 0.1, "strength": 0.25, "damping": 0.02, "limit": 0.25}
CLOTH_INERTIA_MIN = 0.3
# `limit` scales with the bone (skeleton units are px): 6 bone lengths / s leaves a walk untouched and caps the kick
# of a run-stop.
CLOTH_LIMIT_LENGTHS = 6.0
CLOTH_LIMIT_MIN = 0.25


def link_params(kind: str, link: int) -> dict[str, float]:
    """Multipliers / offsets of the preset for the `link`-th bone (0 = root) of a chain: neutral except for cloth."""
    extra = max(0, link - 1) if kind == "cloth" else 0
    return {"inertia": -CLOTH_TAPER["inertia"] * extra, "strength": 1 + CLOTH_TAPER["strength"] * extra,
            "damping": -CLOTH_TAPER["damping"] * extra, "limit": max(CLOTH_LIMIT_MIN, 1 - CLOTH_TAPER["limit"] * extra)}


def constraint(name: str, bone: str, kind: str, order: int, strength: float = 1.0, wind: float = 0.0,
               gravity: float = 0.0, root: bool = False, link: int = 0, length: float = 0.0,
               chain_len: int = 0) -> dict[str, Any]:
    p = CLOTH_SHORT if kind == "cloth" and 0 < chain_len <= CLOTH_SHORT_MAX_BONES else PRESETS[kind]
    t = link_params(kind, link)
    cloth = kind == "cloth"
    limit = round(CLOTH_LIMIT_LENGTHS * length * t["limit"], 3) if cloth and length > 0 else 5000
    inertia = max(CLOTH_INERTIA_MIN if cloth else 0.0, p["inertia"] + t["inertia"])
    c: dict[str, Any] = {"name": name, "order": order, "bone": bone, "x": 0, "y": 0, "rotate": 1, "scaleX": 0,
                         "shearX": 0, "limit": limit, "fps": 60,
                         "inertia": round(inertia, 4),
                         "strength": round(p["strength"] * strength * t["strength"], 3),
                         "damping": round(max(0.5, p["damping"] - LINK_DAMPING * link + t["damping"]), 4),
                         "mass": p["mass"], "wind": wind, "gravity": gravity, "mix": 1}
    if root and cloth:
        # A cape / skirt root also slides a little with the body, the hem lags; other kinds only swing.
        c["x"] = 0.3
    return c


def _next_order(doc: dict[str, Any]) -> int:
    orders = [int(c.get("order", 0)) for key in ("ik", "transform", "path", "physics") for c in doc.get(key) or []]
    return max(orders, default=-1) + 1


def add_physics(doc: dict[str, Any], parts: list[str] | None = None, segments: int = 0, mesh: bool = True,
                strength: float = 1.0, wind: float = 0.0, gravity: float = 0.0, kind: str = "auto"
                ) -> tuple[dict[str, Any], dict[str, Any]]:
    """New skeleton doc + report. `kind` forces one preset for every part instead of the name-based one."""
    doc = json.loads(json.dumps(doc))
    if kind != "auto" and kind not in PRESETS:
        raise ValueError(f"preset must be auto or one of {list(PRESETS)}, got {kind!r}")
    report: dict[str, Any] = {"chains": [], "meshed": [], "skipped": [], "constraints": 0}
    chains: list[tuple[list[str], str, str]] = [(c, k, "rig") for c, k in bone_chains(doc, parts)]
    chain_bones = {b for c, _, _ in chains for b in c}

    if mesh:
        pose = Pose(doc)
        slots = {s["name"]: s for s in doc.get("slots") or []}
        deformed = _deformed(doc)
        lengths = {b["name"]: float(b.get("length", 0) or 0) for b in doc["bones"]}
        rig_kinds = {(b, k) for c, k, _ in chains for b in c}
        targets: dict[str, tuple[str, str, np.ndarray]] = {}
        covered: set[str] = set()
        def unfit(skin: str, slot_name: str, att_name: str, att: dict[str, Any], host: str, k: str) -> str | None:
            if att.get("type", "region") != "region":
                return f"{att.get('type')} attachment"
            if (skin, slot_name, att_name) in deformed:
                return "has attachment / deform keys"
            if min(float(att.get("width", 0)), float(att.get("height", 0))) < MIN_SIZE:
                return f"smaller than {MIN_SIZE:g}px"
            m, o = pose.world[host]
            if not hangs(_region_corners(att, pose, host), o, m @ np.array([lengths.get(host, 0.0), 0.0]) + o, k):
                return f"does not hang past bone {host}"
            return None

        for skin in doc.get("skins") or []:
            for slot_name, atts in (skin.get("attachments") or {}).items():
                slot = slots.get(slot_name)
                if slot is None or slot.get("bone") in chain_bones or slot_name in targets or slot_name in covered:
                    continue
                host = slot["bone"]
                for att_name, att in atts.items():
                    k = classify(slot_name) or classify(att_name)
                    if k is None or not (_wanted(slot_name, parts) or _wanted(att_name, parts)):
                        continue
                    if any((d, k) in rig_kinds for d in _descendants(doc, host)):
                        report["skipped"].append(f"{slot_name}: its bone already has a {k} chain")
                        covered.add(slot_name)
                        break
                    why = unfit(skin.get("name"), slot_name, att_name, att, host, k)
                    if why:
                        report["skipped"].append(f"{skin.get('name')}/{slot_name}/{att_name}: {why}")
                        continue
                    targets[slot_name] = (att_name, k, _region_corners(att, pose, host))
                    break
        plans = []
        for slot_name, (att_name, k, corners) in targets.items():
            host = slots[slot_name]["bone"]
            m, o = pose.world[host]
            length = lengths.get(host, 0.0)
            root, tip, width = anchor_axis(corners, o, m @ np.array([length, 0.0]) + o)
            n = segments if segments in (2, 3, 4) else auto_segments(float(np.linalg.norm(tip - root)), width)
            stem = re.sub(r"\W+", "_", slot_name).strip("_") or "part"
            chain = add_chain(doc, pose, host, root, tip, n, stem)
            plans.append((slot_name, att_name, k, host, root, tip, chain))
        if plans:
            pose = Pose(doc)
            index = {b["name"]: i for i, b in enumerate(doc["bones"])}
            for slot_name, att_name, k, host, root, tip, chain in plans:
                for skin in doc["skins"]:
                    atts = (skin.get("attachments") or {}).get(slot_name) or {}
                    for name, att in list(atts.items()):
                        if unfit(skin.get("name"), slot_name, name, att, host, k):
                            continue
                        corners = _region_corners(att, pose, host)
                        atts[name] = chain_mesh(att, corners, root, tip, host, chain, pose, index)
                        report["meshed"].append([skin.get("name"), slot_name, name])
                chains.append((chain, k, "new"))

    order = _next_order(doc)
    physics = list(doc.get("physics") or [])
    # Constraint names share one namespace in the editor and runtimes (Skeleton.findConstraint by name).
    used = {c.get("name") for key in ("ik", "transform", "path", "physics") for c in doc.get(key) or []}
    blen = {b["name"]: float(b.get("length", 0) or 0) for b in doc["bones"]}
    for chain, k, origin in chains:
        preset = k if kind == "auto" else kind
        for j, b in enumerate(chain):
            physics.append(constraint(_unique(f"phys_{b}", used), b, preset, order, strength, wind, gravity,
                                      root=j == 0, link=j, length=blen.get(b, 0.0), chain_len=len(chain)))
            order += 1
        report["chains"].append({"bones": chain, "preset": preset, "source": origin})
    report["constraints"] = sum(len(c) for c, _, _ in chains)
    if physics:
        doc["physics"] = physics
    return doc, report


PHYSICS_RANGES = {"x": (0, 1), "y": (0, 1), "rotate": (0, 1), "scaleX": (0, 1), "shearX": (0, 1), "inertia": (0, 1),
                  "damping": (0, 1), "mix": (0, 1), "strength": (0, math.inf), "limit": (0, math.inf)}


def validate_physics(doc: dict[str, Any]) -> list[str]:
    """Spine 4.2 JSON rules for `physics` (SkeletonJson.readPhysics): top-level array, unique names, an existing
    bone, unit mixes in 0..1, positive mass and fps."""
    bad: list[str] = []
    phys = doc.get("physics")
    if phys is None:
        return bad
    if not spine_version_ok(doc):
        bad.append(f"physics needs a Spine 4.2+ skeleton, file says {(doc.get('skeleton') or {}).get('spine')!r}")
    if not isinstance(phys, list):
        return [*bad, "physics must be an array"]
    bones = {b["name"] for b in doc.get("bones") or []}
    names: set[str] = set()
    for i, c in enumerate(phys):
        name = c.get("name")
        if not isinstance(name, str) or not name:
            bad.append(f"physics #{i} has no name")
        elif name in names:
            bad.append(f"physics name {name} repeats")
        names.add(name)
        if c.get("bone") not in bones:
            bad.append(f"physics {name} uses unknown bone {c.get('bone')!r}")
        for key, (lo, hi) in PHYSICS_RANGES.items():
            v = c.get(key)
            if v is not None and (not isinstance(v, (int, float)) or not lo <= v <= hi):
                bad.append(f"physics {name}: {key}={v!r} outside {lo}..{hi}")
        if not float(c.get("mass", 1)) > 0:
            bad.append(f"physics {name}: mass must be > 0")
        if not (isinstance(c.get("fps", 60), int) and c.get("fps", 60) > 0):
            bad.append(f"physics {name}: fps must be a positive integer")
        if not any(float(c.get(k, 0)) for k in ("x", "y", "rotate", "scaleX", "shearX")):
            bad.append(f"physics {name} drives nothing (x, y, rotate, scaleX, shearX all 0)")
    return bad


def physics_text(text: str, doc: dict[str, Any]) -> str:
    """`text` with its bones, skins and physics members replaced by `doc`'s (physics appended if new)."""
    out = text
    for key in ("physics", "skins", "bones"):
        if key not in doc:
            continue
        body = json.dumps(doc[key], ensure_ascii=False, separators=(",", ":"))
        top = _member_spans(out, out.index("{"))
        span = next(((vs, ve) for k, _, vs, ve in top if k == key), None)
        if span is not None:
            out = out[:span[0]] + body + out[span[1]:]
        else:
            last_end = top[-1][3]
            out = out[:last_end] + f',"{key}":' + body + out[last_end:]
    return out


def physics_readme(path: Path, runtime: str) -> str | None:
    """README line for an exported skeleton that carries physics constraints (None when it has none)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    phys = doc.get("physics") if isinstance(doc, dict) else None
    if not phys:
        return None
    bones = ", ".join(sorted({str(c.get("bone")) for c in phys})[:8])
    return (f"- Physics Spine 4.2: {len(phys)} constraint ({bones}…) — chỉ chạy với {runtime} 4.2 trở lên; bản cũ hơn "
            "bỏ qua hoặc báo lỗi khi đọc JSON. Runtime tự mô phỏng mỗi frame (Skeleton.update + Physics.update), "
            "chỉnh inertia / strength / damping / wind / gravity trong Spine Editor nếu cần.")


def bent(doc: dict[str, Any], chains: list[list[str]], degrees: float) -> dict[str, Any]:
    """Setup pose with each chain curled by `degrees` in total (spread over its bones): how a swing bends the parts."""
    out = json.loads(json.dumps(doc))
    step = {b: degrees / len(c) for c in chains for b in c}
    for b in out["bones"]:
        if b["name"] in step:
            b["rotation"] = float(b.get("rotation", 0)) + step[b["name"]]
    return out


def qa_render(before: dict[str, Any], after: dict[str, Any], chains: list[list[str]], images: Any,
              skins: list[str] | None = None, height: int = 420) -> tuple[Image.Image, dict[str, float]]:
    """[original | chains drawn | bent one way | bent the other] in one frame, plus how well the meshed file matches
    the original at rest (meshing must not move a pixel in the setup pose)."""
    from . import spine_runtime as rt
    from .spine import compare

    atts_a, atts_b = rt.skin_attachments(before, skins or []), rt.skin_attachments(after, skins or [])
    poses = [rt.Pose(before), rt.Pose(after), rt.Pose(bent(after, chains, 35)), rt.Pose(bent(after, chains, -35))]
    pts = np.concatenate([rt.attachment_points(poses[0], atts_a)]
                         + [rt.attachment_points(p, atts_b) for p in poses[1:]])
    if not len(pts):
        raise ValueError("nothing visible with these skins")
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    pad = 0.05 * max(x1 - x0, y1 - y0)
    box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    scale = height / (box[3] - box[1])
    frames = [rt.render(p, atts_a if i == 0 else atts_b, images, box, scale) for i, p in enumerate(poses)]
    match = compare(frames[0], frames[1])
    bones = {b["name"]: b for b in after["bones"]}
    d = ImageDraw.Draw(frames[1])
    for chain in chains:
        joints = []
        for b in chain:
            m, o = poses[1].world[b]
            for p in (o, m @ np.array([float(bones[b].get("length", 0)), 0.0]) + o):
                joints.append(((p[0] - box[0]) * scale, (box[3] - p[1]) * scale))
        d.line(joints, fill=(158, 140, 252, 255), width=3)
        for p in joints[::2]:
            d.ellipse((p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4), fill=(255, 179, 64, 255))
    w, h = frames[0].size
    out = Image.new("RGBA", (4 * w + 12, h), (40, 36, 52, 255))
    for i, f in enumerate(frames):
        cell = Image.new("RGBA", (w, h), (236, 232, 244, 255))
        cell.alpha_composite(f)
        out.paste(cell, (i * (w + 4), 0))
    return out, match


def _mesh_report(doc: dict[str, Any], meshed: list[list[str]]) -> list[str]:
    from .spine import _mesh_problems

    skins = {s.get("name"): s for s in doc.get("skins") or []}
    bad = []
    for skin, slot, att in meshed:
        bad += [f"mesh {skin}/{slot}/{att}: {p}" for p in _mesh_problems(skins[skin]["attachments"][slot][att], len(doc["bones"]))]
    return bad


class SpinePhysics(OpExecutor):
    kind = "op.spine_physics"
    version = "5"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from . import spine_runtime as rt
        from .spine import PLAYER_VERSION, preview_html
        from .spine_ref import _one, read_skeleton, region_images, skin_parts

        text, doc = await read_skeleton(ctx, inputs)
        if not spine_version_ok(doc):
            raise StepError("op_bad_input", "physics constraints need a Spine 4.2+ skeleton; this file is "
                            f"{(doc.get('skeleton') or {}).get('spine')!r} (re-export it from Spine 4.2)")
        seg = str(params.get("segments") or "auto").strip().lower()
        try:
            new_doc, report = await asyncio.to_thread(
                add_physics, doc, parse_parts(params.get("parts")), int(seg) if seg.isdigit() else 0,
                bool(params.get("mesh", True)), float(params.get("strength", 1.0)), float(params.get("wind", 0.0)),
                float(params.get("gravity", 0.0)), str(params.get("preset") or "auto").lower())
        except ValueError as e:
            raise StepError("op_bad_param", str(e)) from e
        if not report["chains"]:
            raise StepError("op_no_parts", "no hair / cape / skirt / tail / rope / ear part found by bone or slot "
                            "name; name them in the rig or list them in 'parts'", detail={"skipped": report["skipped"]})
        bad = validate_physics(new_doc) + _mesh_report(new_doc, report["meshed"])
        if bad:
            raise StepError("op_invalid_output", "; ".join(bad[:6]), detail={"problems": bad})
        new_text = physics_text(text, new_doc)
        src = _one(inputs, "skeleton")
        stem = Path(src.name or "skeleton.json").stem
        report |= {"runtime": "Spine 4.2+: spine-unity 4.2 / spine-godot 4.2 / spine-ts 4.2",
                   "spine": new_doc["skeleton"].get("spine"),
                   "presets": {k: PRESETS[k] for k in sorted({c["preset"] for c in report["chains"]})}}
        out: Outputs = {"skeleton": [await ctx.blobs.put_bytes(new_text.encode("utf-8"), name=f"{stem}.json",
                                                             mime="application/json")]}
        atlas = inputs.get("atlas") or []
        if atlas:
            out["atlas"], out["texture"] = [atlas[0]], list(atlas[1:])
            try:
                images = region_images(ctx, inputs)
                chains = [c["bones"] for c in report["chains"]]
                skins, _ = rt.resolve_skins(doc, skin_parts({"skin": params.get("skin", "auto")}, doc))
                qa, match = await asyncio.to_thread(qa_render, doc, new_doc, chains, images, skins)
                report["setup_match"] = match
                if match["iou"] < 0.98:
                    ctx.log(f"setup pose moved after meshing (iou {match['iou']})", "warning")
                out["preview"] = [await _put_png(ctx, qa, f"{stem}_physics.png")]
            except (StepError, ValueError, OSError) as e:
                ctx.log(f"no QA render: {e}", "warning")
            if len(atlas) == 2:
                atlas_bytes = await asyncio.to_thread(ctx.blobs.path(atlas[0].hash).read_bytes)
                page = await asyncio.to_thread(ctx.blobs.path(atlas[1].hash).read_bytes)
                page_name = next((ln.strip() for ln in atlas_bytes.decode("utf-8-sig", "replace").splitlines()
                                  if ln.strip()), atlas[1].name or "page.png")
                anims = sorted((new_doc.get("animations") or {}).keys(), key=lambda a: (a not in ("walk", "run"), a))
                page_html = preview_html(stem, new_text.encode("utf-8"), atlas_bytes, page_name, page, anims)
                out["html"] = [await ctx.blobs.put_bytes(page_html.encode("utf-8"), name=f"{stem}_physics.html",
                                                         mime="text/html")]
                report["player"] = PLAYER_VERSION
        out["report"] = [await _put_json(ctx, report, "physics.json")]
        meta: dict[str, Any] = {"chains": len(report["chains"]), "constraints": report["constraints"],
                                "meshed": len(report["meshed"]), "skipped": report["skipped"][:8]}
        if "setup_match" in report:
            meta["setup_iou"] = report["setup_match"]["iou"]
        return StepResult(outputs=out, meta=meta)


SPINE_PHYSICS_OPS: tuple[type[OpExecutor], ...] = (SpinePhysics,)
