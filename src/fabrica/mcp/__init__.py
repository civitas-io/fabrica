"""MCP integration -- Fabrica as an MCP client. See docs/contracts/mcp-integration.md.

Migrated real code from civitas-contrib/packages/fabrica/src/fabrica/mcp/ --
signatures stay close to what already worked; BubblewrapSandbox is replaced
by SrtIsolation (cross-platform, not Linux-only).

MCPTool added 2026-08-25 -- the real, previously-missing piece
civitas.process.AgentProcess.connect_mcp() has always tried to import (see
that module's own docstring history / connect_mcp()'s docstring). Not a
rename or a refactor of MCPToolNamespace -- a second, parallel
civitas.plugins.tools.ToolProvider adapter for a different consumer.
"""

from fabrica.mcp.client import AuditSink, MCPClient
from fabrica.mcp.errors import (
    IsolationUnavailableError,
    MCPConnectionError,
    MCPError,
    MCPServerUnavailableError,
    MCPToolError,
    UnsupportedSandboxConfigurationError,
)
from fabrica.mcp.isolation import SrtIsolation
from fabrica.mcp.namespace import MCPToolNamespace
from fabrica.mcp.server import (
    AuthenticationError,
    FabricaMCPServer,
    FabricaMCPServerError,
    ServerTransportConfig,
    TokenAuthenticator,
    WeakIsolationError,
)
from fabrica.mcp.tool import MCPTool
from fabrica.mcp.types import (
    FilesystemMount,
    MCPServerConfig,
    MCPToolSchema,
    SandboxConfig,
    ToolResult,
    ToolSchema,
)

__all__ = [
    "AuditSink",
    "AuthenticationError",
    "FabricaMCPServer",
    "FabricaMCPServerError",
    "FilesystemMount",
    "IsolationUnavailableError",
    "MCPClient",
    "MCPConnectionError",
    "MCPError",
    "MCPServerConfig",
    "MCPServerUnavailableError",
    "MCPTool",
    "MCPToolError",
    "MCPToolNamespace",
    "MCPToolSchema",
    "SandboxConfig",
    "ServerTransportConfig",
    "SrtIsolation",
    "TokenAuthenticator",
    "ToolResult",
    "ToolSchema",
    "UnsupportedSandboxConfigurationError",
    "WeakIsolationError",
]
