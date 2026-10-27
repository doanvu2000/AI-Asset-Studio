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
from ..claude import DEFAULT_MODEL, QueryFn, ask, load_images, sdk_query
from ...store.stats import meta_cost

DEFAULT_WORKFLOW = "multi_angles_qwen2511"
RETRY_PROMPT = (" The character is standing upright on the ground, full body, vertical, head at the top and feet at"
                " the bottom, same size as the input.")
ON_FAIL = ("drop", "keep", "fail")

# Silhouettes cannot see a staff that switched hands or a cape that changed colour; a VLM pass can.
CONSISTENCY_PROMPT = """Image 1 is the reference FRONT view of a game character. The following images are candidate \
turnaround views generated from it, in this order:
{listing}

For each candidate set ok=true only if ALL of these hold:
- same character as the reference: outfit, colours, proportions and accessories match (small detail noise is fine);
- upright full body, head at the top and feet at the bottom, same pose family, not lying down or cropped;
- it plausibly shows its expected angle;
- asymmetric items stay on the same side of the BODY as in the reference (a weapon in the character's right hand \
stays in its right hand; it may be hidden when that side faces away from the camera).

Left/right mapping, do not mix these up:
- FRONT reference: the character's right hand is on the IMAGE-LEFT.
- BACK view: the character's right hand is on the IMAGE-RIGHT (so an item that is image-left in the front view is \
correctly image-right in the back view).
- Side view where the character faces image-left: we see its LEFT side; right-hand items are on the far side, \
partly or fully hidden.
- Side view where the character faces image-right: we see its RIGHT side; right-hand items are on the near side.
Only reject for a swapped side when you are sure after applying this mapping.
Return one entry per candidate, using the candidate names above; reason is a short phrase."""
CONSISTENCY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"views": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "ok": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["name", "ok", "reason"]}}},
    "required": ["views"],
}


@dataclass(frozen=True)
class ViewSpec:
    name: str
    output: str
    prompt_key: str
    prompt: str
    slot: str | None = None
    expect: str = ""


def parse_views(raw: Any) -> list[ViewSpec]:
    if not isinstance(raw, list) or not raw:
        raise StepError("op_bad_params", "views must be a non-empty list of {name, output, prompt_key, prompt}")
    out = []
    for i, v in enumerate(raw):
        try:
            out.append(ViewSpec(name=str(v["name"]), output=str(v["output"]), prompt_key=str(v["prompt_key"]),
                                prompt=str(v["prompt"]), slot=str(v["slot"]) if v.get("slot") else None,
                                expect=str(v.get("expect") or "")))
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

    def __init__(self, resolve: Callable[[str], Executor], resource: Any = None, query: QueryFn = sdk_query):
        self.resolve = resolve
        self.required_resource = resource
        self.query = query

    async def _judge(self, ctx: StepContext, params: dict[str, Any], front: BlobRef,
                     cands: list[tuple[ViewSpec, BlobRef]]) -> tuple[dict[str, tuple[bool, str]], dict[str, Any]]:
        listing = "\n".join(f"{i + 2}. {v.name}: {v.expect or v.prompt}" for i, (v, _) in enumerate(cands))
        images = await load_images(ctx, [front, *(r for _, r in cands)])
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        opts = {"model": params.get("consistency_model") or DEFAULT_MODEL, "max_turns": 2, "schema": CONSISTENCY_SCHEMA,
                "_timeout_s": 300}
        reply = await ask(self.query, opts, CONSISTENCY_PROMPT.format(listing=listing), str(ctx.job_dir), images)
        got = {str(x.get("name")): (bool(x.get("ok")), str(x.get("reason") or ""))
               for x in (reply.structured or {}).get("views", []) if isinstance(x, dict)}
        # A candidate the model skipped counts as passing: the silhouette check already accepted it.
        return {v.name: got.get(v.name, (True, "not judged")) for v, _ in cands}, reply.meta

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
        consistency = bool(params.get("consistency", False))
        claude_cost = 0.0
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
            shape_ok: list[tuple[ViewSpec, BlobRef]] = []
            for v, ref in zip(pending, refs):
                img = await asyncio.to_thread(im.open_image, ctx.blobs.path(ref.hash))
                r = await asyncio.to_thread(check_view, front_sil, silhouette(img), cfg)
                tries[v.name].append({"attempt": n + 1, "hash": ref.hash, "seeds": res.meta.get("seeds"),
                                      "seconds": res.meta.get("seconds"), **r})
                ctx.log(f"view {v.name} attempt {n + 1}: {'ok' if r['ok'] else ','.join(r['reasons'])} "
                        f"{r['scores']}")
                if r["ok"]:
                    shape_ok.append((v, ref))
                elif v.name not in best or len(r["reasons"]) < best[v.name][0]:
                    best[v.name] = (len(r["reasons"]), ref)
            verdicts: dict[str, tuple[bool, str]] = {}
            if consistency and shape_ok:
                try:
                    verdicts, cmeta = await self._judge(ctx, params, fronts[0], shape_ok)
                    claude_cost += float(cmeta.get("cost_usd") or 0)
                except StepError as e:
                    claude_cost += meta_cost(e.detail)
                    # Fail open: an unavailable model must not throw away views the silhouette check accepted.
                    ctx.log(f"consistency check skipped: {e.code} {str(e)[:200]}")
                    for v, _ in shape_ok:
                        tries[v.name][-1]["consistency"] = {"error": e.code}
            for v, ref in shape_ok:
                ok, why = verdicts.get(v.name, (True, ""))
                entry = tries[v.name][-1]
                if v.name in verdicts:
                    entry["consistency"] = {"ok": ok, "reason": why}
                    ctx.log(f"view {v.name} attempt {n + 1} consistency: {'ok' if ok else why}")
                if ok:
                    chosen[v.name] = ref
                    continue
                entry["ok"] = False
                entry["reasons"] = [*entry.get("reasons", []), "inconsistent"]
                if v.name not in best or best[v.name][0] > 1:
                    best[v.name] = (1, ref)
            pending = [v for v in pending if v.name not in chosen]

        failed = [v.name for v in pending]
        report: dict[str, Any] = {
            "views": [{"name": v.name, "ok": v.name in chosen, "attempts": tries[v.name]} for v in views],
            "failed": failed, "regenerated": sum(len(t) - 1 for t in tries.values()),
            "on_fail": on_fail, "config": config_dict(cfg), "dropped": [], "drop_inputs": [],
            "consistency": consistency,
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
        if consistency:
            meta["claude_cost_usd"] = round(claude_cost, 4)
        return StepResult(outputs=outputs, meta=meta)
