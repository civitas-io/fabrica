"""MCPClient/MCPToolNamespace errors -- see docs/contracts/mcp-integration.md."""

from __future__ import annotations


class MCPError(Exception):
    """Base for all MCPClient/MCPToolNamespace errors."""


class MCPConnectionError(MCPError):
    """connect() failed -- transport-level failure. Raised at connect()
    time, which per MCPToolNamespace's eager-connection requirement means
    at ToolManager.register() time -- a broken MCP server config fails at
    build()/registration, not at first use.
    """


class IsolationUnavailableError(MCPConnectionError):
    """Raised by connect() when srt is unavailable on the host and
    sandbox.allow_unsandboxed is False (the default).
    """


class UnsupportedSandboxConfigurationError(MCPConnectionError):
    """Raised by connect() when sandbox.enabled and
    sandbox.network == "allow" are both true -- srt structurally refuses
    an unsandboxed-network configuration (see contracts/mcp-integration.md's
    "Correction found during implementation" section). Not a silent
    downgrade to network="deny", and not a passthrough of a config srt
    itself would reject.
    """


class MCPServerUnavailableError(MCPError):
    """Raised by MCPToolNamespace.call() when the connection is dead, or
    the server reports this tool no longer exists. Surfaces to ToolManager
    as a routine outcome (RunResult.success=False), per
    execute_in_sandbox's existing routine-vs-infrastructure split.
    """


class MCPToolError(MCPError):
    """The MCP server itself reported is_error=True for a specific
    call_tool invocation -- a routine tool-level failure, distinct from
    MCPServerUnavailableError's connection/existence failures.
    """

    def __init__(self, tool_name: str, detail: str) -> None:
        self.tool_name = tool_name
        self.detail = detail
        super().__init__(f"MCP tool {tool_name!r} failed: {detail}")
