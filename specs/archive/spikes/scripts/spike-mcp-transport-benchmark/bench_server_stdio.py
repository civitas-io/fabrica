"""Real MCP server over stdio -- one 'echo' tool, no artificial delay,
so the benchmark measures real transport/serialization overhead, not a
simulated workload.
"""
from __future__ import annotations

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

_SCHEMA = {"type": "object", "properties": {"n": {"type": "number"}}, "required": ["n"]}


async def _on_list_tools(ctx: object, params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[types.Tool(name="echo", description="Echoes n back", input_schema=_SCHEMA)]
    )


async def _on_call_tool(ctx: object, params: types.CallToolRequestParams) -> types.CallToolResult:
    n = (params.arguments or {}).get("n", 0)
    return types.CallToolResult(content=[types.TextContent(type="text", text=str(n))])


async def main() -> None:
    server: Server[None] = Server(
        "bench-stdio-server", on_list_tools=_on_list_tools, on_call_tool=_on_call_tool
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
