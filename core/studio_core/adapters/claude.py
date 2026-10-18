"""`claude.task`: one-shot Claude Agent SDK call, isolated from personal settings, optional JSON schema + images."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from contextlib import aclosing
from typing import Any, AsyncIterator

from ..config import Settings
from ..contracts import BlobRef, Lane, Outputs, ResourceHealth, StepContext, StepError, StepResult

MAX_IMAGE_BYTES = 3_500_000
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


def build_options(params: dict[str, Any], cwd: str) -> Any:
    from claude_agent_sdk import ClaudeAgentOptions

    opts: dict[str, Any] = dict(
        setting_sources=[], strict_mcp_config=True, tools=[], allowed_tools=[], mcp_servers={},
        model=params.get("model") or "sonnet", max_turns=int(params.get("max_turns", 4)), cwd=cwd,
        env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"},
    )
    if params.get("system"):
        opts["system_prompt"] = str(params["system"])
    if params.get("schema"):
        opts["output_format"] = {"type": "json_schema", "schema": params["schema"]}
    return ClaudeAgentOptions(**opts)


class ClaudeResource:
    name = "claude"

    def __init__(self, settings: Settings, bus: Any = None):
        self.settings = settings
        self.bus = bus

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
    """Images are sent as base64 image content blocks over the SDK's streaming input, so no file tool is needed."""

    kind = "claude.task"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings):
        self.settings = settings

    def cacheable(self, params: dict[str, Any]) -> bool:
        return bool(params.get("cache"))

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def _load_images(self, ctx: StepContext, refs: list[BlobRef]) -> list[tuple[bytes, str]]:
        def read() -> list[tuple[bytes, str]]:
            return [(ctx.blobs.path(r.hash).read_bytes(), r.mime) for r in refs]

        return await asyncio.to_thread(read) if refs else []

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise StepError("claude_bad_params", "params.prompt is required")
        images = await self._load_images(ctx, inputs.get("images", []))
        content = await asyncio.to_thread(build_content, prompt, images)
        ctx.job_dir.mkdir(parents=True, exist_ok=True)
        options = build_options(params, str(ctx.job_dir))

        async def messages() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}

        t0 = time.perf_counter()
        ctx.progress(0, 1, "claude")
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
                            detail={"subtype": result.subtype, "errors": getattr(result, "errors", None)})
        if params.get("schema") and result.structured_output is None:
            raise StepError("claude_no_structured_output", "schema requested but no structured output",
                            retryable=True, detail={"text": (result.result or "")[:2000]})
        payload = result.structured_output if result.structured_output is not None else {
            "text": result.result if result.result is not None else "\n".join(texts)}
        blob = await ctx.blobs.put_bytes(json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"),
                                         name="result.json", mime="application/json")
        ctx.progress(1, 1, "claude")
        return StepResult(outputs={"json": [blob]}, meta={
            "cost_usd": result.total_cost_usd, "duration_ms": result.duration_ms,
            "duration_api_ms": result.duration_api_ms, "num_turns": result.num_turns,
            "model": options.model, "seconds": round(time.perf_counter() - t0, 2)})
