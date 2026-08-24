"""Real MCP server exposed over BOTH sse and streamable_http, on
different ports, sharing the same tool logic -- for a fair, direct
transport comparison. handle_sse's raw scope/receive/send shape
confirmed directly against mcp.server.mcpserver.server's own real,
current usage of SseServerTransport, not guessed.
"""
from __future__ import annotations

import sys

import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

_SCHEMA = {"type": "object", "properties": {"n": {"type": "number"}}, "required": ["n"]}


async def _on_list_tools(ctx: object, params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[types.Tool(name="echo", description="Echoes n back", input_schema=_SCHEMA)]
    )


async def _on_call_tool(ctx: object, params: types.CallToolRequestParams) -> types.CallToolResult:
    n = (params.arguments or {}).get("n", 0)
    return types.CallToolResult(content=[types.TextContent(type="text", text=str(n))])


def build_streamable_http_app(host: str) -> Starlette:
    server: Server[None] = Server(
        "bench-http-server", on_list_tools=_on_list_tools, on_call_tool=_on_call_tool
    )
    return server.streamable_http_app(host=host)


def build_sse_app(host: str) -> Starlette:
    server: Server[None] = Server(
        "bench-sse-server", on_list_tools=_on_list_tools, on_call_tool=_on_call_tool
    )
    sse = SseServerTransport("/messages/")

    async def handle_sse(scope: Scope, receive: Receive, send: Send) -> Response:
        async with sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
        return Response()

    # Starlette's Route(endpoint=...) calls a plain function endpoint with
    # just `request`, not raw ASGI (scope, receive, send) -- confirmed
    # against mcp.server.mcpserver.server's own real, current pattern for
    # wiring handle_sse (an ASGI-shaped callable) into a Route.
    async def sse_endpoint(request: Request) -> Response:
        return await handle_sse(request.scope, request.receive, request._send)  # noqa: SLF001

    return Starlette(
        routes=[
            Route("/sse", endpoint=sse_endpoint),
            Mount("/messages/", app=sse.handle_post_message),
        ]
    )


async def serve(kind: str, host: str, port: int) -> None:
    app = build_streamable_http_app(host) if kind == "streamable_http" else build_sse_app(host)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    import anyio

    _kind = sys.argv[1]  # "streamable_http" or "sse"
    _host = "127.0.0.1"
    _port = int(sys.argv[2])
    anyio.run(serve, _kind, _host, _port)
