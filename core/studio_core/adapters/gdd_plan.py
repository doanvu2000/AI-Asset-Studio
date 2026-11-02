"""GDD text -> asset plan: each item is bound to a workflow (flows/*.yaml) or a single recipe, with the inputs Claude
could fill. The plan is a draft for the user to edit; nothing runs until it is started."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ..contracts import StepError
from ..engine.manifest import clean_inputs
from .claude import DEFAULT_MODEL, QueryFn, ask, sdk_query
from .gdd import MAX_GDD_CHARS, STYLE_OWNED, _input_line, catalog

MAX_ITEMS = 40
TIMEOUT_S = 240
# Audio/TTS is paused for the project, so these recipes never reach Claude nor the plan.
AUDIO_ID = re.compile(r"(^|[-_])(audio|tts|voice|sfx|music|soundscape|sound)([-_]|$)")


def is_audio(recipe_id: str) -> bool:
    return bool(AUDIO_ID.search(recipe_id))


SYSTEM = (
    "You are the art producer of a game studio. From a game design document you list the concrete assets the game "
    "needs (characters, enemies, levels, UI, icons, props) and pick for each the workflow or recipe that makes it. "
    "Prefer a workflow when it covers the whole asset (a character pack makes a full character). Only use ids from "
    "the catalogs. Fill only inputs the catalog lists; leave file inputs (image/mesh/file/video) out, the owner "
    "attaches them. Prompts are English, describe the subject only (shape, material, colors, pose), never the art "
    "style: the project style is added automatically. One item = one asset. List only assets the GDD names, or that are strictly required by something it names; "
    "no filler: never add weather, fonts, audio or music unless the GDD mentions them. Never plan audio, music, "
    "voice or sound assets."
)

PROMPT = """Workflows (a workflow runs several recipes for one asset):
{flows}

Recipes:
{recipes}

{context}Game design document:
<<<
{gdd}
>>>

List at most {limit} assets, most important first. For each: `name` (short, unique), `kind` ("flow" or "recipe"),
`target_id` (workflow or recipe id from the catalogs), `inputs` as {{name, value}} pairs (values as strings),
`notes` (why / which GDD part, one line; mention a file the owner must attach)."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "kind": {"type": "string", "enum": ["flow", "recipe"]},
            "target_id": {"type": "string"},
            "inputs": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                "required": ["name", "value"],
            }},
            "notes": {"type": "string"},
        },
        "required": ["name", "kind", "target_id", "inputs", "notes"],
    }}},
    "required": ["items"],
}


class PlanItem(BaseModel):
    id: str = ""
    name: str = Field(max_length=200)
    kind: Literal["flow", "recipe"]
    target_id: str = Field(max_length=120)
    inputs: dict[str, Any] = {}
    notes: str = Field("", max_length=4000)
    missing: list[str] = []


def flow_specs(d: Any) -> dict[str, dict[str, Any]]:
    return {n: {"type": s.type, "label": s.label, "default": s.default, "required": s.required}
            for n, s in d.inputs.items()}


def missing_inputs(specs: dict[str, dict[str, Any]], inputs: dict[str, Any]) -> list[str]:
    """Required inputs that are neither filled nor defaulted; files can only come from the owner."""
    return [n for n, s in specs.items()
            if n not in STYLE_OWNED and s.get("required", True) and s.get("default") in (None, "") and inputs.get(n) in (None, "")]


def has_audio_node(flow: Any) -> bool:
    """Workflow nodes cannot be optional, so one audio node makes the whole flow unusable while audio is paused."""
    return any(is_audio(n.recipe_id) for n in flow.nodes)


def flow_catalog(flows: list[Any]) -> str:
    lines = []
    for d in flows:
        desc = " ".join((d.description or "").split())
        lines.append(f"- {d.id}: {d.title}" + (f" - {desc}" if desc else ""))
        lines += [f"    {_input_line(n, s)}" for n, s in flow_specs(d).items() if n not in STYLE_OWNED]
    return "\n".join(lines)


def build_prompt(gdd: str, flows: list[Any], recipes: list[dict[str, Any]], limit: int, project: str = "") -> str:
    ctx = f"Project: {project.strip()}\n\n" if project.strip() else ""
    return PROMPT.format(flows=flow_catalog(flows) or "(none)", recipes=catalog(recipes), context=ctx,
                         gdd=gdd.strip()[:MAX_GDD_CHARS], limit=limit)


def items_from_proposal(raw: Any, flows: dict[str, dict[str, dict[str, Any]]],
                        recipes: dict[str, dict[str, dict[str, Any]]], limit: int
                        ) -> tuple[list[PlanItem], list[str]]:
    """`flows` / `recipes` = id -> input specs. Returns (items, notes about dropped ones)."""
    items: list[PlanItem] = []
    dropped: list[str] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()[:200]
        kind, tid = entry.get("kind"), str(entry.get("target_id") or "").strip()
        if not name:
            continue
        pool = flows if kind == "flow" else recipes if kind == "recipe" else None
        if pool is None or tid not in pool:
            dropped.append(f"{name}: unknown {kind or 'target'} {tid!r}")
            continue
        if len(items) >= limit:
            dropped.append(f"{name}: over the {limit}-item limit")
            continue
        pairs = [p for p in entry.get("inputs") or [] if isinstance(p, dict)]
        specs = {n: s for n, s in pool[tid].items() if n not in STYLE_OWNED}
        inputs = clean_inputs(specs, pairs)
        try:
            items.append(PlanItem(name=name, kind=kind, target_id=tid, inputs=inputs,
                                  notes=str(entry.get("notes") or "").strip()[:4000],
                                  missing=missing_inputs(pool[tid], inputs)))
        except ValidationError as e:
            dropped.append(f"{name}: {e.errors()[0]['msg']}")
    return items, dropped


class GddPlanner:
    def __init__(self, work_dir: Path, query: QueryFn = sdk_query):
        self.work_dir = work_dir
        self.query = query

    async def plan(self, gdd: str, flows: list[Any], recipes: list[dict[str, Any]], *, limit: int = MAX_ITEMS,
                   project: str = "", model: str | None = None) -> tuple[list[PlanItem], dict[str, Any]]:
        limit = max(1, min(limit, MAX_ITEMS))
        recipes = [r for r in recipes if not is_audio(str(r["id"]))]
        flows = [d for d in flows if not has_audio_node(d)]
        await asyncio.to_thread(self.work_dir.mkdir, parents=True, exist_ok=True)
        reply = await ask(self.query, {"model": model or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM,
                                       "schema": SCHEMA, "_timeout_s": TIMEOUT_S},
                          build_prompt(gdd, flows, recipes, limit, project), str(self.work_dir))
        raw = reply.structured.get("items") if isinstance(reply.structured, dict) else None
        if not isinstance(raw, list):
            raise StepError("claude_bad_plan", "Claude's reply has no item list", retryable=True)
        flow_pool = {d.id: flow_specs(d) for d in flows}
        recipe_pool = {r["id"]: r.get("inputs") or {} for r in recipes}
        items, dropped = items_from_proposal(raw, flow_pool, recipe_pool, limit)
        if not items:
            raise StepError("claude_empty_plan", "no usable asset in the plan" + (f": {dropped[0]}" if dropped else ""))
        return items, {"dropped": dropped, "cost_usd": reply.meta.get("cost_usd"), "model": reply.meta.get("model")}
