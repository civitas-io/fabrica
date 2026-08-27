# MCP Integration

**Status:** Design · **Last updated:** 2026-08
**Formalized by:** [contracts/mcp-integration.md](contracts/mcp-integration.md) —
implementation-ready types/signatures; that file is authoritative for exact
API shape, this one for the reasoning.
**Depends on:** [tool-execution.md](tool-execution.md) (`ToolNamespace`, unchanged
by this doc), [landscape.md §2](landscape.md#2-mcp-gateways-registries-commoditized-do-not-build)
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

    def __init__(self, client: MCPClient) -> None:
        """Connects EAGERLY, not lazily -- forced by ToolManager.register()'s
        existing contract (contracts/managers.md), not a style preference.
        register() indexes a namespace's tools into the shared Retriever at
        registration time; for that to work, this namespace must already
        know its tool list, which means it must already be connected. Lazy
        connection would leave MCP-sourced tools invisible to find() and
        code-mode until some arbitrary first-call moment -- silently
        breaking unified retrieval for this one tool source specifically.

        Real cost, named rather than hidden: a broken MCP server config now
        fails at build()/registration time, not at first use -- the right
        trade (fail fast and loud beats failing deep inside a code-mode
        execution), but it does mean a slow-to-start or flaky MCP server
        becomes a startup-latency and startup-reliability concern for the
        whole Fabrica construction, not something isolated to whenever that
        tool happens to get called."""

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
        cannot tell an MCP-backed tool from a hand-written one.

        Raises:
            MCPServerUnavailableError: the connection is dead, or the
                server reports this tool no longer exists (see "Degradation
                and staleness" below) -- surfaces as a routine outcome via
                RunResult.success=False (execute_in_sandbox's existing
                routine-vs-infrastructure split, contracts/managers.md),
                not a hard crash, since external service degradation is a
                normal operational reality the model should be able to see
                and react to.
        """


class MCPServerUnavailableError(Exception):
    """Distinct from a routine tool-level error so ToolManager can
    translate it into a clear, actionable RunResult.error_message --
    "tool X is no longer available on server Y" -- rather than an opaque
    failure or, worse, a silent stale success."""
```

`MCPClient` itself (connect/disconnect/list_tools/call_tool, stdio + SSE
transport) is retained close to its existing shape — this doc changes where
it lives and what wraps it, not its internals, which already work.

## Per-server isolation — an explicit guarantee, not an implicit side-effect

**Each `MCPToolNamespace`/`MCPClient` pair launches its own separate
subprocess, independently sandboxed.** An agent using three MCP servers gets
three independently-launched, independently-sandboxed subprocesses — there
is no code path where two servers share a subprocess, a sandbox profile, or a
container, because each namespace is its own independently-constructed object
managing its own connection lifecycle. This falls straight out of the
one-namespace-per-server design; it isn't new mechanism, but it deserves to
be **stated as a guarantee**, not left as an unverified implicit consequence.

**Two things this does NOT close, named rather than overclaimed:**

- **Cross-server side-channels via shared mount points.** If an operator
  configured two different MCP servers with write access to the *same* host
  directory, they could communicate through it regardless of per-subprocess
  sandboxing. The default case is closed (`srt`/`bwrap`'s per-invocation
  `tmpfs` gives each launch a fresh, private `/tmp`) — but this is a
  configuration responsibility, not something the sandbox mechanism itself
  can prevent if an operator explicitly grants two servers the same
  writable path.
- **The trusted parent process is not sandboxed from what it parses.**
  Subprocess isolation protects the host from a malicious server's *code* —
  arbitrary execution, filesystem/network abuse. It does not protect the
  host from a maliciously crafted MCP *protocol response* exploiting a bug
  in `MCPClient`'s own parsing logic, which runs in the trusted parent
  process, outside any sandbox. Per-server subprocess isolation and
  defensive protocol parsing are different problems; this design only
  solves the first.

## Where the sandboxing actually belongs — not `SandboxPool`

Stated plainly, since it would be easy to conflate the two: `SandboxPool`
isolates **short-lived, discardable executions** of AI-generated or
author-trusted code (`boot_clean → execute → terminate`, `contracts/sandbox.md`).
An MCP server is the opposite shape — **connected once, kept running for an
entire session**, called repeatedly. Routing MCP server isolation through
`SandboxPool`'s tier system would force a persistent-process problem into an
abstraction built for ephemeral ones, and would isolate the wrong thing: the
sandbox here hardens *a third-party server binary the operator chose to run*,
not *AI-generated code Fabrica itself doesn't trust* — a different threat
model from what `SandboxPool` exists to address.

**The sandboxing stays exactly where it already is conceptually**: an
internal detail of how `MCPClient` launches its own subprocess, invisible to
`ToolManager`, `SandboxPool`, and `execute_in_sandbox` entirely. None of them
need to know an MCP server is namespace-isolated any more than they need to
know how a hand-written `ToolNamespace`'s own internals work.

## Isolation mechanism — resolved: `srt`, not raw `bwrap`, on both Linux and macOS

**Original framing was incomplete, not wrong in direction.** The old code
used `bwrap` directly, Linux-only. Research surfaced a better answer than
"macOS has no equivalent": **`sandbox-exec` (Seatbelt) is macOS's direct
analog** — same weight class as `bwrap`, still functional despite Apple's
long-standing deprecation notice, used in production today by OpenAI's Codex
CLI, Chromium, and Bazel for exactly this purpose (wrapping a subprocess with
restricted filesystem/network access).

More significantly: **`srt`** — the same Anthropic tool already spiked and
validated in this project (`SPIKE-macos-isolation-srt-libkrun.md`) — **is
already built on top of `bwrap` on Linux and `sandbox-exec` on macOS**,
unifying both into one cross-platform tool, with a *stronger* network
isolation model than raw `bwrap` achieves alone (deny-by-default,
proxy-mediated egress, vs. `bwrap`'s blunter `--unshare-net`).

**Resolved design: `MCPClient` uses `srt` directly — not through
`SandboxPool`'s pool/lifecycle machinery** (that boundary from the previous
section still holds; this is the same standalone binary, called directly, the
way the old code called `bwrap` directly) **— on both Linux and macOS.** This
replaces the old Linux-only `BubblewrapSandbox` entirely, rather than adding a
second, separate macOS-specific implementation alongside it.

**This narrows the real remaining platform gap to Windows only** — `srt`'s
Windows mode was noted as untested in the original spike, and Windows is
already a deliberately deprioritized platform in this project. Resolved:
`MCPClient` accepts `allow_unsandboxed: bool = False`, hard-refusing to
connect on Windows (or anywhere `srt` genuinely isn't available) unless
explicitly overridden — the same "fail closed by default, explicit named
opt-in to bypass" shape already used for `NullPresidiumClient`'s
`allow_ungoverned=True`. Third instance of this pattern now, worth treating as
a named platform-wide rule: **when a security/governance mechanism is
unavailable, fail closed by default; the exception is always an explicit,
greppable opt-in flag, never a silent fallback.**

## Migration, not deprecation

This gives a concrete resolution to the `civitas-contrib/packages/fabrica`
question raised earlier: **the code migrates, the stale docs don't.**
`MCPClient` moves into `civitas-io/fabrica` close to its existing shape,
becoming the implementation behind `MCPToolNamespace`; `BubblewrapSandbox`
specifically does **not** migrate as-is — its Linux-only `bwrap` wrapping is
replaced by the cross-platform `srt` invocation resolved above, since
migrating it unchanged would have re-introduced the exact macOS gap this
design pass just closed. The old package's `find_tools`-only framing (its
`__init__.py` docstring, its `README.md`, the RFC) gets retired as describing
an abandoned direction, separately from this migration, not conflated with it.

## Open questions, resolved through direct walkthrough — four of five closed

All five original questions were walked through directly rather than answered
in one pass, in this order, since each answer sometimes changed the shape of
the next:

1. ~~macOS/Windows behavior when `bwrap` is unavailable~~ **Resolved.** `srt`
   (already spiked in this project) unifies `bwrap` (Linux) and `sandbox-exec`
   (macOS) into one cross-platform tool with a stronger network-isolation
   model than either alone — see "Isolation mechanism," above. Narrows the
   real gap to Windows only, closed with `allow_unsandboxed=False` by default,
   the same fail-closed-with-explicit-opt-in shape as `NullPresidiumClient`.
2. ~~Connection lifecycle ownership~~ **Resolved: eager, and not actually a
   choice.** Forced by `ToolManager.register()`'s existing contract needing a
   tool list to index at registration time — see `MCPToolNamespace.__init__`,
   above.
3. ~~Multiple MCP servers per agent~~ **Resolved: already sufficient, correctly
   out of scope to extend.** One namespace per server; any cross-server
   discovery/aggregation would drift into `landscape.md §2`'s rejected
   "MCP gateway" category. This surfaced a real follow-on question — does
   this give strong enough *isolation* between servers, not just a clean
   API shape — answered explicitly in "Per-server isolation," above.
4. ~~`stubs()` cache invalidation on `list_changed`~~ **Split into a hard
   guarantee (resolved, ships now) and a soft one (still open).** The hard
   guarantee — a stale or degraded tool call fails clearly via
   `MCPServerUnavailableError` → `RunResult.success=False`, never silently —
   does not wait on anything and is specified above. The soft
   guarantee — proactively refreshing `Retriever`'s index *before* the model
   tries a stale entry — genuinely depends on `contracts/retriever.md`'s
   existing open item on eager-cache invalidation timing/atomicity, and
   solving it independently here risked deciding the same question twice,
   inconsistently. This was a real, deliberate trade-off surfaced by pointing
   out the stakes directly (an LLM being told about a tool that no longer
   works is a correctness failure, not a caching nicety) — not a question
   quietly dropped.
5. **Still open, unchanged**: zero spike coverage for `MCPToolNamespace`
   specifically. The old code validated a narrower, different context (a
   standalone MCP client + subprocess sandbox); nothing has validated this
   namespace implementation working end-to-end inside actual code-mode
   execution — eager connection, `Retriever` indexing, sandboxed calls, and
   the new degradation-handling path all together.
