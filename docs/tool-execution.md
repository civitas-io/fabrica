# Tool Execution: Code Mode, with `find` as fallback

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

### Fallback: `find` retrieval (was `find_tools` in RFC 0001)

For models/hosts that don't do code mode, Fabrica keeps a retrieval interface —
but it's no longer tool-specific. See **[retrieval.md](retrieval.md)** for the
full design: a single `find(query, kind, limit)` meta-tool shared by tools and
skills, backed by a pluggable `Retriever` engine (rank-ordered results only —
never filtered by absolute score, per
[SPIKE-tool-retrieval-token-overhead.md](../specs/archive/spikes/SPIKE-tool-retrieval-token-overhead.md)).
This mode is a *compatibility floor*, not the pitch — see
[SPIKE-code-mode-execution.md](../specs/archive/spikes/SPIKE-code-mode-execution.md)
for why code-mode is now validated as the actual headline, not just the aspiration.

Backends (keyword default, prx/LlamaIndex/LangChain as adapters) are specified in
[retrieval.md](retrieval.md#backends--rust-for-the-built-parts-wrap-everything-else),
not duplicated here.

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

0. **Resolved by unification, not deferral.** Disambiguation across tools and
   skills is no longer "one unsolved question tracked in two places" — see
   [retrieval.md](retrieval.md), which gives both a shared `Retriever` engine and
   a shared `find(query, kind)` surface. Memory search remains intentionally
   separate (different semantics — scoped, not a shared registry) but plugs into
   the same underlying engine.
1. Language of the sandbox API — Python first (matches Civitas); TypeScript later?
2. Credential propagation into the sandbox — coordinate with Presidium + tessera so
   the sandbox can *use* secrets it can't *read*.
3. Result-size policy — when a result is itself huge, does it stay in the sandbox as a
   handle the next code block can reference (à la code mode's persistent filesystem)?
4. Determinism/replay — can a code-mode run be recorded and replayed for audit?
