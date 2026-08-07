# Prompts

**Status:** Design · **Last updated:** 2026-08

---

## Thesis: the narrowest of the four managers, on purpose

`context-layer.md` named this component early — *"versioned, addressable
prompt management"* — but it's had zero design attention since, and zero
spike coverage (`HANDOFF.md`'s open-items list). Writing it out reveals it
should stay genuinely small: **storage, versioning, and retrieval of prompt
template *content* — nothing else.** Two things a first pass might reach for
are deliberately excluded, and the reasoning for both is the same principle
named in [architecture.md §1a](architecture.md#1a-a-platform-wide-principle-named-explicitly-library-first-low-coupling-high-cohesion):

**1. `PromptManager` does not render templates.** Filling `{variables}` into
a stored template is a templating-engine concern (Jinja2, f-strings, a
harness's own DSL) — forcing every consumer to accept Fabrica's choice of
renderer would couple `PromptManager` to a decision that belongs to the
harness, not the context layer. `PromptManager` hands back raw template
content; rendering happens after it leaves this component, not inside it.
This is a stronger and simpler answer than memory's "wrap, don't build" (§
below) — there's no crowded market to pick a winner from here, there's just
no reason for this component to own the decision at all.

**2. `PromptManager` does not compress/shrink prompts.** `context-layer.md`
named "promptshrink compression" as one of the scattered ideas being
absorbed into this platform. Having now designed `Compactor`
([memory.md](memory.md)), it's clear that "reduce a block of text's token
footprint under a budget, via an injected `Summarizer`" is already a general
shape this platform has — building a *second*, prompt-specific compression
mechanism inside `PromptManager` would duplicate `Compactor` rather than
generalize it. If prompt compression becomes a real, validated need, it
should extend `Compactor`'s shape (or `Compactor` itself, generalized to
`list[Message] | str`), not become a third mechanism. Not built now, not
forgotten — named here explicitly so the next person doesn't reinvent it.

## Interface (sketch)

```python
@dataclass(frozen=True)
class PromptTemplate:
    name: str              # the address — e.g. "customer_support/greeting"
    version: int           # monotonically increasing per name, assigned by put()
    content: str           # raw template content; PromptManager never
                            # interprets this string's syntax
    metadata: dict[str, Any]
    created_at: datetime


class PromptStore(Protocol):
    """Swappable backend. Default: local files/SQLite (library mode) — see
    system-design.md's existing state-ownership table, which already names
    this without a design doc behind it until now."""
    async def put(self, name: str, content: str, *, metadata: dict | None = None) -> PromptTemplate: ...
    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None: ...
    async def list_versions(self, name: str) -> list[int]: ...
    async def delete(self, name: str, version: int | None = None) -> None: ...


class PromptManager:
    """Adds exactly one thing over the raw backend: a read cache. Prompts
    are read far more often than written, unlike tool/skill retrieval where
    every query is meaningfully different — worth caching, unlike Retriever's
    search results."""
    def __init__(self, store: PromptStore) -> None: ...
    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None: ...
    async def put(self, name: str, content: str, *, metadata: dict | None = None) -> PromptTemplate: ...
    async def list_versions(self, name: str) -> list[int]: ...
    async def delete(self, name: str, version: int | None = None) -> None: ...
```

**Versions are immutable and append-only** — `put()` always creates a new
version, never overwrites one in place. That's the entire point of
versioning: a harness that pinned `version=5` for reproducibility must never
see its content change under it.

## Deployment modes

- **Library** — local files or SQLite, in-process. The expected default; prompt
  content is small and read-heavy, not a scaling concern the way sandboxes or
  retrieval are.
- **Service** — Civitas `StateStore`-backed Postgres, shared across a fleet —
  same two-mode shape as every other manager, not a new pattern.

## Integration

- **Civitas** persists the default store via its `StateStore`, same as memory's
  default store.
- **Presidium** has no obvious grant surface here — prompt template content
  isn't user data requiring scope-gated access the way memory recall is. Flagged
  as an open question below, not assumed.
- **Fabrica** owns storage/versioning/retrieval only, per the thesis above.

## Open questions

1. **No spike exists for this component at all** — unlike every other manager,
   there is zero empirical validation behind any part of this design. Worth
   naming honestly rather than letting it blend in with the validated pieces.
2. Auto-incrementing integer versions (chosen above) vs. content-hash identity
   (Git-like) vs. caller-supplied string tags — not stress-tested against a
   real consumer need. Integers are simplest; not proven sufficient.
3. **Named aliases/tags** (e.g., a "prod" tag pointing at whichever version is
   currently live, separate from "latest") were considered and deliberately
   left out of v1 — real, plausible need, but unvalidated by any concrete use
   case yet. Same "ship the default, revisit if a gap forces it" call as
   Windows support and macOS Tier 2.
4. Does Presidium need any grant surface here at all (e.g., gating who can
   `put()` a new version of a prompt used broadly)? Not designed — genuinely
   unclear whether this is a real governance need or not.
5. If prompt compression becomes real, does it belong as a generalization of
   `Compactor` (accepting `str` alongside `list[Message]`), or as its own
   sibling mechanism reusing `Summarizer`'s DI shape independently? Not decided
   — flagged in the thesis above, not resolved here.
