# Fabrica as an MCP Server

**Status:** Design · **Last updated:** 2026-08
**Depends on:** [mcp-integration.md](mcp-integration.md) (the opposite,
complementary direction), [tool-execution.md](tool-execution.md) (the claim this
closes), [landscape.md §2](landscape.md#2-mcp-gateways--registries--commoditized-do-not-build)
(the constraint checked again, in a new direction)

---

## This closes a debt, not just adds a feature

`tool-execution.md` already asserts this exists, in passing, to justify a
different decision: *"Fabrica exposes itself as an MCP server, so any
MCP-compatible host can connect"* — the stated reason `find`/code-mode doesn't
need a separate Rust/MCP rewrite the way `prx`/`tessera` got. That argument has
been standing on an unbuilt feature. This doc is what actually makes it true.

## The constraint, checked again, in the new direction

`landscape.md §2` rejected a **standalone product** proxying/aggregating many
third-party MCP servers for many consumers. `FabricaMCPServer` is the opposite
shape: **Fabrica exposing its own native capabilities as one MCP endpoint** —
the same pattern as GitHub's or Claude Desktop's own MCP servers, a service
offering its existing functionality through an additional protocol surface.
Not a gray area; checked the same way [mcp-integration.md](mcp-integration.md)
checked the client direction, and it comes out the same way: this isn't the
rejected category.

## The mapping — and the principle that almost got missed

`PromptManager` maps directly onto MCP's native **Prompts** primitive
(`prompts/list`/`prompts/get`) — not an adapter, the same operation MCP
already defines. Everything else becomes Tools.

**The principle that matters more than the mapping itself**: MCP's
`list_tools()` expects a flat, discoverable catalog. If `FabricaMCPServer`
exposed every registered `ToolNamespace` tool and every skill as its own
individually-listed MCP tool, an external client's model would pay the exact
O(N) schema-dump cost this entire project exists to eliminate — just moved
from Fabrica's internal model onto whoever calls in from outside. That would
be a real self-contradiction, not a subtlety.

**Resolution: expose the same two-path shape externally that Fabrica already
uses internally.**

```
fabrica_find(query, kind)      -- the fallback/discovery tool
fabrica_run_code(code)         -- code-mode headline, generic execution
fabrica_run_skill(name, args)  -- SkillManager's structured-args path, exposed
                                   as one generic tool, not one per skill
fabrica_memory_write(...)
fabrica_memory_search(...)
```

Five tools, fixed size, **regardless of how many tools/skills are actually
registered underneath.** An external model discovers via `find()` and writes
code against what it finds, exactly like a native Fabrica-internal model
does. This isn't a new principle — it's `tool-execution.md`'s existing
two-path thesis, applied a second time, in a direction nobody had checked.

## Two layers of authorization, only one of which is new

Every governance mechanism designed so far assumes a call already arrives
from a trusted, Civitas-supervised `agent_id`. An MCP server accepting network
connections needs an answer to a question that has never existed anywhere
else in this design: **who's even allowed to open a connection, before any
`check_grant` call happens at all.**

**Layer 1 (new): connection-level authentication**, transport-dependent:

- **stdio** (a local host — Claude Desktop, Cursor — spawning
  `FabricaMCPServer` as a subprocess): auth is implicit, the same as every
  local MCP server today. Whoever can spawn the process is trusted. No new
  mechanism.
- **HTTP/SSE** (a hosted, network-reachable deployment): an API key /
  bearer token, presented at connection time — resolved above, deliberately
  distinct from `PresidiumClient`'s mTLS choice (see reasoning above).

**Layer 2 (reused, unchanged): per-call governance.** Once a connection is
authenticated, the resolved identity becomes an `agent_id`, and everything
downstream is exactly what already exists — `check_grant`,
`execute_in_sandbox`, OTEL spans with `Scope` attributes. The new
authentication layer sits *in front of* the existing governance model; it
does not duplicate or parallel it.

**Deferred, not designed here**: OAuth2-style per-user consent flows (needed
if, say, Claude Desktop connects on behalf of many distinct end-users, not one
operator-provisioned integration). A real richer alternative to a bare API
key, correctly out of scope for a first design pass — same "ship the narrow
default, extend later" call made throughout this project.

## Isolation: fail closed by default for external callers, fourth instance of the same rule

`SandboxPool`'s tier is a deployment-wide setting, not chosen per-call. An
external MCP client is categorically less trusted than a native, already-
supervised in-process Civitas agent — `check_grant` gates *whether* an
execution is authorized, but says nothing about *how strongly* it should be
isolated once authorized. Resolved the same way three earlier cases were
resolved, not by inventing a new shape:

**`FabricaMCPServer` refuses to start against a `SandboxPool` configured below
Tier 2, unless `allow_weak_isolation_for_external_callers=True` is explicitly
passed.** Doesn't silently force a stronger tier than an operator configured
(that would surprise them with latency/resource characteristics they didn't
choose) — but doesn't silently accept a weak tier for network-exposed,
untrusted code either. `NullPresidiumClient.allow_ungoverned`,
`MCPClient.allow_unsandboxed`, and now this: **when a security mechanism might
be insufficient for the situation at hand, fail closed by default; the
exception is always an explicit, greppable opt-in flag.** Fourth confirmed
instance — this is a real rule this project applies, not a coincidence
of phrasing.

## Deployment shape, and a reuse worth stating rather than re-solving

`FabricaMCPServer` wraps an already-built `Fabrica` facade — the output of
`CivitasBridge.build()` — as an optional additional front door a deployment
can stand up. It does not replace direct in-process use by native Civitas
agents; both can exist against the same underlying object graph
simultaneously.

**No new rate-limiting mechanism is needed at this layer.** `SandboxPool`'s
existing bounded-overflow protection (`warm_size`/`max_concurrent`/queue
timeout, `contracts/sandbox.md`) already defends against a runaway caller
exhausting the pool — a `SandboxPoolExhaustedError` from an external MCP
client is handled exactly the same way as one from an internal agent. This is
inherited protection, not a gap to close.

## Open questions

1. The exact bearer-token issuance/rotation mechanism (static operator-issued
   keys vs. something more dynamic) isn't designed here — resolved that
   *tokens*, not mTLS, are the right shape; not resolved how they're minted,
   distributed, or revoked.
2. Whether `fabrica_run_skill` should expose a way to `list_skills()` (mirroring
   `fabrica_find(kind="skill")`) as a distinct MCP capability, or whether
   `find()` alone is sufficient discovery for both tools and skills externally
   — leaning toward the latter (one discovery path, not two), not decided.
3. Multi-tenant `FabricaMCPServer` deployments (one server, many distinct
   external callers with different `agent_id`s derived from different API
   keys) — the auth-to-`agent_id` mapping is sketched, not stress-tested
   against a real multi-tenant shape.
4. Zero spike coverage — same honest flag as `mcp-integration.md`'s client
   direction. Nothing has validated an external MCP client actually driving
   `fabrica_find`/`fabrica_run_code` end-to-end.
