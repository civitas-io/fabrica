# Contract: `MCPClient`, `MCPToolNamespace`

**Status:** Contract — implementation-ready · **Last updated:** 2026-08
**Depends on:** [mcp-integration.md](../mcp-integration.md) (the design this
formalizes), [contracts/managers.md](managers.md) (`ToolNamespace`,
`execute_in_sandbox`'s routine-vs-infrastructure error split this reuses)
**Migrates real code** from `civitas-contrib/packages/fabrica` — signatures
below stay close to what already works; `BubblewrapSandbox` specifically does
NOT migrate as-is (replaced by `srt`, per the design doc's resolution).

---

## Types

```python
@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: Literal["stdio", "sse"]
    command: str | None = None       # required if transport == "stdio"
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None           # required if transport == "sse"
    sandbox: SandboxConfig | None = None


@dataclass(frozen=True)
class SandboxConfig:
    enabled: bool = True
    network: Literal["allow", "deny"] = "deny"
    filesystem: list[FilesystemMount] = field(default_factory=list)
    allow_unsandboxed: bool = False
    """Resolved in mcp-integration.md: fail-closed-by-default, explicit
    opt-in shape (third confirmed instance of this rule, after
    NullPresidiumClient.allow_ungoverned). Only consulted if `srt` (or its
    underlying bwrap/sandbox-exec) is unavailable on the host -- see
    MCPClient.connect()'s Raises section."""


@dataclass(frozen=True)
class FilesystemMount:
    path: str
    mode: Literal["ro", "rw"]


@dataclass(frozen=True)
class MCPToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]


# ToolSchema/ToolResult are NOT redefined here. This contract originally
# sketched them as their own dataclasses ("tool-execution.md's sketched
# type, given a concrete shape here"); implementation found
# fabrica.tools.types.ToolSchema/ToolResult (already built for
# contracts/managers.md) are structurally identical, field-for-field.
# fabrica.mcp.types re-exports them directly instead of defining a second,
# identical pair -- MCPToolNamespace's return values are then directly
# usable by ToolManager with zero translation at the boundary, which a
# separate-but-identical type would have required anyway.
```

---

## Errors

```python
class MCPError(Exception):
    """Base for all MCPClient/MCPToolNamespace errors."""


class MCPConnectionError(MCPError):
    """connect() failed -- transport-level failure (subprocess wouldn't
    start, SSE endpoint unreachable). Raised at connect() time, which per
    MCPToolNamespace's eager-connection requirement means at
    ToolManager.register() time -- a broken MCP server config fails at
    build()/registration, not at first use (mcp-integration.md's named
    cost of the eager-connection decision)."""


class IsolationUnavailableError(MCPConnectionError):
    """Raised by connect() when srt (or its underlying bwrap/sandbox-exec)
    is unavailable on the host and sandbox.allow_unsandboxed is False (the
    default). Mirrors BubblewrapSandbox.check_or_raise()'s existing
    behavior in the migrated code -- not a new caution, an extension of
    caution that already existed, to a second platform."""


class UnsupportedSandboxConfigurationError(MCPConnectionError):
    """Raised by connect() when sandbox.enabled and
    sandbox.network == "allow" are both true -- srt structurally refuses
    an unsandboxed-network configuration (see the correction above), so
    there is no honest way to honor this request. Not a silent downgrade
    to network="deny" (that would change the server's real behavior
    without telling anyone) or a passthrough of a config srt itself would
    reject (that would surface as a confusing subprocess failure instead
    of a clear, typed error at connect() time)."""


class MCPServerUnavailableError(MCPError):
    """Raised by MCPToolNamespace.call() when the connection is dead, or
    the server reports this tool no longer exists. Surfaces to
    ToolManager as a routine outcome (RunResult.success=False), per
    execute_in_sandbox's existing routine-vs-infrastructure split
    (contracts/managers.md) -- not a hard crash, since external service
    degradation is a normal operational reality the model should see and
    react to, not one that ends the whole execution."""


class MCPToolError(MCPError):
    """The MCP server itself reported isError=True for a specific
    call_tool invocation -- a routine tool-level failure, distinct from
    MCPServerUnavailableError's connection/existence failures. Also
    surfaces via RunResult.success=False, not raised past MCPToolNamespace."""
```

---

## `MCPClient`

```python
class MCPClient:
    def __init__(
        self, config: MCPServerConfig, audit_sink: AuditSink | None = None,
        agent_name: str = "",
    ) -> None: ...

    async def connect(self) -> None:
        """Opens the transport (stdio subprocess wrapped in `srt` when
        sandbox.enabled, or a direct SSE connection), initializes the MCP
        session. Idempotent -- a second call while already connected is a
        no-op, matching the migrated code's existing behavior.

        Raises:
            MCPConnectionError: transport-level failure.
            IsolationUnavailableError: srt unavailable and
                sandbox.allow_unsandboxed is False.
        """

    async def disconnect(self) -> None:
        """Closes the session and underlying transport/subprocess."""

    async def list_tools(self) -> list[MCPToolSchema]:
        """Raises MCPConnectionError (not MCPServerUnavailableError -- this
        method is called once, at connect() time, by MCPToolNamespace's
        constructor, not per-call) if not connected or the server errors."""

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Raises MCPToolError if the server reports isError=True.
        Raises MCPServerUnavailableError if the connection is dead."""
```

Retained close to the migrated code's existing shape (`stdio_client`/`sse_client`
transport selection, `ClientSession` lifecycle) — this contract specifies the
boundary and error behavior, not a rewrite of internals that already work.

**Correction found during implementation**: the migrated code
(`civitas-contrib/packages/fabrica/src/fabrica/mcp/client.py`) reads
`tool.inputSchema`/`result.isError` -- camelCase attribute access matching
an older `mcp` Python SDK. The real, currently-installed `mcp` package
(v2.0.0) exposes these as snake_case attributes on its pydantic models
(`tool.input_schema`, `result.is_error`) -- camelCase survives only as a
constructor kwarg alias, not as an attribute name. Confirmed directly by
constructing real `mcp.types.Tool`/`CallToolResult` objects and reading
their attributes, not assumed. `MCPClient.list_tools()`/`call_tool()` are
implemented against the real, current snake_case attribute names, not
transcribed unchanged from the migrated code -- the same "reconcile
against real source, don't just transcribe" discipline applied to
`CivitasRuntime` in `contracts/civitas-bridge.md`.

**Second correction found during implementation, about `srt` specifically
(not `bwrap`)**: `SandboxConfig.network: Literal["allow", "deny"]` assumes
a coarse binary switch, matching `bwrap`'s `--unshare-net` toggle that the
migrated `BubblewrapSandbox` used. `srt`'s real network model has no such
switch -- it is always an explicit domain allowlist
(`network.allowedDomains`), and `srt` deliberately, structurally REFUSES
an overly-broad wildcard entry (`"*"`) with a hard configuration error,
confirmed by running `srt` directly, not assumed from its docs:
`network="deny"` maps cleanly to `allowedDomains: []` (tested, works
exactly as expected), but `network="allow"` has no honest, safe
translation into `srt`'s settings shape at all -- there is no "allow
literally everything" configuration `srt` will accept.

**Resolved**: `MCPClient.connect()` raises a new error,
`UnsupportedSandboxConfigurationError`, when `sandbox.enabled` and
`sandbox.network == "allow"` are both true -- `srt` cannot honor this
request at all, so pretending otherwise (e.g. silently downgrading to
`"deny"`, or passing through an invalid config that `srt` itself would
reject at its own boundary) would be worse than refusing up front. An
operator whose MCP server genuinely needs broad outbound network access
must set `sandbox.enabled = False` with `allow_unsandboxed = True` for
that specific server -- an explicit, greppable, fail-closed-by-default
opt-in, the fifth confirmed instance of that platform-wide rule.

---

## `MCPToolNamespace`

```python
class MCPToolNamespace:
    """Implements the existing, unchanged ToolNamespace protocol
    (contracts/managers.md) -- ToolManager cannot tell this apart from a
    hand-written namespace."""

    def __init__(self, client: MCPClient) -> None:
        """Connects EAGERLY -- forced by ToolManager.register()'s existing
        contract, which indexes a namespace's tools into the shared
        Retriever at registration time (mcp-integration.md's resolved Q2).
        Calls client.connect() and client.list_tools() here, synchronously
        with construction (via an async factory, in practice -- see Open
        items)."""

    def stubs(self) -> str:
        """Built from the tool list fetched at connect() time. Cached, not
        re-fetched per call."""

    def open(self, path: str) -> ToolSchema:
        """Raises KeyError if path doesn't name a known tool -- consistent
        with a plain dict-like lookup, no new error type needed for this
        case."""

    async def call(self, name: str, params: dict[str, Any]) -> ToolResult:
        """Proxies to client.call_tool(name, params). Catches
        MCPToolError/MCPServerUnavailableError internally and returns
        ToolResult(success=False, error_message=...) rather than letting
        either propagate -- execute_in_sandbox's routine-vs-infrastructure
        split (contracts/managers.md) means a degraded MCP server is a
        ToolResult-level outcome the model sees, not an exception that
        aborts the whole execution."""
```

## Isolation

`MCPClient.connect()`'s stdio path wraps the subprocess launch in `srt`
(unifying `bwrap` on Linux, `sandbox-exec` on macOS — `mcp-integration.md`'s
resolved isolation mechanism), invisible to `ToolManager`/`SandboxPool`
entirely, per the design's stated boundary (persistent-connection isolation
is not routed through `SandboxPool`'s ephemeral-execution lifecycle).

## What this contract deliberately does not cover

- **`srt`'s exact invocation syntax/profile format** — an implementation
  detail of `MCPClient.connect()`, not specified at the contract level, the
  same way `SandboxPool`'s contract doesn't specify Firecracker's exact
  kernel/rootfs configuration.
- **The `list_changed` soft-refresh guarantee** — explicitly deferred in
  `mcp-integration.md`, blocked on `contracts/retriever.md`'s own open item;
  not designed here either.
- **Multi-server aggregation of any kind** — deliberately absent, per the
  `landscape.md §2` boundary already checked in the design doc.

## Open items for implementation

1. ~~`__init__`'s eager connection is inherently async~~ **Resolved**:
   `MCPToolNamespace.create(client)` is an async classmethod factory --
   `__init__` itself stays synchronous, takes an already-fetched schema
   list, and is not the public construction path. `ToolManager.register()`
   itself did not need to become async-aware of this specific namespace
   type -- it already awaits `namespace.list_schemas()`'s caller-side
   construction happening before `register()` is ever invoked, same as
   any other `ToolNamespace` implementation; only the CALLER assembling a
   `MCPToolNamespace` needs `await ... .create(...)`, which is no
   different in shape from `await SkillManager.load(...)`.
2. `open()`'s `KeyError`-on-unknown-path behavior isn't cross-checked against
   how other `ToolNamespace` implementations are expected to handle the same
   case — `tool-execution.md` doesn't specify this generically either.
3. Zero spike coverage — carried forward unchanged from `mcp-integration.md`.
