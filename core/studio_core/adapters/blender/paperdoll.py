"""`blender.paperdoll`: the animate_dirs character plus equipment layers (one per slot) rendered in the same scene with
the body as a holdout, and per clip-frame socket bones for the core to project (ops/paperdoll.py)."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

from ...config import Settings
from ...contracts import BlobRef, Lane, Outputs, StepContext, StepError, StepResult
from ..comfy.server import ComfyResource
from . import paperdoll_math as pdm
from .client import BlenderResource
from .clips import clip_order_key, expected_frames, plan_clips
from .executor import _json_in, _single_mesh, render_params, worker_hash

RIG_FILE = "paperdoll_rig.json"


def frame_name(f: dict[str, Any]) -> str:
    return f"{f['clip']}_dir_{round(f['angle']):03d}_f{f['frame']:02d}"


class BlenderPaperdollExecutor:
    kind = "blender.paperdoll"
    lane = Lane.BLENDER
    version = "1"

    def __init__(self, settings: Settings, blender: BlenderResource, comfy: ComfyResource | None = None):
        self.settings = settings
        self.blender = blender
        self.required_resource = blender
        self.comfy = comfy

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        try:
            plan = json.dumps(plan_clips(params), sort_keys=True)
        except (TypeError, ValueError):
            plan = ""
        return f"{worker_hash()}|{plan}"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        mesh = _single_mesh(inputs)
        joints_doc = await _json_in(ctx, inputs, "joints")
        front = await _json_in(ctx, inputs, "front")
        joints = joints_doc.get("joints", joints_doc)
        if not isinstance(joints, dict) or len(joints) < 19:
            raise StepError("blender_bad_input", "joints json needs the 19-joint schema")
        item_meshes: dict[str, BlobRef] = {}
        for slot in pdm.SLOTS:
            refs = inputs.get(slot) or []
            if len(refs) > 1:
                raise StepError("blender_bad_input", f"expected at most 1 {slot} mesh, got {len(refs)}")
            if refs:
                item_meshes[slot] = refs[0]
        try:
            clips = plan_clips(params)
            equip = pdm.plan_equip(params, set(item_meshes))
        except (TypeError, ValueError) as e:
            raise StepError("blender_bad_params", str(e)) from e
        t0 = time.perf_counter()
        glb, out_dir = ctx.job_dir / "mesh.glb", ctx.job_dir / "doll"

        def stage() -> None:
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.blobs.path(mesh.hash), glb)
            for e in equip:
                if e["mesh"]:
                    e["glb"] = str(ctx.job_dir / f"{e['name']}.glb")
                    shutil.copyfile(ctx.blobs.path(item_meshes[e["slot"]].hash), e["glb"])

        await asyncio.to_thread(stage)
        p = render_params(params)
        if p["engine"] == "CYCLES" and self.comfy is not None:
            await self.comfy.free()

        def on_event(ev: dict[str, Any]) -> None:
            ctx.progress(ev.get("value", 0), ev.get("max", 1), ev.get("label"))

        ctx.progress(0, 1, "blender")
        size = joints_doc.get("size") or [front["width"], front["height"]]
        res = await self.blender.call("paperdoll_dirs", {
            **p, "glb": str(glb), "out_dir": str(out_dir), "clips": clips, "frames": clips[0]["frames"],
            "equip": equip, "joints": joints, "image_w": float(size[0]), "render_res": float(front["width"]),
            "pivot": front["pivot"], "ortho_scale": front["ortho_scale"]},
            on_event=on_event, timeout=float(params.get("_timeout_s", 2400)))
        key = clip_order_key(clips)
        frames = sorted(res["frames"], key=key)
        angles = sorted({f["angle"] for f in frames})
        want = expected_frames(clips, len(angles))
        names = [e["name"] for e in res["equip"]]
        layers = sorted(res["layers"], key=lambda f: (names.index(f["layer"]), *key(f)))
        if len(frames) != want or len(angles) != int(params.get("count", 8)) or len(layers) != want * len(names):
            raise StepError("blender_bad_output", f"worker rendered {len(frames)} frames + {len(layers)} layer frames "
                                                  f"over {len(angles)} angles, expected {want} each")
        images = [await ctx.blobs.put_file(Path(f["file"]), name=f"{frame_name(f)}.png", mime="image/png")
                  for f in frames]
        layer_refs = [await ctx.blobs.put_file(Path(f["file"]), name=f"{f['layer']}_{frame_name(f)}.png",
                                               mime="image/png") for f in layers]
        pivot = [round(x, 2) for x in res["pivot"]]
        info = {"pivot": pivot, "width": res["res"], "height": res["res"], "angles": angles,
                "frames_per_dir": res["frames_per_dir"], "clips": clips, "elevation": res["elevation"],
                "ortho_scale": res["ortho_scale"], "units_per_px": round(res["ortho_scale"] / res["res"], 6),
                "pivot_exact": res["pivot"], "body_height": res["height"], "equip": res["equip"],
                "poses": res["poses"]}
        meta_ref = await ctx.blobs.put_bytes(json.dumps(info).encode(), name=RIG_FILE, mime="application/json")
        return StepResult(outputs={"images": images, "layers": layer_refs, "meta": [meta_ref]}, meta={
            **{k: v for k, v in info.items() if k != "poses"}, "weights": res["weights"], "engine": res["engine"],
            "render_s": res["total_s"], "seconds": round(time.perf_counter() - t0, 2),
            "blender_version": self.blender.version})
