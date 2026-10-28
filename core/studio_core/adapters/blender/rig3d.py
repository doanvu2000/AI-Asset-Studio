"""`blender.rig_export`: static character mesh -> Humanoid-named rig + baked clips -> GLB and FBX + preview sheet."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from ...config import Settings
from ...contracts import Lane, Outputs, StepContext, StepError, StepResult
from ..comfy.server import ComfyResource
from . import rig3d_math
from .client import BlenderResource
from .clips import plan_clips
from .executor import _json_in, _single_mesh, render_params, worker_hash

REPORT_FILE = "rig3d.json"
REPORT_TYPE = "asset_studio.rig3d"
FBX_MIME = "application/octet-stream"
_STEM = re.compile(r"[^A-Za-z0-9_-]+")


def file_stem(name: Any) -> str:
    return _STEM.sub("_", str(name or "").strip()).strip("_")[:60] or "character"


def take_name(clip: str, actions: list[str]) -> str:
    """Blender's FBX importer names actions '<object>|<take>'; reading the takes back confirms what Unity sees."""
    for a in actions:
        if a.split("|")[-1] == clip:
            return a.split("|", 1)[1] if "|" in a else a
    return clip


def preview_sheet(files: list[tuple[str, str]], out: Path, cols: int = 4) -> None:
    """Rest turntable on the first row, then one row of key poses per clip."""
    from PIL import Image, ImageDraw

    rows: list[list[tuple[str, str]]] = []
    for path, label in files:
        group = label.split(" ")[0]
        if not rows or rows[-1][0][1].split(" ")[0] != group or len(rows[-1]) >= cols:
            rows.append([])
        rows[-1].append((path, label))
    with Image.open(files[0][0]) as im:
        w, h = im.size
    sheet = Image.new("RGBA", (cols * w, len(rows) * h), (40, 42, 48, 255))
    draw = ImageDraw.Draw(sheet)
    for r, row in enumerate(rows):
        for c, (path, label) in enumerate(row):
            with Image.open(path) as im:
                tile = im.convert("RGBA")
            sheet.alpha_composite(tile, (c * w, r * h))
            draw.text((c * w + 6, r * h + 4), label, fill=(235, 235, 235, 255))
    sheet.save(out)


class BlenderRigExportExecutor:
    kind = "blender.rig_export"
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
        try:
            clips = plan_clips({"anims": "idle, walk, run, attack", **params})
            fps = int(params.get("fps") or 30)
        except (TypeError, ValueError) as e:
            raise StepError("blender_bad_params", str(e)) from e
        stem = file_stem(params.get("name"))
        t0 = time.perf_counter()
        glb, work = ctx.job_dir / "mesh.glb", ctx.job_dir / "rig"
        out_glb, out_fbx = work / f"{stem}.glb", work / f"{stem}.fbx"

        def stage() -> None:
            (work / "preview").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.blobs.path(mesh.hash), glb)

        await asyncio.to_thread(stage)
        p = render_params({"res": 384, **params})
        if p["engine"] == "CYCLES" and self.comfy is not None:
            await self.comfy.free()

        def on_event(ev: dict[str, Any]) -> None:
            ctx.progress(ev.get("value", 0), ev.get("max", 1), ev.get("label"))

        ctx.progress(0, 1, "blender")
        size = joints_doc.get("size") or [front["width"], front["height"]]
        res = await self.blender.call("rig_export", {
            **p, "glb": str(glb), "out_glb": str(out_glb), "out_fbx": str(out_fbx),
            "out_dir": str(work / "preview"), "clips": clips, "fps": fps, "elevation": params.get("elevation", 15),
            "joints": joints, "image_w": float(size[0]), "render_res": float(front["width"]),
            "pivot": front["pivot"], "ortho_scale": front["ortho_scale"]},
            on_event=on_event, timeout=float(params.get("_timeout_s", 1800)))
        for f in (out_glb, out_fbx):
            if not f.exists():
                raise StepError("blender_no_output", f"rig_export did not write {f.name}")
        check = res.get("check") or {}
        missing = [b for b in rig3d_math.UNITY_REQUIRED if b not in check.get("glb_bones", rig3d_math.UNITY_REQUIRED)]
        if missing:
            raise StepError("blender_bad_output", f"exported GLB lacks humanoid bones {missing}")
        actions = check.get("fbx_actions") or []
        baked = [{"name": c["name"], "take": take_name(c["name"], actions), "first": int(c["first"]),
                  "last": int(c["last"]), "loop": bool(c["loop"]), "sprite_fps": c.get("fps")}
                 for c in res["clips"]]
        report = {"type": REPORT_TYPE, "humanoid": True, "fps": res["fps"], "clips": baked, "bones": res["bones"],
                  "glb": out_glb.name, "fbx": out_fbx.name, "height": res["height"], "weights": res["weights"],
                  "faces": res.get("faces"), "check": check}
        previews = [(f["file"], f["label"]) for f in res["previews"] if Path(f["file"]).exists()]
        if not previews:
            raise StepError("blender_no_output", "rig_export rendered no preview")
        sheet_path = work / "preview.png"
        await asyncio.to_thread(preview_sheet, previews, sheet_path)
        outputs: Outputs = {
            "meshes": [await ctx.blobs.put_file(out_glb, name=out_glb.name, mime="model/gltf-binary")],
            "fbx": [await ctx.blobs.put_file(out_fbx, name=out_fbx.name, mime=FBX_MIME)],
            "preview": [await ctx.blobs.put_file(sheet_path, name=f"{stem}_preview.png", mime="image/png")],
            "images": [await ctx.blobs.put_file(Path(f), name=Path(f).name, mime="image/png") for f, _ in previews],
            "report": [await ctx.blobs.put_bytes(json.dumps(report, indent=1).encode(), name=REPORT_FILE,
                                                 mime="application/json")]}
        return StepResult(outputs=outputs, meta={
            **{k: v for k, v in report.items() if k not in ("type", "check")},
            "animations": check.get("glb_animations"), "render_s": res.get("total_s"),
            "seconds": round(time.perf_counter() - t0, 2), "blender_version": self.blender.version})
