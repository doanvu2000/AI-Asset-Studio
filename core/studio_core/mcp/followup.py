"""Python twin of app/src/renderer/src/composables/followUp.ts ("Dùng tiếp cho…"): which recipes an output can feed,
with their file inputs prefilled by blob hash. Keep the two in step."""
from __future__ import annotations

import re
from typing import Any

VIDEO_EXT = ("mp4", "webm", "mov", "mkv")
BLOB_TYPES = {"image", "images", "mesh", "file", "video"}
PAGE_KEYS = ("texture", "textures", "pages")
# FollowUpMenu.vue: a concept/variants image can start the character kit (8-dir, then Spine chained in core).
KIT = {"first": "sprite-8dir-2d", "then": "spine-character-image"}
ANIM = "sprite-anim-video"
ANIM_ACTIONS = ("idle", "walk", "run", "slash", "jump", "hit", "cast")


MAP_FILE_EXT = ("tmx", "tmj", "ldtk")
MAP_SOURCE_RECIPE = re.compile(r"^(tileset|autotile|parallax|playable|terrain|minimap|iso-tiles|tile-)")
MAP_NAME = re.compile(r"(^|[^a-z])(level|stage|world|room|dungeon|arena|overworld|tilemap|tiled|screenshot|map)"
                      r"([^a-z]|$)", re.I)
TEXTURE_MAP_NAME = re.compile(r"(normal|height|rough(ness)?|metal(lic|ness)?|spec(ular)?|bump|disp(lacement)?|light"
                              r"|shadow|splat|mask|uv|ao|occlusion)[_ -]?map", re.I)


def ext_of(name: str | None) -> str:
    return name.lower().rsplit(".", 1)[-1] if name and "." in name else ""


def looks_like_level(b: dict[str, Any], from_recipe: str | None = None) -> bool:
    if ext_of(b.get("name")) in MAP_FILE_EXT:
        return True
    if not str(b.get("mime") or "").startswith("image/"):
        return False
    if from_recipe and MAP_SOURCE_RECIPE.match(from_recipe):
        return True
    name = b.get("name") or ""
    return bool(MAP_NAME.search(name)) and not TEXTURE_MAP_NAME.search(name)


TILE_SOURCE_RECIPE = re.compile(r"^(texture-seamless|tileset|autotile|trim-sheet|iso-tiles|pbr-material|upscale)")
TILE_NAME = re.compile(r"(tile|texture|seamless|autotile|wang|ground|terrain|water|lava|grass)", re.I)


def looks_like_tile(b: dict[str, Any], from_recipe: str | None = None) -> bool:
    if not str(b.get("mime") or "").startswith("image/"):
        return False
    return bool(from_recipe and TILE_SOURCE_RECIPE.match(from_recipe)) or bool(TILE_NAME.search(b.get("name") or ""))


def looks_like_card_list(b: dict[str, Any], from_recipe: str | None = None) -> bool:
    ext = ext_of(b.get("name"))
    return ext == "csv" or (ext == "json" and "card" in str(b.get("name") or "").lower())


# Recipes whose input type matches almost any image but only make sense for some outputs. TS twin: FOLLOW_UP_GATES.
GATES = {"minimap": looks_like_level, "tile-animated": looks_like_tile, "iso-tiles-image": looks_like_tile,
         "card-set": looks_like_card_list}


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
               siblings: dict[str, list[dict[str, Any]]] | None = None,
               from_recipe: str | None = None) -> list[dict[str, Any]]:
    siblings = siblings or {}
    pool = _uniq([clicked, *(b for refs in siblings.values() for b in refs)])
    pages = next((siblings[k] for k in PAGE_KEYS if siblings.get(k)), [])
    out = []
    for r in recipes:
        gate = GATES.get(r["id"])
        if gate and not gate(clicked, from_recipe):
            continue
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
