"""`blender.fracture`: closed prop mesh -> intact + Voronoi-fractured copies (GLB + FBX), exploded-view GLB and a
destructible.json report (per-piece volume, mass, triangles, convex hull) for the engine exporters."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import blake3

from ...config import Settings
from ...contracts import Lane, Outputs, StepContext, StepError, StepResult
from . import fracture_math
from .client import WORKER_SCRIPT, BlenderResource
from .executor import _single_mesh, call_script, worker_hash

REPORT_FILE = "destructible.json"
REPORT_TYPE = "asset_studio.destructible"
FBX_MIME = "application/octet-stream"
GLB_MIME = "model/gltf-binary"
# Volume drift above this means a cap failed to close somewhere; the gate still shows the pieces.
VOLUME_TOLERANCE = 0.03
_STEM = re.compile(r"[^A-Za-z0-9_]+")
CODE = "import fracture_bpy, importlib\nimportlib.reload(fracture_bpy)\nfracture_bpy.run(params)\n"


def asset_stem(name: Any) -> str:
    return _STEM.sub("_", str(name or "").strip()).strip("_")[:40] or "prop"


def side_hash() -> str:
    h = blake3.blake3(worker_hash().encode())
    for side in ("fracture_bpy.py", "fracture_math.py"):
        h.update(WORKER_SCRIPT.with_name(side).read_bytes())
    return h.hexdigest()


class BlenderFractureExecutor:
    kind = "blender.fracture"
    lane = Lane.BLENDER
    version = "1"

    def __init__(self, settings: Settings, blender: BlenderResource):
        self.settings = settings
        self.blender = blender
        self.required_resource = blender

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        return side_hash()

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        mesh = _single_mesh(inputs)
        try:
            cfg = fracture_math.normalize_params(params)
        except (TypeError, ValueError) as e:
            raise StepError("blender_bad_params", str(e)) from e
        asset = asset_stem(params.get("asset") or (mesh.name or "prop").rsplit(".", 1)[0].removesuffix("_clean"))
        t0 = time.perf_counter()
        work = ctx.job_dir / "fracture"
        src = work / "in.glb"
        texture = work / "cut.png"
        textures = inputs.get("texture") or []

        def stage() -> None:
            work.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.blobs.path(mesh.hash), src)
            if textures:
                shutil.copyfile(ctx.blobs.path(textures[0].hash), texture)

        await asyncio.to_thread(stage)
        files = {"out_intact": work / f"{asset}_intact.glb", "out_exploded": work / f"{asset}_exploded.glb",
                 "out_intact_fbx": work / f"{asset}_intact.fbx", "out_fractured_fbx": work / f"{asset}_fractured.fbx",
                 "report": work / "report.json"}
        script_params = {**params, **cfg, "asset": asset, "texture": str(texture) if textures else "",
                         **{k: str(v) for k, v in files.items()}}
        script_params.pop("_timeout_s", None)
        res = await call_script(self.blender, ctx, CODE, mesh=src, script_params=script_params,
                                timeout_s=float(params.get("_timeout_s", 600)), tag="fracture", unrestricted=True)
        for key, f in files.items():
            if not f.exists():
                raise StepError("blender_no_output", f"fracture did not write {f.name} ({key})")
        raw = json.loads(await asyncio.to_thread(files["report"].read_text, encoding="utf-8"))
        report = {"type": REPORT_TYPE, "fractured_glb": f"{asset}_fractured.glb", "intact_glb": f"{asset}_intact.glb",
                  "fractured_fbx": f"{asset}_fractured.fbx", "intact_fbx": f"{asset}_intact.fbx", **raw}
        outputs: Outputs = {
            "meshes": [await ctx.blobs.put_file(Path(res["out"]), name=report["fractured_glb"], mime=GLB_MIME),
                       await ctx.blobs.put_file(files["out_intact"], name=report["intact_glb"], mime=GLB_MIME)],
            "exploded": [await ctx.blobs.put_file(files["out_exploded"], name=f"{asset}_exploded.glb", mime=GLB_MIME)],
            "fbx": [await ctx.blobs.put_file(files["out_fractured_fbx"], name=report["fractured_fbx"], mime=FBX_MIME),
                    await ctx.blobs.put_file(files["out_intact_fbx"], name=report["intact_fbx"], mime=FBX_MIME)],
            "report": [await ctx.blobs.put_bytes(json.dumps(report, indent=1).encode(), name=REPORT_FILE,
                                                 mime="application/json")]}
        warnings = []
        if report["volume_error"] > VOLUME_TOLERANCE:
            warnings.append(f"tổng thể tích mảnh lệch {report['volume_error']:.1%} so với mesh gốc")
        if not report["all_closed"]:
            warnings.append("có mảnh chưa kín: " + ", ".join(c["name"] for c in report["chunks"] if not c["closed"]))
        if report["tris_total"] > cfg["max_tris"] * 1.02:
            warnings.append(f"{report['tris_total']} tam giác vượt ngân sách {cfg['max_tris']}")
        return StepResult(outputs=outputs, meta={
            **{k: v for k, v in report.items() if k not in ("type", "chunks", "intact_hull", "params")},
            "pieces": len(report["chunks"]), "pattern": cfg["pattern"], "material": cfg["material"],
            "density": cfg["density"], "warnings": warnings,
            "chunk_tris": [c["tris"] for c in report["chunks"]], "chunk_mass": [c["mass"] for c in report["chunks"]],
            "seconds": round(time.perf_counter() - t0, 2), "blender_version": self.blender.version})
