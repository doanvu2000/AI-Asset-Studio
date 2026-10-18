"""Vietnamese → English for image prompts: local models (Z-Image, Qwen, Flux) drop specialised Vietnamese terms
("rương", "tu tiên", "linh khí") that they follow fine in English."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from .claude import DEFAULT_MODEL, QueryFn, ask, sdk_query

VI_CHARS = re.compile(r"[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]", re.IGNORECASE)
MAX_TEXTS = 200
PROMPT = """Translate each Vietnamese image-generation prompt below into a natural English prompt for an image model.
Keep every visual detail, keep the meaning of genre words (tu tiên = xianxia cultivation fantasy, kiếm hiệp = wuxia,
linh khí = spiritual qi aura, rương = treasure chest), keep any English parts and numbers as they are, add nothing.
Return one entry per input, same order.

{listing}"""
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"texts": {"type": "array", "items": {"type": "string"}}},
    "required": ["texts"],
}


def is_vietnamese(text: str) -> bool:
    return bool(VI_CHARS.search(text))


class Translator:
    def __init__(self, work_dir: Path, query: QueryFn = sdk_query):
        self.work_dir = work_dir
        self.query = query
        self.cache: dict[str, str] = {}
        self.lock = asyncio.Lock()

    async def to_english(self, texts: list[str]) -> tuple[list[str], dict[str, Any]]:
        todo = list(dict.fromkeys(t for t in texts if is_vietnamese(t) and t not in self.cache))
        meta: dict[str, Any] = {"translated": len(todo), "cached": 0}
        if todo:
            listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(todo))
            await asyncio.to_thread(self.work_dir.mkdir, parents=True, exist_ok=True)
            reply = await ask(self.query, {"model": DEFAULT_MODEL, "max_turns": 2, "schema": SCHEMA},
                              PROMPT.format(listing=listing), str(self.work_dir))
            got = (reply.structured or {}).get("texts") or []
            async with self.lock:
                for src, dst in zip(todo, got):
                    if isinstance(dst, str) and dst.strip():
                        self.cache[src] = dst.strip()
            meta["cost_usd"] = reply.meta.get("cost_usd")
        meta["cached"] = sum(1 for t in texts if is_vietnamese(t) and t in self.cache) - meta["translated"]
        return [self.cache.get(t, t) for t in texts], meta
