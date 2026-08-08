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


@dataclass(frozen=True)
class ToolSchema:
    """tool-execution.md's sketched type, given a concrete shape here.
    MCPToolSchema's fields map onto this directly -- no translation
    logic beyond field renaming."""
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """tool-execution.md's sketched type, given a concrete shape here."""
    success: bool
    value: Any | None
    error_message: str | None
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

1. `__init__`'s eager connection is inherently async (`connect()` is a
   coroutine), but Python constructors can't be — needs an async factory
   (`await MCPToolNamespace.create(client)`) or `ToolManager.register()`
   itself becoming async-aware of this specific namespace type. Not
   resolved here; a real implementation-level wrinkle the design doc's
   sketch glossed over.
2. `open()`'s `KeyError`-on-unknown-path behavior isn't cross-checked against
   how other `ToolNamespace` implementations are expected to handle the same
   case — `tool-execution.md` doesn't specify this generically either.
3. Zero spike coverage — carried forward unchanged from `mcp-integration.md`.
