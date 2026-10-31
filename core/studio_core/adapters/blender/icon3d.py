"""`blender.icon_render`: a set of meshes -> one ortho icon render each (shared angle preset, camera-tied 3-point
lights, fixed exposure, optional toon); `blender.iso_render`: one mesh -> 2:1 isometric renders in 4 quarter turns
at the pixel scale of an iso tile, with the footprint anchor for engine tiles. Both run icon3d_bpy in the worker."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

import blake3

from ...config import Settings
from ...contracts import BlobRef, Lane, Outputs, StepContext, StepError, StepResult
from .client import WORKER_SCRIPT, BlenderResource
from .executor import call_script, worker_hash

CODE = "import icon3d_bpy, importlib\nimportlib.reload(icon3d_bpy)\nicon3d_bpy.run(params)\n"
MESH_EXT = (".glb", ".gltf", ".fbx", ".obj")
ANGLES = ("iso34", "front", "top")
ISO_TILE_W = (64, 128, 256)


def side_hash() -> str:
    h = blake3.blake3(worker_hash().encode())
    h.update(WORKER_SCRIPT.with_name("icon3d_bpy.py").read_bytes())
    return h.hexdigest()


def mesh_stem(name: str | None, i: int) -> str:
    stem = Path(name or "").stem or f"mesh_{i + 1:02d}"
    if stem in ("mesh", "model", "cleaned") or stem.startswith("mesh_cleaned"):
        stem = f"{stem}_{i + 1:02d}"
    return stem


def unique_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for n in names:
        base, k, cand = n, 2, n
        while cand.lower() in seen:
            cand, k = f"{base}_{k}", k + 1
        seen.add(cand.lower())
        out.append(cand)
    return out


def folder_meshes(folder: str, limit: int = 256) -> list[Path]:
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise StepError("blender_bad_input", f"mesh folder not found: {folder}")
    found = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in MESH_EXT)
    return found[:limit]


async def _stage(ctx: StepContext, refs: list[BlobRef], extra: list[Path], work: Path) -> list[dict[str, Any]]:
    names = unique_names([mesh_stem(r.name, i) for i, r in enumerate(refs)]
                         + [p.stem for p in extra])
    srcs = [(ctx.blobs.path(r.hash), Path(r.name or "m.glb").suffix.lower() or ".glb") for r in refs]
    srcs += [(p, p.suffix.lower()) for p in extra]

    def copy() -> list[dict[str, Any]]:
        work.mkdir(parents=True, exist_ok=True)
        items = []
        for i, ((src, ext), name) in enumerate(zip(srcs, names)):
            ext = ext if ext in MESH_EXT else ".glb"
            dst = work / f"in_{i:03d}{ext}"
            shutil.copyfile(src, dst)
            items.append({"name": name, "path": str(dst)})
        return items

    return await asyncio.to_thread(copy)


class _Base:
    lane = Lane.BLENDER
    version = "1"

    def __init__(self, settings: Settings, blender: BlenderResource):
        self.settings = settings
        self.blender = blender
        self.required_resource = blender

    def cacheable(self, params: dict[str, Any]) -> bool:
        return not params.get("folder")

    def cache_material(self, params: dict[str, Any]) -> str:
        return side_hash()


class BlenderIconRenderExecutor(_Base):
    kind = "blender.icon_render"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        angle = str(params.get("angle") or "iso34")
        if angle not in ANGLES:
            raise StepError("blender_bad_params", f"angle must be one of {', '.join(ANGLES)}")
        refs = list(inputs.get("mesh") or [])
        extra = await asyncio.to_thread(folder_meshes, str(params["folder"])) if params.get("folder") else []
        if not refs and not extra:
            raise StepError("blender_bad_input", "no meshes (drop GLB/FBX/OBJ files or give a folder)")
        if len(refs) + len(extra) > 256:
            raise StepError("blender_bad_input", "at most 256 meshes per run")
        t0 = time.perf_counter()
        work = ctx.job_dir / "icons"
        items = await _stage(ctx, refs, extra, work / "in")
        size = int(params.get("size", 256))
        res = int(params.get("res") or max(256, min(1024, size * 2)))
        report_path = work / "report.json"
        sp = {"mode": "icons", "meshes": items, "angle": angle, "res": res, "out_dir": str(work / "renders"),
              "fill": float(params.get("fill", 0.86)), "toon": bool(params.get("toon")),
              "toon_bands": int(params.get("toon_bands", 3)), "report": str(report_path)}
        await call_script(self.blender, ctx, CODE, script_params=sp, tag="icon3d", unrestricted=True,
                          timeout_s=float(params.get("_timeout_s", 60 + 30 * len(items))))
        if not report_path.exists():
            raise StepError("blender_no_output", "icon render did not write its report")
        rep = json.loads(await asyncio.to_thread(report_path.read_text, encoding="utf-8"))
        images = []
        for e in rep["icons"]:
            f = Path(e["file"])
            if not f.exists():
                raise StepError("blender_no_output", f"missing render for {e['name']}")
            images.append(await ctx.blobs.put_file(f, name=f"{e['name']}.png", mime="image/png"))
            e["file"] = f"{e['name']}.png"
        meta_ref = await ctx.blobs.put_bytes(json.dumps(rep, indent=1).encode(), name="icon_render.json",
                                             mime="application/json")
        return StepResult(outputs={"images": images, "meta": [meta_ref]}, meta={
            "icons": len(images), "angle": angle, "res": res, "toon": bool(params.get("toon")),
            "render_s": rep.get("total_s"), "seconds": round(time.perf_counter() - t0, 2),
            "blender_version": self.blender.version})


class BlenderIsoRenderExecutor(_Base):
    kind = "blender.iso_render"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("mesh") or []
        if len(refs) != 1:
            raise StepError("blender_bad_input", f"expected exactly 1 mesh, got {len(refs)}")
        tile_w = int(params.get("tile_w", 128))
        cells = int(params.get("cells", 1))
        if tile_w not in ISO_TILE_W or not 1 <= cells <= 4:
            raise StepError("blender_bad_params", f"tile_w must be one of {ISO_TILE_W}, cells 1..4")
        t0 = time.perf_counter()
        work = ctx.job_dir / "iso"
        items = await _stage(ctx, list(refs), [], work / "in")
        report_path = work / "report.json"
        sp = {"mode": "iso", "mesh": items[0]["path"], "tile_w": tile_w, "cells": cells,
              "fill": float(params.get("fill", 0.9)), "scale": float(params.get("scale") or 0),
              "supersample": int(params.get("supersample", 2)), "out_dir": str(work / "renders"),
              "report": str(report_path)}
        await call_script(self.blender, ctx, CODE, script_params=sp, tag="iso", unrestricted=True,
                          timeout_s=float(params.get("_timeout_s", 300)))
        if not report_path.exists():
            raise StepError("blender_no_output", "iso render did not write its report")
        rep = json.loads(await asyncio.to_thread(report_path.read_text, encoding="utf-8"))
        images = []
        for fr in rep["frames"]:
            f = Path(fr["file"])
            if not f.exists():
                raise StepError("blender_no_output", f"missing iso render {fr['dir']}")
            images.append(await ctx.blobs.put_file(f, name=f"iso_{fr['dir']}.png", mime="image/png"))
            fr["file"] = f"iso_{fr['dir']}.png"
        rep["name"] = items[0]["name"]
        meta_ref = await ctx.blobs.put_bytes(json.dumps(rep, indent=1).encode(), name="iso_render.json",
                                             mime="application/json")
        return StepResult(outputs={"images": images, "meta": [meta_ref]}, meta={
            "tile_w": tile_w, "cells": cells, "canvas": rep["canvas"], "height_px": rep.get("height_px"),
            "render_s": rep.get("total_s"), "seconds": round(time.perf_counter() - t0, 2),
            "blender_version": self.blender.version})
