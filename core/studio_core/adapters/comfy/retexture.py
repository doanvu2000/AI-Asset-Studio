"""`retex.paint`: paints the orbit views of a mesh one after another (StableGen-style sequential mode).

View 1 is depth-controlled text-to-image. Every later view first renders what the texture painted so far looks like
from its camera; only pixels no earlier view saw well (or that this view sees much more squarely) are masked and
inpainted with ControlNet Union 2.0 depth + inpaint, so neighbouring views agree on the shared surface."""
from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Callable

import numpy as np
from PIL import Image

from ...contracts import BlobRef, Executor, Lane, Outputs, StepContext, StepError, StepResult
from ..blender import retex_math as rm

DEFAULT_WORKFLOW = "retex_zimage_union2"
PROMPT_KEY = "10.text"
SEED_KEY = "14.seed"
STEPS_KEY = "14.steps"
STRENGTH_KEY = "8.strength"
LOOK = ("single isolated object, fully textured surface, plain white background, evenly lit with soft diffuse light, "
        "no cast shadows, no harsh highlights, game asset texture, highly detailed")


class _SubCtx:
    """Maps a sub-run's 0..1 progress into this view's slice of the step."""

    def __init__(self, ctx: StepContext, index: int, total: int, label: str):
        self._ctx, self._i, self._n, self._label = ctx, index, total, label

    def progress(self, value: float, max: float = 1.0, label: str | None = None, node: str | None = None) -> None:
        frac = (value / max) if max else 0.0
        self._ctx.progress(self._i + min(frac, 1.0), self._n, self._label)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)


def view_prompt(prompt: str, style: str, cam: rm.Camera) -> str:
    words = rm.VIEW_WORDS.get(cam.name) or rm.angle_words(cam.azimuth, cam.elevation)
    parts = [prompt.strip().rstrip(","), style.strip().rstrip(","), words, LOOK]
    return ", ".join(p for p in parts if p)


def inpaint_inputs(v: rm.ViewRaster, known_rgb: np.ndarray, known: np.ndarray, grow: int
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """-> (inpaint image (s, s, 3) float, regenerate mask (s, s) bool, object mask). Background stays white and
    known; unknown object pixels are mid grey, the model's neutral fill for masked areas."""
    obj = v.tri >= 0
    regen = obj & ~known
    if grow > 0 and regen.any():
        regen = rm.dilate(regen, grow) & rm.dilate(obj, 2)
    img = np.ones(obj.shape + (3,), np.float32)
    img[obj] = 0.5
    keep = known & ~regen
    img[keep] = known_rgb[keep]
    return img, regen, obj


def composite(base: np.ndarray, gen: np.ndarray, regen: np.ndarray, feather: int) -> tuple[np.ndarray, np.ndarray]:
    """Generated pixels inside the mask, fading into the known render over `feather` px outside it."""
    soft = regen.astype(np.float32)
    if feather > 0:
        soft = np.maximum(soft, np.clip(rm.box_blur(soft, feather) * 2, 0, 1))
    return base * (1 - soft[..., None]) + gen * soft[..., None], soft


class RetexPaintExecutor:
    kind = "retex.paint"
    lane = Lane.GPU
    version = "1"
    auto_retry = 1

    def __init__(self, resolve: Callable[[str], Executor], resource: Any = None):
        self.resolve = resolve
        self.required_resource = resource

    def cacheable(self, params: dict[str, Any]) -> bool:
        return int(params.get("seed", -1)) >= 0

    def cache_material(self, params: dict[str, Any]) -> str:
        from ..blender.retexture import retex_hash

        try:
            gen = self.resolve(f"comfy.{params.get('workflow') or DEFAULT_WORKFLOW}")
            wf = gen.cache_material(params)
        except Exception:
            wf = ""
        return f"{retex_hash()}|{wf}"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from ...ops.retexture import VIEWS_FILE, labelled_grid, load_rgb, put_rgb, save_views, to_png, views_in

        t0 = time.perf_counter()
        views = await views_in(ctx, inputs)
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise StepError("op_bad_params", "prompt is required")
        try:
            seed = int(params.get("seed", -1))
            steps = int(params.get("steps", 10))
            strength = float(params.get("strength", 0.85))
            known_q = float(params.get("known_quality", 0.45))
            better = float(params.get("better", 0.4))
            grow = int(params.get("grow", 6))
            feather = int(params.get("feather", 4))
            acc_size = int(params.get("texture", 1024))
            extra = max(0, min(4, int(params.get("extra_views", 3))))
            target = float(params.get("target_unpainted", 2.0))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        style = str(params.get("style") or "")
        workflow = str(params.get("workflow") or DEFAULT_WORKFLOW)
        try:
            gen = self.resolve(f"comfy.{workflow}")
        except KeyError as e:
            raise StepError("comfy_bad_param", f"unknown comfy workflow: {workflow}") from e
        if seed < 0:
            seed = random.Random().randint(0, 2**31)
        cams = list(views.cams)
        passthrough = {k: v for k, v in params.items() if k.startswith("_")}
        surf = await asyncio.to_thread(rm.uv_surface, views.pos, views.nrm, views.uv, acc_size)
        acc = rm.Accumulator(surf, 4.0)
        painted: list[BlobRef] = []
        masks: list[BlobRef] = []
        report: list[dict[str, Any]] = []

        async def paint(k: int, cam: rm.Camera, total: int) -> None:
            v = await asyncio.to_thread(rm.render_view, cam, views.pos, views.nrm)
            if k == 0:
                base = np.ones((cam.size, cam.size, 3), np.float32)
                regen, obj = np.ones((cam.size, cam.size), bool), v.tri >= 0
                inpaint = np.full_like(base, 0.5)
            else:
                known_rgb, known = await asyncio.to_thread(rm.known_projection, v, views.uv, acc, known_q, better,
                                                           v.cos * v.edge)
                inpaint, regen, obj = await asyncio.to_thread(inpaint_inputs, v, known_rgb, known, grow)
                base = inpaint
            share = float((regen & obj).sum()) / max(1, int(obj.sum()))
            entry: dict[str, Any] = {"view": cam.name, "azimuth": round(cam.azimuth, 1),
                                     "elevation": round(cam.elevation, 1), "regenerated": round(share, 4),
                                     "seed": seed + k}
            if k > 0 and share < 0.002:
                comp, soft = base, np.zeros(regen.shape, np.float32)
                entry["skipped"] = True
            else:
                depth = await put_rgb(ctx, np.repeat(rm.depth_image(v)[..., None], 3, -1), f"depth_{cam.name}.png")
                inp = await put_rgb(ctx, inpaint, f"inpaint_{cam.name}.png")
                msk = await put_rgb(ctx, np.repeat(regen[..., None], 3, -1).astype(np.float32),
                                    f"mask_{cam.name}.png")
                run_params = {**passthrough, PROMPT_KEY: view_prompt(prompt, style, cam), SEED_KEY: seed + k,
                              STEPS_KEY: steps, STRENGTH_KEY: strength}
                if k > 0:
                    run_params.pop("_free_before", None)
                    run_params.pop("_restart_before", None)
                ctx.log(f"retex view {k + 1}/{total} {cam.name}: regenerate {share:.1%}")
                res = await gen.run(_SubCtx(ctx, k, total, cam.name), run_params,  # type: ignore[arg-type]
                                    {"image": [depth, inp, msk]})
                outs = res.outputs.get("images") or []
                if not outs:
                    raise StepError("comfy_no_output", f"view {cam.name}: no image", retryable=True)
                img = await asyncio.to_thread(load_rgb, ctx.blobs.path(outs[0].hash), cam.size)
                comp, soft = await asyncio.to_thread(composite, base, img, regen, feather)
                entry["seconds"] = res.meta.get("seconds")
            vis = await asyncio.to_thread(rm.visibility, surf, v)
            await asyncio.to_thread(acc.add, vis, comp, None if k == 0 else soft)
            painted.append(await put_rgb(ctx, np.where(obj[..., None], comp, 1.0), f"painted_{k:02d}_{cam.name}.png"))
            masks.append(await put_rgb(ctx, np.repeat(soft[..., None], 3, -1), f"mask_{k:02d}_{cam.name}.png"))
            report.append(entry)

        total = len(cams) + extra
        for k, cam in enumerate(list(cams)):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            await paint(k, cam, total)
            ctx.progress(k + 1, total, cam.name)
        # Probe directions the fixed views miss (under a hat brim, between legs) and paint the worst one.
        probes = rm.probe_cameras(views.pos, 20, 256)
        unpainted: list[float] = []
        for e in range(extra + 1):
            counts = await asyncio.to_thread(rm.probe_unpainted, surf, acc.painted(), views.pos, views.nrm,
                                             views.uv, probes)
            unpainted.append(round(100.0 * sum(b for b, _ in counts) / max(1, sum(t for _, t in counts)), 3))
            if e == extra or unpainted[-1] < target or ctx.cancelled.is_set():
                break
            p = probes[max(range(len(probes)), key=lambda i: counts[i][0])]
            cam = rm.Camera(f"extra{e + 1}", p.azimuth, p.elevation, cams[0].center, cams[0].half, cams[0].size)
            ctx.log(f"retex: {unpainted[-1]:.1f}% of the visible surface unpainted, adding {cam.name} "
                    f"(az {cam.azimuth:.0f}, el {cam.elevation:.0f})")
            await paint(len(cams), cam, total)
            cams.append(cam)
            ctx.progress(len(cams), total, cam.name)

        npz = ctx.job_dir / VIEWS_FILE
        await asyncio.to_thread(npz.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(save_views, npz, views.pos.astype(np.float32), views.nrm.astype(np.float32),
                                views.uv.astype(np.float32), cams, views.info)
        views_ref = await ctx.blobs.put_file(npz, name=VIEWS_FILE, mime="application/octet-stream")

        def grid() -> bytes:
            tiles = [(c.name, Image.open(ctx.blobs.path(r.hash))) for c, r in list(zip(cams, painted))[:4]]
            return to_png(np.asarray(labelled_grid(tiles, 384)))

        preview = await ctx.blobs.put_bytes(await asyncio.to_thread(grid), name="painted_views.png", mime="image/png")
        doc = {"seed": seed, "steps": steps, "strength": strength, "views": report, "unpainted_after": unpainted}
        rep = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name="paint.json",
                                        mime="application/json")
        ctx.progress(total, total)
        return StepResult(outputs={"images": painted, "masks": masks, "preview": [preview], "views": [views_ref],
                                   "report": [rep]},
                          meta={"seed": seed, "views": [e["view"] for e in report],
                                "regenerated": {e["view"]: e["regenerated"] for e in report},
                                "unpainted_visible_pct": unpainted, "seconds": round(time.perf_counter() - t0, 2)})
