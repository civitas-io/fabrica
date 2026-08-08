# MCP Integration

**Status:** Design · **Last updated:** 2026-08
**Depends on:** [tool-execution.md](tool-execution.md) (`ToolNamespace`, unchanged
by this doc), [landscape.md §2](landscape.md#2-mcp-gateways--registries--commoditized-do-not-build)
(the constraint this design must not violate)

---

## The constraint this has to reconcile, stated first

`landscape.md §2` already concluded: *"a generic MCP gateway has no moat and
Presidium already owns the governed one. Fabrica does not build this."*
Before designing anything here, it's worth being precise about why what
follows isn't that thing — not asserting it, showing it.

**A "generic MCP gateway"** (the rejected category — Docker MCP Toolkit,
Composio, agentgateway, Kong AI Gateway) is a **standalone product** whose job
is proxying, discovering, aggregating, and governing MCP traffic across many
servers and many consumers. Presidium already wraps `agentgateway` for exactly
this.

**What this doc designs is narrower and different in kind**: `MCPToolNamespace`
is one implementation of `ToolNamespace` — the same interface any
hand-written Python tool namespace already implements. It doesn't discover,
aggregate, or govern MCP servers across a fleet; it lets *one already-configured
MCP server connection* back *one namespace* a developer explicitly registers
with `ToolManager`, exactly like any other namespace. It has no registry, no
cross-server search, and no governance logic of its own — every call still
passes through `execute_in_sandbox`'s existing `check_grant`, same as any
other tool call. If it grew any of those things, it would be drifting into
the rejected category; it's written here specifically to not need them.

**Also distinct from a second, already-noted direction**: `tool-execution.md`
mentions in passing that *"Fabrica exposes itself as an MCP server, so any
MCP-compatible host can connect"* — Fabrica as an MCP **server** (exposing its
own tools/skills outward to other MCP clients). This doc is the opposite
direction: Fabrica as an MCP **client** (consuming an external MCP server as a
tool source). Both are legitimate, complementary, and not designed in the
same place — this doc covers only the client direction.

---

## Thesis: real, validated prior art — not a new build

An `MCPClient` (stdio + SSE transport, `list_tools`/`call_tool`, error handling,
audit-sink integration) and a `BubblewrapSandbox` (Linux namespace isolation
for the MCP server's subprocess) already exist and work, in
`civitas-contrib/packages/fabrica` — built before this repo existed, under
the same name, for what turned out to be a narrower and different purpose
than what this repo became. That code is a real prior spike, not something to
reinvent: this design's job is placing it correctly inside the object model
established since, not rewriting it.

## `MCPToolNamespace`

```python
class MCPToolNamespace:
    """A ToolNamespace backed by one MCP server connection. Implements the
    exact, unchanged ToolNamespace protocol (tool-execution.md) -- no new
    interface for ToolManager to learn."""

    def __init__(self, client: MCPClient) -> None: ...

    def stubs(self) -> str:
        """Progressive-disclosure listing, built from the MCP server's
        list_tools() response -- cached at connect time, not re-fetched
        per call (mirrors PromptManager's read-heavy caching rationale,
        contracts/prompts.md)."""

    def open(self, path: str) -> ToolSchema:
        """One MCP tool's schema. MCPToolSchema's fields (name, description,
        input_schema) map onto ToolSchema directly -- no translation layer
        needed beyond field names."""

    async def call(self, name: str, params: dict) -> ToolResult:
        """Proxies to MCPClient.call_tool(name, params). This is the ONLY
        method that crosses the wire to the MCP server -- exactly the same
        call shape as any other ToolNamespace, so code-mode generated code
        cannot tell an MCP-backed tool from a hand-written one."""
```

`MCPClient` itself (connect/disconnect/list_tools/call_tool, stdio + SSE
transport) is retained close to its existing shape — this doc changes where
it lives and what wraps it, not its internals, which already work.

## Where the `bwrap` sandboxing actually belongs — not `SandboxPool`

Stated plainly, since it would be easy to conflate the two: `SandboxPool`
isolates **short-lived, discardable executions** of AI-generated or
author-trusted code (`boot_clean → execute → terminate`, `contracts/sandbox.md`).
An MCP server is the opposite shape — **connected once, kept running for an
entire session**, called repeatedly. Routing MCP server isolation through
`SandboxPool`'s tier system would force a persistent-process problem into an
abstraction built for ephemeral ones, and would isolate the wrong thing: `bwrap`
here hardens *a third-party server binary the operator chose to run*, not
*AI-generated code Fabrica itself doesn't trust* — a different threat model
from what `SandboxPool` exists to address.

**`BubblewrapSandbox` stays exactly where it already is conceptually**: an
internal detail of how `MCPClient` launches its own subprocess, invisible to
`ToolManager`, `SandboxPool`, and `execute_in_sandbox` entirely. None of them
need to know an MCP server is namespace-isolated any more than they need to
know how a hand-written `ToolNamespace`'s own internals work.

## Platform reality, inherited honestly, not newly discovered

`bwrap` is Linux-only — the old code's own error message already says so
plainly (*"macOS: not supported... Windows: not supported"*). This isn't a new
gap this design introduces; it's the same shape as every other platform
limitation already accepted elsewhere in this project (macOS Tier 2's missing
snapshot/restore, Windows Tier 1 being deferred): **ship the real isolation
where it exists, degrade explicitly and loudly where it doesn't**, rather than
silently running unsandboxed. On macOS/Windows, `MCPClient.connect()` should
either refuse with a clear, actionable error (mirroring `BubblewrapSandbox.check_or_raise()`'s
existing message) or run genuinely unsandboxed with an explicit, unmissable
warning — not decided here (see Open questions).

## Migration, not deprecation

This gives a concrete resolution to the `civitas-contrib/packages/fabrica`
question raised earlier: **the code migrates, the stale docs don't.**
`MCPClient`/`BubblewrapSandbox` move into `civitas-io/fabrica` as the
validated implementation behind `MCPToolNamespace`; the old package's
`find_tools`-only framing (its `__init__.py` docstring, its `README.md`, the
RFC) gets retired as describing an abandoned direction, separately from this
migration, not conflated with it.

## Open questions

1. macOS/Windows behavior when `bwrap` is unavailable — hard refuse vs. loud
   unsandboxed fallback. Not decided; leans toward hard refuse by default
   (consistent with how `Sandbox` never silently downgrades isolation),
   with an explicit opt-in flag for unsandboxed local dev.
2. Connection lifecycle ownership — does `ToolManager.register()` eagerly
   connect the MCP server, or does `MCPToolNamespace` connect lazily on
   first `call()`? Affects `build()`'s startup latency and failure timing.
3. Multiple MCP servers per agent — this design covers one `MCPClient` per
   `MCPToolNamespace`; a developer registering several namespaces (one per
   server) is assumed sufficient, not stress-tested against a real
   multi-server use case.
4. Whether `stubs()`'s cache needs invalidation if the MCP server's own tool
   set changes mid-session (MCP supports server-initiated `list_changed`
   notifications) — not addressed; the old code never had to consider this.
5. Zero spike coverage for this design specifically — the old code worked in
   its original, narrower context; nothing has validated `MCPToolNamespace`
   as a `ToolNamespace` implementation end-to-end inside code-mode execution.
