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
    # allow_weak_isolation_for_external_callers=True: honest, not a
    # workaround -- see tests/mcp/test_server.py's identical note.
    server = FabricaMCPServer(
        fabrica,
        ServerTransportConfig(kind="stdio"),
        allow_weak_isolation_for_external_callers=True,
    )
    try:
        await server.start()
    finally:
        # The real mcp stdio_client context manager closes this process's
        # stdin on exit, which server.start()'s own read loop treats as
        # EOF and returns from -- a graceful path, not a SIGKILL, so this
        # runs in the normal case. Without it, real dispatch's backend
        # (e.g. a real SrtSandbox, which allocates a real instance-level
        # directory) is never closed for the whole lifetime of this
        # subprocess -- one leaked directory per test invocation of this
        # fixture, same real gap Sandbox.close() exists to close elsewhere.
        await fabrica.close()


if __name__ == "__main__":
    anyio.run(main)
