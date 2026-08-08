# Contract: `CivitasBridge`

**Status:** Contract — implementation-ready. `CivitasRuntime` (below) has been
reconciled against `python-civitas`'s real source (`civitas/runtime.py`,
`civitas/genserver.py`, `civitas/plugins/state.py`), superseding the
originally-provisional sketch. · **Last updated:** 2026-08
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

## `CivitasRuntime` — grounded against real `python-civitas` source, not guessed

Reconciled by reading `civitas/runtime.py`, `civitas/genserver.py`, and
`civitas/plugins/state.py` directly, not just docs. Two real corrections to
what an earlier draft of this contract assumed:

1. **There is no "register a supervision spec" call.** Civitas's real
   mechanism is `Runtime.spawn(supervisor_name, agent_class, name, config, *,
   wait=True) -> str` — dynamically spawning an agent *into an
   already-existing, named `DynamicSupervisor`* that Civitas's own deployment
   topology defines. `CivitasBridge` does not create a supervisor or choose a
   restart strategy; it spawns into one Civitas already established. Returns
   the spawned agent's **name** (a string) on success, raises `SpawnError` on
   failure — not an opaque handle object.
2. **`StateStore` is far simpler than originally sketched**: keyed by
   `agent_name: str`, storing `dict[str, Any]` directly (`get`/`set`/`delete`/
   `list_agents`/`close`) — not a byte-oriented, opaquely-keyed store.

```python
class CivitasRuntime(Protocol):
    """The subset of civitas.runtime.Runtime's real public API CivitasBridge
    needs. Not a new interface Civitas must conform to -- civitas.runtime.Runtime
    already satisfies this shape today."""
    async def spawn(
        self, supervisor_name: str, agent_class: type[GenServer], name: str,
        config: dict[str, Any] | None = None, *, wait: bool = True,
    ) -> str: ...
    async def despawn(self, supervisor_name: str, name: str) -> None: ...


class SpawnError(Exception):
    """Re-exported from civitas.process -- not redefined by Fabrica.
    Raised by CivitasRuntime.spawn on failure (name collision, max_children
    reached, governance veto via on_spawn_requested, ...)."""
```

---

## Types

```python
class ComponentStateHandle(Protocol):
    """A StateStore access already bound to one component's name -- so a
    manager cannot accidentally read or write a different component's
    state by passing the wrong key. Thin wrapper over Civitas's real
    civitas.plugins.state.StateStore (get(agent_name)/set(agent_name, state)/
    delete(agent_name)), pre-bound to one name. CivitasBridge is the only
    thing holding a direct reference to the raw StateStore; managers only
    ever see this name-bound wrapper."""
    async def get(self) -> dict[str, Any] | None: ...
    async def set(self, state: dict[str, Any]) -> None: ...
    async def delete(self) -> None: ...
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
    """Raised at construction time when mode="service" but civitas_runtime,
    civitas_state_store, or dynamic_supervisor_name is None -- service mode
    is meaningless without a runtime and a named supervisor to spawn into,
    and state persistence needs the real StateStore. All three required
    together, per CivitasBridge.__init__'s Raises section."""
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
        civitas_state_store: StateStore | None = None,
        dynamic_supervisor_name: str | None = None,
        overrides: dict[str, Literal["library", "service"]] | None = None,
    ) -> None:
        """
        Raises:
            UngovernedConfigurationError: presidium_client is None and
                allow_ungoverned is False.
            RuntimeRequiredError: mode="service" and civitas_runtime,
                civitas_state_store, or dynamic_supervisor_name is None.
                All three are required together in service mode -- spawning
                needs a runtime AND a named, already-existing
                DynamicSupervisor to spawn into; state persistence needs
                the real StateStore.

        summarizer=None is NOT an error -- MemoryManager receives a
        NullCompactor instead of RecencyCompactor (contracts/memory.md).
        Compaction becomes unavailable, not the whole construction.

        dynamic_supervisor_name names a DynamicSupervisor Civitas's own
        deployment topology must already define -- CivitasBridge spawns
        managers INTO it, per civitas.runtime.Runtime.spawn's real
        contract; it never creates a supervisor of its own.

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
           PromptManager get a ComponentStateHandle via
           request_state_persistence (service mode) or a local default
           store directly (library mode, no request made at all).
        5. In service mode only, calls request_supervision once per
           GenServer-backed manager, spawning it into
           dynamic_supervisor_name.
        6. Returns the assembled Fabrica facade, with every manager
           exposed as a public attribute (fabrica.tools, fabrica.skills,
           fabrica.memory, fabrica.prompts) -- never hidden exclusively
           behind CivitasBridge, per the scope note above.

        Idempotency of repeated build() calls on the same CivitasBridge
        instance is NOT specified here -- see Open items.
        """

    async def request_supervision(
        self, agent_class: type[GenServer], name: str, config: dict[str, Any] | None = None,
    ) -> str:
        """Only called during build(), in service mode. Delegates entirely
        to civitas_runtime.spawn(dynamic_supervisor_name, agent_class, name,
        config) -- CivitasBridge adds no logic of its own beyond supplying
        the supervisor name it was configured with. Returns the spawned
        agent's name (matching Runtime.spawn's real return type).

        Raises:
            SpawnError: propagated unchanged from civitas_runtime.spawn --
                name collision, capacity limits, or a governance veto via
                Civitas's own on_spawn_requested hook.
        """

    async def request_state_persistence(self, component_name: str) -> ComponentStateHandle:
        """Called during build() for PromptManager/MemoryManager in
        service mode. Returns a ComponentStateHandle wrapping
        civitas_state_store, pre-bound to component_name -- the manager
        never sees the raw StateStore or any other component's name. In
        library mode, this is never called at all -- library-mode
        managers get a local default store directly, matching
        system-design.md's existing "no Civitas dependency in library
        mode" shape."""
```

---

## What this contract deliberately does not cover

- **Any registration mechanism beyond `spawn`/`despawn`** — `civitas.runtime.Runtime`
  has a much larger surface (`stop_agent`, health probing, remote workers,
  `spawn_into`, cross-tree authorization via `spawner_allowlist`); this
  contract only specifies the subset `CivitasBridge` actually needs, not a
  full wrapper of Civitas's runtime API.
- **`DynamicSupervisor` construction or configuration** — `dynamic_supervisor_name`
  must already exist in Civitas's own deployment topology; `CivitasBridge`
  never creates, configures, or tunes restart strategy/capacity limits for it.
- **The bounded runtime extension (a smaller scope of "B")** — deliberately
  left possible via `build()`'s public-attribute requirement, not designed
  here. No method for it exists in this contract.
- **Backend selection specifics** (which `RetrieverBackend`, which
  `MemoryStore` adapter) — `CivitasBridge` constructs sensible defaults per
  the zero-infra philosophy; overriding *which* adapter, as opposed to
  *which mode*, is not exposed by anything in this contract's signature.

## Open items for implementation

1. Whether `CivitasBridge` should validate that `dynamic_supervisor_name`
   actually resolves to a real `DynamicSupervisor` before attempting any
   `spawn` calls (a clearer, earlier error), or let the first `spawn` call's
   `SpawnError` surface a misconfiguration — not decided; the real API gives
   no dedicated "does this supervisor exist" check to call first.
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
