"""`op.spine_convert`: a Spine 4.2 skeleton + atlas (spine-character / spine-face / spine-physics output) becomes a
native Godot 4 Skeleton2D scene and a Unity bone prefab with clips, as two zips, so no Spine runtime or licence is
needed in the game."""
from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path
from typing import Any

from ..contracts import Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor
from .rig import _put_json
from .spine_ref import _one, read_skeleton, region_images, skin_parts


def _zip(files: list[tuple[str, bytes | Path]], root: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in files:
            z.writestr(f"{root}/{rel}", data.read_bytes() if isinstance(data, Path) else data)
    return buf.getvalue()


class SpineConvert(OpExecutor):
    kind = "op.spine_convert"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from ..export.spine_skeleton2d import Rig, godot_files
        from ..export.spine_unity2d import unity_files

        _, doc = await read_skeleton(ctx, inputs)
        images = region_images(ctx, inputs)
        stem = Path(_one(inputs, "skeleton").name or "skeleton.json").stem
        asset = str(params.get("asset") or "").strip() or stem
        skins = ",".join(skin_parts({"skin": params.get("skin", "auto")}, doc))
        try:
            rig = await asyncio.to_thread(Rig, doc, images, skins, str(params.get("bake") or "auto"),
                                          float(params.get("fps", 30)))
        except (KeyError, ValueError, IndexError) as e:
            raise StepError("op_bad_input", f"cannot read the Spine skeleton: {e!r}") from e
        if not rig.parts:
            raise StepError("op_no_parts", "no region / mesh attachment with an atlas region found for this skin",
                            detail={"warnings": rig.warnings})
        want = str(params.get("engines") or "both")
        out: Outputs = {}
        notes: list[str] = []
        if want in ("both", "godot"):
            files, n = await asyncio.to_thread(godot_files, rig, asset)
            data = await asyncio.to_thread(_zip, files, asset)
            out["godot"] = [await ctx.blobs.put_bytes(data, name=f"{asset}_godot.zip", mime="application/zip")]
            notes += n
        if want in ("both", "unity"):
            files, n = await asyncio.to_thread(unity_files, rig, asset, ctx.step_id, float(params.get("ppu", 100)))
            data = await asyncio.to_thread(_zip, files, asset)
            out["unity"] = [await ctx.blobs.put_bytes(data, name=f"{asset}_unity.zip", mime="application/zip")]
            notes += n
        report = {"asset": asset, "skins": skins.split(",") if skins else [], "bones": len(rig.bones),
                  "parts": len(rig.parts), "animations": list(rig.anims), "baked": rig.bake, "fps": rig.fps,
                  "warnings": rig.warnings, "notes": notes}
        out["report"] = [await _put_json(ctx, report, f"{asset}_convert.json")]
        for w in rig.warnings:
            ctx.log(w, "warning")
        return StepResult(outputs=out, meta={"bones": len(rig.bones), "parts": len(rig.parts),
                                             "animations": len(rig.anims), "baked": rig.bake,
                                             "warnings": rig.warnings[:8]})


SPINE_CONVERT_OPS: tuple[type[OpExecutor], ...] = (SpineConvert,)
