# Memory

**Status:** Design · **Last updated:** 2026-08

---

## Thesis: wrap, don't build

Agent memory is a **crowded, mature market with no single winner.** Reimplementing a
knowledge graph or vector-memory engine would be off-strategy. Consistent with the
platform's interface-first philosophy, Fabrica ships a **`MemoryStore` protocol** with
**adapters** for the established players.

## The landscape (2025)

| Player | Shape | Note |
|---|---|---|
| **Mem0** | open-source memory layer; vector + graph + KV, 3-scope (user/session/agent) | widely deployed; some reports of fragility |
| **Zep** | temporal knowledge graph (Graphiti); sub-200 ms retrieval | strong benchmarks; **self-host CE deprecated Apr 2025 → cloud-only** |
| **Letta** (ex-MemGPT) | "LLM-as-OS" full agent runtime with tiered self-editing memory | it's a *runtime*, so wrap the memory API, not the whole thing |
| **Cognee** | open-source graph+vector+relational memory | good production adoption |
| **LangMem** | LangChain SDK; semantic/episodic/procedural; LangGraph-native | best if already in the LangChain ecosystem |

Takeaway: these differ enough that a **protocol + adapters** serves users better than
one opinionated build. Zep going cloud-only is a live example of *why* a portable
interface matters.

## Interface (sketch)

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
```

Default: an in-process store (SQLite/vector) so `pip install fabrica` works with zero
infra. Adapters: `fabrica-contrib[mem0|zep|letta|cognee|langmem]`.

## Deployment modes

- **Library** — in-process default store, dev + small.
- **Service** — a `MemoryStore` `GenServer` (or a remote adapter) shared across agents.

## Integration

- **Civitas** persists the default store via its `StateStore`; memory writes emit OTEL
  spans.
- **Presidium** governs scope access (e.g. a grant like `data:customer_pii:read`
  gates cross-user recall) and can audit memory reads/writes.
- **Fabrica** owns the retrieval interface and adapter surface only.

## Open questions

1. Is memory a v1 concern or deferred until tools/skills land? (Leaning: protocol in
   v1, adapters follow demand.)
2. How does memory interact with the code-mode sandbox — is recall a tool the sandbox
   calls, or context injected before the run?
