"""Optional vision pass over a project QA report: Claude sees the whole set on one numbered contact sheet and writes
a short art-director summary plus a note per asset that stands out."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .claude import DEFAULT_MODEL, QueryFn, ask, sdk_query

MAX_VISION_ASSETS = 36
TIMEOUT_S = 240

SYSTEM = (
    "You are the art director of a game studio reviewing whether a set of game assets looks like one game: same "
    "palette family, consistent proportions/scale, the same outline/line weight and the same light direction. Be "
    "concrete and brief. Write in Vietnamese."
)

PROMPT = """The image is a contact sheet of {n} assets from the project {project}, numbered 1..{n} in the top-left of
each cell (cells are thumbnails, the gray background is not part of the assets).

Automatic checks already found (asset number: findings):
{findings}

Reference palette ({palette_source}): {palette}

Give a 2-4 sentence `summary` of how consistent the set is and the main problem to fix first. Then list in `assets`
only the assets that clearly break the set's look (`i` = cell number, `note` = what is off, one line), including
problems the automatic checks missed; agree or disagree with the automatic findings by what you see."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "assets": {"type": "array", "items": {
            "type": "object",
            "properties": {"i": {"type": "integer"}, "note": {"type": "string"}},
            "required": ["i", "note"],
        }},
    },
    "required": ["summary", "assets"],
}


def build_prompt(assets: list[dict[str, Any]], reference: dict[str, Any], project: str = "") -> str:
    lines = []
    for i, a in enumerate(assets, 1):
        found = "; ".join(x["message"] for x in a.get("issues") or []) or "ok"
        lines.append(f"{i}. {a.get('asset_name') or '?'}: {found}")
    return PROMPT.format(n=len(assets), project=project.strip() or "(unnamed)", findings="\n".join(lines),
                         palette=", ".join(reference.get("palette") or []) or "none",
                         palette_source=reference.get("palette_source") or "-")


def notes_by_index(entries: Any, count: int) -> dict[int, str]:
    out: dict[int, str] = {}
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and type(e.get("i")) is int and isinstance(e.get("note"), str):
            if 1 <= e["i"] <= count and e["note"].strip():
                out[e["i"] - 1] = e["note"].strip()
    return out


class ProjectQaReviewer:
    def __init__(self, work_dir: Path, query: QueryFn = sdk_query):
        self.work_dir = work_dir
        self.query = query

    async def review(self, sheet_png: bytes, assets: list[dict[str, Any]], reference: dict[str, Any], *,
                     project: str = "", model: str | None = None) -> dict[str, Any]:
        await asyncio.to_thread(self.work_dir.mkdir, parents=True, exist_ok=True)
        reply = await ask(self.query, {"model": model or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM,
                                       "schema": SCHEMA, "_timeout_s": TIMEOUT_S},
                          build_prompt(assets, reference, project), str(self.work_dir),
                          images=[(sheet_png, "image/png")])
        data = reply.structured if isinstance(reply.structured, dict) else {}
        notes = notes_by_index(data.get("assets"), len(assets))
        return {"summary": str(data.get("summary") or "").strip(),
                "notes": {assets[i]["version_id"]: n for i, n in notes.items()},
                "cost_usd": reply.meta.get("cost_usd"), "model": reply.meta.get("model")}
