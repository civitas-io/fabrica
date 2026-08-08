# Contract: `FabricaMCPServer`

**Status:** Contract — implementation-ready · **Last updated:** 2026-08
**Depends on:** [mcp-server.md](../mcp-server.md) (the design this formalizes),
[contracts/civitas-bridge.md](civitas-bridge.md) (`Fabrica`, the facade this
wraps), [contracts/managers.md](managers.md) (`execute_in_sandbox`, reused
unchanged underneath every tool handler)

---

## Types

```python
class TokenAuthenticator(Protocol):
    """Injected dependency -- resolves a bearer token to an agent_id, or
    None if invalid/unrecognized. FabricaMCPServer never stores or
    validates tokens itself; that's an operator-supplied concern (a
    lookup table, a database, an external auth service). Same DI shape
    as Summarizer/PresidiumClient -- external dependencies are always
    fully-constructed objects, never raw config this contract would
    translate itself (architecture.md §1a)."""
    async def authenticate(self, token: str) -> str | None: ...


@dataclass(frozen=True)
class ServerTransportConfig:
    kind: Literal["stdio", "http"]
    host: str | None = None           # required if kind == "http"
    port: int | None = None           # required if kind == "http"
    authenticator: TokenAuthenticator | None = None
    """Required if kind == "http" -- HTTP/SSE connections need bearer-token
    resolution to an agent_id (mcp-server.md's resolved auth layer). None
    for stdio -- see stdio_agent_id below."""
    stdio_agent_id: str = "mcp-stdio-client"
    """The agent_id assigned to every stdio connection. Only one trusted
    local caller exists per stdio session (implicit trust via process
    spawn, same as every local MCP server) -- no per-connection identity
    to resolve, so a single configured default suffices."""
```

---

## Errors

```python
class FabricaMCPServerError(Exception):
    """Base for FabricaMCPServer-specific errors."""


class WeakIsolationError(FabricaMCPServerError):
    """Raised at construction time when the underlying SandboxPool's tier
    is below Tier 2 and allow_weak_isolation_for_external_callers is
    False (the default) -- mcp-server.md's resolved isolation rule,
    fourth confirmed instance of the fail-closed-by-default,
    explicit-opt-in-to-bypass pattern (NullPresidiumClient.allow_ungoverned,
    MCPClient.allow_unsandboxed, this)."""


class AuthenticationError(FabricaMCPServerError):
    """Raised when an HTTP/SSE connection presents a token that
    authenticator.authenticate() resolves to None. This is a
    connection-level rejection -- it happens BEFORE any check_grant call,
    a layer that doesn't exist anywhere else in this platform's design
    (mcp-server.md's two-layer authorization model)."""
```

---

## `FabricaMCPServer`

```python
class FabricaMCPServer:
    def __init__(
        self,
        fabrica: Fabrica,
        transport: ServerTransportConfig,
        *,
        allow_weak_isolation_for_external_callers: bool = False,
    ) -> None:
        """
        Raises:
            WeakIsolationError: fabrica's underlying SandboxPool tier is
                below Tier 2 and allow_weak_isolation_for_external_callers
                is False.

        Wraps an ALREADY-BUILT Fabrica facade (the output of
        CivitasBridge.build()) -- this is an additional front door, never
        a replacement for direct in-process use. FabricaMCPServer holds no
        object graph of its own; every tool handler below delegates
        directly to fabrica's existing managers."""

    async def start(self) -> None:
        """Begins listening (stdio: reads from stdin; http: binds
        host:port). Blocks until stop() is called or the process ends."""

    async def stop(self) -> None:
        """Closes the listener and any open connections."""
```

## Tool handlers — five fixed tools, deliberately never more

Per `mcp-server.md`'s central finding: the tool list must stay fixed-size
regardless of how many `ToolNamespace` tools or skills are registered
underneath, or this would reintroduce the exact O(N) schema-dump cost the
whole platform exists to eliminate — just moved onto the external caller.

```python
# MCP tool: fabrica_find
async def _handle_find(self, agent_id: str, query: str, kind: str | None = None) -> list[RankedMatch]:
    """Delegates to fabrica.tools.find(query) / fabrica.skills.find(query)
    depending on kind (None searches both, matching contracts/retriever.md's
    optional-kind behavior). No check_grant call here -- discovery/search
    was never gated by Presidium anywhere else in this platform either."""

# MCP tool: fabrica_run_code
async def _handle_run_code(self, agent_id: str, code: str) -> RunResult:
    """Delegates to fabrica.tools.run_code(code, agent_id=agent_id,
    scope=Scope(agent_id=agent_id)). check_grant, execute_in_sandbox, and
    every existing governance/observability mechanism apply completely
    unchanged -- this handler adds nothing beyond resolving the MCP
    connection's agent_id and passing it through."""

# MCP tool: fabrica_run_skill
async def _handle_run_skill(self, agent_id: str, name: str, args: dict[str, Any]) -> RunResult:
    """Delegates to fabrica.skills.run(name, args, agent_id=agent_id,
    scope=Scope(agent_id=agent_id)). Same governance pass-through as
    fabrica_run_code."""

# MCP tool: fabrica_memory_write / fabrica_memory_search
async def _handle_memory_write(self, agent_id: str, content: str, metadata: dict | None = None) -> str:
    """Delegates to fabrica.memory.write(Scope(agent_id=agent_id),
    MemoryItem(content=content, metadata=metadata or {}))."""

async def _handle_memory_search(self, agent_id: str, query: str, limit: int = 5) -> list[MemoryItem]:
    """Delegates to fabrica.memory.search(Scope(agent_id=agent_id), query, limit)."""
```

**`agent_id` in every handler above is resolved by the connection layer, not
passed by the caller** — an external MCP client cannot claim to be a
different `agent_id` than the one its token (or stdio session) resolved to.
This is the connection-auth layer's entire job: establish `agent_id` once,
trust it for the lifetime of the connection, let every downstream call use
it exactly like a native Civitas agent's own identity.

## Prompts — MCP's native primitive, not a custom tool

```python
async def _handle_prompts_list(self) -> list[PromptTemplate]:
    """Delegates to fabrica.prompts.list_names(), then get(name) for each
    -- list_names() was ADDED to contracts/prompts.md's PromptStore/
    PromptManager specifically because writing this handler surfaced that
    no such method existed (real gap, closed immediately, not left
    dangling as an open item)."""

async def _handle_prompts_get(self, name: str, version: int | None = None) -> PromptTemplate:
    """Delegates directly to fabrica.prompts.get(name, version) -- the
    closest thing to a zero-translation mapping in this whole contract."""
```

## What this contract deliberately does not cover

- **The MCP protocol/transport implementation itself** (JSON-RPC framing,
  stdio/SSE wire details) — assumed to come from the `mcp` library already
  used by `MCPClient`, not reimplemented here.
- **Token issuance, rotation, or revocation** — `TokenAuthenticator` is the
  boundary; how an operator actually mints/manages tokens is out of scope,
  per `mcp-server.md` open question 1.
- **Rate limiting** — deliberately absent; inherited from `SandboxPool`'s
  existing bounded-overflow protection, per the design doc's stated reuse.

## Open items for implementation

1. ~~`fabrica_prompts_list` has no backing method~~ **Resolved**: `list_names()`
   added directly to `contracts/prompts.md`'s `PromptStore`/`PromptManager`
   while writing this handler, rather than left as a dangling gap.
2. `WeakIsolationError`'s check happens once, at construction — if an
   operator changes `SandboxPool`'s tier after `FabricaMCPServer` is already
   running (in a service-mode deployment where this could happen live),
   there's no re-check. Not addressed.
3. Multi-tenant HTTP deployments (`mcp-server.md` open question 3) — this
   contract resolves the single-token-per-connection shape but doesn't
   stress-test many simultaneous distinct `agent_id`s against shared
   `SandboxPool`/`Retriever` state.
4. Zero spike coverage — carried forward unchanged.
