"""Claude Agent SDK executors, isolated from personal settings: `claude.task` (one-shot, optional JSON schema + images)
and `claude.brief` (short idea -> structured t2i brief)."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from pathlib import Path
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from ..config import Settings
from ..contracts import BlobRef, Lane, Outputs, ResourceHealth, StepContext, StepError, StepResult
from ..contracts import report_cost

MAX_IMAGE_BYTES = 3_500_000
DEFAULT_TIMEOUT_S = 300.0
MAX_IMAGE_SIDE = 1568
API_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def _image_block(data: bytes, mime: str) -> dict[str, Any]:
    if mime not in API_IMAGE_MIMES or len(data) > MAX_IMAGE_BYTES:
        from PIL import Image

        im = Image.open(io.BytesIO(data))
        im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        data, mime = buf.getvalue(), "image/png"
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(data).decode()}}


def build_content(prompt: str, images: list[tuple[bytes, str]]) -> list[dict[str, Any]]:
    return [_image_block(d, m) for d, m in images] + [{"type": "text", "text": prompt}]


_settings: Settings | None = None

# Pinned ids: the CLI's bare aliases follow whatever it ships with, and recipes should not change model silently.
MODEL_IDS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
DEFAULT_MODEL = "opus"
MODEL_CHOICES = list(MODEL_IDS)


def model_id(name: Any) -> str:
    key = str(name or DEFAULT_MODEL)
    return MODEL_IDS.get(key, key)


def cli_path() -> str | None:
    """The bundled SDK CLI is stripped from frozen builds, and the SDK refuses npm's claude.CMD on Windows."""
    exe = getattr(_settings, "claude_exe", None)
    return str(exe) if exe and Path(exe).is_file() else None


def build_options(params: dict[str, Any], cwd: str, *, mcp_servers: dict[str, Any] | None = None,
                  allowed_tools: list[str] | None = None) -> Any:
    from claude_agent_sdk import ClaudeAgentOptions

    opts: dict[str, Any] = dict(
        setting_sources=[], strict_mcp_config=True, tools=[], allowed_tools=allowed_tools or [],
        mcp_servers=mcp_servers or {}, model=model_id(params.get("model")),
        max_turns=int(params.get("max_turns", 4)), cwd=cwd, env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"},
    )
    if cli := cli_path():
        opts["cli_path"] = cli
    if params.get("system"):
        opts["system_prompt"] = str(params["system"])
    if params.get("schema"):
        opts["output_format"] = {"type": "json_schema", "schema": params["schema"]}
    return ClaudeAgentOptions(**opts)


@dataclass
class ClaudeReply:
    structured: Any
    text: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def payload(self) -> Any:
        return self.structured if self.structured is not None else {"text": self.text}


QueryFn = Callable[[list[dict[str, Any]], Any], Awaitable[ClaudeReply]]


async def sdk_query(content: list[dict[str, Any]], options: Any) -> ClaudeReply:
    """Images go as base64 content blocks over the SDK's streaming input, so no file tool is needed."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

    async def messages() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}

    t0 = time.perf_counter()
    result: Any = None
    texts: list[str] = []
    try:
        async with aclosing(query(prompt=messages(), options=options)) as stream:
            async for msg in stream:
                if isinstance(msg, AssistantMessage):
                    texts += [b.text for b in msg.content if isinstance(b, TextBlock)]
                elif isinstance(msg, ResultMessage):
                    result = msg
    except asyncio.CancelledError:
        raise
    except Exception as e:
        raise StepError("claude_failed", repr(e)[:500], retryable=True) from e
    if result is None:
        raise StepError("claude_no_result", "SDK stream ended without a result", retryable=True)
    if result.is_error:
        raise StepError("claude_error", str(result.result or result.subtype)[:500], retryable=True,
                        detail={"subtype": result.subtype, "errors": getattr(result, "errors", None),
                                "cost_usd": result.total_cost_usd})
    if getattr(options, "output_format", None) and result.structured_output is None:
        raise StepError("claude_no_structured_output", "schema requested but no structured output",
                        retryable=True, detail={"text": (result.result or "")[:2000], "cost_usd": result.total_cost_usd})
    return ClaudeReply(result.structured_output, result.result if result.result is not None else "\n".join(texts),
                       {"cost_usd": result.total_cost_usd, "duration_ms": result.duration_ms,
                        "duration_api_ms": result.duration_api_ms, "num_turns": result.num_turns,
                        "model": options.model, "seconds": round(time.perf_counter() - t0, 2)})


def timeout_limit(raw: Any) -> float | None:
    """Only an explicit numeric 0 disables the timeout; None/"" (unset form fields) fall back to the default."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    if value == 0:
        return None
    return value if value > 0 else DEFAULT_TIMEOUT_S


async def ask(query_fn: QueryFn, params: dict[str, Any], prompt: str, cwd: str,
              images: list[tuple[bytes, str]] | None = None, *, ctx: StepContext | None = None,
              **option_kw: Any) -> ClaudeReply:
    """With `ctx`, each answered call's cost (also of an error reply) is reported via ctx.add_cost."""
    content = await asyncio.to_thread(build_content, prompt, images or [])
    limit = timeout_limit(params.get("_timeout_s"))
    try:
        async with asyncio.timeout(limit):
            reply = await query_fn(content, build_options(params, cwd, **option_kw))
    except TimeoutError as e:
        raise StepError("claude_timeout", f"Claude did not answer within {limit:.0f}s", retryable=True) from e
    except StepError as e:
        if isinstance(e.detail, dict):
            await report_cost(ctx, e.detail.get("cost_usd"))
        raise
    await report_cost(ctx, reply.meta.get("cost_usd"))
    return reply


async def load_images(ctx: StepContext, refs: list[BlobRef]) -> list[tuple[bytes, str]]:
    def read() -> list[tuple[bytes, str]]:
        return [(ctx.blobs.path(r.hash).read_bytes(), r.mime) for r in refs]

    return await asyncio.to_thread(read) if refs else []


async def put_json(ctx: StepContext, payload: Any, name: str) -> BlobRef:
    return await ctx.blobs.put_bytes(json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"),
                                     name=name, mime="application/json")


class ClaudeResource:
    name = "claude"

    def __init__(self, settings: Settings, bus: Any = None):
        global _settings
        self.settings = settings
        self.bus = bus
        _settings = settings

    async def health(self) -> ResourceHealth:
        try:
            import claude_agent_sdk
        except ImportError as e:
            return ResourceHealth(name=self.name, status="down", detail={"error": repr(e)})
        return ResourceHealth(name=self.name, status="up",
                              detail={"sdk_version": getattr(claude_agent_sdk, "__version__", None), "managed": False})

    async def ensure_started(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class ClaudeTaskExecutor:
    """Outputs `json` (structured output, or {"text": …}) and, without a schema, also `text` (text/plain)."""

    kind = "claude.task"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return bool(params.get("cache"))

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise StepError("claude_bad_params", "params.prompt is required")
        images = await load_images(ctx, inputs.get("images", []))
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "claude")
        reply = await ask(self.query, params, prompt, str(ctx.job_dir), images, ctx=ctx)
        outputs = {"json": [await put_json(ctx, reply.payload, "result.json")]}
        if reply.structured is None:
            outputs["text"] = [await ctx.blobs.put_bytes(reply.text.encode("utf-8"), name="result.txt",
                                                         mime="text/plain")]
        ctx.progress(1, 1, "claude")
        return StepResult(outputs=outputs, meta=reply.meta)


BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "what the asset is, concrete nouns + key parts"},
        "style": {"type": "string", "description": "rendering/art style"},
        "view": {"type": "string", "description": "camera view and framing"},
        "palette": {"type": "array", "items": {"type": "string"}, "description": "3-6 named colors"},
        "negative": {"type": "string", "description": "comma-separated things to avoid"},
        "prompt": {"type": "string", "description": "final text-to-image prompt, one paragraph"},
    },
    "required": ["subject", "style", "view", "palette", "negative", "prompt"],
    "additionalProperties": False,
}

BRIEF_TARGETS = {
    "concept-2d": "a single 2D game asset (item, icon or prop) shown whole, centered, on a plain white background",
    "character-3d": "a single full-body game character seen from the front, standing upright in a neutral pose, "
                    "whole figure head to feet, plain white background; the image feeds image-to-3D reconstruction, "
                    "so avoid motion blur, cropped limbs, props detached from the body and busy backgrounds",
    "prop-3d": "a single game prop in three-quarter view, whole object visible, plain white background; the image "
               "feeds image-to-3D reconstruction, so keep a clean readable silhouette",
    "generic": "a single game asset image",
}

BRIEF_SYSTEM = (
    "You expand a short asset idea into a production brief for a fast text-to-image model (Z-Image Turbo, no "
    "negative prompt support). Keep the user's intent; add concrete shape, material and color detail, never "
    "extra characters or scenery. The final prompt is plain English, 40-90 words, comma-separated phrases, "
    "subject first, then style, palette, lighting, view. Do not mention the model or use weights/brackets."
)


def brief_prompt(idea: str, target: str, style: str) -> str:
    lines = [f"Idea: {idea}", f"Target: {BRIEF_TARGETS.get(target, BRIEF_TARGETS['generic'])}."]
    if style:
        lines.append(f"Required style: {style}.")
    return "\n".join(lines)


class ClaudeBriefExecutor:
    """Bind the result into a later step with `${steps.<id>.text}` or `${steps.<id>.json.<field>}`."""

    kind = "claude.brief"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return bool(params.get("cache"))

    def cache_material(self, params: dict[str, Any]) -> str:
        return BRIEF_SYSTEM + json.dumps(BRIEF_SCHEMA, sort_keys=True)

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        idea = str(params.get("idea") or "").strip()
        if not idea:
            raise StepError("claude_bad_params", "params.idea is required")
        target = str(params.get("target") or "generic")
        q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": BRIEF_SYSTEM, "schema": BRIEF_SCHEMA,
             "_timeout_s": params.get("_timeout_s")}
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "brief")
        reply = await ask(self.query, q, brief_prompt(idea, target, str(params.get("style") or "")),
                          str(ctx.job_dir), ctx=ctx)
        brief = reply.structured if isinstance(reply.structured, dict) else {}
        prompt = str(brief.get("prompt") or "").strip().rstrip(".").strip()
        if not prompt:
            raise StepError("claude_bad_output", "brief has no prompt", retryable=True, detail={"brief": brief})
        brief = {**brief, "idea": idea, "target": target}
        outputs = {"json": [await put_json(ctx, brief, "brief.json")],
                   "text": [await ctx.blobs.put_bytes(prompt.encode("utf-8"), name="prompt.txt", mime="text/plain")]}
        ctx.progress(1, 1, "brief")
        return StepResult(outputs=outputs, meta={**reply.meta, "prompt": prompt})
