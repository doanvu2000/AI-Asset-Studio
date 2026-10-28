"""`claude.spine_review_loop`: add the anim from a motion spec, render it, let Claude score it against the reference
frames and correct the spec, and repeat until `accept_score` or `rounds`. Keeps the best-scoring rendered round."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult
from ..ops import spine_anim as sa
from ..ops.rig import _read_json
from ..ops.spine_ref import (
    add_anim,
    animation_duration,
    read_skeleton,
    read_text,
    region_images,
    render_joint_zoom,
    render_strip,
)
from .claude import QueryFn, ask, load_images, put_json, sdk_query

SYSTEM = ("You are a senior Spine 2D animator. You tune a procedural motion spec so the rendered animation matches "
          "a reference video. " + sa.MOTION_SPEC_SEMANTICS)

PROMPT = """{images_note}
Brief: {brief}
Animation name: {name}. Duration of the current draft: {duration:g}s.
{ref_note}
Rig:
{rig}

Current spec (round {round} of at most {rounds}):
{spec}
{limits}{history}
Compare the draft with the reference: arm height, elbow and wrist, hip amplitude, lean, head tilt, timing, blink
spacing. Also judge the joints: does any elbow/wrist show a visible seam or sharp corner (see the zoom image)? If so,
lower the score and fix it within the joint limits rather than bending further; mention the joint limits in the
critique. Return a 0-10 match score for the CURRENT draft, a short critique (Markdown, cite tile numbers), and a
corrected full spec. Keep the same bone names; only bones listed in the rig exist."""

HISTORY_NOTE = """
Earlier rounds (score, critique). Do not reverse an earlier judgement unless the reference tiles prove it wrong; if
you do, name the tiles that show it:
{rows}
"""


def parse_spec(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict) and isinstance(raw.get("spec"), dict):
        raw = raw["spec"]
    if not isinstance(raw, dict):
        raise StepError("spec_invalid", "spec input must be a motion spec JSON object")
    return raw


class SpineReviewLoopExecutor:
    kind = "claude.spine_review_loop"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        text, _ = await read_skeleton(ctx, inputs)
        images = region_images(ctx, inputs)
        spec = parse_spec(await _read_json(ctx, inputs, "spec"))
        refs = await load_images(ctx, inputs.get("ref") or [])
        if not refs:
            raise StepError("op_no_input", "input 'ref' (reference contact sheets) is empty")
        rig = await read_text(ctx, inputs["rig"][0]) if inputs.get("rig") else ""
        ref_meta = await _read_json(ctx, inputs, "ref_meta", required=False)
        name = str(params.get("name") or "anim").strip()
        rounds = max(1, min(int(params.get("rounds", 3)), 8))
        accept = float(params.get("accept_score", 8))
        parts = [p for p in re.split(r"[,\s]+", str(params.get("skin") or "")) if p]
        count = int(params.get("count", 8))
        if params.get("second_opinion"):
            ctx.log("second_opinion is not supported yet; Claude scores alone", "warning")
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)

        history: list[dict[str, Any]] = []
        strips: list[Any] = []
        best: dict[str, Any] | None = None
        for r in range(rounds):
            ctx.progress(r, rounds, f"vòng {r + 1}: render")
            try:
                res = await asyncio.to_thread(add_anim, text, spec, name)
            except StepError as e:
                if best is None:
                    raise
                history.append({"round": r + 1, "error": e.message, "spec": spec})
                ctx.log(f"round {r + 1}: spec rejected ({e.message}); keeping round {best['round']}", "warning")
                break
            doc = json.loads(res["text"])
            dur = animation_duration(doc, name)
            times = [round(dur * i / (count - 1), 3) for i in range(count)]
            strip_img, strip_meta = await asyncio.to_thread(render_strip, doc, images, name, times, parts,
                                                            int(params.get("height", 520)))
            strip_ref = await ctx.blobs.put_bytes(_png(strip_img), name=f"{name}_round{r + 1}.png", mime="image/png")
            strips.append(strip_ref)
            limits = sa.joint_limits(doc, spec)
            deep = sa.deepest_bend(doc, name, limits)
            zoom_png, zoom_ref = None, None
            if deep:
                zoom_img = await asyncio.to_thread(render_joint_zoom, doc, images, name, deep[1], deep[0], parts)
                zoom_png = _png(zoom_img)
                zoom_ref = await ctx.blobs.put_bytes(zoom_png, name=f"{name}_round{r + 1}_zoom.png", mime="image/png")
            ctx.progress(r + 0.5, rounds, f"vòng {r + 1}: Claude chấm")
            prompt = PROMPT.format(
                images_note=(f"Images 1-{len(refs)}: reference video frames (contact sheets, row-major tiles labelled "
                             f"#index time). Image {len(refs) + 1}: render of the current draft at the times "
                             "labelled on each cell."
                             + (f" Image {len(refs) + 2}: close-up of joint {deep[0]} at t={deep[1]:g}s, its deepest "
                                "bend relative to its limit." if deep else "")),
                brief=params.get("brief") or "(none)", name=name, duration=dur,
                ref_note=_ref_note(ref_meta), rig=rig or "(not given)", round=r + 1, rounds=rounds,
                spec=json.dumps(spec, separators=(",", ":")), limits=_limits_note(limits),
                history=HISTORY_NOTE.format(rows="\n".join(
                    f"- round {h['round']}: score {h.get('score')}: {h.get('critique', h.get('error', ''))}"
                    for h in history)) if history else "")
            reply = await ask(self.query, {**params, "schema": sa.REVIEW_SCHEMA, "system": SYSTEM,
                                           "max_turns": params.get("max_turns", 2)},
                              prompt, str(ctx.job_dir),
                              [*refs, (_png(strip_img), "image/png"), *([(zoom_png, "image/png")] if zoom_png else [])],
                              ctx=ctx)
            out = reply.structured if isinstance(reply.structured, dict) else {}
            score = float(out.get("score", 0) or 0)
            new_spec = out.get("spec") if isinstance(out.get("spec"), dict) else spec
            entry = {"round": r + 1, "score": score, "critique": str(out.get("critique", "")), "spec": spec,
                     "next_spec": new_spec, "diff": sa.diff_spec(spec, new_spec), "strip": strip_ref.hash,
                     "times": times, "missing_skins": strip_meta["missing_skins"],
                     "zoom": zoom_ref.hash if zoom_ref else None,
                     "deepest_bend": {"bone": deep[0], "time": deep[1], "offset": round(deep[2], 2),
                                      "total": round(limits[deep[0]]["setup"] + deep[2], 2),
                                      "limit": limits[deep[0]]["limit"]} if deep else None}
            history.append(entry)
            if best is None or score > best["score"]:
                best = {**entry, "res": res, "strip_ref": strip_ref}
            if score >= accept:
                break
            spec = new_spec

        assert best is not None
        review = {"score": best["score"], "critique": best["critique"], "best_round": best["round"],
                  "accepted": best["score"] >= accept, "accept_score": accept,
                  "rounds": [{k: v for k, v in h.items()} for h in history]}
        stem = (inputs["skeleton"][0].name or "skeleton.json").rsplit(".", 1)[0]
        outputs: Outputs = {
            "skeleton": [await ctx.blobs.put_bytes(best["res"]["text"].encode("utf-8"), name=f"{stem}.json",
                                                   mime="application/json")],
            "anim": [await put_json(ctx, best["res"]["anim"], f"{name}.anim.json")],
            "strip": [best["strip_ref"]],
            "strips": strips,
            "spec": [await put_json(ctx, best["spec"], f"{name}.motion_spec.json")],
            "review": [await put_json(ctx, review, "review.json")],
        }
        ctx.progress(1, 1, "xong")
        return StepResult(outputs=outputs, meta={
            "score": best["score"], "critique": best["critique"], "best_round": best["round"],
            "accepted": review["accepted"],
            "score_history": [h.get("score") for h in history if "score" in h]})


def _limits_note(limits: dict[str, dict[str, Any]]) -> str:
    if not limits:
        return ""
    rows = [f"- {b}: setup {j['setup']:.1f} deg, allowed rotate offset [{j['lo']:.1f}, {j['hi']:.1f}]"
            f"{' (hard seam)' if j.get('hard') else ''}" for b, j in limits.items()]
    return ("Joint limits (keys outside are clamped by the generator; hard-seam joints show a sharp corner when "
            "bent further):\n" + "\n".join(rows) + "\n")


def _ref_note(meta: Any) -> str:
    if not isinstance(meta, dict) or not meta.get("times"):
        return ""
    return (f"Reference tiles were sampled at {meta.get('fps')} fps from video time {meta.get('t0')}s "
            f"(tile #i = {meta.get('t0')} + i/{meta.get('fps')} s).")


def _png(img: Any) -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
