"""Trim sheet generation: `trim.layout` (Claude picks the strips, or a preset) and `trim.strips` (one Z-Image image
per strip, each with its own prompt, which a recipe's map_param cannot express since the prompts come from a step)."""
from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Callable

from ..config import Settings
from ..contracts import BlobRef, Executor, Lane, Outputs, StepContext, StepError, StepResult, cache_enabled
from ..ops.trim import BASE, HEIGHTS, LAYOUT_FILE, MAX_STRIPS, PRESETS, gen_size, normalize_layout, preset_layout
from .claude import DEFAULT_MODEL, QueryFn, ask, put_json, sdk_query
from .comfy.retexture import _SubCtx

SYSTEM = ("You are a senior environment artist who designs trim sheets for modular game levels. A trim sheet is "
          "one square texture made of horizontal strips stacked top to bottom; each strip tiles seamlessly left to "
          "right and is mapped onto mesh faces (wall surfaces, moldings, frames, baseboards, edges). You pick strips "
          "that together dress a whole set of walls, floors and props in one style. Prompts are English and describe "
          "only the strip's surface, front-on.")

PROMPT = """Design the trim sheet for this style: {style}
Return `strips`, top to bottom, {lo}-{hi} strips. Each strip has:
- `name`: 1-2 lowercase words (e.g. wall, brick, molding, frame, baseboard, metal, edge),
- `px`: its height in pixels on a 2048 px sheet, one of {heights}; wide surfaces get 256 or 512, moldings and frames
  128, thin details 64 or 32. The heights must add up to exactly 2048.
- `prompt`: what the strip shows, e.g. "weathered red brick row with mortar lines".{extra}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"strips": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "px": {"type": "integer", "enum": list(HEIGHTS)},
                       "prompt": {"type": "string"}},
        "required": ["name", "px", "prompt"]}}},
    "required": ["strips"],
}
MODES = ("claude", "preset")
DEFAULT_WORKFLOW = "t2i_zimage_turbo"
PROMPT_KEY = "57:27.text"
WIDTH_KEY = "57:13.width"
HEIGHT_KEY = "57:13.height"
BATCH_KEY = "57:13.batch_size"
SEED_KEY = "57:3.seed"


class TrimLayoutExecutor:
    kind = "trim.layout"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return str(params.get("mode") or "claude") == "preset" or cache_enabled(params)

    def cache_material(self, params: dict[str, Any]) -> str:
        return SYSTEM + PROMPT + json.dumps(SCHEMA, sort_keys=True) + json.dumps(PRESETS, sort_keys=True)

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        mode = str(params.get("mode") or "claude")
        style = " ".join(str(params.get("style") or "").split())
        preset = str(params.get("preset") or "general")
        if mode not in MODES:
            raise StepError("op_bad_params", f"mode must be one of {MODES}")
        if not style:
            raise StepError("op_bad_params", "style is required")
        meta: dict[str, Any] = {"mode": mode}
        if mode == "preset":
            try:
                grid, storey = float(params.get("grid", 2) or 2), int(params.get("storey", 0) or 0)
                layout = preset_layout(preset, style, grid, storey)
            except (TypeError, ValueError) as e:
                raise StepError("op_bad_params", str(e)) from e
            meta["preset"] = preset
        else:
            notes = str(params.get("notes") or "").strip()
            extra = f"\nExtra direction from the artist: {notes}" if notes else ""
            prompt = PROMPT.format(style=style, lo=4, hi=MAX_STRIPS, heights=", ".join(map(str, HEIGHTS)),
                                   extra=extra)
            q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": SCHEMA,
                 "_timeout_s": params.get("_timeout_s")}
            await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
            ctx.progress(0, 1, "trim layout")
            reply = await ask(self.query, q, prompt, str(ctx.job_dir), ctx=ctx)
            try:
                layout = normalize_layout(reply.structured, style)
            except ValueError as e:
                raise StepError("claude_bad_output", str(e), retryable=True,
                                detail={"reply": reply.structured}) from e
            meta.update(reply.meta)
        text = "\n".join(f"{s['px']:>4}  {s['name']}: {s['prompt']}" for s in layout["strips"])
        ctx.progress(1, 1, "trim layout")
        return StepResult(outputs={"json": [await put_json(ctx, layout, LAYOUT_FILE)],
                                   "text": [await ctx.blobs.put_bytes(text.encode("utf-8"), name="trim_layout.txt",
                                                                      mime="text/plain")]},
                          meta={**meta, "strips": len(layout["strips"]), "total_px": BASE,
                                "names": [s["name"] for s in layout["strips"]]})


class TrimStripsExecutor:
    """One text-to-image call per strip of trim_layout.json, in layout order."""

    kind = "trim.strips"
    lane = Lane.GPU
    version = "1"
    auto_retry = 1

    def __init__(self, resolve: Callable[[str], Executor], resource: Any = None):
        self.resolve = resolve
        self.required_resource = resource

    def cacheable(self, params: dict[str, Any]) -> bool:
        try:
            return int(params.get("seed", -1)) >= 0
        except (TypeError, ValueError):
            return False

    def cache_material(self, params: dict[str, Any]) -> str:
        try:
            return self.resolve(f"comfy.{params.get('workflow') or DEFAULT_WORKFLOW}").cache_material(params)
        except Exception:
            return ""

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("layout") or []
        if not refs:
            raise StepError("op_no_input", "input 'layout' (trim_layout.json) is empty")
        try:
            raw = json.loads(await asyncio.to_thread(ctx.blobs.path(refs[0].hash).read_bytes))
            layout = normalize_layout(raw, str(raw.get("style") or ""))
            seed = int(params.get("seed", -1))
            width, height = int(params.get("width", 1536)), int(params.get("height", 512))
        except (OSError, TypeError, ValueError, AttributeError) as e:
            raise StepError("op_bad_input", f"bad trim layout: {e}") from e
        if width % 16 or height % 16 or not 256 <= min(width, height) <= max(width, height) <= 2048:
            raise StepError("op_bad_params", "width/height must be multiples of 16 within 256..2048")
        workflow = str(params.get("workflow") or DEFAULT_WORKFLOW)
        try:
            gen = self.resolve(f"comfy.{workflow}")
        except KeyError as e:
            raise StepError("comfy_bad_param", f"unknown comfy workflow: {workflow}") from e
        if seed < 0:
            seed = random.Random().randint(0, 2**31)
        passthrough = {k: v for k, v in params.items() if k.startswith("_")}
        strips = layout["strips"]
        out: list[BlobRef] = []
        t0 = time.perf_counter()
        for i, s in enumerate(strips):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            w, h = gen_size(s["aspect"]) if s.get("aspect") else (width, height)
            run_params = {**passthrough, PROMPT_KEY: s["gen_prompt"], WIDTH_KEY: w, HEIGHT_KEY: h,
                          BATCH_KEY: 1, SEED_KEY: seed + i}
            if i:
                run_params.pop("_free_before", None)
                run_params.pop("_restart_before", None)
            ctx.log(f"trim strip {i + 1}/{len(strips)} {s['name']}")
            res = await gen.run(_SubCtx(ctx, i, len(strips), s["name"]), run_params, {})  # type: ignore[arg-type]
            imgs = res.outputs.get("images") or []
            if not imgs:
                raise StepError("comfy_no_output", f"strip {s['name']}: no image", retryable=True)
            path = ctx.blobs.path(imgs[0].hash)
            out.append(await ctx.blobs.put_file(path, name=f"{i:02d}_{s['name']}.png", mime="image/png"))
        ctx.progress(len(strips), len(strips))
        return StepResult(outputs={"images": out, "layout": [refs[0]]},
                          meta={"strips": len(strips), "seed": seed, "width": width, "height": height,
                                "seconds": round(time.perf_counter() - t0, 1)})
