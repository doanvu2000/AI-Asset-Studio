"""Blender worker executors: `blender.render_dirs` (GLB -> N ortho direction renders), `blender.cleanup` (GLB -> GLB)."""
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
from ..comfy.server import ComfyResource
from . import cleanup_math
from .client import WORKER_SCRIPT, BlenderResource

RENDER_KEYS = ("elevation", "count", "res", "mirror", "engine", "samples", "supersample", "margin", "lighting",
               "key", "fill", "rim", "ambient")


def worker_hash() -> str:
    h = blake3.blake3(WORKER_SCRIPT.read_bytes())
    h.update(Path(cleanup_math.__file__).read_bytes())
    return h.hexdigest()


def _single_mesh(inputs: Outputs) -> BlobRef:
    meshes = inputs.get("mesh", [])
    if len(meshes) != 1:
        raise StepError("blender_bad_input", f"expected exactly 1 mesh, got {len(meshes)}")
    return meshes[0]


def render_params(params: dict[str, Any]) -> dict[str, Any]:
    p = {k: params[k] for k in RENDER_KEYS if k in params}
    p.setdefault("engine", "BLENDER_EEVEE")
    p["engine"] = str(p["engine"]).upper()
    if p["engine"] in ("EEVEE", "BLENDER_EEVEE_NEXT"):
        p["engine"] = "BLENDER_EEVEE"
    return p


class BlenderRenderDirsExecutor:
    kind = "blender.render_dirs"
    lane = Lane.BLENDER
    version = "2"

    def __init__(self, settings: Settings, blender: BlenderResource, comfy: ComfyResource | None = None):
        self.settings = settings
        self.blender = blender
        self.required_resource = blender
        self.comfy = comfy

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        return worker_hash()

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        mesh = _single_mesh(inputs)
        t0 = time.perf_counter()
        glb = ctx.job_dir / "mesh.glb"
        out_dir = ctx.job_dir / "frames"

        def stage() -> None:
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.blobs.path(mesh.hash), glb)

        await asyncio.to_thread(stage)
        p = render_params(params)
        if p["engine"] == "CYCLES" and self.comfy is not None:
            await self.comfy.free()

        def on_event(ev: dict[str, Any]) -> None:
            ctx.progress(ev.get("value", 0), ev.get("max", 1), ev.get("label"))

        ctx.progress(0, 1, "blender")
        res = await self.blender.call("render_dirs", {**p, "glb": str(glb), "out_dir": str(out_dir)},
                                      on_event=on_event, timeout=float(params.get("_timeout_s", 900)))
        frames = sorted(res["frames"], key=lambda f: f["angle"])
        images = []
        for f in frames:
            images.append(await ctx.blobs.put_file(Path(f["file"]), name=f"dir_{round(f['angle']):03d}.png",
                                                   mime="image/png"))
        pivot = [round(x, 2) for x in res["pivot"]]
        info = {"pivot": pivot, "width": res["res"], "height": res["res"], "angles": [f["angle"] for f in frames],
                "elevation": res["elevation"], "ortho_scale": res["ortho_scale"],
                "units_per_px": round(res["ortho_scale"] / res["res"], 6)}
        meta_ref = await ctx.blobs.put_bytes(json.dumps(info, indent=2).encode(), name="render.json",
                                             mime="application/json")
        return StepResult(outputs={"images": images, "meta": [meta_ref]}, meta={
            "pivot": pivot,
            "angles": [f["angle"] for f in frames], "ortho_scale": res["ortho_scale"], "engine": res["engine"],
            "res": res["res"], "supersample": res["supersample"], "elevation": res["elevation"],
            "radius": res["radius"], "height": res["height"], "verts": res["verts"],
            "render_s": res["total_s"], "seconds": round(time.perf_counter() - t0, 2),
            "blender_version": self.blender.version})


class BlenderCleanupExecutor:
    kind = "blender.cleanup"
    lane = Lane.BLENDER
    version = "1"

    def __init__(self, settings: Settings, blender: BlenderResource):
        self.settings = settings
        self.blender = blender
        self.required_resource = blender

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        return worker_hash()

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        mesh = _single_mesh(inputs)
        try:
            p = cleanup_math.normalize_params(params)
        except (TypeError, ValueError) as e:
            raise StepError("blender_bad_params", str(e)) from e
        t0 = time.perf_counter()
        src = ctx.job_dir / "in.glb"
        out = ctx.job_dir / "clean.glb"

        def stage() -> None:
            ctx.job_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.blobs.path(mesh.hash), src)

        await asyncio.to_thread(stage)

        def on_event(ev: dict[str, Any]) -> None:
            ctx.progress(ev.get("value", 0), ev.get("max", 1), ev.get("label"))

        ctx.progress(0, 1, "blender")
        res = await self.blender.call("cleanup", {**p, "glb": str(src), "out": str(out)}, on_event=on_event,
                                      timeout=float(params.get("_timeout_s", 600)))
        if not out.exists():
            raise StepError("blender_no_output", "cleanup finished without writing a GLB")
        stem = (mesh.name or "mesh").rsplit(".", 1)[0]
        ref = await ctx.blobs.put_file(out, name=f"{stem}_clean.glb", mime="model/gltf-binary")
        meta = {k: v for k, v in res.items() if k != "out"}
        return StepResult(outputs={"meshes": [ref]}, meta={
            **meta, "params": p, "bytes": ref.size, "seconds": round(time.perf_counter() - t0, 2),
            "blender_version": self.blender.version})
