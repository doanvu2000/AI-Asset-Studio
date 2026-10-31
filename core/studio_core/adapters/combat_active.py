"""`claude.combat_active`: Claude reads a contact sheet of the attack clips (one direction, numbered frames) and names
the frames where the blow can hurt. Skipped (no call, empty output) when the user wrote the active frames, the clip
list has no attack, or `claude` is off; op.frame_data then falls back to its CPU heuristic."""
from __future__ import annotations

import asyncio
import io
import json
from typing import Any

from PIL import Image

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult, cache_enabled
from ..ops.executors import _read_json
from ..ops.frame_data import ACTIVE_TYPE, clip_role, contact_sheet, parse_active, parse_names
from .claude import DEFAULT_MODEL, QueryFn, ask, put_json, sdk_query

SYSTEM = (
    "You are a combat animator for 2D action games. You read sprite animation strips and say on which frames a "
    "blow actually connects: the weapon or effect is fully extended through the hit zone. Wind-up and recovery "
    "frames are not active. Frames are numbered from 0 in the small black label on each cell.")

PROMPT = """The image has one row per attack clip; each cell shows its frame number. Rows, top to bottom:
{rows}
For each clip return the active (damaging) frame numbers: usually 1-3 consecutive frames at the peak of the swing,
thrust, shot or burst, never frame 0 unless the clip really starts mid-blow. Use only numbers that are visible."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"clips": {"type": "array", "items": {
        "type": "object",
        "properties": {"clip": {"type": "string"}, "active": {"type": "array", "items": {"type": "integer"}},
                       "note": {"type": "string"}},
        "required": ["clip", "active"]}}},
    "required": ["clips"],
}


def to_active_doc(raw: Any, sizes: dict[str, int]) -> dict[str, Any]:
    """Only asked clips and in-range frames count; a clip Claude skipped keeps the CPU heuristic."""
    clips: list[dict[str, Any]] = []
    for row in (raw.get("clips") if isinstance(raw, dict) else None) or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("clip") or "").strip()
        n = sizes.get(name)
        if n is None or any(c["clip"] == name for c in clips):
            continue
        frames = sorted({int(f) for f in row.get("active") or [] if isinstance(f, int) and 0 <= f < n})
        if frames:
            clips.append({"clip": name, "active": frames, "note": str(row.get("note") or "")[:200]})
    if not clips:
        raise StepError("claude_bad_output", "no clip with valid active frames", retryable=True, detail={"reply": raw})
    return {"type": ACTIVE_TYPE, "clips": clips}


class ClaudeCombatActiveExecutor:
    kind = "claude.combat_active"
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
        from ..export.sheet import layout

        skip = StepResult(outputs={"json": []}, meta={"asked": 0})
        if parse_active(params.get("active")) or str(params.get("claude", True)).strip().lower() in ("false", "0", "no"):
            return skip
        sheets, metas = inputs.get("sheet") or [], inputs.get("meta") or []
        if not sheets or not metas:
            raise StepError("op_no_input", "inputs 'sheet' and 'meta' are required")
        meta = await _read_json(ctx, metas[0])
        try:
            lay = layout(meta, directional_rows=True)
        except (KeyError, ValueError, TypeError) as e:
            raise StepError("op_bad_input", f"bad sheet meta: {e}") from e
        attack = parse_names(params.get("attack_clips"))
        idle = str(params.get("idle_clip") or "idle").strip().lower()
        sizes = {t.clip: len(t.cells) for t in lay.tracks
                 if t.clip.lower() != idle and clip_role(t.clip, attack) == "attack"}
        if not sizes:
            return skip

        def draw() -> bytes:
            with Image.open(ctx.blobs.path(sheets[0].hash)) as im:
                strip = contact_sheet(im.convert("RGBA"), lay, list(sizes))
            buf = io.BytesIO()
            strip.save(buf, "PNG")
            return buf.getvalue()

        png = await asyncio.to_thread(draw)
        rows = "\n".join(f"{i + 1}. {name} ({n} frames)" for i, (name, n) in enumerate(sizes.items()))
        q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": SCHEMA,
             "_timeout_s": params.get("_timeout_s")}
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "active")
        reply = await ask(self.query, q, PROMPT.format(rows=rows), str(ctx.job_dir), [(png, "image/png")], ctx=ctx)
        doc = to_active_doc(reply.structured, sizes)
        ctx.progress(1, 1, "active")
        return StepResult(outputs={"json": [await put_json(ctx, doc, "combat_active.json")]},
                          meta={**reply.meta, "asked": len(sizes), "answered": [c["clip"] for c in doc["clips"]]})
