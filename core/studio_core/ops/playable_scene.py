"""Playable test scene from a project's assets: `op.scene_gather` collects an 8-direction character, ground/wall
textures (or a blob-47 autotile), props and parallax layers (explicit inputs first, then the run's project);
`claude.scene_layout` (adapters/scene_layout.py) or `procedural_layout` plans a ~32×20 grid; `op.playable_scene`
builds the tile atlas, mockup, scene doc, Tiled .tmx and LDtk .ldtk; `op.scene_engine_check` opens the exported
scene in Godot / Unity, drives the player for a few frames and screenshots it (export/playable_scene.py)."""
from __future__ import annotations

import asyncio
import json
import random
import re
import sqlite3
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..contracts import BlobRef, Lane, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png

ASSETS_FILE = "scene_assets.json"
ASSETS_TYPE = "asset-studio/scene-assets"
LAYOUT_FILE = "scene_layout.json"
LAYOUT_TYPE = "asset-studio/scene-layout"
SCENE_FILE = "playable_scene.json"
SCENE_TYPE = "asset-studio/playable-scene"
STYLES = ("topdown", "sideview")
ENGINES = ("godot", "unity", "both", "none")
SOLID, FLOOR, PATH, AIR = "#", ".", "=", " "
ROLES = ("floor", "path", "wall")
VARIANTS = 4
ATLAS_COLS = 8
MAX_PROPS = 40
MAX_PROP_KINDS = 8
# Player tuning in tiles: topdown walk speed; sideview run speed, gravity and a jump apex of 3.5 tiles, so the
# procedural platforms (at most 3 tiles above the surface below) are always reachable.
TOPDOWN_SPEED = 4.5
SIDE_SPEED = 5.0
SIDE_GRAVITY = 30.0
SIDE_JUMP_TILES = 3.5
CHAR_HEIGHT_TILES = {"topdown": 1.7, "sideview": 1.8}
PROP_MAX_TILES = 2.0
# A sheet without a walk clip would slide: the idle frame hops BOB_TILES once per BOB_STEP_TILES travelled, leaning
# ±BOB_TILT_DEG on alternate steps, so the motion reads as steps.
BOB_TILES = 0.09
BOB_STEP_TILES = 0.8
BOB_TILT_DEG = 4.0
WALK_CLIPS = ("walk", "run")
ROLE_WORDS = {
    "wall": ("wall", "brick", "cliff", "rock", "fence", "hedge", "tường", "vách", "gạch"),
    "path": ("path", "road", "cobble", "paving", "pave", "street", "dirt", "trail", "plank", "floor tile", "đường",
             "lát", "sỏi", "cuội"),
}
FALLBACK_TILE_RGB = {"floor": (112, 160, 84), "path": (190, 166, 120), "wall": (98, 92, 88)}


def _slug(name: str, fallback: str) -> str:
    from .enemy_variants import slug

    s = slug(str(name or ""))[:40]
    return s or fallback


def _ident(name: str, fallback: str) -> str:
    """PascalCase node / asset name (engines dislike spaces and leading digits)."""
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", _slug(name, fallback)) if p]
    s = "".join(p[:1].upper() + p[1:] for p in parts) or fallback
    return f"N{s}" if s[0].isdigit() else s


def _int(params: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(params.get(key, default) if params.get(key) not in (None, "") else default)
    except (TypeError, ValueError) as e:
        raise StepError("op_bad_params", f"{key} must be an integer") from e
    if not lo <= v <= hi:
        raise StepError("op_bad_params", f"{key} must be in [{lo}, {hi}]")
    return v


def _choice(params: dict[str, Any], key: str, options: tuple[str, ...]) -> str:
    v = str(params.get(key) or options[0]).strip().lower()
    if v not in options:
        raise StepError("op_bad_params", f"{key} must be one of {', '.join(options)}")
    return v


async def _put_json(ctx: StepContext, doc: Any, name: str) -> BlobRef:
    return await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode("utf-8"), name=name,
                                     mime="application/json")


async def read_doc(ctx: StepContext, refs: list[BlobRef], doc_type: str, what: str) -> dict[str, Any]:
    for ref in refs:
        try:
            doc = json.loads(await asyncio.to_thread(ctx.blobs.path(ref.hash).read_bytes))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("type") == doc_type:
            return doc
    raise StepError("op_bad_input", f"{what} is missing or not a {doc_type} document")


def alpha_bbox(img: Image.Image) -> tuple[int, int, int, int] | None:
    a = np.asarray(img.convert("RGBA"))[..., 3] >= 32
    ys, xs = np.nonzero(a)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def alpha_share(img: Image.Image) -> float:
    a = np.asarray(img.convert("RGBA"))[..., 3]
    return float((a >= 32).mean())




def sheet_meta_ok(meta: Any) -> bool:
    return isinstance(meta, dict) and bool(meta.get("frame_w")) and bool(meta.get("frame_h"))


def directional(meta: dict[str, Any], directional_rows: bool = False) -> bool:
    from ..export.sheet import layout

    try:
        return layout(meta, directional_rows=directional_rows).facing
    except (KeyError, TypeError, ValueError):
        return False


def has_walk(meta: dict[str, Any], directional_rows: bool = False, clip_hint: str = "anim") -> bool:
    from ..export.sheet import layout

    try:
        lay = layout(meta, clip_hint, directional_rows=directional_rows)
    except (KeyError, TypeError, ValueError):
        return False
    return any(c in ("walk", "run") for c in lay.clips()) or (clip_hint in ("walk", "run") and lay.facing)


def anim_clips(meta: dict[str, Any], directional_rows: bool = False, clip_hint: str = "anim") -> tuple[str, str]:
    """(walk clip or "", standing clip) among the facing clips the engines export as <clip>_<dir> / states."""
    from ..export.sheet import layout

    try:
        lay = layout(meta, clip_hint, directional_rows=directional_rows)
    except (KeyError, TypeError, ValueError):
        return "", "idle"
    clips = list(dict.fromkeys(t.clip for t in lay.tracks if t.dir))
    walk = next((c for c in WALK_CLIPS if c in clips), "")
    idle = "idle" if "idle" in clips else next((c for c in clips if c != walk), walk or "idle")
    return walk, idle


def south_frame(sheet: Image.Image, meta: dict[str, Any], directional_rows: bool = False,
                clip_hint: str = "anim") -> Image.Image:
    from ..export.sheet import layout

    lay = layout(meta, clip_hint, directional_rows=directional_rows)
    cell = next((t.cells[0] for t in lay.tracks if t.dir == "S"), None) or (lay.cells[0] if lay.cells else None)
    if cell is None:
        return sheet
    return sheet.crop((cell.x, cell.y, cell.x + cell.w, cell.y + cell.h))


def scale_sheet(sheet: Image.Image, meta: dict[str, Any], s: float) -> tuple[Image.Image, dict[str, Any]]:
    """Every grid cell resized by `s` and repacked without padding; meta rects/pivot follow."""
    fw, fh = int(meta["frame_w"]), int(meta["frame_h"])
    pad = int(meta.get("padding") or 0)
    cols, rows = int(meta.get("cols") or 1), int(meta.get("rows") or 1)
    nw, nh = max(1, round(fw * s)), max(1, round(fh * s))
    out = Image.new("RGBA", (cols * nw, rows * nh), (0, 0, 0, 0))
    for r in range(rows):
        for c in range(cols):
            x, y = c * (fw + pad), r * (fh + pad)
            if x + fw > sheet.width or y + fh > sheet.height:
                continue
            cell = sheet.crop((x, y, x + fw, y + fh)).resize((nw, nh), Image.Resampling.LANCZOS)
            out.paste(cell, (c * nw, r * nh))
    m = dict(meta)
    m.update(frame_w=nw, frame_h=nh, padding=0, width=cols * nw, height=rows * nh)
    pv = meta.get("pivot")
    m["pivot"] = [round(float(pv[0]) * nw / fw, 2), round(float(pv[1]) * nh / fh, 2)] if isinstance(pv, list) \
        and len(pv) == 2 else [nw / 2, float(nh)]
    if isinstance(meta.get("frames_named"), list):
        named = []
        for n in meta["frames_named"]:
            n = dict(n)
            if all(k in n for k in ("x", "y")):
                n["x"], n["y"] = int(n["x"]) // (fw + pad) * nw, int(n["y"]) // (fh + pad) * nh
                n["w"], n["h"] = nw, nh
            named.append(n)
        m["frames_named"] = named
    m.pop("frames", None)
    return out, m




def role_for(name: str, taken: set[str]) -> str:
    low = name.lower()
    for role in ("wall", "path"):
        if role not in taken and any(w in low for w in ROLE_WORDS[role]):
            return role
    return next((r for r in ROLES if r not in taken), "floor")


def _project_rows(db: Path, run_id: str, project: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """(project id, finished runs newest first with their steps, image versions) read straight from SQLite: a step
    has no repo handle, and a read-only connection cannot disturb the writer."""
    from ..store.repo import _RUNS, _STEPS

    if not db.is_file():
        return "", [], []
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        pid = ""
        if project:
            row = con.execute("SELECT id FROM projects WHERE id = ? OR lower(name) = lower(?) ORDER BY id = ? DESC",
                              (project, project, project)).fetchone()
            pid = row["id"] if row else ""
        else:
            row = con.execute("SELECT project_id FROM runs WHERE id = ?", (run_id,)).fetchone()
            pid = row["project_id"] if row else ""
        if not pid:
            return "", [], []
        runs = []
        for r in con.execute("SELECT * FROM runs WHERE project_id = ? AND id != ? AND status = 'succeeded' "
                             "AND recipe_id != 'playable-scene' ORDER BY created_at DESC LIMIT 200", (pid, run_id)):
            run = _RUNS.decode(r)
            steps = [_STEPS.decode(s) for s in con.execute(
                "SELECT * FROM steps WHERE run_id = ? AND status IN ('succeeded', 'cached') ORDER BY \"order\", rowid",
                (run.id,))]
            runs.append({"run": run, "steps": steps})
        versions = [json.loads(v["blob"]) for v in con.execute(
            "SELECT blob FROM versions WHERE project_id = ? AND status != 'rejected' "
            "AND json_extract(blob, '$.mime') LIKE 'image/%' ORDER BY created_at DESC LIMIT 300", (pid,))]
        return pid, runs, versions
    finally:
        con.close()


def _run_label(run: Any) -> str:
    inp = run.inputs or {}
    for k in ("asset", "name", "prompt", "brief", "idea"):
        if isinstance(inp.get(k), str) and inp[k].strip():
            return inp[k].strip()
    return run.title or run.recipe_id


def collect_project(blobs: Any, runs: list[dict[str, Any]], versions: list[dict[str, Any]]) -> dict[str, Any]:
    """Candidates by kind from a project's finished runs, newest first, de-duplicated by blob hash."""
    from ..store.bundle import engine_source

    found: dict[str, list[dict[str, Any]]] = {"sheets": [], "textures": [], "autotile": [], "parallax": [],
                                              "images": []}
    seen: set[str] = set()
    for item in runs:
        run, steps = item["run"], item["steps"]
        label = _run_label(run)
        for step in reversed(steps):
            try:
                src = engine_source(step, steps, blobs, "x")
            except Exception:  # noqa: BLE001  (one odd step must not hide the rest of the project)
                continue
            pairs = [(src.sheet.name, src.meta, label)] if src.sheet is not None and src.meta else                 loose_sheets(blobs, step)
            for h, meta, name in pairs:
                if h in seen:
                    continue
                seen.add(h)
                found["sheets"].append({"hash": h, "meta": meta, "label": name, "recipe": run.recipe_id,
                                        "directional_rows": src.directional_rows, "clip_hint": src.clip_hint,
                                        "run": run.id})
            for _, path, role in src.textures:
                if role == "color" and path.name not in seen:
                    seen.add(path.name)
                    found["textures"].append({"hash": path.name, "label": label, "run": run.id})
            if src.autotile and src.autotile_atlas is not None and src.autotile_atlas.name not in seen:
                seen.add(src.autotile_atlas.name)
                found["autotile"].append({"doc": src.autotile, "label": label, "run": run.id})
            if src.parallax:
                key = "|".join(p.name for _, p, _ in src.parallax)
                if key not in seen:
                    seen.add(key)
                    found["parallax"].append({"layers": [{"name": n, "hash": p.name, "factor": f}
                                                         for n, p, f in src.parallax], "label": label})
    for v in versions:
        h = str(v.get("hash") or "")
        if h and h not in seen:
            seen.add(h)
            found["images"].append({"hash": h, "label": str(v.get("name") or "")})
    return found


def opaque_textures(blobs: Any, images: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    """Square, fully opaque images (imported seamless textures without a tile_check report)."""
    out = []
    for im in images:
        try:
            with Image.open(blobs.path(im["hash"])) as raw:
                if raw.width != raw.height or not 32 <= raw.width <= 4096:
                    continue
                img = raw.convert("RGBA")
        except OSError:
            continue
        if alpha_share(img) > 0.995:
            out.append({"hash": im["hash"], "label": Path(im["label"] or "texture").stem})
        if len(out) >= limit:
            break
    return out


def loose_sheets(blobs: Any, step: Any) -> list[tuple[str, dict[str, Any], str]]:
    """Sheet PNG + its .json side by side in one step's outputs (library-import, hand-made sheets)."""
    refs = [r for rs in step.outputs.values() for r in rs]
    metas = []
    for r in refs:
        if (r.name or "").lower().endswith(".json") and r.size < 2_000_000:
            try:
                m = json.loads(blobs.path(r.hash).read_bytes())
            except (OSError, ValueError):
                continue
            if sheet_meta_ok(m) and m.get("width") and m.get("height"):
                metas.append((Path(r.name or "").stem.lower(), m))
    out = []
    for r in refs:
        if not r.mime.startswith("image/") or not metas:
            continue
        stem = Path(r.name or "").stem.lower()
        try:
            with Image.open(blobs.path(r.hash)) as im:
                size = im.size
        except OSError:
            continue
        fit = [m for k, m in metas if (int(m["width"]), int(m["height"])) == size]
        named = [m for k, m in metas if k == stem and m in fit]
        if named or len(fit) == 1:
            out.append((r.hash, (named or fit)[0], re.sub(r"[_ -]?sheet$", "", stem) or stem))
    return out


def _tall(blobs: Any, s: dict[str, Any]) -> bool:
    try:
        with Image.open(blobs.path(s["hash"])) as im:
            frame = south_frame(im.convert("RGBA"), s["meta"], s.get("directional_rows", False),
                                s.get("clip_hint", "anim"))
    except (OSError, KeyError, ValueError):
        return False
    box = alpha_bbox(frame)
    return bool(box) and (box[3] - box[1]) > 1.15 * (box[2] - box[0])


def pick_character(sheets: list[dict[str, Any]], blobs: Any = None
                   ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The newest directional sheet that walks, else the newest standing figure (taller than wide — a chest or a
    barrel rendered in 8 directions is wide), else the newest directional one; the other directional sheets become
    props (their south frame)."""
    facing = [s for s in sheets if directional(s["meta"], s.get("directional_rows", False))]
    walkers = [s for s in facing if has_walk(s["meta"], s.get("directional_rows", False), s.get("clip_hint", ""))]
    tall = [s for s in facing if blobs is not None and _tall(blobs, s)] if not walkers else []
    pick = (walkers or tall or facing or [None])[0]
    return pick, [s for s in facing if s is not pick]


class SceneGather(OpExecutor):
    kind = "op.scene_gather"
    version = "1"

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    def cacheable(self, params: dict[str, Any]) -> bool:
        # The project's runs change between runs of this step; only an explicit "no project" result is stable.
        return str(params.get("project") or "").strip().lower() == "none"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        style = _choice(params, "style", STYLES)
        project = str(params.get("project") or "").strip()
        notes: list[str] = []
        character, extra_sheets = await self._input_character(ctx, inputs.get("character") or [])
        textures = [{"hash": r.hash, "label": Path(r.name or "tile").stem} for r in inputs.get("tiles") or []
                    if r.mime.startswith("image/")]
        autotile = await self._input_autotile(ctx, inputs.get("autotile") or [])
        props = [{"hash": r.hash, "label": Path(r.name or "prop").stem} for r in inputs.get("props") or []
                 if r.mime.startswith("image/")]
        background = [{"hash": r.hash, "name": Path(r.name or f"layer{i}").stem, "factor": None}
                      for i, r in enumerate(inputs.get("background") or []) if r.mime.startswith("image/")]
        pid = ""
        if project.lower() != "none" and self.settings is not None:
            db = Path(getattr(self.settings, "db_path", ""))
            pid, runs, versions = await asyncio.to_thread(_project_rows, db, ctx.run_id, project)
            if project and not pid:
                raise StepError("op_bad_params", f"unknown project '{project}'")
            if pid:
                found = await asyncio.to_thread(collect_project, ctx.blobs, runs, versions)
                pick, rest = pick_character(found["sheets"], ctx.blobs)
                if character is None and pick is not None:
                    character = {"hash": pick["hash"], "meta": pick["meta"], "name": pick["label"],
                                 "directional_rows": pick["directional_rows"], "clip_hint": pick["clip_hint"],
                                 "source": f"project run {pick['run'][:8]}"}
                    extra_sheets += rest
                if not textures and not autotile:
                    textures = [{"hash": t["hash"], "label": t["label"]} for t in found["textures"][:3]] or                         await asyncio.to_thread(opaque_textures, ctx.blobs, found["images"])
                if autotile is None and found["autotile"] and len(textures) < 2:
                    autotile = found["autotile"][0]["doc"]
                if not background and found["parallax"]:
                    background = [{"hash": la["hash"], "name": la["name"], "factor": la["factor"]}
                                  for la in found["parallax"][0]["layers"]]
                if not props:
                    used = {t["hash"] for t in textures} | {b["hash"] for b in background} |                         {s["hash"] for s in found["sheets"]}
                    if character:
                        used.add(character["hash"])
                    props = await asyncio.to_thread(self._image_props, ctx, found["images"], used)
        for s in extra_sheets:
            props.append({"sheet": s["hash"], "meta": s["meta"], "label": s["label"],
                          "directional_rows": s.get("directional_rows", False), "clip_hint": s.get("clip_hint", "")})
        if character is None:
            raise StepError("op_no_input", "no 8-direction character: give a sprite sheet (+ its .json) or start the "
                                           "run in a project that has a sprite-8dir run")
        roles: set[str] = set()
        for t in textures[:3]:
            t["role"] = role_for(t["label"], roles)
            roles.add(t["role"])
        textures = [t for t in textures[:3] if "role" in t]
        if not textures and not autotile:
            notes.append("no tile texture: flat placeholder colours")
        props = props[:MAX_PROP_KINDS]
        doc = {"type": ASSETS_TYPE, "style": style, "project": pid, "character": character, "textures": textures,
               "autotile": autotile, "props": props, "background": background, "notes": notes}
        sheet = await asyncio.to_thread(contact_sheet, ctx.blobs, doc)
        ctx.progress(1.0)
        return StepResult(outputs={"assets": [await _put_json(ctx, doc, ASSETS_FILE)],
                                   "preview": [await _put_png(ctx, sheet, "scene_assets.png")]},
                          meta={"project": pid, "character": character.get("name"), "textures": len(textures),
                                "props": len(props), "background": len(background), "autotile": bool(autotile)})

    async def _input_character(self, ctx: StepContext, refs: list[BlobRef]
                               ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        if not refs:
            return None, []
        metas, pngs = [], []
        for r in refs:
            if (r.name or "").lower().endswith(".json") or r.mime == "application/json":
                try:
                    m = json.loads(await asyncio.to_thread(ctx.blobs.path(r.hash).read_bytes))
                except (OSError, ValueError) as e:
                    raise StepError("op_bad_input", f"{r.name}: not JSON ({e})") from e
                if not sheet_meta_ok(m):
                    raise StepError("op_bad_input", f"{r.name}: not a sprite sheet .json (needs frame_w/frame_h)")
                metas.append((r, m))
            elif r.mime.startswith("image/"):
                pngs.append(r)
        sheets = []
        for png in pngs:
            stem = Path(png.name or "").stem.lower()
            match = next((m for r, m in metas if Path(r.name or "").stem.lower() == stem), None)
            if match is None and len(metas) == 1 and len(pngs) == 1:
                match = metas[0][1]
            if match is None:
                img = await _load(ctx, png)
                match = guess_meta(img.size)
            if match is None:
                raise StepError("op_bad_input", f"{png.name}: no matching .json and not a 1×8 / 8×1 sheet")
            sheets.append({"hash": png.hash, "meta": match, "label": Path(png.name or "hero").stem,
                           "directional_rows": False, "clip_hint": "anim", "run": "input"})
        pick, rest = await asyncio.to_thread(pick_character, sheets, ctx.blobs)
        if pick is None:
            raise StepError("op_bad_input", "character sheet has no directions (needs an 8-direction sprite sheet)")
        return ({"hash": pick["hash"], "meta": pick["meta"], "name": pick["label"], "directional_rows": False,
                 "clip_hint": "anim", "source": "input"}, rest)

    async def _input_autotile(self, ctx: StepContext, refs: list[BlobRef]) -> dict[str, Any] | None:
        from .autotile import AUTOTILE_TYPE

        for r in refs:
            try:
                doc = json.loads(await asyncio.to_thread(ctx.blobs.path(r.hash).read_bytes))
            except (OSError, ValueError):
                continue
            if isinstance(doc, dict) and doc.get("type") == AUTOTILE_TYPE:
                if not ctx.blobs.path(str(doc.get("atlas", {}).get("hash", ""))).is_file():
                    raise StepError("op_bad_input", "autotile.json: its atlas image is not in this studio's store")
                return doc
        if refs:
            raise StepError("op_bad_input", "autotile input is not an autotile.json (autotile-terrain)")
        return None

    @staticmethod
    def _image_props(ctx: StepContext, images: list[dict[str, Any]], used: set[str]) -> list[dict[str, Any]]:
        out = []
        for im in images:
            if im["hash"] in used or len(out) >= MAX_PROP_KINDS:
                continue
            p = ctx.blobs.path(im["hash"])
            try:
                with Image.open(p) as raw:
                    if max(raw.size) > 4096 or raw.mode not in ("RGBA", "LA", "P"):
                        continue
                    img = raw.convert("RGBA")
            except OSError:
                continue
            # A prop is a cut-out: some transparency around an object that is neither a speck nor the whole canvas.
            if 0.05 <= alpha_share(img) <= 0.85 and alpha_bbox(img) is not None:
                out.append({"hash": im["hash"], "label": Path(im["label"] or "prop").stem})
        return out


def guess_meta(size: tuple[int, int]) -> dict[str, Any] | None:
    w, h = size
    angles = [0, 45, 90, 135, 180, 225, 270, 315]
    if w == 8 * h:
        return {"frame_w": h, "frame_h": h, "cols": 8, "rows": 1, "count": 8, "order": angles}
    if h == 8 * w:
        return {"frame_w": w, "frame_h": w, "cols": 1, "rows": 8, "count": 8, "order": angles}
    return None


def _font(px: int) -> Any:
    try:
        return ImageFont.truetype("arial.ttf", px)
    except OSError:
        return ImageFont.load_default()


def contact_sheet(blobs: Any, doc: dict[str, Any], cell: int = 160) -> Image.Image:
    """What Claude sees: the character, each texture with its role, each prop numbered as the layout refers to it."""
    items: list[tuple[str, Image.Image]] = []
    ch = doc["character"]
    with Image.open(blobs.path(ch["hash"])) as im:
        items.append(("player", south_frame(im.convert("RGBA"), ch["meta"], ch.get("directional_rows", False),
                                            ch.get("clip_hint", "anim"))))
    for t in doc["textures"]:
        with Image.open(blobs.path(t["hash"])) as im:
            items.append((t["role"], im.convert("RGBA")))
    for i, p in enumerate(doc["props"]):
        items.append((f"prop {i}: {p['label'][:14]}", prop_image(blobs, p)))
    cols = min(4, len(items))
    rows = -(-len(items) // cols)
    out = Image.new("RGB", (cols * cell, rows * (cell + 22)), (38, 40, 46))
    d = ImageDraw.Draw(out)
    for i, (label, img) in enumerate(items):
        x, y = (i % cols) * cell, (i // cols) * (cell + 22)
        thumb = img.copy()
        thumb.thumbnail((cell - 12, cell - 12))
        out.paste(thumb, (x + (cell - thumb.width) // 2, y + 22 + (cell - thumb.height) // 2), thumb)
        d.text((x + 6, y + 4), label, fill=(235, 235, 240), font=_font(14))
    return out


def prop_image(blobs: Any, p: dict[str, Any]) -> Image.Image:
    with Image.open(blobs.path(p.get("sheet") or p["hash"])) as im:
        img = im.convert("RGBA")
    if p.get("sheet"):
        img = south_frame(img, p["meta"], p.get("directional_rows", False), p.get("clip_hint", "anim"))
    box = alpha_bbox(img)
    return img.crop(box) if box else img




def _neighbors(x: int, y: int, w: int, h: int) -> list[tuple[int, int]]:
    return [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)) if 0 <= x + dx < w and 0 <= y + dy < h]


def _walkable(c: str, style: str) -> bool:
    return c in (FLOOR, PATH) if style == "topdown" else c == AIR


def _reach(grid: list[list[str]], start: tuple[int, int], style: str) -> set[tuple[int, int]]:
    h, w = len(grid), len(grid[0])
    seen = {start}
    q = deque([start])
    while q:
        x, y = q.popleft()
        for nx, ny in _neighbors(x, y, w, h):
            if (nx, ny) not in seen and _walkable(grid[ny][nx], style):
                seen.add((nx, ny))
                q.append((nx, ny))
    return seen


def _nearest(cells: list[tuple[int, int]], to: tuple[int, int]) -> tuple[int, int] | None:
    return min(cells, key=lambda c: (abs(c[0] - to[0]) + abs(c[1] - to[1]), c[1], c[0])) if cells else None


def _stands(grid: list[list[str]], x: int, y: int) -> bool:
    return grid[y][x] == AIR and y + 1 < len(grid) and grid[y + 1][x] == SOLID


def normalize_layout(raw: Any, style: str, cols: int, rows: int, n_props: int, planner: str = "claude"
                     ) -> dict[str, Any]:
    """Any rows / props / spawn → a playable grid: walls round the edge (topdown) or a floor and side walls
    (sideview), spawn on a walkable cell, every walkable area joined to the spawn, props only where they stand."""
    raw = raw if isinstance(raw, dict) else {}
    lines = raw.get("rows") if isinstance(raw.get("rows"), list) else []
    default = FLOOR if style == "topdown" else AIR
    known = {SOLID, FLOOR, PATH, AIR} if style == "topdown" else {SOLID, AIR}
    grid = []
    for y in range(rows):
        line = str(lines[y]) if y < len(lines) else ""
        row = []
        for x in range(cols):
            c = line[x] if x < len(line) else default
            c = {"_": AIR, "~": SOLID, "W": SOLID, "X": SOLID, "+": PATH}.get(c, c)
            if style == "sideview" and c in (FLOOR, PATH):
                c = AIR
            row.append(c if c in known else default)
        grid.append(row)
    if style == "topdown":
        for y in range(rows):
            for x in range(cols):
                if x in (0, cols - 1) or y in (0, rows - 1):
                    grid[y][x] = SOLID
                elif grid[y][x] == AIR:
                    grid[y][x] = FLOOR
    else:
        for y in range(rows):
            grid[y][0] = grid[y][cols - 1] = SOLID
        grid[rows - 1] = [SOLID] * cols
        grid[0] = [SOLID] + [AIR] * (cols - 2) + [SOLID]
    sp = raw.get("spawn")
    want = (int(sp[0]), int(sp[1])) if isinstance(sp, (list, tuple)) and len(sp) == 2 and all(
        isinstance(v, (int, float)) for v in sp) else (2, rows // 2)
    ok = [(x, y) for y in range(rows) for x in range(cols)
          if (_walkable(grid[y][x], style) if style == "topdown" else _stands(grid, x, y))]
    if not ok:
        mid = (cols // 2, rows // 2 if style == "topdown" else rows - 2)
        grid[mid[1]][mid[0]] = FLOOR if style == "topdown" else AIR
        ok = [mid]
    spawn = _nearest(ok, want) or ok[0]
    _connect(grid, spawn, style)
    reach = _reach(grid, spawn, style)
    props = []
    taken = {spawn}
    for p in raw.get("props") if isinstance(raw.get("props"), list) else []:
        if not isinstance(p, dict) or len(props) >= MAX_PROPS:
            continue
        try:
            k, px, py = int(p.get("prop", 0)), int(p.get("x", -1)), int(p.get("y", -1))
        except (TypeError, ValueError):
            continue
        if not 0 <= k < n_props:
            continue
        cand = [(x, y) for (x, y) in reach if (x, y) not in taken and abs(x - spawn[0]) + abs(y - spawn[1]) > 1
                and (grid[y][x] == FLOOR if style == "topdown" else _stands(grid, x, y))
                and abs(x - px) + abs(y - py) <= 3]
        cell = (px, py) if (px, py) in cand else _nearest(cand, (px, py))
        if cell is None:
            continue
        taken.add(cell)
        props.append({"prop": k, "x": cell[0], "y": cell[1]})
    return {"type": LAYOUT_TYPE, "style": style, "cols": cols, "rows": rows,
            "grid": ["".join(r) for r in grid], "spawn": list(spawn), "props": props, "planner": planner,
            "reason": str(raw.get("reason") or "")[:600]}


def _connect(grid: list[list[str]], spawn: tuple[int, int], style: str) -> None:
    """Joins every walkable island to the spawn's: topdown carves an L-shaped path; sideview air pockets the player
    cannot reach are sealed (filled solid)."""
    h, w = len(grid), len(grid[0])
    for _ in range(64):
        reach = _reach(grid, spawn, style)
        rest = [(x, y) for y in range(h) for x in range(w) if _walkable(grid[y][x], style) and (x, y) not in reach]
        if not rest:
            return
        if style == "sideview":
            for x, y in rest:
                grid[y][x] = SOLID
            return
        a = rest[0]
        b = _nearest(list(reach), a)
        if b is None:
            return
        x, y = a
        while x != b[0]:
            x += 1 if b[0] > x else -1
            if grid[y][x] == SOLID and 0 < x < w - 1:
                grid[y][x] = PATH
        while y != b[1]:
            y += 1 if b[1] > y else -1
            if grid[y][x] == SOLID and 0 < y < h - 1:
                grid[y][x] = PATH


def procedural_layout(style: str, cols: int, rows: int, n_props: int, seed: int = 0) -> dict[str, Any]:
    rng = random.Random(seed)
    if style == "topdown":
        g = [[FLOOR] * cols for _ in range(rows)]
        py = rows // 2 + rng.randint(-2, 2)
        for x in range(1, cols - 1):
            if rng.random() < 0.25:
                py = max(2, min(rows - 3, py + rng.choice((-1, 1))))
            g[py][x] = PATH
            g[py + 1][x] = PATH
        px = cols // 2 + rng.randint(-3, 3)
        for y in range(1, rows - 1):
            g[y][px] = PATH
        want, placed = rng.randint(3, 5), 0
        for _ in range(60):
            if placed >= want:
                break
            bw, bh = rng.randint(2, 5), rng.randint(2, 3)
            bx, by = rng.randint(2, cols - bw - 2), rng.randint(2, rows - bh - 2)
            if any(g[y][x] != FLOOR for y in range(by - 1, by + bh + 1) for x in range(bx - 1, bx + bw + 1)):
                continue
            for y in range(by, by + bh):
                for x in range(bx, bx + bw):
                    g[y][x] = SOLID
            placed += 1
        spawn = [2, py]
        cells = [(x, y) for y in range(2, rows - 2) for x in range(2, cols - 2) if g[y][x] == FLOOR]
    else:
        g = [[AIR] * cols for _ in range(rows)]
        ground = rows - 3
        heights = []
        for x in range(cols):
            if x and rng.random() < 0.18:
                ground = max(rows - 5, min(rows - 2, ground + rng.choice((-1, 1))))
            heights.append(ground)
            for y in range(ground, rows):
                g[y][x] = SOLID
        x = 4
        while x < cols - 6:
            length = rng.randint(3, 5)
            top = min(heights[x:x + length])
            y = max(3, top - rng.randint(2, 3))
            for k in range(length):
                g[y][x + k] = SOLID
            # A second, higher ledge reachable from this one now and then, so the level is not one flat band.
            if y - 3 >= 3 and rng.random() < 0.5:
                hx = x + rng.randint(1, max(1, length - 2))
                for k in range(min(3, cols - 2 - hx)):
                    g[y - 3][hx + k] = SOLID
            x += length + rng.randint(2, 4)
        spawn = [2, heights[2] - 1]
        cells = [(x, y) for y in range(1, rows - 1) for x in range(3, cols - 2) if _stands(g, x, y)]
    rng.shuffle(cells)
    props = []
    for x, y in cells:
        if len(props) >= min(MAX_PROPS, max(0, n_props) * 3):
            break
        if all(abs(x - p["x"]) + abs(y - p["y"]) > 3 for p in props) and abs(x - spawn[0]) + abs(y - spawn[1]) > 3:
            props.append({"prop": len(props) % max(1, n_props), "x": x, "y": y})
    raw = {"rows": ["".join(r) for r in g], "spawn": spawn, "props": props if n_props else [],
           "reason": f"procedural {style} (seed {seed})"}
    return normalize_layout(raw, style, cols, rows, n_props, planner="procedural")




def _tex_block(img: Image.Image, tile: int, pixel: bool) -> Image.Image:
    side = tile * VARIANTS
    resample = Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS
    sq = min(img.size)
    return img.convert("RGBA").crop((0, 0, sq, sq)).resize((side, side), resample)


def _tint(img: Image.Image, mul: tuple[float, float, float], add: float = 0.0) -> Image.Image:
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    a[..., :3] = np.clip(a[..., :3] * np.asarray(mul, np.float32) + add, 0, 255)
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def _flat(rgb: tuple[int, int, int], tile: int, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    side = tile * VARIANTS
    a = np.empty((side, side, 4), np.float32)
    a[..., :3] = rgb
    a[..., :3] += rng.normal(0, 7, (side, side, 1))
    a[..., 3] = 255
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")


class Atlas:
    """Tiles in reading order, ATLAS_COLS per row; every terrain is a VARIANTS² block so neighbouring cells continue
    the seamless texture (cell x, y uses block cell x % 4, y % 4)."""

    def __init__(self, tile: int) -> None:
        self.tile = tile
        self.tiles: list[Image.Image] = []
        self.sets: dict[str, list[int]] = {}
        self.solid: set[int] = set()

    def add_block(self, name: str, block: Image.Image, solid: bool) -> None:
        ids = []
        t = self.tile
        for j in range(VARIANTS):
            for i in range(VARIANTS):
                ids.append(len(self.tiles))
                self.tiles.append(block.crop((i * t, j * t, (i + 1) * t, (j + 1) * t)))
        self.sets[name] = ids
        if solid:
            self.solid.update(ids)

    def add_tiles(self, name: str, tiles: list[Image.Image], solid: bool) -> None:
        ids = list(range(len(self.tiles), len(self.tiles) + len(tiles)))
        self.tiles += tiles
        self.sets[name] = ids
        if solid:
            self.solid.update(ids)

    def at(self, name: str, x: int, y: int) -> int:
        ids = self.sets[name]
        return ids[(y % VARIANTS) * VARIANTS + x % VARIANTS]

    def image(self) -> Image.Image:
        rows = -(-len(self.tiles) // ATLAS_COLS)
        out = Image.new("RGBA", (ATLAS_COLS * self.tile, rows * self.tile), (0, 0, 0, 0))
        for i, t in enumerate(self.tiles):
            out.paste(t, ((i % ATLAS_COLS) * self.tile, (i // ATLAS_COLS) * self.tile))
        return out


def build_atlas(blobs: Any, assets: dict[str, Any], tile: int, pixel: bool, style: str) -> Atlas:
    tex = {}
    for t in assets.get("textures") or []:
        with Image.open(blobs.path(t["hash"])) as im:
            tex[t["role"]] = _tex_block(im, tile, pixel)
    floor = tex.get("floor") or (_tint(tex["path"], (0.8, 0.95, 0.75)) if "path" in tex else None) \
        or _flat(FALLBACK_TILE_RGB["floor"], tile, 1)
    path = tex.get("path") or (_tint(floor, (1.12, 1.0, 0.8), 18) if tex else _flat(FALLBACK_TILE_RGB["path"], tile, 2))
    wall = tex.get("wall") or (_tint(floor, (0.55, 0.52, 0.5)) if tex else _flat(FALLBACK_TILE_RGB["wall"], tile, 3))
    at = Atlas(tile)
    if style == "topdown":
        at.add_block("floor", floor, False)
        at.add_block("path", path, False)
        at.add_block("wall", _tint(wall, (1.12, 1.12, 1.12), 6), True)
        at.add_block("wall_face", wall_face(wall, tile), True)
        at.add_tiles("shadow", [shadow_tile(tile)], False)
        auto = assets.get("autotile")
        if auto:
            at.add_tiles("auto", autotile_tiles(blobs, auto, tile, pixel), False)
    else:
        at.add_block("wall", wall, True)
        # Surface tiles: the wall texture with the floor texture's top band laid over it (grass on dirt).
        top = wall.copy()
        band = int(tile * 0.38)
        for j in range(VARIANTS):
            strip = floor.crop((0, j * tile, tile * VARIANTS, j * tile + band))
            top.paste(strip, (0, j * tile))
        at.add_block("top", top, True)
    return at


def wall_face(wall: Image.Image, tile: int) -> Image.Image:
    """Front of a topdown wall (the side facing the camera): darker going down, a lit lip at the top edge and a dark
    contact line at the foot, repeated per tile row of the block."""
    v = np.linspace(0.0, 1.0, tile, dtype=np.float32)
    k = 0.8 - 0.28 * v
    lip = max(1, round(tile / 16))
    k[:lip] = 1.3
    k[lip:2 * lip] = 1.05
    k[-lip:] = 0.38
    a = np.asarray(wall.convert("RGBA")).astype(np.float32)
    a[..., :3] *= np.tile(k, a.shape[0] // tile)[:, None, None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")


def shadow_tile(tile: int) -> Image.Image:
    """Translucent black fading downwards: laid over the floor cell right below a wall, as if lit from above."""
    v = np.clip(1.0 - np.arange(tile, dtype=np.float32) / (0.7 * tile), 0.0, 1.0) ** 1.6
    a = np.zeros((tile, tile, 4), np.uint8)
    a[..., 3] = (v * 120).astype(np.uint8)[:, None]
    return Image.fromarray(a, "RGBA")


def autotile_tiles(blobs: Any, doc: dict[str, Any], tile: int, pixel: bool) -> list[Image.Image]:
    with Image.open(blobs.path(str(doc["atlas"]["hash"]))) as im:
        atlas = im.convert("RGBA")
    src = int(doc["tile_size"])
    resample = Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS
    return [atlas.crop((e["col"] * src, e["row"] * src, (e["col"] + 1) * src, (e["row"] + 1) * src))
            .resize((tile, tile), resample) for e in doc["tiles"]]


def tile_layers(layout: dict[str, Any], at: Atlas, autotile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Ground (walk-on, never collides), topdown Shadow (below each wall) and Solid (collides) in draw order as flat
    id lists, row-major, -1 = empty. A topdown wall shows its face where the cell below is open, its top elsewhere
    (the map edge counts as wall)."""
    from .autotile import mask_at

    cols, rows, style = layout["cols"], layout["rows"], layout["style"]
    grid = layout["grid"]
    ground, solid, shadow = [-1] * (cols * rows), [-1] * (cols * rows), [-1] * (cols * rows)
    by_mask = {}
    fill = None
    if autotile and "auto" in at.sets:
        ids = at.sets["auto"]
        for i, e in enumerate(autotile["tiles"]):
            if e.get("mask") is None:
                fill = ids[i]
            else:
                by_mask[int(e["mask"])] = ids[i]
        cells = np.array([[c == PATH for c in row] for row in grid], bool)
    for y in range(rows):
        for x in range(cols):
            c, i = grid[y][x], y * cols + x
            if style == "topdown":
                if by_mask and fill is not None:
                    ground[i] = by_mask.get(mask_at(cells, y, x), fill) if c == PATH else fill
                else:
                    ground[i] = at.at("path" if c == PATH else "floor", x, y)
                if c == SOLID:
                    face = y + 1 < rows and grid[y + 1][x] != SOLID and "wall_face" in at.sets
                    solid[i] = at.at("wall_face" if face else "wall", x, y)
                elif y > 0 and grid[y - 1][x] == SOLID and "shadow" in at.sets:
                    shadow[i] = at.sets["shadow"][0]
            elif c == SOLID:
                solid[i] = at.at("top" if y == 0 or grid[y - 1][x] != SOLID else "wall", x, y)
    layers = [{"name": "Ground", "collide": False, "tiles": ground}]
    if style == "topdown":
        layers.append({"name": "Shadow", "collide": False, "tiles": shadow})
    return [*layers, {"name": "Solid", "collide": True, "tiles": solid}]


def layer_tiles(scene: dict[str, Any], name: str) -> list[int]:
    return next((la["tiles"] for la in scene["layers"] if la["name"] == name), [])


def player_setup(sheet: Image.Image, meta: dict[str, Any], tile: int, style: str, directional_rows: bool,
                 clip_hint: str) -> tuple[float, dict[str, Any]]:
    """Scale that makes the character CHAR_HEIGHT_TILES tall, plus its collider in scaled px around the feet."""
    frame = south_frame(sheet, meta, directional_rows, clip_hint)
    box = alpha_bbox(frame) or (0, 0, frame.width, frame.height)
    vis_w, vis_h = box[2] - box[0], box[3] - box[1]
    s = CHAR_HEIGHT_TILES[style] * tile / max(1, vis_h)
    if style == "topdown":
        col = {"w": round(tile * 0.6, 2), "h": round(tile * 0.4, 2), "y": round(-tile * 0.2, 2)}
    else:
        w = round(max(tile * 0.4, min(tile * 0.9, vis_w * s * 0.55)), 2)
        h = round(vis_h * s * 0.92, 2)
        col = {"w": w, "h": h, "y": round(-h / 2, 2)}
    return s, col


def prop_setup(img: Image.Image, tile: int, style: str, pixel: bool) -> tuple[Image.Image, list[list[float]]]:
    """The prop resized to at most PROP_MAX_TILES square, and its collider polygon in px around the bottom-centre
    anchor (y up negative): the traced alpha hull, cut to the footprint for topdown so the player walks behind."""
    from ..export.collider import alpha_mask, trace

    s = min(PROP_MAX_TILES * tile / img.width, PROP_MAX_TILES * tile / img.height, 4.0)
    size = (max(1, round(img.width * s)), max(1, round(img.height * s)))
    out = img.resize(size, Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS)
    polys = trace(alpha_mask(out), tolerance=1.5, merge="hull")
    poly = polys[0] if polys else [(0, 0), (size[0], 0), (size[0], size[1]), (0, size[1])]
    if style == "topdown":
        poly = clip_below(poly, size[1] * 0.6) or [(0, size[1] * 0.6), (size[0], size[1] * 0.6),
                                                   (size[0], size[1]), (0, size[1])]
    return out, [[round(x - size[0] / 2, 2), round(y - size[1], 2)] for x, y in poly]


def clip_below(poly: list[tuple[float, float]], y0: float) -> list[tuple[float, float]]:
    """Sutherland–Hodgman against the half-plane y >= y0 (image y down)."""
    out: list[tuple[float, float]] = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        cin, pin = cur[1] >= y0, prev[1] >= y0
        if cin != pin:
            t = (y0 - prev[1]) / (cur[1] - prev[1])
            out.append((prev[0] + t * (cur[0] - prev[0]), y0))
        if cin:
            out.append(cur)
    return out if len(out) >= 3 else []


def render_mockup(scene: dict[str, Any], atlas_img: Image.Image, sheet: Image.Image, props: list[Image.Image],
                  backgrounds: list[Image.Image]) -> Image.Image:
    t, cols, rows = scene["tile"], scene["cols"], scene["rows"]
    W, H = cols * t, rows * t
    out = Image.new("RGBA", (W, H), (58, 66, 84, 255))
    for bg in backgrounds:
        k = H / bg.height
        layer = bg.resize((max(1, round(bg.width * k)), H), Image.Resampling.LANCZOS)
        for x in range(0, W, layer.width):
            out.alpha_composite(layer, (x, 0))
    tiles = {}
    for layer in scene["layers"]:
        for i, tid in enumerate(layer["tiles"]):
            if tid < 0:
                continue
            if tid not in tiles:
                c, r = tid % ATLAS_COLS, tid // ATLAS_COLS
                tiles[tid] = atlas_img.crop((c * t, r * t, (c + 1) * t, (r + 1) * t))
            out.alpha_composite(tiles[tid], ((i % cols) * t, (i // cols) * t))
    sprites: list[tuple[float, Image.Image, tuple[int, int]]] = []
    for p in scene["props"]:
        img = props[p["kind"]]
        fx, fy = p["pos"]
        sprites.append((fy, img, (round(fx - img.width / 2), round(fy - img.height))))
    pl = scene["player"]
    frame = south_frame(sheet, pl["meta"], pl.get("directional_rows", False), pl.get("clip_hint", "anim"))
    pv = pl["meta"].get("pivot") or [frame.width / 2, frame.height]
    fx, fy = pl["pos"]
    sprites.append((fy + 0.01, frame, (round(fx - float(pv[0])), round(fy - float(pv[1])))))
    for _, img, xy in sorted(sprites, key=lambda s: s[0]):
        _paste(out, img, xy)
    return out


def _paste(dst: Image.Image, img: Image.Image, xy: tuple[int, int]) -> None:
    """alpha_composite refuses negative offsets; crop what hangs over the top / left edge instead."""
    x, y = xy
    cx, cy = max(0, -x), max(0, -y)
    if cx >= img.width or cy >= img.height:
        return
    dst.alpha_composite(img.crop((cx, cy, img.width, img.height)), (x + cx, y + cy))


class PlayableScene(OpExecutor):
    kind = "op.playable_scene"
    version = "2"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        assets = await read_doc(ctx, inputs.get("assets") or [], ASSETS_TYPE, "assets")
        layout = await read_doc(ctx, inputs.get("layout") or [], LAYOUT_TYPE, "layout")
        asset = _ident(str(params.get("asset") or "PlayableScene"), "PlayableScene")
        result = await asyncio.to_thread(build_scene, ctx.blobs, assets, layout, params, asset)
        scene, atlas_img, mockup, sheet_img, prop_imgs, tmx, ldtk = result
        refs: dict[str, BlobRef] = {}
        refs["atlas"] = await _put_png(ctx, atlas_img, f"{asset}_tiles.png")
        refs["sheet"] = await _put_png(ctx, sheet_img, f"{scene['player']['name']}.png")
        scene["tileset"]["hash"] = refs["atlas"].hash
        scene["player"]["sheet"] = refs["sheet"].hash
        for kind, img in zip(scene["prop_kinds"], prop_imgs):
            kind["hash"] = (await _put_png(ctx, img, f"{kind['name']}.png")).hash
        refs["mockup"] = await _put_png(ctx, mockup, f"{asset}_mockup.png")
        prev = mockup.copy()
        prev.thumbnail((1600, 1600))
        scene_ref = await _put_json(ctx, scene, SCENE_FILE)
        tmx_ref = await ctx.blobs.put_bytes(tmx.encode("utf-8"), name=f"{asset}.tmx", mime="application/xml")
        ldtk_ref = await ctx.blobs.put_bytes(ldtk.encode("utf-8"), name=f"{asset}.ldtk", mime="application/json")
        ctx.progress(1.0)
        return StepResult(outputs={"images": [refs["mockup"]], "preview": [await _put_png(ctx, prev, "preview.png")],
                                   "scene": [scene_ref], "tmx": [tmx_ref], "ldtk": [ldtk_ref],
                                   "tiles": [refs["atlas"]]},
                          meta={"style": scene["style"], "tile": scene["tile"], "grid": [scene["cols"], scene["rows"]],
                                "props": len(scene["props"]), "tiles": scene["tileset"]["count"],
                                "planner": layout.get("planner"), "mockup": list(mockup.size),
                                "walk_clip": scene["player"]["walk_clip"] or None, "notes": scene["notes"]})


def build_scene(blobs: Any, assets: dict[str, Any], layout: dict[str, Any], params: dict[str, Any], asset: str
                ) -> tuple[dict[str, Any], Image.Image, Image.Image, Image.Image, list[Image.Image], str, str]:
    style = layout["style"]
    ch = assets["character"]
    with Image.open(blobs.path(ch["hash"])) as im:
        raw_sheet = im.convert("RGBA")
    meta = ch["meta"]
    pixel = bool(meta.get("pixel") or meta.get("filter") == "point" or max(int(meta["frame_w"]),
                                                                              int(meta["frame_h"])) <= 64)
    tile = int(params.get("tile") or 0) or (16 if pixel else 32)
    if tile not in (16, 24, 32, 48, 64):
        raise StepError("op_bad_params", "tile must be 0 (auto), 16, 24, 32, 48 or 64")
    s, collider = player_setup(raw_sheet, meta, tile, style, ch.get("directional_rows", False),
                               ch.get("clip_hint", "anim"))
    sheet, smeta = scale_sheet(raw_sheet, meta, s) if abs(s - 1) > 0.01 else (raw_sheet, dict(meta))
    at = build_atlas(blobs, assets, tile, pixel, style)
    layers = tile_layers(layout, at, assets.get("autotile"))
    cols, rows = layout["cols"], layout["rows"]
    kinds, prop_imgs = [], []
    names: set[str] = set()
    for i, p in enumerate(assets.get("props") or []):
        img, poly = prop_setup(prop_image(blobs, p), tile, style, pixel)
        name = _ident(p.get("label") or f"prop{i}", f"Prop{i}")
        while name in names:
            name += "_"
        names.add(name)
        kinds.append({"name": name, "w": img.width, "h": img.height, "collider": poly})
        prop_imgs.append(img)
    sx, sy = layout["spawn"]
    foot = (sy + 1) * tile - 1 if style == "sideview" else (sy + 0.6) * tile
    walk_clip, idle_clip = anim_clips(smeta, ch.get("directional_rows", False), ch.get("clip_hint", "anim"))
    bob_px = 0.0 if walk_clip else (max(1.0, round(BOB_TILES * tile)) if pixel else round(BOB_TILES * tile, 2))
    player = {"name": _ident(ch.get("name") or "Hero", "Hero"), "meta": smeta, "pos": [(sx + 0.5) * tile, foot],
              "collider": collider, "directional_rows": ch.get("directional_rows", False),
              "clip_hint": ch.get("clip_hint", "anim"), "walk": bool(walk_clip), "walk_clip": walk_clip,
              "idle_clip": idle_clip, "pixel": pixel,
              "bob": {"px": bob_px, "step_px": round(BOB_STEP_TILES * tile, 2),
                      "tilt": BOB_TILT_DEG if bob_px else 0.0}}
    notes = [] if walk_clip else [f"no walk clip: '{idle_clip}' frame bobs {bob_px:g}px per step instead of sliding"]
    props = [{"kind": p["prop"], "cell": [p["x"], p["y"]],
              "pos": [(p["x"] + 0.5) * tile, (p["y"] + (1 if style == "sideview" else 0.85)) * tile]}
             for p in layout["props"] if p["prop"] < len(kinds)]
    bg_imgs, background = [], []
    nb = len(assets.get("background") or [])
    for i, b in enumerate(assets.get("background") or []):
        with Image.open(blobs.path(b["hash"])) as im:
            bg_imgs.append(im.convert("RGBA"))
        factor = b.get("factor")
        background.append({"name": _ident(b.get("name") or f"layer{i}", f"Layer{i}"), "hash": b["hash"],
                           "factor": round(float(factor) if isinstance(factor, (int, float))
                                           else 0.15 + 0.6 * i / max(1, nb - 1), 3),
                           "size": list(bg_imgs[-1].size)})
    speed = TOPDOWN_SPEED if style == "topdown" else SIDE_SPEED
    gravity = SIDE_GRAVITY if style == "sideview" else 0.0
    scene = {"type": SCENE_TYPE, "asset": asset, "style": style, "tile": tile, "cols": cols, "rows": rows,
             "grid": layout["grid"], "spawn": layout["spawn"], "planner": layout.get("planner"),
             "tileset": {"columns": ATLAS_COLS, "count": len(at.tiles), "solid": sorted(at.solid),
                         "sets": at.sets},
             "layers": layers, "player": player, "prop_kinds": kinds, "props": props, "background": background,
             "physics": {"speed": speed * tile, "gravity": gravity * tile,
                         "jump": round((2 * SIDE_GRAVITY * SIDE_JUMP_TILES) ** 0.5 * tile, 2) if gravity else 0.0},
             "camera": {"view_rows": min(rows, 12 if style == "topdown" else 11)}, "notes": notes}
    atlas_img = at.image()
    mockup = render_mockup(scene, atlas_img, sheet, prop_imgs, bg_imgs)
    return scene, atlas_img, mockup, sheet, prop_imgs, tiled_tmx(scene, asset), ldtk_project(scene, asset)




def tiled_tmx(scene: dict[str, Any], asset: str) -> str:
    t, cols, rows = scene["tile"], scene["cols"], scene["rows"]
    count = scene["tileset"]["count"]
    m = ET.Element("map", {"version": "1.10", "tiledversion": "1.11.0", "orientation": "orthogonal",
                           "renderorder": "right-down", "width": str(cols), "height": str(rows), "tilewidth": str(t),
                           "tileheight": str(t), "infinite": "0", "nextlayerid": "1", "nextobjectid": "1"})
    props = ET.SubElement(m, "properties")
    ET.SubElement(props, "property", {"name": "style", "value": scene["style"]})
    ts = ET.SubElement(m, "tileset", {"firstgid": "1", "name": f"{asset}_tiles", "tilewidth": str(t),
                                      "tileheight": str(t), "tilecount": str(count), "columns": str(ATLAS_COLS)})
    rows_px = -(-count // ATLAS_COLS) * t
    ET.SubElement(ts, "image", {"source": f"{asset}_tiles.png", "width": str(ATLAS_COLS * t), "height": str(rows_px)})
    for tid in scene["tileset"]["solid"]:
        el = ET.SubElement(ts, "tile", {"id": str(tid)})
        ET.SubElement(ET.SubElement(el, "properties"), "property", {"name": "solid", "type": "bool", "value": "true"})
    lid = 1
    for b in scene["background"]:
        il = ET.SubElement(m, "imagelayer", {"id": str(lid), "name": b["name"], "parallaxx": str(b["factor"]),
                                             "parallaxy": "1" if scene["style"] == "sideview" else str(b["factor"]),
                                             "repeatx": "1"})
        ET.SubElement(il, "image", {"source": f"Background/{b['name']}.png", "width": str(b["size"][0]),
                                    "height": str(b["size"][1])})
        lid += 1
    for layer in scene["layers"]:
        el = ET.SubElement(m, "layer", {"id": str(lid), "name": layer["name"], "width": str(cols),
                                        "height": str(rows)})
        if layer["collide"]:
            ET.SubElement(ET.SubElement(el, "properties"), "property",
                          {"name": "collision", "type": "bool", "value": "true"})
        data = ET.SubElement(el, "data", {"encoding": "csv"})
        data.text = "\n" + ",\n".join(",".join(str(v + 1) for v in layer["tiles"][y * cols:(y + 1) * cols])
                                      for y in range(rows)) + "\n"
        lid += 1
    og = ET.SubElement(m, "objectgroup", {"id": str(lid), "name": "Objects"})
    oid = 1
    pl = scene["player"]
    ET.SubElement(ET.SubElement(og, "object", {"id": str(oid), "name": pl["name"], "type": "player",
                                               "x": f"{pl['pos'][0]:g}", "y": f"{pl['pos'][1]:g}"}), "point")
    for p in scene["props"]:
        oid += 1
        k = scene["prop_kinds"][p["kind"]]
        o = ET.SubElement(og, "object", {"id": str(oid), "name": k["name"], "type": "prop",
                                         "x": f"{p['pos'][0] - k['w'] / 2:g}", "y": f"{p['pos'][1] - k['h']:g}",
                                         "width": str(k["w"]), "height": str(k["h"])})
        ET.SubElement(ET.SubElement(o, "properties"), "property", {"name": "sprite", "type": "file",
                                                                    "value": f"Props/{k['name']}.png"})
    m.set("nextlayerid", str(lid + 1))
    m.set("nextobjectid", str(oid + 1))
    ET.indent(m, " ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(m, encoding="unicode") + "\n"


def _iid(*parts: Any) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "asset-studio/ldtk/" + "/".join(map(str, parts))))


def _ldtk_field_def(uid: int) -> dict[str, Any]:
    return {"identifier": "image", "doc": None, "__type": "String", "uid": uid, "type": "F_String", "isArray": False,
            "canBeNull": True, "arrayMinLength": None, "arrayMaxLength": None, "editorDisplayMode": "Hidden",
            "editorDisplayScale": 1, "editorDisplayPos": "Above", "editorLinkStyle": "StraightArrow",
            "editorDisplayColor": None, "editorAlwaysShow": False, "editorShowInWorld": True,
            "editorCutLongValues": True, "editorTextSuffix": None, "editorTextPrefix": None,
            "useForSmartColor": False, "exportToToc": False, "searchable": False, "min": None, "max": None,
            "regex": None, "acceptFileTypes": None, "defaultOverride": None, "textLanguageMode": None,
            "symmetricalRef": False, "autoChainRef": True, "allowOutOfLevelRef": True, "allowedRefs": "OnlySame",
            "allowedRefsEntityUid": None, "allowedRefTags": [], "tilesetUid": None}


def _ldtk_entity_def(ident: str, uid: int, w: int, h: int, color: str, fields: list[dict[str, Any]]
                     ) -> dict[str, Any]:
    return {"identifier": ident, "uid": uid, "tags": [], "exportToToc": False, "allowOutOfBounds": False, "doc": None,
            "width": w, "height": h, "resizableX": False, "resizableY": False, "minWidth": None, "maxWidth": None,
            "minHeight": None, "maxHeight": None, "keepAspectRatio": False, "tileOpacity": 1, "fillOpacity": 0.08,
            "lineOpacity": 0, "hollow": False, "color": color, "renderMode": "Rectangle", "showName": True,
            "tilesetId": None, "tileRenderMode": "FitInside", "tileRect": None, "uiTileRect": None,
            "nineSliceBorders": [], "maxCount": 0, "limitScope": "PerLevel", "limitBehavior": "MoveLastOne",
            "pivotX": 0.5, "pivotY": 1, "fieldDefs": fields}


def _ldtk_layer_def(ident: str, kind: str, uid: int, grid: int, tileset: int | None = None,
                    values: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"__type": kind, "identifier": ident, "type": kind, "uid": uid, "doc": None, "uiColor": None,
            "gridSize": grid, "guideGridWid": 0, "guideGridHei": 0, "displayOpacity": 1, "inactiveOpacity": 1,
            "hideInList": False, "hideFieldsWhenInactive": True, "canSelectWhenInactive": True,
            "renderInWorldView": True, "pxOffsetX": 0, "pxOffsetY": 0, "parallaxFactorX": 0, "parallaxFactorY": 0,
            "parallaxScaling": True, "requiredTags": [], "excludedTags": [], "autoTilesKilledByOtherLayerUid": None,
            "uiFilterTags": [], "useAsyncRender": False, "intGridValues": values or [], "intGridValuesGroups": [],
            "autoRuleGroups": [], "autoSourceLayerDefUid": None, "tilesetDefUid": tileset, "tilePivotX": 0,
            "tilePivotY": 0, "biomeFieldUid": None}


def _ldtk_layer(scene: dict[str, Any], ident: str, kind: str, def_uid: int, level_uid: int,
                tileset: tuple[int, str] | None = None) -> dict[str, Any]:
    return {"__identifier": ident, "__type": kind, "__cWid": scene["cols"], "__cHei": scene["rows"],
            "__gridSize": scene["tile"], "__opacity": 1, "__pxTotalOffsetX": 0, "__pxTotalOffsetY": 0,
            "__tilesetDefUid": tileset[0] if tileset else None, "__tilesetRelPath": tileset[1] if tileset else None,
            "iid": _iid(scene["asset"], ident), "levelId": level_uid, "layerDefUid": def_uid, "pxOffsetX": 0,
            "pxOffsetY": 0, "visible": True, "optionalRules": [], "intGridCsv": [], "autoLayerTiles": [], "seed": 0,
            "overrideTilesetUid": None, "gridTiles": [], "entityInstances": []}


def ldtk_project(scene: dict[str, Any], asset: str) -> str:
    """One-level LDtk 1.5 project: Entities (player + props, pivot at the feet), an IntGrid `Collision` layer
    (1 = solid, drawn faint over the tiles) and the scene's tile layers (top first) on the atlas the engines use."""
    t, cols, rows = scene["tile"], scene["cols"], scene["rows"]
    count = scene["tileset"]["count"]
    ts_uid, lvl_uid, ent_uid, col_uid = 1, 2, 3, 4
    player_def, prop_def, field_uid = 10, 11, 12
    rel = f"{asset}_tiles.png"
    tileset = {"__cWid": ATLAS_COLS, "__cHei": -(-count // ATLAS_COLS), "identifier": "Tiles", "uid": ts_uid,
               "relPath": rel, "embedAtlas": None, "pxWid": ATLAS_COLS * t, "pxHei": -(-count // ATLAS_COLS) * t,
               "tileGridSize": t, "spacing": 0, "padding": 0, "tags": [], "tagsSourceEnumUid": None, "enumTags": [],
               "customData": [], "savedSelections": [], "cachedPixelData": None}
    tile_defs = [(la["name"], 5 + i) for i, la in enumerate(reversed(scene["layers"]))]
    col_def = _ldtk_layer_def("Collision", "IntGrid", col_uid, t, values=[
        {"value": 1, "identifier": "solid", "color": "#404040", "tile": None, "groupUid": 0}])
    col_def["displayOpacity"] = 0.35
    layer_defs = [_ldtk_layer_def("Entities", "Entities", ent_uid, t), col_def,
                  *(_ldtk_layer_def(name, "Tiles", uid, t, ts_uid) for name, uid in tile_defs)]
    pl = scene["player"]
    entity_defs = [_ldtk_entity_def("Player", player_def, t, t * 2, "#3C8CFF", []),
                   _ldtk_entity_def("Prop", prop_def, t, t, "#BE823C", [_ldtk_field_def(field_uid)])]
    # Prop instances keep their own size; a non-resizable def would make LDtk snap them back to t×t.
    entity_defs[1].update(resizableX=True, resizableY=True)
    insts = {k: _ldtk_layer(scene, k, kind, uid, lvl_uid, (ts_uid, rel) if kind == "Tiles" else None)
             for k, kind, uid in (("Entities", "Entities", ent_uid), ("Collision", "IntGrid", col_uid),
                                  *((name, "Tiles", uid) for name, uid in tile_defs))}

    def entity(ident: str, def_uid: int, x: float, y: float, w: int, h: int, n: int,
               image: str | None = None) -> dict[str, Any]:
        fields = [] if image is None else [{"__identifier": "image", "__type": "String", "__value": image,
                                            "__tile": None, "defUid": field_uid,
                                            "realEditorValues": [{"id": "V_String", "params": [image]}]}]
        return {"__identifier": ident, "__grid": [int(x // t), int(max(0, y - 1) // t)], "__pivot": [0.5, 1],
                "__tags": [], "__tile": None, "__smartColor": "#3C8CFF" if ident == "Player" else "#BE823C",
                "__worldX": round(x), "__worldY": round(y), "iid": _iid(asset, ident, n), "width": w, "height": h,
                "defUid": def_uid, "px": [round(x), round(y)], "fieldInstances": fields}

    insts["Entities"]["entityInstances"].append(entity("Player", player_def, pl["pos"][0], pl["pos"][1], t, 2 * t, 0))
    for n, p in enumerate(scene["props"], 1):
        k = scene["prop_kinds"][p["kind"]]
        insts["Entities"]["entityInstances"].append(
            entity("Prop", prop_def, p["pos"][0], p["pos"][1], k["w"], k["h"], n, f"Props/{k['name']}.png"))
    solid_set = set(scene["tileset"]["solid"])
    for layer in scene["layers"]:
        tl = insts[layer["name"]]
        for i, tid in enumerate(layer["tiles"]):
            if tid < 0:
                continue
            x, y = i % cols, i // cols
            tl["gridTiles"].append({"px": [x * t, y * t], "src": [(tid % ATLAS_COLS) * t, (tid // ATLAS_COLS) * t],
                                    "f": 0, "t": tid, "d": [i], "a": 1})
        if layer["collide"]:
            insts["Collision"]["intGridCsv"] = [1 if tid in solid_set and tid >= 0 else 0 for tid in layer["tiles"]]
    level = {"identifier": "Level_0", "iid": _iid(asset, "level"), "uid": lvl_uid, "worldX": 0, "worldY": 0,
             "worldDepth": 0, "pxWid": cols * t, "pxHei": rows * t, "__bgColor": "#3A4254", "bgColor": None,
             "useAutoIdentifier": False, "bgRelPath": None, "bgPos": None, "bgPivotX": 0.5, "bgPivotY": 0.5,
             "__smartColor": "#ADADB5", "__bgPos": None, "externalRelPath": None, "fieldInstances": [],
             "layerInstances": list(insts.values()), "__neighbours": []}
    doc = {"__header__": {"fileType": "LDtk Project JSON", "app": "LDtk", "doc": "https://ldtk.io/json",
                          "schema": "https://ldtk.io/files/JSON_SCHEMA.json", "appAuthor": "Sebastien 'deepnight' Benard",
                          "appVersion": "1.5.3", "url": "https://ldtk.io"},
           "iid": _iid(asset, "project"), "jsonVersion": "1.5.3", "appBuildId": 473703, "nextUid": 100,
           "identifierStyle": "Capitalize", "toc": [], "worldLayout": "Free", "worldGridWidth": cols * t,
           "worldGridHeight": rows * t, "defaultLevelWidth": cols * t, "defaultLevelHeight": rows * t,
           "defaultPivotX": 0, "defaultPivotY": 0, "defaultGridSize": t, "defaultEntityWidth": t,
           "defaultEntityHeight": t, "bgColor": "#40465B", "defaultLevelBgColor": "#3A4254", "minifyJson": False,
           "externalLevels": False, "exportTiled": False, "simplifiedExport": False, "imageExportMode": "None",
           "exportLevelBg": True, "pngFilePattern": None, "backupOnSave": False, "backupLimit": 10,
           "backupRelPath": None, "levelNamePattern": "Level_%idx", "tutorialDesc": None, "customCommands": [],
           "flags": [], "defs": {"layers": layer_defs, "entities": entity_defs, "tilesets": [tileset], "enums": [],
                                 "externalEnums": [], "levelFields": []},
           "levels": [level], "worlds": [], "dummyWorldIid": _iid(asset, "world")}
    return json.dumps(doc, indent=1, ensure_ascii=False)




class SceneEngineCheck(OpExecutor):
    kind = "op.scene_engine_check"
    version = "1"
    lane = Lane.CPU

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    def cacheable(self, params: dict[str, Any]) -> bool:
        # The result depends on the installed engines and the Unity project, not only on the inputs.
        return False

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from ..export import playable_scene as px

        engine = _choice(params, "engine", ENGINES)
        frames = _int(params, "frames", 45, 5, 600)
        refs = inputs.get("scene") or []
        scene = await read_doc(ctx, refs, SCENE_TYPE, "scene")
        report: dict[str, Any] = {"engines": {}}
        images: list[BlobRef] = []
        want = ("godot", "unity") if engine == "both" else () if engine == "none" else (engine,)
        for i, eng in enumerate(want):
            ctx.progress(i, len(want), label=eng)
            res = await px.engine_check(ctx, self.settings, scene, eng, frames)
            report["engines"][eng] = {k: v for k, v in res.items() if k != "images"}
            for name, path in res.get("images", []):
                images.append(await ctx.blobs.put_file(path, name=name, mime="image/png"))
            if res.get("errors"):
                ctx.log(f"{eng}: {'; '.join(res['errors'][:3])}", level="warning")
        ran = [r for r in report["engines"].values() if not r.get("skipped")]
        report["ok"] = all(r.get("ok") for r in ran) if ran else None
        ctx.progress(1.0)
        meta = {e: {"ok": r.get("ok"), "moved": r.get("moved"), "skipped": r.get("skipped")}
                for e, r in report["engines"].items()}
        return StepResult(outputs={"images": images, "report": [await _put_json(ctx, report, "engine_check.json")],
                                   "scene": refs},
                          meta={"engines": meta, "ok": report["ok"]})


PLAYABLE_OPS = (PlayableScene,)
SETTINGS_OPS = (SceneGather, SceneEngineCheck)
