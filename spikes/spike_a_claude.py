"""P0-a: claude-agent-sdk — auth, isolation from personal settings, JSON schema output, in-process tool, latency."""
import asyncio
import json
import os
import sys
import time

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ResultMessage, SystemMessage, TextBlock, ToolUseBlock,
    create_sdk_mcp_server, query, tool,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "prompts": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
        "style": {"type": "string"},
        "saw_personal_instructions": {"type": "boolean"},
    },
    "required": ["prompts", "style", "saw_personal_instructions"],
    "additionalProperties": False,
}


@tool("asset_lookup", "Return metadata of an asset by id", {"asset_id": str})
async def asset_lookup(args):
    return {"content": [{"type": "text", "text": json.dumps({"id": args["asset_id"], "kind": "sprite", "size": [256, 256]})}]}


async def run(label, prompt, **opts):
    t0 = time.perf_counter()
    first = None
    init = None
    result = None
    texts, tools_used = [], []
    async for msg in query(prompt=prompt, options=ClaudeAgentOptions(**opts)):
        if first is None:
            first = time.perf_counter() - t0
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            init = msg.data
        elif isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, TextBlock):
                    texts.append(b.text)
                elif isinstance(b, ToolUseBlock):
                    tools_used.append(b.name)
        elif isinstance(msg, ResultMessage):
            result = msg
    dt = time.perf_counter() - t0
    out = {
        "label": label,
        "total_s": round(dt, 2),
        "first_msg_s": round(first or 0, 2),
        "api_ms": getattr(result, "duration_api_ms", None),
        "cost_usd": getattr(result, "total_cost_usd", None),
        "is_error": getattr(result, "is_error", None),
        "structured": getattr(result, "structured_output", None),
        "result_text": (getattr(result, "result", None) or "")[:400],
        "tools_used": tools_used,
        "init_tools": (init or {}).get("tools"),
        "init_mcp": (init or {}).get("mcp_servers"),
        "init_model": (init or {}).get("model"),
        "init_plugins": (init or {}).get("plugins"),
        "init_slash": len((init or {}).get("slash_commands") or []),
        "api_key_source": (init or {}).get("apiKeySource"),
        "texts": [t[:300] for t in texts],
    }
    print(json.dumps(out, ensure_ascii=False, indent=1), flush=True)
    return out


async def main():
    print("ANTHROPIC_API_KEY set:", bool(os.environ.get("ANTHROPIC_API_KEY")), flush=True)
    base = dict(setting_sources=[], cwd=os.path.dirname(os.path.abspath(__file__)), permission_mode="default")

    await run(
        "schema-haiku",
        "Write 3 short image-generation prompts for a 'red health potion' game icon. "
        "Set saw_personal_instructions=true only if your system context contains instructions about "
        "'caveman', 'chủ nhân' or 'RTK'.",
        model="haiku", tools=[], allowed_tools=[], max_turns=3,
        output_format={"type": "json_schema", "schema": SCHEMA}, **base,
    )

    server = create_sdk_mcp_server("core", "0.1.0", tools=[asset_lookup])
    await run(
        "tool-sonnet",
        "Call asset_lookup with asset_id 'a42' and report its size in one sentence.",
        model="sonnet", tools=[], mcp_servers={"core": server},
        allowed_tools=["mcp__core__asset_lookup"], max_turns=4, **base,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
