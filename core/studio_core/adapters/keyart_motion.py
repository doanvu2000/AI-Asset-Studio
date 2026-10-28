"""`claude.keyart_motion`: key art (+ optional hint) -> one subtle-motion prompt for Wan 2.2 image-to-video. With
`suggest` off, or no Claude, the user's hint (or a safe default) is used as is."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query

DEFAULT_MOTION = ("The scene holds its composition while hair and cloth sway gently in a light breeze, soft particles "
                  "drift through the air and the light flickers subtly")
SUFFIX = ", smooth natural motion, the same characters, style and colors as the image, stable composition, high detail"
CAMERAS = ["static", "slow push-in", "slow pull-out", "slow pan left", "slow pan right", "slow orbit"]

SYSTEM = (
    "You direct motion for game key art that an image-to-video model (Wan 2.2) turns into a 5 second teaser. The "
    "first frame is the key art itself, so the prompt must describe what is already in the image and only add "
    "subtle, believable motion: hair, cloth, capes, flags, foliage, smoke, fire, water, glowing particles, light "
    "flicker, blinking, breathing, and at most one gentle camera move. Never add new characters, objects, text, "
    "cuts or scene changes, and never make characters walk, fight or leave their pose."
)

PROMPT = """Write the motion prompt for this key art.
{hint}{loop}Return:
- `scene`: one sentence naming the subject(s), setting and art style as they appear.
- `motion`: 2-5 short phrases, each one element of the image and how it moves.
- `camera`: one of {cameras}.
- `prompt`: the final English prompt, 40-90 words, one paragraph: subject and setting first (as in the image), then
  the motions, then the camera. Plain sentences, no weights, no brackets, no mention of the model."""

LOOP_NOTE = {
    "crossfade": ("The video will loop by blending its end into its start, so every motion must be cyclic and stay "
                  "in place (sway, flicker, drift that keeps coming back) and the camera must be static.\n"),
    "pingpong": "The video will loop by playing forward then backward, so motion should read well in reverse.\n",
    "none": "",
}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"scene": {"type": "string"}, "motion": {"type": "array", "items": {"type": "string"}},
                   "camera": {"type": "string", "enum": CAMERAS}, "prompt": {"type": "string"}},
    "required": ["scene", "motion", "camera", "prompt"],
}


def build_prompt(hint: str, loop: str) -> str:
    h = f"Motion the designer asked for (keep it): {hint.strip()}\n" if hint.strip() else ""
    return PROMPT.format(hint=h, loop=LOOP_NOTE.get(loop, ""), cameras=", ".join(CAMERAS))


def _clean(text: str) -> str:
    return " ".join(str(text).split()).rstrip(" .,")


def finalize(raw: Any, hint: str, loop: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or not _clean(raw.get("prompt") or ""):
        raise StepError("claude_bad_output", "motion reply has no prompt", retryable=True, detail={"reply": raw})
    camera = str(raw.get("camera") or "static")
    camera = camera if camera in CAMERAS else "static"
    base = _clean(raw["prompt"])
    if loop == "crossfade" and camera != "static":
        # A camera move cannot blend back into the first frame without a visible zoom/pan ghost.
        base += ". The camera stays still"
        camera = "static"
    return {"source": "claude", "scene": _clean(raw.get("scene") or ""),
            "motion": [_clean(m) for m in raw.get("motion") or [] if _clean(m)], "camera": camera,
            "hint": hint.strip(), "loop": loop, "base_prompt": base, "prompt": base + SUFFIX}


def manual(hint: str, loop: str) -> dict[str, Any]:
    base = _clean(hint) or DEFAULT_MOTION
    return {"source": "user" if hint.strip() else "default", "scene": "", "motion": [], "camera": "", "hint": hint.strip(),
            "loop": loop, "base_prompt": base, "prompt": base + SUFFIX}


def _flag(v: Any, default: bool) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() not in ("false", "0", "no", "off", "")


class ClaudeKeyartMotionExecutor:
    kind = "claude.keyart_motion"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return bool(params.get("cache"))

    def cache_material(self, params: dict[str, Any]) -> str:
        return SYSTEM + PROMPT + SUFFIX + json.dumps(SCHEMA, sort_keys=True)

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        hint = str(params.get("motion") or "")
        loop = str(params.get("loop") or "crossfade")
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        meta: dict[str, Any] = {}
        if _flag(params.get("suggest"), True):
            images = await load_images(ctx, inputs.get("images", []))
            if not images:
                raise StepError("op_no_input", "input 'images' (key art) is empty")
            q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": SCHEMA,
                 "_timeout_s": params.get("_timeout_s")}
            ctx.progress(0, 1, "motion")
            reply = await ask(self.query, q, build_prompt(hint, loop), str(ctx.job_dir), images[:1], ctx=ctx)
            doc = finalize(reply.structured, hint, loop)
            meta = dict(reply.meta)
        else:
            doc = manual(hint, loop)
        outputs = {"json": [await put_json(ctx, doc, "motion.json")],
                   "text": [await ctx.blobs.put_bytes(doc["prompt"].encode("utf-8"), name="motion_prompt.txt",
                                                      mime="text/plain")]}
        ctx.progress(1, 1, "motion")
        return StepResult(outputs=outputs, meta={**meta, "source": doc["source"], "camera": doc["camera"],
                                                 "prompt": doc["prompt"]})
