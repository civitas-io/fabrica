# Contract: `CivitasBridge`

**Status:** Contract — implementation-ready, with one interface explicitly
provisional (see below) · **Last updated:** 2026-08
**Depends on:** [system-design.md §1–§2](../system-design.md) (the corrected
"requests, never reaches in" design), [contracts/memory.md](memory.md)
(`NullCompactor`, resolved together with this contract)
**Sixth and final contract** in the object model — `Retriever`, `Sandbox`,
`managers.md`, `memory.md`, `prompts.md` precede this one.

The one component `architecture.md §1a` explicitly grants permission to
integrate tightly. This contract's job is stating exactly where that license
ends, not just what it can do.

---

## Scope, decided through direct discussion before this was written

1. **Construction-time wiring only.** `CivitasBridge` assembles the object
   graph once (`build() -> Fabrica`) and is inert afterward. It does not
   orchestrate per-turn, cross-manager behavior — that would duplicate
   Civitas's own runtime loop. A bounded, opt-in, read-only-preferred
   extension is deliberately left possible later, not built now: `build()`
   returns every manager as a public `Fabrica` attribute (never hidden
   exclusively behind `CivitasBridge`), so a later narrow convenience method
   needs no new privileged access — `CivitasBridge` already holds every
   reference it would need, from construction.
2. **Requests, never reaches in.** `CivitasBridge` never touches a
   supervision tree or a `StateStore` directly. It calls `request_supervision`/
   `request_state_persistence`; Civitas's own runtime decides how to fulfill
   each request. Applied consistently — this is also why `PromptManager`/
   `MemoryManager` route their persistence through `CivitasBridge`, not
   directly to Civitas (`system-design.md §1`'s correction).
3. **External dependencies are always fully-constructed objects, never raw
   config values `CivitasBridge` would translate itself.** Established with
   `Summarizer`, then applied to `PresidiumClient` for the same reason: a bare
   config value (e.g. an endpoint string) would be insufficient anyway
   (mTLS needs certs, not just a URL) and would otherwise force
   `CivitasBridge`'s constructor to slowly absorb another object's entire
   configuration surface. Convenience factories (`PresidiumClient.from_endpoint(...)`)
   belong on the dependency's own class, never on `CivitasBridge`.

---

## An explicitly provisional type — read before the rest

```python
class CivitasRuntime(Protocol):
    """PROVISIONAL. Fabrica does not have visibility into Civitas's actual
    registration API in this contract-writing session -- python-civitas's
    own docs confirm real vocabulary (`Supervisor` with ONE_FOR_ONE/
    ONE_FOR_ALL/REST_FOR_ONE strategies, `GenServer`), but not the exact
    method signature a library uses to register one. This Protocol is
    Fabrica's best current guess at the shape it needs, written so this
    contract is concrete enough to implement against -- NOT a claim about
    what Civitas's real SDK looks like. Expect this to be replaced by, or
    reconciled with, Civitas's actual runtime-handle type before
    CivitasBridge ships. Same honesty standard as `Message` in
    contracts/memory.md, which has the identical caveat for a different
    reason."""
    async def supervise(self, spec: SupervisionSpec) -> SupervisionHandle: ...
    async def persist(self, component_name: str) -> StateHandle: ...
```

---

## Types

```python
@dataclass(frozen=True)
class SupervisionSpec:
    component_name: str
    restart_strategy: Literal["one_for_one", "one_for_all", "rest_for_one"]
    """Matches Civitas's own Supervisor vocabulary directly (not an
    invented Fabrica-specific term) -- see python-civitas's design docs."""


class SupervisionHandle(Protocol):
    """Opaque to CivitasBridge beyond identity. Returned so a future
    extension COULD act on it (e.g. explicit shutdown) without
    CivitasBridge needing that capability today -- not exercised by
    anything in this contract."""
    component_name: str


class StateHandle(Protocol):
    """What a manager (PromptManager, MemoryManager) actually uses to
    read/write its own state via Civitas's StateStore, without ever
    holding a direct reference to the StateStore itself."""
    async def get(self, key: str) -> bytes | None: ...
    async def put(self, key: str, value: bytes) -> None: ...
    async def delete(self, key: str) -> None: ...
```

---

## Errors

```python
class CivitasBridgeError(Exception):
    """Base for CivitasBridge-specific errors."""


class UngovernedConfigurationError(CivitasBridgeError):
    """Raised at construction time (not at first check_grant call) when
    presidium_client is None and allow_ungoverned is False -- the default
    combination. Forces an explicit decision before Fabrica is usable at
    all, rather than allowing "nobody configured this" to silently become
    "everything is allowed" by omission."""


class RuntimeRequiredError(CivitasBridgeError):
    """Raised at construction time when mode="service" but
    civitas_runtime is None -- service mode is meaningless without
    something to register supervision/persistence requests against."""
```

`CompactionUnavailableError` (raised by `NullCompactor`, not by
`CivitasBridge` itself) is specified in `contracts/memory.md` — referenced
here, not redefined.

---

## `NullPresidiumClient`

```python
class NullPresidiumClient:
    """Wired in by CivitasBridge when presidium_client=None and
    allow_ungoverned=True. Implements PresidiumClient's one method,
    unconditionally allowing -- the opposite failure direction from
    NullCompactor (contracts/memory.md), and deliberately so: check_grant
    is mandatory hot-path control flow under code-mode itself (called by
    execute_in_sandbox on every execution), unlike compaction, which a
    harness invokes optionally. Failing closed here by default would make
    the platform's own headline feature unusable without Presidium --
    exactly the zero-infra-quickstart cost this exists to avoid, PROVIDED
    the caller has explicitly opted in via allow_ungoverned=True."""
    async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
        return GrantResult(decision="allow", reason="allow_ungoverned=True, no Presidium configured")
```

---

## `CivitasBridge`

```python
class CivitasBridge:
    def __init__(
        self,
        mode: Literal["library", "service"] = "library",
        *,
        summarizer: Summarizer | None = None,
        presidium_client: PresidiumClient | None = None,
        allow_ungoverned: bool = False,
        civitas_runtime: CivitasRuntime | None = None,
        overrides: dict[str, Literal["library", "service"]] | None = None,
    ) -> None:
        """
        Raises:
            UngovernedConfigurationError: presidium_client is None and
                allow_ungoverned is False.
            RuntimeRequiredError: mode="service" and civitas_runtime is None.

        summarizer=None is NOT an error -- MemoryManager receives a
        NullCompactor instead of RecencyCompactor (contracts/memory.md).
        Compaction becomes unavailable, not the whole construction.

        overrides is the v2 per-component mode-granularity hook
        (system-design.md §2) -- present in the signature from v1, even
        though nothing in this contract exercises anything beyond
        uniform mode selection yet. This is what makes v2 additive: the
        parameter already exists, only its effect grows later.
        """

    async def build(self) -> Fabrica:
        """Assembles the full object graph exactly once:
        1. Resolves each component's mode (uniform `mode`, unless
           `overrides` names it specifically).
        2. Constructs each backend: Sandbox (platform-dispatched,
           isolation.md), RetrieverBackend, PromptStore, MemoryStore,
           WorkingMemoryStore, Compactor (RecencyCompactor(summarizer) or
           NullCompactor if summarizer is None).
        3. Constructs the shared Retriever and SandboxPool.
        4. Constructs each manager with its dependencies injected --
           ToolManager, SkillManager get the shared engines +
           presidium_client (or NullPresidiumClient); MemoryManager,
           PromptManager get their state handles via
           request_state_persistence (service mode) or a local default
           (library mode, no request made at all).
        5. In service mode only, calls request_supervision once per
           component that needs it.
        6. Returns the assembled Fabrica facade, with every manager
           exposed as a public attribute (fabrica.tools, fabrica.skills,
           fabrica.memory, fabrica.prompts) -- never hidden exclusively
           behind CivitasBridge, per the scope note above.

        Idempotency of repeated build() calls on the same CivitasBridge
        instance is NOT specified here -- see Open items.
        """

    async def request_supervision(self, spec: SupervisionSpec) -> SupervisionHandle:
        """Only called during build(), in service mode. Delegates
        entirely to civitas_runtime.supervise(spec) -- CivitasBridge adds
        no logic of its own here beyond the call itself."""

    async def request_state_persistence(self, component_name: str) -> StateHandle:
        """Called during build() for PromptManager/MemoryManager in
        service mode. Delegates entirely to civitas_runtime.persist(...).
        In library mode, this is never called at all -- library-mode
        managers get a local default store directly, matching
        system-design.md's existing "no Civitas dependency in library
        mode" shape."""
```

---

## What this contract deliberately does not cover

- **`CivitasRuntime`'s real shape** — explicitly provisional, see above.
  This contract cannot be considered final until reconciled with Civitas's
  actual registration API.
- **The bounded runtime extension (a smaller scope of "B")** — deliberately
  left possible via `build()`'s public-attribute requirement, not designed
  here. No method for it exists in this contract.
- **Backend selection specifics** (which `RetrieverBackend`, which
  `MemoryStore` adapter) — `CivitasBridge` constructs sensible defaults per
  the zero-infra philosophy; overriding *which* adapter, as opposed to
  *which mode*, is not exposed by anything in this contract's signature.

## Open items for implementation

1. `CivitasRuntime`'s reconciliation with Civitas's actual SDK (above) — the
   most significant open item across all six contracts, since it's the one
   piece of a contract acknowledged as possibly wrong in its current form,
   not just incomplete.
2. `build()`'s idempotency on repeated calls — construct a second, independent
   `Fabrica` graph each time, return the same cached instance, or raise?
   Not decided.
3. Partial-failure behavior during `build()` — if `request_supervision`
   succeeds for `ToolManager` but fails for `SkillManager`, is the whole
   `build()` call rolled back, or does it return a partially-supervised
   `Fabrica`? Not specified; leans toward "roll back entirely," consistent
   with fail-closed patterns elsewhere, but not decided as a contract rule.
4. Whether `overrides`' per-component granularity (v2) needs its own
   validation — e.g., rejecting an override key that doesn't name a real
   component — is unspecified, since v1 doesn't exercise this path at all.
