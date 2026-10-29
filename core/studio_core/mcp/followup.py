"""Python twin of app/src/renderer/src/composables/followUp.ts ("Dùng tiếp cho…"): which recipes an output can feed,
with their file inputs prefilled by blob hash. Keep the two in step."""
from __future__ import annotations

from typing import Any

VIDEO_EXT = ("mp4", "webm", "mov", "mkv")
BLOB_TYPES = {"image", "images", "mesh", "file", "video"}
PAGE_KEYS = ("texture", "textures", "pages")
# FollowUpMenu.vue: a concept/variants image can start the character kit (8-dir, then Spine chained in core).
KIT = {"first": "sprite-8dir-2d", "then": "spine-character-image"}
ANIM = "sprite-anim-video"
ANIM_ACTIONS = ("idle", "walk", "run", "slash", "jump", "hit", "cast")


def ext_of(name: str | None) -> str:
    return name.lower().rsplit(".", 1)[-1] if name and "." in name else ""


def fits(spec: dict[str, Any], b: dict[str, Any]) -> bool:
    ext, mime, t = ext_of(b.get("name")), str(b.get("mime") or ""), spec.get("type")
    if t in ("image", "images"):
        return mime.startswith("image/")
    if t == "mesh":
        return ext in (spec.get("ext") or ["glb"]) or mime == "model/gltf-binary"
    if t == "video":
        return mime.startswith("video/") or ext in VIDEO_EXT
    if t == "file":
        return bool(spec.get("ext")) and ext in spec["ext"]
    return False


def _uniq(blobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for b in blobs:
        if b["hash"] not in seen:
            seen.add(b["hash"])
            out.append(b)
    return out


def follow_ups(recipes: list[dict[str, Any]], clicked: dict[str, Any],
               siblings: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    siblings = siblings or {}
    pool = _uniq([clicked, *(b for refs in siblings.values() for b in refs)])
    pages = next((siblings[k] for k in PAGE_KEYS if siblings.get(k)), [])
    out = []
    for r in recipes:
        blob_inputs = [(n, s) for n, s in (r.get("inputs") or {}).items() if s.get("type") in BLOB_TYPES]
        required = [(n, s) for n, s in blob_inputs if s.get("required") is not False]
        if not required:
            continue
        prefill: dict[str, list[str]] = {}
        used: set[str] = set()
        target = (next(((n, s) for n, s in required if fits(s, clicked)), None)
                  or next(((n, s) for n, s in blob_inputs if fits(s, clicked)), None))
        if target:
            prefill[target[0]] = [clicked["hash"]]
            used.add(clicked["hash"])
        for by_name in (True, False):
            for name, spec in blob_inputs:
                if name in prefill:
                    continue
                cands = siblings.get(name, []) if by_name else pool
                hit = next((b for b in cands if b["hash"] not in used and fits(spec, b)), None)
                if hit is None:
                    continue
                used.add(hit["hash"])
                prefill[name] = ([hit["hash"], *(p["hash"] for p in pages)] if spec.get("siblings") == "atlas_pages"
                                 else [hit["hash"]])
        missing = [s.get("label") or n for n, s in required if n not in prefill]
        via_siblings = len(required) - len(missing) >= 2
        if not (target and target[1].get("required") is not False) and not via_siblings:
            continue
        out.append({"recipe": r["id"], "name": r.get("name", r["id"]), "inputs": prefill, "missing": missing})
    out.sort(key=lambda f: (len(f["missing"]), f["name"]))
    return out


def kit_available(recipe_id: str, clicked: dict[str, Any], recipe_ids: set[str]) -> bool:
    return (str(clicked.get("mime") or "").startswith("image/")
            and recipe_id.startswith(("concept-", "variants-"))
            and KIT["first"] in recipe_ids and KIT["then"] in recipe_ids)
