"""Skin variants of an existing Spine skeleton: render the setup pose, let an image editor (Flux.2 Klein) restyle the
whole figure at once so parts stay consistent, then match each atlas region's colour statistics, per paint colour, to
the pixels it owns in the edit and apply that to the region's texture. The art (lines, shading) is kept and nothing
moves, so the variant atlas drops in for the original one; hidden regions take the whole-figure map."""
from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from ..contracts import Outputs, StepContext, StepError, StepResult
from . import spine_runtime as rt
from .executors import OpExecutor, _images_in, _put_png
from .spine import _over
from .spine_ref import read_skeleton, region_images, skin_parts

BG = (255, 255, 255, 255)


def setup_render(doc: dict[str, Any], images: rt.RegionImages, parts: list[str], height: int = 1024
                 ) -> tuple[Image.Image, np.ndarray, list[str], np.ndarray]:
    """(setup pose RGBA on transparent, owner map: index into paths of the top region per pixel or -1, paths,
    per-pixel RGB tint of the owning slot/attachment)."""
    skins, _ = rt.resolve_skins(doc, parts)
    atts = rt.skin_attachments(doc, skins)
    pose = rt.Pose(doc)
    pts = rt.attachment_points(pose, atts)
    if not len(pts):
        raise StepError("op_bad_input", "nothing visible with these skins")
    (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
    pad = 0.04 * max(x1 - x0, y1 - y0)
    box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    scale = height / (box[3] - box[1])
    canvas = None
    owner = tint = None
    paths: list[str] = []
    for slot, path, layer, color in rt.layers(pose, atts, images, box, scale):
        if canvas is None:
            canvas = Image.new("RGBa", layer.size, (0, 0, 0, 0))
            owner = np.full((layer.height, layer.width), -1, np.int32)
            tint = np.ones((layer.height, layer.width, 3))
        if slot.get("blend") == "additive":
            continue
        canvas = _over(canvas, layer)
        if path not in paths:
            paths.append(path)
        top = np.asarray(layer)[..., 3] > 128
        owner[top] = paths.index(path)
        tint[top] = color[:3]
    if canvas is None:
        raise StepError("op_bad_input", "nothing visible with these skins")
    return canvas.convert("RGBA"), owner, paths, tint


def on_background(img: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", img.size, BG)
    bg.alpha_composite(img)
    return bg.convert("RGB")


def _content_box(rgb: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(np.abs(rgb.astype(np.int16) - 255).max(2) > 30)
    return None if len(xs) < 50 else (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def align_edit(render: Image.Image, edited: Image.Image) -> Image.Image:
    """The editor resizes and may shift the figure a little: map its content box onto the render's."""
    target = _content_box(np.asarray(on_background(render)))
    src = _content_box(np.asarray(edited.convert("RGB")))
    if target is None or src is None:
        return edited.convert("RGB").resize(render.size, Image.Resampling.LANCZOS)
    out = Image.new("RGB", render.size, BG[:3])
    tw, th = target[2] - target[0], target[3] - target[1]
    out.paste(edited.convert("RGB").crop(src).resize((tw, th), Image.Resampling.LANCZOS), target[:2])
    return out


def _interior(owner: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    # Pixels whose 3x3 neighbourhood is one region and opaque: no outline blending, tolerant of a 1px shift.
    ok = (alpha >= 250) & (owner >= 0)
    h, w = owner.shape
    pad = np.pad(owner, 1, constant_values=-2)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            ok &= pad[dy:dy + h, dx:dx + w] == owner
    return ok


# BT.601 full-range: luma apart from chroma, so a recolour can keep the art's shading contrast.
_YCC = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5], [0.5, -0.418688, -0.081312]])
_RGB = np.linalg.inv(_YCC)
# Gain limits on (luma, chroma) spread: the editor adds texture (metal glints, cloth weave) that the flat art does
# not have, and a full std match would turn that into noise. A new hue (grey cloth -> red) is a shift of the median,
# not a gain; at 3x a flat navy cluster blew up to saturated blue on a real edit.
GAIN = ((0.7, 1.4), (0.4, 1.5))


def _stats(ycc: np.ndarray) -> np.ndarray:
    # Median, not mean: the edit never lines up exactly, so outline and background pixels leak into a region's
    # owned pixels; on a real Klein edit this cut the per-region colour error from 28 to 9. The spread stays std:
    # an IQR collapses to ~0 on flat fills and blows the gain up.
    return np.stack([np.median(ycc, 0), ycc.std(0) + 1e-3])


def _main_part(m: np.ndarray) -> np.ndarray:
    """Largest 4-connected piece of a mask. A region seen through a gap (a backpack between arm and body) is often
    painted over there by the editor (the jacket closed), so its smaller pieces would teach the wrong colour."""
    ys, xs = np.nonzero(m)
    if not len(ys):
        return m
    y0, x0 = ys.min(), xs.min()
    sub = m[y0:ys.max() + 1, x0:xs.max() + 1]
    h, w = sub.shape
    label = np.zeros(sub.shape, np.int32)
    flat_m, flat_l = sub.ravel(), label.ravel()
    best, best_n, cur = 0, 0, 0
    for start in np.flatnonzero(flat_m):
        if flat_l[start]:
            continue
        cur += 1
        flat_l[start] = cur
        queue, n = deque([start]), 0
        while queue:
            i = queue.popleft()
            n += 1
            y, x = divmod(i, w)
            for j, ok in ((i - w, y > 0), (i + w, y < h - 1), (i - 1, x > 0), (i + 1, x < w - 1)):
                if ok and flat_m[j] and not flat_l[j]:
                    flat_l[j] = cur
                    queue.append(j)
        if n > best_n:
            best, best_n = cur, n
    out = np.zeros_like(m)
    out[y0:ys.max() + 1, x0:xs.max() + 1] = label == best
    return out


def colour_maps(render: Image.Image, edited: Image.Image, owner: np.ndarray, paths: list[str],
                tint: np.ndarray | None = None) -> tuple[dict[str, np.ndarray], np.ndarray, dict[str, int]]:
    """Per region a (K, 2, 2, 3) map, for each paint colour [[src median, src std], [dst median, dst std]] in YCbCr
    over the pixels the region owns (statistics, not per-pixel fits: the edit need not line up exactly), the
    whole-figure map for regions
    drawn but hidden, and pixels used per region. Both sides are divided by the slot tint: the map is applied to the
    untinted atlas, and the runtime tints the result again."""
    blur = ImageFilter.BoxBlur(1)
    div = np.maximum(tint, 0.05) if tint is not None else 1.0
    src = (np.clip(np.asarray(on_background(render), np.float64) / 255 / div, 0, 1)) @ _YCC.T
    dst = (np.clip(np.asarray(edited.filter(blur), np.float64) / 255 / div, 0, 1)) @ _YCC.T
    mask = _interior(owner, np.asarray(render)[..., 3])
    if mask.sum() < 50:
        raise StepError("op_bad_input", "the setup render has almost no opaque interior to learn colours from")
    whole = np.stack([_stats(src[mask]), _stats(dst[mask])])[None]
    maps, used = {}, {}
    for i, p in enumerate(paths):
        m = mask & (owner == i)
        n = int(m.sum())
        used[p] = n
        if n >= 20:
            part = _main_part(m)
            m = part if part.sum() >= 20 else m
            s_px, d_px = src[m], dst[m]
            lab = _clusters(s_px)
            maps[p] = np.stack([np.stack([_stats(s_px[lab == k]), _stats(d_px[lab == k])])
                                for k in range(lab.max() + 1)])
        else:
            maps[p] = whole
    return maps, whole, used


# Colour clusters weigh luma less: shading moves luma inside one paint colour, a different paint moves chroma.
_FEAT = np.array([0.5, 1.0, 1.0])
MAX_CLUSTERS = 4
MIN_SHARE = 0.03
MERGE = 0.06
SOFT = 0.05


def _clusters(ycc: np.ndarray) -> np.ndarray:
    """Labels 0..K-1 for the paint colours inside one region (a backpack texture with a brown bedroll and a navy
    body needs a map per colour, one shared map drags both toward the edit's average)."""
    f = ycc * _FEAT
    centres = [np.median(f, 0)]
    for _ in range(MAX_CLUSTERS - 1):
        d = np.min([np.linalg.norm(f - c, axis=1) for c in centres], 0)
        centres.append(f[int(d.argmax())])
    c = np.array(centres)
    for _ in range(12):
        lab = np.linalg.norm(f[:, None] - c[None], axis=2).argmin(1)
        c = np.array([f[lab == k].mean(0) if (lab == k).any() else c[k] for k in range(len(c))])
    keep = [k for k in range(len(c)) if (lab == k).mean() >= MIN_SHARE]
    merged: list[int] = []
    for k in sorted(keep, key=lambda k: -(lab == k).sum()):
        if all(np.linalg.norm(c[k] - c[j]) > MERGE for j in merged):
            merged.append(k)
    c = c[merged]
    return np.linalg.norm(f[:, None] - c[None], axis=2).argmin(1)


def _apply(rgb: np.ndarray, m: np.ndarray) -> np.ndarray:
    """`m` is (K, 2, 2, 3): one map per colour cluster, blended by how close each texel is to the cluster's colour."""
    m = m[None] if m.ndim == 3 else m
    ycc = rgb @ _YCC.T
    outs, dist = [], []
    for (mu_s, sd_s), (mu_t, sd_t) in m:
        gain = sd_t / sd_s
        gain[0] = np.clip(gain[0], *GAIN[0])
        gain[1:] = np.clip(gain[1:], *GAIN[1])
        outs.append((ycc - mu_s) * gain + mu_t)
        dist.append(np.linalg.norm((ycc - mu_s) * _FEAT, axis=-1))
    if len(outs) == 1:
        return outs[0] @ _RGB.T
    d = np.stack(dist)
    w = np.exp(-(d - d.min(0)) ** 2 / (2 * SOFT ** 2))
    w /= w.sum(0)
    return np.einsum("k...,k...c->...c", w, np.stack(outs)) @ _RGB.T


def recolor_page(page: Image.Image, regions: list[Any], maps: dict[str, np.ndarray], whole: np.ndarray,
                 pma: bool) -> Image.Image:
    """Regions the render never drew (other skins) keep their colours."""
    arr = np.asarray(page.convert("RGBA"), np.float64) / 255
    rgb, a = arr[..., :3], arr[..., 3:]
    if pma:
        rgb = np.where(a > 0, rgb / np.maximum(a, 1e-6), 0)
    out = rgb.copy()
    for r in regions:
        m = maps.get(r.name)
        if m is None:
            continue
        w, h = (r.height, r.width) if r.degrees in (90, 270) else (r.width, r.height)
        out[r.y:r.y + h, r.x:r.x + w] = _apply(rgb[r.y:r.y + h, r.x:r.x + w], m)
    out = out.clip(0, 1)
    if pma:
        out = out * a
    return Image.fromarray((np.concatenate([out, a], 2) * 255 + 0.5).astype(np.uint8), "RGBA")


def rename_pages(atlas_text: str, names: dict[str, str]) -> str:
    return "\n".join(names.get(line.strip(), line) for line in atlas_text.splitlines()) + "\n"


class SpineSetupRender(OpExecutor):
    """Setup pose on white, the input for an image editor."""
    kind = "op.spine_setup_render"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        _, doc = await read_skeleton(ctx, inputs)
        images = region_images(ctx, inputs)
        img, _, paths, _ = await asyncio.to_thread(setup_render, doc, images,
                                                skin_parts({"skin": params.get("skin", "auto")}, doc),
                                                int(params.get("height", 1024)))
        ref = await _put_png(ctx, on_background(img), "setup.png")
        return StepResult(outputs={"images": [ref]}, meta={"size": list(img.size), "regions": len(paths)})


class SpineSkinVariant(OpExecutor):
    """Per edited render: a recoloured copy of every atlas page, the atlas pointing at them, and a preview."""
    kind = "op.spine_skin_variant"
    version = "7"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        _, doc = await read_skeleton(ctx, inputs)
        images = region_images(ctx, inputs)
        atlas_ref = inputs["atlas"][0]
        atlas_text = await asyncio.to_thread(ctx.blobs.path(atlas_ref.hash).read_text, encoding="utf-8-sig")
        edits = [Image.open(ctx.blobs.path(r.hash)) for r in _images_in(inputs, "edits")]
        parts = skin_parts({"skin": params.get("skin", "auto")}, doc)
        render, owner, paths, tint = await asyncio.to_thread(setup_render, doc, images, parts,
                                                       int(params.get("height", 1024)))
        keep = [w.strip().lower() for w in str(params.get("keep", "eye,mouth")).split(",") if w.strip()]
        stem = (atlas_ref.name or "skeleton").rsplit(".", 1)[0]
        ctx.job_dir.mkdir(parents=True, exist_ok=True)
        # skeleton + atlas + texture (the first variant's pages) make the step exportable to an engine as one Spine
        # asset; every variant stays in atlas/pages for the raw export.
        out: dict[str, list] = {"atlas": [], "pages": [], "previews": [], "report": [],
                                "skeleton": [inputs["skeleton"][0]], "texture": []}
        report = []
        for i, edited in enumerate(edits, 1):
            tag = f"v{i}"
            aligned = align_edit(render, edited)
            maps, whole, used = await asyncio.to_thread(colour_maps, render, aligned, owner, paths, tint)
            maps = {k: v for k, v in maps.items() if not any(w in k.lower() for w in keep)}
            names = {p.name: f"{Path(p.name).stem}_{tag}{Path(p.name).suffix}" for p in images.atlas.pages}
            page_files = []
            for pi, page in enumerate(images.atlas.pages):
                regions = [r for r in images.atlas.every if r.page == pi]
                img = await asyncio.to_thread(recolor_page, Image.open(images.pages[pi]), regions, maps, whole,
                                              page.pma)
                f = ctx.job_dir / names[page.name]
                await asyncio.to_thread(img.save, f)
                page_files.append(f)
                ref = await ctx.blobs.put_file(f, name=f.name, mime="image/png")
                out["pages"].append(ref)
                if i == 1:
                    out["texture"].append(ref)
            text = rename_pages(atlas_text, names)
            atlas_file = ctx.job_dir / f"{stem}_{tag}.atlas"
            atlas_file.write_text(text, encoding="utf-8")
            out["atlas"].append(await ctx.blobs.put_file(atlas_file, name=atlas_file.name, mime="text/plain"))
            variant = rt.RegionImages.from_files(atlas_file, page_files)
            after, _, _, _ = await asyncio.to_thread(setup_render, doc, variant, parts, int(params.get("height", 1024)))
            pair = Image.new("RGB", (render.width * 3, render.height), BG[:3])
            pair.paste(on_background(render), (0, 0))
            pair.paste(aligned, (render.width, 0))
            pair.paste(on_background(after), (render.width * 2, 0))
            out["previews"].append(await _put_png(ctx, pair, f"{stem}_{tag}_preview.png"))
            report.append({"variant": tag, "atlas": atlas_file.name, "pages": list(names.values()),
                           "regions_fitted": sum(1 for n in used.values() if n >= 20),
                           "regions_drawn": len(paths), "regions": len(images.atlas.regions)})
        out["report"].append(await ctx.blobs.put_bytes(json.dumps(report, indent=2).encode(), name="variants.json",
                                                       mime="application/json"))
        return StepResult(outputs=out, meta={"variants": report})


VARIANT_OPS: tuple[type[OpExecutor], ...] = (SpineSetupRender, SpineSkinVariant)
