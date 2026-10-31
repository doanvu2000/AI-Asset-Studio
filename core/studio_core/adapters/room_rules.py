"""`claude.room_rules`: Claude proposes how each room kind of a roguelike kit (start, combat, corridor, treasure, boss)
is built: door counts, straight corridors, interior shape, props and how often it appears. op.room_kit clamps the answer
to what its generator can honour. Skipped (no call, empty output) when `claude` is off; op.room_kit then uses its
default rules, as it does when this step fails."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult, cache_enabled
from ..ops import playable_scene as ps
from ..ops import room_kit as rk
from .claude import DEFAULT_MODEL, QueryFn, ask, put_json, sdk_query

SYSTEM = (
    "You are a level designer for roguelike dungeon crawlers. You decide what kinds of rooms a procedural dungeon "
    "needs and how doors connect them so every generated map is fun, readable and has no dead-end doors.")

PROMPT = """Plan the room kinds for a {style} {genre} kit of {count} sample rooms. Theme / notes: {brief}
Every room is the same size. Doors sit at fixed spots on these sides: {sides}. Props available: {props}.
Return one entry for each kind: start, combat, corridor, treasure, boss.
- min_doors / max_doors: 1..{n_sides}. A boss or treasure room is usually a dead end (1 door); combat rooms are hubs.
- straight: true for a corridor that joins two opposite doors.
- shape: interior layout, one of {shapes} (arena = open middle).
- props: 0..{max_props} props scattered in the room; share: how common the kind is (0.05..1).
- arena: 0..6, the radius of the cleared middle (boss fights).
Keep the start room calm and the boss room open."""

def schema() -> dict[str, Any]:
    """Built on call: ops.room_kit may still be importing when this package is."""
    return {
        "type": "object",
        "properties": {
            "rooms": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(rk.KINDS)},
                    "min_doors": {"type": "integer"}, "max_doors": {"type": "integer"},
                    "straight": {"type": "boolean"}, "shape": {"type": "string", "enum": list(rk.SHAPES)},
                    "props": {"type": "integer"}, "share": {"type": "number"}, "arena": {"type": "integer"},
                    "note": {"type": "string"}},
                "required": ["kind", "min_doors", "max_doors", "shape"]}},
            "reason": {"type": "string"}},
        "required": ["rooms", "reason"],
    }


def build_prompt(style: str, count: int, brief: str, prop_names: list[str]) -> str:
    sides = rk.sides_for(style)
    return PROMPT.format(style=style, genre="dungeon top-down" if style == "topdown" else "platformer side-view",
                         count=count, brief=brief or "(none, a classic dungeon)", sides=", ".join(sides),
                         props=", ".join(prop_names) or "none", n_sides=len(sides), shapes=", ".join(rk.SHAPES),
                         max_props=rk.MAX_PROPS)


class ClaudeRoomRulesExecutor:
    kind = "claude.room_rules"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return cache_enabled(params)

    def cache_material(self, params: dict[str, Any]) -> str:
        return SYSTEM + PROMPT + json.dumps(schema(), sort_keys=True)

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        if str(params.get("claude", True)).strip().lower() in ("false", "0", "no"):
            return StepResult(outputs={"json": []}, meta={"asked": 0})
        scene = await ps.read_doc(ctx, inputs.get("scene") or [], ps.SCENE_TYPE, "scene")
        style = scene.get("style") if scene.get("style") in rk.STYLES else "topdown"
        count = ps._int(params, "rooms", 20, 5, 64)
        brief = " ".join(str(params.get("brief") or "").split())
        names = [str(k.get("name")) for k in scene.get("prop_kinds") or [] if isinstance(k, dict)]
        q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": schema(),
             "_timeout_s": params.get("_timeout_s")}
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "rules")
        reply = await ask(self.query, q, build_prompt(style, count, brief, names), str(ctx.job_dir), ctx=ctx)
        doc = rk.normalize_rules(reply.structured, style)
        if doc["source"] != "claude":
            raise StepError("claude_bad_output", "no usable room rule in the reply", retryable=True,
                            detail={"reply": reply.structured})
        ctx.progress(1, 1, "rules")
        return StepResult(outputs={"json": [await put_json(ctx, doc, rk.RULES_FILE)]},
                          meta={**reply.meta, "asked": len(rk.KINDS)})
