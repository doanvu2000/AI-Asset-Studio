"""`studio-core agent …`: the MCP tools as a plain CLI for scripts and agents without MCP (same CoreLink, same
semantics: attach to the running core or spawn a private one)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from studio_core.mcp.server import StudioTools

GATES = ("human", "agent", "auto_first")


def add_parser(sub: Any) -> None:
    p = sub.add_parser("agent", help="drive the studio from a script/agent (recipes, run, outputs, status)")
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.set_defaults(log_level="warning")
    cmds = p.add_subparsers(dest="agent_cmd", required=True)
    cmds.add_parser("recipes", help="list recipes")
    d = cmds.add_parser("describe", help="input spec of one recipe")
    d.add_argument("recipe")
    cmds.add_parser("status", help="core / ComfyUI / Blender / lanes / missing models")
    r = cmds.add_parser("run", help="start a recipe run")
    r.add_argument("recipe")
    r.add_argument("--input", "-i", action="append", default=[], metavar="NAME=VALUE",
                   help="recipe input; VALUE is parsed as JSON when it can be (numbers, true, lists), file inputs "
                        "take absolute paths")
    r.add_argument("--project", default=None)
    r.add_argument("--title", default=None)
    r.add_argument("--gate", choices=GATES, default="auto_first")
    r.add_argument("--wait", action="store_true", help="wait for the end, then export outputs")
    r.add_argument("--no-cache", action="store_true", help="recompute every step instead of reusing cached results")
    r.add_argument("--timeout", type=float, default=3600)
    w = cmds.add_parser("wait", help="wait for a run")
    w.add_argument("run_id")
    w.add_argument("--timeout", type=float, default=3600)
    o = cmds.add_parser("outputs", help="export a run's outputs, print their paths")
    o.add_argument("run_id")
    ls = cmds.add_parser("runs", help="recent runs")
    ls.add_argument("--limit", type=int, default=20)


def _inputs(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep or not name:
            raise SystemExit(f"--input expects NAME=VALUE, got {pair!r}")
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        out[name] = value
    return out


async def _wait(tools: StudioTools, run_id: str, timeout: float) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        view = await tools.wait_run(run_id, min(600.0, max(1.0, deadline - loop.time())), None)
        if not view.get("timed_out") or loop.time() >= deadline:
            return view


async def _dispatch(args: argparse.Namespace, tools: StudioTools) -> Any:
    cmd = args.agent_cmd
    if cmd == "recipes":
        return await tools.list_recipes()
    if cmd == "describe":
        return await tools.describe_recipe(args.recipe)
    if cmd == "status":
        return await tools.system_status()
    if cmd == "runs":
        return await tools.list_runs(args.limit, False)
    if cmd == "outputs":
        return (await tools.get_outputs(args.run_id, False)).structured_content
    if cmd == "wait":
        return await _wait(tools, args.run_id, args.timeout)
    started = await tools.start_run(args.recipe, _inputs(args.input), args.title, args.gate, args.project,
                                   no_cache=args.no_cache)
    if not args.wait:
        return started
    view = await _wait(tools, started["run_id"], args.timeout)
    if view["status"] != "succeeded":
        return view
    return {**((await tools.get_outputs(started["run_id"], False)).structured_content or {}), "steps": view["steps"]}


def _text(cmd: str, res: Any) -> str:
    if cmd == "recipes":
        return "\n".join(f"{r['id']:<28} {r['name']}" for r in res["recipes"])
    if isinstance(res, dict) and "files" in res:
        lines = [f"run {res['run_id']}: {res['status']} -> {res.get('export_dir')}"]
        lines += [f"  {f['output']:<14} {f['path']}" for f in res["files"]]
        return "\n".join(lines)
    if isinstance(res, dict) and "steps" in res and "status" in res:
        lines = [f"run {res['run_id']}: {res['status']}" + (" (timed out)" if res.get("timed_out") else "")]
        lines += [f"  {s['key']:<16} {s['status']}" + (f"  {s['substatus']}" if s.get("substatus") else "")
                  for s in res["steps"]]
        lines.append(res.get("next", ""))
        return "\n".join(lines)
    return json.dumps(res, indent=1, ensure_ascii=False)


async def _amain(args: argparse.Namespace) -> int:
    from mcp.server.mcpserver.exceptions import ToolError

    from studio_core.config import load_settings
    from studio_core.mcp.link import CoreLink
    from studio_core.mcp.server import StudioTools

    link = CoreLink(load_settings(args.data_dir).data_dir)
    tools = StudioTools(link)
    try:
        res = await _dispatch(args, tools)
    except ToolError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        await link.close()
    print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else _text(args.agent_cmd, res))
    ok = not (isinstance(res, dict) and res.get("status") in ("failed", "cancelled", "interrupted"))
    return 0 if ok else 1


def run(args: argparse.Namespace) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return asyncio.run(_amain(args))
