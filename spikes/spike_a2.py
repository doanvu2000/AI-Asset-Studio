import asyncio, time, os
from claude_agent_sdk import query, ClaudeAgentOptions, SystemMessage, ResultMessage
async def one(**kw):
    t0=time.perf_counter(); mcp=None
    async for m in query(prompt="Say OK.", options=ClaudeAgentOptions(setting_sources=[], tools=[], model="haiku", max_turns=1, **kw)):
        if isinstance(m, SystemMessage) and m.subtype=="init": mcp=[x["name"] for x in m.data.get("mcp_servers",[])]
        if isinstance(m, ResultMessage): r=m.result
    print(kw, round(time.perf_counter()-t0,2), mcp, r)
async def main():
    await one(strict_mcp_config=True)
    await one(env={"ENABLE_CLAUDEAI_MCP_SERVERS":"false"})
asyncio.run(main())
