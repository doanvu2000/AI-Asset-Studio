"""`upscale.model`: the model pass of the upscale recipe, Real-ESRGAN or SeedVR2 per the `engine` param.

SeedVR2 takes a target short edge instead of a fixed factor; its workflow derives that from the input size, so the
step only rewrites the factor in the expression. `auto` picks SeedVR2 when its custom node and weights are on this
machine (it is an optional pack) and Real-ESRGAN otherwise."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ...config import Settings
from ...contracts import Executor, Lane, Outputs, StepContext, StepError, StepResult

ENGINES = ("auto", "esrgan", "seedvr2")
WORKFLOWS = {"esrgan": "upscale_esrgan", "seedvr2": "upscale_seedvr2"}
SEEDVR2_NODE_DIR = "seedvr2"
SEEDVR2_MODEL_DIR = "SEEDVR2"
SEEDVR2_FACTOR_KEY = "5.expression"


def seedvr2_expression(scale: int) -> str:
    # The upscaler node wants an even short edge.
    return f"2 * round(min(a, b) * {scale} / 2)"


def seedvr2_models(workflows_dir: Path) -> list[str]:
    f = workflows_dir / f"{WORKFLOWS['seedvr2']}.api.json"
    try:
        wf = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(n["inputs"]["model"]) for n in wf.values()
            if str(n.get("class_type", "")).startswith("SeedVR2Load") and "model" in n.get("inputs", {})]


def seedvr2_installed(settings: Settings) -> bool:
    nodes = settings.comfy_dir / "custom_nodes"
    try:
        has_node = any(d.is_dir() and SEEDVR2_NODE_DIR in d.name.lower() for d in nodes.iterdir())
    except OSError:
        return False
    models = seedvr2_models(settings.comfy_workflows_dir)
    return has_node and bool(models) and all(
        (settings.comfy_models_dir / SEEDVR2_MODEL_DIR / m).is_file() for m in models)


def resolve_engine(engine: Any, settings: Settings) -> str:
    e = str(engine or "auto").lower()
    if e not in ENGINES:
        raise StepError("op_bad_param", f"engine must be one of {ENGINES}, got {e!r}")
    if e == "auto":
        return "seedvr2" if seedvr2_installed(settings) else "esrgan"
    return e


class UpscaleModelExecutor:
    kind = "upscale.model"
    lane = Lane.GPU
    version = "1"
    auto_retry = 1

    def __init__(self, resolve: Callable[[str], Executor], settings: Settings, resource: Any = None):
        self.resolve = resolve
        self.settings = settings
        self.required_resource = resource

    def _gen(self, engine: str) -> Executor:
        try:
            return self.resolve(f"comfy.{WORKFLOWS[engine]}")
        except KeyError as e:
            raise StepError("comfy_bad_param", f"unknown comfy workflow: {WORKFLOWS[engine]}") from e

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        try:
            engine = resolve_engine(params.get("engine"), self.settings)
            return f"{engine}:{self._gen(engine).cache_material(params)}"
        except Exception:
            return ""

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        engine = resolve_engine(params.get("engine"), self.settings)
        scale = int(params.get("scale", 4))
        if scale not in (2, 4):
            raise StepError("op_bad_param", f"scale must be 2 or 4, got {scale}")
        if engine == "seedvr2" and not seedvr2_installed(self.settings):
            raise StepError("comfy_missing_model", "SeedVR2 is not installed: add the 'Phóng to SeedVR2' pack in "
                            "Settings → Setup, or pick engine esrgan")
        passthrough = {k: v for k, v in params.items() if k.startswith("_")}
        run_params = {**passthrough, SEEDVR2_FACTOR_KEY: seedvr2_expression(scale)} if engine == "seedvr2" \
            else passthrough
        ctx.log(f"upscale engine {engine} x{scale}")
        res = await self._gen(engine).run(ctx, run_params, {"image": list(inputs.get("image") or [])})
        return StepResult(outputs={"images": res.outputs.get("images", [])},
                          meta={**res.meta, "engine": engine, "scale": scale})
