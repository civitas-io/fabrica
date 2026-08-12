"""A real FabricaMCPServer running over stdio -- built from a real,
CivitasBridge-assembled Fabrica facade (allow_ungoverned=True, zero
external infra needed). Used by tests/mcp/test_server.py as the "external
server" a real MCP ClientSession connects to over a real subprocess
boundary -- not a mock.

Invoked as: python -m tests.mcp.fixtures.fabrica_stdio_server
"""

from __future__ import annotations

import anyio

from fabrica.civitas_bridge import CivitasBridge
from fabrica.mcp.server import FabricaMCPServer, ServerTransportConfig


async def main() -> None:
    fabrica = await CivitasBridge(allow_ungoverned=True).build()
    server = FabricaMCPServer(fabrica, ServerTransportConfig(kind="stdio"))
    await server.start()


if __name__ == "__main__":
    anyio.run(main)
