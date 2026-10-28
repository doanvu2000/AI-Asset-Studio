"""GDD text -> proposed asset-list rows, each bound to one of the available recipes (Claude, structured output)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..engine.manifest import ManifestRow, rows_from_proposal
from ..engine.recipe import BLOB_TYPES
from .claude import DEFAULT_MODEL, QueryFn, ask, sdk_query

MAX_ROWS = 30
MAX_GDD_CHARS = 60_000
TIMEOUT_S = 240
# The project style owns the look; a value the model put here would win over it (apply_style fills only empties).
STYLE_OWNED = {"style", "negative", "preset"}

SYSTEM = (
    "You are the art producer of a game studio. From a game design document you list the concrete art and audio "
    "assets the game needs and pick, for each, the pipeline (recipe) that makes it. Only use recipe ids from the "
    "catalog. Fill only inputs the catalog lists; leave file inputs (image/mesh/file/video) out, the owner attaches "
    "them. Prompts are English, describe the subject only (shape, material, colors, pose); never the art style, the "
    "project style is added automatically. One row = one asset; prefer fewer, important assets over filler."
)

PROMPT = """Recipe catalog:
{catalog}

{context}Game design document:
<<<
{gdd}
>>>

List at most {limit} assets, most important first. For each: `name` (short, unique, the language of the GDD is
fine), `recipe_id`, `inputs` as {{name, value}} pairs (values as strings), `notes` (why / which GDD part, one line;
mention a file the owner must attach)."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"rows": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "recipe_id": {"type": "string"},
            "inputs": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                "required": ["name", "value"],
            }},
            "notes": {"type": "string"},
        },
        "required": ["name", "recipe_id", "inputs", "notes"],
    }}},
    "required": ["rows"],
}


def _input_line(name: str, spec: dict[str, Any]) -> str:
    t = spec.get("type", "text")
    if t in BLOB_TYPES:
        return f"{name} ({t} file, owner attaches)"
    bits = [t]
    if spec.get("options"):
        bits.append("one of " + "|".join(str(o) for o in spec["options"]))
    if "min" in spec or "max" in spec:
        bits.append(f"{spec.get('min', '')}..{spec.get('max', '')}")
    if "default" in spec and spec["default"] not in (None, ""):
        bits.append(f"default {spec['default']}")
    label = f" - {spec['label']}" if spec.get("label") else ""
    return f"{name} ({', '.join(bits)}){label}"


def _own_inputs(r: dict[str, Any]) -> dict[str, Any]:
    return {n: s for n, s in (r.get("inputs") or {}).items() if n not in STYLE_OWNED}


def catalog(recipes: list[dict[str, Any]]) -> str:
    lines = []
    for r in recipes:
        desc = " ".join(str(r.get("description") or "").split())
        lines.append(f"- {r['id']}: {r.get('name') or r['id']}" + (f" - {desc}" if desc else ""))
        lines += [f"    {_input_line(n, s)}" for n, s in _own_inputs(r).items()]
    return "\n".join(lines)


def build_prompt(gdd: str, recipes: list[dict[str, Any]], limit: int, project: str = "",
                 existing: list[str] | None = None) -> str:
    ctx = f"Project: {project.strip()}\n\n" if project.strip() else ""
    if existing:
        ctx += "Already in the list (do not repeat): " + "; ".join(existing[:200]) + "\n\n"
    return PROMPT.format(catalog=catalog(recipes), context=ctx, gdd=gdd.strip()[:MAX_GDD_CHARS], limit=limit)


class ManifestPlanner:
    def __init__(self, work_dir: Path, query: QueryFn = sdk_query):
        self.work_dir = work_dir
        self.query = query

    async def propose(self, gdd: str, recipes: list[dict[str, Any]], *, limit: int = MAX_ROWS, project: str = "",
                      existing: list[str] | None = None, model: str | None = None
                      ) -> tuple[list[ManifestRow], dict[str, Any]]:
        limit = max(1, min(limit, MAX_ROWS))
        await asyncio.to_thread(self.work_dir.mkdir, parents=True, exist_ok=True)
        reply = await ask(self.query, {"model": model or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM,
                                       "schema": SCHEMA, "_timeout_s": TIMEOUT_S},
                          build_prompt(gdd, recipes, limit, project, existing), str(self.work_dir))
        specs = {r["id"]: _own_inputs(r) for r in recipes}
        raw = (reply.structured or {}).get("rows") if isinstance(reply.structured, dict) else None
        rows, dropped = rows_from_proposal(raw, specs, limit)
        return rows, {"dropped": dropped, "cost_usd": reply.meta.get("cost_usd"), "model": reply.meta.get("model")}
