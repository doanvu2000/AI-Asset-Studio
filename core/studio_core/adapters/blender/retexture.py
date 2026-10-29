"""Mesh retexture Blender steps: `blender.views_render` (import any mesh, keep/unwrap UV, orbit views) and
`blender.retex_apply` (baked maps -> one material -> GLB). bpy code lives in retex_bpy.py; rasterising the views
happens here in numpy (retex_math) so paint and bake share the exact camera model."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

import blake3
import numpy as np
from PIL import Image

from ...config import Settings
from ...contracts import Lane, Outputs, StepContext, StepError, StepResult
from . import retex_math as rm
from .client import WORKER_SCRIPT, BlenderResource
from .executor import _single_mesh, call_script, worker_hash

MESH_EXTS = (".glb", ".gltf", ".fbx", ".obj")
MIME_EXT = {"model/gltf-binary": ".glb", "model/gltf+json": ".gltf", "model/obj": ".obj"}
UV_MODES = ("auto", "keep", "new")
MAP_ROLES = ("albedo", "normal", "roughness", "metallic", "ao")


def retex_hash() -> str:
    h = blake3.blake3(worker_hash().encode())
    for name in ("retex_math.py", "retex_bpy.py"):
        h.update(WORKER_SCRIPT.with_name(name).read_bytes())
    return h.hexdigest()


def mesh_ext(name: str | None, mime: str) -> str:
    suffix = Path(name or "").suffix.lower()
    if suffix in MESH_EXTS:
        return suffix
    if mime in MIME_EXT:
        return MIME_EXT[mime]
    raise StepError("blender_bad_input", f"mesh must be {', '.join(MESH_EXTS)}, got {name or mime}")


def mesh_stem(name: str | None) -> str:
    stem = Path(name or "mesh").stem
    for suffix in ("_clean", "_base", "_original", "_retextured"):
        stem = stem.removesuffix(suffix)
    return stem or "mesh"


def shade(nrm_rgb: np.ndarray) -> np.ndarray:
    """Clay look from camera-space normals, for the shape preview Claude and the UI see."""
    n = nrm_rgb.astype(np.float32) / 127.5 - 1.0
    hit = nrm_rgb.any(-1)
    light = np.array([-0.35, 0.5, 0.79])
    s = 0.3 + 0.7 * np.clip(n @ (light / np.linalg.norm(light)), 0, 1)
    out = np.where(hit[..., None], s[..., None] * np.array([0.82, 0.8, 0.76]), 1.0)
    return np.clip(np.round(out * 255), 0, 255).astype(np.uint8)


def render_views(pos: np.ndarray, nrm: np.ndarray, count: int, size: int, edge_px: int = 6
                 ) -> tuple[list[rm.Camera], list[np.ndarray], list[np.ndarray], list[float]]:
    """-> cameras, depth images, normal images, fraction of the frame the object covers per view."""
    cams = rm.cameras(pos, count, size)
    depths, normals, fill = [], [], []
    for cam in cams:
        v = rm.render_view(cam, pos, nrm, edge_px)
        depths.append(rm.depth_image(v))
        normals.append(rm.normal_image(v, nrm))
        fill.append(round(float((v.tri >= 0).mean()), 4))
    return cams, depths, normals, fill


def _png(a: np.ndarray) -> bytes:
    import io

    buf = io.BytesIO()
    Image.fromarray(a).save(buf, "PNG")
    return buf.getvalue()


class BlenderViewsRenderExecutor:
    kind = "blender.views_render"
    lane = Lane.BLENDER
    version = "1"

    def __init__(self, settings: Settings, blender: BlenderResource):
        self.settings = settings
        self.blender = blender
        self.required_resource = blender

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        return retex_hash()

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from ...ops.retexture import VIEWS_FILE, labelled_grid, save_views

        mesh = _single_mesh(inputs)
        try:
            count = int(params.get("views", 6))
            size = int(params.get("res", 1024))
            uv_mode = str(params.get("uv", "auto"))
            max_overlap = float(params.get("max_overlap", 0.02))
        except (TypeError, ValueError) as e:
            raise StepError("blender_bad_params", str(e)) from e
        if count not in rm.VIEW_SETS or uv_mode not in UV_MODES or not 256 <= size <= 2048:
            raise StepError("blender_bad_params", f"views {sorted(rm.VIEW_SETS)}, uv {UV_MODES}, res 256..2048")
        t0 = time.perf_counter()
        ext = mesh_ext(mesh.name, mesh.mime)
        stem = mesh_stem(mesh.name)
        work = ctx.job_dir / "views"
        src = work / f"src{ext}"
        paths = {"original": work / "original.glb", "npz": work / "geom.npz", "report": work / "prepare.json"}

        def stage() -> None:
            work.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.blobs.path(mesh.hash), src)

        await asyncio.to_thread(stage)
        res = await call_script(self.blender, ctx, "import retex_bpy\nretex_bpy.prepare(params)\n",
                                script_params={"src": str(src), "uv": uv_mode, "max_overlap": max_overlap,
                                               **{k: str(v) for k, v in paths.items()}},
                                timeout_s=float(params.get("_timeout_s", 600)), tag="views", unrestricted=True)
        for key in ("original", "npz", "report"):
            if not paths[key].exists():
                raise StepError("blender_no_output", f"views_render did not write {paths[key].name}")
        prep = json.loads(await asyncio.to_thread(paths["report"].read_text, encoding="utf-8"))

        def geometry() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            with np.load(paths["npz"]) as z:
                return z["pos"].astype(np.float64), z["nrm"].astype(np.float64), z["uv"].astype(np.float64)

        pos, nrm, uv = await asyncio.to_thread(geometry)
        ctx.progress(0, 1, "views")
        nrm, flipped = await asyncio.to_thread(rm.orient_normals, pos, nrm)
        cams, depths, normals, fill = await asyncio.to_thread(render_views, pos, nrm, count, size)
        info = {**prep, "stem": stem, "source_ext": ext, "res": size, "fill": fill, "flipped_normals": flipped}
        npz = work / VIEWS_FILE
        await asyncio.to_thread(save_views, npz, pos.astype(np.float32), nrm.astype(np.float32),
                                uv.astype(np.float32), cams, info)
        outputs: Outputs = {
            "mesh": [await ctx.blobs.put_file(Path(res["out"]), name=f"{stem}_base.glb", mime="model/gltf-binary")],
            "original": [await ctx.blobs.put_file(paths["original"], name=f"{stem}_original.glb",
                                                  mime="model/gltf-binary")],
            "views": [await ctx.blobs.put_file(npz, name=VIEWS_FILE, mime="application/octet-stream")],
            "depth": [], "normals": []}
        for cam, d, n in zip(cams, depths, normals):
            outputs["depth"].append(await ctx.blobs.put_bytes(await asyncio.to_thread(_png, d),
                                                              name=f"depth_{cam.name}.png", mime="image/png"))
            outputs["normals"].append(await ctx.blobs.put_bytes(await asyncio.to_thread(_png, n),
                                                                name=f"normal_{cam.name}.png", mime="image/png"))
        clay = [(c.name, Image.fromarray(shade(n))) for c, n in zip(cams, normals)][:4]
        grid = await asyncio.to_thread(labelled_grid, clay, 384)
        outputs["preview"] = [await ctx.blobs.put_bytes(await asyncio.to_thread(_png, np.asarray(grid)),
                                                        name="shape_preview.png", mime="image/png")]
        outputs["report"] = [await ctx.blobs.put_bytes(json.dumps({**info, "views": [c.to_dict() for c in cams]},
                                                                  indent=1).encode(), name="views.json",
                                                       mime="application/json")]
        return StepResult(outputs=outputs, meta={
            "views": [c.name for c in cams], "tris": prep.get("tris"), "uv_mode": uv_mode,
            "unwrapped": prep.get("unwrapped"), "overlap": prep.get("overlap"),
            "overlap_before": prep.get("overlap_before"), "warnings": prep.get("warnings"), "fill": fill,
            "seconds": round(time.perf_counter() - t0, 2), "blender_version": self.blender.version})


def map_roles(names: list[tuple[str, Path]]) -> dict[str, Path]:
    """op.pbr names its maps <stem>_<role>.png; height is not wired into the GLB."""
    out: dict[str, Path] = {}
    for name, path in names:
        stem = Path(name).stem
        for role in MAP_ROLES:
            if stem.endswith(f"_{role}") and role not in out:
                out[role] = path
    return out


class BlenderRetexApplyExecutor:
    kind = "blender.retex_apply"
    lane = Lane.BLENDER
    version = "1"

    def __init__(self, settings: Settings, blender: BlenderResource):
        self.settings = settings
        self.blender = blender
        self.required_resource = blender

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        return retex_hash()

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        mesh = _single_mesh(inputs)
        maps_in = inputs.get("maps") or []
        if not maps_in and inputs.get("texture"):
            maps_in = list(inputs["texture"])
        t0 = time.perf_counter()
        work = ctx.job_dir / "apply"
        base = work / "base.glb"
        stem = mesh_stem(mesh.name)
        staged: list[tuple[str, Path]] = []

        def stage() -> None:
            work.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.blobs.path(mesh.hash), base)
            for i, r in enumerate(maps_in):
                dst = work / f"map{i}_{Path(r.name or 'map.png').name}"
                shutil.copyfile(ctx.blobs.path(r.hash), dst)
                staged.append((r.name or dst.name, dst))

        await asyncio.to_thread(stage)
        roles = map_roles(staged)
        if "albedo" not in roles:
            if len(staged) == 1:
                roles = {"albedo": staged[0][1]}
            else:
                raise StepError("blender_bad_input", "maps need an *_albedo.png (op.pbr output)")
        name = str(params.get("name") or stem)
        res = await call_script(self.blender, ctx, "import retex_bpy\nretex_bpy.apply(params)\n", mesh=base,
                                script_params={"maps": {k: str(v) for k, v in roles.items()}, "name": name},
                                timeout_s=float(params.get("_timeout_s", 300)), tag="apply", unrestricted=True)
        ref = await ctx.blobs.put_file(Path(res["out"]), name=f"{stem}_retextured.glb", mime="model/gltf-binary")
        return StepResult(outputs={"meshes": [ref]}, meta={
            "maps": sorted(roles), "tris": res.get("tris"), "materials": res.get("materials"), "bytes": ref.size,
            "seconds": round(time.perf_counter() - t0, 2), "blender_version": self.blender.version})
