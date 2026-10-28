"""Image similarity for the Library: perceptual hashes (no model), cosine top-k over embeddings, near-duplicate
groups, and the "make more like this" plan (same recipe + inputs, the asset as style reference)."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import RunRow, VersionRow, VersionStatus
from .project_qa import _sheet_like

HASH_BITS = 64
NEAR_HASH = 6
DEFAULT_THRESHOLD = 0.95
STYLE_NAMES = ("style_ref", "style_image", "style_reference")
PROMPT_INPUTS = ("prompt", "idea")
_BLOB_TYPES = {"image", "images", "mesh", "file", "video"}


def flatten(img: Image.Image, max_side: int = 512) -> Image.Image:
    """RGB on white: transparent sprites would otherwise hash/embed their (arbitrary) hidden RGB."""
    im = img.copy()
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        return bg.convert("RGB")
    return im.convert("RGB")


def _gray(img: Image.Image, size: tuple[int, int]) -> np.ndarray:
    return np.asarray(img.convert("L").resize(size, Image.Resampling.LANCZOS), dtype=np.float64)


def _pack(bits: np.ndarray) -> str:
    return int.from_bytes(np.packbits(bits.astype(np.uint8).ravel()).tobytes(), "big").__format__("016x")


def dhash(img: Image.Image) -> str:
    a = _gray(img, (9, 8))
    return _pack(a[:, 1:] > a[:, :-1])


_N = 32
_DCT = np.array([[math.cos(math.pi * (2 * x + 1) * u / (2 * _N)) for x in range(_N)] for u in range(_N)])


def phash(img: Image.Image) -> str:
    d = _DCT @ _gray(img, (_N, _N)) @ _DCT.T
    low = d[:8, :8].ravel()
    return _pack(low > np.median(low[1:]))


def hamming(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return (int(a, 16) ^ int(b, 16)).bit_count()


def hashes(img: Image.Image) -> tuple[str, str]:
    flat = flatten(img, 256)
    return phash(flat), dhash(flat)


def normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


def cosine_topk(query: np.ndarray, matrix: np.ndarray, k: int) -> list[tuple[int, float]]:
    """(row, cosine) best first; rows of `matrix` and `query` are expected L2-normalised."""
    if not len(matrix):
        return []
    sims = matrix.astype(np.float32) @ np.asarray(query, dtype=np.float32).ravel()
    k = min(k, len(sims))
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx], kind="stable")]
    return [(int(i), float(sims[i])) for i in idx]


def library_versions(versions: Sequence[VersionRow], only_approved: bool = False) -> list[VersionRow]:
    """Image versions worth comparing, one per blob (approved copy first, then newest); sheets are left out."""
    by_hash: dict[str, VersionRow] = {}
    rank = lambda v: (v.status == VersionStatus.APPROVED, v.created_at)  # noqa: E731
    for v in versions:
        if (not (v.blob.mime or "").startswith("image/") or v.status == VersionStatus.REJECTED
                or (only_approved and v.status != VersionStatus.APPROVED) or _sheet_like(v.blob)):
            continue
        cur = by_hash.get(v.blob.hash)
        if cur is None or rank(v) > rank(cur):
            by_hash[v.blob.hash] = v
    return sorted(by_hash.values(), key=lambda v: v.created_at, reverse=True)


def asset_view(v: VersionRow, copies: int = 1) -> dict[str, Any]:
    return {"version_id": v.id, "run_id": v.run_id, "project_id": v.project_id, "asset_name": v.asset_name,
            "status": v.status.value if isinstance(v.status, VersionStatus) else v.status,
            "blob": v.blob.model_dump(mode="json"), "created_at": v.created_at, "copies": copies}


@dataclass
class Entry:
    phash: str | None = None
    dhash: str | None = None
    vec: np.ndarray | None = None


def pair_kind(a: Entry, b: Entry, same_blob: bool, threshold: float) -> tuple[str, float | None, int | None] | None:
    """('exact' | 'near' | 'similar', cosine, pHash distance) when the two count as duplicates, else None."""
    dist = hamming(a.phash, b.phash)
    cos = float(np.dot(a.vec, b.vec)) if a.vec is not None and b.vec is not None else None
    # pHash ignores hue, so a palette swap hashes identical; the embedding (when there is one) must agree.
    if same_blob or (dist == 0 and (hamming(a.dhash, b.dhash) or 0) <= 2 and (cos is None or cos >= 0.98)):
        return "exact", cos, dist
    if dist is not None and dist <= NEAR_HASH and (cos is None or cos >= threshold - 0.1):
        return "near", cos, dist
    if cos is not None and cos >= threshold:
        return "similar", cos, dist
    return None


def duplicate_groups(versions: Sequence[VersionRow], entries: Mapping[str, Entry],
                     threshold: float = DEFAULT_THRESHOLD, copies: Mapping[str, list[VersionRow]] | None = None
                     ) -> list[dict[str, Any]]:
    """Connected groups of near-duplicate versions. `copies` (blob hash -> one version per run with that blob) turns
    a blob published by several runs into an 'exact' group on its own."""
    vs = [v for v in versions if v.blob.hash in entries]
    parent = list(range(len(vs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pairs: list[tuple[int, int, str, float | None, int | None]] = []
    mat = None
    with_vec = [i for i, v in enumerate(vs) if entries[v.blob.hash].vec is not None]
    if len(with_vec) >= 2:
        mat = np.stack([entries[vs[i].blob.hash].vec for i in with_vec]).astype(np.float32)
    candidates: set[tuple[int, int]] = set()
    if mat is not None:
        sims = mat @ mat.T
        ii, jj = np.nonzero(np.triu(sims >= threshold - 0.1, 1))
        candidates |= {(with_vec[a], with_vec[b]) for a, b in zip(ii.tolist(), jj.tolist())}
    ph = [(i, int(entries[v.blob.hash].phash, 16)) for i, v in enumerate(vs) if entries[v.blob.hash].phash]
    for x in range(len(ph)):
        for y in range(x + 1, len(ph)):
            if (ph[x][1] ^ ph[y][1]).bit_count() <= NEAR_HASH:
                candidates.add((ph[x][0], ph[y][0]))
    for i, j in sorted(candidates):
        a, b = vs[i], vs[j]
        hit = pair_kind(entries[a.blob.hash], entries[b.blob.hash], a.blob.hash == b.blob.hash, threshold)
        # Directions/frames of one run look alike by design; only near-identical images count there.
        if hit and not (hit[0] == "similar" and a.run_id == b.run_id):
            pairs.append((i, j, *hit))
            parent[find(i)] = find(j)

    groups: dict[int, list[int]] = {}
    for i, *_ in pairs:
        groups.setdefault(find(i), [])
    for i in range(len(vs)):
        if find(i) in groups:
            groups[find(i)].append(i)
    out: list[dict[str, Any]] = []
    order = {"exact": 0, "near": 1, "similar": 2}
    for root, members in groups.items():
        gp = [p for p in pairs if find(p[0]) == root]
        out.append({
            "kind": min((p[2] for p in gp), key=order.__getitem__),
            "members": [asset_view(vs[i]) for i in members],
            "pairs": [{"a": vs[i].id, "b": vs[j].id, "kind": kind, "score": None if cos is None else round(cos, 4),
                       "phash_distance": dist} for i, j, kind, cos, dist in gp],
        })
    for h, vs_same in (copies or {}).items():
        live = [v for v in vs_same if v.status != VersionStatus.REJECTED]
        if len(live) >= 2:
            out.append({"kind": "exact", "members": [asset_view(v) for v in live],
                        "pairs": [{"a": live[0].id, "b": v.id, "kind": "exact", "score": 1.0, "phash_distance": 0}
                                  for v in live[1:]]})
    out.sort(key=lambda g: (order[g["kind"]], -len(g["members"])))
    return out


def _style_input(inputs: Mapping[str, Mapping[str, Any]]) -> str | None:
    for name, spec in inputs.items():
        if spec.get("type") != "image":
            continue
        if name in STYLE_NAMES or "style" in str(spec.get("label") or "").lower():
            return name
    return None


def _carry(run: RunRow, specs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The run's inputs minus what the project/bible put there (they are applied again on the new run), except the
    prompt they only wrapped: the subject is the user's."""
    meta = (run.params or {}).get("$meta") or {}
    filled = set(meta.get("project_applied") or []) | set((meta.get("style_bible") or {}).get("applied") or [])
    out: dict[str, Any] = {}
    for k, v in (run.inputs or {}).items():
        if k not in specs or v is None or v == "" or v == []:
            continue
        if k in filled and k not in PROMPT_INPUTS:
            continue
        out[k] = v
    return out


def more_like(blob_hash: str, run: RunRow | None, recipes: Mapping[str, Mapping[str, Any]],
              fallback: Sequence[str] = ("concept-2d-style",)) -> dict[str, Any] | None:
    """Recipe + inputs for "make more like this asset". `recipes` maps id -> {name, inputs}. Prefers the source
    recipe with the asset in its style-reference input, then the recipe's `-style` sibling, then the plain rerun;
    an asset with no source run goes to the first available fallback style recipe."""
    def plan(rid: str, inputs: dict[str, Any], style: str | None, mode: str) -> dict[str, Any]:
        if style:
            inputs = {**inputs, style: blob_hash}
        return {"recipe_id": rid, "recipe_name": recipes[rid].get("name", rid), "inputs": inputs,
                "style_input": style, "mode": mode, "source_recipe_id": run.recipe_id if run else None,
                "project_id": run.project_id if run else None}

    if run is not None and run.recipe_id in recipes:
        specs = recipes[run.recipe_id].get("inputs") or {}
        carried = _carry(run, specs)
        style = _style_input(specs)
        if style:
            return plan(run.recipe_id, carried, style, "style_ref")
        sibling = f"{run.recipe_id}-style"
        if sibling in recipes:
            sspecs = recipes[sibling].get("inputs") or {}
            sstyle = _style_input(sspecs)
            if sstyle:
                moved = {k: v for k, v in carried.items() if k in sspecs and k != sstyle
                         and (sspecs[k].get("type") in _BLOB_TYPES) == (specs[k].get("type") in _BLOB_TYPES)}
                return plan(sibling, moved, sstyle, "style_recipe")
        return plan(run.recipe_id, carried, None, "same_recipe")
    for rid in fallback:
        if rid in recipes:
            style = _style_input(recipes[rid].get("inputs") or {})
            if style:
                return plan(rid, {}, style, "style_recipe")
    return None


def rank_by_hash(query: Entry, rows: Sequence[tuple[VersionRow, Entry]], k: int) -> list[tuple[VersionRow, float, int]]:
    """Model-free fallback: closest pHash first; score is 1 - distance/64 so it reads like a similarity."""
    scored = []
    for v, e in rows:
        d = hamming(query.phash, e.phash)
        if d is not None:
            scored.append((v, 1 - d / HASH_BITS, d))
    scored.sort(key=lambda t: t[2])
    return scored[:k]

