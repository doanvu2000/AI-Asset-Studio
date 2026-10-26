"""`mesh.checked`: image(s) → mesh via a comfy workflow, rendered back at the input azimuths and compared by
silhouette; a mesh that does not match is regenerated with fresh seeds.

The same four Pixal3D/TRELLIS views can give a clean mesh or shredded fragments depending on the seed alone
(measured: min silhouette IoU 0.82–0.90 for good meshes, ≤0.66 for broken ones).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from ...contracts import BlobRef, Executor, Lane, Outputs, StepContext, StepError, StepResult
from ...ops.viewcheck import _iou, silhouette
from .executor import _drop_inputs

DEFAULT_MIN_IOU = 0.75
ON_FAIL = ("best", "fail")
CHECK_KEYS = {"workflow", "attempts", "min_iou", "azimuths", "on_fail", "check_res"}
# A 256px EEVEE check render needs well under 1 GB; below this the resident mesh model is unloaded first.
VRAM_FREE_MIN_MB = 3072


def vram_free_mb() -> int | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32"
                             else 0).stdout
        return min(int(v) for v in out.split())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _sil(path: Any) -> Any:
    with Image.open(path) as img:
        return silhouette(img)


class CheckedMeshExecutor:
    kind = "mesh.checked"
    lane = Lane.GPU
    version = "1"
    auto_retry = 1

    def __init__(self, resolve: Callable[[str], Executor], resource: Any = None, blender: Any = None):
        self.resolve = resolve
        self.required_resource = resource
        self.blender = blender
        self.vram_free_mb = vram_free_mb

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    def _image_azimuths(self, gen: Executor, params: dict[str, Any], n_images: int) -> list[float | None]:
        """Azimuth of each input image, following the workflow slots left after `_drop_inputs`."""
        az = params.get("azimuths") or {}
        if not isinstance(az, dict):
            raise StepError("op_bad_params", "azimuths must map '<node>.<input>' image keys to degrees")
        entry = getattr(gen, "entry", {}) or {}
        keys = [i["key"] for i in entry.get("images", [])]
        drops = params.get("_drop_inputs")
        if drops and getattr(gen, "workflow_path", None) is not None:
            prompt = json.loads(gen.workflow_path.read_text(encoding="utf-8"))
            keys = _drop_inputs(prompt, entry, drops, keys)
        out = [float(az[k]) if k in az else None for k in keys[:n_images]]
        return out + [None] * (n_images - len(out))

    async def _score(self, ctx: StepContext, mesh: BlobRef, attempt: int, targets: list[tuple[float, Any]],
                     res: int) -> tuple[dict[float, float], list[str]]:
        work = ctx.job_dir / f"check-{attempt}"
        glb = work / "mesh.glb"

        def stage() -> None:
            work.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.blobs.path(mesh.hash), glb)

        await asyncio.to_thread(stage)
        # The mesh model stays resident in ComfyUI; freeing it every attempt would reload it for each regeneration.
        free = getattr(self.required_resource, "free", None)
        if free is not None:
            mb = await asyncio.to_thread(self.vram_free_mb)
            if mb is None or mb < VRAM_FREE_MIN_MB:
                await free()
        r = await self.blender.call("render_dirs", {"glb": str(glb), "out_dir": str(work / "frames"), "count": 4,
                                                    "elevation": 0, "res": res, "engine": "BLENDER_EEVEE"},
                                    timeout=300)
        frames = {round(f["angle"]) % 360: f["file"] for f in r["frames"]}
        renders = [(await ctx.blobs.put_file(Path(frames[a]), name=f"check{attempt}_{a:03d}.png", mime="image/png")).hash
                   for a in sorted(frames)]
        scores = {}
        for az, sil in targets:
            frame = frames.get(round(az) % 360)
            if frame is None or sil.shape is None:
                continue
            got = await asyncio.to_thread(_sil, frame)
            scores[az] = round(_iou(sil.shape, got.shape), 3) if got.shape is not None else 0.0
        return scores, renders

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        t0 = time.perf_counter()
        workflow = str(params.get("workflow") or "")
        try:
            gen = self.resolve(f"comfy.{workflow}")
        except KeyError as e:
            raise StepError("comfy_bad_param", f"unknown comfy workflow: {workflow}") from e
        images = inputs.get("image") or []
        attempts = max(1, int(params.get("attempts", 3)))
        min_iou = float(params.get("min_iou", DEFAULT_MIN_IOU))
        on_fail = str(params.get("on_fail", "best"))
        if on_fail not in ON_FAIL:
            raise StepError("op_bad_params", f"on_fail must be one of {ON_FAIL}")
        gen_params = {k: v for k, v in params.items() if k not in CHECK_KEYS}

        targets = []
        for ref, az in zip(images, self._image_azimuths(gen, params, len(images))):
            if az is not None:
                targets.append((az, await asyncio.to_thread(_sil, ctx.blobs.path(ref.hash))))
        check = self.blender is not None and bool(targets)

        tries: list[dict[str, Any]] = []
        best: tuple[float, BlobRef, StepResult] | None = None
        for n in range(attempts):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.log(f"mesh attempt {n + 1}/{attempts}")
            res = await gen.run(ctx, gen_params, {"image": list(images)})
            meshes = res.outputs.get("meshes") or []
            if not meshes:
                raise StepError("comfy_no_output", "mesh workflow returned no mesh", retryable=True)
            entry: dict[str, Any] = {"attempt": n + 1, "hash": meshes[0].hash, "seeds": res.meta.get("seeds"),
                                     "seconds": res.meta.get("seconds")}
            tries.append(entry)
            if not check:
                best = (1.0, meshes[0], res)
                break
            try:
                scores, renders = await self._score(ctx, meshes[0], n + 1, targets, int(params.get("check_res", 256)))
            except StepError as e:
                # Fail open: a broken render must not discard a mesh that may be fine.
                ctx.log(f"mesh check skipped: {e.code} {str(e)[:200]}")
                entry["error"] = e.code
                best = (1.0, meshes[0], res)
                break
            low = min(scores.values()) if scores else 0.0
            entry.update({"iou": {str(round(k)): v for k, v in scores.items()}, "min_iou": low, "ok": low >= min_iou,
                          "renders": renders})
            ctx.log(f"mesh attempt {n + 1}: min iou {low:.2f} {entry['iou']}")
            if best is None or low > best[0]:
                best = (low, meshes[0], res)
            if entry["ok"]:
                break

        assert best is not None
        skipped = check and any(t.get("error") for t in tries)
        passed = not check or any(t.get("ok") for t in tries)
        status = "off" if not check else "passed" if passed else "skipped" if skipped else "failed"
        report = {"attempts": tries, "min_iou": min_iou, "checked": check, "passed": passed, "skipped": skipped,
                  "status": status, "chosen": best[1].hash, "regenerated": len(tries) - 1, "on_fail": on_fail}
        if status == "failed" and on_fail == "fail":
            raise StepError("mesh_rejected", f"mesh still off the input views after {attempts} attempts "
                                             f"(best min IoU {best[0]:.2f} < {min_iou})", detail=report)
        report_ref = await ctx.blobs.put_bytes(json.dumps(report, indent=1).encode("utf-8"), name="mesh_report.json",
                                               mime="application/json")
        outputs: Outputs = {**best[2].outputs, "report": [report_ref]}
        return StepResult(outputs=outputs, meta={**best[2].meta, "attempts": len(tries), "passed": passed,
                                                 "skipped": skipped, "check": status,
                                                 "best_min_iou": round(best[0], 3) if check else None,
                                                 "seconds": round(time.perf_counter() - t0, 2)})
