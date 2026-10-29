"""`claude.anim_motion`: character image + action preset/hint -> one in-place motion prompt for Wan 2.2 TI2V, written so
the clip can be cut into sprite frames (static camera, flat backdrop, full body in frame). With `suggest` off the
preset/hint is used as is."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult
from ..ops.anim_video import ACTIONS, FACINGS, resolve_loop
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query

SUFFIX = (". The camera is completely static with no zoom and no pan, the plain flat grey background stays unchanged, "
          "the whole body stays inside the frame, the same character design, colors and art style as the image, "
          "smooth clear motion")

SYSTEM = (
    "You direct short image-to-video clips (Wan 2.2) that become 2D game sprite animations. The first frame is the "
    "character image on a plain flat background. The prompt must describe that character as it looks, then one "
    "single action performed in place, readable as a game animation: clear key poses, no camera move, no new objects, "
    "no other characters, no scenery, no text, no effects that cover the body."
)

PROMPT = """Write the motion prompt for this character.
Action: {action}{hint}
View: {view}; the character faces {facing}.
{loop}Return:
- `character`: one sentence naming the character, outfit, weapon/props and art style as they appear.
- `beats`: 2-5 short phrases, the key poses of the action in order.
- `prompt`: the final English prompt, 40-80 words, one paragraph: the character first (as in the image), then the
  action as a sequence of poses, staying in place. Plain sentences, no weights, no brackets, no mention of the model."""

LOOP_NOTE = {
    True: "The clip will loop as a game cycle, so the action must repeat steadily and end in the pose it started from.\n",
    False: "The clip plays once, so start from the image pose, do the action once, then settle back near that pose.\n",
}
VIEWS = {"side": "side view (platformer)", "topdown": "top-down 3/4 view (RPG)"}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"character": {"type": "string"}, "beats": {"type": "array", "items": {"type": "string"}},
                   "prompt": {"type": "string"}},
    "required": ["character", "beats", "prompt"],
}


def action_text(action: str, hint: str) -> str:
    return hint.strip() or ACTIONS.get(action, ACTIONS["custom"])["motion"] or ACTIONS["idle"]["motion"]


def build_prompt(action: str, hint: str, loop: bool, view: str, facing: str) -> str:
    h = f"\nDesigner's description (keep it): {hint.strip()}" if hint.strip() else ""
    return PROMPT.format(action=ACTIONS.get(action, ACTIONS["custom"])["motion"] or action, hint=h,
                         view=VIEWS.get(view, VIEWS["side"]), facing=FACINGS.get(facing, FACINGS["right"])["text"],
                         loop=LOOP_NOTE[loop])


def _clean(text: Any) -> str:
    return " ".join(str(text).split()).rstrip(" .,")


def finalize(raw: Any, action: str, hint: str, loop: bool) -> dict[str, Any]:
    if not isinstance(raw, dict) or not _clean(raw.get("prompt") or ""):
        raise StepError("claude_bad_output", "motion reply has no prompt", retryable=True, detail={"reply": raw})
    base = _clean(raw["prompt"])
    return {"source": "claude", "action": action, "loop": loop, "hint": hint.strip(),
            "character": _clean(raw.get("character") or ""),
            "beats": [_clean(b) for b in raw.get("beats") or [] if _clean(b)],
            "base_prompt": base, "prompt": base + SUFFIX}


def manual(action: str, hint: str, loop: bool) -> dict[str, Any]:
    base = "The character " + _clean(action_text(action, hint))
    return {"source": "user" if hint.strip() else "preset", "action": action, "loop": loop, "hint": hint.strip(),
            "character": "", "beats": [], "base_prompt": base, "prompt": base + SUFFIX}


def _flag(v: Any, default: bool) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() not in ("false", "0", "no", "off", "")


class ClaudeAnimMotionExecutor:
    kind = "claude.anim_motion"
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
        action = str(params.get("action") or "custom")
        if action not in ACTIONS:
            raise StepError("op_bad_param", f"action must be one of {list(ACTIONS)}, got {action!r}")
        hint = str(params.get("motion") or "")
        if action == "custom" and not hint.strip():
            raise StepError("op_bad_param", "action 'custom' needs a motion description")
        loop = resolve_loop(action, params.get("loop"))
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        meta: dict[str, Any] = {}
        if _flag(params.get("suggest"), True):
            images = await load_images(ctx, inputs.get("images", []))
            if not images:
                raise StepError("op_no_input", "input 'images' (character) is empty")
            q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": SCHEMA,
                 "_timeout_s": params.get("_timeout_s")}
            ctx.progress(0, 1, "motion")
            prompt = build_prompt(action, hint, loop, str(params.get("view") or "side"),
                                  str(params.get("facing") or "right"))
            reply = await ask(self.query, q, prompt, str(ctx.job_dir), images[:1], ctx=ctx)
            doc = finalize(reply.structured, action, hint, loop)
            meta = dict(reply.meta)
        else:
            doc = manual(action, hint, loop)
        outputs = {"json": [await put_json(ctx, doc, "anim_motion.json")],
                   "text": [await ctx.blobs.put_bytes(doc["prompt"].encode("utf-8"), name="anim_prompt.txt",
                                                      mime="text/plain")]}
        ctx.progress(1, 1, "motion")
        return StepResult(outputs=outputs, meta={**meta, "source": doc["source"], "prompt": doc["prompt"]})
