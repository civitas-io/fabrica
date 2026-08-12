"""MCP integration -- Fabrica as an MCP client. See docs/contracts/mcp-integration.md.

Migrated real code from civitas-contrib/packages/fabrica/src/fabrica/mcp/ --
signatures stay close to what already worked; BubblewrapSandbox is replaced
by SrtIsolation (cross-platform, not Linux-only).
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
    UnsupportedTransportError,
    WeakIsolationError,
)
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
    "UnsupportedTransportError",
    "WeakIsolationError",
]
