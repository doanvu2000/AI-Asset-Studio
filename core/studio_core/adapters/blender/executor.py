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
from .clips import clip_order_key, expected_frames, plan_clips
from .client import WORKER_SCRIPT, BlenderResource

RENDER_KEYS = ("elevation", "count", "res", "mirror", "engine", "samples", "supersample", "margin", "lighting",
               "key", "fill", "rim", "ambient")


def worker_hash() -> str:
    h = blake3.blake3(WORKER_SCRIPT.read_bytes())
    # Frozen builds point cleanup_math.__file__ at a .pyc that does not exist on disk.
    h.update(WORKER_SCRIPT.with_name("cleanup_math.py").read_bytes())
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


def _alpha_from(normal: Path, color: Path) -> None:
    """The normal pass renders with an override material, so take the coverage of the real colour frame."""
    from PIL import Image
    with Image.open(color) as c, Image.open(normal) as n:
        out = n.convert("RGBA")
        out.putalpha(c.convert("RGBA").getchannel("A"))
    out.save(normal)


async def put_normals(ctx: StepContext, frames: list[dict[str, Any]], names: list[str]) -> list[BlobRef]:
    out = []
    for f, name in zip(frames, names):
        if not f.get("normal"):
            raise StepError("blender_no_output", f"normal pass missing for {Path(f['file']).name}")
        await asyncio.to_thread(_alpha_from, Path(f["normal"]), Path(f["file"]))
        out.append(await ctx.blobs.put_file(Path(f["normal"]), name=name, mime="image/png"))
    return out


class BlenderRenderDirsExecutor:
    kind = "blender.render_dirs"
    lane = Lane.BLENDER
    version = "3"

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
        normals = bool(params.get("normals"))
        res = await self.blender.call("render_dirs", {**p, "glb": str(glb), "out_dir": str(out_dir),
                                                      "normals": normals},
                                      on_event=on_event, timeout=float(params.get("_timeout_s", 900)))
        frames = sorted(res["frames"], key=lambda f: f["angle"])
        images = []
        for f in frames:
            images.append(await ctx.blobs.put_file(Path(f["file"]), name=f"dir_{round(f['angle']):03d}.png",
                                                   mime="image/png"))
        normal_frames = await put_normals(ctx, frames, [f"dir_{round(f['angle']):03d}_n.png" for f in frames])             if normals else []
        pivot = [round(x, 2) for x in res["pivot"]]
        info = {"pivot": pivot, "width": res["res"], "height": res["res"], "angles": [f["angle"] for f in frames],
                "elevation": res["elevation"], "ortho_scale": res["ortho_scale"],
                "units_per_px": round(res["ortho_scale"] / res["res"], 6)}
        meta_ref = await ctx.blobs.put_bytes(json.dumps(info, indent=2).encode(), name="render.json",
                                             mime="application/json")
        return StepResult(outputs={"images": images, "normal_frames": normal_frames, "meta": [meta_ref]}, meta={
            "pivot": pivot, "normals": normals,
            "angles": [f["angle"] for f in frames], "ortho_scale": res["ortho_scale"], "engine": res["engine"],
            "res": res["res"], "supersample": res["supersample"], "elevation": res["elevation"],
            "radius": res["radius"], "height": res["height"], "verts": res["verts"],
            "render_s": res["total_s"], "seconds": round(time.perf_counter() - t0, 2),
            "blender_version": self.blender.version})


async def _json_in(ctx: StepContext, inputs: Outputs, port: str) -> dict[str, Any]:
    refs = inputs.get(port, [])
    if len(refs) != 1:
        raise StepError("blender_bad_input", f"expected exactly 1 '{port}' json, got {len(refs)}")
    try:
        return json.loads(await asyncio.to_thread(ctx.blobs.path(refs[0].hash).read_bytes))
    except ValueError as e:
        raise StepError("blender_bad_input", f"'{port}' is not JSON: {e}") from e


class BlenderAnimateDirsExecutor:
    """Rigs the mesh from 2D joints marked on a front render (`front` = that render's render.json) and renders the
    procedural clips in `anims` (default walk only): images are clip-major, then direction, then frame, so op.sheet
    lays each clip out as a block of direction rows (anim.json `clips`)."""
    kind = "blender.animate_dirs"
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
        try:
            clips = plan_clips(params)
        except (TypeError, ValueError) as e:
            raise StepError("blender_bad_params", str(e)) from e
        normals = bool(params.get("normals"))
        t0 = time.perf_counter()
        glb = ctx.job_dir / "mesh.glb"
        out_dir = ctx.job_dir / "anim"

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
        size = joints_doc.get("size") or [front["width"], front["height"]]
        res = await self.blender.call("animate_dirs", {
            **p, "glb": str(glb), "out_dir": str(out_dir), "clips": clips, "normals": normals,
            "frames": clips[0]["frames"],
            "joints": joints, "image_w": float(size[0]), "render_res": float(front["width"]),
            "pivot": front["pivot"], "ortho_scale": front["ortho_scale"]},
            on_event=on_event, timeout=float(params.get("_timeout_s", 1800)))
        frames = sorted(res["frames"], key=clip_order_key(clips))
        angles = sorted({f["angle"] for f in frames})
        want = expected_frames(clips, len(angles))
        if len(frames) != want or len(angles) != int(params.get("count", 8)):
            raise StepError("blender_bad_output", f"worker rendered {len(frames)} frames over {len(angles)} angles, "
                                                  f"expected {want} for clips {[c['name'] for c in clips]}")
        legacy = [c["name"] for c in clips] == ["walk"]
        names = [f"{'' if legacy else f.get('clip', 'walk') + '_'}dir_{round(f['angle']):03d}_f{f['frame']:02d}"
                 for f in frames]
        images = []
        for f, name in zip(frames, names):
            images.append(await ctx.blobs.put_file(Path(f["file"]), name=f"{name}.png", mime="image/png"))
        normal_frames = await put_normals(ctx, frames, [f"{n}_n.png" for n in names]) if normals else []
        pivot = [round(x, 2) for x in res["pivot"]]
        info = {"pivot": pivot, "width": res["res"], "height": res["res"], "angles": angles,
                "frames_per_dir": res["frames_per_dir"], "clips": clips, "elevation": res["elevation"],
                "ortho_scale": res["ortho_scale"], "units_per_px": round(res["ortho_scale"] / res["res"], 6)}
        meta_ref = await ctx.blobs.put_bytes(json.dumps(info, indent=2).encode(), name="anim.json",
                                             mime="application/json")
        return StepResult(outputs={"images": images, "normal_frames": normal_frames, "meta": [meta_ref]}, meta={
            "normals": normals,
            **info, "weights": res["weights"], "engine": res["engine"], "render_s": res["total_s"],
            "seconds": round(time.perf_counter() - t0, 2), "blender_version": self.blender.version})


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


SCRIPT_ERRORS = {"blender_script_error", "blender_script_timeout"}


async def put_frames(ctx: StepContext, frames: list[dict[str, Any]]) -> list[BlobRef]:
    out = []
    for f in sorted(frames, key=lambda f: f["angle"]):
        out.append(await ctx.blobs.put_file(Path(f["file"]), name=f"dir_{round(f['angle']):03d}.png",
                                            mime="image/png"))
    return out


async def call_script(blender: BlenderResource, ctx: StepContext, code: str, *, mesh: Path | None = None,
                      script_params: dict[str, Any] | None = None, render: dict[str, Any] | None = None,
                      timeout_s: float = 120, tag: str = "script", unrestricted: bool = False) -> dict[str, Any]:
    """Runs `code` in the worker; the result's `out` is the exported GLB, `render.frames` the optional views."""
    work = ctx.job_dir / tag
    out = work / "model.glb"
    await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
    req: dict[str, Any] = {"code": code, "params": script_params or {}, "out": str(out), "timeout_s": timeout_s,
                           "unrestricted": unrestricted}
    if mesh is not None:
        req["glb_in"] = str(mesh)
    if render is not None:
        req["render"] = render_params(render)
        req["render_dir"] = str(work / "frames")

    def on_event(ev: dict[str, Any]) -> None:
        ctx.progress(ev.get("value", 0), ev.get("max", 1), ev.get("label"))

    try:
        res = await blender.call("script", req, on_event=on_event, timeout=timeout_s + 300)
    except StepError as e:
        if e.code in SCRIPT_ERRORS:
            trace = (e.detail or {}).get("trace") or ""
            raise StepError(e.code, f"{e.message}\n{trace}".strip()[-4000:], retryable=False,
                            detail={"trace": trace}) from e
        raise
    if not out.exists():
        raise StepError("blender_no_output", "script finished without writing a GLB")
    return res


class BlenderScriptExecutor:
    """bpy code from `params.code` (or a text blob on input `code`) runs in a fresh empty scene of the resident
    worker, optionally after importing input `mesh`; the scene is exported as GLB and optionally rendered."""

    kind = "blender.script"
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
        code = str(params.get("code") or "")
        if not code.strip() and inputs.get("code"):
            code = await asyncio.to_thread(ctx.blobs.path(inputs["code"][0].hash).read_text, encoding="utf-8")
        if not code.strip():
            raise StepError("blender_bad_params", "params.code or input 'code' is required")
        meshes = inputs.get("mesh", [])
        if len(meshes) > 1:
            raise StepError("blender_bad_input", f"expected at most 1 mesh, got {len(meshes)}")
        t0 = time.perf_counter()
        src = None
        if meshes:
            src = ctx.job_dir / "in.glb"

            def stage() -> None:
                ctx.job_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ctx.blobs.path(meshes[0].hash), src)

            await asyncio.to_thread(stage)
        render = params.get("render")
        res = await call_script(self.blender, ctx, code, mesh=src, script_params=params.get("params") or {},
                                render=render if isinstance(render, dict) else None,
                                timeout_s=float(params.get("timeout_s", 120)),
                                unrestricted=bool(params.get("unrestricted")))
        name = str(params.get("name") or "script")
        outputs: Outputs = {"meshes": [await ctx.blobs.put_file(Path(res["out"]), name=f"{name}.glb",
                                                                mime="model/gltf-binary")]}
        rinfo = res.get("render")
        if rinfo:
            outputs["images"] = await put_frames(ctx, rinfo["frames"])
        report = {k: v for k, v in res.items() if k not in ("out", "render")}
        if rinfo:
            report["render"] = {k: v for k, v in rinfo.items() if k != "frames"}
        outputs["report"] = [await ctx.blobs.put_bytes(json.dumps(report, indent=1).encode(), name="report.json",
                                                       mime="application/json")]
        return StepResult(outputs=outputs, meta={**{k: v for k, v in report.items() if k != "stdout"},
                                                 "seconds": round(time.perf_counter() - t0, 2),
                                                 "blender_version": self.blender.version})
