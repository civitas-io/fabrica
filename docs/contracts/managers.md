# Contract: `PresidiumClient.check_grant`, `execute_in_sandbox`, `ToolManager`, `SkillManager`

**Status:** Contract — implementation-ready · **Last updated:** 2026-08
**Depends on:** [contracts/retriever.md](retriever.md), [contracts/sandbox.md](sandbox.md),
[system-design.md](../system-design.md) §1 (composition-over-inheritance resolution)

> **The real implementation exists now (2026-08-23/24):**
> `fabrica.presidium.rest_client.RestPresidiumClient` -- REST + mTLS, circuit-breaker
> protected, fail-closed on every unreachable/malformed condition, exactly matching this
> contract's own spec below. Built against `civitas-io/presidium`'s own real, shipped M7
> server, verified both with `httpx.MockTransport` (deterministic unit tests) and a real
> end-to-end test booting an actual Presidium server with real mTLS certs
> (`tests/presidium/test_rest_client_real_presidium_server.py`). Requires the
> `fabrica[presidium]` extra (`httpx`) -- `PresidiumClient` itself stays a duck-typed
> Protocol, unaffected. See `fabrica.presidium.rest_client`'s own module docstring for
> the exact wire contract confirmed against Presidium's real source.
>
> **A second real implementation exists too (2026-08-25):**
> `fabrica.presidium.in_process_client.InProcessPresidiumClient` -- for single-node
> deployments that embed a real `presidium.GovernedRuntime` directly, no network hop, no
> mTLS setup, matching Civitas's own in-process-vs-distributed transport duality. Speaks
> the exact same logical contract as `RestPresidiumClient` (same `Scope`-to-`parameters`
> flattening, same `resource`/fixed-`"invoke"`-`action` convention), just without JSON/HTTP
> in between -- verified with a real `GovernedRuntime` (real `InMemoryRegistry`, real
> `CelPolicyEngine`), nothing mocked, since there's nothing to mock in-process. Requires the
> `fabrica[presidium-inprocess]` extra (the real `presidium` package, not `httpx`).

Four things in one doc because they're genuinely coupled: the shared helper is
the primary consumer of the grant check, and both managers are thin wrappers
around the helper. Splitting them into separate files would fragment content
that only makes sense read together.

---

## `PresidiumClient.check_grant` — the one method `PresidiumClient` has

Never previously given a real signature — `system-design.md` only established
*that* it's synchronous, REST+mTLS, circuit-breaker protected, fail-closed.

```python
@dataclass(frozen=True)
class GrantResult:
    decision: Literal["allow", "deny", "require_approval"]
    reason: str | None = None
    approval_context: dict | None = None
    """Opaque payload passed through to Civitas's durable-suspension
    mechanism when decision == "require_approval". This contract does not
    interpret it — HITL suspend/resume is a Civitas/Presidium primitive
    out of scope here (civitas-presidium-integration.md)."""


class PresidiumClient:
    async def check_grant(
        self, *, agent_id: str, action: str, scope: Scope
    ) -> GrantResult:
        """`action` is a free-form string Presidium interprets — e.g.
        "code_mode", "skill_run:skill_name". `scope` is the same Scope
        used by MemoryStore and the usage-ledger span attributes
        (memory.md, system-design.md §7) — one Scope type, reused, not
        redefined per surface.

        CRITICAL: never raises for a Presidium-unreachable condition.
        Returns GrantResult(decision="deny") instead — fail-closed must
        be a plain return value the caller is forced to check, not an
        exception a broad `except:` somewhere upstream could
        accidentally swallow and treat as permissive. An exception here
        would risk inverting the entire fail-closed guarantee by
        accident, not by design.

        Circuit-breaker protected: after N consecutive failures, trips
        open and returns deny immediately (no fresh timeout wait per
        call) until a cooldown elapses, then half-opens to test recovery
        — system-design.md §6.
        """
```

---

## `execute_in_sandbox` — the shared orchestration, in exactly one place

```python
class GrantDeniedError(Exception):
    """Raised by execute_in_sandbox after check_grant explicitly
    returned deny. Distinct from check_grant's own contract (which never
    raises) — by the time this is raised, the check has already
    happened deterministically; there's no ambiguity to accidentally
    swallow."""
    def __init__(self, reason: str | None) -> None: ...


class ApprovalRequiredError(Exception):
    """Raised when check_grant returns require_approval. Carries
    approval_context through for the caller to hand to Civitas's
    durable-suspension mechanism — implementing that mechanism is out
    of scope for this contract."""
    def __init__(self, approval_context: dict | None) -> None: ...


async def execute_in_sandbox(
    *,
    presidium_client: PresidiumClient,
    sandbox_pool: SandboxPool,
    action: str,
    agent_id: str,
    scope: Scope,
    code: str,
    on_tool_call: ToolCallCallback,
    timeout: float = 30.0,
) -> RunResult:
    """The one implementation of check_grant -> acquire -> run -> release
    -> span, used by both ToolManager.run_code() and SkillManager.run()
    (system-design.md §1's composition-over-inheritance resolution — this
    function is the "composition," not a base class).

    Sequence:
    1. check_grant(agent_id, action, scope) — raises GrantDeniedError or
       ApprovalRequiredError immediately on deny/require_approval; no
       sandbox is ever acquired for a denied or pending action.
    2. sandbox_pool.acquire() — may raise SandboxPoolExhaustedError.
    3. sandbox_pool.run(handle, code, on_tool_call=..., timeout=timeout)
       — may raise SandboxTimeoutError or SandboxCrashedError.
    4. sandbox_pool.release(handle) — ALWAYS called, via try/finally,
       regardless of whether step 3 succeeded, returned success=False,
       or raised. A released handle's instance is always terminated
       (contracts/sandbox.md) — this function never attempts to reuse it.
    5. Emits fabrica.tool.code_mode.run (or the skill-execution
       equivalent), with Scope fields as span attributes — this is how
       usage reaches Presidium (system-design.md §7), not a separate
       call this function makes.

    Sandbox-level exceptions (steps 2–3) propagate unchanged — this
    function does not wrap them in a new error type, keeping the error
    hierarchy flat rather than adding a redundant layer.
    """
```

---

## `ToolManager`

```python
class ToolManager:
    def __init__(
        self, retriever: Retriever, sandbox_pool: SandboxPool,
        presidium_client: PresidiumClient,
    ) -> None: ...

    async def register(self, namespace: ToolNamespace) -> None:
        """Registers every tool in namespace as Indexable(kind="tool")
        with the shared Retriever. Delegates idempotency to
        Retriever.register — re-registering an identical namespace is a
        no-op, not an error.

        Corrected from an earlier sync signature: `Retriever.register()`
        is `async def` (contracts/retriever.md) — a sync method cannot
        properly await it. Found by trying to implement this for real,
        not caught by review beforehand."""

    async def find(self, query: str, *, limit: int = 5) -> list[RankedMatch]:
        """The find() fallback (tool-execution.md) for hosts that can't
        run code-mode. Thin delegation to
        retriever.search(query, kind="tool", limit=limit) — no logic of
        its own beyond fixing kind="tool"."""

    async def run_code(
        self, code: str, *, agent_id: str, scope: Scope, timeout: float = 30.0,
    ) -> RunResult:
        """The code-mode headline path. Delegates to execute_in_sandbox
        with action="code_mode" and on_tool_call wired to actually
        invoke the registered ToolNamespace's real functions — this is
        where "real tool access" is implemented, not inside
        execute_in_sandbox itself, which knows nothing about what a
        tool call actually does."""

    @property
    def tier(self) -> int:
        """Real addition, closing contracts/mcp-server.md's own flagged
        gap: delegates to sandbox_pool.tier (contracts/sandbox.md's own
        "Real addition" section) — read-only, ToolManager never changes
        tier itself. Added specifically so FabricaMCPServer can check
        isolation strength without reaching into ToolManager's private
        SandboxPool reference directly."""
```

---

## `SkillManager`

```python
class SkillParseError(Exception):
    """Raised by load() on a malformed SKILL.md — missing required
    frontmatter fields, name charset violation, or description over
    1024 chars, per the real-spec field-by-field check in
    skills-gateway.md."""


class SkillNotFoundError(Exception):
    """Raised by run() when name isn't a registered skill."""


class SkillManager:
    def __init__(
        self, retriever: Retriever, sandbox_pool: SandboxPool,
        presidium_client: PresidiumClient,
    ) -> None: ...

    async def load(self, skill_dir: Path) -> None:
        """Parses a SKILL.md package (frontmatter + body + optional
        scripts/assets/references — skills-gateway.md's real-spec
        check found zero bigpowers skills exercising the bundled-file
        path, so this is genuinely less-tested ground than frontmatter
        parsing). Registers as Indexable(kind="skill").

        Corrected from an earlier sync signature, resolving open item 3
        below: it was kept sync specifically "for parity with
        ToolManager.register()" — but ToolManager.register() itself was
        just corrected to async (it has to await Retriever.register()).
        The same parity argument now means load() must be async too, for
        the identical reason: it needs to await Retriever.register() to
        index the loaded skill.

        Frontmatter fields: `name`/`description` required, `script`
        optional (find()-discoverable-only skill if absent), `eager`
        optional bool, default false — resolves retrieval.md's open item 2
        (author-declared, per-item, matching `ToolSchema.eager`'s
        identical resolution).

        Raises:
            SkillParseError: malformed frontmatter.
        """

    async def find(self, query: str, *, limit: int = 5) -> list[RankedMatch]:
        """Thin delegation to retriever.search(query, kind="skill", ...)
        — same shape as ToolManager.find(), different kind."""

    async def run(
        self, name: str, args: dict, *, agent_id: str, scope: Scope,
        timeout: float = 30.0,
    ) -> RunResult:
        """Runs a NAMED, pre-written, author-trusted script — not
        arbitrary generated code, the genuine difference from
        run_code() that justified keeping these as separate classes
        (system-design.md §1). Delegates to execute_in_sandbox with
        action=f"skill_run:{name}".

        Raises:
            SkillNotFoundError: name isn't registered.
        """

    @property
    def tier(self) -> int:
        """Real addition, same rationale as ToolManager.tier above --
        delegates to sandbox_pool.tier."""
```

---

## What this contract deliberately does not cover

- **The actual durable-suspension/HITL resume flow** triggered by
  `ApprovalRequiredError` — a Civitas/Presidium primitive
  (`civitas-presidium-integration.md`), not implemented here. This contract's
  job ends at raising the error with enough context to act on.
- **`ToolNamespace`'s own shape** (`stubs()`/`open()`/`call()`) — `tool-execution.md`'s
  concern, referenced here as a dependency, not redefined.
- **`SKILL.md` parsing internals** — this contract specifies `load()`'s error
  behavior, not the YAML/Markdown parsing implementation itself.

## Real addition: `tracer` DI, closing system-design.md §7's biggest gap

`ToolManager`/`SkillManager` and `execute_in_sandbox` now accept an
optional `tracer: fabrica.observability.Tracer | None = None`, defaulting
to `NullTracer()` -- same DI shape as `PresidiumClient`/`Summarizer`
everywhere else. Real spans: `fabrica.tool.find`/`fabrica.skill.find`
(nesting `Retriever.search()`'s own span underneath), and
`fabrica.tool.code_mode.run`/`fabrica.skill.run` as the real PARENT of
`fabrica.presidium.check_grant`, `fabrica.sandbox.acquire`, and
`fabrica.sandbox.run` -- one real nested tree via `trace_id`/
`parent_span_id`, not four spans sharing a name prefix. Full design and
the real Civitas-Tracer finding that shaped this:
[system-design.md §7](../system-design.md#7-observability-spans-this-system-emits).

**Later addition**: `fabrica.tool.find`/`fabrica.skill.find` also carry
`volume_bytes` -- real bytes of the returned `Indexable.description`
fields, the context-footprint metering dimension
(`civitas-presidium-integration.md`) extended here to match what
`MemoryManager.write()`/`search()` and (later) `PromptManager.get()`/
`put()` already emit. `description` specifically, not the whole
`Indexable` object serialized -- it's the field `retriever/types.py`
itself calls out as "the only field actually embedded/matched", the
realistic proxy for what a caller actually renders into a model's
context, not administrative metadata like `id`/`kind`/`eager`.

## Open items for implementation

1. ~~Should `find()` on both managers accept a `kind` override...~~
   **Resolved: no override, `find()` stays fixed per manager.** A caller
   genuinely needing "search everything" already has a real, existing way
   to get it -- call `Retriever.search(query, kind=None)` directly, which
   already supports searching across all kinds (`contracts/retriever.md`).
   Adding a `kind` override to `ToolManager.find()`/`SkillManager.find()`
   would mean re-exposing a capability the shared `Retriever` already
   provides, through two managers that are deliberately kept separate
   for their different trust models (`architecture.md §1a`) -- coupling
   them to a generality neither actually needs on its own is the exact
   pattern that principle argues against. No code change: this was
   already the implemented behavior, only the open question needed
   closing.
2. ~~Span-naming convention for skills vs. tools~~ **Resolved while writing this
   contract** — `system-design.md §7` was missing a `SkillManager` row entirely,
   not just an inconsistent name. Added `fabrica.skill.find` and
   `fabrica.skill.run`, mirroring `ToolManager`'s pair.
3. ~~Whether `SkillManager.load()` should be async~~ **Resolved: yes, async.**
   Not for the file-I/O reason originally considered here (large bundled
   assets) — for a more basic one found while implementing: `load()` must
   await `Retriever.register()`, which is itself `async def`
   (`contracts/retriever.md`). `ToolManager.register()` was corrected to
   `async` for the identical reason. Both signatures above are now async.
4. `ToolNamespace.list_schemas()` — added to `tool-execution.md` while
   implementing `register()`, since `stubs()`'s formatted-string output
   gave `ToolManager` no way to enumerate a namespace's tools to build
   `Indexable`s. Not stress-tested against a namespace with a very large
   tool count, where building the full list eagerly on every `register()`
   call could matter.
