# Contract: `PromptStore`, `PromptManager`

**Status:** Contract — implementation-ready · **Last updated:** 2026-08
**Depends on:** [prompts.md](../prompts.md) (the design this formalizes),
[architecture.md §1a](../architecture.md#1a-a-platform-wide-principle-named-explicitly-library-first-low-coupling-high-cohesion)
(the reason rendering and compression are excluded, not just deferred)

The smallest of the five contracts, deliberately — `prompts.md`'s thesis is
that this component should stay narrow, and that narrowness carries through
into a short contract, not an artificially padded one. Two ideas promoted
here from `prompts.md`'s "Explored" survey: the cache-boundary marker and the
`PROMPT.md` portable format.

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
    cacheable: bool = False
    cache_boundary: int | None = None
```

**`cacheable`/`cache_boundary` solve a real tension, not a trivial addition.**
`PromptManager` is contractually forbidden from parsing `content`'s
templating syntax — so it cannot *detect* where a template's static portion
ends and a `{variable}`-style substitution point begins. Provider-side prompt
caching (`prompts.md`'s "Explored" section) needs exactly that boundary to be
known, since one substituted token anywhere in the cached prefix breaks the
match. The resolution: the **author declares it explicitly** at `put()` time
— `PromptManager` stays syntax-agnostic (it never validates or computes this
value, only stores and returns it), while still surfacing what a harness
needs to build a provider-specific `cache_control` call.

- `cacheable` defaults to `False`, deliberately, not `True`. Declaring a
  template cacheable is a real claim the author must make on purpose — an
  author who marks non-stable content cacheable by accident actively *harms*
  cache hit rates by leading a harness to treat dynamic content as a stable
  prefix.
- `cache_boundary` is only meaningful when `cacheable=True`. `None` means the
  entire `content` is stable end-to-end — the common case, since
  variable-bearing content is typically appended by the harness *after* this
  template rather than interpolated into its middle. A non-`None` value is a
  character offset into `content` marking the end of the guaranteed-stable
  portion.
- Neither field is validated against `content` in any way — `PromptManager`
  has no way to verify an author's claim is even correct, and doesn't try to.

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


class PromptParseError(PromptError):
    """Raised by PromptManager.load() on a malformed PROMPT.md — missing
    the required `name` frontmatter field, or malformed YAML. Mirrors
    SkillParseError's role for SKILL.md (contracts/managers.md) — same
    shape, different file format."""
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
        cacheable: bool = False, cache_boundary: int | None = None,
    ) -> PromptTemplate:
        """Always creates a NEW version — never overwrites an existing one.
        version is assigned as (current max version for name) + 1, starting
        at 1 for a new name. Raises PromptBackendError on backend failure.

        content is stored and returned verbatim. This store never parses,
        validates, or interprets its syntax (no Jinja2/f-string awareness at
        all) — per prompts.md's thesis, that's deliberately out of scope.
        cacheable/cache_boundary are likewise stored and returned verbatim,
        never validated against content — see the Types section above."""

    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None:
        """version=None returns the highest version for name. None (not an
        error) if name doesn't exist, or if version is given but doesn't
        exist for that name."""

    async def list_versions(self, name: str) -> list[int]:
        """Ascending order. Empty list (not an error) if name doesn't exist."""

    async def list_names(self) -> list[str]:
        """Every registered name, regardless of version count. Added after
        writing contracts/mcp-server.md's fabrica_prompts_list handler --
        MCP's native prompts/list primitive needs to enumerate ALL prompts,
        and list_versions(name) presupposes a name already known. A real
        gap found by wiring this contract to an actual caller, not
        anticipated in advance."""

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

    async def put(
        self, name: str, content: str, *, metadata: dict[str, Any] | None = None,
        cacheable: bool = False, cache_boundary: int | None = None,
    ) -> PromptTemplate:
        """Delegates to store.put(), then invalidates this name's
        version=None cache entry (since "latest" just changed) — the new
        version's own cache entry is populated directly from this call's
        result, not re-fetched."""

    async def list_versions(self, name: str) -> list[int]: ...
    async def list_names(self) -> list[str]:
        """Not cached -- unlike get()'s read-heavy justification, enumerating
        all names is not assumed to be a hot path; delegates straight
        through to store.list_names() every call."""

    async def delete(self, name: str, version: int | None = None) -> None:
        """Invalidates every cached entry for name, not just the deleted
        version — a deleted specific version can change what version=None
        now resolves to."""

    async def load(self, path: Path) -> PromptTemplate:
        """Reads a PROMPT.md-shaped file (YAML frontmatter + a raw content
        body, mirroring SKILL.md's shape — skills-gateway.md) and calls
        put() with the result. This is how prompts enter version control:
        authored and reviewed via normal PRs and `git diff`
        (Humanloop's precedent, prompts.md's "Explored" section), not typed
        directly into a database.

        Frontmatter contract:
        - `name` (required) -> PromptTemplate.name. Missing this field
          raises PromptParseError.
        - `cacheable` (optional, default False) -> the typed field.
        - `cache_boundary` (optional, default None) -> the typed field.
        - Every OTHER frontmatter key is passed through into `metadata`
          verbatim — load() does not define a fixed metadata schema.
        - The body (everything after the closing frontmatter delimiter)
          becomes `content` verbatim — load() applies no templating
          awareness here either, consistent with put().

        Idempotent for unchanged content: if the file's content and
        cacheable/cache_boundary/metadata are identical to the current
        latest version for this name, load() returns that existing version
        rather than creating a new one — avoids version-number churn from
        repeated CI loads of an unchanged file. Mirrors
        ToolManager.register()'s idempotency (contracts/managers.md).

        Raises:
            PromptParseError: malformed frontmatter, or a missing `name`.
            PromptBackendError: propagated from the underlying put().
        """
```

**No `render()` method exists on this contract, deliberately** — per
`prompts.md`'s thesis, templating is a harness-level decision, not something
this component performs on a consumer's behalf.

---

## What this contract deliberately does not cover

- **Rendering** (variable substitution into `content`) — explicitly out of
  scope; see `prompts.md`'s thesis. A harness receives raw `content` and
  renders it however it chooses.
- **Compression/shrinking of prompt content** — explicitly out of scope.
  Corrected from an earlier version of `prompts.md`: this should NOT extend
  `Compactor` (`contracts/memory.md`) if it's ever built — extractive
  compression (LLMLingua-2) is mechanically distinct from `Compactor`'s
  abstractive approach, closer in shape to a `RetrieverBackend`. See
  `prompts.md`'s "Explored" section.
- **Any provider-specific caching API translation** — `cacheable`/
  `cache_boundary` are passed through verbatim; turning them into an
  Anthropic `cache_control` block or an OpenAI/Gemini call shape is the
  harness's job, not this contract's.
- **`{variable}`-substitution detection** — `cache_boundary` is
  author-declared, never computed by parsing `content`; see the Types
  section for why.
- **Named aliases/tags** (e.g. a `"prod"` pointer distinct from the highest
  integer version) — considered, deliberately deferred; see `prompts.md`
  open question 3.
- **Any Presidium grant surface** — no `Scope` exists on these types at all;
  whether prompt template access needs governance is unresolved
  (`prompts.md` open question 4), not silently assumed to be "no."

## Real addition: `tracer` DI on `PromptManager`, closing a gap bigger than a missing attribute

Until now, `PromptManager` emitted NOTHING -- not a missing attribute on
an existing span, the whole component had no tracer integration at all,
unlike every other manager (`system-design.md §7`'s own span table never
listed it). Found and closed while extending the context-footprint
metering dimension (`civitas-presidium-integration.md`) from
`MemoryManager` to the rest of the system that returns content for a
caller to put into a model's context.

`PromptManager` now accepts an optional `tracer: fabrica.observability
.Tracer | None = None` (defaults to `NullTracer()`, the same DI shape as
everywhere else). `get()` emits `fabrica.prompt.get` with `prompt_name`,
`version`, a real `cache_hit` boolean (mirroring `SandboxPool.acquire()`'s
`warm_hit`), and `volume_bytes` (real content byte length -- the same
dimension `MemoryManager.write()`/`search()` already emits, `0` when
the name doesn't resolve to anything). `put()` emits `fabrica.prompt.put`
with `prompt_name`, the assigned `version`, and `volume_bytes` of the
content being written.

**A real naming collision found immediately, not assumed safe**:
`fabrica.observability.traced()`'s own second positional parameter is
itself called `name` (the SPAN's name) -- passing the prompt's `name` as
a keyword attribute (`name=name`) collides with it. Both spans use
`prompt_name` instead, caught by a failing test on the very first attempt,
not found by inspection alone.

**A real, correct existing behavior surfaced while writing the test for
it, not a bug**: calling `get(name)` (no `version`) immediately after
`put(name, ...)` is a genuine cache MISS, not a hit -- `put()` only
populates the cache entry for the SPECIFIC version it just wrote
(`(name, version)`), and explicitly POPS `(name, None)`'s "latest" alias
since latest just changed, rather than speculatively repopulating it.
The first test written here assumed the opposite and failed, which is
how this was caught.

`CivitasBridge.build()` was found NOT wiring `tracer` through to
`PromptManager` at all (it already did for `MemoryManager`) -- fixed
alongside this addition, not a separate follow-up.

## Real addition, mirroring `memory.md`'s `PersistedMemoryStore`: `PersistedPromptStore`

Same gap, same resolution shape: `PersistedPromptStore`
(`fabrica/prompts/store.py`) is a `PromptStore` backed by a locally-defined
`BlobStore` Protocol (not an import of
`fabrica.civitas_bridge.state.ComponentStateHandle`, for the identical
architectural + circular-import reasons documented in `memory.md`'s
matching section) -- write-through on every `put()`/`delete()`, loaded
once at construction via an async `create()` factory, delegating to a real
`InMemoryPromptStore` internally so open item 2's atomic-version-assignment
guarantee is never duplicated or allowed to drift.

Version numbers become string keys in the JSON snapshot (JSON object keys
are always strings) -- converted back to `int` on restore, never left as
strings for a caller to trip over. Tested against both a minimal
`BlobStore` double and a real `civitas.plugins.state.InMemoryStateStore` +
`ComponentStateHandle`, including a real "restart" scenario across
multiple prompt versions.

## Open items for implementation

1. ~~`PromptManager`'s cache has no eviction policy specified...~~
   **Resolved: unbounded in-process, no size ceiling or TTL.** Prompt
   catalogs are expected to be small and curated (unlike tool/skill
   catalogs or memory, where volume is the whole point) -- unbounded
   caching of `(name, version)` entries is a reasonable default for that
   shape of workload, matching this project's own "ship the default,
   revisit if forced" pattern used elsewhere (Windows Tier 1, macOS
   Tier 2, `KeywordBackend`'s pure-Python v1). Not validated against any
   real large-catalog deployment -- named plainly as an assumption, not
   hidden as a silent default. Revisit with an LRU/TTL bound only if a
   real deployment's prompt catalog turns out large enough for unbounded
   growth to matter; `put()`/`delete()`'s existing invalidation logic
   would be unaffected by adding a bound later.
2. Concurrent `put()` calls to the same `name` — is `version = max + 1`
   computed atomically by the backend, or could two concurrent writers land
   on the same version number? Depends on the specific `PromptStore`
   implementation's transaction guarantees; not specified generically here.
3. ~~Whether `PromptTemplate.content` has any size ceiling at all...~~
   **Resolved: yes, 256KB** (`MAX_PROMPT_CONTENT_BYTES`), enforced in
   `PromptTemplate.__post_init__` -- rejects with `PromptTooLargeError`,
   deliberately NOT truncating the way `RunResult.stdout` does: unlike
   execution output, a prompt's content is authored, instructional
   input, and silently truncating it would corrupt its meaning, not just
   its length.
4. ~~`cache_boundary` has no validation against `content`'s actual
   length...~~ **Resolved: reject, not pass through.** Also enforced in
   `PromptTemplate.__post_init__`, raising the new
   `InvalidCacheBoundaryError` for a negative offset or one past the end
   of `content` -- catching an author's mistake at write time beats a
   confusing runtime bug later in whatever downstream code slices
   `content` at that boundary. Validating at the type's own construction
   (not in `PromptManager`, which still never validates anything else,
   or in any one `PromptStore` implementation) means every construction
   path gets the guarantee for free, with nothing to duplicate or forget
   in a future backend. A real bug found and fixed while wiring this up:
   `InMemoryPromptStore.put()`'s broad `except Exception` was swallowing
   both new errors into a generic `PromptBackendError`, indistinguishable
   from an actual storage failure -- fixed to let them propagate
   unwrapped.
5. `load()`'s PROMPT.md frontmatter schema is specified only loosely here
   (`name` required, `cacheable`/`cache_boundary` optional, everything else
   -> `metadata`) — no formal schema validation (YAML types, unexpected
   keys) is defined, and unlike SKILL.md, this format has not been checked
   against any real corpus (`skills-gateway.md`'s real-spec conformance
   check has no equivalent here yet).
