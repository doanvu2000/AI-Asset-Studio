"""`claude.store_layout`: Claude looks at the key art and the game title and picks the focus box every store crop must
keep, the title font among the ones installed here, its colours/outline and where the title sits on wide and tall
capsules. op.store_kit renders it."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult
from ..ops import store_capsules as sc
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query

SYSTEM = (
    "You are the art director of a game studio preparing store-page art (Steam capsules, app store banners). "
    "Every capsule is a crop of one key art with the game title set on it. You keep the hero readable in every "
    "crop, put the title over the calmest part of the art, and choose type that fits the game's genre and style "
    "and stays legible at 462x174 px."
)

PROMPT = """The image is the key art of the game "{title}".
Return:
- `focus`: the box every crop must keep (the hero's face and upper body, or the main subject), as fractions 0-1 of
  the image width and height: x0, y0, x1, y1. Tight but complete.
- `font`: one of these installed fonts:
{fonts}
- `case`: upper | title | as_is (how the title is printed; never change its words).
- `fill` and `outline`: #rrggbb for the letters and their outline, contrasting with the art behind the title;
  `outline_width`: outline thickness as a fraction of the font size, 0-0.15.
- `shadow`: soft drop shadow behind the title. `plate`: darken the art behind the title with a gradient (busy art).
- `wide_anchor` (920x430 and 1232x706 capsules, the art is cropped to about 2:1 / 16:9): top | bottom | left | right,
  over open space, never over the focus; `wide_size`: title width as a fraction of the capsule width, 0.3-0.9.
- `tall_anchor` (748x896 and 600x900 capsules, a narrow vertical crop around the focus): top | bottom;
  `tall_size`: 0.5-0.95.
- `reason`: one sentence on the choice."""

_BOX = {"type": "object", "properties": {k: {"type": "number"} for k in ("x0", "y0", "x1", "y1")},
        "required": ["x0", "y0", "x1", "y1"]}


def schema(fonts: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "focus": _BOX,
            "font": {"type": "string", **({"enum": fonts} if fonts else {})},
            "case": {"type": "string", "enum": list(sc.CASES)},
            "fill": {"type": "string"}, "outline": {"type": "string"}, "outline_width": {"type": "number"},
            "shadow": {"type": "boolean"}, "plate": {"type": "boolean"},
            "wide_anchor": {"type": "string", "enum": list(sc.WIDE_ANCHORS)}, "wide_size": {"type": "number"},
            "tall_anchor": {"type": "string", "enum": list(sc.TALL_ANCHORS)}, "tall_size": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["focus", "font", "case", "fill", "outline", "outline_width", "shadow", "plate", "wide_anchor",
                     "wide_size", "tall_anchor", "tall_size", "reason"],
    }


def build_prompt(title: str, fonts: list[str]) -> str:
    lines = "\n".join(f"  - {f}: {sc.FONT_FILES[f][0]}" for f in fonts) or "  - default: plain sans"
    return PROMPT.format(title=title, fonts=lines)


class ClaudeStoreLayoutExecutor:
    kind = "claude.store_layout"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return bool(params.get("cache"))

    def cache_material(self, params: dict[str, Any]) -> str:
        return SYSTEM + PROMPT + json.dumps(sorted(sc.available_fonts()))

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        images = await load_images(ctx, inputs.get("images", []))
        if not images:
            raise StepError("op_no_input", "input 'images' (key art) is empty")
        title = " ".join(str(params.get("title") or "").split())
        if not title:
            raise StepError("op_bad_params", "title is empty")
        fonts = sc.font_choices(title)
        q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM,
             "schema": schema(fonts), "_timeout_s": params.get("_timeout_s")}
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "layout")
        reply = await ask(self.query, q, build_prompt(title, fonts), str(ctx.job_dir), images[:1], ctx=ctx)
        if not isinstance(reply.structured, dict) or sc.normalize_focus(reply.structured.get("focus")) is None:
            raise StepError("claude_bad_output", "layout has no usable focus box", retryable=True,
                            detail={"reply": reply.structured})
        layout = sc.normalize_layout(reply.structured, title)
        summary = (f"focus {layout['focus']}, font {layout['font']}, {layout['case']}, fill {layout['fill']} / "
                   f"outline {layout['outline']}, wide {layout['wide_anchor']}, tall {layout['tall_anchor']}\n"
                   f"{layout['reason']}")
        outputs = {"json": [await put_json(ctx, layout, "store_layout.json")],
                   "text": [await ctx.blobs.put_bytes(summary.encode("utf-8"), name="store_layout.txt",
                                                      mime="text/plain")]}
        ctx.progress(1, 1, "layout")
        return StepResult(outputs=outputs, meta={**reply.meta, "font": layout["font"], "focus": layout["focus"],
                                                 "wide_anchor": layout["wide_anchor"]})
