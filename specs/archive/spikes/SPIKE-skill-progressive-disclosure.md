# Spike: skill progressive disclosure (real SKILL.md catalog)

## Question

Using the real bigpowers SKILL.md catalog (81 skills), does a two-level
progressive-disclosure loader — frontmatter-only index, full body loaded only
on demand — keep token cost flat as the candidate pool grows (same methodology
as Spike 1, applied to skills), and does a real model correctly select among
genuinely overlapping real descriptions (not synthetic ones) when given only
the index?

Tests Devon's "flat index cost" and "discovery precision" success metrics in
[docs/problem-definition.md](../../docs/problem-definition.md).

## Result

**Answered — and it surfaced a real design gap, not just a confirmation.**

## Findings

### Part 1 — token cost as the catalog grows

Real corpus: 81 production `SKILL.md` files (this session's own bigpowers
catalog), bodies ranging 55–4,010 words. Measured against real Claude-on-Vertex
calls at N = 10, 30, 81 skills:

| N skills | Static (all full bodies upfront) | Progressive total (2 turns) | Turn 1 (index only) | Turn 2 (1 full body) |
|---:|---:|---:|---:|---:|
| 10 | 16,277 | 3,113 | 1,478 | 1,635 |
| 30 | 43,334 | 5,555 | 2,699 | 2,856 |
| 81 | 143,967 | 12,653 | 6,248 | 6,405 |

At full catalog scale (N=81), progressive disclosure uses **~9% of static's
token cost** (12,653 vs 143,967) — a large, real saving.

**But it is not flat, and this matters.** Turn 1 (the index) grew from 1,478 to
6,248 tokens as N went 10→81 — **roughly linear in N**, just with a much
smaller constant than full bodies. This differs from Spike 1's `find_tools`
result, where turn 1 was *exactly constant* (730 tokens) regardless of N.

### Part 2 — why the difference, and what it means

The two designs are not architecturally equivalent, and this spike's script
exposed that by testing the design as currently sketched in
`skills-gateway.md`, not an idealized version of it:

- **`find_tools` (Spike 1):** the model sees *only* the meta-tool's schema.
  Matching happens server-side, hidden from the model. Context cost is truly
  O(1) in registry size.
- **This spike's skill index:** every skill's name+description was placed
  directly in the prompt for the *model itself* to browse and choose from —
  there was no server-side search step. That's why it's O(N) with a small
  constant, not O(1). This is what `skills-gateway.md`'s `SkillStore.index()`
  currently implies ("returns names + descriptions... cheap"), without
  specifying whether the model sees the whole index or a filtered result of a
  search.

**This is a real, previously-unnoticed gap** between how the doc reads and
what it would need to say to make the same O(1) claim tools make. Two honest
paths forward, not resolved here:
1. Accept O(N)-with-small-constant as good enough at realistic catalog sizes
   (81 skills cost ~6.2k tokens for the index — cheap in absolute terms even
   if not flat), or
2. Add a `find_skills(query)` server-side search step mirroring `find_tools`,
   trading model-visible browsing for true flat cost at any catalog size.

### Part 3 — real-world disambiguation (not synthetic)

Four genuinely ambiguous queries against the **full real 81-skill index**,
each with plausible near-neighbors actually present in the catalog (e.g.
`fix-bug` vs `investigate-bug` vs `diagnose-root` vs `quick-fix`;
`dispatch-agents` vs `delegate-task`):

| Query | Picked | Plausible near-neighbors in catalog |
|---|---|---|
| "I found a bug in production, help me fix it" | `fix-bug` | investigate-bug, diagnose-root, quick-fix |
| "break a big feature into vertical slices before building it" | `slice-tasks` | plan-work, scope-work |
| "run multiple independent tasks in parallel without waiting" | `dispatch-agents` | delegate-task |
| "the agent seems stuck and isn't making progress" | `diagnose-stall` | diagnose-root |

**All four picks are exactly correct** against each skill's own stated
purpose — a strong real-world confirmation of Spike 4's disambiguation finding,
this time on authentic content with real near-neighbor ambiguity, not a
synthetic benchmark I designed myself to be easy to get right.

## Evidence

Script: `specs/archive/spikes/scripts/spike-skill-progressive-disclosure/spike.py`
(held, not deleted). Loads and parses the real `SKILL.md` frontmatter directly
from `~/.pi/agent/npm/node_modules/bigpowers/.pi/skills/*/SKILL.md` — no
synthetic data used anywhere in this spike.

```
N skills |  static (O(N)) |  progressive total |    turn1 |    turn2 | picked
--------------------------------------------------------------------------------
      10 |          16277 |               3113 |     1478 |     1635 | spike-prototype
      30 |          43334 |               5555 |     2699 |     2856 | spike-prototype
      81 |         143967 |              12653 |     6248 |     6405 | spike-prototype
```

## Implications for the plan

- **Devon's "flat index cost" success metric needs a precise definition, not a
  reused assumption from tools.** "Flat" is only true for `find_tools` because
  matching is server-side. If `skills-gateway.md`'s design stays as
  model-browses-the-index, the honest metric is "index cost grows linearly
  with a small, cheap constant" — not O(1). Worth stating explicitly rather
  than silently inheriting Priya's tool metric language.
- **A `find_skills(query)` search step is the direct fix**, if true flatness
  at any catalog size is a hard requirement — this would make skills and tools
  architecturally symmetric. Whether that's worth the added complexity for
  realistic catalog sizes (dozens to low hundreds) is a real design decision,
  not obvious either way.
- **Disambiguation quality holds up on real, not synthetic, data** — 4/4 exact
  correct picks including on skills with genuine, currently-existing
  near-neighbors. This meaningfully increases confidence in Spike 4's finding
  beyond "it worked on data I made up to be testable."

## What was NOT explored

- **Catalogs beyond 81 skills.** The linear-index-cost concern would bite
  harder at hundreds/thousands of skills — not tested.
- **Supplementary files.** Real `SKILL.md` packages can ship scripts/assets
  alongside the markdown body (this spike only measured text body tokens, not
  any bundled files a skill might reference).
- **Anthropic's actual Claude Skills feature** — this spike hand-rolled a
  `load_skill` tool to simulate progressive disclosure; it did not test
  whether Anthropic's native skills mechanism (Oct 2025) behaves the same way
  token-wise.
- **The `find_skills`-with-server-side-search alternative** — named as a
  possible fix above, but not built or measured here.

## Recommendation

**Progressive disclosure is still clearly worth building** — 91% token
reduction at N=81 is large and real, even without hitting true O(1). But
**`skills-gateway.md` needs a corrected, more precise claim** before
`plan-work`: either commit to "cheap and linear" as the honest metric, or add
a `find_skills` search step to make the O(1) claim actually true. This is
exactly the kind of design gap spikes exist to catch before it's locked into
an implementation plan — flagging for the end-of-round critique rather than
silently patching the doc now, per instruction.
