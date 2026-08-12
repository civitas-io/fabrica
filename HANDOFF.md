# Handoff: Fabrica

**Purpose of this doc:** resume work cold, after a context compaction, without
re-deriving anything already decided. Read this first, then follow the links —
don't re-read the whole repo linearly.

---

## What Fabrica is, in one paragraph

Fabrica is the **context layer** of the Civitas platform — the third pillar
alongside Civitas (runtime, keeps agents alive) and Presidium (control,
governance). Fabrica decides what a Python agent sees and where its work
actually runs: tools, skills, memory, prompts, and the sandboxed execution
substrate underneath all of them. Its headline mechanism is **code-mode**: a
model writes code against a tool namespace, that code runs in an isolated
sandbox, and only the final result crosses back into the model's context —
validated by spike to be both ~79% cheaper *and* more correct than traditional
direct tool-calling (`SPIKE-code-mode-execution.md`).

Repo: `civitas-io/fabrica` (public). Design/validate/critique/architecture/
system-design/contracts is complete, and **all six object-model contracts
PLUS both MCP directions are now real, tested code** (`src/fabrica/`) —
see "Current state" immediately below for exactly what exists and what's
still genuinely left (mostly HTTP/SSE MCP transport and the
`civitas-contrib` migration/PR cleanup).

---

## Current state — read this section, not the chronological log below, for "where are we"

**This section is authoritative — rewritten in full for this compaction, not
appended to, since the previous version had drifted: the opening paragraph
above still said "no code exists yet" while this section's own nested detail
had grown to 225 lines describing five real, tested components.** Detailed
narrative for each component (bugs found, exact reasoning) lives in `git log`
commit messages, not repeated here — this section states facts, not stories.

### Design phase: complete

Full discovery→define→design→validate→critique→architecture→system-design→contracts
arc. Ten spikes, all real hardware/API evidence. Eight contracts written
(`Retriever`, `Sandbox`, `managers.md`, `memory.md`, `prompts.md`,
`civitas-bridge.md`, `mcp-integration.md`, `mcp-server.md`). Four platform-wide
rules confirmed multiple times, safe to apply without re-deriving:
**library-first/low-coupling** (`architecture.md §1a`); **requests, never
reaches in** (toward Civitas and Presidium alike); **external dependencies are
always fully-constructed objects, never raw config**; **fail closed by
default, explicit greppable opt-in to bypass** (four confirmed instances:
`allow_ungoverned`, `allow_unsandboxed`, `allow_weak_isolation_for_external_callers`,
plus the general rule itself).

### Implementation phase: all six object-model contracts are real code

`src/fabrica/` — all built to their exact contracts, all with real tests (not
mocked stubs standing in for untested logic), 154 tests total, clean
`ruff`/`mypy --strict`, stable across repeated runs:

- **`Retriever`** (`src/fabrica/retriever/`) — `KeywordBackend` (pure-Python
  BM25 via `rank-bm25`, deliberately not Rust/PyO3 yet — no performance
  number justifies that tooling cost). 16 tests.
- **`Sandbox`/`SandboxPool`** (`src/fabrica/sandbox/`) — `SubprocessSandbox`
  (Tier 0), a REAL subprocess + ZMQ `ipc://` tool-call bridge, not a stub.
  17 tests, including one real end-to-end tool-call round trip through an
  actual subprocess boundary.
- **`managers.md`** (`src/fabrica/managers/`, `src/fabrica/tools/`,
  `src/fabrica/presidium.py`, `src/fabrica/scope.py`) — `execute_in_sandbox`,
  `ToolManager`, `SkillManager`. `SkillManager`'s `SKILL.md` parser validated
  against the real 81-skill `bigpowers` catalog. `PresidiumClient` here is
  the Protocol only — the real REST+mTLS implementation is deliberately
  deferred (see "Immediate next action" below for why). 12 tests.
- **`MemoryManager`** (`src/fabrica/memory/`) — all three facets:
  `InMemoryWorkingMemoryStore`, `RecencyCompactor`/`NullCompactor` (both named
  edge cases from the contract exercised directly, not glossed over),
  `InMemoryMemoryStore`. 23 tests.
- **`PromptManager`** (`src/fabrica/prompts/`) — `InMemoryPromptStore` with
  real atomic version assignment under concurrency, `PromptManager`'s cache,
  `load()`'s `PROMPT.md` parser. 24 tests.
- **`CivitasBridge`** (`src/fabrica/civitas_bridge/`) — resolved via option
  (b): defer `PresidiumClient`'s real REST+mTLS engineering (no real
  Presidium endpoint exists to validate it against), build the
  `civitas`-facing half for real instead. `CivitasRuntime`/`StateStore` are
  structural Protocols (no hard `civitas` dependency for those); `GenServer`
  is a genuine, deliberate exception — imported directly, because
  Civitas's real dynamic-spawn mechanism is nominally coupled to that
  concrete class, not just its structural shape (mypy caught this for
  real when a first attempt at a structural `CivitasGenServer` Protocol
  failed contravariance checking against `Runtime.spawn`'s actual
  signature). `civitas>=0.11.0` is now a real runtime dependency of
  `fabrica-context`, not dev-only — the one deliberate exception to
  "depend on shapes, not packages", consistent with `CivitasBridge` being
  the one component `architecture.md §1a` licenses to integrate tightly.
  `request_supervision`/`request_state_persistence` are both real and
  tested against a genuine `civitas.runtime.Runtime`/`DynamicSupervisor`/
  `InMemoryStateStore` (spawning a real `GenServer`, real name-collision
  `SpawnError`, real name-bound state isolation) — not against a
  hand-rolled test double. 21 tests.

  **Two real gaps found and fixed in `contracts/civitas-bridge.md` before
  writing code against it, not papered over**: (1) `system-design.md`'s
  component matrix calls every manager a `GenServer` under service mode,
  but Civitas's real spawn mechanism reconstructs an agent class from a
  dotted path with only `name` — structurally incompatible with this
  codebase's constructor-injected managers (`ToolManager(retriever,
  sandbox_pool, presidium_client)`, etc). Resolved: `request_supervision`
  stays real and tested, but no manager calls it in v1 — it's available
  for a genuinely fresh, self-contained `GenServer`-shaped component,
  which none of Fabrica's own managers are. (2) `request_state_persistence`
  returns a `ComponentStateHandle` over a whole-blob `get`/`set`, but no
  `StateStore`-backed `MemoryStore`/`PromptStore` adapter exists to
  receive it (never designed in `contracts/memory.md`/`contracts/prompts.md`).
  Resolved the same way: the method itself is real and tested, `build()`
  just doesn't call it for managers yet — both `MemoryManager`/
  `PromptManager` use their in-memory default stores in both modes until
  that adapter is designed as its own unit of work.

Real bugs found and fixed by actually running things, not caught by review —
worth knowing these exist as a class, not just as history: macOS's long tmp
paths breaking `ipc://` socket length limits; a package submodule named
`types.py` shadowing Python's own stdlib `types` when run as a script;
`pytest` needing unique test-module basenames across the whole suite unless
test directories are proper packages (fixed by adding `__init__.py`
everywhere under `tests/`).

Two real contract gaps found and fixed in the docs *before* writing code
against them: `ToolNamespace` had no enumeration method (added
`list_schemas()`); `ToolManager.register()`/`SkillManager.load()` were
declared sync but need to await `Retriever.register()`'s `async def` (both
corrected to async).

- **`MCPClient`/`MCPToolNamespace`** (`src/fabrica/mcp/`) — migrated real
  code from `civitas-contrib/packages/fabrica`, not a rewrite. Two real
  corrections found reconciling against the actually-installed `mcp`
  v2.0.0 SDK (not transcribed from the older-SDK-era migrated code
  unchanged): attribute access is snake_case now (`tool.input_schema`,
  `result.is_error`), not the migrated code's camelCase; and `srt`
  (replacing `BubblewrapSandbox`) structurally REFUSES an
  unrestricted-network config (`allowedDomains: ["*"]` is a hard config
  error, confirmed by running `srt` directly) — `SandboxConfig.network=
  "allow"` now raises `UnsupportedSandboxConfigurationError` at
  `connect()` time rather than silently downgrading or passing through a
  config `srt` itself would reject (fifth confirmed
  fail-closed-by-default instance). `MCPToolNamespace`'s async-constructor
  gap (contract's own flagged open item) resolved with an async factory,
  `MCPToolNamespace.create(client)`. 21 tests, all against a REAL MCP
  server subprocess (`tests/mcp/fixtures/echo_server.py`, the actual `mcp`
  library's own server-side API, not a mock) — including real
  `srt`-sandboxed connections.
- **`FabricaMCPServer`** (`src/fabrica/mcp/server.py`) — built against
  `mcp.server.lowlevel.Server`'s real, current constructor-callback API
  (`on_list_tools`/`on_call_tool`/`on_list_prompts`/`on_get_prompt`), not
  an older decorator-based `FastMCP` style some `mcp` SDK versions use.
  Five fixed MCP tools + native Prompts mapping, exactly per the contract.
  **Both stdio AND HTTP transports are real** — HTTP reuses the `mcp`
  library's OWN bearer-auth support (`Server.streamable_http_app
  (token_verifier=...)`, real `AuthenticationMiddleware`/`BearerAuthBackend`/
  `RequireAuthMiddleware`) via a small `_TokenVerifierAdapter`, rather than
  hand-rolled ASGI middleware — confirmed working end to end with a real
  `uvicorn` server + real bearer-token accept/reject before wiring it in,
  not assumed from docs. `agent_id` is resolved from the verified token's
  `AccessToken.subject`, never from caller-supplied arguments.
  A real, previously-undocumented gap found and fixed at the same time:
  `ServerTransportConfig`'s "authenticator required if kind='http'" was
  only ever a docstring claim — `__post_init__` now actually enforces it.
  13 tests total: 8 against a REAL `mcp.ClientSession` over a real stdio
  subprocess to a real `CivitasBridge`-built `Fabrica`
  (`tests/mcp/fixtures/fabrica_stdio_server.py`, including one genuine
  code-mode execution reached entirely through the MCP protocol), 5 more
  against a REAL `uvicorn`-hosted HTTP server with real bearer-token
  accept/reject (unauthenticated rejected, wrong token rejected, correct
  token resolves to the right `agent_id`, memory writes/searches stay
  correctly `Scope`-isolated per resolved `agent_id`).
  **Deliberately still NOT built, stated honestly rather than stubbed**:
  `WeakIsolationError`'s real tier check — `SandboxPool` has no queryable
  tier attribute to check against yet, a pre-existing contract gap this
  pass surfaced but didn't introduce; the legacy SSE transport (distinct
  from the modern streamable-HTTP transport implemented here) — building
  the deprecated `mcp` transport first would be backwards.

**Not built yet, deliberately**: the real `fabrica-contrib[mem0|zep|letta|
cognee|langmem]` adapters (need real external services to test against,
`memory.md`'s own "wrap, don't build" thesis); a `StateStore`-backed
`MemoryStore`/`PromptStore` adapter for `CivitasBridge`'s service mode
(see `CivitasBridge`'s own entry above for why).

### What's left, in priority order

1. **`PresidiumClient`'s real REST+mTLS implementation** — deliberately
   deferred (option (b), chosen over building it against a self-written
   fake HTTP server), since no real Presidium deployment/endpoint exists
   anywhere to validate it against. `NullPresidiumClient` and the
   `PresidiumClient` Protocol are real and sufficient for everything built
   so far; revisit only once a real Presidium endpoint exists, or the fake-
   server-tested option is deliberately chosen instead.
2. **A `StateStore`-backed `MemoryStore`/`PromptStore` adapter** for
   `CivitasBridge`'s service mode — `request_state_persistence` itself is
   real and tested, but nothing consumes the `ComponentStateHandle` it
   returns yet (see `CivitasBridge`'s own entry above). Needs its own
   design pass: a snapshot format and a read-modify-write strategy over
   `ComponentStateHandle`'s whole-blob `get`/`set` — not designed in
   `contracts/memory.md`/`contracts/prompts.md` at all.
3. **`WeakIsolationError`'s real tier check** — `SandboxPool` has no
   queryable tier attribute yet (`contracts/sandbox.md` never specified
   one), so `FabricaMCPServer`'s `allow_weak_isolation_for_external_callers`
   is accepted and stored but currently has no effect. Needs a
   `SandboxPool.tier` (or equivalent) surface added to `contracts/sandbox.md`
   first.
4. A batch of older, explicitly-fine-to-leave-deferred design-layer items
   (`tool-execution.md`, `retrieval.md`, `isolation.md`, `skills-gateway.md`
   open questions) and smaller contract-level wrinkles (`RecencyCompactor`'s
   `preserve_last_n=6` still unvalidated, multi-tenant `FabricaMCPServer`
   untested under real concurrent load) — see `git log` and the contracts
   themselves for the full list; none of these block anything above.

**Immediate next action**: none of the remaining items are blocking each
other or anything already built -- pick whichever matters most next.
All three `civitas-contrib` housekeeping items (docs superseded, CI
lint/tests, the mypy type-duplication + optional-import fix) are now
fully closed out -- nothing pending there anymore.

## Read in this order if you're new to this

1. `README.md` — entry point, links to everything.
2. `docs/architecture.md` — visual walkthrough (8 SVG diagrams), the external/product view.
3. `docs/system-design.md` — internals: object model, deployment topology, error
   handling, state ownership (3 more SVG diagrams). One level below architecture.
4. `docs/critique.md` — every design claim checked against evidence. Read this to
   know what's *actually proven* vs. what's still an assumption.
5. `docs/contracts/*.md` — implementation-ready signatures, growing one component
   at a time (`Retriever`, `Sandbox`, then the managers).

---

## The full arc of work, in sequence (for context on *how* decisions were made)

1. **Discovery** — `docs/personas.md`: 5 personas (Priya/Marcus/Elena/Devon/Alicia)
   + JTBD, plus the model itself as a non-human actor.
2. **Define** — `docs/problem-definition.md`: per-persona problem statements,
   success metrics, non-goals, in build-priority order.
3. **Design** — `docs/context-layer.md`, `tool-execution.md`, `isolation.md`,
   `retrieval.md`, `memory.md`, `skills-gateway.md`,
   `civitas-presidium-integration.md`, `landscape.md`.
4. **Validate** — 9 spikes in `specs/archive/spikes/` (10th added later, see below),
   all real hardware/API
   evidence, not simulated:
   - `SPIKE-tool-retrieval-token-overhead.md` — `find_tools` stays near-flat vs. static linear growth (real Claude-on-Vertex calls)
   - `SPIKE-tool-disambiguation-retrieval-quality.md` — embeddings beat keyword matching (100% vs 67% precision@3); **includes the prx detour** that found prx's off-the-shelf model hits 100% precision@3 on this task
   - `SPIKE-prx-invocation-latency.md` — persistent process beats subprocess-per-call (37ms vs 75ms); found the cold/warm start gap (~260ms vs ~5ms)
   - `SPIKE-skill-progressive-disclosure.md` — validated against the **real bigpowers SKILL.md catalog** (not synthetic data); found the original skill-index design wasn't actually O(1)
   - `SPIKE-firecracker-boot-restore-latency.md` — real bare-metal hardware (homelab, SSH); restore 8.1–10.7ms confirmed; found boot-time claims conflated two different signals (VMM-ready 10.5ms vs. userspace-ready 1,055ms)
   - `SPIKE-macos-isolation-srt-libkrun.md` — `srt` real numbers (p50 152ms); libkrun confirmed to have **no snapshot/restore at all** (permanent ceiling)
   - `SPIKE-code-mode-execution.md` — **the most important one.** Validated the actual headline mechanism (not just the fallback): ~79% cheaper AND exactly correct in 3/3 runs, while the traditional approach was wrong in 3/3 runs
   - `SPIKE-memory-mem0-wrap.md` — real Mem0 integration; found it requires an API key by default (contradicts zero-infra assumption) and has an internal `add()`/`search()` parameter inconsistency
   - `SPIKE-zmq-sandbox-channel-feasibility.md` — confirmed `pyzmq` is viable (1.6MB, self-contained, 0.73ms round trip) for the Tier 0/1 sandbox callback
   - **10th spike, added post-contracts**: `SPIKE-recency-compactor-validation.md` —
     closed the project's biggest evidence gap (`RecencyCompactor` had zero
     validation until now). Real Gemini calls, 5 runs each: full history 5/5
     grounded-correct, `RecencyCompactor`'s real strategy 5/5 (matched the
     baseline exactly), naive truncation 0/5 genuinely grounded (it landed on
     the same answer every time but never once via real reasoning from the
     actual constraint). Caught and fixed a real false-positive in its own
     checker mid-spike — documented, not hidden. Validates the core
     summarize-vs-truncate mechanism; `preserve_last_n=6` itself remains an
     unvalidated guess, precisely scoped as still open.
5. **Critique** — `docs/critique.md`: every claim checked, 11 corrections + 5 open
   architecture decisions, **all resolved and applied**, not left as proposals.
6. **Architecture** — `docs/architecture.md` + `docs/assets/*.svg` (8 diagrams).
   **Hand-crafted SVG only, no Mermaid** — matches the visual system already used
   in `python-civitas`/`presidium`'s `docs/assets/` (dark cards, blue=Runtime,
   purple=Control, **amber=Context**, the new addition for Fabrica).
7. **System design** — `docs/system-design.md` + 3 more SVGs. Internal object
   model, library-vs-service topology, the internal code-mode sequence
   (including the sandbox→`ToolManager` callback loop). **5 open questions, all
   resolved** through direct back-and-forth (see below) — none left as
   placeholders.
8. **Contracts** (in progress) — `docs/contracts/`:
   - `retriever.md` — done. Found real gaps writing it (no removal method, no
     way to enumerate eager items, `kind` parameter question resolved).
   - `sandbox.md` — done. **Found and corrected a real bug**, not just a gap:
     `system-design.md`'s original resolution said a used sandbox instance gets
     reused after an overflow — wrong, arbitrary code may have left arbitrary
     state; corrected to always-terminate-and-restore-fresh.
   - `managers.md` — done. Formalized `PresidiumClient.check_grant` (never
     raises on Presidium-unreachable — returns `deny` as a plain value, so a
     broad `except:` can't accidentally treat an outage as permissive),
     `execute_in_sandbox` (the literal implementation of "composition, not
     inheritance"), `ToolManager`, `SkillManager`.
   - `memory.md` — done. Kept `MemoryItem.score` unlike `RankedMatch`
     (no counter-evidence exists for memory recall the way there was for
     tool retrieval — not copying the no-score rule by reflex). Made
     `Message.tokens` required, not optional, specifically to avoid Fabrica
     needing to bundle or guess at a model-specific tokenizer.
   - `prompts.md` + `contracts/prompts.md` — done, and the FIRST manager written
     design-doc-first (the other three formalized existing design docs; this
     one had none until now, and zero spike coverage — stated honestly, not
     hidden). Deliberately excludes both rendering and prompt compression, per
     `architecture.md §1a`'s library-first principle applied from the start:
     rendering is a harness decision.
   - `prompts.md` was then extended with a grounded "Explored: the wider
     feature space" survey (provider-side prompt caching mechanics, DSPy/
     TextGrad automated tuning, LLMLingua-2 extractive compression, registry/
     hub table-stakes features, a `PROMPT.md` portable format precedented by
     both Humanloop and this codebase's own `SkillManager.load()`). This
     **corrected an earlier claim**: compression does NOT belong as a
     `Compactor` extension — LLMLingua-2's extractive token-classification
     approach is mechanically nothing like `Compactor`'s abstractive
     `Summarizer`-based one; it's closer in shape to a `RetrieverBackend`
     (wrap a small local model) than to `Summarizer`. Two ideas flagged as
     worth prioritizing soon, not yet built: a cache-boundary marker
     (low-coupling, high-leverage) and the `PROMPT.md` format.
   - **Both promoted into `contracts/prompts.md`.** Resolved a real tension
     while doing so: `PromptManager` can't detect a template's cache
     boundary itself (it's contractually forbidden from parsing `content`'s
     templating syntax), so `cacheable`/`cache_boundary` are
     author-declared fields, stored and returned verbatim, never validated
     -- the author makes the claim, `PromptManager` just carries it.
     `load(path)` reads a `PROMPT.md` file (frontmatter + body, mirroring
     `SKILL.md`) and is idempotent for unchanged content, same pattern as
     `ToolManager.register()`.
   - **`CivitasBridge` — now done too.** See the `CivitasBridge` design
     walkthrough section further down for the full reasoning trail; all six
     object-model contracts are complete as of this revision.

---

## The five `system-design.md` decisions, resolved through direct challenge (worth knowing *how*, not just *what*)

Each of these went through a real back-and-forth, not a first-draft acceptance —
worth preserving the reasoning, not just the answer, since it shows the pattern
to keep using:

1. **Warm-pool-exhausted:** hybrid bounded overflow (cold-start up to a ceiling,
   then queue) — chosen specifically because unbounded cold-start is the exact
   "runaway resource consumer" shape Marcus's persona names as a fear.
2. **`PresidiumClient` transport:** REST+mTLS (confirmed Presidium is a genuinely
   separate deployment, not co-located). This led to removing `emit_usage_event`
   entirely — usage rides OTEL spans instead (async by construction, reusing
   existing plumbing instead of building new).
3. **Sandbox callback transport:** started as "vsock uniformly?", challenged
   twice — first to confirm the *trusted* `ToolManager`↔`SandboxPool` hop should
   just reuse Civitas's own transport ladder, then to correct an implicit
   Linux-only assumption (macOS has `VZVirtioSocketDevice`, Windows has
   `AF_HYPERV`/Hyper-V Sockets, the same thing WSL2 itself uses) — landed on
   ZMQ-with-a-relay, sanity-checked by spike.
4. **`ToolManager`/`SkillManager` split:** composition over inheritance — kept
   separate (genuinely different trust models: arbitrary generated code vs.
   author-trusted named scripts), shared execution plumbing via one helper
   function, not a base class.
5. **Mode-switching granularity:** phased — v1 ships one flag, but
   `CivitasBridge`'s internals are built as if per-component granularity
   already exists, so v2 (per-component overrides) is additive, not a rework.

---

## THE OPEN QUESTION THAT TRIGGERED THIS HANDOFF — resolved

**Resolved:** Fabrica's target is confirmed as general Civitas agents, not
coding agents specifically — but the resolution came with a real reframe, not
just a confirmation. `docs/memory.md` was rewritten to cover **three facets**,
not one: working memory (session-scoped scratchpad), compaction (a
harness-engineering primitive — Civitas decides *when* to compact, Fabrica
decides *how*), and long-term memory (the original Mem0-style design,
unchanged). The concrete trigger for adding compaction as a first-class facet:
**this very `HANDOFF.md` was a manual, human-triggered instance of exactly that
mechanism** — a person noticed context pressure and asked for a checkpoint by
hand, which is precisely the kind of thing a generic `MemoryManager` should
offer as a callable primitive instead.

Key design decision: `Compactor` never makes its own LLM call (would violate
"wrap, don't build" by requiring Fabrica to hold model credentials) — a
`Summarizer` is injected via real DI at construction time, same pattern as
`Sandbox`/`RetrieverBackend`. See `docs/memory.md`'s new sections for the full
`WorkingMemoryStore`/`Compactor`/`Summarizer`/`MemoryManager` shape.

**Update:** `docs/contracts/memory.md` is now written (see the Contracts list
above). Remaining flagged there, not resolved at the time: `RecencyCompactor`
had zero empirical backing (no spike) — **later closed**, see
`SPIKE-recency-compactor-validation.md` above. A single-message-exceeds-budget
edge case still has no defined behavior.

---

## What's explicitly left open elsewhere (not oversights — named, not buried)

- **Two remaining critique gaps** (`docs/critique.md`, closing section): other
  memory backends beyond Mem0 (Zep/Letta/Cognee/LangMem) untested; `SKILL.md`'s
  optional fields and bundled-resource loading (`scripts/`/`assets/`/`references/`)
  untested — zero bigpowers skills exercise that path.
- **Tier 2 sandbox relay** (`vsock`/`VZVirtioSocketDevice`/`AF_HYPERV` bridge
  implementation) — architecture resolved, implementation feasibility only
  sanity-checked for the *easier* Tier 0/1 half (`SPIKE-zmq-sandbox-channel-feasibility.md`).
  The harder half (the actual cross-VM-boundary relay) has no spike yet.
- **Contract-level open items**, each named in its own doc rather than silently
  assumed: `Retriever`'s eager-cache invalidation and batch-atomicity;
  `Sandbox`'s replenishment-scheduling mechanism, release-on-unknown-handle
  behavior, and whether `on_tool_call` needs its own timeout; `managers.md`'s
  `find()`-kind-override question and whether `SkillManager.load()` should be async.
- **Windows Tier 1** (`srt`'s Windows mode) — deliberately deferred, not
  blocking, per explicit direction ("small segment, spike only if a gap forces it").
- **macOS Tier 2** (libkrun) ships despite having no snapshot/restore — explicit
  decision ("great to have, not a must"), not an oversight.

---

## A named platform-wide principle (added after four contracts were already written)

`architecture.md §1a`: **library-first, low coupling / high cohesion** — every
component must work as an independently reusable piece; only the orchestrator
layer (`CivitasBridge`/Civitas) is allowed to be tightly integrated. This was
driving decisions all session without being named (separate `ToolManager`/
`SkillManager`, `prx`/`tessera` staying outside Fabrica, swappable `Protocol`
backends everywhere) until a sidebar about graph-based dynamic memory
compaction (comparing Fabrica's `MemoryManager` to Generative Agents/MemGPT/
MemOS prior art) surfaced it directly: Fabrica's three memory facets
deliberately do NOT share one unified retrieval/retention score, unlike
Generative Agents' single composite formula, specifically because a shared
score would couple `WorkingMemoryStore`/`Compactor`/`MemoryStore` to each
other's internal signals. See `memory.md`'s "Related work and a deliberate
divergence" section for the full reasoning. **Apply this principle explicitly
when writing `PromptManager`/`CivitasBridge`'s contracts** — it's now a named
rule, not something to re-derive.

## Process conventions established this whole session — keep using them

- **Spikes:** lock a specific question + timebox before writing any code
  (`spike-prototype` skill discipline). Code is held in
  `specs/archive/spikes/scripts/`, not deleted, per explicit instruction — findings
  docs are the durable artifact.
- **Diagrams:** hand-crafted SVG only, matching the existing platform palette
  (dark cards `#111827`; blue `#2563EB` Runtime; purple `#7C3AED` Control; amber
  `#D97706` Context). **Always render to PNG and visually inspect before
  finalizing** — this caught multiple real layout bugs (clipped columns,
  overlapping text) that would have shipped silently otherwise.
- **Relative links from `specs/archive/spikes/*.md` back into `docs/` need
  `../../../docs/...`** (three levels up), not `../../docs/...` — this exact
  mistake was made **three separate times** across different spike files
  before being caught by explicit verification. Check this specifically on
  any new file in that directory.
- **git/GitHub:** active `gh` account must be `jerynmathew` (admin on
  `civitas-io`), not `jeryn-fiddler`. The macOS keychain sometimes overrides
  the active-account switch for plain `git push` — if push fails with a
  permission error against the wrong user, use
  `GIT_ASKPASS=<script echoing jerynmathew + token> GIT_TERMINAL_PROMPT=0 git -c credential.helper= push`
  to force the correct credential without it leaking into `.git/config`.
  Always verify `.git/config` has no embedded token after pushing.
- **Every resolution keeps its reasoning trail** — corrections and resolved
  open questions are marked `~~struck through~~` **Resolved** with the
  original text preserved, never deleted. This handoff doc is written in that
  same spirit: don't just say what was decided, say how, so the next session
  can extend the pattern instead of re-deriving it.

---

## `CivitasBridge` design walkthrough — in progress, two decisions settled

Before writing `contracts/civitas-bridge.md`, walked through where `§1a`'s
"only the orchestrator integrates tightly" license actually ends. Two things
settled, both corrections to what `system-design.md` said before:

1. **Construction-time wiring only (v1), a bounded runtime extension possible
   later (deferred, not ruled out).** `CivitasBridge`'s job is assembling the
   object graph once (`build() -> Fabrica`) — never per-turn orchestration
   across managers, which would duplicate Civitas's own runtime loop. Kept
   extensible the same way mode-switching was: `build()` must return every
   manager as a public attribute on `Fabrica`, never hidden exclusively
   behind `CivitasBridge` — that's what would make a later, narrow,
   opt-in, read-only convenience method (never a decision-making one)
   additive rather than a rework.
2. **`CivitasBridge` requests, Civitas performs — corrects a real
   inconsistency, not just adds detail.** `system-design.md` previously said
   `CivitasBridge` "registers GenServers with the supervision tree,"
   implying it reaches into Civitas's internals directly. Wrong, and
   inconsistent with `PresidiumClient`'s own design one paragraph earlier in
   the same doc (`check_grant` asks, Presidium decides). Fixed: `CivitasBridge`
   calls `request_supervision(...)`/`request_state_persistence(...)`; Civitas's
   own runtime decides how to fulfill each request. This also fixed a second,
   previously-unnoticed inconsistency: `PromptManager`/`MemoryManager` were
   described as depending on Civitas's `StateStore` directly, which would have
   quietly created a THIRD place this system talks outward, contradicting
   §1's own claim that there are only two (`CivitasBridge`, `PresidiumClient`).
   Both managers' state persistence is now explicitly mediated through
   `CivitasBridge.request_state_persistence` — fixed in both
   `system-design.md` (component matrix + state table) and `memory.md`
   (Integration section).

**`Summarizer` DI entry point — resolved.** `CivitasBridge.__init__` takes
`summarizer: Summarizer | None = None` — a plain typed keyword parameter, not
a generic dependency-registry bag (that would be premature generalization:
`Summarizer` is the ONLY dependency in this whole system that structurally
cannot have a zero-config default, so there's no real risk yet of
`CivitasBridge`'s constructor bloating with more of these — revisit
generalizing only if a second one actually appears). Optional, not required,
so constructing `Fabrica` never forces every deployment to supply a model
connection just to start up.

This meant `MemoryManager`'s required `Compactor` constructor parameter
needed an answer for `summarizer=None`: **`NullCompactor`**, a Null Object
implementing `Compactor`, wired in by `CivitasBridge` instead of
`RecencyCompactor` when no `Summarizer` was configured. Raises
`CompactionUnavailableError` only when `compact()` is actually invoked —
keeps `MemoryManager`'s constructor simple (always receives a valid
`Compactor`, no `Optional`/`None`-branching at every call site) and answers
"is compaction configured?" once, at construction time, not repeatedly at
every call site. Written into `contracts/memory.md` already, ahead of
`CivitasBridge`'s own contract, since it's `Compactor`/`MemoryManager`'s
behavior being specified, not `CivitasBridge`'s.

**Presidium-absent question — resolved, asymmetrically from `NullCompactor`
on purpose.** `check_grant` is mandatory hot-path control flow (called by
`execute_in_sandbox` on every execution), unlike compaction which a harness
invokes optionally — so `NullPresidiumClient` must ALLOW by default, the
opposite failure direction from `NullCompactor`. To keep that from being a
silent security hole: `presidium_client=None` requires an explicit
`allow_ungoverned=True` or `CivitasBridge` raises `UngovernedConfigurationError`
at construction — forces a visible, greppable decision rather than a default
you fall into by omission.

**DI shape for `presidium_client` — resolved, mirrors `summarizer` exactly.**
A fully-constructed `presidium_client: PresidiumClient | None` object, not a
raw endpoint string — a bare endpoint would have been insufficient anyway
(mTLS needs certs, not just a URL) and would have forced `CivitasBridge`'s
constructor to slowly absorb `PresidiumClient`'s entire configuration surface.
Named as a general rule, not a one-off: **`CivitasBridge` accepts
fully-constructed objects for every external dependency, never raw config it
would translate itself** — applies to any future DI'd dependency by default.

## `CivitasBridge` — DONE. All six object-model contracts complete.

`contracts/civitas-bridge.md` written, incorporating every decision above.

**`CivitasRuntime` reconciliation — done, not left provisional.** Read
`python-civitas`'s actual source directly (`civitas/runtime.py`,
`civitas/genserver.py`, `civitas/plugins/state.py`), not just its docs, and
found the real API differs from the first draft in two concrete ways:

1. There is no "register a supervision spec" call. The real mechanism is
   `Runtime.spawn(supervisor_name, agent_class, name, config, *, wait=True) ->
   str` — dynamically spawning an agent into an **already-existing, named**
   `DynamicSupervisor` that Civitas's own deployment topology defines.
   `CivitasBridge` doesn't create a supervisor or choose a restart strategy;
   it spawns into one Civitas already set up, and needs a new
   `dynamic_supervisor_name` constructor parameter to know which one. Returns
   the spawned agent's name (a string), raises `SpawnError` on failure — not
   an opaque handle.
2. `civitas.plugins.state.StateStore` is far simpler than guessed: keyed by
   `agent_name: str`, storing `dict[str, Any]` directly. Replaced the
   invented byte-oriented `StateHandle` with `ComponentStateHandle` — a thin
   wrapper pre-bound to one component's name over the real `StateStore`, so a
   manager can't accidentally address another component's state.

`CivitasBridge.__init__` now requires `civitas_runtime`, `civitas_state_store`,
AND `dynamic_supervisor_name` together in service mode (`RuntimeRequiredError`
if any is missing) — all three genuinely needed given the real API, not one
combined "runtime" object as first sketched. This is a real correction, not
just filling in the previously-flagged gap with confirmation — the original
guess was wrong in its shape, not just underspecified.

---

## Immediate next action (supersedes any earlier "next action" note above) — HISTORICAL, itself now superseded by "Current state" near the top of this document

*This section's own "supersedes any earlier note" claim is exactly the kind
of drift the new "Current state" section at the top now exists to prevent —
kept here as reasoning-trail detail, not as the place to look for current
status.*

All six object-model contracts are done: `Retriever`, `Sandbox`,
`managers.md` (`PresidiumClient.check_grant`, `execute_in_sandbox`,
`ToolManager`, `SkillManager`), `memory.md`, `prompts.md`, `civitas-bridge.md`.
The design/validate/critique/architecture/system-design/contracts arc that
`README.md`'s reading order describes is now complete end to end.

**A live PyPI naming collision was also found** — the name `fabrica` is
already taken on PyPI by an unrelated third party (a Codex-transport
scaffold, v0.0.7) — neither this repo NOR `civitas-contrib/packages/fabrica`
(a real, separate, code-containing package that also claims `name = "fabrica"`
in its `pyproject.toml`, discovered while investigating this) can ever ship
under that name. Candidate alternates checked available at the time:
`civitas-fabrica`, `fabrica-context`, `pyfabrica`, `fabrica-ctx`,
`civitas-context`, `fabrica-agent`, `fabricapy`.
**Resolved later, after a more thorough second pass (18 of 23 candidates
checked across PyPI + both Homebrew registries): `fabrica-context`** — see
"Current state" at the top of this document for the full reasoning.

**`civitas-contrib`'s three stale Fabrica documents are still unfixed** —
`docs/design/fabrica.md`, `packages/fabrica/README.md`,
`rfcs/0001-tool-retrieval.md` all describe the old, narrower `find_tools`-only
version of Fabrica (framing `find_tools` as the solution, the exact reverse of
this project's actual conclusion that code-mode is the headline and `find()`
is the fallback), with nothing anywhere pointing to `civitas-io/fabrica`.
Fixing this was paused specifically because the naming collision means a
"here's the real repo" pointer can't yet say what to `pip install`.
**`packages/fabrica`'s real code — resolved: migrate, don't archive.**
Designed [mcp-integration.md](docs/mcp-integration.md): the old `MCPClient`/
`BubblewrapSandbox` code (~500 lines) becomes the validated implementation
behind a new `MCPToolNamespace` — one more `ToolNamespace` implementation,
zero changes needed to `ToolManager`/`SandboxPool`/`execute_in_sandbox`.
Explicitly reconciled against `landscape.md §2`'s "do not build an MCP
gateway" decision: this is an internal adapter for Fabrica's own
tool-execution pipeline (inherits Presidium governance for free via the
existing `execute_in_sandbox` grant check), not a standalone gateway product
with its own discovery/aggregation/governance — the thing that's rejected.
Also distinguished from a second, already-noted direction in
`tool-execution.md` (Fabrica *as* an MCP server) — this is the client
direction, deliberately designed separately. The `bwrap` sandboxing stays
internal to the adapter, never routed through `SandboxPool`'s tier system
(different lifecycle shape: persistent connection vs. ephemeral execution).
Only the old package's stale `find_tools`-only *docs* get retired — the code
migrates.

What's left, roughly in order of how blocking each is:

1. ~~Resolve the PyPI naming collision~~ **Resolved: `fabrica-context`** —
   see "Current state" at the top of this document.
2. **Fix the three stale `civitas-contrib` documents** — now unblocked,
   mark superseded, point at `civitas-io/fabrica` and its `pip install
   fabrica-context` name, note the MCP code's migration destination
   specifically (not just "see the new repo").
3. **Actually perform the `MCPClient` code migration + the `srt` rewrite**
   into `civitas-io/fabrica`, once real code-writing begins there —
   `BubblewrapSandbox` specifically does NOT migrate as-is (resolved in
   `mcp-integration.md`: replaced by cross-platform `srt`, not carried over
   Linux-only).
4. **No code exists yet anywhere in `civitas-io/fabrica`.** Scaffolding the
   actual `fabrica/` Python package (`pyproject.toml`, source layout) is the
   next phase-level step — `CivitasRuntime` is now resolved, so this is no
   longer blocked on that.
5. Everything in "What's explicitly left open elsewhere" above — still
   accurate, nothing there has been resolved by the contracts work.
6. `mcp-integration.md`'s open questions — **four of five resolved** through
   direct walkthrough (isolation mechanism -> `srt`; connection lifecycle ->
   eager, forced by `ToolManager.register()`'s contract; multi-server ->
   already sufficient; cache invalidation -> split into a hard guarantee that
   ships now and a soft one blocked on `Retriever`'s own open item). One
   genuinely still open: zero spike coverage for `MCPToolNamespace`
   end-to-end. A new platform-wide rule surfaced along the way, third
   instance of it now: **when a security/governance mechanism is
   unavailable, fail closed by default; the exception is always an
   explicit, greppable opt-in flag, never a silent fallback** (`allow_ungoverned`,
   now `allow_unsandboxed`).
7. `prompts.md`'s "Explored" survey has two items marked worth prioritizing
   that got promoted into the contract (cache-boundary, `PROMPT.md` format)
   and several marked correctly deferred — no action needed unless demand
   surfaces.
8. `RecencyCompactor`'s core mechanism is now spiked (see the arc section
   above) — the strategy is validated; `preserve_last_n=6` specifically is
   not. Next-level spike, if ever prioritized: vary N against a real
   token-budget boundary, not just the fixed window this spike used.
9. **New: `mcp-server.md` written** — Fabrica as an MCP server, the
   complementary opposite of `mcp-integration.md`'s client direction. Closes a
   claim `tool-execution.md` made and never designed. Key finding: the
   two-path token-efficiency thesis has to be preserved for EXTERNAL MCP
   clients too, not just internal models — exposing every registered
   tool/skill as its own flat MCP tool would reintroduce the exact O(N)
   schema-dump cost this project exists to eliminate, just shifted onto
   whoever calls in from outside. Resolved: five fixed, generic MCP tools
   (`fabrica_find`/`fabrica_run_code`/`fabrica_run_skill`/memory ops) plus
   `PromptManager` mapped directly onto MCP's native Prompts primitive.
   Bearer-token auth for HTTP/SSE (deliberately NOT mTLS, unlike
   `PresidiumClient` — different topology, many diverse third-party clients
   vs. one controlled service). A NEW connection-level auth layer sits in
   front of, not parallel to, existing per-call governance (`check_grant`
   reused unchanged once a connection resolves to an `agent_id`).
   `allow_weak_isolation_for_external_callers` is the FOURTH confirmed
   instance of the fail-closed-by-default-explicit-opt-in-to-bypass rule.
   Zero spike coverage, same honest flag as everywhere else this applies.

## Two more contracts written: `mcp-integration.md`, `mcp-server.md` — reasoning-trail detail; see "Current state" at the top for status

`docs/contracts/mcp-integration.md` (`MCPClient`/`MCPToolNamespace`) and
`docs/contracts/mcp-server.md` (`FabricaMCPServer`) are both done, formalizing
the two MCP-direction design docs. Two real findings from writing exact
signatures, not just transcription:

1. **A genuine implementation wrinkle in the client contract**: eager
   connection (required by `ToolManager.register()`'s existing contract)
   needs to happen in `__init__`, but `connect()` is a coroutine and Python
   constructors can't be `async`. Needs an async factory
   (`await MCPToolNamespace.create(client)`) or equivalent — not resolved,
   flagged honestly as something the design doc's sketch glossed over.
2. **A real gap in `contracts/prompts.md`, found and closed immediately**:
   writing `FabricaMCPServer`'s `prompts/list` handler required enumerating
   *all* registered prompt names, and `PromptStore`/`PromptManager` only had
   `list_versions(name)` — versions of a name already known. Added
   `list_names() -> list[str]` to both directly, rather than left dangling.

Eight contracts total now across the object model + MCP integration.
Remaining named debt: `mcp-server.md`'s `WeakIsolationError` only checks once
at construction (no live re-check if a service-mode deployment's tier changes
later), and multi-tenant HTTP deployments aren't stress-tested against shared
`SandboxPool`/`Retriever` state. Zero spike coverage for both, unchanged from
the design docs.
