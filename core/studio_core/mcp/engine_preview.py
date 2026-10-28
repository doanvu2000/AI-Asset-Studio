from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ImageContent, TextContent

from studio_core.mcp.link import CoreError, CoreLink

POLL_S = 2.0


async def engine_preview(link: CoreLink, step_id: str, engine: str, timeout_s: float, include_image: bool,
                         ppu: float = 100.0) -> CallToolResult:
    try:
        job = await link.request("POST", f"/api/steps/{step_id}/engine-preview",
                                 json={"engine": engine, "ppu": ppu}, timeout=300)
        deadline = time.monotonic() + max(10.0, timeout_s)
        while job["status"] in ("queued", "running") and time.monotonic() < deadline:
            await asyncio.sleep(POLL_S)
            job = await link.request("GET", f"/api/engine-preview/jobs/{job['id']}")
        if job["status"] in ("queued", "running"):
            job = await link.request("POST", f"/api/engine-preview/jobs/{job['id']}/cancel")
            job["status"] = "timeout"
        content: list[Any] = []
        image = job.get("image")
        if include_image and image:
            png = await link.fetch_bytes(f"/api/blobs/{image['hash']}")
            content.append(ImageContent(type="image", data=base64.b64encode(png).decode(), mime_type="image/png"))
    except CoreError as e:
        raise ToolError(f"{e.code}: {e.message}") from e
    summary = {k: job.get(k) for k in ("id", "status", "engine", "export_dir", "target", "seconds", "errors",
                                      "warnings", "note")}
    summary["image_hash"] = image["hash"] if image else None
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(summary, ensure_ascii=False)), *content])
