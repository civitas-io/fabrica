# Tool Execution: Code Mode, with `find_tools` as fallback

**Status:** Design · **Last updated:** 2026-08 · **Supersedes:** RFC 0001 (as headline)

---

## What changed since RFC 0001

RFC 0001 proposed a `find_tools(query)` meta-tool so the model retrieves only the tool
schemas it needs. **That thesis was correct and is now proven — by being shipped
natively:**

- **Anthropic Tool Search Tool** (24 Nov 2025): mark tools `defer_loading: true`; the
  model sees a search tool + critical tools and pulls the rest on demand. Variants
  `tool_search_tool_bm25_20251119` and `tool_search_tool_regex_20251119`; beta header
  `advanced-tool-use-2025-11-20`; Sonnet 4.5+/Opus 4.5+. Reports **190K+ tokens saved.**

So a standalone `find_tools` gateway is no longer a differentiated product. The
frontier moved one layer over:

- **Code execution with MCP / "Code Mode"** (Anthropic + Cloudflare, Nov 2025): tools
  are presented as a **filesystem of code APIs**; the agent **writes code** to call
  them; the code runs in a **sandbox**; intermediate results stay in the sandbox and
  never enter the model context. Reported: **~150K → ~2K tokens (98.7%).**

The bigger win isn't schema retrieval — it's keeping **intermediate results and
orchestration out of the window entirely.** That is Fabrica's headline.

## Fabrica's two modes

### Headline: tools-as-code + sandboxed execution

```
1. Fabrica exposes tools as a code-API namespace (progressive disclosure —
   the model explores a directory of typed stubs, loads only what it opens).
2. The model writes code against that namespace.
3. Fabrica runs the code in an isolated Sandbox (see isolation.md).
4. Only the final result returns to the model context.
   Loops, filtering, joins, retries — all happen in the sandbox.
```

Why Fabrica and not just use Anthropic/Cloudflare:

- **Vendor-neutral.** Works across model providers, not Claude-only.
- **Self-hostable.** Not Workers-locked.
- **Runtime-native sandbox.** The execution substrate is a supervised Civitas
  process — fault-tolerant, observable, and hardenable to microVMs. Cloudflare ties
  this to Workers; Anthropic to its own platform. Civitas already *is* a process
  runtime.
- **Governed by Presidium.** Grants and policy gate what the sandbox may touch.

### Fallback: `find_tools` retrieval (from RFC 0001)

For models/hosts that don't do code mode, Fabrica keeps the original interface:

```json
{ "name": "find_tools",
  "input_schema": { "type": "object",
    "properties": { "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5} },
    "required": ["query"] } }
```

- Keyword (BM25) backend by default; embedding backend via `fabrica-contrib[search]`.
- Aggregates multiple sources (local registry, MCP servers, remote).
- Returns schemas in the host API's native format (Anthropic / OpenAI).

This mode is a *compatibility floor*, not the pitch.

## Build vs. wrap: the retrieval backend

Research turned up a **two-tier market** for tool retrieval (full detail in
[landscape.md](landscape.md#6-tool-search--retrieval-backends--a-two-tier-market)):

- **Tier 1 — mature, inside mega-frameworks or hosted products.** LlamaIndex's
  `ToolRetriever`/`ObjectIndex`, LangChain's `retriever_tool` + `EnsembleRetriever`
  (hybrid BM25+embeddings), and Composio's hosted `COMPOSIO_SEARCH_TOOLS`. All
  actively maintained. All require adopting a mega-framework or a commercial platform.
- **Tier 2 — a real gap.** No small, standalone, framework-agnostic, **MCP-native**
  library exists that just does "one `find_tools` meta-tool, multiple sources,
  keyword-first with optional embeddings." That gap is what Fabrica's `find_tools`
  fallback fills.

So: **build the interface layer** (it's genuinely missing) — but **wrap, don't
reimplement, the embedding engine underneath it**, mirroring the `MemoryStore`
posture in [memory.md](memory.md):

- `fabrica` core ships a zero-dependency `KeywordBackend` (BM25) by default.
- `fabrica-contrib[llamaindex]` / `fabrica-contrib[langchain]` wrap `ObjectIndex` /
  `EnsembleRetriever` as an `EmbeddingBackend`, instead of Fabrica maintaining its
  own vector-index code.
- `fabrica-contrib[search]` (sentence-transformers, from the original RFC) remains
  as a dependency-light option for teams that want embeddings without adopting
  either framework.

## Why this lives in Fabrica, not the Rust toolchain (prx/tessera)

Worth stating explicitly since it wasn't obvious at first glance: `find_tools` /
tools-as-code is **not** a candidate for the prx/tessera-style Rust CLI toolchain.
It fails every test that put prx and tessera there instead:

| | find_tools / tools-as-code | prx / tessera |
|---|---|---|
| Consumer | the **model**, mid-inference, inside a running agent | a human/coding-agent at a terminal |
| Needs governance? | yes — Presidium grants filter what's discoverable/callable | no governance model |
| Needs supervision/restart? | yes — runs as a Civitas-supervised process | no — one-shot CLI invocation |
| Needs the sandbox/isolation story? | yes — directly wired to `Sandbox` (isolation.md) | no |
| Distribution | `pip install fabrica` | Homebrew / static binary |

The tight coupling to Civitas (supervision, `ToolProvider`/`ModelProvider`) and
Presidium (grants, audit) **is the differentiator** (see civitas-presidium-
integration.md). Spinning it out as a standalone Rust/MCP binary would strip that
coupling away and reduce it to just another commodity MCP gateway — the exact
thing [landscape.md](landscape.md#2-mcp-gateways--registries--commoditized-do-not-build)
says not to build. Portability for non-Civitas/non-Python consumers is still
achieved — Fabrica exposes itself as an MCP server, so any MCP-compatible host can
connect without needing a Rust rewrite or a separate brand.

## Core interfaces (sketch)

```python
class ToolNamespace(Protocol):
    """Tools exposed as an explorable code-API surface."""
    def stubs(self) -> str: ...                     # progressive-disclosure listing
    def open(self, path: str) -> ToolSchema: ...    # load one tool's definition
    async def call(self, name: str, params: dict) -> ToolResult: ...

class ToolSource(Protocol):                         # from RFC 0001, retained
    name: str
    async def list_tools(self) -> list[ToolSchema]: ...
    async def call_tool(self, name: str, params: dict) -> ToolResult: ...
    async def health_check(self) -> bool: ...
```

Code-mode execution binds a `ToolNamespace` into a `Sandbox` runtime (see
[isolation.md](isolation.md)); the generated code imports the namespace and returns a
value. Fabrica captures stdout/stderr, enforces limits, and returns only the result.

## Open questions

0. **Disambiguation among overlapping tools/skills is one unsolved design question,
   not several.** See [skills-gateway.md](skills-gateway.md#open-questions) —
   ranking/prioritization tooling across `find_tools`, skill discovery, and memory
   search is deferred to a single cross-cutting design pass, not solved ad hoc per
   surface.
1. Language of the sandbox API — Python first (matches Civitas); TypeScript later?
2. Credential propagation into the sandbox — coordinate with Presidium + tessera so
   the sandbox can *use* secrets it can't *read*.
3. Result-size policy — when a result is itself huge, does it stay in the sandbox as a
   handle the next code block can reference (à la code mode's persistent filesystem)?
4. Determinism/replay — can a code-mode run be recorded and replayed for audit?
