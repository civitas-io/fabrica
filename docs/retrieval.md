# Retrieval: the shared engine behind tools and skills

**Status:** Design · **Last updated:** 2026-08
**Resolves:** [critique.md §C1](critique.md) (skills' linear-index-cost gap vs.
tools' O(1) `find_tools`) via unification rather than either accepting the gap or
duplicating infrastructure.

---

## Why this exists

`find_tools` ([tool-execution.md](tool-execution.md)) achieves O(1) token cost
because matching happens server-side — the model never sees the full registry.
The skills index, as originally sketched in
[skills-gateway.md](skills-gateway.md), did not have this property:
[SPIKE-skill-progressive-disclosure.md](../specs/archive/spikes/SPIKE-skill-progressive-disclosure.md)
measured it growing linearly (1,478→6,248 tokens, N=10→81) because the whole index
was placed directly in the model's context to browse.

Rather than accept that asymmetry or build a second, near-identical search
mechanism for skills, this doc unifies the **engine**, while deliberately keeping
memory's interface separate — it has different semantics (scoped to a user/
session/team, not a shared browsable registry), so forcing it into the same call
shape would paper over a real difference rather than resolve one.

## The three sources of inspiration, and what each contributes

| Source | Contributes |
|---|---|
| **`find_tools`** (RFC 0001) | The interface shape: one meta-tool, server-side matching, model never sees the full list. |
| **Anthropic's `tool_search_tool`** (`defer_loading`) | The eager/deferred split — critical, frequently-used items stay always-visible; everything else is deferred and only surfaces via search. |
| **prx** ([SPIKE-tool-disambiguation-retrieval-quality.md](../specs/archive/spikes/SPIKE-tool-disambiguation-retrieval-quality.md)) | A validated backend (100% precision@3, zero fine-tuning) plus two hard rules that now apply platform-wide: **rank, never absolute threshold** ([SPIKE-tool-retrieval-token-overhead.md](../specs/archive/spikes/SPIKE-tool-retrieval-token-overhead.md), [SPIKE-code-mode-execution.md](../specs/archive/spikes/SPIKE-code-mode-execution.md)), and **persistent-process integration**, not subprocess-per-call ([SPIKE-prx-invocation-latency.md](../specs/archive/spikes/SPIKE-prx-invocation-latency.md)). |

## The engine

```python
class Retriever(Protocol):
    """The shared retrieval engine. One implementation, reused by every
    discovery surface that needs it — this is where 'wrap, don't build'
    and rank-not-threshold live ONCE, not duplicated per surface."""
    def index(self, items: list[Indexable]) -> None: ...
    def search(self, query: str, limit: int = 5) -> list[RankedMatch]: ...
    # Returns rank-ordered matches only. Callers MUST NOT filter by an
    # absolute score — Spike 4/7 finding: correct hits and near-misses can
    # land in the same low-score band (observed: 0.01-0.04 on real data).

@dataclass
class Indexable:
    id: str
    kind: Literal["tool", "skill"]     # memory is intentionally excluded, see below
    name: str
    description: str                   # the text actually embedded/matched
    eager: bool = False                # inverted `defer_loading` — True stays
                                        # always in context, skipping search entirely

@dataclass
class RankedMatch:
    item: Indexable
    rank: int
```

## The model-facing surface

Tools and skills share **one** meta-tool on top of this engine:

```json
{
  "name": "find",
  "description": "Search for tools or skills by capability. Returns matching items.",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {"type": "string"},
      "kind": {"type": "string", "enum": ["tool", "skill"]},
      "limit": {"type": "integer", "default": 5}
    },
    "required": ["query", "kind"]
  }
}
```

`eager` items (a small, curated set per deployment) are never searched for — they're
placed directly in context alongside the `find` schema itself, matching Anthropic's
`defer_loading: false` concept for critical/frequently-used items.

**Memory stays separate.** `MemoryStore.search(scope, query, limit)`
([memory.md](memory.md)) keeps its own signature — it's scoped, not a shared
registry — but its implementation plugs into the same `Retriever` underneath.
Shared code, honest interface per surface.

## Backends — Rust for the built parts, wrap everything else

Per the engineering principle in [context-layer.md](context-layer.md#engineering-principle-rust-for-compute-python-for-interface):
where Fabrica *builds* a backend, it's Rust with a Python binding, not pure Python.
Where Fabrica *wraps* an existing engine, the wrapped engine's own language is
irrelevant — that's the whole point of wrapping.

| Backend | Package | Shape |
|---|---|---|
| `KeywordBackend` (default) | `fabrica` core | **Rust crate + PyO3 binding**, not `rank_bm25`-in-Python as originally sketched in RFC 0001 — this is Fabrica-built, so it follows the Rust-for-compute principle. Still `pip install fabrica` for the user; the compute lives in a compiled extension, not a Rust toolchain the user sees. |
| `PrxBackend` | `fabrica-contrib[prx]` | Wraps prx directly — already Rust-native, already validated. Persistent-process integration per Spike 5, not subprocess-per-call. |
| `LlamaIndexBackend` / `LangChainBackend` | `fabrica-contrib[llamaindex\|langchain]` | Wraps `ObjectIndex`/`EnsembleRetriever` — Python libraries, wrapped as-is, no reimplementation. |

## Integration with the platform

- **Civitas**: the engine (whichever backend) runs as a supervised `GenServer` when
  in service mode, matching prx's persistent-process finding — a crashed retriever
  restarts automatically rather than needing a fresh subprocess per call.
- **Presidium**: grants filter what's *indexable* in the first place — an agent
  without a grant for a tool/skill never sees it in `find` results, same governance
  seam already documented for `find_tools`/`SkillStore` individually.
- **Fabrica**: owns the `Retriever` protocol and the default `KeywordBackend`
  implementation only; every other backend is an adapter over someone else's engine.

## Open questions

1. Exact Rust/PyO3 packaging shape for `KeywordBackend` — a workspace crate inside
   `fabrica`, or a separate `fabrica-retrieval-core` crate versioned independently?
   Implementation-phase decision, not resolved here.
2. Does `eager` get set per-deployment (config) or per-item (author-declared in the
   tool/skill's own metadata)? Both are defensible; not decided.
3. Scale ceiling — this doc resolves the O(1)-vs-linear architecture question, but
   the *quality* of matching at hundreds of tools/skills combined in one index
   (rather than tested separately, as the spikes did) remains unmeasured.
4. Whether `find`'s `kind` parameter should support searching both tools and skills
   in one call (a task that plausibly needs either) or forces the model to pick —
   currently specified as required, forcing a choice; worth revisiting once real
   usage patterns exist.
