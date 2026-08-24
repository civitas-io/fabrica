"""MCP integration types -- see docs/contracts/mcp-integration.md.

ToolSchema/ToolResult here are the SAME dataclasses as fabrica.tools.types --
re-exported, not redefined, so MCPToolNamespace's return values are directly
usable by ToolManager without a translation layer at the boundary. The
contract sketches them as separate dataclasses; implementation found they're
identical in shape to fabrica.tools.types.ToolSchema/ToolResult (already
built for managers.md), so defining a second, structurally-identical pair
here would just be duplication for no behavioral difference -- fixed by
reusing, not by transcribing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from fabrica.tools.types import ToolResult, ToolSchema

__all__ = [
    "FilesystemMount",
    "MCPServerConfig",
    "MCPToolSchema",
    "SandboxConfig",
    "ToolResult",
    "ToolSchema",
]


@dataclass(frozen=True)
class FilesystemMount:
    path: str
    mode: Literal["ro", "rw"] = "ro"


@dataclass(frozen=True)
class SandboxConfig:
    enabled: bool = True
    network: Literal["allow", "deny"] = "deny"
    filesystem: list[FilesystemMount] = field(default_factory=list)
    allow_unsandboxed: bool = False
    """Fail-closed-by-default, explicit opt-in shape (see
    NullPresidiumClient.allow_ungoverned) -- consulted when srt is
    unavailable, OR when network="allow" is requested (srt structurally
    cannot honor that -- see contracts/mcp-integration.md's "Correction
    found during implementation" section).
    """


@dataclass(frozen=True)
class MCPServerConfig:
    """For stdio: set command (and optionally args/env). For sse and
    streamable_http: set url. streamable_http is the MCP spec's newer
    transport -- a single POST/GET/DELETE endpoint, no separate SSE-upgrade
    endpoint -- and is what most current remote MCP servers actually ship,
    often *instead of* classic sse rather than alongside it (GH #26).
    """

    name: str
    transport: Literal["stdio", "sse", "streamable_http"]
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    sandbox: SandboxConfig | None = None

    def __post_init__(self) -> None:
        if self.transport == "stdio" and not self.command:
            raise ValueError(f"MCPServerConfig {self.name!r}: transport=stdio requires 'command'")
        if self.transport in ("sse", "streamable_http") and not self.url:
            raise ValueError(
                f"MCPServerConfig {self.name!r}: transport={self.transport} requires 'url'"
            )


@dataclass(frozen=True)
class MCPToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]
