from __future__ import annotations

from pathlib import Path
from typing import Any

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.translate import Translator, is_vietnamese


def test_detects_vietnamese() -> None:
    assert is_vietnamese("rương báu bằng gỗ")
    assert is_vietnamese("ĐÁ")
    assert not is_vietnamese("wooden treasure chest, 3/4 view")


async def test_translates_only_vietnamese_once(tmp_path: Path) -> None:
    calls: list[str] = []

    async def query(content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        text = next(c["text"] for c in content if c.get("type") == "text")
        calls.append(text)
        n = sum(1 for line in text.splitlines() if line[:1].isdigit() and ". " in line)
        return ClaudeReply({"texts": [f"en{i}" for i in range(n)]}, "", {"cost_usd": 0.01})

    tr = Translator(tmp_path, query)
    out, meta = await tr.to_english(["cây cổ thụ", "a rock", "cây cổ thụ", "rương báu"])
    assert out == ["en0", "a rock", "en0", "en1"]
    assert meta["translated"] == 2 and len(calls) == 1 and "2. rương báu" in calls[0]
    out2, meta2 = await tr.to_english(["rương báu"])
    assert out2 == ["en1"] and meta2 == {"translated": 0, "cached": 1} and len(calls) == 1
