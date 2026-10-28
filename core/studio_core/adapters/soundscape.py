"""`claude.soundscape`: a scene/concept image (+ mood) -> one audio plan: a music cue (+ optional tense variant on the same
BPM/key), an ambience loop and a few environment SFX, each as a prompt ready for ACE-Step / Stable Audio."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query

PLAN_TYPE = "soundscape_plan"
# TextEncodeAceStepAudio1.5's keyscale combo (comfy_extras/nodes_ace.py).
KEYSCALES = [f"{root} {q}" for q in ("major", "minor")
             for root in ("C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B")]
BPM_RANGE = (50, 200)
DEFAULT_DB = {"music": -10.0, "music_tense": -10.0, "ambience": -16.0, "sfx": -6.0}
MUSIC_SUFFIX = ", instrumental, loopable game background music"
AMBIENCE_SUFFIX = ", continuous seamless ambient background loop, environment field recording, no music, no voices"
SFX_SUFFIX = ", single isolated game sound effect, clean, high quality"

SYSTEM = (
    "You are the audio director of a game studio. From a scene or concept image you design the scene's soundscape so "
    "that music, ambience and effects belong together. All prompts are English. Music prompts are comma-separated "
    "tags for a text-to-music model (genre, instruments, mood, energy; instrumental, no vocals, no tempo numbers). "
    "Ambience and effect prompts describe the sound itself for a text-to-audio model (source, material, space, "
    "distance), never music. Effects are short one-shots of things visible or implied in the scene."
)

PROMPT = """Design the soundscape of the scene in the image.
{mood}Return:
- `scene`: one sentence describing the place and mood you hear.
- `bpm` (integer {lo}-{hi}) and `keyscale` (one of: {keys}) shared by every music cue.
- `music`: the scene's main background cue (`label` = 1-2 lowercase words, `prompt`, `volume_db` suggested playback
  volume in dB, 0 = full, about -8..-14 for music).
- `tense`: a tense/combat variant of the same cue that layers or crossfades with it: same tempo and key, more
  percussion and urgency, same instrument family.
- `ambience`: one continuous background loop of the place (wind, water, crowd, machinery...), `volume_db` about
  -14..-20.
- `sfx`: exactly {n} distinct environment effects (`label` 1-2 lowercase words, `prompt`, `volume_db` about -3..-10)."""

_CUE = {"type": "object",
        "properties": {"label": {"type": "string"}, "prompt": {"type": "string"}, "volume_db": {"type": "number"}},
        "required": ["label", "prompt", "volume_db"]}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"scene": {"type": "string"}, "bpm": {"type": "integer"},
                   "keyscale": {"type": "string", "enum": KEYSCALES},
                   "music": _CUE, "tense": _CUE, "ambience": _CUE,
                   "sfx": {"type": "array", "items": _CUE}},
    "required": ["scene", "bpm", "keyscale", "music", "tense", "ambience", "sfx"],
}


def build_prompt(mood: str, n_sfx: int) -> str:
    m = f"Mood / direction from the designer: {mood.strip()}\n" if mood.strip() else ""
    return PROMPT.format(mood=m, lo=BPM_RANGE[0], hi=BPM_RANGE[1], keys=", ".join(KEYSCALES), n=n_sfx)


def _slug(text: str, fallback: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:24] or fallback


def _db(raw: Any, role: str) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_DB[role]
    return round(min(0.0, max(-40.0, v)), 1)


def _cue(raw: Any, role: str, fallback: str, suffix: str, taken: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not str(raw.get("prompt") or "").strip():
        return None
    label = _slug(raw.get("label") or fallback, fallback)
    base, k = label, 2
    while label in taken:
        label, k = f"{base}_{k}", k + 1
    taken.add(label)
    prompt = " ".join(str(raw["prompt"]).split()).rstrip(" .,")
    return {"role": role, "label": label, "prompt": prompt, "gen_prompt": prompt + suffix,
            "volume_db": _db(raw.get("volume_db"), role), "loop": role != "sfx"}


def normalize_plan(raw: Any, *, n_sfx: int, tense: bool, mood: str = "") -> dict[str, Any]:
    """Model reply -> the plan doc the recipe binds into ACE-Step / Stable Audio steps (prompts already suffixed)."""
    if not isinstance(raw, dict):
        raise StepError("claude_bad_output", "soundscape reply is not an object", retryable=True)
    try:
        bpm = int(round(float(raw.get("bpm"))))
    except (TypeError, ValueError):
        bpm = 100
    bpm = min(BPM_RANGE[1], max(BPM_RANGE[0], bpm))
    key = str(raw.get("keyscale") or "")
    key = key if key in KEYSCALES else "C major"
    taken: set[str] = set()
    music = _cue(raw.get("music"), "music", "main", MUSIC_SUFFIX, taken)
    if music is None:
        raise StepError("claude_bad_output", "soundscape has no music prompt", retryable=True, detail={"reply": raw})
    tense_cue = _cue(raw.get("tense"), "music_tense", "tense", MUSIC_SUFFIX, taken) if tense else None
    ambience = _cue(raw.get("ambience"), "ambience", "ambience", AMBIENCE_SUFFIX, set())
    if ambience is None:
        raise StepError("claude_bad_output", "soundscape has no ambience prompt", retryable=True, detail={"reply": raw})
    sfx_taken: set[str] = set()
    sfx = [c for c in (_cue(s, "sfx", f"sfx_{i + 1}", SFX_SUFFIX, sfx_taken)
                       for i, s in enumerate(raw.get("sfx") or [])) if c][:n_sfx]
    if not sfx:
        raise StepError("claude_bad_output", "soundscape has no sfx prompts", retryable=True, detail={"reply": raw})
    musics = [music] + ([tense_cue] if tense_cue else [])
    return {
        "type": PLAN_TYPE, "scene": str(raw.get("scene") or "").strip(), "mood": mood.strip(),
        "bpm": bpm, "keyscale": key, "music": musics, "ambience": ambience, "sfx": sfx,
        # Flat fields the recipe binds with ${steps.plan.json.<field>}.
        "music_prompts": [m["gen_prompt"] for m in musics],
        "music_labels": "\n".join(f"music_{m['label']}" for m in musics),
        "ambience_prompt": ambience["gen_prompt"],
        "ambience_label": f"ambience_{ambience['label']}",
        "sfx_prompts": [s["gen_prompt"] for s in sfx],
        "sfx_labels": "\n".join(f"sfx_{s['label']}" for s in sfx),
    }


class ClaudeSoundscapeExecutor:
    kind = "claude.soundscape"
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
        images = await load_images(ctx, inputs.get("images", []))
        if not images:
            raise StepError("op_no_input", "input 'images' (scene/concept image) is empty")
        n_sfx = max(1, min(8, int(params.get("sfx_count", 4))))
        tense = str(params.get("tense", True)).lower() not in ("false", "0", "no", "off", "")
        mood = str(params.get("mood") or "")
        q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": SCHEMA,
             "_timeout_s": params.get("_timeout_s")}
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "soundscape")
        reply = await ask(self.query, q, build_prompt(mood, n_sfx), str(ctx.job_dir), images[:1], ctx=ctx)
        plan = normalize_plan(reply.structured, n_sfx=n_sfx, tense=tense, mood=mood)
        summary = (f"{plan['scene']}\n{plan['bpm']} BPM, {plan['keyscale']}\n"
                   + "\n".join(f"[{c['role']}] {c['label']}: {c['prompt']}"
                               for c in [*plan["music"], plan["ambience"], *plan["sfx"]]))
        outputs = {"json": [await put_json(ctx, plan, "soundscape_plan.json")],
                   "text": [await ctx.blobs.put_bytes(summary.encode("utf-8"), name="soundscape_plan.txt",
                                                      mime="text/plain")]}
        ctx.progress(1, 1, "soundscape")
        return StepResult(outputs=outputs, meta={**reply.meta, "bpm": plan["bpm"], "keyscale": plan["keyscale"],
                                                 "scene": plan["scene"]})
