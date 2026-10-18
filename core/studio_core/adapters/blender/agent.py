"""`blender.agent`: Claude drives the user's GUI Blender through the `mcp-for-blender` MCP server and exports a GLB."""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

from ...config import Settings
from ...contracts import Lane, Outputs, StepContext, StepError, StepResult
from ..claude import QueryFn, ask, put_json, sdk_query

MCP_NAME = "blender"
DEFAULT_TOOLS = ("get_addon_status", "get_scene_info", "execute_blender_code", "look")
# generate_3d may spend the user's credits and import_asset downloads from the web; both are opt-in per step.
OPTIONAL_TOOLS = ("search_assets", "import_asset", "generate_3d", "record_trajectory_feedback")

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, "exported": {"type": "boolean"},
                   "objects": {"type": "array", "items": {"type": "string"}}},
    "required": ["summary", "exported", "objects"],
    "additionalProperties": False,
}

SYSTEM = """You operate the user's open Blender through MCP tools. Start with get_addon_status and get_scene_info.
Work only inside a new collection named "{collection}"; never delete or modify objects outside it.
Look shader nodes up by type, never by name; set material colors on node inputs. Use `look` to check your work.
When finished, export ONLY your collection's objects as one GLB with execute_blender_code:
select them (deselect everything else) and call
bpy.ops.export_scene.gltf(filepath=r"{out}", export_format="GLB", use_selection=True, export_apply=True).
Then reply with the JSON result (exported=true only if that export call succeeded)."""


def mcp_command() -> tuple[str, list[str]]:
    exe = shutil.which("uvx") or str(Path.home() / ".local" / "bin" / ("uvx.exe" if os.name == "nt" else "uvx"))
    return exe, ["mcp-for-blender"]


def allowed_tools(extra: list[str] | None) -> list[str]:
    names = list(DEFAULT_TOOLS) + [t for t in (extra or []) if t in OPTIONAL_TOOLS]
    return [f"mcp__{MCP_NAME}__{t}" for t in dict.fromkeys(names)]


class BlenderAgentExecutor:
    """One job at a time: the GUI Blender is a single shared scene."""

    kind = "blender.agent"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query
        self._lock = asyncio.Lock()

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        task = str(params.get("task") or params.get("prompt") or "").strip()
        if not task:
            raise StepError("blender_agent_bad_params", "params.task is required")
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        out = ctx.job_dir / "agent.glb"
        await asyncio.to_thread(out.unlink, missing_ok=True)
        collection = f"studio_{ctx.step_id[:8]}"
        prompt = task
        meshes = inputs.get("mesh", [])
        if meshes:
            src = ctx.job_dir / "input.glb"
            await asyncio.to_thread(shutil.copyfile, ctx.blobs.path(meshes[0].hash), src)
            prompt += f"\n\nStart by importing this GLB into your collection: {src}"
        cmd, args = (str(params["mcp_command"]), list(params.get("mcp_args") or [])) if params.get("mcp_command") \
            else mcp_command()
        q = {"model": params.get("model") or "sonnet", "max_turns": int(params.get("max_turns", 40)),
             "system": SYSTEM.format(collection=collection, out=str(out)), "schema": RESULT_SCHEMA}
        servers = {MCP_NAME: {"type": "stdio", "command": cmd, "args": args}}
        t0 = time.perf_counter()
        if self._lock.locked():
            ctx.progress(0, 1, "waiting for GUI Blender")
        async with self._lock:
            ctx.progress(0, 1, "agent")
            try:
                async with asyncio.timeout(float(params.get("timeout_s", 900))):
                    reply = await ask(self.query, q, prompt, str(ctx.job_dir), mcp_servers=servers,
                                      allowed_tools=allowed_tools(params.get("tools")))
            except TimeoutError as e:
                raise StepError("blender_agent_timeout", "agent exceeded its time limit", retryable=True) from e
        result = reply.structured if isinstance(reply.structured, dict) else {"summary": reply.text}
        outputs: Outputs = {"json": [await put_json(ctx, result, "agent.json")]}
        exists = await asyncio.to_thread(out.exists)
        if exists:
            outputs["meshes"] = [await ctx.blobs.put_file(out, name="agent.glb", mime="model/gltf-binary")]
        elif params.get("require_mesh", True):
            raise StepError("blender_agent_no_output", f"agent finished without exporting a GLB: "
                            f"{str(result.get('summary'))[:300]}", detail={"result": result})
        ctx.progress(1, 1, "agent")
        return StepResult(outputs=outputs, meta={**reply.meta, "summary": result.get("summary"),
                                                 "collection": collection, "exported": exists,
                                                 "wall_s": round(time.perf_counter() - t0, 2)})
