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

Repo: `civitas-io/fabrica` (public). Currently pure design/docs — **no code
exists yet**, by deliberate sequencing (design → validate → critique →
architecture → system design → contracts → *then* implementation).

---

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

## Immediate next action (supersedes any earlier "next action" note above)

All six object-model contracts are done: `Retriever`, `Sandbox`,
`managers.md` (`PresidiumClient.check_grant`, `execute_in_sandbox`,
`ToolManager`, `SkillManager`), `memory.md`, `prompts.md`, `civitas-bridge.md`.
The design/validate/critique/architecture/system-design/contracts arc that
`README.md`'s reading order describes is now complete end to end.

**A live PyPI naming collision was also found and is unresolved**: the name
`fabrica` is already taken on PyPI by an unrelated third party (a Codex-transport
scaffold, v0.0.7) — neither this repo NOR `civitas-contrib/packages/fabrica`
(a real, separate, code-containing package that also claims `name = "fabrica"`
in its `pyproject.toml`, discovered while investigating this) can ever ship
under that name. Candidate alternates already checked available:
`civitas-fabrica`, `fabrica-context`, `pyfabrica`, `fabrica-ctx`,
`civitas-context`, `fabrica-agent`, `fabricapy`. Not decided which, if any.

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

1. **Resolve the PyPI naming collision** — blocks writing an accurate
   pointer in the stale `civitas-contrib` docs, and blocks ever actually
   publishing this package.
2. **Fix the three stale `civitas-contrib` documents**, once naming is
   settled — mark superseded, point at `civitas-io/fabrica`, note the MCP
   code's migration destination specifically (not just "see the new repo").
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
