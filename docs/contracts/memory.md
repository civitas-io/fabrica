# Contract: `WorkingMemoryStore`, `Compactor`/`Summarizer`, `MemoryStore`, `MemoryManager`

**Status:** Contract — implementation-ready · **Last updated:** 2026-08
**Depends on:** [memory.md](../memory.md) (the three-facet design this formalizes),
[contracts/managers.md](managers.md) (`Scope`, the DI pattern this reuses a third time)

Four things in one doc, same reasoning as `managers.md`: `MemoryManager` is a
thin facade over the other three, and none of them makes sense read in isolation.

---

## Types

```python
@dataclass(frozen=True)
class MemoryItem:
    id: str | None            # None before write() assigns one
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    score: float | None = None
```

**`score` is kept, deliberately unlike `RankedMatch`.** `Retriever`'s
`RankedMatch` has no score field on purpose — spike evidence showed tool
retrieval's correct/incorrect matches share the same low score band, so
thresholding there is actively misleading. **No equivalent evidence exists for
memory recall.** Memory search legitimately needs variable-length results (0
relevant memories some calls, 10 others), which requires *some* cutoff
decision — copying the no-score rule here without counter-evidence would be
applying a pattern by reflex, not by reasoning. Kept until a memory-specific
spike says otherwise.

```python
@dataclass(frozen=True)
class Message:
    """Provisional. NOT yet reconciled with whatever Civitas's own runtime
    loop uses internally for conversation history (memory.md open question
    8) — defined here only so this contract is concrete enough to implement
    against. Expect conversion to/from Civitas's real representation before
    MemoryManager ships."""
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tokens: int
    """Required, not optional — deliberately. Estimating token counts would
    require Fabrica to bundle or guess at a model-specific tokenizer
    (tiktoken is OpenAI-specific, others differ) — a real, avoidable
    dependency. The harness already has this number from the model
    provider's own usage reporting on every response; requiring it here
    costs nothing and avoids inventing a second source of truth."""


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    preserved: list[Message]
    tokens_before: int
    tokens_after: int
```

`Scope` is unchanged from `contracts/managers.md` / `memory.md` — not
redefined here.

---

## Errors

```python
class MemoryError(Exception):
    """Base for all MemoryManager/MemoryStore/WorkingMemoryStore/Compactor
    errors."""


class MemoryBackendError(MemoryError):
    """The configured long-term adapter failed (unreachable, malformed
    response, etc). Wraps whatever the underlying library raised
    (a Mem0 exception, a Zep SDK error, ...) so callers depend on ONE
    error type regardless of which adapter is configured. This is the
    actual point of the protocol+adapters pattern (memory.md) — made a
    contract guarantee here, not left implicit."""
    def __init__(self, backend: str, cause: Exception) -> None: ...


class WorkingMemoryQuotaExceeded(MemoryError):
    """remember() would exceed the per-scope size ceiling. Raised, not
    silently accepted or truncated — unlike RunResult.stdout
    (contracts/sandbox.md), key-value data has no natural truncation
    point, so silently dropping part of it would be worse than
    rejecting the write outright."""


class CompactionError(MemoryError):
    """The injected Summarizer raised during compact(). Not swallowed or
    silently downgraded to naive truncation — the harness already owns
    its own retry/fallback policy (it called compact() in the first
    place because it tracks its own token budget); this contract's job
    is to surface the failure clearly, not decide what happens next."""
    def __init__(self, cause: Exception) -> None: ...
```

**`recall()`, `get()`, and `forget()`/`clear()` on an unknown key/id are *not*
errors** — a missing key returns `None`; forgetting/clearing something absent
is a no-op. Consistent with the no-op-on-unknown precedent already established
for `Retriever.deregister()`.

---

## `WorkingMemoryStore`

```python
class WorkingMemoryStore(Protocol):
    async def remember(self, scope: Scope, key: str, value: Any) -> None:
        """Overwrites any existing value for (scope, key) — last-write-wins,
        no versioning. Keyed by the FULL Scope tuple, not just
        session_id, even though session_id is the dominant discriminator
        in practice — avoids leaking working memory across agents that
        happen to share a session_id in some deployment shape.

        Raises:
            WorkingMemoryQuotaExceeded: this write would exceed the
                per-scope size ceiling (default implementation:
                configurable, e.g. 256KB serialized).
        """

    async def recall(self, scope: Scope, key: str) -> Any | None:
        """None if (scope, key) was never remembered or has been
        cleared — not an error."""

    async def snapshot(self, scope: Scope) -> dict[str, Any]:
        """Everything currently stored for scope. The only sanctioned
        way to move something into long-term memory (memory.md: no
        promote() bridge) — a harness reads via snapshot()/recall() and
        writes into MemoryStore itself, explicitly."""

    async def clear(self, scope: Scope) -> None:
        """Wipes everything for scope. No-op if scope has nothing
        stored."""
```

Default implementation: an in-process dict keyed by `(scope-tuple, key)` —
zero infra, matching the platform-wide default philosophy. Does not survive a
process restart on its own; survives only via Civitas's `StateStore`
checkpointing (`memory.md`'s Integration section) — this contract does not
implement that checkpointing itself.

---

## `Summarizer`, `Compactor`, `RecencyCompactor`

```python
class Summarizer(Protocol):
    """Injected dependency — Fabrica never constructs one itself, never
    holds model credentials. The harness supplies this at MemoryManager
    construction time, wrapping whichever model connection it already
    has (or a smaller/cheaper model dedicated to summarization)."""
    async def summarize(self, messages: list[Message], *, target_tokens: int) -> str: ...


class Compactor(Protocol):
    async def compact(self, messages: list[Message], *, budget_tokens: int) -> CompactionResult:
        """Raises CompactionError if the injected Summarizer raises."""


class RecencyCompactor:
    """Default Compactor. Preserves the most recent messages verbatim,
    folds everything older into one summary via the injected Summarizer.

    Algorithm: walk backward from the newest message, preserving each
    one verbatim while (a) fewer than preserve_last_n have been kept,
    and (b) their cumulative tokens stay under budget_tokens.
    preserve_last_n is a ceiling, not a guarantee — if the last
    preserve_last_n messages alone already exceed budget_tokens, fewer
    are preserved, not more dropped from the summary side.

    Remaining (older) messages are summarized with
    target_tokens = budget_tokens - (tokens consumed by preserved messages).
    """
    def __init__(self, summarizer: Summarizer, *, preserve_last_n: int = 6) -> None: ...


class CompactionUnavailableError(MemoryError):
    """Raised by NullCompactor.compact() -- compaction was invoked, but
    CivitasBridge was constructed with summarizer=None, so no model
    connection was ever configured to perform it."""


class NullCompactor:
    """What CivitasBridge wires into MemoryManager when summarizer=None
    at construction (a decision walked through before this contract was
    written -- HANDOFF.md). Implements the Compactor protocol like any
    other implementation -- a Null Object, not a special case.

    This matters for a concrete reason, not just tidiness: MemoryManager's
    constructor requires a Compactor (contracts/memory.md's facade
    section) -- making it Optional instead would push an `if self._compactor
    is None` branch into every call site inside MemoryManager that touches
    compaction. Standard Null Object pattern instead: MemoryManager always
    receives A Compactor, full stop; NullCompactor just always raises
    CompactionUnavailableError the moment compact() is actually invoked.
    The 'is compaction configured?' question is answered once, at
    CivitasBridge construction time, not repeatedly at every call site."""
    async def compact(self, messages: list[Message], *, budget_tokens: int) -> CompactionResult:
        raise CompactionUnavailableError(
            "No Summarizer was configured -- construct CivitasBridge with "
            "summarizer=... to enable compaction."
        )
```

**Resolved: fold it into the summary, like any other message that doesn't
fit the recency window.** If a *single* message's `tokens` already exceeds
`budget_tokens` (a pathological but possible input), `_select_preserved`
preserves zero messages -- a literal, mechanical extension of the stated
algorithm, not a special case bolted on to paper over the gap -- and that
message joins everything else headed to the injected `Summarizer`.
Deliberately NOT truncated (would need a tokenizer dependency
`Message.tokens` was specifically designed to avoid, and could cut meaning
arbitrarily mid-sentence) and NOT dropped silently (would lose information
without signaling that happened). `CompactionResult.preserved == []` is
itself the observable signal a caller needs -- no separate flag was added,
since the existing shape already communicates "nothing survived verbatim
this round" to anyone who checks it. Whether the Summarizer's own
underlying model can actually compress a message that large is outside
Fabrica's visibility or control -- `Summarizer` is an injected dependency
with no model connection Fabrica constructs or credentials Fabrica holds,
so this genuinely is not Fabrica's problem to solve here.

---

## `MemoryStore` (long-term)

```python
class MemoryStore(Protocol):
    async def write(self, scope: Scope, item: MemoryItem) -> str:
        """Returns the assigned id. Raises MemoryBackendError on adapter
        failure."""

    async def search(self, scope: Scope, query: str, limit: int = 5) -> list[MemoryItem]:
        """Raises MemoryBackendError on adapter failure. Returns []
        (not an error) if scope has no memories at all — an empty
        scope is a normal state, not a failure."""

    async def get(self, scope: Scope, id: str) -> MemoryItem | None: ...

    async def forget(self, scope: Scope, id: str) -> None: ...
```

Unchanged in shape from `memory.md`'s sketch — this section exists to attach
the error contract (`MemoryBackendError`, empty-result-is-not-an-error), not
to redesign the interface.

---

## `MemoryManager` — the facade

```python
class MemoryManager:
    def __init__(
        self, working: WorkingMemoryStore, long_term: MemoryStore, compactor: Compactor,
    ) -> None: ...

    # working memory — thin delegation
    async def remember(self, scope: Scope, key: str, value: Any) -> None: ...
    async def recall(self, scope: Scope, key: str) -> Any | None: ...

    # compaction — thin delegation; MemoryManager stays ignorant of
    # summarization mechanics entirely, per memory.md's Civitas-decides-
    # when / Fabrica-decides-how split
    async def compact(self, messages: list[Message], *, budget_tokens: int) -> CompactionResult: ...

    # long-term memory — thin delegation
    async def write(self, scope: Scope, item: MemoryItem) -> str: ...
    async def search(self, scope: Scope, query: str, limit: int = 5) -> list[MemoryItem]: ...
    async def get(self, scope: Scope, id: str) -> MemoryItem | None: ...
    async def forget(self, scope: Scope, id: str) -> None: ...
```

`MemoryManager` adds no logic of its own beyond construction-time composition
— every method is a direct pass-through to one of the three injected
backends. This is intentional: unlike `execute_in_sandbox` (real shared
orchestration logic across `ToolManager`/`SkillManager`), there is no
cross-facet orchestration here to centralize. The facade exists purely so a
harness holds one object, not three.

---

## What this contract deliberately does not cover

- **`fabrica-contrib[mem0|zep|letta|cognee|langmem]` adapter internals** —
  each adapter's job is to implement `MemoryStore` and translate its own
  failures into `MemoryBackendError`; this contract specifies the boundary,
  not each adapter's implementation.
- **Civitas's `StateStore` checkpointing** of working memory and the default
  long-term store — referenced as a dependency (`memory.md`'s Integration
  section), mediated through `CivitasBridge.request_state_persistence`
  (`system-design.md §1`'s correction), not specified here.
- **Whether `CivitasBridge` was constructed with a `Summarizer`** — out of
  scope for this contract; `NullCompactor` documents the behavior when it
  wasn't, but the decision of whether/how a harness supplies one belongs to
  `CivitasBridge`'s own contract, not yet written.
- **`Message` ↔ Civitas's runtime-loop representation conversion** — flagged
  above as unreconciled, not assumed away.

## Real addition, built after `contracts/civitas-bridge.md`'s own open item: `PersistedMemoryStore`

`civitas-bridge.md`'s "Correction found during implementation" section
flagged a real gap: `CivitasBridge.request_state_persistence` returns a
`ComponentStateHandle`, but no `MemoryStore` implementation existed to
receive it. `PersistedMemoryStore` (`fabrica/memory/store.py`) closes this.

**Shape, decided through implementation**: a `BlobStore` Protocol (`get()`/
`set()` over a whole `dict[str, Any]`) is defined LOCALLY in
`fabrica.memory.store` -- not an import of
`fabrica.civitas_bridge.state.ComponentStateHandle` -- for two reasons, one
architectural and one structural. Architecturally: `fabrica.memory`
depending on `fabrica.civitas_bridge` would invert the dependency direction
`architecture.md §1a` establishes (only `CivitasBridge` integrates tightly;
every other component stays independently usable). Structurally: it would
also be a genuine circular import, since `fabrica.civitas_bridge` already
imports `fabrica.memory` for `MemoryManager`. `ComponentStateHandle`
already satisfies `BlobStore`'s shape today -- `CivitasBridge.build()`
passes one in without `fabrica.memory` ever needing to know that type
exists, the same "depend on shapes, not packages" pattern used for
`PresidiumClient`/`Summarizer`/`CivitasRuntime`.

**Write-through, not lazy, and load-once, not per-read**: `BlobStore` has
no partial-update operation -- every `write()`/`forget()` persists the
ENTIRE current snapshot immediately (`_memory_snapshot`/
`_restore_memory_snapshot`), and the full state is loaded exactly once, at
construction (`PersistedMemoryStore.create()`, an async factory --
`__init__` itself stays synchronous, same pattern as
`MCPToolNamespace.create()`). This is correct, not just simple: a
`ComponentStateHandle` has exactly one owning writer per `component_name`
by construction (`CivitasBridge` binds one per manager), never a fanned-out
reader that could miss another process's concurrent update.

**Reuses `InMemoryMemoryStore` internally via composition**, not
duplicated matching/scoring logic -- `PersistedMemoryStore` delegates
`write()`/`search()`/`get()`/`forget()` to a real `InMemoryMemoryStore`
instance, intercepting only the mutating calls to persist afterward. The
two implementations' actual behavior can never silently drift apart.

Tested against both a minimal `BlobStore` test double (isolating this
class's own logic) AND a real `civitas.plugins.state.InMemoryStateStore` +
`ComponentStateHandle` (proving the duck-typing genuinely works against
the real class, not just an idealized one) -- including a real
"restart" scenario: a second, independent `PersistedMemoryStore.create()`
over the same underlying store sees everything the first one wrote.

## Real addition: `tracer` DI on `MemoryManager`

`MemoryManager` now accepts an optional `tracer: fabrica.observability
.Tracer | None = None` (defaults to `NullTracer()`). `write()`/`search()`
each emit a real `fabrica.memory.write`/`fabrica.memory.search` span
carrying `Scope` fields (`user_id`/`session_id`/`agent_id`/`team_id`) and
the backend class name as attributes -- standalone spans, not nested
under anything else, since no caller flow established a parent context
for them in this pass. Full design:
[system-design.md §7](../system-design.md#7-observability-spans-this-system-emits).

## Open items for implementation

1. ~~The single-message-exceeds-budget edge case in `RecencyCompactor`...~~
   **Resolved above, in `RecencyCompactor`'s own section** -- found
   already implemented and tested
   (`test_single_message_exceeding_budget_preserves_nothing_verbatim`),
   just previously left marked "unresolved" here even though the code's
   own docstring already stated the mechanical answer plainly. Pure
   documentation fix -- no code change needed.
2. ~~`WorkingMemoryQuotaExceeded`'s default ceiling...~~ **Resolved: 256KB,
   shipped as `DEFAULT_QUOTA_BYTES`** (`working_memory.py`), with its own
   docstring stating plainly it's a placeholder, not validated against
   any real working-memory usage pattern -- no spike exists for what a
   realistic session's scratchpad actually accumulates. `quota_bytes` is
   a real, overridable constructor parameter on `InMemoryWorkingMemoryStore`,
   not a hardcoded constant -- a deployment with real data can override
   it today without waiting for that spike. Revisit the DEFAULT (not the
   mechanism, which is already real and tested) if real usage data ever
   shows 256KB too small or too generous.
3. ~~Per `memory.md` open question 9: the whole preserve-verbatim-plus-summarize
   strategy has zero empirical backing~~ **Resolved for the strategy, not the
   number.** [SPIKE-recency-compactor-validation.md](../../specs/archive/spikes/SPIKE-recency-compactor-validation.md)
   validated the summarize-vs-truncate mechanism directly (5/5 grounded-correct
   vs. 0/5), the same rigor `SPIKE-code-mode-execution.md` applied elsewhere.
   ~~`preserve_last_n=6` itself is still an unvalidated guess~~ **Narrowed,
   not fully closed.**
   [SPIKE-recency-compactor-n-value.md](../../specs/archive/spikes/SPIKE-recency-compactor-n-value.md)
   varied `preserve_last_n` (2, 6, 10) against a REAL, deliberately tight
   `budget_tokens` boundary (unlike the first spike's "plenty of room"
   scenario) — calling the actual `RecencyCompactor`/`_select_preserved`
   code, not a reimplementation. The real budget-clipping mechanism
   engaged exactly as coded (`preserve_last_n=10`'s request was correctly
   honored down to 6, the most the budget allowed, in every run), and all
   three N values scored 5/5 grounded-correct — `preserve_last_n`'s exact
   value made no observable difference in this scenario. Still open,
   precisely scoped now rather than broadly: multiple competing facts
   needing simultaneous preservation, and precision-sensitive facts a
   summary might paraphrase or round, remain untested — this pair of
   spikes has only validated the single-clear-constraint case.
