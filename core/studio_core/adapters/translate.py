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
TIMEOUT_S = 90
PROMPT = """Translate each Vietnamese image-generation prompt below into a natural English prompt for an image model.
Keep every visual detail, keep the meaning of genre words (tu tiên = xianxia cultivation fantasy, kiếm hiệp = wuxia,
linh khí = spiritual qi aura, rương = treasure chest), keep any English parts and numbers as they are, add nothing.
Return one entry per input: `i` is the input's number, `text` its translation.

{listing}"""
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"texts": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "text": {"type": "string"}},
        "required": ["i", "text"],
    }}},
    "required": ["texts"],
}


def is_vietnamese(text: str) -> bool:
    return bool(VI_CHARS.search(text))


def by_index(entries: Any, count: int) -> dict[int, str]:
    """1-based `i` from the listing -> translation; out-of-range, duplicate or empty entries are dropped."""
    out: dict[int, str] = {}
    seen: set[int] = set()
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or type(e.get("i")) is not int or not isinstance(e.get("text"), str):
            continue
        k = e["i"] - 1
        if k in seen:
            out.pop(k, None)
            continue
        seen.add(k)
        if 0 <= k < count and e["text"].strip():
            out[k] = e["text"].strip()
    return out


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
            reply = await ask(self.query, {"model": DEFAULT_MODEL, "max_turns": 2, "schema": SCHEMA,
                                           "_timeout_s": TIMEOUT_S},
                              PROMPT.format(listing=listing), str(self.work_dir))
            got = by_index((reply.structured or {}).get("texts"), len(todo))
            async with self.lock:
                for k, dst in got.items():
                    self.cache[todo[k]] = dst
            meta["cost_usd"] = reply.meta.get("cost_usd")
        meta["cached"] = sum(1 for t in texts if is_vietnamese(t) and t in self.cache) - meta["translated"]
        return [self.cache.get(t, t) for t in texts], meta
