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
4. **Validate** — 8 spikes in `specs/archive/spikes/`, all real hardware/API
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
   - **Not yet written:** `MemoryManager` (design reframed in `docs/memory.md`,
     contract still to write), `PromptManager`, `CivitasBridge`.

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

**Next:** write `docs/contracts/memory.md` against this updated design — not
yet done as of this handoff revision.

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

## Immediate next action

Write `docs/contracts/memory.md` against the three-facet design now in
`docs/memory.md` — `PresidiumClient.check_grant`/`execute_in_sandbox`-level rigor:
exact `WorkingMemoryStore`/`Compactor`/`Summarizer`/`MemoryManager` signatures,
error types, and what's deliberately left out (the `Message`-type reconciliation
with Civitas's own runtime loop is real integration work, not a contract-level
decision). Then `PromptManager`, then `CivitasBridge`.
