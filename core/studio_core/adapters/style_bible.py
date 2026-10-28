"""Claude vision writes the style bible's prompt suffix and negative notes from the project's approved assets (one
numbered contact sheet) and the measured style stats."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .claude import DEFAULT_MODEL, QueryFn, ask, sdk_query

MAX_VISION_ASSETS = 24
TIMEOUT_S = 240

SYSTEM = (
    "You are the art director of a game studio. You write the house style of a game as a short text-to-image prompt "
    "fragment so new assets match the approved ones. Describe only the look (rendering technique, line work, "
    "shading, lighting, palette, proportions), never a subject, character or object. Prompts are English."
)

PROMPT = """The image is a contact sheet of {n} approved assets from the project {project}, numbered 1..{n} (the gray
background and the numbers are not part of the assets).

Measured from the pixels:
- palette (dominant colors): {palette}
- outline: {outline}
- main light from: {light}
- rendering, rough pixel-based guess (check it on the sheet): {render}

Write:
- `suffix`: one comma-separated English prompt fragment (12-40 words) that makes a text-to-image model draw new
  assets in exactly this style. Style words only, no subject. Mention the palette in words (e.g. "muted earthy
  greens and warm browns"), not hex codes.
- `negative`: comma-separated English things to avoid that would break this style (5-15 short items).
- `pixel_art`: true if the assets are pixel art (visible pixel grid, hard pixel edges), false otherwise.
- `summary`: 1-3 sentences in Vietnamese describing the style for the team."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"suffix": {"type": "string"}, "negative": {"type": "string"}, "pixel_art": {"type": "boolean"},
                   "summary": {"type": "string"}},
    "required": ["suffix", "negative", "pixel_art", "summary"],
}


def build_prompt(stats: dict[str, Any], n: int, project: str = "") -> str:
    outline = ("yes" + (f", about {stats['outline_weight']} px per 256 px height" if stats.get("outline_weight")
                        else "")) if stats.get("outlined") else "none" if stats.get("outlined") is False else "unclear"
    return PROMPT.format(n=n, project=project.strip() or "(unnamed)",
                         palette=", ".join(stats.get("palette") or []) or "unknown", outline=outline,
                         light=stats.get("light_label") or "unclear",
                         render="pixel art" if stats.get("pixel_art") else "painted / smooth")


class StyleBibleWriter:
    def __init__(self, work_dir: Path, query: QueryFn = sdk_query):
        self.work_dir = work_dir
        self.query = query

    async def write(self, sheet_png: bytes, stats: dict[str, Any], n: int, *, project: str = "",
                    model: str | None = None) -> dict[str, Any]:
        await asyncio.to_thread(self.work_dir.mkdir, parents=True, exist_ok=True)
        reply = await ask(self.query, {"model": model or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM,
                                       "schema": SCHEMA, "_timeout_s": TIMEOUT_S},
                          build_prompt(stats, n, project), str(self.work_dir), images=[(sheet_png, "image/png")])
        data = reply.structured if isinstance(reply.structured, dict) else {}
        return {"suffix": str(data.get("suffix") or "").strip(), "negative": str(data.get("negative") or "").strip(),
                "summary": str(data.get("summary") or "").strip(),
                "pixel_art": data.get("pixel_art") if isinstance(data.get("pixel_art"), bool) else None,
                "cost_usd": reply.meta.get("cost_usd"), "model": reply.meta.get("model")}
