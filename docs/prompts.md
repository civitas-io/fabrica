# Prompts

**Status:** Design · **Last updated:** 2026-08
**Formalized by:** [contracts/prompts.md](contracts/prompts.md) — implementation-ready
types/signatures; that file is authoritative for exact API shape, this one for
the reasoning.

---

## Thesis: the narrowest of the four managers, on purpose

`context-layer.md` named this component early — *"versioned, addressable
prompt management"* — but it's had zero design attention since, and zero
spike coverage (`HANDOFF.md`'s open-items list). Writing it out reveals it
should stay genuinely small: **storage, versioning, and retrieval of prompt
template *content* — nothing else.** Two things a first pass might reach for
are deliberately excluded, and the reasoning for both is the same principle
named in [architecture.md §1a](architecture.md#1a-a-platform-wide-principle-named-explicitly-library-first-low-coupling-high-cohesion):

**1. `PromptManager` does not render templates.** Filling `{variables}` into
a stored template is a templating-engine concern (Jinja2, f-strings, a
harness's own DSL) — forcing every consumer to accept Fabrica's choice of
renderer would couple `PromptManager` to a decision that belongs to the
harness, not the context layer. `PromptManager` hands back raw template
content; rendering happens after it leaves this component, not inside it.
This is a stronger and simpler answer than memory's "wrap, don't build" (§
below) — there's no crowded market to pick a winner from here, there's just
no reason for this component to own the decision at all.

**2. `PromptManager` does not compress/shrink prompts.** `context-layer.md`
named "promptshrink compression" as one of the scattered ideas being
absorbed into this platform. **Correction to an earlier claim in this same
doc's history:** it was initially assumed this should extend `Compactor`
([memory.md](memory.md)) if it ever gets built, on the theory that
"reduce a block of text's token footprint under a budget" is one general
shape. That's wrong once the actual research is looked at directly — see
["Explored: the wider feature space"](#explored-the-wider-feature-space-not-built-investigated-deliberately)
below. Extractive compression (LLMLingua-2) and `Compactor`'s abstractive
summarization are mechanically different techniques with different
dependency shapes; collapsing them would have been a mistake in the other
direction from duplicating. Not built now either way — named here so the
next person doesn't reinvent it, and doesn't inherit the wrong assumption
about where it belongs if it is built.

## Interface (sketch)

```python
@dataclass(frozen=True)
class PromptTemplate:
    name: str              # the address — e.g. "customer_support/greeting"
    version: int           # monotonically increasing per name, assigned by put()
    content: str           # raw template content; PromptManager never
                            # interprets this string's syntax
    metadata: dict[str, Any]
    created_at: datetime


class PromptStore(Protocol):
    """Swappable backend. Default: local files/SQLite (library mode) — see
    system-design.md's existing state-ownership table, which already names
    this without a design doc behind it until now."""
    async def put(self, name: str, content: str, *, metadata: dict | None = None) -> PromptTemplate: ...
    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None: ...
    async def list_versions(self, name: str) -> list[int]: ...
    async def delete(self, name: str, version: int | None = None) -> None: ...


class PromptManager:
    """Adds exactly one thing over the raw backend: a read cache. Prompts
    are read far more often than written, unlike tool/skill retrieval where
    every query is meaningfully different — worth caching, unlike Retriever's
    search results."""
    def __init__(self, store: PromptStore) -> None: ...
    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None: ...
    async def put(self, name: str, content: str, *, metadata: dict | None = None) -> PromptTemplate: ...
    async def list_versions(self, name: str) -> list[int]: ...
    async def delete(self, name: str, version: int | None = None) -> None: ...
```

**Versions are immutable and append-only** — `put()` always creates a new
version, never overwrites one in place. That's the entire point of
versioning: a harness that pinned `version=5` for reproducibility must never
see its content change under it.

## Deployment modes

- **Library** — local files or SQLite, in-process. The expected default; prompt
  content is small and read-heavy, not a scaling concern the way sandboxes or
  retrieval are.
- **Service** — Civitas `StateStore`-backed Postgres, shared across a fleet —
  same two-mode shape as every other manager, not a new pattern.

## Integration

- **Civitas** persists the default store via its `StateStore`, same as memory's
  default store.
- **Presidium** has no obvious grant surface here — prompt template content
  isn't user data requiring scope-gated access the way memory recall is. Flagged
  as an open question below, not assumed.
- **Fabrica** owns storage/versioning/retrieval only, per the thesis above.

## Open questions

1. **No spike exists for this component at all** — unlike every other manager,
   there is zero empirical validation behind any part of this design. Worth
   naming honestly rather than letting it blend in with the validated pieces.
2. Auto-incrementing integer versions (chosen above) vs. content-hash identity
   (Git-like) vs. caller-supplied string tags — not stress-tested against a
   real consumer need. Integers are simplest; not proven sufficient.
3. **Named aliases/tags** (e.g., a "prod" tag pointing at whichever version is
   currently live, separate from "latest") were considered and deliberately
   left out of v1 — real, plausible need, but unvalidated by any concrete use
   case yet. Same "ship the default, revisit if a gap forces it" call as
   Windows support and macOS Tier 2.
4. Does Presidium need any grant surface here at all (e.g., gating who can
   `put()` a new version of a prompt used broadly)? Not designed — genuinely
   unclear whether this is a real governance need or not.
5. If prompt compression becomes real, does it need `Summarizer`'s DI shape at
   all, or is it closer to a `RetrieverBackend`-style wrapped local model
   (LLMLingua-2 is a small importable classifier, not a big-LLM dependency)?
   See ["Explored"](#explored-the-wider-feature-space-not-built-investigated-deliberately)
   below — leaning toward "its own mechanism," not resolved as a contract
   decision here.

## Explored: the wider feature space (not built, investigated deliberately)

A deliberate divergent-thinking pass — "think bigger, we may or may not use
any of it" — grounded against the actual current (2024–2025) prompt-tooling
landscape rather than invented from scratch. Captured here in full even where
the answer is "no, not now," per the same "named, not buried" convention used
for every other deferred decision in this platform.

### A real miscategorization worth fixing regardless of anything else

`PromptManager`'s read cache (above) and **"prompt caching" as an industry
term are two different things**, and the design currently addresses only the
minor one. Provider-side prompt caching (Anthropic/OpenAI/Gemini) reuses
**KV-cache computation** for an exact token-for-token repeated prefix,
skipping re-computation of that portion entirely — one of the single largest
cost/latency levers available (Anthropic: up to 90% cost reduction via
explicit `cache_control` breakpoints; OpenAI: ~50% discount, automatic above
~1024 tokens; Gemini: explicit context caching, 1-hour TTL). It requires
**exact prefix stability** — one changed token anywhere breaks the cache from
that point forward, which is why providers all converge on the same
structuring rule: stable content (system prompt, tool defs) first, dynamic
content (user query, live conversation) last.

**`PromptManager`'s storage model already has the right shape for this, by
accident:** a stored `PromptTemplate` *is* the stable prefix; whatever a
harness fills in at runtime is the dynamic suffix. `PromptManager` doesn't
need to know Anthropic vs. OpenAI vs. Gemini specifics — it only needs to
**preserve and expose the boundary** between "versioned, stable" and
"filled in per-call," which every provider's caching mechanism relies on
regardless of vendor. This is the one idea from this whole pass that looks
worth doing something with soon: low-coupling (no provider-specific logic
owned here), cheap, and high-leverage.

### Automated prompt optimization / "tuning" — DSPy, TextGrad

DSPy (Stanford) and TextGrad treat prompt wording as something to *search
over* against an eval metric and training examples — not hand-edit. A DSPy
optimizer's output is, mechanically, just another prompt. This maps directly
onto `PromptManager`'s existing version model: it doesn't need to *run* DSPy
(a heavy, separate dependency — real "wrap, don't build" territory), but
version `metadata` could record **provenance** — hand-written vs.
auto-optimized, against which metric, what score it achieved.
`PromptManager` would become the registry optimization tools write results
*into*, never the optimizer itself. Real value, correctly speculative until
there's an actual optimization workflow to attach it to.

### Extractive compression (LLMLingua-2) — mechanically distinct from `Compactor`

LLMLingua-2 formulates compression as **extractive token classification**: a
small, local, bidirectional-Transformer classifier removes low-information
tokens from the *original* text — the output is a strict subset of the
input, never new text, which is precisely why it avoids the hallucination
risk of LLM-based summarization. This is mechanically nothing like
`Compactor`'s `Summarizer`-based abstractive approach (rewrite into new,
shorter text via an injected large model). It's closer in shape to a
`RetrieverBackend` — wrap a small local model — than to `Summarizer`'s
harness-injected big-LLM pattern. If this is ever built, it should probably
be its own swappable mechanism, not a generalization of `Compactor` as
previously (incorrectly) assumed in this document's history.

### Registry/hub workflow features — an honest gap, named

Humanloop, PromptLayer, LangSmith Hub, and MLflow Prompt Registry all treat
as baseline: **diff views** between versions, **environment aliases**
(dev/staging/prod), **A/B testing** linked to eval datasets, and
**attribution** (who changed what, when). This platform's design has
versioning and nothing else on that list. That's a real gap in the
*workflow* layer, distinct from the *storage* layer this contract actually
covers — named honestly rather than implied to be covered by "versioning"
alone.

### A portable file format — validated by direct precedent, not invented here

Humanloop ships a dedicated `.prompt` file format specifically for
git-native source control — prompts authored and reviewed via normal PRs and
`git diff`, not hidden inside a database. This maps directly onto a pattern
this exact codebase already has working: `SkillManager.load(skill_dir)`
reading a `SKILL.md` package (`skills-gateway.md`). A `PromptManager.load(path)`
reading a `PROMPT.md`-shaped file (frontmatter + content) would be the same
shape applied to prompts. Of everything in this section, this is the
strongest single idea — not because it's novel, but because it has a working
precedent in a real product (Humanloop) *and* a working precedent already
implemented in this codebase (`SkillManager`), which is an unusually strong
position for a "considered" idea to be in.

### Composition/partials

Real and common (Jinja includes, reusable blocks referenced across multiple
top-level templates). Adds genuine complexity this pass doesn't resolve: if a
shared fragment changes, do dependent templates that reference it
auto-update, or stay pinned to whatever fragment version they last resolved?
That's a real versioning-semantics question, not a detail — unresolved here.

### Triage

**Worth doing something with soon:** the cache-boundary marker (cheap,
high-leverage, no provider coupling) and the `PROMPT.md` portable format
(direct precedent already exists in this codebase).

**Real value, correctly deferred:** provenance metadata for optimization
tooling; diff/environment-alias/A/B-testing workflow features — genuine
value, but zero validated demand yet, same "ship the default, revisit if
forced" logic as Windows support and macOS Tier 2.

**Belongs elsewhere or not at all:** running DSPy/TextGrad optimization
itself (a separate tool, not this platform's job); multi-provider prompt
reformatting (real coupling risk, no clear owner); prompt-injection scanning
(plausibly Presidium's governance concern, not this component's).
