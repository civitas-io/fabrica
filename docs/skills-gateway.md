# Skills Gateway

**Status:** Design · **Last updated:** 2026-08

---

## Thesis

Skills — packaged, reusable agent capabilities — standardized fast in late 2025, and
**no agent runtime owns the loading of them.** That gap is Fabrica's strongest
near-term product.

- **Claude Agent Skills** (Anthropic, Oct 2025): modular capabilities packaging
  instructions + metadata + optional scripts/resources, invoked automatically when
  relevant. Supported across Claude.ai, Claude Code, the Agent SDK, and the Developer
  Platform.
- **Open `SKILL.md` standard** (published Dec 2025): a portable directory containing a
  `SKILL.md` plus optional scripts/references/assets, designed to work across Claude
  Code, OpenAI Codex, GitHub Copilot, etc. Uses **progressive disclosure** — the agent
  reads names + descriptions first, and loads full detail only when a task matches.
- **AGENTS.md** — the sibling project-context standard, governed by the Agentic AI
  Foundation (Linux Foundation; Anthropic/Google/Microsoft/OpenAI).

## Design principle: conform, don't invent

Fabrica **does not define a new skill format.** It implements a runtime-native loader
for the open `SKILL.md` standard, with progressive disclosure as a first-class feature.
This rides a standardizing wave instead of fighting it.

## What Fabrica adds over "just read the file"

1. **Runtime-native discovery via the shared retrieval engine.** A `SkillStore`
   registers each skill's frontmatter (name + description) as an `Indexable` with
   the `Retriever` from [retrieval.md](retrieval.md) — the same engine tool discovery
   uses, not a parallel mechanism. **Correction from measurement:** an earlier
   design (index dumped directly into model context) was **not** O(1) — see
   [SPIKE-skill-progressive-disclosure.md](../specs/archive/spikes/SPIKE-skill-progressive-disclosure.md),
   which measured linear growth (1,478→6,248 tokens, N=10→81). The shared `find`
   meta-tool fixes this the same way it works for tools: server-side matching,
   model never sees the full catalog.
2. **On-demand loading.** Only the matched skill's body + assets enter context.
3. **Sandbox-aware execution.** Skills that ship scripts execute in the same tiered
   `Sandbox` (see isolation.md) — a skill's code is untrusted code too.
4. **Governed.** Presidium grants decide which skills an agent may load/run.
5. **Versioned + sourced.** Local dirs, git repos, or a shared skills service.

## Interface (sketch)

```python
class SkillStore(Protocol):
    def register(self, retriever: Retriever) -> None: ...  # indexes as Indexable(kind="skill")
    def load(self, name: str) -> Skill: ...                 # full SKILL.md body + asset refs
    async def run(self, name: str, args: dict, sandbox: Sandbox) -> RunResult: ...
```

Discovery itself (the old `SkillCard`-listing shape) is now the shared `find(query,
kind="skill")` call from [retrieval.md](retrieval.md), not a skills-specific type —
see that doc for `Indexable`/`RankedMatch`.

## Deployment modes

- **Library** — read skills from local directories in-process.
- **Service** — a `SkillStore` `GenServer` on the Civitas bus backed by a shared repo,
  so a fleet of agents share one curated, versioned skill catalog.

## Open questions

1. **Resolved by [retrieval.md](retrieval.md), not deferred.** "Used... only when
   relevant" (Devon's JTBD) is the *same failure mode* as tool-selection accuracy
   degrading past ~20–30 candidates — the model choosing among N things it only
   partially sees. Rather than a skills-only fix, this is now one shared `Retriever`
   engine and one `find(query, kind)` surface across tools and skills, validated on
   real data in [SPIKE-skill-progressive-disclosure.md](../specs/archive/spikes/SPIKE-skill-progressive-disclosure.md)
   (4/4 exact picks on genuinely ambiguous real skills).
2. Exact `SKILL.md` frontmatter fields to index — track the published standard.
3. Trust model for third-party skills (signing, provenance) — **deferred, not
   dropped**, same posture as Elena's log-tamper deferral: get discovery + loading
   working end-to-end first, add provenance once there's a real ecosystem to protect.
4. Relationship to Civitas's own skill/prompt ideas (v0.5 roadmap) — consolidate here.
