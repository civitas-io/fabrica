# Contract: `Retriever`

**Status:** Contract — implementation-ready · **Last updated:** 2026-08
**Supersedes:** the `Retriever` sketch in [retrieval.md](../retrieval.md)
**Depends on:** [system-design.md](../system-design.md) (`Retriever` is one of the
two foundational shared engines, per §1)

This is a contract, not a design doc — full signatures, exact error semantics,
precise async behavior. Where writing this out in full exposed a real gap the
design-level sketch glossed over, the gap is named and resolved here, not carried
forward silently.

---

## Two levels, not one: `RetrieverBackend` and `Retriever`

The design sketch in `retrieval.md` described one `Retriever` Protocol with
swappable backends, but didn't say **where fallback logic lives**. Writing the
actual methods out made this concrete: if fallback (§6 of `system-design.md`)
were implemented inside every backend, it'd be duplicated three times
(`KeywordBackend`, `PrxBackend`, `LlamaIndexBackend`) instead of written once.

So: `RetrieverBackend` is the narrow, swappable strategy. `Retriever` is the one
public class every discovery surface actually depends on — it wraps a primary
backend with `KeywordBackend` as an always-present fallback, and owns fallback
logic, duplicate detection, and the eager/deferred split in exactly one place.
**`ToolManager` and `SkillManager` never see a `RetrieverBackend` directly.**

```python
class RetrieverBackend(Protocol):
    """A single search strategy. Implementations: KeywordBackend (default,
    Rust+PyO3), PrxBackend, LlamaIndexBackend, LangChainBackend. Never used
    directly outside Fabrica — always wrapped by Retriever."""

    async def add(self, items: list[Indexable]) -> None: ...
    async def remove(self, ids: list[str]) -> None: ...
    async def query(
        self, query: str, kind: Literal["tool", "skill"] | None, limit: int
    ) -> list[RankedMatch]: ...
    async def health_check(self) -> bool: ...
```

```python
class Retriever:
    """The public engine. Every discovery surface (find(), skill discovery)
    depends on this class, not on any specific backend."""

    def __init__(
        self,
        primary: RetrieverBackend,
        fallback: RetrieverBackend | None = None,
    ) -> None:
        """`fallback` defaults to a fresh `KeywordBackend` if not supplied.
        There is no "no fallback" mode — every Retriever has one, matching
        the resilience pattern in system-design.md §6."""
        ...
```

---

## Types

```python
@dataclass(frozen=True)
class Indexable:
    """One thing the Retriever can discover: a tool or a skill.
    Never memory — memory has its own MemoryStore.search(), see memory.md;
    forcing it through this type would paper over a real semantic difference
    (memory is scoped, this is a shared registry)."""

    id: str
    kind: Literal["tool", "skill"]
    name: str
    description: str          # the only field actually embedded/matched
    eager: bool = False        # inverted `defer_loading` — see list_eager()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Indexable.id must be non-empty")


@dataclass(frozen=True)
class RankedMatch:
    """A single search result.

    Deliberately has NO score field. This isn't an omission — rank-not-
    threshold (system-design.md §6, from SPIKE-tool-retrieval-token-overhead.md
    and SPIKE-code-mode-execution.md) is enforced by the type itself, not
    just a docstring warning. Correct hits and near-misses were observed
    landing in the same 0.01–0.04 score band on real data; exposing a score
    field here would invite exactly the misuse those spikes warned against."""

    item: Indexable
    rank: int   # 0 = best match. Lists returned by search() are always
                # pre-sorted ascending by rank — callers must not re-sort.
```

---

## Errors

```python
class RetrieverError(Exception):
    """Base for all Retriever errors."""


class DuplicateIndexableError(RetrieverError):
    """Raised by register() when an id already exists with DIFFERENT
    kind/name/description than the incoming Indexable. Re-registering an
    id with IDENTICAL fields is idempotent and does not raise this — a
    service restart re-registering its own tool set must not error."""

    def __init__(self, id: str, existing: Indexable, incoming: Indexable) -> None:
        ...


class RetrieverUnavailableError(RetrieverError):
    """Raised only when EVERY backend — including the fallback — has
    failed. This should be rare: most individual backend failures are
    absorbed by automatic fallback (system-design.md §6) and never reach
    a caller. If this is raised, both the primary and the zero-dependency
    KeywordBackend fallback are down, which is a serious operational
    signal, not a routine error to catch-and-continue on."""
```

---

## Methods

```python
async def register(self, items: list[Indexable]) -> None:
    """Add or update items in the index — writes to BOTH the primary and
    fallback backend, so fallback search results stay complete even
    during a primary outage. Duplicate-checking happens here, once, not
    per-backend.

    Idempotent for identical re-registration. Raises DuplicateIndexableError
    if an id already exists with different content — callers must
    deregister first to intentionally change an item's identity-bearing
    fields.

    Raises:
        DuplicateIndexableError: id collision with different content.
        RetrieverUnavailableError: both primary and fallback failed to
            persist the registration.
    """

async def deregister(self, ids: list[str]) -> None:
    """Remove items by id. Deregistering an id that doesn't exist is a
    no-op, not an error — callers should not need to track what's
    already registered to safely call this."""

async def search(
    self,
    query: str,
    *,
    kind: Literal["tool", "skill"] | None = None,
    limit: int = 5,
    timeout: float = 2.0,
) -> list[RankedMatch]:
    """Search the index. Returns at most `limit` matches, pre-sorted
    ascending by rank (rank=0 best).

    `kind=None` searches across both tools and skills in one call —
    resolves the open question in retrieval.md §7 ("does find's kind
    parameter force a choice") in favor of NOT forcing one; `kind="tool"`
    or `"skill"` filters to just that kind when the caller knows which
    it wants.

    Returns an empty list, never raises, if nothing matches — an empty
    index or a non-matching query are both valid outcomes, not errors.

    On primary-backend failure or timeout, falls back to the configured
    fallback backend automatically and logs a degraded-mode event
    (system-design.md §6) — this is transparent to the caller, not
    something every call site needs to catch.

    Raises:
        RetrieverUnavailableError: both primary and fallback failed or
            timed out.
    """

async def list_eager(
    self, kind: Literal["tool", "skill"] | None = None
) -> list[Indexable]:
    """Return all Indexables registered with eager=True — the items that
    belong directly in a model's context rather than behind search()
    (Anthropic's `defer_loading` concept, inverted; see retrieval.md).

    Served from an in-memory cache maintained by Retriever itself, not
    delegated to either backend — eager items bypass search by design,
    not as a performance shortcut.

    No `limit` parameter: an eager set is expected to be small and
    curated. A large eager set defeats the purpose of having a Retriever
    at all — that's a caller misconfiguration this method does not
    silently truncate around.
    """
```

---

## What this contract deliberately does not cover

- **Grant-based filtering is not `Retriever`'s job.** Per `retrieval.md`'s
  integration section, Presidium grants determine what's indexable in the
  first place; `ToolManager`/`SkillManager` filter `RankedMatch` results by
  the calling agent's grants as a wrapping concern. `Retriever` has no
  concept of "which agent is asking" — it is a general-purpose index, not a
  per-agent view.
- **Which concrete `RetrieverBackend` to use is a `SandboxPool`/`CivitasBridge`
  wiring decision** (`system-design.md §1`, §2), not something this contract
  specifies.
- **The exact `KeywordBackend` (Rust+PyO3) implementation** is out of scope
  for this contract — this document specifies what any `RetrieverBackend`
  must do, not how the default one does it.

## Open items for implementation

1. `list_eager`'s in-memory cache needs an invalidation strategy when an
   eager item is deregistered mid-flight — not specified here, an
   implementation detail below this contract's level.
2. Whether `register`/`deregister` should batch-fail-atomically (all-or-nothing)
   or best-effort (partial success, per-item errors returned) when the input
   list is large — not decided; the signatures above assume atomic for
   simplicity, worth revisiting if real registries are large enough for
   partial failure to matter.
