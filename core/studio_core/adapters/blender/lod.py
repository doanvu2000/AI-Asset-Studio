"""`blender.lod`: static prop mesh (GLB / FBX / OBJ) -> LOD chain (decimated, UV seams and normals kept), collision shapes
(box / convex hull / voxel-clustered hulls), optional 8-view impostor atlas, and lod.json (triangles, silhouette IoU,
hull / mesh volume, transition heights) for the engine exporters."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

import blake3

from ...config import Settings
from ...contracts import Lane, Outputs, StepContext, StepError, StepResult
from . import lod_math as lm
from .client import WORKER_SCRIPT, BlenderResource
from .executor import _single_mesh, call_script, worker_hash
from .fracture import FBX_MIME, GLB_MIME, asset_stem

REPORT_FILE = "lod.json"
REPORT_TYPE = "asset_studio.lod_chain"
MESH_EXT = (".glb", ".gltf", ".fbx", ".obj")
SIDE_FILES = ("lod_bpy.py", "lod_math.py", "fracture_bpy.py", "fracture_math.py", "icon3d_bpy.py", "toon_bands.py")
IOU_MIN = 0.95
CODE = "import lod_bpy, importlib\nimportlib.reload(lod_bpy)\nlod_bpy.run(params)\n"


def side_hash() -> str:
    h = blake3.blake3(worker_hash().encode())
    for side in SIDE_FILES:
        h.update(WORKER_SCRIPT.with_name(side).read_bytes())
    return h.hexdigest()


def compose_atlas(frames: list[Path], cols: int, out: Path) -> None:
    from PIL import Image

    images = [Image.open(f).convert("RGBA") for f in frames]
    w, h = images[0].size
    rows = -(-len(images) // cols)
    atlas = Image.new("RGBA", (w * cols, h * rows), (0, 0, 0, 0))
    for i, im in enumerate(images):
        atlas.paste(im, ((i % cols) * w, (i // cols) * h))
    atlas.save(out)


def finish_report(raw: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Adds what depends only on the numbers: transition heights (Unity LODGroup) and distances (Godot ranges)."""
    slots = len(raw["lods"]) + (1 if raw.get("impostor") else 0)
    heights = lm.transition_heights(slots, cfg)
    return {"type": REPORT_TYPE, **raw, "transition_heights": heights,
            "distances_m": lm.lod_distances(raw["size"], heights)}


def report_warnings(report: dict[str, Any]) -> list[str]:
    out = list(report.get("warnings") or [])
    lods = report["lods"]
    if len(lods) == 1:
        out.append(f"mesh chỉ {lods[0]['tris']} tam giác hoặc không giảm thêm được: chỉ có LOD0")
    for s in report.get("skipped") or []:
        out.append(f"bỏ LOD {s['target']} tam giác: {s['reason']}")
    for e in lods[1:]:
        if e["iou"]["min"] < IOU_MIN:
            out.append(f"LOD{e['level']} lệch hình bóng (IoU {e['iou']['min']:.3f} < {IOU_MIN})")
    ratio = (report.get("collision") or {}).get("volume_ratio")
    if ratio is not None and ratio > lm.HULL_RATIO_LIMIT:
        out.append(f"collision {report['collision']['mode']} dày hơn mesh {ratio:.2f}x (> {lm.HULL_RATIO_LIMIT})")
    return out


class BlenderLodExecutor:
    kind = "blender.lod"
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
            cfg = lm.normalize_params(params)
        except (TypeError, ValueError) as e:
            raise StepError("blender_bad_params", str(e)) from e
        asset = asset_stem(params.get("asset") or (mesh.name or "prop").rsplit(".", 1)[0].removesuffix("_clean"))
        ext = Path(mesh.name or "m.glb").suffix.lower()
        t0 = time.perf_counter()
        work = ctx.job_dir / "lod"
        src = work / f"in{ext if ext in MESH_EXT else '.glb'}"

        def stage() -> None:
            work.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.blobs.path(mesh.hash), src)

        await asyncio.to_thread(stage)
        report_path = work / "report.json"
        script_params = {**params, **cfg, "asset": asset, "src": str(src), "out_dir": str(work),
                         "report": str(report_path)}
        script_params.pop("_timeout_s", None)
        await call_script(self.blender, ctx, CODE, script_params=script_params,
                          timeout_s=float(params.get("_timeout_s", 900)), tag="lod_run", unrestricted=True)
        if not report_path.exists():
            raise StepError("blender_no_output", "lod did not write its report")
        raw = json.loads(await asyncio.to_thread(report_path.read_text, encoding="utf-8"))
        report = finish_report(raw, cfg)
        meshes, fbx, collision, impostor = [], [], [], []
        for e in report["lods"]:
            for key, bucket, mime in (("glb", meshes, GLB_MIME), ("fbx", fbx, FBX_MIME)):
                f = work / e[key]
                if not f.exists():
                    raise StepError("blender_no_output", f"lod did not write {f.name}")
                bucket.append(await ctx.blobs.put_file(f, name=f.name, mime=mime))
        if report["collision"]["mode"] != "none":
            for ext_, bucket, mime in ((".glb", collision, GLB_MIME), (".fbx", fbx, FBX_MIME)):
                f = work / f"{asset}_collision{ext_}"
                if not f.exists():
                    raise StepError("blender_no_output", f"lod did not write {f.name}")
                bucket.append(await ctx.blobs.put_file(f, name=f.name, mime=mime))
            report["collision_glb"], report["collision_fbx"] = f"{asset}_collision.glb", f"{asset}_collision.fbx"
        if report.get("impostor"):
            atlas = work / f"{asset}_impostor.png"
            frames = [work / n for n in report["impostor"]["frames"]]
            await asyncio.to_thread(compose_atlas, frames, report["impostor"]["cols"], atlas)
            impostor.append(await ctx.blobs.put_file(atlas, name=atlas.name, mime="image/png"))
            report["impostor"]["atlas"] = atlas.name
        warnings = report_warnings(report)
        report["warnings"] = warnings
        outputs: Outputs = {
            "meshes": meshes, "fbx": fbx, "collision": collision, "impostor": impostor,
            "report": [await ctx.blobs.put_bytes(json.dumps(report, indent=1).encode(), name=REPORT_FILE,
                                                 mime="application/json")]}
        col = report["collision"]
        return StepResult(outputs=outputs, meta={
            "asset": asset, "preset": cfg["preset"], "source_tris": report["source_tris"],
            "lod_tris": [e["tris"] for e in report["lods"]], "lod_iou": [e["iou"]["min"] for e in report["lods"]],
            "collision": col["mode"], "hull_ratio": col.get("volume_ratio"), "impostor": bool(impostor),
            "warnings": warnings, "seconds": round(time.perf_counter() - t0, 2),
            "blender_version": self.blender.version})
