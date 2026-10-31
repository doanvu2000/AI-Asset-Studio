"""`claude.scene_layout`: Claude looks at the gathered assets and draws a small level as text rows (one character per
tile) plus prop spots and the spawn; `planner: procedural` skips Claude. Either way op.playable_scene gets a grid that
`normalize_layout` made playable (closed edges, spawn on a walkable cell, every area reachable)."""
from __future__ import annotations

import asyncio
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult, cache_enabled
from ..ops import playable_scene as ps
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query

SYSTEM = (
    "You are a game level designer making small, readable test levels for 2D games. Levels have a clear route, "
    "a few open areas worth exploring, props placed where they make sense (against walls, at crossings, on "
    "ledges), and nothing that traps the player."
)

PROMPT_TOPDOWN = """Design a {cols}x{rows} top-down level (one character per tile, {rows} rows of exactly {cols}
characters). Theme / notes: {brief}
The image shows the assets: the player, the textures (floor = walkable ground, path = walkable road, wall = solid
block) and the props numbered 0..{last}.
Characters: "#" wall (solid), "." floor, "=" path. The outer border is wall. Make 2-4 areas joined by paths, wall
blocks or short wall lines that read as buildings / rocks / ruins, and keep corridors at least 2 tiles wide.
`props`: up to {max_props} entries {{"prop": n, "x": column, "y": row}} on floor tiles (not on paths), grouped
sensibly. `spawn`: [x, y] of a floor or path tile near one edge."""

PROMPT_SIDEVIEW = """Design a {cols}x{rows} side-view platformer level (one character per tile, {rows} rows of exactly
{cols} characters, row 0 is the top). Theme / notes: {brief}
The image shows the assets: the player, the textures and the props numbered 0..{last}.
Characters: "#" solid ground / platform, " " air. The bottom row and the left/right columns are solid. Make an
uneven ground line, 4-7 floating platforms 3-6 tiles long, never more than 3 tiles above the surface you jump
from, and a route from left to right. `props`: up to {max_props} entries {{"prop": n, "x": column, "y": row}} on an
air tile directly above solid ground. `spawn`: [x, y] of an air tile standing on the ground near the left."""


def schema() -> dict[str, Any]:
    prop = {"type": "object", "properties": {"prop": {"type": "integer"}, "x": {"type": "integer"},
                                             "y": {"type": "integer"}}, "required": ["prop", "x", "y"]}
    return {"type": "object", "properties": {
        "rows": {"type": "array", "items": {"type": "string"}},
        "props": {"type": "array", "items": prop},
        "spawn": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
        "reason": {"type": "string"}}, "required": ["rows", "props", "spawn", "reason"]}


def build_prompt(style: str, cols: int, rows: int, brief: str, n_props: int) -> str:
    tpl = PROMPT_TOPDOWN if style == "topdown" else PROMPT_SIDEVIEW
    return tpl.format(cols=cols, rows=rows, brief=brief or "(none — a small varied test level)",
                      last=max(0, n_props - 1), max_props=min(ps.MAX_PROPS, max(0, n_props) * 3))


def usable(raw: Any, rows: int) -> bool:
    return isinstance(raw, dict) and isinstance(raw.get("rows"), list) and len(raw["rows"]) >= rows // 2


class ClaudeSceneLayoutExecutor:
    kind = "claude.scene_layout"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return cache_enabled(params)

    def cache_material(self, params: dict[str, Any]) -> str:
        return SYSTEM + PROMPT_TOPDOWN + PROMPT_SIDEVIEW

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        assets = await ps.read_doc(ctx, inputs.get("assets") or [], ps.ASSETS_TYPE, "assets")
        style = str(params.get("style") or assets.get("style") or "topdown")
        if style not in ps.STYLES:
            raise StepError("op_bad_params", f"style must be one of {', '.join(ps.STYLES)}")
        cols = ps._int(params, "cols", 32, 12, 96)
        rows = ps._int(params, "rows", 20, 8, 64)
        seed = ps._int(params, "seed", 0, 0, 2**31 - 1)
        planner = str(params.get("planner") or "claude")
        n_props = len(assets.get("props") or [])
        meta: dict[str, Any] = {"planner": planner}
        if planner == "procedural":
            layout = ps.procedural_layout(style, cols, rows, n_props, seed)
        else:
            brief = " ".join(str(params.get("brief") or "").split())
            images = await load_images(ctx, inputs.get("images", [])[:1])
            q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": schema(),
                 "_timeout_s": params.get("_timeout_s")}
            await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
            ctx.progress(0, 1, "layout")
            reply = await ask(self.query, q, build_prompt(style, cols, rows, brief, n_props), str(ctx.job_dir),
                              images, ctx=ctx)
            raw = reply.structured
            if not usable(raw, rows):
                raise StepError("claude_bad_output", "layout has no usable rows", retryable=True,
                                detail={"reply": raw})
            meta.update(reply.meta)
            layout = ps.normalize_layout(raw, style, cols, rows, n_props, planner="claude")
            meta["changed_cells"] = _changed(raw.get("rows") or [], layout["grid"])
        ctx.progress(1, 1, "layout")
        summary = f"{style} {cols}x{rows}, {len(layout['props'])} props, spawn {layout['spawn']}: {layout['reason']}"
        return StepResult(outputs={"json": [await put_json(ctx, layout, ps.LAYOUT_FILE)],
                                   "text": [await ctx.blobs.put_bytes(("\n".join(layout["grid"]) + "\n\n" + summary)
                                                                      .encode("utf-8"), name="scene_layout.txt",
                                                                      mime="text/plain")]},
                          meta={**meta, "props": len(layout["props"]), "grid": [cols, rows]})


def _changed(raw_rows: list[Any], grid: list[str]) -> int:
    """Cells normalize_layout had to change (border, reachability) — a quality signal for the plan."""
    n = 0
    for y, row in enumerate(grid):
        src = str(raw_rows[y]) if y < len(raw_rows) else ""
        n += sum(1 for x, c in enumerate(row) if (src[x] if x < len(src) else None) != c)
    return n
