# Memory

**Status:** Design · **Last updated:** 2026-08

---

## Reframe: memory is a harness-engineering primitive, not just personal recall

The original version of this document covered exactly one thing: cross-session
factual/preference recall, Mem0-style (*"the user prefers dark mode and lives in
Bangalore"*). That's real, but it's only one of **three genuinely different
time horizons** a harness builder (Priya) needs help with, and the other two are
arguably more universal, since almost every agent hits them regardless of
whether it does any personalization at all:

| Facet | Time horizon | The problem it solves |
|---|---|---|
| **Working memory** | one task/session, ephemeral | Where does an agent stash task state — decisions made, approaches already tried, progress — without either re-deriving it every turn or bloating the raw message history just to remember it? |
| **Compaction** | one context window, bounded by a token budget | When conversation history approaches the model's context limit, how does a harness keep running without either truncating blindly or hand-rolling its own summarization? |
| **Long-term memory** | across sessions, persistent | Cross-session facts/preferences — the original scope of this document, unchanged in substance below. |

**Compaction earns its place here for a very concrete reason, not a theoretical
one:** producing this repo's own [`HANDOFF.md`](../HANDOFF.md) was a manual,
human-triggered instance of exactly this mechanism — a person noticed context
pressure and asked for a checkpoint by hand. A generic `MemoryManager` should
offer that as a callable primitive a harness invokes automatically, not
something that only happens when a human remembers to ask for it.

**The Civitas boundary stays exactly where it already is, not muddied by
adding this:** Civitas owns "keeps agents alive" — the runtime loop, session
lifecycle, token-budget tracking — so Civitas decides *when* to compact
(crossing a threshold it already tracks). Fabrica decides *how* — the actual
compaction policy and mechanism. Same split as tool execution: Civitas decides
*when* to call a tool, Fabrica decides *how* retrieval/execution works. Adding
compaction to Fabrica's scope reinforces this boundary rather than creating a
new cross-cutting one.

## Related work and a deliberate divergence

The idea of unifying content-ingestion, indexing/retrieval, and
compaction/forgetting into one governed pipeline is not new here — it's the
subject of real, named prior art, worth citing accurately rather than
reinventing implicitly:

- **Generative Agents** (Park et al., Stanford, 2023) — the cleanest unifying
  precedent. One memory stream of raw, timestamped content; one **composite
  retrieval score** combining recency, importance, and relevance; periodic
  **reflection**, where the agent synthesizes recent raw memories into
  higher-level abstractions that get written back into the same stream and
  become retrievable themselves. Content becomes knowledge by feeding back
  into the same indexed structure it came from — not a separate stage.
- **MemGPT / Letta** — the OS-memory-hierarchy framing this platform's
  `WorkingMemoryStore` / `MemoryStore` split structurally echoes: core memory
  (≈ RAM, in-context) vs. archival memory (≈ cold storage), with the *model
  itself* deciding what moves between tiers via explicit tool calls.
- **MemOS / Continuum Memory Architecture** (2024–2026) — newer, less
  battle-tested than the two above, but converging on the same idea: an
  explicit "operating system for memory" with named lifecycle stages (create,
  activate, fuse, dispose).

**Fabrica borrows the tiered shape from MemGPT (working vs. long-term), but
deliberately does NOT adopt Generative Agents' single unified
recency+importance+relevance score across all three facets.** This is a
conscious divergence, not an oversight, and the reasoning is Civitas's own
platform philosophy, not a Fabrica-specific one:

**Civitas is library-first, low-coupling/high-cohesion by design — every
component must work as an independently reusable piece, and only the
orchestrator layer is allowed to be tightly integrated** (see
[architecture.md §1a](architecture.md#1a-a-platform-wide-principle-named-explicitly-library-first-low-coupling-high-cohesion)
for the full principle and the other decisions it explains). A single
composite score across `WorkingMemoryStore`, `Compactor`, and `MemoryStore`
would require each of them to know about the others' internal signals to stay
consistent — exactly the coupling this platform avoids everywhere else. It
would also reintroduce the dependency `Summarizer`'s DI boundary exists
specifically to keep out: Generative Agents' "importance" scoring comes from
an LLM call, and folding that into `Compactor` would mean Fabrica owning a
model dependency it has deliberately avoided at every other layer.

**The trade-off, stated plainly rather than hidden:** three independently
swappable policies (recency-only in `Compactor`, semantic-score in
`MemoryStore`, exact-key lookup in `WorkingMemoryStore`) are individually
simpler and more reusable outside Fabrica, but less principled as a whole than
one unified formula would be. For a platform library serving many different
harnesses with different needs, that trade favors reusability over unified
elegance — the right call *for this kind of system*, even though it would be
the wrong call for a single fixed research agent like the one Generative
Agents describes.

## Thesis for the long-term facet: wrap, don't build — but ship a working default, not the raw library

Agent memory is a **crowded, mature market with no single winner.** Reimplementing a
knowledge graph or vector-memory engine would be off-strategy. Consistent with the
platform's interface-first philosophy, Fabrica ships a **`MemoryStore` protocol** with
**adapters** for the established players.

**Validated, not just proposed** — see
[SPIKE-memory-mem0-wrap.md](../specs/archive/spikes/SPIKE-memory-mem0-wrap.md): a
working `MemoryStore` adapter over real Mem0 was built and round-tripped (write →
search, real local embeddings, real relevance score) in minutes. But "wrap, don't
build" is **not** the same as "wrap, zero config" — Mem0's own defaults require an
OpenAI API key just to instantiate. **The `fabrica-contrib[mem0]` adapter must ship
a pinned, working local config (fastembed + chroma + `infer=False`) as its own
default** — Priya should never see the credentials error this spike hit first.
Two real frictions the adapter must absorb invisibly: Mem0's own `add()`/`search()`
have inconsistent parameter conventions (top-level kwargs vs. a `filters=` dict),
and there's no native `team_id` (works via `metadata`, confirmed end-to-end).

## The long-term-memory landscape (2025)

| Player | Shape | Note |
|---|---|---|
| **Mem0** | open-source memory layer; vector + graph + KV, 3-scope (user/session/agent) | widely deployed; some reports of fragility |
| **Zep** | temporal knowledge graph (Graphiti); sub-200 ms retrieval | strong benchmarks; **self-host CE deprecated Apr 2025 → cloud-only** |
| **Letta** (ex-MemGPT) | "LLM-as-OS" full agent runtime with tiered self-editing memory | it's a *runtime*, so wrap the memory API, not the whole thing |
| **Cognee** | open-source graph+vector+relational memory | good production adoption |
| **LangMem** | LangChain SDK; semantic/episodic/procedural; LangGraph-native | best if already in the LangChain ecosystem |

Takeaway: these differ enough that a **protocol + adapters** serves users better than
one opinionated build. Zep going cloud-only is a live example of *why* a portable
interface matters. This landscape and this takeaway apply only to the long-term
facet — there is no equivalent "market" for working memory or compaction, since
both are thin, Fabrica-owned mechanisms, not markets to wrap.

## Interface: long-term memory (`MemoryStore`)

Unchanged from the original design — the reframe adds two new facets, it doesn't
alter this one.

```python
class MemoryStore(Protocol):
    async def write(self, scope: Scope, item: MemoryItem) -> str: ...
    async def search(self, scope: Scope, query: str, limit: int = 5) -> list[MemoryItem]: ...
    async def get(self, scope: Scope, id: str) -> MemoryItem | None: ...
    async def forget(self, scope: Scope, id: str) -> None: ...

@dataclass
class Scope:
    user_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    team_id: str | None = None    # shared with usage/budget rollups — see
                                   # civitas-presidium-integration.md#usage--budget-ceilings
```

Default: an in-process store (SQLite/vector) so `pip install fabrica` works with zero
infra. Adapters: `fabrica-contrib[mem0|zep|letta|cognee|langmem]`.

## Interface: working memory (`WorkingMemoryStore`)

A scratchpad, not a knowledge base — no semantic search, no external infra, tied to
`Scope.session_id`'s lifecycle by default. Deliberately much simpler than the
long-term store: there is no "market" here to wrap, so Fabrica owns this outright
rather than shipping a protocol-plus-adapters shape for it.

```python
class WorkingMemoryStore(Protocol):
    async def remember(self, scope: Scope, key: str, value: Any) -> None: ...
    async def recall(self, scope: Scope, key: str) -> Any | None: ...
    async def snapshot(self, scope: Scope) -> dict[str, Any]: ...
    async def clear(self, scope: Scope) -> None: ...
```

No `promote()` bridge to long-term memory is planned — if a harness decides a
working-memory item is worth keeping past the session, it reads it via `recall`/
`snapshot` and writes it into the long-term store itself, explicitly. Consistent
with keeping `ToolManager`/`SkillManager` as separate classes rather than hiding
behavior behind a cross-cutting method: no magic bridges between facets that have
genuinely different persistence and governance semantics.

## Interface: compaction (`Compactor`, DI'd with a `Summarizer`)

The part that must **not** grow its own model dependency. If `Compactor` made its
own LLM call to produce summaries, Fabrica would need its own model credentials
and config — a real "wrap, don't build" violation, and the one thing everything
else in this design has been careful to avoid. Instead: the harness already has a
model connection (or wants to use a smaller/cheaper model dedicated to
summarization) — it injects a `Summarizer` once, at construction time, exactly
like `SandboxPool` is constructed with a `Sandbox` backend and `Retriever` with a
`RetrieverBackend`. Not a per-call callback — real dependency injection, so the
same summarization mechanism is reused across every `compact()` call, not
re-supplied at each site that needs one.

```python
class Summarizer(Protocol):
    """Injected dependency. Fabrica never constructs one itself and never
    holds model credentials — the harness supplies this, wrapping
    whichever model connection it already has."""
    async def summarize(self, messages: list[Message], *, target_tokens: int) -> str: ...


class Compactor(Protocol):
    """The compaction POLICY — which messages are preserved verbatim vs.
    folded into a summary. Swappable, like Sandbox/RetrieverBackend, so a
    non-default policy (e.g. "preserve messages tagged important" instead
    of "preserve the last N") doesn't require touching MemoryManager."""
    async def compact(
        self, messages: list[Message], *, budget_tokens: int
    ) -> CompactionResult: ...


class RecencyCompactor:
    """The default Compactor implementation: preserve the last
    preserve_last_n messages verbatim, fold everything older into one
    summary via the injected Summarizer."""
    def __init__(self, summarizer: Summarizer, *, preserve_last_n: int = 6) -> None: ...


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    preserved: list[Message]
    tokens_before: int
    tokens_after: int
```

`Message` is a new type this facet introduces — not yet reconciled with whatever
representation Civitas's own runtime loop already uses internally for conversation
history. That reconciliation is real integration work, flagged below, not assumed
away by inventing a redundant type that quietly conflicts with one Civitas already
has.

## The unified facade: `MemoryManager`

One class agents/harnesses actually interact with, composed of the three backends
above — the same "public engine wraps swappable pieces" shape as `Retriever` and
`SandboxPool`, used a third time here rather than inventing a new pattern:

```python
class MemoryManager:
    def __init__(
        self, working: WorkingMemoryStore, long_term: MemoryStore, compactor: Compactor,
    ) -> None: ...

    # working memory
    async def remember(self, scope: Scope, key: str, value: Any) -> None: ...
    async def recall(self, scope: Scope, key: str) -> Any | None: ...

    # compaction — Civitas decides WHEN (a budget threshold it already
    # tracks), this decides HOW; MemoryManager itself stays ignorant of
    # summarization, delegating entirely to the injected Compactor
    async def compact(self, messages: list[Message], *, budget_tokens: int) -> CompactionResult: ...

    # long-term memory — delegates directly to the MemoryStore protocol
    async def write(self, scope: Scope, item: MemoryItem) -> str: ...
    async def search(self, scope: Scope, query: str, limit: int = 5) -> list[MemoryItem]: ...
    async def get(self, scope: Scope, id: str) -> MemoryItem | None: ...
    async def forget(self, scope: Scope, id: str) -> None: ...
```

## Deployment modes

- **Long-term memory** — Library (in-process default store) or Service (a
  `MemoryStore` `GenServer`, or a remote adapter, shared across agents) —
  unchanged from the original design.
- **Working memory** — almost always Library-mode in-process, given its
  ephemeral, session-local nature. Worth persisting via Civitas's `StateStore`
  anyway (see Integration, below) so it survives a supervised process restart —
  that's the actual point of Civitas's own state supervision, not a reason to
  treat working memory as disposable.
- **Compaction** — has no deployment mode of its own. `Compactor` holds no
  persistent state — it's a pure function of the messages and budget it's given,
  plus whatever `Summarizer` it was constructed with.

## Integration

- **Civitas** persists the long-term default store AND working memory via its
  `StateStore` (working memory rides the same mechanism precisely so it survives
  a supervised restart, not just a clean session end) — **mediated through
  `CivitasBridge.request_state_persistence`**, not a direct call from
  `MemoryManager` (`system-design.md §1`'s correction: `CivitasBridge` is the
  only seam allowed to talk outward to Civitas; a manager reaching Civitas
  directly would quietly create a second one). Memory writes emit OTEL spans.
  Civitas also owns *when* to trigger compaction — it already tracks token
  budgets for its own runtime loop; `MemoryManager.compact()` is a mechanism
  Civitas calls, not one that watches its own budget independently.
- **Presidium** governs scope access (e.g. a grant like `data:customer_pii:read`
  gates cross-user recall) and can audit memory reads/writes. This applies to the
  long-term facet; working memory and compaction don't cross a scope-access
  boundary the same way, since they never leave the originating session.
- **Fabrica** owns the retrieval interface and adapter surface for long-term
  memory, and owns the working-memory and compaction mechanisms outright (no
  adapters needed for either, per the landscape note above).

## Open questions

**Long-term memory (original, unchanged):**

1. Is memory a v1 concern or deferred until tools/skills land? (Leaning: protocol in
   v1, adapters follow demand.)
2. How does memory interact with the code-mode sandbox — is recall a tool the sandbox
   calls, or context injected before the run?
3. `infer=False` (needed for a zero-LLM-dependency default) trades away Mem0's
   native semantic dedup/update — confirmed via spike: two writes of identical
   content produced two entries, not one updated one. Is write-only-no-dedup an
   acceptable default, or does the adapter need its own dedup layer?
4. `infer=True` (Mem0's actual smart-memory value proposition, LLM-driven fact
   extraction) remains completely untested — the spike deliberately used the
   zero-infra local path only.
5. Only Mem0 was spiked. Zep, Letta, Cognee, and LangMem may have entirely
   different integration frictions — unknown until each is tried.

**Working memory (new):**

6. Should `WorkingMemoryStore` support anything beyond exact-key recall — e.g. a
   `list_keys` or prefix query — or is that scope creep toward re-implementing
   the long-term store's search? Leaning: no, keep it a plain scratchpad.
7. Is there a size ceiling per session before working memory itself needs
   compaction, or is it assumed to always stay small relative to the context
   window it's meant to save? Unverified either way.

**Compaction (new):**

8. `Message`'s shape hasn't been reconciled with whatever Civitas's runtime loop
   already uses internally for conversation history — real integration work, not
   assumed away.
9. `RecencyCompactor`'s `preserve_last_n` default of 6 is a guess, not validated
   by any spike — unlike almost everything else in this design, compaction has
   zero empirical evidence behind it yet. A spike here (does a
   summary-plus-recent-N actually preserve enough for a model to keep working
   correctly, measured the same rigorous way SPIKE-code-mode-execution.md
   measured correctness) is a real gap, not a formality.
10. Should `Compactor` be swappable per-deployment the same way the isolation
    backend or retrieval backend is, or is `RecencyCompactor` good enough as the
    only implementation for v1? Leaning toward shipping one default and revisiting
    only if a real gap forces it — consistent with how Windows support and macOS
    Tier 2 were handled.
