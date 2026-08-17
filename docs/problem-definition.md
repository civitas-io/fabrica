# Problem Definition

**Status:** Define phase, complete · **Last updated:** 2026-08
**Precedes:** [personas.md](personas.md) (Discovery) · **Feeds:** architecture docs
(context-layer.md, tool-execution.md, isolation.md, skills-gateway.md, memory.md)

---

## How to read this doc

Each persona from [personas.md](personas.md) gets a **problem statement** (specific,
falsifiable — should point at an exact moment the pain occurs), **success metrics**
(how we'd know it's solved), and **non-goals** (what this slice explicitly does not
solve). Order follows the prioritization signal from personas.md: whoever's job
blocks everyone else if unsolved goes first.

```
1. Priya   — Agent Builder                — P1 gate (nobody else matters without agents built on this)
2. Marcus  — Platform/Production Engineer — P2 gate (the stated differentiator; without it it's "just retrieval, no execution")
3. Elena   — Security/Governance Engineer — governance seams, can lag slightly (Presidium covers policy)
4. Devon   — Skill/Tool Author            — fast-follow, rides the SKILL.md wave
5. Alicia  — Engineering Decision Maker   — aggregator persona, not new build scope
```

---

## 1. Priya — Agent Builder

**JTBD:** *When I add a capability to my agent, I want it to just work without me
hand-managing token budgets, so I can ship without babysitting the context window.*

**Problem statement**

> Today, adding a tool to a Civitas agent means hand-authoring a JSON schema, wiring
> it into the LLM call's `tools` list, and — with no library help — watching schema
> tokens grow linearly and untracked as the tool count rises. There is no primitive
> that lets a developer add a capability and have retrieval, execution, and context
> budget handled automatically. The pain is invisible at 5 tools and acute at 50.

**Success metrics**

| Metric | Target |
|---|---|
| Time-to-first-tool-call | `pip install fabrica` \u2192 first working tool call in **< 5 minutes**, no external services |
| Schema-token overhead is flat, not linear in N | bounded overhead per call whether 5 or 500 tools are registered |
| Zero required infra for hello-world | Tier-0 sandbox + in-memory index, no Docker/DB/Firecracker to try it |

**Non-goals**

- Production-grade isolation (Marcus's job, P2 — Priya gets Tier 0 free, upgrades later with no code changes)
- Cross-agent/cross-team tool governance (Elena's job, Presidium-side)
- A management UI/dashboard for tool inventories
- Optimizing for enterprise-scale registries (1000s of tools) in v1

---

## 2. Marcus — Platform / Production Engineer

**JTBD:** *When agents run model-generated code in prod, I want provable isolation
and a scalable sandbox pool, so a bad run can't touch the host or blow the budget.*

**Problem statement**

> Today, running model-generated or tool-invoked code from an LLM agent in
> production means either trusting it in-process (no isolation) or standing up
> bespoke sandboxing infrastructure per team. There is no drop-in, tiered isolation
> primitive a Civitas-based agent can adopt without redesigning its execution path.
> The risk is invisible until an escape, a runaway resource consumer, or a leaked
> credential happens — at which point it's an incident, not a design review.

**Success metrics**

| Metric | Target |
|---|---|
| Zero-code-change tier upgrade | Tier 0 \u2192 Tier 1 (gVisor) \u2192 Tier 2 (Firecracker) is a **config change only** |
| Warm-pool restore latency | p99 in **single-digit ms** from a Firecracker snapshot |
| Zero credential leakage | **Resolved by decision, not by hardening an injection path** -- see [credentials.md](credentials.md): `Sandbox` gets no credential-injection mechanism at all, so there is no leakage path through the sandbox boundary to defend in the first place. Validated end to end with a real, independently-built credential broker (Tessera) in [SPIKE-tessera-credential-integration.md](../specs/archive/spikes/SPIKE-tessera-credential-integration.md). |
| Self-healing pool | a crashed sandbox-pool process restarts automatically under Civitas supervision |

**Non-goals**

- No custom hypervisor work — wrap Firecracker/gVisor/Kata, never reimplement isolation primitives
- No multi-region/global pool orchestration in v1 — single-cluster first
- No hosted managed-sandbox SaaS (see [tool-execution.md](tool-execution.md) — build/maintain, don't operate)
- GPU-in-sandbox — deferred (open question in [isolation.md](isolation.md))

---

## 3. Elena — Security / Governance Engineer

**JTBD:** *When an agent calls a tool or executes generated code, I want to control
and audit exactly what it can reach — and how much of it — so I can prove compliance
without slowing builders down.* (widened to include consumption, not just reach —
see the usage/budget note below)

**Problem statement**

> Today, once an agent is running, there's no single place to see or bound what it
> actually touched — which tools it called, what code it executed, what memory it
> read, and how much of each it consumed against a team's budget. Grants exist as
> config, but compliance requires proving what *happened*, not just what was
> *allowed*. Without emitted, structured events at every execution point, an audit
> is a forensic reconstruction, not a query.

**Success metrics**

| Metric | Target |
|---|---|
| Every execution point emits an event | 100% of tool calls, sandbox runs, memory ops, skill invocations produce a structured audit + usage event |
| Grant/budget denial happens before execution | 0 cases of a denied action executing before the deny is processed |
| No secret ever appears in an event payload | automated redaction test on every emitted event |

**Non-goals**

- Fabrica does not implement the policy engine, grant model, or usage ledger itself — it emits events and enforces pre-flight checks Presidium returns (see [civitas-presidium-integration.md](civitas-presidium-integration.md#usage--budget-ceilings--metering-vs-enforcement))
- No compliance dashboard/reporting UI — that's `presidium-audit`'s job, consuming these events
- Fabrica doesn't decide *what* is compliant — only that every reachable action is observable
- **Log tamper-evidence (signatures, hash-chaining) — deferred, not dropped.** Ship the emission pipeline working end-to-end first; add integrity guarantees once there's a real log to protect.

---

## 4. Devon — Skill / Tool Author

**JTBD:** *When I want my capability adopted, I want a standard packaging format
with clear discovery, so it gets used correctly and only when relevant.*

**Problem statement**

> Today, packaging a capability for reuse means either hand-rolling a bespoke
> integration per consuming framework, or hoping a description string is enough for
> an LLM to find it among dozens of others. `SKILL.md` is emerging as the portable
> standard, but no Python agent runtime loads it natively — authors either write for
> Claude-specific tooling or roll their own loader per project. The result: a skill
> gets authored once and used inconsistently, or never surfaces when it's actually
> relevant.

**Success metrics**

| Metric | Target |
|---|---|
| Write once, run anywhere | a `SKILL.md` package loads unmodified across any Fabrica-based agent |
| Discovery precision | correct skill surfaces in top-3 candidates for a benchmark task set |
| Flat index cost | loading the skill index (names + descriptions only) stays token-bounded regardless of skill count — achieved via the shared `find` primitive in [retrieval.md](retrieval.md), not the original per-skill-index design (correction logged in [critique.md](critique.md)) |
| Time to first invocation | a new `SKILL.md` is loadable and callable in minutes, no custom glue code |

**Non-goals**

- No new skill format — conform to `SKILL.md`, full stop
- No skill marketplace/registry UI in v1
- No skill versioning/dependency resolution in v1 — flagged as a future concern
- **Third-party skill trust/signing — deferred, not dropped.** Same posture as Elena's log-tamper deferral: get discovery + loading working first, add provenance once there's a real ecosystem to protect.
- **Disambiguation among overlapping skills is real but explicitly not solved here.** It's the same failure mode as tool-selection accuracy degrading past ~20\u201330 candidates — elevated to a cross-cutting design question spanning tools, skills, and memory search, not a skills-only fix. See [skills-gateway.md](skills-gateway.md#open-questions).

---

## 5. Alicia — Engineering Decision Maker

**JTBD:** *When choosing agent infrastructure, I want an open, standards-aligned
layer not locked to one model vendor, so we keep flexibility as the ecosystem
shifts.*

**Structural note:** Alicia doesn't generate new build scope. She's the **aggregator
persona** — her job is satisfied entirely by personas 1\u20134's slices being genuinely
true, plus evidence that they are. Solving her with new features would be solving a
marketing problem with engineering effort.

**Problem statement**

> Teams evaluating agent infrastructure face a false choice: adopt vendor-native
> features (fast-moving, but locked to one model provider's roadmap) or build
> bespoke glue in-house (flexible, but a maintenance tax reinventing solved
> problems). There's no standards-aligned layer delivering tool retrieval, sandboxed
> execution, skills, and memory without betting the roadmap on one vendor, or paying
> indefinitely to DIY it.

**Success metrics (evidence artifacts, not new features)**

| Metric | Target |
|---|---|
| Model-provider portability | swapping the underlying LLM requires **zero changes** to tool/skill/memory code |
| Migration cost from vendor lock-in | a real agent hand-wired to one model's native tool-search ports to Fabrica with a measurable before/after (tokens, LOC) |
| Standards-compliance checklist | Fabrica conforms to `SKILL.md` and other open standards wherever one exists; zero proprietary formats invented needlessly |

**Non-goals**

- No dedicated marketing/distribution engine — satisfied by personas 1\u20134's artifacts
- No new build scope beyond what Priya/Marcus/Elena/Devon already require

---

## Cross-cutting decisions made during Define

These came up while defining individual personas but apply platform-wide. Each is
already reflected in the relevant design doc; listed here so the reasoning trail
isn't lost.

| Decision | Where it's captured |
|---|---|
| Build the `find` interface/aggregation layer (real gap); wrap Tier-1 embedding engines (LlamaIndex/LangChain, later prx) rather than reimplementing retrieval; never operate it as a hosted multi-tenant service | [tool-execution.md](tool-execution.md), [retrieval.md](retrieval.md), [landscape.md](landscape.md#6-tool-search--retrieval-backends--a-two-tier-market) |
| `find`/tools-as-code lives in **Fabrica**, not the Rust toolchain (prx/tessera) — different consumer (the model, mid-inference) and governance/supervision needs that the toolchain doesn't have | [tool-execution.md](tool-execution.md#why-this-lives-in-fabrica-not-the-rust-toolchain-prxtessera) |
| Usage/budget ceilings split into **metering** (Fabrica emits) vs **enforcement** (Presidium decides) — not a fourth system, an extension of Presidium's already-claimed cost-tracking scope | [civitas-presidium-integration.md](civitas-presidium-integration.md#usage--budget-ceilings--metering-vs-enforcement) |
| `Scope` gains `team_id`, shared between `MemoryStore` and the usage ledger | [memory.md](memory.md), [civitas-presidium-integration.md](civitas-presidium-integration.md) |
| Disambiguation among overlapping tools/skills is **resolved by unification** (one `Retriever` engine, one `find(query, kind)` surface), not deferred — memory search intentionally kept separate (different semantics) but shares the same engine | [retrieval.md](retrieval.md) |
| Two **deferred-not-dropped** items on the same footing: log tamper-evidence (Elena) and skill trust/signing (Devon) — both supply-chain-adjacent, both wait until the base feature works | this doc, \u00a73 and \u00a74 |
| **Post-Define, surfaced during Validate/Critique:** code-mode (the actual headline, not just the `find` fallback) is empirically validated — lower token cost *and* better correctness than direct tool-calling | [SPIKE-code-mode-execution.md](../specs/archive/spikes/SPIKE-code-mode-execution.md), [critique.md](critique.md) |
| **Post-Define:** isolation backend is platform-dispatched (auto-detected per host OS) and deliberately hidden from users — a stated exception to how Civitas normally exposes config | [isolation.md](isolation.md), [critique.md](critique.md) |
| **Post-Define:** engineering principle — where Fabrica *builds* compute internals, Rust with a Python binding, not pure Python; wrapped libraries (Mem0, prx, LlamaIndex) are unaffected | [context-layer.md](context-layer.md#engineering-principle-rust-for-compute-python-for-interface) |

---

## What's next

**Update — this section is stale in its original form and kept for the historical
record.** At the time this was written, Validate hadn't started. It has since
happened: seven spikes tested exactly the riskiest claims here, including Priya's
P1 slice (tools-as-code + `find` fallback — both validated,
[SPIKE-code-mode-execution.md](../specs/archive/spikes/SPIKE-code-mode-execution.md))
and Marcus's P2 slice (Firecracker restore latency, real hardware,
[SPIKE-firecracker-boot-restore-latency.md](../specs/archive/spikes/SPIKE-firecracker-boot-restore-latency.md)).
Full results, corrections, and remaining open decisions are in
[critique.md](critique.md), not repeated here. Next real step now: `plan-work`.

Original text below, preserved:

Per the lifecycle: **Design** is already partially drafted (context-layer.md,
tool-execution.md, isolation.md, skills-gateway.md, memory.md) — this Define pass
was partly a validation of that earlier work, and it held up (e.g. the tiered
`Sandbox` protocol directly resolves the Priya-vs-Marcus/Elena tension named in
personas.md).

*(Original next-step line preserved for the record, superseded by the update at the
top of this section: "Next real step: Validate — a spike against Priya's actual
workflow... to test the success metrics above against reality before locking the
build roadmap." That validation is now done — see the update above.)*
