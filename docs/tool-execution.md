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

1. Language of the sandbox API — Python first (matches Civitas); TypeScript later?
2. Credential propagation into the sandbox — coordinate with Presidium + tessera so
   the sandbox can *use* secrets it can't *read*.
3. Result-size policy — when a result is itself huge, does it stay in the sandbox as a
   handle the next code block can reference (à la code mode's persistent filesystem)?
4. Determinism/replay — can a code-mode run be recorded and replayed for audit?
