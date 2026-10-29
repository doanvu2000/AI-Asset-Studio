"""Reports MCP tool calls to the core (POST /api/agent/activity) so the desktop app can show what the agent does.
Best effort: a core that is down or older never makes a tool call fail."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import PurePath
from typing import Any

from studio_core.mcp.link import CoreLink

log = logging.getLogger(__name__)

POST_TIMEOUT_S = 2.0
MAX_SUMMARY = 300
_PATHISH = re.compile(r"^([a-zA-Z]:[\\/]|[\\/]{1,2}|~[\\/])")
_HEX = re.compile(r"^[0-9a-f]{32,64}$")
# Free text an agent passes (GDD, instructions) can be long or private; only its size is reported.
_TEXT_KEYS = {"gdd_text", "instructions", "text", "description"}


def _short(v: Any) -> str:
    if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if _PATHISH.match(v):
            return PurePath(v.replace("\\", "/")).name or "…"
        if _HEX.match(v):
            return v[:10]
        return v if len(v) <= 48 else v[:45] + "…"
    if isinstance(v, (list, tuple)):
        inner = ", ".join(_short(x) for x in list(v)[:3])
        return f"[{inner}{', …' if len(v) > 3 else ''}]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}={_short(x)}" for k, x in list(v.items())[:6]) + "}"
    return type(v).__name__


def summarize(args: dict[str, Any]) -> str:
    parts = []
    for k, v in args.items():
        if v in (None, "", [], {}):
            continue
        parts.append(f"{k}=<{len(v)} chars>" if k in _TEXT_KEYS and isinstance(v, str) else f"{k}={_short(v)}")
    s = " ".join(parts)
    return s if len(s) <= MAX_SUMMARY else s[:MAX_SUMMARY - 1] + "…"


def run_id_of(args: dict[str, Any], result: Any = None) -> str | None:
    for src in (args, result if isinstance(result, dict) else None):
        if src and isinstance(src.get("run_id"), str):
            return src["run_id"]
    return None


class ActivityReporter:
    def __init__(self, link: CoreLink) -> None:
        self.link = link
        self.session = uuid.uuid4().hex[:16]
        self.client = "agent"
        self.client_version = ""
        self.announced = False
        # Test doubles of the link record every request; only a real HTTP link has a core to report to.
        self.enabled = isinstance(link, CoreLink)

    def set_client(self, info: Any) -> None:
        name = getattr(info, "name", None) if info is not None else None
        if name:
            self.client = str(name)[:80]
            self.client_version = str(getattr(info, "version", "") or "")[:40]

    async def post(self, phase: str, *, spawn: bool = True, **fields: Any) -> None:
        if not self.enabled:
            return
        if not spawn and getattr(self.link, "_client", None) is None:
            return
        body = {"session": self.session, "client": self.client, "client_version": self.client_version,
                "phase": phase, **{k: v for k, v in fields.items() if v is not None}}
        try:
            await asyncio.wait_for(self.link.request("POST", "/api/agent/activity", json=body, timeout=POST_TIMEOUT_S),
                                   POST_TIMEOUT_S + 1)
        except Exception as e:
            log.debug("activity %s not delivered: %s", phase, e)

    async def connect(self, *, spawn: bool) -> None:
        if self.announced or not self.enabled:
            return
        if not spawn and getattr(self.link, "_client", None) is None:
            return
        self.announced = True
        await self.post("connect", spawn=spawn, summary=f"{self.client} {self.client_version}".strip())

    async def disconnect(self) -> None:
        if self.announced:
            await self.post("disconnect", spawn=False)
