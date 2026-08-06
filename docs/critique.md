# Critique: Design vs. Evidence

**Status:** Review · **Last updated:** 2026-08
**Method:** every claim in the design docs (`context-layer.md`, `tool-execution.md`,
`isolation.md`, `skills-gateway.md`, `memory.md`, `civitas-presidium-integration.md`,
`landscape.md`, `problem-definition.md`) checked against the six spikes in
`specs/archive/spikes/`. Corrections below are proposals, **not yet applied** to the
docs they reference — this is the critique pass itself.

---

## Executive summary

The thesis holds — code-mode's token-savings argument, the platform-primitives
differentiation, and the interface-first package split all survive contact with real
measurement. But **the single most important unresolved gap isn't a wrong number —
it's that the actual headline mechanism was never tested.** Six spikes went deep on
the fallback (`find_tools` retrieval) and the isolation substrate, and zero went near
"model writes code against a `ToolNamespace`, runs it in a sandbox, gets a result
back" — the thing `tool-execution.md` explicitly calls the pitch, not the fallback.
That should be resolved or explicitly re-scoped before writing an implementation plan,
not discovered during it.

Below: (A) concrete doc corrections evidence demands, (B) the code-mode gap in detail,
(C) open architecture decisions the spikes surfaced but didn't resolve, (D) what's
actually solid and doesn't need more proof, (E) a phase-by-phase readiness read
against the original P0–P4 plan.

---

## A. Corrections the evidence demands

Each row: the doc's current claim, what the spike found, and the fix.

| # | Doc / claim | Spike evidence | Fix needed |
|---|---|---|---|
| A1 | `isolation.md` tier table: "Firecracker \| 125 ms boot" | Real measurement: `InstanceStart`→VMM-`Running` is **10.5ms**; →actual usable userspace (full Ubuntu) is **1,055ms**. The "125ms" figure conflates two different readiness signals and assumes a minimal image nobody has built. | Split the claim into VMM-ready vs. userspace-ready; note the minimal-image assumption explicitly. |
| A2 | `isolation.md` warm-pool section: silent on snapshot creation | Snapshot *creation* took **~807ms** for a 512MiB guest — a real, one-time pool-initialization cost, currently invisible in the doc. | Add creation cost to the warm-pool design as a distinct, budgeted cost. |
| A3 | `isolation.md`: no mention that a minimal rootfs/init is separate work | Full-Ubuntu boot-to-usable was 1s+; production-grade ~125ms needs a purpose-built minimal image, which doesn't exist and isn't scoped anywhere. | Name "build a minimal rootfs/init" as its own work item, not an assumed side-effect of "use Firecracker." |
| A4 | `isolation.md` tier table has no platform dimension; reads as OS-agnostic | gVisor **and** Firecracker are Linux-only. `srt` (real numbers: p50 152ms) is the macOS Tier-1 candidate; `libkrun`/`krunvm` is the macOS Tier-2 candidate **with no snapshot/restore at all**; Windows has no validated Tier-1 candidate. | Reframe the whole tier table as platform-dispatched, not "the" implementation. Carry the macOS numbers and the Windows gap in explicitly, not as a footnote. |
| A5 | `tool-execution.md`'s "vendor-neutral... hardenable to microVMs" framed as a general platform claim | True and validated on Linux only. macOS: Tier 1 works but ~50% slower than Linux gVisor and has no restore-based warm pool at Tier 2. Windows: unvalidated, real gap. | Scope the differentiator claim honestly per platform rather than implying uniform readiness. |
| A6 | `problem-definition.md` Devon metric: "Flat index cost... stays token-bounded regardless of skill count" | Measured: index cost grew **linearly** (1,478→6,248 tokens, N=10→81) because the current `SkillStore.index()` design puts the whole index in the model's own context — no server-side search step like `find_tools` has. Not flat. | Either correct the metric to "linear with a small constant" honestly, or add a `find_skills(query)` search step to make the O(1) claim true. **This is an open decision, not a wording fix** — see C1 below. |
| A7 | `tool-execution.md` "build vs. wrap" backend list names LlamaIndex/LangChain only | `prx`'s off-the-shelf, code-tuned embedding model hit **100% precision@3** on a 12-tool disambiguation benchmark with zero fine-tuning — same-org, zero-dependency, Apache 2.0. | Add `prx` as a named candidate backend. |
| A8 | No doc states a rank-vs-threshold requirement for retrieval backends | Every embedding score observed (prx: 0.01–0.04; correct hits and near-misses both landed in this band) was low in absolute terms but reliable in **rank**. A threshold rule would have silently discarded every correct answer. | Add "select by rank, never absolute threshold" as a hard interface requirement wherever retrieval backends are specified. |
| A9 | No doc mentions tool-identity stability | Spike 1's turn-2 cost drifted slightly because the matcher could flip between near-duplicate tool variants for the same capability as N grew. | Add a stable/deterministic tool-identity requirement to the `ToolSchema`/matcher design. |
| A10 | No doc states the prx integration shape | Fresh subprocess per call: p50 74.9ms. Persistent process (`prx mcp`, Civitas-supervised): p50 37.0ms, and the *true* cold-start cost (~260ms) is paid once, not per call. | If prx is adopted, state explicitly: persistent supervised child, not subprocess-per-call. |
| A11 | `landscape.md §3a` names "sandbox-exec / Seatbelt" generically for macOS Tier 1 | The concrete, validated candidate is Anthropic's own `srt`, built on `sandbox-exec`, with real measured overhead and a **Windows mode** (`srt-sandbox` user + WFP filters) that's a plausible fix for the previously-flagged Windows Tier-1 gap. | Update `landscape.md §3a` and `isolation.md` to name `srt` specifically, with numbers, and flag its Windows mode as the leading unresolved lead. |

---

## B. The gap that matters more than any single number

> **UPDATE:** resolved by [SPIKE-code-mode-execution.md](../specs/archive/spikes/SPIKE-code-mode-execution.md),
> run immediately after this critique was written. The mechanism works: 3/3 runs
> produced lower token cost (~79% reduction, consistent) **and** the traditional
> approach was wrong in 3/3 runs (errors of 16–48%, once even miscounting the file
> set) while code-mode was exactly correct in 3/3. The section below is kept as
> written to preserve the reasoning trail — it correctly identified the risk before
> it was closed.

`tool-execution.md` is explicit: *"code-mode... is Fabrica's headline. `find_tools`
retrieval... is a compatibility floor, not the pitch."* Every tool-related spike this
round tested the floor, not the pitch:

- **Spike 1** measured `find_tools` token overhead — retrieval, not execution.
- **Spike 4 + the prx detour** measured retrieval *quality* — still retrieval.
- **Spike 5** measured prx invocation latency — infrastructure for retrieval.

**None tested a model actually writing code against a `ToolNamespace`, executing it
in a `Sandbox`, and returning a result** — the exact mechanism in
`tool-execution.md`'s "Headline: tools-as-code + sandboxed execution" section, and
the thing the ~98.7% token-reduction number (Anthropic + Cloudflare, cited in
`landscape.md §1`) is actually about. The isolation spikes (Firecracker, srt) proved
the *substrate* works; they never ran generated code against a tool namespace inside
it.

This isn't a knock on the spikes done — token-flatness and disambiguation were the
right things to de-risk first, and they turned out to matter (A6, A7, A8, A9 all came
from exactly this work). But **the differentiated part of the product is currently
the least-validated part of it.** Before an implementation plan gets written, this
should be named as a decision point: either (a) run one more spike specifically on
code-mode execution end-to-end before planning, or (b) explicitly accept the risk and
plan P1 knowing the headline mechanism is unproven, with the fallback as the only
de-risked path.

---

## C. Open architecture decisions the spikes surfaced but didn't resolve

These aren't implementation specifics (which can wait, per your own call) — they're
decisions that change scope or interfaces, which is why they belong in this critique
rather than deferred silently.

1. **Skills: accept linear-with-small-constant, or build `find_skills`?** (from A6)
   The fix isn't obvious — a `find_skills` search step makes skills and tools
   architecturally symmetric but adds real complexity for what might be an
   unnecessary optimization at realistic catalog sizes (81 skills cost ~6.2k tokens
   either way — cheap in absolute terms). This needs a decision, not a spike.
2. **Cross-platform Tier dispatch is a real architecture change, not a caveat.**
   `isolation.md`'s `Sandbox` protocol needs to actually dispatch by platform
   (A4) — this changes the shape of how a backend gets selected, not just what's
   documented.
3. **The Windows Tier-1 gap may already be solved (srt's Windows mode) — but nobody
   has looked.** Cheap to check, currently just sitting there as an assumption.
4. **libkrun's permanent lack of snapshot/restore means macOS Tier 2 has a
   structurally different performance ceiling than Linux Tier 2.** Is a cold-boot-only
   macOS Tier 2 even worth shipping, or should macOS stop at Tier 1 (`srt`) until/
   unless Apple's Containerization framework (untested) closes the gap?
5. **`code-mode` vs. `find_tools` prioritization** (from B) — does P1 ship both, or
   does the fallback ship first and code-mode becomes its own phase?

---

## D. What's solid — doesn't need more proof before planning

Worth stating plainly so the critique doesn't read as "everything is uncertain":

- **The core token-flatness thesis is real, not cited.** Static tool schemas scale
  linearly (measured: +5,752 tokens, N=5→50); `find_tools` stays near-flat
  (+105 tokens over the same range). Same pattern held for skills, just with a
  different constant (A6 is about *how* flat, not *whether* it helps).
- **Embedding-based disambiguation is necessary, not optional**, and this held on
  both synthetic (Spike 4) and real (Spike 6, the bigpowers catalog) data — 100%
  precision@3 vs. 67% for naive keyword matching, with the gap concentrated exactly
  on paraphrased queries as predicted.
- **Firecracker restore latency is genuinely fast** — 8.1–10.7ms across 5 trials on
  real bare-metal hardware, closely matching cited literature. This is the number
  Marcus's whole warm-pool design leans on, and it's now measured, not assumed.
- **`srt` is a real, working, fast-to-adopt Tier 1 on macOS today** — slower than
  Linux gVisor, but functional, with real enforcement confirmed (write/network
  denial, sensible default read posture).
- **The package-boundary decisions** (prx/tessera stay separate; no generic MCP
  gateway; wrap memory, don't build it; build the tool-retrieval interface but wrap
  its embedding backend) were never contradicted by any spike — they held up as
  sound scoping calls throughout.

## E. Phase-by-phase readiness vs. the original P0–P4 plan

| Phase | Original scope | Readiness after 6 spikes |
|---|---|---|
| P0 — Thesis | docs + personas + problem definition | Done; this critique is part of closing it out. |
| P1 — Tools | `find_tools` fallback + tools-as-code namespace | **Fallback well-validated. Headline mechanism (code-mode execution) untested — see §B.** |
| P2 — Isolation | `Sandbox` protocol; Firecracker backend + warm pools | Linux: strong (real numbers, real hardware). macOS: partial (Tier 1 real, Tier 2 structurally weaker). Windows: research-only, unvalidated. |
| P3 — Skills | `SKILL.md`-conformant gateway with progressive disclosure | Retrieval/disambiguation validated on real data. **O(1)-vs-linear architecture decision open (C1).** Conformance validated only against bigpowers' dialect of `SKILL.md`, not Anthropic's native format — worth a direct check before calling this "conformant." |
| P4 — Memory & Prompts | `MemoryStore` protocol + adapters; `PromptStore` | **Zero spikes touched this phase.** Entirely design-only, no empirical work at all yet. |

---

## What this critique is not

It's not a verdict that anything built so far is wrong — the corrections in §A are
mostly precision fixes (a number was imprecise, a caveat was missing), not reversals.
The one thing worth real attention before moving to an implementation plan is §B: the
differentiated headline mechanism has had zero hands-on validation while its fallback
got six spikes' worth. Worth deciding deliberately whether that's acceptable risk for
P1 or worth one more targeted spike before locking a plan.
