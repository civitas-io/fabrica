# Personas & Jobs-to-be-Done

**Status:** Discovery · **Last updated:** 2026-08

---

## Why personas + JTBD, not demographics

Fabrica is developer infrastructure. Nobody "buys a persona" — they hire the product
to do a job. Each persona below is a **role**, paired with the job they're hiring
Fabrica to do. One nuance specific to this product category: **the model itself is a
primary consumer** of several Fabrica interfaces (`find_tools`, the code-mode
namespace, skill discovery). It doesn't configure or adopt anything, but its behavior
constrains API design — it must self-serve from descriptions alone, tolerate "no
match," and need no SDK-specific glue. Tracked separately from the human personas.

---

## Human personas

### 1. Priya — Agent Builder

- **Role:** Python developer building agents on Civitas.
- **JTBD:** *"When I add a capability to my agent, I want it to just work without me
  hand-managing token budgets, so I can ship without babysitting the context window."*
- **Touches:** `fabrica` library mode — tools-as-code, `find_tools` fallback, skills,
  memory. Wants zero-infra defaults (`pip install fabrica` and go).
- **Fear:** infrastructure ceremony getting in the way of shipping.

### 2. Marcus — Platform / Production Engineer

- **Role:** Owns the production deployment; SRE-minded.
- **JTBD:** *"When agents run model-generated code in prod, I want provable isolation
  and a scalable sandbox pool, so a bad run can't touch the host or blow the budget."*
- **Touches:** `Sandbox` protocol, Firecracker tier, warm pools, OTEL spans, service
  mode.
- **Fear:** an escape from a "sandbox" that wasn't actually one; an unbounded cost run.

### 3. Elena — Security / Governance Engineer

- **Role:** Defines and audits what agents may touch (Presidium-adjacent).
- **JTBD:** *"When an agent calls a tool or executes generated code, I want to control
  and audit exactly what it can reach, so I can prove compliance without slowing
  builders down."*
- **Touches:** grants/policy seams into Presidium, credential injection into the
  sandbox, audit events.
- **Fear:** a secret leaking into the model's context window or a log.

### 4. Devon — Skill / Tool Author

- **Role:** Packages a capability for reuse across agents/teams (may be internal or
  third-party).
- **JTBD:** *"When I want my capability adopted, I want a standard packaging format
  with clear discovery, so it gets used correctly and only when relevant."*
- **Touches:** `SKILL.md` conformance, `ToolSource` protocol, versioning.
- **Fear:** publishing a skill that's never discovered, or discovered wrong.

### 5. Alicia — Engineering Decision Maker

- **Role:** Staff/Principal architect or VP Eng evaluating build vs. buy.
- **JTBD:** *"When choosing agent infrastructure, I want an open, standards-aligned
  layer not locked to one model vendor, so we keep flexibility as the ecosystem
  shifts."*
- **Touches:** the vendor-neutral pitch as a whole — interface-first packages,
  `SKILL.md` conformance, portability vs. Anthropic/Cloudflare-locked tooling.
- **Fear:** betting the roadmap on a vendor-specific feature that gets deprecated or
  never ported.

## Non-human actor

### The Model — the LLM inside the agent

- **Consumes directly:** `find_tools`, the code-mode namespace/stubs, the skill index.
- **Constraint it imposes:** every interface must be self-descriptive from names +
  descriptions alone (progressive disclosure), must degrade gracefully on "no match,"
  and must require no model-specific SDK glue to use.
- Not a buyer or configurer — but its behavior is a hard design constraint on
  everything the human personas build with.

---

## The central tension (design already anticipated it)

Priya wants **zero-config simplicity**. Marcus and Elena want **hardened, auditable,
tiered isolation**. These pull in opposite directions — which is exactly why the
`Sandbox` protocol in [isolation.md](isolation.md) is tiered (subprocess → gVisor →
Firecracker → Kata) rather than one-size-fits-all: Priya gets Tier 0 for free in dev;
Marcus/Elena upgrade to Tier 2 for prod without Priya's code changing.

---

## Prioritization signal for the roadmap

Whoever's job we solve *first* determines what P1 actually is. Candidates, ranked by
"blocks everyone else if unsolved":

1. **Priya** — if the library isn't trivially adoptable, nobody else's persona matters
   (no agents built on it to secure or govern).
2. **Marcus** — production isolation is Fabrica's stated differentiator; without it,
   the product is "yet another find_tools wrapper."
3. **Elena** — governance seams can lag slightly since Presidium already covers policy
   at the platform level; Fabrica's job is to expose the hooks, not lead with them.
4. **Devon** — skills are the "fast-follow" bet (rides the `SKILL.md` wave) but not
   blocking.
5. **Alicia** — influenced by all of the above being genuinely true, not a separate
   build track.

This suggests **P1 = Priya's job done well** (tools-as-code + `find_tools` fallback,
trivially easy), with **Marcus's isolation tiers as P2** arriving before any real
production claim is made — matching the phase order already sketched in the README.
