"""`claude.ui_layout`: Claude lays out one game UI screen as a flat node tree over the kit pieces it is shown;
`planner: template` (or an uploaded layout JSON) skips Claude. op.ui_screen validates, fits and renders it."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult, cache_enabled
from ..ops import ui_screen as us
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query

SYSTEM = (
    "You are a senior game UI designer laying out mobile game screens. You build clear hierarchy, big thumb-friendly "
    "buttons (at least 8% of the screen height for the main action), generous spacing, and you keep everything well "
    "inside the safe area. You only use the element types you are given."
)

PROMPT = """Lay out the "{screen}" screen of a {orientation} mobile game.
Game / theme / wording notes: {brief}
The reference canvas is {w}x{h} px; the root is the safe area, so the layout must also work on 20:9 phones and 4:3
tablets: everything is placed in fractions of its parent, never pixels.

Return `nodes`, a flat list; each node:
- `name`: unique PascalCase id (it becomes the GameObject / Control name), `parent`: name of an earlier node or "" for
  the safe-area root.
- `type`: panel (framed window/background), button, tab, slot (inventory/item cell), bar (progress/slider, `value`
  0-1), label (text only), icon (a picture: `icon` = one or two English words, e.g. coin, gem, star, settings, back,
  close, lock, trophy, chest, heart, play), list (a grid of identical items: `count`, `columns`, `item` = slot | button
  | tab | icon, optional `item_texts` / `item_icons` arrays, `font_size`).
- `rect`: [x, y, w, h] as fractions 0-1 of the parent (top-left origin). Children stay inside the parent.
- buttons/tabs may have `text` and/or `icon`; labels have `text`. `font_size` in reference px (24-140), `align`
  left|center|right, `color` #rrggbb for text. Text is short (max ~20 characters), in the language of the notes.
- `variant` (optional): which kit piece of that type to use, 0 = first.
Kit pieces available (type: count): {kit}
A typical {screen} screen looks like this (reuse or improve; don't copy blindly):
{example}
Keep 20-60 nodes. Put the most important action largest and lowest-thumb-reachable; the header at the top."""


def schema() -> dict[str, Any]:
    node = {"type": "object", "properties": {
        "name": {"type": "string"}, "parent": {"type": "string"},
        "type": {"type": "string", "enum": list(us.NODE_TYPES)},
        "rect": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
        "text": {"type": "string"}, "icon": {"type": "string"}, "font_size": {"type": "number"},
        "align": {"type": "string", "enum": list(us.ALIGNS)}, "color": {"type": "string"},
        "value": {"type": "number"}, "variant": {"type": "integer"}, "count": {"type": "integer"},
        "columns": {"type": "integer"}, "item": {"type": "string"},
        "item_texts": {"type": "array", "items": {"type": "string"}},
        "item_icons": {"type": "array", "items": {"type": "string"}}},
        "required": ["name", "parent", "type", "rect"]}
    return {"type": "object", "properties": {"nodes": {"type": "array", "items": node}, "reason": {"type": "string"}},
            "required": ["nodes", "reason"]}


def kit_summary(doc: Any) -> str:
    counts: dict[str, int] = {}
    for e in (doc or {}).get("elements") or [] if isinstance(doc, dict) else []:
        if not e.get("state"):
            k = str(e.get("name") or "element").split("_")[0]
            counts[k] = counts.get(k, 0) + 1
    if not counts:
        return "panel: 1, button: 2, tab: 1, slot: 1, bar: 2 (frame + fill) — a plain default kit"
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))


def build_prompt(screen: str, orientation: str, brief: str, kit_doc: Any) -> str:
    w, h = us.REF[orientation]
    example = json.dumps(us.template(screen)["nodes"], ensure_ascii=False, separators=(",", ":"))
    return PROMPT.format(screen=screen, orientation=orientation, brief=brief or "(none — generic casual game)", w=w,
                         h=h, kit=kit_summary(kit_doc), example=example)


class ClaudeUiLayoutExecutor:
    kind = "claude.ui_layout"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return cache_enabled(params)

    def cache_material(self, params: dict[str, Any]) -> str:
        return SYSTEM + PROMPT

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        screen = str(params.get("screen") or "home")
        if screen not in us.SCREENS:
            raise StepError("op_bad_params", f"unknown screen '{screen}' (use {', '.join(us.SCREENS)})")
        orientation = str(params.get("orientation") or "portrait")
        title = " ".join(str(params.get("title") or "").split())
        brief = " ".join(f"{title}. {params.get('brief') or ''}".strip(" .").split()) if title else             " ".join(str(params.get("brief") or "").split())
        kit_doc = None
        if inputs.get("kit"):
            try:
                kit_doc = json.loads(await asyncio.to_thread(ctx.blobs.path(inputs["kit"][0].hash).read_bytes))
            except (OSError, ValueError) as e:
                raise StepError("op_bad_input", f"kit is not JSON: {e}") from e
        given = inputs.get("layout") or []
        planner = str(params.get("planner") or "claude")
        meta: dict[str, Any] = {"planner": planner}
        if given:
            try:
                raw = json.loads(await asyncio.to_thread(ctx.blobs.path(given[0].hash).read_bytes))
            except (OSError, ValueError) as e:
                raise StepError("op_bad_input", f"layout is not JSON: {e}") from e
            meta["planner"] = "file"
            reason = "layout file given"
        elif planner == "template":
            raw, reason = us.template(screen, brief.split(".")[0][:24] if params.get("title_from_brief") else ""), \
                "built-in template"
        else:
            images = await load_images(ctx, inputs.get("images", [])[:1])
            q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": schema(),
                 "_timeout_s": params.get("_timeout_s")}
            await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
            ctx.progress(0, 1, "layout")
            reply = await ask(self.query, q, build_prompt(screen, orientation, brief, kit_doc), str(ctx.job_dir),
                              images, ctx=ctx)
            raw = reply.structured if isinstance(reply.structured, dict) else None
            if not raw or not isinstance(raw.get("nodes"), list) or not raw["nodes"]:
                raise StepError("claude_bad_output", "layout has no nodes", retryable=True,
                                detail={"reply": reply.structured})
            reason = str(raw.get("reason") or "")
            meta.update(reply.meta)
        layout = us.normalize_layout(raw, screen, orientation)
        layout["reason"] = reason
        ctx.progress(1, 1, "layout")
        summary = f"{screen} ({orientation}): {len(layout['nodes'])} nodes — {reason}"
        return StepResult(outputs={"json": [await put_json(ctx, layout, us.LAYOUT_FILE)],
                                   "text": [await ctx.blobs.put_bytes(summary.encode("utf-8"), name="ui_layout.txt",
                                                                      mime="text/plain")]},
                          meta={**meta, "nodes": len(layout["nodes"])})
