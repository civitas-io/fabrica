# Critique: Design vs. Evidence

**Status:** Review, largely resolved · **Last updated:** 2026-08
**Method:** every claim in the design docs (`context-layer.md`, `tool-execution.md`,
`isolation.md`, `skills-gateway.md`, `memory.md`, `civitas-presidium-integration.md`,
`landscape.md`, `problem-definition.md`) checked against the spikes in
`specs/archive/spikes/` (seven by the end, not six — the code-mode spike was added
mid-critique, see §B). **Update:** this doc originally proposed corrections without
applying them. All 11 items in §A and all 5 items in §C are now marked Resolved and
**have been applied** to the docs they reference — the table entries below keep the
original finding for the reasoning trail, with a Resolved note added, not deleted.

---

## Executive summary

The thesis holds — code-mode's token-savings argument, the platform-primitives
differentiation, and the interface-first package split all survive contact with real
measurement. **The one gap this critique originally flagged as most important —
the headline mechanism (code-mode) had never been tested — has since been closed**
(§B): a seventh spike validated it directly, with a stronger result than expected
(lower cost *and* better correctness than the traditional approach). Six earlier
spikes had gone deep on the fallback (`find` retrieval, now unified with skills —
see `retrieval.md`) and the isolation substrate; none had touched the pitch itself
until this gap was named and closed.
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
| A1 | `isolation.md` tier table: "Firecracker \| 125 ms boot" | Real measurement: `InstanceStart`→VMM-`Running` is **10.5ms**; →actual usable userspace (full Ubuntu) is **1,055ms**. The "125ms" figure conflates two different readiness signals and assumes a minimal image nobody has built. | **Resolved** — `isolation.md` now states both numbers explicitly with the minimal-image caveat. |
| A2 | `isolation.md` warm-pool section: silent on snapshot creation | Snapshot *creation* took **~807ms** for a 512MiB guest — a real, one-time pool-initialization cost, currently invisible in the doc. | **Resolved** — added to `isolation.md` as a distinct, budgeted cost. |
| A3 | `isolation.md`: no mention that a minimal rootfs/init is separate work | Full-Ubuntu boot-to-usable was 1s+; production-grade ~125ms needs a purpose-built minimal image, which doesn't exist and isn't scoped anywhere. | **Resolved** — named explicitly in `isolation.md` as its own scope item. |
| A4 | `isolation.md` tier table has no platform dimension; reads as OS-agnostic | gVisor **and** Firecracker are Linux-only. `srt` (real numbers: p50 152ms) is the macOS Tier-1 candidate; `libkrun`/`krunvm` is the macOS Tier-2 candidate **with no snapshot/restore at all**; Windows has no validated Tier-1 candidate. | **Resolved** — `isolation.md`'s tier table is now platform-dispatched (auto-detected, not user-configured — a deliberate exception to how transport works). Windows Tier-1 deferred (small segment; `srt`'s claimed Windows support untested but not blocking). macOS Tier 2's cold-boot-only ceiling accepted explicitly ("snapshot/restore is great to have, not a must"). |
| A5 | `tool-execution.md`'s "vendor-neutral... hardenable to microVMs" framed as a general platform claim | True and validated on Linux only. macOS: Tier 1 works but ~50% slower than Linux gVisor and has no restore-based warm pool at Tier 2. Windows: unvalidated, real gap. | **Resolved** — `isolation.md` now states per-platform reality directly rather than implying uniform readiness. |
| A6 | `problem-definition.md` Devon metric: "Flat index cost... stays token-bounded regardless of skill count" | Measured: index cost grew **linearly** (1,478→6,248 tokens, N=10→81) because the current `SkillStore.index()` design puts the whole index in the model's own context — no server-side search step like `find_tools` has. Not flat. | **Resolved** — [retrieval.md](retrieval.md): a shared `Retriever` engine + `find(query, kind)` surface makes tools and skills both O(1), no duplicated infrastructure. See C1. |
| A7 | `tool-execution.md` "build vs. wrap" backend list names LlamaIndex/LangChain only | `prx`'s off-the-shelf, code-tuned embedding model hit **100% precision@3** on a 12-tool disambiguation benchmark with zero fine-tuning — same-org, zero-dependency, Apache 2.0. | **Resolved** — `retrieval.md` names `PrxBackend` explicitly as a candidate. |
| A8 | No doc states a rank-vs-threshold requirement for retrieval backends | Every embedding score observed (prx: 0.01–0.04; correct hits and near-misses both landed in this band) was low in absolute terms but reliable in **rank**. A threshold rule would have silently discarded every correct answer. | **Resolved** — `retrieval.md`'s `Retriever` protocol states this as a hard requirement in its own docstring. |
| A9 | No doc mentions tool-identity stability | Spike 1's turn-2 cost drifted slightly because the matcher could flip between near-duplicate tool variants for the same capability as N grew. | **Resolved** — `retrieval.md`'s `Indexable.id` is now documented as the stable, deterministic key callers must key on, distinct from `name`. |
| A10 | No doc states the prx integration shape | Fresh subprocess per call: p50 74.9ms. Persistent process (`prx mcp`, Civitas-supervised): p50 37.0ms, and the *true* cold-start cost (~260ms) is paid once, not per call. | **Resolved** — `retrieval.md`'s `PrxBackend` entry states persistent-process integration explicitly. |
| A11 | `landscape.md §3a` names "sandbox-exec / Seatbelt" generically for macOS Tier 1 | The concrete, validated candidate is Anthropic's own `srt`, built on `sandbox-exec`, with real measured overhead and a **Windows mode** (`srt-sandbox` user + WFP filters) that's a plausible fix for the previously-flagged Windows Tier-1 gap. | **Resolved** — `landscape.md §3a` and `isolation.md` now name `srt` specifically with real numbers; Windows mode flagged as a deferred, unverified lead (per direction: small segment, test only if a gap forces it). |

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
   **Resolved — neither, a third option:** [retrieval.md](retrieval.md) unifies
   tools and skills under one `Retriever` engine and one `find(query, kind)` call,
   inspired by `find_tools`'s interface shape, Anthropic's `defer_loading` eager/
   deferred split, and prx's validated backend + rank-not-threshold/persistent-
   process lessons. Memory intentionally stays separate (different semantics),
   sharing only the engine underneath. Also folds in a stated engineering
   principle: where Fabrica builds (not wraps) compute, prefer Rust with a Python
   binding — the default `KeywordBackend` follows this, matching prx's own shape.
2. **Resolved.** Dispatch is auto-detected by host OS, not user-configured — a
   deliberate exception to how transport works elsewhere in Civitas, stated
   explicitly in `isolation.md` rather than left implicit.
3. **Resolved (deferred deliberately).** Windows is a small segment; `srt`'s claimed
   `windows-install` support is untested but not blocking — verify only if a real
   gap surfaces, not preemptively.
4. **Resolved.** Cold-boot-only macOS Tier 2 is accepted and shippable —
   snapshot/restore is valuable, not mandatory. Apple's Containerization framework
   remains a named-but-unevaluated alternative if libkrun's packaging friction
   becomes a real integration cost.
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

| Phase | Original scope | Readiness after all seven spikes + this critique's fixes |
|---|---|---|
| P0 — Thesis | docs + personas + problem definition | Done. |
| P1 — Tools | `find` fallback + tools-as-code namespace | **Both validated.** Headline mechanism (code-mode) proven — lower cost *and* better correctness than the traditional loop, 3/3 runs (§B). Fallback unified with skills under one `Retriever` engine (`retrieval.md`). |
| P2 — Isolation | `Sandbox` protocol; Firecracker backend + warm pools | Linux: strong, real hardware. macOS: Tier 1 real (`srt`, measured), Tier 2 real but permanently cold-boot-only (libkrun, accepted per direction — "great to have, not a must"). Windows: deliberately deferred, not blocking. Platform dispatch is now auto-detected and hidden from users, a stated architecture decision, not an afterthought. |
| P3 — Skills | `SKILL.md`-conformant gateway with progressive disclosure | Retrieval/disambiguation validated on real data (4/4 exact picks, genuine ambiguity). **O(1)-vs-linear resolved** via unification with tools, not accepted as a limitation. Conformance still validated only against bigpowers' dialect of `SKILL.md`, not Anthropic's native format — the one item in this whole critique that remains genuinely unchecked, not just deferred by decision. |
| P4 — Memory & Prompts | `MemoryStore` protocol + adapters; `PromptStore` | **Still zero empirical work.** Untouched by any spike or this critique's fixes — the most honest gap remaining in the whole doc set. |

---

## What this critique is not

It was never a verdict that anything built so far was wrong — the corrections in §A
turned out to be mostly precision fixes (a number was imprecise, a caveat was
missing), not reversals, and every one of them is now applied. The one thing that
genuinely mattered — §B's finding that the differentiated headline mechanism had
zero hands-on validation — got closed by running the spike rather than accepting the
risk, and the result came back stronger than the question asked for.

**What's actually left, honestly:** P4 (memory & prompts) has had no empirical work
at all, and P3's `SKILL.md` conformance has only been checked against one tool's
dialect of the format, not Anthropic's native one. Those are the two real remaining
gaps in this doc set — everything else raised here has a resolution applied, not
just proposed.
