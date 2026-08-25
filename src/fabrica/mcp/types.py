"""MCP integration types -- see docs/contracts/mcp-integration.md.

ToolSchema/ToolResult here are the SAME dataclasses as fabrica.tools.types --
re-exported, not redefined, so MCPToolNamespace's return values are directly
usable by ToolManager without a translation layer at the boundary. The
contract sketches them as separate dataclasses; implementation found they're
identical in shape to fabrica.tools.types.ToolSchema/ToolResult (already
built for managers.md), so defining a second, structurally-identical pair
here would just be duplication for no behavioral difference -- fixed by
reusing, not by transcribing.

FilesystemMount/SandboxConfig/MCPServerConfig/MCPToolSchema below follow the
exact same reasoning, fixed 2026-08-25: these used to be a SECOND,
independently-defined copy of civitas.sandbox.config/civitas.mcp.types'
own dataclasses of the same name -- found, while fixing
AgentProcess.connect_mcp()'s missing MCPTool, to have silently diverged
(SandboxConfig.enabled defaulted opposite ways -- False in civitas, True
here -- a real, security-relevant inconsistency, not just a cosmetic one;
MCPServerConfig.env's default differed too). fabrica already has a real,
hard runtime dependency on civitas (unlike everywhere else in this
package, which depends on shapes, not packages -- see architecture.md
§1a's one deliberate exception), so re-exporting civitas's own canonical
types costs nothing new and eliminates the divergence at the source
instead of hand-syncing two definitions forever. civitas's own
SandboxConfig gained the allow_unsandboxed field fabrica's version already
had (civitas core itself never reads it -- it exists there purely so one
shared type carries everything SrtIsolation below needs).
"""

from __future__ import annotations

from civitas.mcp.types import MCPServerConfig, MCPToolSchema
from civitas.sandbox.config import FilesystemMount, SandboxConfig

from fabrica.tools.types import ToolResult, ToolSchema

__all__ = [
    "FilesystemMount",
    "MCPServerConfig",
    "MCPToolSchema",
    "SandboxConfig",
    "ToolResult",
    "ToolSchema",
]
