# Contract: `FabricaMCPServer`

**Status:** Implemented (`src/fabrica/mcp/server.py`), both stdio and HTTP
transports real · **Last updated:** 2026-08
**Depends on:** [mcp-server.md](../mcp-server.md) (the design this formalizes),
[contracts/civitas-bridge.md](civitas-bridge.md) (`Fabrica`, the facade this
wraps), [contracts/managers.md](managers.md) (`execute_in_sandbox`, reused
unchanged underneath every tool handler)

---

## Correction found during implementation: HTTP transport reuses the `mcp` library's OWN real auth support, not hand-rolled ASGI middleware

The original sketch above implied `FabricaMCPServer` would need its own
ASGI app assembly and its own bearer-token extraction. Reading
`mcp.server.lowlevel.Server`'s real, current API during implementation
found this already exists, built-in: `Server.streamable_http_app(auth=...,
token_verifier=...)` returns a real `Starlette` app with
`AuthenticationMiddleware`/`BearerAuthBackend` (extracts the bearer token,
calls `token_verifier.verify_token`) and `RequireAuthMiddleware` (rejects
any request with no authenticated user) already wired in -- confirmed
working end to end (a real `uvicorn` server, a real
`mcp.client.streamable_http` client, real accept/reject) before writing
any of `FabricaMCPServer`'s own code, not assumed from the library's docs.

`TokenAuthenticator` (this contract's own DI boundary) is adapted onto the
real `mcp.server.auth.provider.TokenVerifier` Protocol via a small
`_TokenVerifierAdapter` -- `agent_id` is carried in the real
`AccessToken.subject` field (not `client_id`, which means something
different in OAuth: the calling application, not the resolved end-user
identity) and retrieved per-request via `get_access_token()`'s contextvar
inside `_on_call_tool`, exactly where `stdio_agent_id` is used for stdio.

Two required-but-otherwise-unused fields on the real `AuthSettings`
pydantic model, `issuer_url` and `resource_server_url`, must both be
supplied even though no real OAuth authorization-server flow is used here
-- confirmed directly (not from docs) that `streamable_http_app` only adds
OAuth discovery *routes* when `auth_server_provider` is ALSO set, which it
never is in this contract; `issuer_url` is a required placeholder value in
that configuration, not a real identity this server asserts.

**A real gap found and fixed at the same time**: this contract's
`ServerTransportConfig` never actually enforced "authenticator: Required
if kind == 'http'" -- it was stated in the docstring only.
`ServerTransportConfig.__post_init__` now raises `ValueError` for
`kind="http"` missing any of `host`/`port`/`authenticator`, matching
`MCPServerConfig`'s own validation pattern (`contracts/mcp-integration.md`).

**`AuthenticationError` is no longer constructed or raised anywhere in
this module** -- `RequireAuthMiddleware` rejects unauthenticated requests
with a real HTTP 401 response, not a Python exception `FabricaMCPServer`
could catch and re-raise. Kept in the public error hierarchy for a caller
who wants one `FabricaMCPServerError` subtree to catch, but nothing in
`src/fabrica/mcp/server.py` raises it.

**`UnsupportedTransportError` (originally sketched for an unimplemented
HTTP path) no longer exists** -- both transports are real now, so there
is nothing left for it to guard.

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
    """Required if kind == "http" -- HTTP connections need bearer-token
    resolution to an agent_id (mcp-server.md's resolved auth layer). None
    for stdio -- see stdio_agent_id below."""
    stdio_agent_id: str = "mcp-stdio-client"

    def __post_init__(self) -> None:
        """Real gap found and fixed during implementation: this contract's
        first draft stated "required if kind == 'http'" only in a
        docstring, never enforced it. Enforced now -- raises ValueError
        for kind="http" missing any of host/port/authenticator, matching
        MCPServerConfig's own __post_init__ validation pattern
        (contracts/mcp-integration.md)."""
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
  wire details) — comes from the real `mcp` library (`Server.run`,
  `Server.streamable_http_app`), not reimplemented here.
- **The legacy SSE transport** (distinct from the modern "streamable HTTP"
  transport implemented here) — streamable HTTP is the currently-
  recommended transport in the real `mcp` SDK; building the deprecated
  transport first would be backwards.
- **Token issuance, rotation, or revocation** — `TokenAuthenticator` is the
  boundary; how an operator actually mints/manages tokens is out of scope,
  per `mcp-server.md` open question 1.
- **Rate limiting** — deliberately absent; inherited from `SandboxPool`'s
  existing bounded-overflow protection, per the design doc's stated reuse.

## Open items for implementation

1. ~~`fabrica_prompts_list` has no backing method~~ **Resolved**: `list_names()`
   added directly to `contracts/prompts.md`'s `PromptStore`/`PromptManager`
   while writing this handler, rather than left as a dangling gap.
2. ~~`WeakIsolationError`'s real tier check cannot run at all yet~~
   **Resolved**: `contracts/sandbox.md` now specifies a real, queryable
   `Sandbox.tier`/`SandboxPool.tier` (its own "Real addition" section) --
   `FabricaMCPServer.__init__` checks `fabrica.tools.tier < 2` for real
   and raises `WeakIsolationError` when true and
   `allow_weak_isolation_for_external_callers` is false, tested directly
   against a real `CivitasBridge`-built `Fabrica`. **Still genuinely
   open, a DIFFERENT, smaller gap than before**: the check runs once, at
   construction -- if a service-mode deployment's tier changed live
   after `FabricaMCPServer` is already running, there is still no
   re-check. Also honestly worth restating: only Tier 0
   (`SubprocessSandbox`) exists anywhere in this codebase today, so every
   real HTTP deployment must currently pass
   `allow_weak_isolation_for_external_callers=True` to construct at all
   -- the fail-closed default working as intended, not a bug.
3. ~~Multi-tenant HTTP deployments… doesn't stress-test MANY simultaneous
   distinct `agent_id`s against shared `SandboxPool`/`Retriever` state
   under real concurrent load.~~ **Resolved**:
   `tests/mcp/test_server_stress.py` -- a real `uvicorn` server, 10
   simultaneous agents (distinct bearer tokens, distinct `agent_id`s,
   each opening its OWN real `mcp.client.streamable_http` connection via
   `asyncio.gather`, not sequential calls) contending for the same
   `SandboxPool`/`Retriever`. Three real findings, not just "it didn't
   crash": (1) concurrent `fabrica_memory_write`/`fabrica_memory_search`
   across 10 agents at once -- each agent's search saw EXACTLY its own
   item, proving `Scope` isolation holds under genuine concurrent
   write/search pressure, not just the sequential case already covered
   elsewhere; (2) 10 agents running code-mode concurrently against
   `max_concurrent=4` -- the bounded-overflow queue correctly served all
   10 with each agent's own distinct computed result intact (no
   cross-talk between queued/overflowed executions sharing the pool);
   (3) the extreme case, `max_concurrent=1` forcing full serialization of
   all 10 -- completed correctly within a bounded overall wait, proving
   the failure mode under maximum contention is queuing, never a
   deadlock or hang. All three tests pass consistently across repeated
   runs, no flakiness observed.
4. Zero spike coverage — carried forward unchanged. Real, working tests
   exist (`tests/mcp/test_server.py`) proving correctness; a spike would
   be about production-scale characteristics (latency, concurrent
   connection limits under `uvicorn`), which is a different question.
