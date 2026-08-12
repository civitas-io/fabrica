"""A real, minimal MCP stdio server -- used by tests/mcp/test_client.py and
test_namespace.py as the "external" server MCPClient connects to, over a
real stdio subprocess boundary. Not a mock -- this is the actual `mcp`
library's server-side API (mcp.server.lowlevel.Server), run for real.

Invoked as: python -m tests.mcp.fixtures.echo_server
"""

from __future__ import annotations

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

_ADD_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
    "required": ["a", "b"],
}
_FAIL_SCHEMA = {"type": "object", "properties": {}}


async def _on_list_tools(ctx: object, params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(name="add", description="Add two numbers", input_schema=_ADD_SCHEMA),
            types.Tool(
                name="always_fails",
                description="Always returns is_error=True",
                input_schema=_FAIL_SCHEMA,
            ),
        ]
    )


async def _on_call_tool(ctx: object, params: types.CallToolRequestParams) -> types.CallToolResult:
    if params.name == "add":
        args = params.arguments or {}
        total = args["a"] + args["b"]
        return types.CallToolResult(content=[types.TextContent(type="text", text=str(total))])
    if params.name == "always_fails":
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="simulated failure")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=f"unknown tool: {params.name}")],
        is_error=True,
    )


async def main() -> None:
    server: Server[None] = Server(
        "echo-test-server", on_list_tools=_on_list_tools, on_call_tool=_on_call_tool
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
