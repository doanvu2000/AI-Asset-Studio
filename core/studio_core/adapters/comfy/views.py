"""`views.qwen_checked`: generate turnaround views, check each against the front, regenerate the bad ones.

The engine has no loop construct, so generate → check → regenerate lives in one step; every attempt goes to the
report. Views that never pass are dropped (their conditioning slot unlinked downstream), kept, or fail the step.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from ...contracts import BlobRef, Executor, Lane, Outputs, StepContext, StepError, StepResult
from ...ops import image as im
from ...ops.viewcheck import ViewCheckConfig, check_view, config_dict, silhouette

DEFAULT_WORKFLOW = "multi_angles_qwen2511"
RETRY_PROMPT = (" The character is standing upright on the ground, full body, vertical, head at the top and feet at"
                " the bottom, same size as the input.")
ON_FAIL = ("drop", "keep", "fail")


@dataclass(frozen=True)
class ViewSpec:
    name: str
    output: str
    prompt_key: str
    prompt: str
    slot: str | None = None


def parse_views(raw: Any) -> list[ViewSpec]:
    if not isinstance(raw, list) or not raw:
        raise StepError("op_bad_params", "views must be a non-empty list of {name, output, prompt_key, prompt}")
    out = []
    for i, v in enumerate(raw):
        try:
            out.append(ViewSpec(name=str(v["name"]), output=str(v["output"]), prompt_key=str(v["prompt_key"]),
                                prompt=str(v["prompt"]), slot=str(v["slot"]) if v.get("slot") else None))
        except (KeyError, TypeError) as e:
            raise StepError("op_bad_params", f"views[{i}]: missing {e}") from None
    if len({v.name for v in out}) != len(out) or len({v.output for v in out}) != len(out):
        raise StepError("op_bad_params", "views need unique names and outputs")
    return out


class CheckedViewsExecutor:
    kind = "views.qwen_checked"
    lane = Lane.GPU
    version = "1"
    auto_retry = 1

    def __init__(self, resolve: Callable[[str], Executor], resource: Any = None):
        self.resolve = resolve
        self.required_resource = resource

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    def _generator(self, workflow: str) -> Executor:
        try:
            return self.resolve(f"comfy.{workflow}")
        except KeyError as e:
            raise StepError("comfy_bad_param", f"unknown comfy workflow: {workflow}") from e

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        t0 = time.perf_counter()
        fronts = inputs.get("image") or []
        if len(fronts) != 1:
            raise StepError("op_no_input", f"needs exactly one front image, got {len(fronts)}")
        views = parse_views(params.get("views"))
        gen = self._generator(str(params.get("workflow") or DEFAULT_WORKFLOW))
        all_outputs = [o["node"] for o in getattr(gen, "entry", {}).get("outputs", [])]
        unknown = [v.output for v in views if v.output not in all_outputs]
        if unknown:
            raise StepError("op_bad_params", f"views: {unknown} are not outputs of the workflow")
        attempts = max(1, int(params.get("attempts", 3)))
        on_fail = str(params.get("on_fail", "drop"))
        if on_fail not in ON_FAIL:
            raise StepError("op_bad_params", f"on_fail must be one of {ON_FAIL}")
        min_views = int(params.get("min_views", 1))
        retry_prompt = str(params.get("retry_prompt", RETRY_PROMPT))
        cfg = ViewCheckConfig.from_params(params)
        passthrough = {k: v for k, v in params.items() if k.startswith("_")}

        front = await asyncio.to_thread(im.open_image, ctx.blobs.path(fronts[0].hash))
        front_sil = await asyncio.to_thread(silhouette, front)
        if front_sil.bbox is None:
            raise StepError("op_bad_input", "front image has no foreground")

        tries: dict[str, list[dict[str, Any]]] = {v.name: [] for v in views}
        chosen: dict[str, BlobRef] = {}
        best: dict[str, tuple[int, BlobRef]] = {}
        pending = sorted(views, key=lambda v: all_outputs.index(v.output))
        for n in range(attempts):
            if not pending:
                break
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            keep = {v.output for v in pending}
            run_params = {**passthrough, "_drop_outputs": [o for o in all_outputs if o not in keep]}
            run_params.update({v.prompt_key: v.prompt + (retry_prompt if n else "") for v in pending})
            ctx.log(f"views attempt {n + 1}/{attempts}: {', '.join(v.name for v in pending)}")
            res = await gen.run(ctx, run_params, {"image": list(fronts)})
            refs = res.outputs.get("images", [])
            if len(refs) != len(pending):
                raise StepError("views_mismatch", f"expected {len(pending)} view images, got {len(refs)}",
                                retryable=True)
            for v, ref in zip(pending, refs):
                img = await asyncio.to_thread(im.open_image, ctx.blobs.path(ref.hash))
                r = await asyncio.to_thread(check_view, front_sil, silhouette(img), cfg)
                tries[v.name].append({"attempt": n + 1, "hash": ref.hash, "seeds": res.meta.get("seeds"),
                                      "seconds": res.meta.get("seconds"), **r})
                ctx.log(f"view {v.name} attempt {n + 1}: {'ok' if r['ok'] else ','.join(r['reasons'])} "
                        f"{r['scores']}")
                if r["ok"]:
                    chosen[v.name] = ref
                elif v.name not in best or len(r["reasons"]) < best[v.name][0]:
                    best[v.name] = (len(r["reasons"]), ref)
            pending = [v for v in pending if v.name not in chosen]

        failed = [v.name for v in pending]
        report: dict[str, Any] = {
            "views": [{"name": v.name, "ok": v.name in chosen, "attempts": tries[v.name]} for v in views],
            "failed": failed, "regenerated": sum(len(t) - 1 for t in tries.values()),
            "on_fail": on_fail, "config": config_dict(cfg), "dropped": [], "drop_inputs": [],
        }
        if failed and on_fail == "fail":
            raise StepError("views_rejected", f"views still bad after {attempts} attempts: {', '.join(failed)}",
                            detail=report)
        out: list[BlobRef] = []
        for v in views:
            if v.name in chosen:
                out.append(chosen[v.name])
            elif on_fail == "keep":
                out.append(best[v.name][1])
            else:
                report["dropped"].append(v.name)
                if v.slot:
                    report["drop_inputs"].append(v.slot)
        if len(out) < min_views:
            raise StepError("views_rejected", f"only {len(out)} usable view(s), need {min_views}", detail=report)
        data = json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8")
        report_ref = await ctx.blobs.put_bytes(data, name="views_report.json", mime="application/json")
        outputs: Outputs = {"images": out, "report": [report_ref]}
        meta = {"failed": failed, "dropped": report["dropped"], "regenerated": report["regenerated"],
                "attempts": {k: len(t) for k, t in tries.items()}, "seconds": round(time.perf_counter() - t0, 2)}
        return StepResult(outputs=outputs, meta=meta)
