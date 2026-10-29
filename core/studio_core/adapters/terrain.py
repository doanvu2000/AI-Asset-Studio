"""`claude.terrain_plan`: a terrain idea -> region map (mountains, hills, plateaus, plains, valleys, lakes, sea, rivers
on a 0..1 map), four splat layer materials as seamless-texture prompts and an optional skybox prompt."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult, cache_enabled
from ..ops.terrain import PLAN_TYPE, REGION_TYPES, ROLES, clean_plan, _f
from .claude import DEFAULT_MODEL, QueryFn, ask, put_json, sdk_query

# The same framing texture-seamless gives Z-Image, so the layers come out tileable and shadow-free.
TEXTURE_SUFFIX = (", seamless tileable texture, one continuous surface photographed straight down from directly above, "
                  "orthographic, no perspective, flat even diffuse lighting, no shadows, no vignette, natural irregular "
                  "variation, every detail unique and non-repeating, no seams, no borders, fills the whole frame, game "
                  "terrain texture")
SKY_PREFIX = "360 degree panorama with equirectangular projection"
SKY_SUFFIX = ", equirectangular 360 panorama, seamless wrap-around"
SKY_NEGATIVE = "seam, frame, border, text, watermark, tilted horizon, characters, foreground objects"
ROLE_HINT = {"ground": "the main walkable ground covering most flat and gentle land (grass, sand, dirt, moss...)",
             "shore": "wet / low ground beside water and river banks (sand, mud, pebbles...)",
             "cliff": "bare rock on steep slopes and cliffs",
             "peak": "the highest ground above the snowline (snow, ice, or pale rock / ash when the climate is warm)"}

SYSTEM = (
    "You are a level designer who lays out natural 3D game terrain. From a short description you place landforms on a "
    "square map and choose the four ground materials of its splat map. Coordinates are fractions of the map: x 0 = "
    "west edge, 1 = east edge; y 0 = north edge, 1 = south edge. Heights are fractions of the map's height range. Keep "
    "the user's layout (where things are, what is next to what); fill in a believable rest. All text is English."
)

PROMPT = """Terrain to lay out: {idea}
{style}Return:
- `summary`: one sentence describing the terrain.
- `base`: elevation of the general lowland, 0..0.5 (about 0.15 when there is sea or lakes, higher on a highland).
- `water_level`: elevation of sea / lake surfaces, 0..0.5, or -1 when there is no standing water; keep it below
  `base` so the lowland stays dry.
- `snowline`: fraction 0..1 of the final height range above which the peak material lies (about 0.7 for snowy
  mountains, 1.1 for no snow at all).
- `regions`: 3-12 landforms. `type` is one of {types}; `x`, `y` centre; `rx`, `ry` radii (0.03..0.8, a region may run
  past the map edge, e.g. a sea along the south coast with y > 1); `angle` rotation in degrees; `height` 0..1 (peak
  height for mountains/hills/plateau top above base, flatness level for plain, depth for valley/lake/sea); `label`.
- `rivers`: 0-3 rivers, each `points` from source to mouth (3-8 [x, y] pairs ending in a lake, the sea or the map
  edge), `width` 0.003..0.02 and `depth` 0.01..0.06.
- `layers`: the four splat materials, each with `name` (1-3 words), `prompt` (a top-down ground texture description
  for a texture generator, material + detail + colour, 15-35 words, no camera words) and `tile_m` (metres one texture
  tile covers, 4..30):
  - `ground`: {ground}
  - `shore`: {shore}
  - `cliff`: {cliff}
  - `peak`: {peak}
- `sky`: one 40-80 word prompt for a 360 degree skybox panorama matching the terrain's climate and time of day
  (sky, clouds, distant horizon only), starting with "{sky}"."""

_XY = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
_LAYER = {"type": "object", "properties": {"name": {"type": "string"}, "prompt": {"type": "string"},
                                           "tile_m": {"type": "number"}},
          "required": ["name", "prompt", "tile_m"]}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"}, "base": {"type": "number"}, "water_level": {"type": "number"},
        "snowline": {"type": "number"},
        "regions": {"type": "array", "items": {
            "type": "object",
            "properties": {"type": {"type": "string", "enum": list(REGION_TYPES)}, "label": {"type": "string"},
                           "x": {"type": "number"}, "y": {"type": "number"}, "rx": {"type": "number"},
                           "ry": {"type": "number"}, "angle": {"type": "number"}, "height": {"type": "number"}},
            "required": ["type", "x", "y", "rx", "ry", "height"]}},
        "rivers": {"type": "array", "items": {
            "type": "object", "properties": {"points": {"type": "array", "items": _XY}, "width": {"type": "number"},
                                             "depth": {"type": "number"}},
            "required": ["points"]}},
        "layers": {"type": "object", "properties": {r: _LAYER for r in ROLES}, "required": list(ROLES)},
        "sky": {"type": "string"},
    },
    "required": ["summary", "base", "water_level", "snowline", "regions", "rivers", "layers", "sky"],
}


def build_prompt(idea: str, style: str) -> str:
    s = f"Visual style of the game: {style.strip()}\n" if style.strip() else ""
    return PROMPT.format(idea=idea.strip(), style=s, types=", ".join(REGION_TYPES), sky=SKY_PREFIX, **ROLE_HINT)


def _clean_text(v: Any) -> str:
    return " ".join(str(v or "").split()).rstrip(" .,")


def normalize_plan(raw: Any, *, idea: str, style: str = "", skybox: bool = False) -> dict[str, Any]:
    """Model reply -> plan doc for op.terrain_height plus the flat lists the recipe maps over
    (`layer_prompts`, `sky_prompts`, empty when no skybox was asked for)."""
    if not isinstance(raw, dict):
        raise StepError("claude_bad_output", "terrain plan reply is not an object", retryable=True)
    plan = clean_plan(raw)
    if not plan["regions"]:
        raise StepError("claude_bad_output", "terrain plan has no regions", retryable=True, detail={"reply": raw})
    layers: dict[str, Any] = {}
    src = raw.get("layers") if isinstance(raw.get("layers"), dict) else {}
    for role in ROLES:
        lay = src.get(role) if isinstance(src.get(role), dict) else {}
        prompt = _clean_text(lay.get("prompt"))
        if not prompt:
            raise StepError("claude_bad_output", f"terrain plan has no '{role}' layer prompt", retryable=True,
                            detail={"reply": raw})
        layers[role] = {"name": _clean_text(lay.get("name"))[:40] or role, "prompt": prompt,
                        "tile_m": _f(lay.get("tile_m"), 10.0, 1.0, 200.0)}
    look = f", {style.strip()}" if style.strip() else ""
    sky = _clean_text(raw.get("sky")) or f"{SKY_PREFIX}, open sky above {idea}"
    if not sky.lower().startswith("360"):
        sky = f"{SKY_PREFIX}, {sky}"
    return {
        "type": PLAN_TYPE, "idea": idea.strip(), "summary": _clean_text(raw.get("summary")), **plan,
        "layers": layers, "sky": sky,
        "layer_prompts": [layers[r]["prompt"] + look + TEXTURE_SUFFIX for r in ROLES],
        "layer_names": "\n".join(layers[r]["name"] for r in ROLES),
        "sky_prompts": [sky + look + SKY_SUFFIX] if skybox else [],
        "sky_negative": SKY_NEGATIVE,
    }


def _on(v: Any) -> bool:
    return str(v).strip().lower() not in ("false", "0", "no", "off", "")


class ClaudeTerrainPlanExecutor:
    kind = "claude.terrain_plan"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return cache_enabled(params)

    def cache_material(self, params: dict[str, Any]) -> str:
        return SYSTEM + PROMPT + json.dumps(SCHEMA, sort_keys=True)

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        idea = str(params.get("idea") or "").strip()
        if not idea:
            raise StepError("claude_bad_params", "params.idea is required")
        style = str(params.get("style") or "")
        q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": SCHEMA,
             "_timeout_s": params.get("_timeout_s")}
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "terrain plan")
        reply = await ask(self.query, q, build_prompt(idea, style), str(ctx.job_dir), ctx=ctx)
        plan = normalize_plan(reply.structured, idea=idea, style=style, skybox=_on(params.get("skybox", False)))
        summary = (f"{plan['summary']}\n"
                   + "\n".join(f"[{r['type']}] {r['label']} @ {r['x']:.2f},{r['y']:.2f} h={r['height']:.2f}"
                               for r in plan["regions"])
                   + f"\nrivers: {len(plan['rivers'])}\n"
                   + "\n".join(f"{k}: {v['name']} — {v['prompt']}" for k, v in plan["layers"].items()))
        outputs = {"json": [await put_json(ctx, plan, "terrain_plan.json")],
                   "text": [await ctx.blobs.put_bytes(summary.encode("utf-8"), name="terrain_plan.txt",
                                                      mime="text/plain")]}
        ctx.progress(1, 1, "terrain plan")
        return StepResult(outputs=outputs, meta={**reply.meta, "regions": len(plan["regions"]),
                                                 "rivers": len(plan["rivers"]), "summary": plan["summary"]})
