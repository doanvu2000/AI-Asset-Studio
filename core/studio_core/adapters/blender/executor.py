"""`blender.render_dirs`: GLB -> N orthographic direction renders via the resident worker."""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

import blake3

from ...config import Settings
from ...contracts import Lane, Outputs, StepContext, StepError, StepResult
from ..comfy.server import ComfyResource
from .client import WORKER_SCRIPT, BlenderResource

RENDER_KEYS = ("elevation", "count", "res", "mirror", "engine", "samples", "supersample", "margin", "lighting",
               "key", "fill", "rim", "ambient")


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
    version = "1"

    def __init__(self, settings: Settings, blender: BlenderResource, comfy: ComfyResource | None = None):
        self.settings = settings
        self.blender = blender
        self.comfy = comfy

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        return blake3.blake3(WORKER_SCRIPT.read_bytes()).hexdigest()

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        meshes = inputs.get("mesh", [])
        if len(meshes) != 1:
            raise StepError("blender_bad_input", f"expected exactly 1 mesh, got {len(meshes)}")
        t0 = time.perf_counter()
        glb = ctx.job_dir / "mesh.glb"
        out_dir = ctx.job_dir / "frames"

        def stage() -> None:
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.blobs.path(meshes[0].hash), glb)

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
        return StepResult(outputs={"images": images}, meta={
            "angles": [f["angle"] for f in frames], "ortho_scale": res["ortho_scale"], "engine": res["engine"],
            "res": res["res"], "supersample": res["supersample"], "elevation": res["elevation"],
            "radius": res["radius"], "height": res["height"], "verts": res["verts"],
            "render_s": res["total_s"], "seconds": round(time.perf_counter() - t0, 2),
            "blender_version": self.blender.version})
