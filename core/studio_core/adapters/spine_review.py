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
from ..ops.spine_skirt import keyed_chains
from ..ops.spine_ref import (
    add_anim,
    animation_duration,
    read_skeleton,
    read_text,
    region_images,
    render_hand_zoom,
    render_held,
    render_joint_zoom,
    render_knee_zoom,
    render_strip,
    render_views,
    skin_parts,
    skirt_views,
    welded_joints,
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
{limits}{legs}{history}
Compare the draft with the reference: arm height, elbow and wrist, hip amplitude, hip bounce (count the dips over
the whole reference clip; a count in the brief wins and is the total for the clip, not per sway), lean, head tilt,
timing, blink spacing. Also judge:
- joints: skin and every piece of clothing must stay one continuous piece across each elbow/wrist, with no crease,
  gap or sharp corner (see the joint zooms, with the outfit and bare skin). Set seam_visible true if any zoom shows
  one, lower the score and fix it within the joint limits rather than bending further; mention the joint limits in
  the critique. A welded or covered joint should bend: a stick-straight arm there is a fault.
- limbs must not overlap or cross (see the knee zoom): if the knees overlap, lower hip.shift_x or the bounce depth
  first; do not rotate the legs directly.
- hands: the fingers trail the arm and curl slightly, never a rigid block, and do not break at a knuckle (see the
  hand close-up).
- skirts and dresses: the waist stays put, the skirt hangs straight from the hip (it does not lean with the chest),
  the hem sways gently and out of step across the folds, with no tear or visible mesh edge (see the skirt views).
- a hand-carried accessory (bag) must hang down by gravity, never be flung sideways (see the render wearing it); the
  generator counter-rotates it, so fix a flung bag by softening the arm/wrist keys, not by keying the bag.
Return a 0-10 match score for the CURRENT draft, a short critique (Markdown, cite tile numbers), and a corrected
full spec. Keep the same bone names; only bones listed in the rig exist."""

WELD_WARNING = ("Rig đã đổi attachment quanh khớp {bones} (mọi skin) thành mesh weighted 2 bone để da/áo liền khi "
                "gập: dùng skeleton xuất ra thay cho bản gốc khi tích hợp game.")
SKIRT_WARNING = ("{anims}: váy mesh nay theo key chuỗi váy sẵn có ({angles}), lệch có chủ đích so với bản gốc "
                 "(váy cứng cũ không theo).")
COVER_WARNING = ("Khớp {bones} được nới tới 40° vì có {skins} che: dùng anim này phải mặc kèm trang phục đó, bỏ ra "
                 "thì lộ mối nối.")

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
        parts = skin_parts(params)
        welded = await welded_joints(ctx, inputs)
        covers = {b: s for b, s in sa.joint_covers(json.loads(text), parts, str(params.get("joint_cover") or "auto"))
                  .items() if b not in welded}
        bare = [p for p in re.split(r"[,\s]+", str(params.get("bare_skin") or "")) if p] or parts[:1]
        hang_mode = str(params.get("hang") or "auto")
        skirt_mode = str(params.get("skirt") or "auto")
        views_mode = str(params.get("skirt_views") or "auto")
        if covers:
            ctx.log("joints covered by clothing: " + ", ".join(f"{b} ({s})" for b, s in covers.items()))
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
                res = await asyncio.to_thread(add_anim, text, spec, name, covers, welded, hang_mode,
                                              skirt_mode)
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
            limits = sa.joint_limits(doc, spec, covers, welded)
            deep = sa.deepest_bend(doc, name, limits)
            zoom_png, zoom_ref, bare_png, bare_ref = None, None, None, None
            if deep:
                zoom_png = _png(await asyncio.to_thread(render_joint_zoom, doc, images, name, deep[1], deep[0], parts))
                zoom_ref = await ctx.blobs.put_bytes(zoom_png, name=f"{name}_round{r + 1}_zoom.png", mime="image/png")
                if bare and bare != parts:
                    bare_png = _png(await asyncio.to_thread(render_joint_zoom, doc, images, name, deep[1], deep[0],
                                                            bare))
                    bare_ref = await ctx.blobs.put_bytes(bare_png, name=f"{name}_round{r + 1}_zoom_bare.png",
                                                         mime="image/png")
            knee = await asyncio.to_thread(sa.knee_gap, doc, name)
            knee_png, knee_ref = None, None
            if knee:
                knee_img = await asyncio.to_thread(render_knee_zoom, doc, images, name, knee["time"], knee["knees"],
                                                   parts)
                knee_png = _png(knee_img)
                knee_ref = await ctx.blobs.put_bytes(knee_png, name=f"{name}_round{r + 1}_knees.png",
                                                     mime="image/png")
            held = await asyncio.to_thread(render_held, doc, images, name, res["hang"], parts,
                                           int(params.get("height", 520)))
            held_png, held_ref = None, None
            if held:
                held_png = _png(held[0])
                held_ref = await ctx.blobs.put_bytes(held_png, name=f"{name}_round{r + 1}_held.png", mime="image/png")
            hand = await asyncio.to_thread(render_hand_zoom, doc, images, name, res["follow"], parts)
            hand_png = _png(hand[0]) if hand else None
            hand_ref = await ctx.blobs.put_bytes(hand_png, name=f"{name}_round{r + 1}_hand.png",
                                                 mime="image/png") if hand_png else None
            view_skins = skirt_views(doc, res["skirt"], views_mode)
            views = await asyncio.to_thread(render_views, doc, images, name, view_skins, parts)
            view_refs = [(await ctx.blobs.put_bytes(_png(img), name=f"{name}_round{r + 1}_{s.split('/')[-1]}.png",
                                                    mime="image/png"), s) for img, s in views]
            extra = [(zoom_png, f"close-up of joint {deep[0]} at t={deep[1]:g}s, its deepest bend relative to its "
                                "limit") if deep else None,
                     (bare_png, f"the same close-up with bare skin only ({', '.join(bare)})") if deep else None,
                     (knee_png, f"both knees at t={knee['time']:g}s, where they come closest") if knee else None,
                     (held_png, f"the draft at t={held[1]['time']:g}s (hand raised highest) wearing the hand-carried "
                                f"accessory {held[1]['skin']}") if held else None]
            extra += [(hand_png, f"close-up of hand {hand[1]['bone']} at t={hand[1]['time']:g}s, where the arm "
                                 "swings fastest") if hand else None]
            extra += [(_png(img), f"the draft wearing {s} (skirt view)") for img, s in views]
            extra = [e for e in extra if e and e[0]]
            ctx.progress(r + 0.5, rounds, f"vòng {r + 1}: Claude chấm")
            prompt = PROMPT.format(
                images_note=(f"Images 1-{len(refs)}: reference video frames (contact sheets, row-major tiles labelled "
                             f"#index time). Image {len(refs) + 1}: render of the current draft at the times "
                             "labelled on each cell."
                             + "".join(f" Image {len(refs) + 2 + i}: {what}." for i, (_, what) in enumerate(extra))),
                brief=params.get("brief") or "(none)", name=name, duration=dur,
                ref_note=_ref_note(ref_meta), rig=rig or "(not given)", round=r + 1, rounds=rounds,
                spec=json.dumps(spec, separators=(",", ":")), limits=_limits_note(limits), legs=_knee_note(knee),
                history=HISTORY_NOTE.format(rows="\n".join(
                    f"- round {h['round']}: score {h.get('score')}: {h.get('critique', h.get('error', ''))}"
                    for h in history)) if history else "")
            reply = await ask(self.query, {**params, "schema": sa.REVIEW_SCHEMA, "system": SYSTEM,
                                           "max_turns": params.get("max_turns", 2)},
                              prompt, str(ctx.job_dir),
                              [*refs, (_png(strip_img), "image/png"), *((png, "image/png") for png, _ in extra)],
                              ctx=ctx)
            out = reply.structured if isinstance(reply.structured, dict) else {}
            score = float(out.get("score", 0) or 0)
            seam = bool(out.get("seam_visible"))
            new_spec = out.get("spec") if isinstance(out.get("spec"), dict) else spec
            entry = {"round": r + 1, "score": score, "critique": str(out.get("critique", "")), "spec": spec,
                     "next_spec": new_spec, "diff": sa.diff_spec(spec, new_spec), "strip": strip_ref.hash,
                     "times": times, "missing_skins": strip_meta["missing_skins"],
                     "zoom": zoom_ref.hash if zoom_ref else None,
                     "zoom_bare": bare_ref.hash if bare_ref else None, "seam_visible": seam,
                     "deepest_bend": {"bone": deep[0], "time": deep[1], "offset": round(deep[2], 2),
                                      "total": round(limits[deep[0]]["setup"] + deep[2], 2),
                                      "limit": limits[deep[0]]["limit"]} if deep else None,
                     "knee": knee, "knee_zoom": knee_ref.hash if knee_ref else None,
                     "bounce": spec.get("bounce"), "hang": res["hang"],
                     "held": held[1] if held else None, "held_view": held_ref.hash if held_ref else None,
                     "hand": hand[1] if hand else None, "hand_zoom": hand_ref.hash if hand_ref else None,
                     "skirt": res["skirt"], "skirt_views": [{"skin": s, "image": ref.hash} for ref, s in view_refs]}
            history.append(entry)
            overlap = bool(knee and knee["overlap"])
            fault = overlap or seam
            if best is None or (not fault, score) > (not best["fault"], best["score"]):
                best = {**entry, "res": res, "strip_ref": strip_ref, "overlap": overlap, "fault": fault}
            if score >= accept and not fault:
                break
            spec = new_spec

        assert best is not None
        keyed = keyed_chains(json.loads(text), best["skirt"], name) if best.get("skirt") else {}
        warning = " ".join(w for w in (
            WELD_WARNING.format(bones=", ".join(welded)) if welded else "",
            SKIRT_WARNING.format(anims=", ".join(keyed), angles=", ".join(f"≤{v:g}°" for v in keyed.values()))
            if keyed else "",
            COVER_WARNING.format(bones=", ".join(covers), skins=", ".join(sorted(set(covers.values()))))
            if covers else "") if w)
        critique = best["critique"] + (f"\n\n**Lưu ý:** {warning}" if warning else "")
        review = {"score": best["score"], "critique": critique, "best_round": best["round"],
                  "accepted": best["score"] >= accept and not best["fault"], "accept_score": accept,
                  "cover": covers, "welded": welded, "seam_visible": best["seam_visible"], "warning": warning or None, "knee_overlap": best["overlap"],
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
            "score": best["score"], "critique": critique, "best_round": best["round"],
            "accepted": review["accepted"], "warning": warning or None, "knee_overlap": best["overlap"],
            "seam_visible": best["seam_visible"], "welded": welded,
            "score_history": [h.get("score") for h in history if "score" in h]})


def _limits_note(limits: dict[str, dict[str, Any]]) -> str:
    if not limits:
        return ""

    def tag(j: dict[str, Any]) -> str:
        if j.get("welded"):
            return " (welded: skin and clothing bend as one piece, bend it naturally within the range)"
        if j.get("cover"):
            return f" (hard seam covered by {j['cover']}: seam hidden, bend it within the range)"
        return " (hard seam)" if j.get("hard") else ""

    rows = [f"- {b}: setup {j['setup']:.1f} deg, allowed rotate offset [{j['lo']:.1f}, {j['hi']:.1f}]{tag(j)}"
            for b, j in limits.items()]
    return ("Joint limits (keys outside are clamped by the generator; uncovered hard-seam joints show a sharp corner "
            "when bent further):\n" + "\n".join(rows) + "\n")


def _knee_note(knee: dict[str, Any] | None) -> str:
    if not knee:
        return ""
    state = ("OVERLAP: the knees come too close or cross; lower hip.shift_x or the bounce depth"
             if knee["overlap"] else "ok")
    return (f"Knee check ({' / '.join(knee['knees'])}): closest horizontal gap {knee['min_gap']:g}px at "
            f"t={knee['time']:g}s = {knee['ratio'] * 100:.0f}% of setup {knee['setup_gap']:g}px (overlap below "
            f"{sa.KNEE_OVERLAP * 100:.0f}%): {state}.\n")


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
