"""Project style lock: `op.style_audit` measures every approved image version of a project against 1-3 anchor images
(palette ΔE2000, contrast, saturation, outline weight, optional CLIP), ranks the drift, writes style_lock.json plus a
ranking grid, LUT-recolours the colour outliers on CPU and prepares the style outliers for a Klein restyle (off by
default). `op.style_lock_finish` merges both into new images; no existing version is touched."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..adapters import style_metrics as sm
from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png
from .lut import REF_BG, fit_lut, lut_applied, with_alpha
from .restyle import _json_in

REPORT_TYPE = "asset-studio/style-lock-report"
REPORT_FILE = "style_lock.json"
LAYOUT_FILE = "style_lock_layout.json"
MAX_ANCHORS = 3
SHEET_ASPECT = 2.2
CLIP_SIDE = 448
DEFAULT_STYLE = "consistent palette, line weight and shading matching the rest of the project"


def _sheet_like(blob: dict[str, Any]) -> bool:
    w, h = blob.get("width"), blob.get("height")
    if "sheet" in str(blob.get("name") or "").lower():
        return True
    return bool(w and h) and (w / h >= SHEET_ASPECT or h / w >= SHEET_ASPECT)


def approved_rows(db: Path, run_id: str, project: str, limit: int
                  ) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]], int]:
    """(project {id, name}, usable image versions newest first, skipped, non-image count) of the project's approved
    versions, read-only from SQLite since a step has no repo handle."""
    if not db.is_file():
        return {"id": "", "name": ""}, [], [], 0
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        if project:
            row = con.execute("SELECT id, name FROM projects WHERE id = ? OR lower(name) = lower(?) "
                              "ORDER BY id = ? DESC", (project, project, project)).fetchone()
        else:
            row = con.execute("SELECT p.id, p.name FROM runs r JOIN projects p ON p.id = r.project_id "
                              "WHERE r.id = ?", (run_id,)).fetchone()
        if row is None:
            return {"id": "", "name": ""}, [], [], 0
        rows = con.execute("SELECT id, asset_name, run_id, blob FROM versions WHERE project_id = ? "
                           "AND status = 'approved' AND run_id != ? ORDER BY created_at DESC", (row["id"], run_id))
        usable: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen: set[str] = set()
        non_image = 0
        for r in rows:
            blob = json.loads(r["blob"])
            if not str(blob.get("mime") or "").startswith("image/"):
                non_image += 1
                continue
            entry = {"version_id": r["id"], "run_id": r["run_id"], "asset_name": r["asset_name"], "blob": blob}
            if blob["hash"] in seen:
                continue
            seen.add(blob["hash"])
            if _sheet_like(blob):
                skipped.append({"version_id": r["id"], "asset_name": r["asset_name"], "reason": "sprite sheet"})
            elif len(usable) < limit:
                usable.append(entry)
        return {"id": row["id"], "name": row["name"]}, usable, skipped, non_image
    finally:
        con.close()


def _recolor(img: Image.Image, stats: sm.AssetStats, ref: dict[str, Any], strength: float) -> Image.Image:
    lut = fit_lut(stats.rgb, sm.transfer_target(stats.rgb, ref, strength))
    out = Image.new("RGBA", img.size)
    step = max(1, 1_000_000 // img.width)
    for y in range(0, img.height, step):
        out.paste(lut_applied(img.crop((0, y, img.width, min(img.height, y + step))), lut), (0, y))
    return out


def _flat(img: Image.Image) -> Image.Image:
    # Klein reads only RGB: a flat mid grey is neutral where black would be graded as scenery.
    bg = Image.new("RGBA", img.size, (*REF_BG, 255))
    bg.alpha_composite(img.convert("RGBA"))
    return bg.convert("RGB")


def _clip_copy(img: Image.Image) -> Image.Image:
    c = img.copy()
    c.thumbnail((CLIP_SIDE, CLIP_SIDE), Image.Resampling.BOX)
    return c


def _stem(name: str) -> str:
    return Path(name or "asset").stem or "asset"


def _unique(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        k, cand = 2, n
        while cand in out:
            cand, k = f"{n}_{k}", k + 1
        out.append(cand)
    return out


def ranking_grid(cells: list[tuple[Image.Image, str, str, bool]], cols: int = 6, cell: int = 160) -> Image.Image:
    rows = max(1, -(-len(cells) // cols))
    gap, label = 6, 28
    out = Image.new("RGB", (gap + cols * (cell + gap), gap + rows * (cell + label + gap)), (24, 24, 28))
    d = ImageDraw.Draw(out)
    for i, (img, title, sub, flagged) in enumerate(cells):
        x, y = gap + (i % cols) * (cell + gap), gap + (i // cols) * (cell + label + gap)
        tile = Image.new("RGBA", (cell, cell), (46, 46, 52, 255))
        thumb = img.convert("RGBA")
        thumb.thumbnail((cell - 8, cell - 8), Image.Resampling.LANCZOS)
        tile.alpha_composite(thumb, ((cell - thumb.width) // 2, (cell - thumb.height) // 2))
        out.paste(tile.convert("RGB"), (x, y))
        if flagged:
            d.rectangle((x, y, x + cell - 1, y + cell - 1), outline=(230, 70, 70), width=3)
        d.text((x + 2, y + cell + 2), title[:26], fill=(230, 230, 230))
        d.text((x + 2, y + cell + 14), sub[:30], fill=(170, 170, 170))
    return out


async def _put_json(ctx: StepContext, doc: Any, name: str) -> BlobRef:
    return await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode("utf-8"), name=name,
                                     mime="application/json")


def _bool(params: dict[str, Any], key: str, default: bool) -> bool:
    v = params.get(key, default)
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")


def _num(params: dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(params.get(key, default))
    except (TypeError, ValueError) as e:
        raise StepError("op_bad_params", f"bad {key}: {e}") from e
    if not lo <= v <= hi:
        raise StepError("op_bad_params", f"{key} must be in [{lo}, {hi}]")
    return v


class StyleAudit(OpExecutor):
    kind = "op.style_audit"
    version = "1"

    def __init__(self, settings: Any = None, embedder: Any = None) -> None:
        self.settings = settings
        self.embedder = embedder

    def cacheable(self, params: dict[str, Any]) -> bool:
        # The project's approved versions change between runs of this step.
        return False

    def _clip(self) -> Any:
        if self.embedder is not None:
            return self.embedder
        if self.settings is None:
            return None
        from ..adapters.embedder import ClipEmbedder, model_dir

        return ClipEmbedder(model_dir(self.settings))

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        k = _num(params, "k", 3.0, 1.0, 10.0)
        strength = _num(params, "strength", 0.6, 0.0, 1.0)
        limit = int(_num(params, "max_assets", 200, 1, 2000))
        top = int(_num(params, "top", 24, 1, 120))
        recolor, restyle = _bool(params, "recolor", True), _bool(params, "restyle", False)
        use_clip = _bool(params, "clip", True)
        style = str(params.get("style") or "").strip() or DEFAULT_STYLE
        project = str(params.get("project") or "").strip()
        warnings: list[str] = []

        anchor_refs = _images_in(inputs, "anchors")[:MAX_ANCHORS]
        anchors, anchor_stats, anchor_skipped = [], [], []
        for ref in anchor_refs:
            img = await _load(ctx, ref)
            st = await asyncio.to_thread(sm.measure_asset, img)
            if st is None:
                anchor_skipped.append(ref.name or ref.hash[:8])
                continue
            anchors.append(img)
            anchor_stats.append(st)
        if not anchor_stats:
            raise StepError("op_bad_input", "no usable anchor image (fully transparent or blank)")
        if anchor_skipped:
            warnings.append(f"anchor ignored (empty): {', '.join(anchor_skipped)}")
        ref = sm.anchor_profile(anchor_stats)

        if self.settings is None:
            raise StepError("op_no_input", "style audit needs the project database (settings)")
        proj, versions, skipped, non_image = await asyncio.to_thread(
            approved_rows, Path(getattr(self.settings, "db_path", "")), ctx.run_id, project, limit)
        if project and not proj["id"]:
            raise StepError("op_bad_params", f"unknown project '{project}'")
        if not versions:
            # Inside a fresh workflow (character-pack) nothing is approved yet: an empty report, not a failed run.
            warnings.append("project has no approved image versions yet: audit skipped, nothing to compare to the anchors")

        items: list[dict[str, Any]] = []
        for i, v in enumerate(versions):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.progress(i, len(versions), label="measure")
            try:
                img = await _load(ctx, BlobRef(**v["blob"]))
            except StepError:
                skipped.append({"version_id": v["version_id"], "asset_name": v["asset_name"], "reason": "unreadable"})
                continue
            st = await asyncio.to_thread(sm.measure_asset, img)
            if st is None:
                skipped.append({"version_id": v["version_id"], "asset_name": v["asset_name"],
                                "reason": "empty image"})
                continue
            items.append({**v, "img": img, "stats": st, "dist": sm.distances(st, ref)})

        metrics_used = ["palette", "contrast", "saturation"]
        if any("outline" in it["dist"] for it in items):
            metrics_used.append("outline")
        embedder = self._clip() if use_clip and items else None
        if embedder is not None and await asyncio.to_thread(embedder.available):
            try:
                cd = await asyncio.to_thread(sm.clip_distances, embedder, [_clip_copy(a) for a in anchors],
                                             [_clip_copy(it["img"]) for it in items])
                for it, d in zip(items, cd):
                    it["dist"]["clip"] = d
                metrics_used.append("clip")
            except Exception as e:  # onnx/model failures must not lose the CPU metrics
                warnings.append(f"CLIP skipped: {e}")
        elif use_clip and items:
            warnings.append("CLIP model unavailable: style distance metric skipped")

        if not items:
            ranked, limits = [], {}
        elif len(items) < sm.MIN_PEERS:
            warnings.append(f"only {len(items)} measurable asset(s): need {sm.MIN_PEERS} for a threshold, none flagged")
            ranked, limits = [{"z": {}, "flags": [], "score": 0.0} for _ in items], {}
        else:
            ranked, limits = sm.rank([it["dist"] for it in items], k)
        if limits.get("palette") and limits["palette"]["median"] > sm.ABS_MIN["palette"]:
            warnings.append("most assets are far from the anchors; a distribution threshold under-flags them")
        for it, r in zip(items, ranked):
            it.update(r)
        items.sort(key=lambda it: (-it["score"], it["asset_name"]))

        names = _unique([f"{_stem(it['asset_name'])}_stylelock" for it in items])
        recolored: list[BlobRef] = []
        sources: list[BlobRef] = []
        restyle_in: list[BlobRef] = []
        prompts: list[str] = []
        layout_items: list[dict[str, Any]] = []
        for n, (it, name) in enumerate(zip(items, names)):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            color = bool(set(it["flags"]) & set(sm.COLOR_METRICS))
            styled = bool(set(it["flags"]) & set(sm.STYLE_METRICS))
            do_restyle = styled and restyle
            fixed = it["img"]
            if color and recolor:
                fixed = await asyncio.to_thread(_recolor, it["img"], it["stats"], ref, strength)
                after = await asyncio.to_thread(sm.measure_asset, fixed)
                it["delta_e_after"] = round(sm.distances(after, ref)["palette"], 3) if after else None
            if (color and recolor) and not do_restyle:
                recolored.append(await _put_png(ctx, fixed, f"{name}.png"))
            if do_restyle:
                sources.append(await _put_png(ctx, fixed, f"{name}_src.png"))
                restyle_in.append(await _put_png(ctx, await asyncio.to_thread(_flat, fixed), f"{name}_in.png"))
                hint = sm.outline_hint(it["stats"].outline, ref["outline"]) if "outline" in it["flags"] else ""
                prompts.append(f"{style}. Redraw this image in that style; keep the subject, pose, composition and "
                               f"proportions unchanged. {hint}".strip())
                layout_items.append({"version_id": it["version_id"], "file": f"{name}.png"})
            made = bool(color and recolor) or do_restyle
            it["fix"] = {"color": color, "style": styled, "recolored": bool(color and recolor),
                         "restyle_queued": do_restyle, "file": f"{name}.png" if made else None}
            ctx.progress(n + 1, len(items), label="fix")

        assets = []
        for rank_i, it in enumerate(items, 1):
            st: sm.AssetStats = it["stats"]
            assets.append({
                "rank": rank_i, "version_id": it["version_id"], "run_id": it["run_id"],
                "asset_name": it["asset_name"], "blob": it["blob"], "score": it["score"],
                "flagged": bool(it["flags"]), "flags": it["flags"],
                "metrics": {"palette_de": round(it["dist"]["palette"], 3), "contrast": round(st.contrast, 4),
                            "saturation": round(st.saturation, 4),
                            "outline": None if st.outline is None else round(st.outline, 3),
                            "clip_dist": round(it["dist"]["clip"], 4) if "clip" in it["dist"] else None},
                "distances": {m: round(v, 4) for m, v in it["dist"].items()}, "z": it["z"],
                "fix": it["fix"], "delta_e_after": it.get("delta_e_after")})
        de = [a["metrics"]["palette_de"] for a in assets]
        after = [a["delta_e_after"] for a in assets if a["delta_e_after"] is not None]
        report = {
            "type": REPORT_TYPE, "version": 1, "created_at": time.time(), "project": proj,
            "anchors": {"count": len(anchor_stats), "palette": [_hex(c) for c in ref["colors"]],
                        "metrics": {"contrast": round(ref["contrast"], 4), "saturation": round(ref["saturation"], 4),
                                    "outline": None if ref["outline"] is None else round(ref["outline"], 3)}},
            "metrics_used": metrics_used,
            "threshold": {"method": "limit = median + k * max(1.4826 * MAD, floor) per metric; flagged when past the "
                                    "limit and past the metric's absolute minimum", "k": k, "min_assets": sm.MIN_PEERS,
                          "per_metric": limits},
            "options": {"recolor": recolor, "restyle": restyle, "strength": strength, "clip": use_clip,
                        "style": style},
            "assets": assets, "skipped": skipped,
            "summary": {"total": len(assets), "flagged": sum(a["flagged"] for a in assets), "skipped": len(skipped),
                        "non_image": non_image, "recolored": sum(a["fix"]["recolored"] for a in assets),
                        "restyle_queued": len(restyle_in),
                        "median_palette_de": round(float(np.median(de)), 3) if de else None,
                        "median_palette_de_after": round(float(np.median(after)), 3) if after else None},
            "warnings": warnings}
        for w in warnings:
            ctx.log(w, "warning")
        cells = [(a, "anchor", "", False) for a in anchors[:MAX_ANCHORS]]
        cells += [(it["img"], f"#{n} {_stem(it['asset_name'])}",
                   f"score {it['score']:.1f} dE {it['dist']['palette']:.1f}", bool(it["flags"]))
                  for n, it in enumerate(items[:top], 1)]
        grid = await asyncio.to_thread(ranking_grid, cells)
        ctx.progress(1.0)
        return StepResult(
            outputs={"report": [await _put_json(ctx, report, REPORT_FILE)],
                     "preview": [await _put_png(ctx, grid, "style_lock_ranking.png")],
                     "recolored": recolored, "sources": sources, "restyle_in": restyle_in,
                     "layout": [await _put_json(ctx, {"prompts": prompts, "items": layout_items}, LAYOUT_FILE)]},
            meta={"project": proj["id"], "assets": len(assets), "flagged": report["summary"]["flagged"],
                  "recolored": len(recolored), "restyle_queued": len(restyle_in), "metrics": metrics_used})


def _hex(rgb: np.ndarray) -> str:
    r, g, b = (int(round(float(c) * 255)) for c in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


class StyleLockFinish(OpExecutor):
    kind = "op.style_lock_finish"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        report = await _json_in(ctx, inputs, "report")
        layout = await _json_in(ctx, inputs, "layout")
        if not isinstance(report, dict) or report.get("type") != REPORT_TYPE:
            raise StepError("op_bad_input", "report input is not a style-lock report")
        recolored = list(inputs.get("recolored") or [])
        sources = list(inputs.get("sources") or [])
        edits = list(inputs.get("edits") or [])
        files = [it.get("file") for it in (layout or {}).get("items") or []]
        out = list(recolored)
        restyled: list[str] = []
        for i, src_ref in enumerate(sources):
            if i >= len(edits):
                ctx.log(f"no Klein edit for {files[i] if i < len(files) else i}: asset left unchanged", "warning")
                continue
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            src, edit = await _load(ctx, src_ref), await _load(ctx, edits[i])
            final = with_alpha(edit.convert("RGB").resize(src.size, Image.Resampling.LANCZOS), src)
            name = files[i] if i < len(files) and files[i] else f"restyled_{i:02d}.png"
            out.append(await _put_png(ctx, final, name))
            restyled.append(name)
            ctx.progress(i + 1, max(1, len(sources)))
        done = {r.name for r in recolored} | set(restyled)
        for a in report.get("assets") or []:
            f = a.get("fix") or {}
            f["restyled"] = f.get("file") in restyled
            f["output"] = f.get("file") if f.get("file") in done else None
        report["summary"]["restyled"] = len(restyled)
        report["summary"]["new_versions"] = len(out)
        cells = []
        for r in out:
            cells.append((await _load(ctx, r), _stem(r.name or ""), "", False))
        grid = await asyncio.to_thread(ranking_grid, cells) if cells else Image.new("RGB", (8, 8), (24, 24, 28))
        return StepResult(outputs={"images": out, "report": [await _put_json(ctx, report, REPORT_FILE)],
                                   "preview": [await _put_png(ctx, grid, "style_lock_result.png")]},
                          meta={"recolored": len(recolored), "restyled": len(restyled)})


STYLE_AUDIT_SETTINGS_OPS = (StyleAudit,)
STYLE_AUDIT_OPS = (StyleLockFinish,)
