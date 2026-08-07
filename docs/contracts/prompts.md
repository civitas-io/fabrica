# Contract: `PromptStore`, `PromptManager`

**Status:** Contract — implementation-ready · **Last updated:** 2026-08
**Depends on:** [prompts.md](../prompts.md) (the design this formalizes),
[architecture.md §1a](../architecture.md#1a-a-platform-wide-principle-named-explicitly-library-first-low-coupling-high-cohesion)
(the reason rendering and compression are excluded, not just deferred)

The smallest of the five contracts, deliberately — `prompts.md`'s thesis is
that this component should stay narrow, and that narrowness carries through
into a short contract, not an artificially padded one.

---

## Types

```python
@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: int
    content: str
    metadata: dict[str, Any]
    created_at: datetime
```

No `Scope` here — unlike every other manager, prompt template content isn't
scoped to a user/session/agent/team. It's addressed by `name` alone, closer to
a shared code artifact than to user data. (This is also why `prompts.md`
leaves Presidium's grant surface as an open question — there's no natural
`Scope` to gate access by.)

---

## Errors

```python
class PromptError(Exception):
    """Base for all PromptStore/PromptManager errors."""


class PromptBackendError(PromptError):
    """The configured backend failed (unreachable DB, disk I/O error, ...).
    Same wrapping guarantee as MemoryBackendError (contracts/memory.md):
    callers depend on one error type regardless of backend."""
    def __init__(self, cause: Exception) -> None: ...
```

**`get()` on an unknown `name`, or an unknown `version` of a known `name`,
returns `None` — not an error.** **`delete()` on an unknown `name`/`version`
is a no-op.** Both consistent with the no-op/`None`-on-unknown precedent used
everywhere else in this platform (`Retriever.deregister`, `MemoryStore.get`/
`forget`).

---

## `PromptStore`

```python
class PromptStore(Protocol):
    async def put(
        self, name: str, content: str, *, metadata: dict[str, Any] | None = None,
    ) -> PromptTemplate:
        """Always creates a NEW version — never overwrites an existing one.
        version is assigned as (current max version for name) + 1, starting
        at 1 for a new name. Raises PromptBackendError on backend failure.

        content is stored and returned verbatim. This store never parses,
        validates, or interprets its syntax (no Jinja2/f-string awareness at
        all) — per prompts.md's thesis, that's deliberately out of scope."""

    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None:
        """version=None returns the highest version for name. None (not an
        error) if name doesn't exist, or if version is given but doesn't
        exist for that name."""

    async def list_versions(self, name: str) -> list[int]:
        """Ascending order. Empty list (not an error) if name doesn't exist."""

    async def delete(self, name: str, version: int | None = None) -> None:
        """version=None deletes every version of name. An int deletes just
        that version. No-op if the target doesn't exist."""
```

Default implementation: local files or SQLite, in-process — per
`system-design.md`'s existing (previously undocumented) state-ownership table.

---

## `PromptManager`

```python
class PromptManager:
    """Adds exactly one thing over the raw backend: a read cache keyed by
    (name, version). Justified because prompts are read far more often than
    written (every agent turn re-fetches its system prompt; puts happen only
    when an author changes one) — unlike Retriever, where every search query
    is meaningfully different and caching search RESULTS wouldn't help."""

    def __init__(self, store: PromptStore) -> None: ...

    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None:
        """Cache hit avoids a backend round-trip entirely. A cache miss
        populates the cache before returning. version=None (latest) is
        cached too, but see the invalidation note below."""

    async def put(self, name: str, content: str, *, metadata: dict[str, Any] | None = None) -> PromptTemplate:
        """Delegates to store.put(), then invalidates this name's
        version=None cache entry (since "latest" just changed) — the new
        version's own cache entry is populated directly from this call's
        result, not re-fetched."""

    async def list_versions(self, name: str) -> list[int]: ...
    async def delete(self, name: str, version: int | None = None) -> None:
        """Invalidates every cached entry for name, not just the deleted
        version — a deleted specific version can change what version=None
        now resolves to."""
```

**No `render()` method exists on this contract, deliberately** — per
`prompts.md`'s thesis, templating is a harness-level decision, not something
this component performs on a consumer's behalf.

---

## What this contract deliberately does not cover

- **Rendering** (variable substitution into `content`) — explicitly out of
  scope; see `prompts.md`'s thesis. A harness receives raw `content` and
  renders it however it chooses.
- **Compression/shrinking of prompt content** — explicitly out of scope; if
  this becomes a real need, it should extend `Compactor`
  (`contracts/memory.md`), not become a mechanism unique to this contract.
- **Named aliases/tags** (e.g. a `"prod"` pointer distinct from the highest
  integer version) — considered, deliberately deferred; see `prompts.md`
  open question 3.
- **Any Presidium grant surface** — no `Scope` exists on these types at all;
  whether prompt template access needs governance is unresolved
  (`prompts.md` open question 4), not silently assumed to be "no."

## Open items for implementation

1. `PromptManager`'s cache has no eviction policy specified (size ceiling,
   TTL, or unbounded-in-process) — needs a decision before implementation,
   not assumed to be safe by default.
2. Concurrent `put()` calls to the same `name` — is `version = max + 1`
   computed atomically by the backend, or could two concurrent writers land
   on the same version number? Depends on the specific `PromptStore`
   implementation's transaction guarantees; not specified generically here.
3. Whether `PromptTemplate.content` has any size ceiling at all (unlike
   `RunResult.stdout`'s explicit 64KB cap) — unspecified, and prompt content
   is normally small, but a caller could in principle `put()` something huge.
