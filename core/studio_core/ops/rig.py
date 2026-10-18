"""Cutout-rig ops: `op.parts` (SAM masks -> partition + joint guess), `op.layers` (final layers, joint fill
requests) and `op.spine_export` (Spine 4.2 skeleton/atlas/page + setup-pose check + HTML preview)."""
from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from . import parts as pt
from . import spine as sp
from .executors import OpExecutor, _images_in, _load, _put_png

DEFAULT_ROLES = ["head", "torso", "arm", "hand", "leg", "cape"]
DEFAULT_INPAINT = ["torso", "upper_arm_l", "upper_arm_r", "thigh_l", "thigh_r"]


async def _put_json(ctx: StepContext, payload: Any, name: str) -> BlobRef:
    data = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    return await ctx.blobs.put_bytes(data, name=name, mime="application/json")


async def _read_json(ctx: StepContext, inputs: Outputs, name: str, required: bool = True) -> Any:
    refs = inputs.get(name) or []
    if not refs:
        if required:
            raise StepError("op_no_input", f"input '{name}' is empty")
        return None
    try:
        return json.loads(await asyncio.to_thread(ctx.blobs.path(refs[0].hash).read_bytes))
    except (OSError, ValueError) as e:
        raise StepError("op_bad_input", f"input '{name}' is not JSON: {e}") from e


def _one(inputs: Outputs, name: str) -> BlobRef:
    refs = _images_in(inputs, name)
    if len(refs) != 1:
        raise StepError("op_bad_input", f"expected exactly 1 '{name}', got {len(refs)}")
    return refs[0]


def _mask(img: Image.Image, size: tuple[int, int]) -> np.ndarray:
    g = img.convert("L")
    if g.size != size:
        g = g.resize(size, Image.Resampling.NEAREST)
    return np.asarray(g) > 127


def run_parts(rgba: Image.Image, masks: list[Image.Image], roles: list[str]) -> tuple[dict[str, Any], Image.Image,
                                                                                       Image.Image]:
    arr = np.asarray(rgba)
    seed = {r: _mask(m, rgba.size) for r, m in zip(roles, masks)}
    p = pt.partition(arr[..., 3], seed)
    boxes = {n: pt._bbox(p.mask(n)) for n in p.names}
    meta = {"size": list(rgba.size), "names": p.names, "center_x": round(p.center_x, 1), "joints": p.joints,
            "boxes": {k: v for k, v in boxes.items() if v}, "hints": p.hints,
            "areas": {n: int(p.mask(n).sum()) for n in p.names}}
    labels = Image.fromarray(p.labels.astype(np.uint8), "L")
    return meta, labels, pt.overlay(rgba, p.labels, p.names, p.joints, pt.SKELETON_LINES)


class Parts(OpExecutor):
    kind = "op.parts"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        rgba = (await _load(ctx, _one(inputs, "image"))).convert("RGBA")
        refs = _images_in(inputs, "masks")
        roles = [str(r) for r in (params.get("roles") or DEFAULT_ROLES)]
        if len(refs) != len(roles):
            raise StepError("op_bad_input", f"{len(refs)} masks for {len(roles)} roles {roles}")
        masks = [await _load(ctx, r) for r in refs]
        try:
            meta, labels, preview = await asyncio.to_thread(run_parts, rgba, masks, roles)
        except pt.PartsError as e:
            raise StepError(e.code, str(e), detail=e.detail) from e
        ctx.progress(0.8)
        out = {"meta": [await _put_json(ctx, meta, "parts.json")],
               "labels": [await _put_png(ctx, labels, "labels.png")],
               "preview": [await _put_png(ctx, preview, "parts_preview.png")]}
        ctx.progress(1.0)
        return StepResult(outputs=out, meta={"names": meta["names"], "hints": meta["hints"]})


def proposed_joints(bones: Any) -> dict[str, Any] | None:
    if not isinstance(bones, dict):
        return None
    j = bones.get("joints", bones)
    return j if isinstance(j, dict) else None


def run_layers(rgba: Image.Image, labels_img: Image.Image, meta: dict[str, Any], bones: Any, params: dict[str, Any]
               ) -> tuple[dict[str, Any], list[Image.Image], list[Image.Image], Image.Image]:
    arr = np.asarray(rgba)
    labels = np.asarray(labels_img.convert("L")).astype(np.int16)
    if labels.shape != arr.shape[:2]:
        raise pt.PartsError("op_bad_input", "labels and image sizes differ")
    names = list(meta["names"])
    auto = {k: tuple(v) for k, v in meta["joints"].items()}
    sil = arr[..., 3] > 0
    ys = np.nonzero(sil)[0]
    fig_h = float(ys.max() - ys.min() + 1)
    use = None if str(params.get("rig", "claude")) == "auto" else proposed_joints(bones)
    joints, sources = pt.merge_joints(auto, use, labels, names, float(meta["center_x"]),
                                      float(params.get("tolerance", 0.06)) * fig_h)
    p = pt.Partition(labels, names, joints, float(meta["center_x"]))
    split = pt.final_parts(p, joints)
    fills = pt.joint_fills(split, joints, float(params.get("overlap", 0.6)))
    inpaint = set(params.get("inpaint", DEFAULT_INPAINT) or []) if params.get("fill", True) else set()
    order = pt.draw_order(list(split))
    crops: list[Image.Image] = []
    entries: list[dict[str, Any]] = []
    holes: list[Image.Image] = []
    hole_meta: list[dict[str, Any]] = []
    pivots = {b[0]: b[2] for b in sp.BONES}
    for z, name in enumerate(order):
        m = split[name] | fills.get(name, np.zeros_like(sil))
        box = pt._bbox(m)
        if box is None:
            continue
        x0, y0, x1, y1 = box
        crop = arr[y0:y1, x0:x1].copy()
        crop[..., 3] = np.where(m[y0:y1, x0:x1], crop[..., 3], 0)
        crops.append(Image.fromarray(crop, "RGBA"))
        pj = pivots.get(name)
        entries.append({"name": name, "offset": [x0, y0], "size": [x1 - x0, y1 - y0], "z": z,
                        "pivot": list(joints[pj]) if pj in joints else None,
                        "fill_px": int(fills[name].sum()) if name in fills else 0})
        if name in inpaint and name in fills:
            req, rbox = pt.fill_request(arr, split[name], fills[name])
            holes.append(req)
            hole_meta.append({"part": name, "box": rbox})
    bottom = float(ys.max() + 1)
    out = {"size": list(rgba.size), "joints": {k: list(v) for k, v in joints.items()}, "joint_sources": sources,
           "origin": [joints["pelvis"][0], bottom], "parts": entries, "holes": hole_meta}
    vis = np.zeros_like(labels)
    for i, name in enumerate(order):
        vis[split[name]] = i + 1
    preview = pt.overlay(rgba, vis, order, joints, pt.SKELETON_LINES)
    return out, crops, holes, preview


class Layers(OpExecutor):
    kind = "op.layers"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        rgba = (await _load(ctx, _one(inputs, "image"))).convert("RGBA")
        labels = await _load(ctx, _one(inputs, "labels"))
        meta = await _read_json(ctx, inputs, "parts")
        bones = await _read_json(ctx, inputs, "bones", required=False)
        try:
            out, crops, holes, preview = await asyncio.to_thread(run_layers, rgba, labels, meta, bones, params)
        except pt.PartsError as e:
            raise StepError(e.code, str(e), detail=e.detail) from e
        except KeyError as e:
            raise StepError("op_bad_input", f"parts meta lacks {e}") from e
        layer_refs = [await _put_png(ctx, c, f"{e['name']}.png") for c, e in zip(crops, out["parts"])]
        hole_refs = [await _put_png(ctx, h, f"fill_{m['part']}.png") for h, m in zip(holes, out["holes"])]
        res: Outputs = {"layers": layer_refs, "meta": [await _put_json(ctx, out, "layers.json")],
                        "preview": [await _put_png(ctx, preview, "layers_preview.png")]}
        if hole_refs:
            res["holes"] = hole_refs
        ctx.progress(1.0)
        return StepResult(outputs=res, meta={"joint_sources": out["joint_sources"], "parts": len(layer_refs),
                                             "holes": [h["part"] for h in out["holes"]]})


def merge_fills(layers: list[sp.Layer], hole_meta: list[dict[str, Any]], holes: list[Image.Image],
                fills: list[Image.Image]) -> list[str]:
    by_name = {l.name: l for l in layers}
    merged = []
    for hm, req, fill in zip(hole_meta, holes, fills):
        layer = by_name.get(hm["part"])
        if layer is None:
            continue
        x0, y0, x1, y1 = hm["box"]
        if fill.size != (x1 - x0, y1 - y0):
            fill = fill.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
        hole = np.asarray(req.convert("RGBA"))[..., 3] == 0
        frgb = np.asarray(fill.convert("RGB"))
        lay = np.asarray(layer.image).copy()
        ox, oy = layer.offset
        hy, hx = np.nonzero(hole)
        cx, cy = hx + x0 - ox, hy + y0 - oy
        ok = (cx >= 0) & (cy >= 0) & (cx < lay.shape[1]) & (cy < lay.shape[0])
        hy, hx, cx, cy = hy[ok], hx[ok], cx[ok], cy[ok]
        new = frgb[hy, hx]
        # The model sometimes paints background into the hole; those pixels keep the copied original instead.
        bg = (new.min(axis=1) > 235) & (lay[cy, cx, :3].min(axis=1) <= 235)
        lay[cy[~bg], cx[~bg], :3] = new[~bg]
        layer.image = Image.fromarray(lay, "RGBA")
        merged.append(hm["part"])
    return merged


def run_export(original: Image.Image | None, layers: list[sp.Layer], meta: dict[str, Any], name: str,
               with_anim: bool, padding: int) -> dict[str, Any]:
    joints = {k: tuple(v) for k, v in meta["joints"].items()}
    origin = tuple(meta["origin"])
    doc, _ = sp.build_skeleton(layers, joints, origin, name, with_anim)
    positions, pw, ph = sp.pack([l.image.size for l in layers], padding)
    page = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    regions = []
    for l, (x, y) in zip(layers, positions):
        page.paste(l.image, (x, y))
        regions.append((l.name, x, y, l.image.width, l.image.height))
    page_name = f"{name}.png"
    atlas = sp.atlas_text(page_name, pw, ph, regions)
    problems = sp.validate_skeleton(doc, atlas)
    region_imgs = {l.name: l.image for l in layers}
    size = tuple(meta["size"])
    setup = sp.compose(doc, region_imgs, origin, size)  # type: ignore[arg-type]
    report: dict[str, Any] = {"problems": problems, "page": [pw, ph], "bones": len(doc["bones"]),
                              "slots": len(doc["slots"]), "joint_sources": meta.get("joint_sources")}
    if original is not None:
        report["setup_vs_original"] = sp.compare(setup, original)
    frames = None
    if with_anim:
        w, h = size
        mw, mh = int(w * 0.2), int(h * 0.1)
        cells = [("idle", 0.5), ("idle", 1.5), ("relax", 0.6), ("relax", 1.2)]
        frames = Image.new("RGBA", ((w + 2 * mw) * len(cells), h + 2 * mh), (236, 236, 240, 255))
        for i, (a, t) in enumerate(cells):
            f = sp.compose(doc, region_imgs, origin, (w + 2 * mw, h + 2 * mh), a, t, (mw, mh))
            frames.alpha_composite(f, (i * (w + 2 * mw), 0))
    sk_bytes = json.dumps(doc, indent=1).encode("utf-8")
    buf = io.BytesIO()
    page.save(buf, "PNG")
    html = sp.preview_html(name, sk_bytes, atlas.encode(), page_name, buf.getvalue(),
                           list((doc.get("animations") or {}).keys()))
    bg = Image.new("RGBA", setup.size, (236, 236, 240, 255))
    bg.alpha_composite(setup)
    return {"doc": doc, "skeleton": sk_bytes, "atlas": atlas, "page": buf.getvalue(), "page_name": page_name,
            "setup": bg, "frames": frames, "html": html, "report": report}


class SpineExport(OpExecutor):
    kind = "op.spine_export"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        meta = await _read_json(ctx, inputs, "meta")
        layer_refs = _images_in(inputs, "layers")
        if len(layer_refs) != len(meta.get("parts", [])):
            raise StepError("op_bad_input", f"{len(layer_refs)} layer images for {len(meta.get('parts', []))} parts")
        layers = [sp.Layer(e["name"], (await _load(ctx, r)).convert("RGBA"), tuple(e["offset"]))
                  for r, e in zip(layer_refs, meta["parts"])]
        holes = [await _load(ctx, r) for r in inputs.get("holes") or []]
        fills = [await _load(ctx, r) for r in inputs.get("fills") or []]
        merged: list[str] = []
        if fills:
            if len(fills) != len(holes) or len(holes) != len(meta.get("holes", [])):
                raise StepError("op_bad_input", f"{len(fills)} fills for {len(holes)} fill requests")
            merged = await asyncio.to_thread(merge_fills, layers, meta["holes"], holes, fills)
        original = None
        if inputs.get("image"):
            original = (await _load(ctx, inputs["image"][0])).convert("RGBA")
        name = str(params.get("name") or "character").strip().replace(" ", "_") or "character"
        res = await asyncio.to_thread(run_export, original, layers, meta, name, bool(params.get("animations", True)),
                                      int(params.get("padding", 2)))
        if res["report"]["problems"]:
            raise StepError("spine_invalid", "; ".join(res["report"]["problems"][:5]), detail=res["report"])
        report = {**res["report"], "inpainted": merged}
        out: Outputs = {
            "skeleton": [await ctx.blobs.put_bytes(res["skeleton"], name=f"{name}.json", mime="application/json")],
            "atlas": [await ctx.blobs.put_bytes(res["atlas"].encode(), name=f"{name}.atlas", mime="text/plain")],
            "texture": [await ctx.blobs.put_bytes(res["page"], name=res["page_name"], mime="image/png")],
            "preview": [await _put_png(ctx, res["setup"], "setup_pose.png")],
            "html": [await ctx.blobs.put_bytes(res["html"].encode(), name=f"{name}.html", mime="text/html")],
            "report": [await _put_json(ctx, report, "spine_report.json")],
        }
        if res["frames"] is not None:
            out["frames"] = [await _put_png(ctx, res["frames"], "anim_frames.png")]
        ctx.progress(1.0)
        return StepResult(outputs=out, meta=report)


RIG_OPS: tuple[type[OpExecutor], ...] = (Parts, Layers, SpineExport)
