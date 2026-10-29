"""`blender.modular_kit`: grid-snapped modular building pieces (walls, openings, corners, floor, ceiling, stair, pillar,
railing) UV-mapped into a trim sheet -> GLB / FBX / OBJ per piece, a sample-room GLB and modular.json (bounds, grid
check, boundary match, colliders, room placements) for the engine exporters."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import blake3

from ...config import Settings
from ...contracts import BlobRef, Lane, Outputs, StepContext, StepError, StepResult
from . import modular_math as mm
from .client import WORKER_SCRIPT, BlenderResource
from .executor import call_script, worker_hash
from .fracture import FBX_MIME, GLB_MIME, asset_stem

REPORT_FILE = "modular.json"
REPORT_TYPE = "asset_studio.modular_kit"
OBJ_MIME = "model/obj"
CODE = "import modular_bpy, importlib\nimportlib.reload(modular_bpy)\nmodular_bpy.run(params)\n"
MAP_ROLES = ("albedo", "normal", "roughness")


def side_hash() -> str:
    h = blake3.blake3(worker_hash().encode())
    for side in ("modular_bpy.py", "modular_math.py", "trim_math.py"):
        h.update(WORKER_SCRIPT.with_name(side).read_bytes())
    return h.hexdigest()


def fallback_trim(grid: float, storey: int) -> dict[str, Any]:
    """Strip layout of the modular preset, for a greybox kit built without a trim sheet."""
    from ...ops.trim import make_strip_repeats, preset_layout, strip_ranges

    lay = preset_layout("modular", "greybox", grid, storey)
    ranges = strip_ranges(lay["strips"])
    for r, s in zip(ranges, lay["strips"]):
        r["repeats"] = make_strip_repeats(r["px"], s.get("aspect"))
    return {"strips": ranges}


def _map_paths(ctx: StepContext, refs: list[BlobRef]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in refs:
        stem = (r.name or "").rsplit(".", 1)[0]
        role = next((x for x in MAP_ROLES if stem.endswith(f"_{x}")), None)
        if role and role not in out:
            out[role] = str(ctx.blobs.path(r.hash))
    return out


class BlenderModularKitExecutor:
    kind = "blender.modular_kit"
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
        try:
            cfg = mm.normalize_params(params)
        except (TypeError, ValueError) as e:
            raise StepError("blender_bad_params", str(e)) from e
        asset = asset_stem(params.get("asset") or "kit")
        t0 = time.perf_counter()
        work = ctx.job_dir / "modular"
        trim_refs = inputs.get("trim") or []
        if trim_refs:
            try:
                trim = json.loads(await asyncio.to_thread(ctx.blobs.path(trim_refs[0].hash).read_bytes))
                if not isinstance(trim.get("strips"), list) or not trim["strips"]:
                    raise ValueError("no strips")
            except (OSError, ValueError, AttributeError) as e:
                raise StepError("op_bad_input", f"bad trim.json: {e}") from e
        else:
            trim = fallback_trim(cfg["grid"], cfg["storey"])
        maps = _map_paths(ctx, inputs.get("maps") or [])
        await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
        trim_path = work / "trim.json"
        await asyncio.to_thread(trim_path.write_text, json.dumps(trim), encoding="utf-8")
        report_path = work / "report.json"
        script_params = {**cfg, "asset": asset, "out_dir": str(work), "trim": str(trim_path), "maps": maps,
                         "report": str(report_path)}
        res = await call_script(self.blender, ctx, CODE, script_params=script_params,
                                timeout_s=float(params.get("_timeout_s", 600)), tag="modular", unrestricted=True)
        if not report_path.exists():
            raise StepError("blender_no_output", "modular kit did not write its report")
        raw = json.loads(await asyncio.to_thread(report_path.read_text, encoding="utf-8"))
        meshes, fbx, objs = [], [], []
        for e in raw["pieces"]:
            stem = f"{asset}_{e['piece']}"
            for ext, bucket, mime in ((".glb", meshes, GLB_MIME), (".fbx", fbx, FBX_MIME), (".obj", objs, OBJ_MIME)):
                f = work / f"{stem}{ext}"
                if not f.exists():
                    raise StepError("blender_no_output", f"modular kit did not write {f.name}")
                bucket.append(await ctx.blobs.put_file(f, name=f.name, mime=mime))
            e["glb"], e["fbx"], e["obj"] = meshes[-1].name, fbx[-1].name, objs[-1].name
            e["obj_hash"] = objs[-1].hash
        room = work / f"{asset}_room.glb"
        if not room.exists():
            raise StepError("blender_no_output", "modular kit did not write the sample room")
        room_ref = await ctx.blobs.put_file(room, name=room.name, mime=GLB_MIME)
        kit_ref = await ctx.blobs.put_file(Path(res["out"]), name=f"{asset}_kit.glb", mime=GLB_MIME)
        report = {"type": REPORT_TYPE, "room_glb": room.name, "kit_glb": kit_ref.name, "trimmed": bool(trim_refs),
                  **raw}
        warnings = []
        over = [e["piece"] for e in raw["pieces"] if e["tris"] > cfg["max_tris"]]
        if over:
            warnings.append(f"vượt {cfg['max_tris']} tam giác: {', '.join(over)}")
        if not raw["all_bounds_ok"]:
            warnings.append("kích thước lệch lưới: " + ", ".join(e["piece"] for e in raw["pieces"]
                                                                 if not e["bounds"]["ok"]))
        if not raw["all_ends_ok"]:
            warnings.append("đầu mảnh không khớp tường kề")
        if raw["uv_outside"]:
            warnings.append(f"{raw['uv_outside']} mặt có UV ra ngoài dải")
        return StepResult(outputs={
            "meshes": meshes, "kit": [kit_ref], "room": [room_ref], "fbx": fbx, "obj": objs,
            "report": [await ctx.blobs.put_bytes(json.dumps(report, indent=1).encode(), name=REPORT_FILE,
                                                 mime="application/json")]},
            meta={"pieces": len(raw["pieces"]), "grid": cfg["grid"], "storey": raw["dims"]["storey"],
                  "max_tris": raw["max_tris"], "bounds_ok": raw["all_bounds_ok"], "ends_ok": raw["all_ends_ok"],
                  "uv_outside": raw["uv_outside"], "trimmed": bool(trim_refs), "warnings": warnings,
                  "tris": {e["piece"]: e["tris"] for e in raw["pieces"]},
                  "seconds": round(time.perf_counter() - t0, 2), "blender_version": self.blender.version})
