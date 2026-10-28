"""Storyboard → animatic. `claude.storyboard` breaks a cutscene script into 4–12 shots (framing, camera move, prompt,
characters, one voiced line, SFX/music cues) and a cast with voices; `storyboard.frames` draws each shot (Klein edit
over a layout of the character references, or Z-Image when no referenced character is in it) and on a regenerate
keeps the frames the reviewer picked; `storyboard.score` optionally makes a music bed (ACE-Step) and shot SFX
(Stable Audio)."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

from PIL import Image

from ..config import Settings
from ..contracts import BlobRef, Executor, Lane, Outputs, StepContext, StepError, StepResult
from ..ops.animatic import ASPECTS, CAMERAS, FRAMINGS, gen_size, shot_index
from ..ops.executors import _put_png
from ..ops.keyart import fit_cover
from ..store.stats import meta_cost
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query
from .tts import EN_AUTO

PLAN_TYPE = "storyboard"
MIN_SHOTS, MAX_SHOTS = 4, 12
MAX_REFS = 3
EXPRESSIONS = ["neutral", "happy", "sad", "angry", "surprised", "scared", "thinking", "determined"]
KOKORO_POOL = {"male": ["am_michael", "bm_george", "am_puck", "am_adam", "bm_lewis", "am_eric", "am_onyx", "bm_daniel"],
               "female": ["af_heart", "bf_emma", "af_nicole", "af_bella", "bf_isabella", "af_sarah", "af_nova"]}
# Piper vi_VN vais1000 has one (female) speaker: speakers are told apart by pitch shifting in the mix.
VI_PITCH = {("male", "young"): 0.88, ("male", "adult"): 0.83, ("male", "old"): 0.78,
            ("female", "young"): 1.06, ("female", "adult"): 1.0, ("female", "old"): 0.94}
KLEIN = "edit_flux2_klein4b"
ZIMAGE = "t2i_zimage_turbo"
FRAMING_TEXT = {"wide": "Wide establishing shot, the characters small within the full environment.",
                "medium": "Medium shot, characters framed from the waist up.",
                "close": "Close-up shot on the face and shoulders."}
LAYOUT_HEIGHT = {"wide": 0.55, "medium": 0.92, "close": 1.0}
LAYOUT_CROP = {"wide": 1.0, "medium": 0.75, "close": 0.45}
LAYOUT_BG = (128, 128, 128)

SYSTEM = (
    "You are the cutscene director and storyboard artist of a game studio. You turn a short script into a shot list "
    "for an animatic: clear staging, readable framing, varied shot sizes, and spoken lines short enough to be voiced "
    "by text-to-speech (plain sentences, numbers as words, no emoji, no stage directions).")

PROMPT = """Break this cutscene script into a storyboard of {lo}-{hi} shots for an animatic of about {seconds} \
seconds in a {aspect} frame.
Script:
\"\"\"{script}\"\"\"
{chars}
Visual style: {style}
Return:
- `language`: "vi" if the script is Vietnamese, otherwise "en". All spoken lines are in this language.
- `title`: a short title.
- `cast`: every speaking role (and "Narrator" when there is narration) with `name`, `gender` (male | female),
  `age` (young | adult | old) and `speed` (0.85-1.15 speaking rate that fits them).
- `music`: `prompt` (English tags for an instrumental underscore: genre, instruments, mood) and `bpm`.
- `shots` in story order, each with:
  `duration` in seconds (1.5-8; a spoken line needs about 2.5 words per second plus a breath),
  `framing` (wide | medium | close), `camera` (static | push_in | pull_out | pan_left | pan_right),
  `characters`: names of the characters visible in the shot (at most 3, names as listed above),
  `description`: one English image prompt for the frame: setting, action, poses, expressions, lighting and
  composition; describe visible characters by appearance and position (left / centre / right); no text, captions
  or speech bubbles,
  `speaker` and `line`: at most one short spoken line (max 2 sentences) exactly as it should be voiced, keeping the
  script's own dialogue words; empty strings when nobody speaks,
  `expression` of the speaker (one of: {exprs}),
  `sfx`: a short English sound-effect prompt for the shot, or empty,
  `music_cue`: a few words on the music at that moment, or empty.
Establish places with wide shots, give emotional lines close shots, and vary framing and camera between neighbours."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": ["vi", "en"]},
        "title": {"type": "string"},
        "cast": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "gender": {"type": "string"}, "age": {"type": "string"},
            "speed": {"type": "number"}}, "required": ["name", "gender", "age", "speed"]}},
        "music": {"type": "object", "properties": {"prompt": {"type": "string"}, "bpm": {"type": "number"}},
                  "required": ["prompt", "bpm"]},
        "shots": {"type": "array", "items": {"type": "object", "properties": {
            "duration": {"type": "number"}, "framing": {"type": "string", "enum": list(FRAMINGS)},
            "camera": {"type": "string", "enum": list(CAMERAS)},
            "characters": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"}, "speaker": {"type": "string"}, "line": {"type": "string"},
            "expression": {"type": "string"}, "sfx": {"type": "string"}, "music_cue": {"type": "string"}},
            "required": ["duration", "framing", "camera", "characters", "description", "speaker", "line",
                         "expression", "sfx", "music_cue"]}},
    },
    "required": ["language", "title", "cast", "music", "shots"],
}


def _clean(v: Any) -> str:
    return " ".join(str(v or "").split())


def parse_characters(raw: Any, refs: int) -> list[dict[str, Any]]:
    """`Name, description` per line; line i names reference image i. Extra lines are characters without a picture."""
    out: list[dict[str, Any]] = []
    for ln in str(raw or "").splitlines():
        if not ln.strip():
            continue
        name, _, desc = ln.partition(",")
        out.append({"name": _clean(name), "description": _clean(desc)})
    for i in range(len(out), refs):
        out.append({"name": f"Character {i + 1}", "description": ""})
    for i, c in enumerate(out):
        c["ref"] = i if i < refs else None
    return out


def match_name(raw: Any, names: list[str]) -> str | None:
    key = _clean(raw).casefold()
    if not key:
        return None
    for n in names:
        if n.casefold() == key:
            return n
    for n in names:
        if key in n.casefold() or n.casefold() in key:
            return n
    return None


def _gender(v: Any) -> str:
    return "female" if str(v or "").lower().startswith("f") else "male"


def _age(v: Any) -> str:
    a = str(v or "").lower()
    return a if a in ("young", "old") else "adult"


def cast_voices(raw: Any) -> list[dict[str, Any]]:
    """Distinct Kokoro voice per English speaker; distinct pitch per Vietnamese speaker (one Piper voice)."""
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    pitches: dict[float, int] = {}
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict) or not _clean(c.get("name")) or any(o["name"] == _clean(c["name"]) for o in out):
            continue
        g, a = _gender(c.get("gender")), _age(c.get("age"))
        prefer = [EN_AUTO[(g, a)], *KOKORO_POOL[g]]
        voice = next((v for v in prefer if v not in used), prefer[0])
        used.add(voice)
        base = VI_PITCH[(g, a)]
        n = pitches.get(base, 0)
        pitches[base] = n + 1
        try:
            speed = round(min(1.15, max(0.85, float(c.get("speed") or 1.0))), 2)
        except (TypeError, ValueError):
            speed = 1.0
        out.append({"name": _clean(c["name"]), "gender": g, "age": a, "speed": speed, "kokoro": voice,
                    "pitch": round(base * (1 + 0.05 * ((n + 1) // 2) * (1 if n % 2 else -1)), 3)})
    return out


def normalize_plan(raw: Any, *, characters: list[dict[str, Any]], target_seconds: float, max_shots: int,
                   aspect: str, style: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("shots"), list):
        raise StepError("claude_bad_output", "storyboard reply has no shots", retryable=True)
    hi = max(MIN_SHOTS, min(MAX_SHOTS, max_shots))
    cast = cast_voices(raw.get("cast"))
    char_names = [c["name"] for c in characters]
    shots: list[dict[str, Any]] = []
    for item in raw["shots"]:
        if not isinstance(item, dict) or not _clean(item.get("description")):
            continue
        visible: list[str] = []
        for n in item.get("characters") or []:
            m = match_name(n, char_names) or match_name(n, [c["name"] for c in cast]) or _clean(n)
            if m and m not in visible:
                visible.append(m)
        line = _clean(item.get("line"))
        speaker = match_name(item.get("speaker"), [c["name"] for c in cast]) or _clean(item.get("speaker"))
        if line and not speaker:
            speaker = "Narrator"
        if line and speaker not in [c["name"] for c in cast]:
            cast += cast_voices([{"name": speaker, "gender": "male", "age": "adult", "speed": 1.0}])
        expr = _clean(item.get("expression")).lower()
        try:
            dur = float(item.get("duration") or 3.0)
        except (TypeError, ValueError):
            dur = 3.0
        shots.append({
            "duration": dur, "framing": item.get("framing") if item.get("framing") in FRAMINGS else "medium",
            "camera": item.get("camera") if item.get("camera") in CAMERAS else "static",
            "characters": visible[:MAX_REFS],
            "refs": [c["ref"] for c in characters if c["name"] in visible and c["ref"] is not None][:MAX_REFS],
            "description": _clean(item["description"]), "speaker": speaker if line else "", "line": line,
            "expression": expr if expr in EXPRESSIONS else "neutral", "sfx": _clean(item.get("sfx")),
            "music_cue": _clean(item.get("music_cue"))})
    shots = shots[:hi]
    if len(shots) < 2:
        raise StepError("claude_bad_output", f"storyboard has {len(shots)} usable shot(s)", retryable=True,
                        detail={"reply": raw})
    total = sum(s["duration"] for s in shots)
    # Claude's durations drift from the asked length; rescale before clamping so the cut keeps its rhythm.
    scale = target_seconds / total if target_seconds > 0 and total > 0 else 1.0
    for i, s in enumerate(shots, 1):
        s["duration"] = round(min(8.0, max(1.5, s["duration"] * scale)), 2)
        s["id"], s["index"] = f"shot_{i:02d}", i
    music = raw.get("music") if isinstance(raw.get("music"), dict) else {}
    try:
        bpm = int(min(160, max(60, float(music.get("bpm") or 90))))
    except (TypeError, ValueError):
        bpm = 90
    lang = raw.get("language") if raw.get("language") in ("vi", "en") else "en"
    return {"type": PLAN_TYPE, "version": 1, "title": _clean(raw.get("title")) or "Cutscene", "language": lang,
            "aspect": aspect if aspect in ASPECTS else "16:9", "style": style, "target_seconds": target_seconds,
            "characters": characters, "cast": cast,
            "music": {"prompt": _clean(music.get("prompt")) or "cinematic orchestral underscore", "bpm": bpm},
            "shots": [{"id": s.pop("id"), "index": s.pop("index"), **s} for s in shots]}


def tts_doc(plan: dict[str, Any]) -> dict[str, Any]:
    """The shot lines as a `tts.npc_voice` dialogue doc: one line per voiced shot, the speaker's voice on the line."""
    lang = plan["language"]
    cast = {c["name"]: c for c in plan.get("cast") or []}
    lines = []
    for s in plan["shots"]:
        if not s.get("line"):
            continue
        c = cast.get(s["speaker"]) or {}
        lines.append({"id": s["id"], "category": "line", "expression": s.get("expression", "neutral"),
                      "text": {lang: s["line"]},
                      "voice": {"gender": c.get("gender", "male"), "age": c.get("age", "adult"),
                                "speed": c.get("speed", 1.0), "name": c.get("kokoro", "auto")}})
    return {"type": "npc_dialogue", "version": 1, "name": plan.get("title"), "persona": "", "languages": [lang],
            "expressions": EXPRESSIONS, "voice": {"gender": "male", "age": "adult", "speed": 1.0, "style": ""},
            "lines": lines, "allow_empty": True}


def summary(plan: dict[str, Any]) -> str:
    rows = [f"{plan['title']} ({plan['language']}, {plan['aspect']}, {len(plan['shots'])} shots)"]
    for s in plan["shots"]:
        line = f' — {s["speaker"]}: "{s["line"]}"' if s.get("line") else ""
        rows.append(f"[{s['id']} {s['duration']}s {s['framing']}/{s['camera']} {', '.join(s['characters'])}] "
                    f"{s['description']}{line}")
    return "\n".join(rows)


class ClaudeStoryboardExecutor:
    kind = "claude.storyboard"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return bool(params.get("cache"))

    def cache_material(self, params: dict[str, Any]) -> str:
        return SYSTEM + PROMPT + json.dumps(SCHEMA, sort_keys=True)

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        script = str(params.get("script") or "").strip()
        if not script:
            raise StepError("op_bad_input", "script is empty")
        refs = (inputs.get("images") or [])[:MAX_REFS]
        characters = parse_characters(params.get("characters"), len(refs))
        style = _clean(params.get("style"))
        aspect = str(params.get("aspect") or "16:9")
        seconds = float(params.get("seconds") or 30)
        hi = max(MIN_SHOTS, min(MAX_SHOTS, int(params.get("max_shots") or 8)))
        if characters:
            listing = "\n".join(f"{i + 1}. {c['name']}" + (f" - {c['description']}" if c["description"] else "")
                                + (" (reference image attached)" if c["ref"] is not None else "")
                                for i, c in enumerate(characters))
            chars = "Characters (reference images are attached in this order):\n" + listing
        else:
            chars = "No character list was given; name the characters you need."
        prompt = PROMPT.format(lo=MIN_SHOTS, hi=hi, seconds=round(seconds), aspect=aspect, script=script,
                               chars=chars, style=style or "cinematic game cutscene", exprs=", ".join(EXPRESSIONS))
        q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": SCHEMA,
             "_timeout_s": params.get("_timeout_s")}
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "storyboard")
        reply = await ask(self.query, q, prompt, str(ctx.job_dir), await load_images(ctx, refs), ctx=ctx)
        plan = normalize_plan(reply.structured, characters=characters, target_seconds=seconds, max_shots=hi,
                              aspect=aspect, style=style)
        ctx.progress(1, 1, "storyboard")
        outputs = {"json": [await put_json(ctx, plan, "storyboard.json")],
                   "dialogue": [await put_json(ctx, tts_doc(plan), "storyboard_lines.json")],
                   "text": [await ctx.blobs.put_bytes(summary(plan).encode("utf-8"), name="storyboard.txt",
                                                      mime="text/plain")]}
        return StepResult(outputs=outputs, meta={**reply.meta, "shots": len(plan["shots"]),
                                                 "language": plan["language"],
                                                 "voiced": sum(1 for s in plan["shots"] if s["line"])})


def _flat(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (*LAYOUT_BG, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return img.convert("RGB")


def layout_canvas(refs: list[Image.Image], size: tuple[int, int], framing: str) -> Image.Image:
    """The shot's characters side by side on flat grey at the frame's aspect, cropped and sized for the framing, so
    Klein's output keeps the frame size and starts from the right identities."""
    w, h = size
    canvas = Image.new("RGB", size, LAYOUT_BG)
    if not refs:
        return canvas
    slot = w / len(refs)
    for i, ref in enumerate(refs):
        img = _flat(ref)
        if img.height > img.width * 1.15:
            img = img.crop((0, 0, img.width, max(1, round(img.height * LAYOUT_CROP.get(framing, 1.0)))))
        scale = min(slot * 0.94 / img.width, h * LAYOUT_HEIGHT.get(framing, 0.9) / img.height)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.Resampling.LANCZOS)
        x = round(i * slot + (slot - img.width) / 2)
        y = (h - img.height) // 2 if framing == "close" else h - img.height
        canvas.paste(img, (x, y))
    return canvas


def frame_prompt(shot: dict[str, Any], style: str, with_refs: bool) -> str:
    framing = FRAMING_TEXT.get(shot.get("framing", "medium"), "")
    style = style or "cinematic game cutscene illustration"
    mood = (f" {shot['speaker']} looks {shot['expression']}." if shot.get("line") and shot.get("speaker")
            and shot.get("expression") not in (None, "", "neutral") else "")
    if not with_refs:
        return (f"{shot['description']} {framing}{mood} {style}. A single cinematic storyboard frame, no text, no "
                "captions, no speech bubbles, no borders.")
    who = "each character" if len(shot.get("refs") or []) > 1 else "the character"
    return (f"Turn this character reference into one finished cinematic storyboard frame. Scene: "
            f"{shot['description']} {framing}{mood} Keep {who}'s face, hairstyle, outfit and colours exactly as in "
            f"the reference; repose and move them to fit the scene. Replace the flat grey backdrop with the full "
            f"environment filling the whole frame edge to edge. Style: {style}. No text, no captions, no borders.")


async def kept_frames(ctx: StepContext, keep: Any) -> dict[int, BlobRef]:
    """Shot index → frame the reviewer picked before asking to regenerate the rest."""
    out: dict[int, BlobRef] = {}
    for h in keep if isinstance(keep, list) else []:
        ref = await ctx.blobs.get(str(h))
        idx = shot_index(ref.name) if ref is not None else None
        if ref is not None and idx is not None:
            out.setdefault(idx, ref)
    return out


class _Composite:
    """Runs other `comfy.*` executors inside this step, like views.qwen_checked."""
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

    def _gen(self, workflow: str) -> Executor:
        try:
            return self.resolve(f"comfy.{workflow}")
        except KeyError as e:
            raise StepError("comfy_bad_param", f"unknown comfy workflow: {workflow}") from e

    @staticmethod
    async def _plan(ctx: StepContext, inputs: Outputs) -> dict[str, Any]:
        refs = inputs.get("plan") or []
        if not refs:
            raise StepError("op_no_input", "input 'plan' (storyboard json) is empty")
        return json.loads(await asyncio.to_thread(ctx.blobs.path(refs[0].hash).read_bytes))


class StoryboardFramesExecutor(_Composite):
    kind = "storyboard.frames"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        t0 = time.perf_counter()
        plan = await self._plan(ctx, inputs)
        size = gen_size(str(plan.get("aspect") or "16:9"))
        style = _clean(params.get("style")) or str(plan.get("style") or "")
        char_refs = (inputs.get("characters") or [])[:MAX_REFS]
        char_imgs: list[Image.Image] = []
        for r in char_refs:
            char_imgs.append(await asyncio.to_thread(lambda p: Image.open(p).copy(), ctx.blobs.path(r.hash)))
        kept = await kept_frames(ctx, params.get("_keep"))
        shots = plan.get("shots") or []
        frames: list[BlobRef] = []
        report: list[dict[str, Any]] = []
        metas: list[dict[str, Any]] = []
        for i, shot in enumerate(shots):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            if shot["index"] in kept:
                frames.append(kept[shot["index"]])
                report.append({"id": shot["id"], "kept": True})
                continue
            refs = [char_imgs[k] for k in shot.get("refs") or [] if k < len(char_imgs)]
            prompt = frame_prompt(shot, style, bool(refs))
            ctx.log(f"{shot['id']}: {'klein' if refs else 'z-image'}")
            if refs:
                layout = await asyncio.to_thread(layout_canvas, refs, size, shot.get("framing", "medium"))
                lref = await _put_png(ctx, layout, f"{shot['id']}_layout.png")
                res = await self._gen(KLEIN).run(ctx, {"75:74.text": prompt}, {"image": [lref]})
            else:
                res = await self._gen(ZIMAGE).run(ctx, {"57:27.text": prompt, "57:13.width": size[0],
                                                        "57:13.height": size[1], "57:13.batch_size": 1}, {})
            outs = res.outputs.get("images") or []
            if not outs:
                raise StepError("comfy_no_output", f"{shot['id']}: generator returned no image", retryable=True)
            img = await asyncio.to_thread(lambda p: Image.open(p).convert("RGB"), ctx.blobs.path(outs[0].hash))
            img = await asyncio.to_thread(fit_cover, img, size)
            frames.append(await _put_png(ctx, img, f"{shot['id']}.png"))
            metas.append(res.meta)
            report.append({"id": shot["id"], "kept": False, "engine": "klein" if refs else "z-image",
                           "prompt": prompt, "seeds": res.meta.get("seeds")})
            ctx.progress(i + 1, len(shots), shot["id"])
        doc = {"type": "storyboard_frames", "size": list(size), "frames": report}
        cost = round(sum(meta_cost(m) for m in metas), 6)
        meta: dict[str, Any] = {"shots": len(shots), "kept": len(kept), "generated": len(metas),
                                "seconds": round(time.perf_counter() - t0, 2)}
        if cost:
            meta["cost_usd"] = cost
        return StepResult(outputs={"images": frames, "json": [await put_json(ctx, doc, "storyboard_frames.json")]},
                          meta=meta)


class StoryboardScoreExecutor(_Composite):
    kind = "storyboard.score"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        def on(k: str) -> bool:
            return str(params.get(k, False)).lower() in ("true", "1", "yes", "on")

        if not on("music") and not on("sfx"):
            return StepResult(outputs={}, meta={"skipped": True})
        plan = await self._plan(ctx, inputs)
        shots = plan.get("shots") or []
        audio: list[BlobRef] = []
        meta: dict[str, Any] = {}
        if on("music"):
            est = sum(float(s.get("duration") or 0) for s in shots)
            secs = round(min(240.0, max(20.0, est * 1.25 + 4)))
            cues = "; ".join(dict.fromkeys(s["music_cue"] for s in shots if s.get("music_cue")))[:300]
            tags = f"{plan['music']['prompt']}, instrumental, cinematic underscore, no vocals"
            ctx.log(f"music {secs}s: {tags} | {cues}")
            res = await self._gen("music_ace_step15").run(ctx, {"94.tags": tags + (f", {cues}" if cues else ""),
                                                                 "94.duration": secs, "94.bpm": plan["music"]["bpm"],
                                                                 "98.seconds": secs}, {})
            audio += [await _rename(ctx, r, f"music.{_ext(r)}") for r in (res.outputs.get("audio") or [])[:1]]
            meta["music_seconds"] = secs
        if on("sfx"):
            todo = [s for s in shots if s.get("sfx")][: int(params.get("max_sfx") or 6)]
            for i, s in enumerate(todo):
                res = await self._gen("sfx_stable_audio").run(ctx, {"6.text": s["sfx"], "11.seconds": 3.0,
                                                                     "11.batch_size": 1}, {})
                audio += [await _rename(ctx, r, f"sfx_{s['id']}.{_ext(r)}")
                          for r in (res.outputs.get("audio") or [])[:1]]
                ctx.progress(i + 1, len(todo), s["id"])
            meta["sfx"] = len(todo)
        return StepResult(outputs={"audio": audio} if audio else {}, meta=meta)


def _ext(ref: BlobRef) -> str:
    return (ref.name or "a.wav").rsplit(".", 1)[-1].lower()


async def _rename(ctx: StepContext, ref: BlobRef, name: str) -> BlobRef:
    got = await ctx.blobs.put_file(ctx.blobs.path(ref.hash), name=name, mime=ref.mime)
    return got.model_copy(update={"name": name})
