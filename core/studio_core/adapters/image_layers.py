"""Claude steps of image-layers: `claude.layers_caption` describes the input for Qwen-Image-Layered (the user's own
prompt skips Claude), `claude.layers_names` names the separated layers from their numbered contact sheet. Both fall
back (generic caption, layer_NN) when Claude fails, since neither is worth failing a 10-minute GPU run for."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query

DEFAULT_CAPTION = "A 2D game art illustration on a plain white background."

CAPTION_SYSTEM = (
    "You caption images for an image-decomposition model that splits a picture into RGBA layers. Describe only what "
    "is visible, plainly and concretely: subject(s), pose, clothing/props, colours, art style and the background.")
CAPTION_PROMPT = ("Write one English caption, 30-70 words, for this image. Name every distinct element (character, "
                  "props, effects, background). No opinions, no camera jargon, no mention of layers.")
CAPTION_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"caption": {"type": "string"}},
                                  "required": ["caption"]}

NAMES_SYSTEM = ("You name the layers of a picture that was split into RGBA layers, for an artist's layer panel and "
                "for game-engine node names.")
NAMES_PROMPT = """The image shows {count} layers, numbered 1..{count} from back to front, each on a checkerboard.
Return `names`: exactly {count} short English snake_case names (1-3 words, e.g. back_cape_arms, legs_boots,
face_torso, background), in the same order, each saying what that layer contains."""


def names_schema(count: int) -> dict[str, Any]:
    return {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"},
                                                       "minItems": count, "maxItems": count}},
            "required": ["names"]}


def _flag(v: Any, default: bool = True) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() not in ("false", "0", "no", "off", "")


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


class ClaudeLayersCaptionExecutor:
    kind = "claude.layers_caption"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return bool(params.get("cache")) or bool(_clean(params.get("prompt")))

    def cache_material(self, params: dict[str, Any]) -> str:
        return CAPTION_SYSTEM + CAPTION_PROMPT + DEFAULT_CAPTION

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        given = _clean(params.get("prompt"))
        meta: dict[str, Any] = {}
        if given:
            doc = {"source": "user", "prompt": given}
        else:
            images = await load_images(ctx, inputs.get("images", []))
            if not images:
                raise StepError("op_no_input", "input 'images' is empty")
            await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
            q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": CAPTION_SYSTEM,
                 "schema": CAPTION_SCHEMA, "_timeout_s": params.get("_timeout_s")}
            ctx.progress(0, 1, "caption")
            try:
                reply = await ask(self.query, q, CAPTION_PROMPT, str(ctx.job_dir), images[:1], ctx=ctx)
                text = _clean((reply.structured or {}).get("caption") if isinstance(reply.structured, dict) else "")
                meta = dict(reply.meta)
            except StepError as e:
                ctx.log(f"Claude caption failed ({e.code}); using a generic caption", "warning")
                text = ""
            doc = {"source": "claude" if text else "default", "prompt": text or DEFAULT_CAPTION}
        ctx.progress(1, 1, "caption")
        text_ref = await ctx.blobs.put_bytes(doc["prompt"].encode("utf-8"), name="caption.txt", mime="text/plain")
        return StepResult(outputs={"json": [await put_json(ctx, doc, "caption.json")], "text": [text_ref]},
                          meta={**meta, **doc})


class ClaudeLayersNamesExecutor:
    kind = "claude.layers_names"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return bool(params.get("cache")) or not _flag(params.get("enabled"))

    def cache_material(self, params: dict[str, Any]) -> str:
        return NAMES_SYSTEM + NAMES_PROMPT

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        info_refs = inputs.get("info") or []
        if not info_refs:
            raise StepError("op_no_input", "input 'info' is empty")
        info = json.loads(await asyncio.to_thread(ctx.blobs.path(info_refs[0].hash).read_bytes))
        count = len(info.get("layers") or [])
        names: list[str] = []
        meta: dict[str, Any] = {}
        source = "default"
        if _flag(params.get("enabled")) and count:
            images = await load_images(ctx, inputs.get("images", []))
            if not images:
                raise StepError("op_no_input", "input 'images' (numbered layer sheet) is empty")
            await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
            q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": NAMES_SYSTEM,
                 "schema": names_schema(count), "_timeout_s": params.get("_timeout_s")}
            ctx.progress(0, 1, "names")
            try:
                reply = await ask(self.query, q, NAMES_PROMPT.format(count=count), str(ctx.job_dir), images[:1],
                                  ctx=ctx)
                raw = reply.structured.get("names") if isinstance(reply.structured, dict) else None
                if isinstance(raw, list) and len(raw) == count:
                    names, source = [_clean(n) for n in raw], "claude"
                else:
                    ctx.log(f"Claude returned {len(raw) if isinstance(raw, list) else 0} names for {count} layers; "
                            "keeping layer_NN", "warning")
                meta = dict(reply.meta)
            except StepError as e:
                ctx.log(f"Claude layer naming failed ({e.code}); keeping layer_NN", "warning")
        doc = {"source": source, "names": names}
        ctx.progress(1, 1, "names")
        return StepResult(outputs={"json": [await put_json(ctx, doc, "layer_names.json")]},
                          meta={**meta, "source": source, "names": names})
