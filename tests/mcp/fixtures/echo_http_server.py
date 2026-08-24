"""A real, minimal MCP server exposed over Streamable HTTP -- used by
tests/mcp/test_client.py to prove MCPClient's new streamable_http transport
(closing python-civitas GH #26) against an actual running server, not a
mock. Reuses the exact same tool logic as echo_server.py (the stdio
sibling) so both transports are tested against equivalent behavior.

Deliberately built directly on mcp.server.lowlevel.Server +
Server.streamable_http_app() rather than reusing fabrica.mcp.server's own
FabricaMCPServer -- that class's HTTP mode requires a real authenticator
(ServerTransportConfig's own construction-time check), which is correct
for a real deployment but out of scope here: this fixture exists to prove
the MCPClient <-> streamable_http_client transport wiring, not auth.

Run directly with `python -m tests.mcp.fixtures.echo_http_server <port>`
for manual/local testing; imported and started programmatically (via a
background asyncio task, mirroring test_server.py's own _RunningHttpServer
pattern) by test_client.py.
"""

from __future__ import annotations

import sys

import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server

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


def build_app(host: str) -> object:
    server: Server[None] = Server(
        "echo-http-test-server", on_list_tools=_on_list_tools, on_call_tool=_on_call_tool
    )
    # No auth/token_verifier -- deliberate, see module docstring.
    return server.streamable_http_app(host=host)


async def serve(host: str, port: int) -> None:
    config = uvicorn.Config(build_app(host), host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    import anyio

    _host = "127.0.0.1"
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 8933
    anyio.run(serve, _host, _port)
