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

1. **Runtime-native discovery.** A `SkillStore` that indexes `SKILL.md` frontmatter
   (name + description) across many skill directories/sources and exposes them to an
   agent via progressive disclosure — mirroring how tools work in `tool-execution.md`.
2. **On-demand loading.** Only the matched skill's body + assets enter context.
3. **Sandbox-aware execution.** Skills that ship scripts execute in the same tiered
   `Sandbox` (see isolation.md) — a skill's code is untrusted code too.
4. **Governed.** Presidium grants decide which skills an agent may load/run.
5. **Versioned + sourced.** Local dirs, git repos, or a shared skills service.

## Interface (sketch)

```python
class SkillStore(Protocol):
    def index(self) -> list[SkillCard]: ...          # name + description only (cheap)
    def load(self, name: str) -> Skill: ...          # full SKILL.md body + asset refs
    async def run(self, name: str, args: dict, sandbox: Sandbox) -> RunResult: ...

@dataclass
class SkillCard:      # progressive-disclosure listing entry
    name: str
    description: str
    source: str
    version: str
```

## Deployment modes

- **Library** — read skills from local directories in-process.
- **Service** — a `SkillStore` `GenServer` on the Civitas bus backed by a shared repo,
  so a fleet of agents share one curated, versioned skill catalog.

## Open questions

1. **Disambiguation among overlapping skills — a cross-cutting concern, not a skills-
   only one.** "Used... only when relevant" (Devon's JTBD) is the *same failure mode*
   as tool-selection accuracy degrading past ~20–30 candidates ([tool-execution.md](tool-execution.md)) —
   the model choosing among N things it only partially sees. Ranking/prioritization
   tooling is deferred — not because it's low priority, but because it's genuinely
   unsolved and deserves its own design pass across *all* of Fabrica's discovery
   surfaces (tools, skills, memory search) rather than a skills-only fix. Tracked as
   a dedicated cross-cutting design question before P3 ships broadly.
2. Exact `SKILL.md` frontmatter fields to index — track the published standard.
3. Trust model for third-party skills (signing, provenance) — **deferred, not
   dropped**, same posture as Elena's log-tamper deferral: get discovery + loading
   working end-to-end first, add provenance once there's a real ecosystem to protect.
4. Relationship to Civitas's own skill/prompt ideas (v0.5 roadmap) — consolidate here.
