"""Mesh retexture (#41) shared data + `op.project_bake`: painted orbit views -> one UV albedo texture.

The views step stores the triangle soup (world corners, corner normals, UVs) and the orbit cameras in
retex_views.npz; paint and bake re-rasterise from it, so every step uses one camera model."""
from __future__ import annotations

import asyncio
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..adapters.blender import retex_math as rm
from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor

VIEWS_FILE = "retex_views.npz"
REPORT_FILE = "retexture.json"
REPORT_TYPE = "asset-studio/mesh-retexture"
TEXTURE_SIZES = (512, 1024, 2048, 4096)


@dataclass
class Views:
    pos: np.ndarray
    nrm: np.ndarray
    uv: np.ndarray
    cams: list[rm.Camera]
    info: dict[str, Any]


def save_views(path: Path, pos: np.ndarray, nrm: np.ndarray, uv: np.ndarray, cams: list[rm.Camera],
               info: dict[str, Any]) -> None:
    meta = json.dumps({"cams": [c.to_dict() for c in cams], "info": info})
    with open(path, "wb") as f:
        np.savez_compressed(f, pos=pos, nrm=nrm, uv=uv, meta=np.array(meta))


def load_views(path: Path) -> Views:
    try:
        with np.load(path, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            pos, nrm, uv = z["pos"].astype(np.float64), z["nrm"].astype(np.float64), z["uv"].astype(np.float64)
    except (OSError, ValueError, KeyError) as e:
        raise StepError("op_bad_input", f"not a {VIEWS_FILE}: {e}") from e
    cams = [rm.Camera(c["name"], float(c["azimuth"]), float(c["elevation"]), tuple(c["center"]), float(c["half"]),
                      int(c["size"])) for c in meta["cams"]]
    return Views(pos, nrm, uv, cams, meta.get("info") or {})


async def views_in(ctx: StepContext, inputs: Outputs) -> Views:
    refs = inputs.get("views") or []
    if len(refs) != 1:
        raise StepError("op_no_input", f"needs exactly one {VIEWS_FILE}, got {len(refs)}")
    return await asyncio.to_thread(load_views, ctx.blobs.path(refs[0].hash))


def load_rgb(path: Path, size: int) -> np.ndarray:
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        if rgb.size != (size, size):
            rgb = rgb.resize((size, size), Image.Resampling.LANCZOS)
        return np.asarray(rgb, np.float32) / 255.0


def to_png(a: np.ndarray) -> bytes:
    arr = np.clip(np.round(a * 255), 0, 255).astype(np.uint8) if a.dtype != np.uint8 else a
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def labelled_grid(images: list[tuple[str, Image.Image]], cell: int = 384, cols: int = 2) -> Image.Image:
    rows = max(1, (len(images) + cols - 1) // cols)
    gap, label = 6, 18
    out = Image.new("RGB", (cols * cell + (cols + 1) * gap, rows * (cell + label) + (rows + 1) * gap), (32, 32, 36))
    d = ImageDraw.Draw(out)
    for i, (name, img) in enumerate(images):
        x, y = gap + (i % cols) * (cell + gap), gap + (i // cols) * (cell + label + gap)
        out.paste(img.convert("RGB").resize((cell, cell), Image.Resampling.LANCZOS), (x, y + label))
        d.text((x + 2, y + 3), name, fill=(230, 230, 230))
    return out


def coverage_image(res: rm.BakeResult) -> np.ndarray:
    """UV layout: painted texels show the texture, island texels no view saw are red, outside islands dark."""
    img = np.full(res.texture.shape, 0.12, np.float32)
    img[res.covered] = res.texture[res.covered] * 0.85 + 0.15
    img[res.covered & ~res.painted] = (1.0, 0.1, 0.1)
    return img


def bake(views: Views, images: list[np.ndarray], size: int, power: float, edge_px: int, probe: bool = True
         ) -> tuple[rm.BakeResult, dict[str, Any]]:
    surf = rm.uv_surface(views.pos, views.nrm, views.uv, size)
    acc = rm.Accumulator(surf, power)
    per_view = []
    for cam, img in zip(views.cams, images):
        v = rm.render_view(cam, views.pos, views.nrm, edge_px)
        vis = rm.visibility(surf, v)
        acc.add(vis, img)
        per_view.append({"view": cam.name, "texels": int(vis.sel.size)})
    res = rm.finish_texture(acc)
    visible = rm.visible_unpainted(surf, acc.painted(), views.pos, views.nrm, views.uv) if probe else None
    covered = int(res.covered.sum())
    return res, {"texture": size, "covered_texels": covered, "painted_texels": int(res.painted.sum()),
                 "unpainted_pct": round(visible, 3) if visible is not None else res.texel_unpainted_pct,
                 "texel_unpainted_pct": res.texel_unpainted_pct, "views": per_view, "power": power}


class ProjectBake(OpExecutor):
    """Projects every painted view back onto the UV layout, blends by cos θ^power (edge pixels fade out), fills
    texels no view saw from their UV neighbours (push-pull) and pads islands for mipmaps."""
    kind = "op.project_bake"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        t0 = time.perf_counter()
        views = await views_in(ctx, inputs)
        refs = inputs.get("images") or []
        if len(refs) != len(views.cams):
            raise StepError("op_bad_input", f"expected {len(views.cams)} painted views, got {len(refs)}")
        try:
            size = int(params.get("texture", 2048))
            power = float(params.get("power", 4.0))
            edge_px = int(params.get("edge_px", 6))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        if size not in TEXTURE_SIZES or power <= 0:
            raise StepError("op_bad_params", f"texture must be one of {TEXTURE_SIZES}, power > 0")
        ctx.progress(0, 3, "load")
        images = [await asyncio.to_thread(load_rgb, ctx.blobs.path(r.hash), c.size) for r, c in zip(refs, views.cams)]
        ctx.progress(1, 3, "bake")
        res, stats = await asyncio.to_thread(bake, views, images, size, power, edge_px)
        stem = str(views.info.get("stem") or "mesh")
        ctx.progress(2, 3, "save")
        tex = await ctx.blobs.put_bytes(await asyncio.to_thread(to_png, res.texture), name=f"{stem}.png",
                                        mime="image/png")
        cov = await ctx.blobs.put_bytes(await asyncio.to_thread(to_png, coverage_image(res)),
                                        name=f"{stem}_uv_coverage.png", mime="image/png")
        info = views.info
        warnings = list(info.get("warnings") or [])
        if stats["unpainted_pct"] >= 2.0:
            warnings.append("unpainted_over_2pct")
        doc = {"type": REPORT_TYPE, "stem": stem, **stats, "uv": {k: info.get(k) for k in (
            "uv_mode", "unwrapped", "overlap", "overlap_before", "had_uv")}, "warnings": warnings,
            "views": [c.name for c in views.cams], "texture_hash": tex.hash}
        rep = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=REPORT_FILE,
                                        mime="application/json")
        ctx.progress(3, 3)
        return StepResult(outputs={"texture": [tex], "coverage": [cov], "report": [rep]},
                          meta={"unpainted_pct": stats["unpainted_pct"], "texel_unpainted_pct": res.texel_unpainted_pct,
                                "texture": size, "warnings": warnings,
                                "seconds": round(time.perf_counter() - t0, 2)})


RETEX_OPS = (ProjectBake,)


async def put_rgb(ctx: StepContext, a: np.ndarray, name: str) -> BlobRef:
    return await ctx.blobs.put_bytes(await asyncio.to_thread(to_png, a), name=name, mime="image/png")

