# Spike: prx invocation latency (subprocess vs. persistent process)

## Question

What is the real latency (p50/p95) of invoking prx's search — via (a) a fresh
subprocess per call, and (b) a single persistent process (`prx mcp` over stdio)
reused across calls — and does either fall within a tolerable range for a
production tool-call hot path (target: sub-50ms great, sub-200ms tolerable,
seconds disqualifying)?

Follows up directly on the open "integration mechanism" question raised in
[SPIKE-tool-disambiguation-retrieval-quality.md](SPIKE-tool-disambiguation-retrieval-quality.md#what-was-not-explored) —
that spike proved prx's retrieval quality but explicitly did not test whether
it's fast enough to use.

## Result

**Answered**, plus one unplanned but material finding (cold vs. warm start).

## Findings

Measured against the same 12-tool fixture as the disambiguation spike, 15 query
round-trips per method, on this dev machine (Apple Silicon — not a production
sandbox/microVM environment):

| Method | p50 | p95 | max |
|---|---:|---:|---:|
| Fresh subprocess per call | 74.9ms | 90.7ms | 90.7ms |
| Persistent `prx mcp` process, reused | 37.0ms | 67.6ms | 67.6ms |

**Persistent process is ~2x faster per call** than spawning fresh each time —
expected, since it amortizes process-spawn and binary-load cost across many
calls instead of paying it every time.

**Unplanned finding — cold vs. warm process start differs by ~40-50x.** An
early manual protocol probe (done to learn the JSON-RPC shape before writing
the timed script) measured `prx mcp` startup + initialize at **259ms**. The
timed script's own startup measurement, run moments later, showed **6.4ms** —
a 40x discrepancy too large to be noise. A dedicated follow-up test (5
back-to-back fresh `prx mcp` launches) confirmed: **every launch after the
first one lands in the 4.4–6.3ms range.** The likely explanation is OS page
cache — the very first launch pays real disk I/O to load the binary (and
possibly the memory-mapped model weights); every subsequent launch on the same
machine hits warm pages. This tracks with prx's own documented zero-copy
mmap behavior for embeddings (README: "the OS page cache keeps the index warm
across queries").

**Neither number crosses into "disqualifying."** Both p95s are comfortably
under the 200ms tolerable bar; the persistent-process p50 (37ms) is close to
the "great" bar but doesn't clear it outright given a 67.6ms p95, and the
subprocess p50 (75ms) sits solidly in "tolerable," not "great."

## Evidence

Scripts: `specs/archive/spikes/scripts/spike-prx-invocation-latency/spike.py`
(main comparison) and an inline follow-up script (cold/warm confirmation, not
saved separately — reproducible from the commands shown in this doc).

```
=== Option A: fresh subprocess per call ===
subprocess ... n= 15  min=  73.0ms  p50=  74.9ms  p95=  90.7ms  max=  90.7ms

=== Option B: single persistent `prx mcp` process, reused ===
one-time startup + initialize handshake: 6.4ms (paid once, not per call)
mcp tools/call ... n= 15  min=  36.1ms  p50=  37.0ms  p95=  67.6ms  max=  67.6ms

warm MCP process is ~2.0x faster per call than spawning fresh each time
```

```
startup #1: 6.3ms
startup #2: 4.9ms
startup #3: 4.6ms
startup #4: 4.6ms
startup #5: 4.4ms
```
(vs. the very first, true-cold manual probe: 259ms)

## Implications for the plan

- **The persistent-process shape is the right integration model, and it fits
  the platform's own philosophy well.** A long-lived `prx mcp` child process,
  supervised by Civitas (as a child under the same supervision tree that
  already restarts crashed processes), pays the true cold-start cost
  (~260ms, rare — once at boot or after a crash-restart) and then serves every
  subsequent call warm (~37-67ms). This is a natural fit for a Civitas
  `GenServer` wrapping the child process, not a fresh subprocess per tool call.
- **Subprocess-per-call remains a viable fallback**, not a dead end — ~75-90ms
  is still within the "tolerable" band, just not as good. Worth keeping as the
  zero-infrastructure default (Tier 0, matching Priya's "no external services"
  requirement) with the persistent-process mode as the upgrade path — the same
  library-mode/service-mode pattern already used everywhere else in the
  platform.
- **The MCP stdio transport needs a bridge into Civitas's async model.**
  `prx mcp` speaks newline-delimited JSON-RPC over stdio; Civitas's
  `AgentProcess`/`GenServer` model is async message-passing. This spike used a
  minimal hand-rolled client — a real implementation needs a small adapter
  layer, not raw stdio plumbing scattered through Fabrica's code.
- **The cold/warm gap connects directly to Marcus's isolation tiers.** If prx
  became a sandbox-adjacent dependency, whether a fresh Firecracker microVM
  pays the ~260ms cold cost or inherits a warm ~5ms path depends on whether the
  underlying host shares page cache across guests for a common read-only base
  image — a real, concrete question for `isolation.md`'s warm-pool design, not
  answered here.

## What was NOT explored

- **Real production environment.** Measured on a dev laptop, not a container,
  microVM, or CI runner. Latency profile under real infra (network filesystem,
  cgroup CPU limits, cold Firecracker guest) is unknown — connects to the
  still-blocked Firecracker spike (needs a Linux host with KVM).
- **Scale.** Only a 12-tool index. Whether latency holds at 100+ tools (or
  degrades due to index size) wasn't tested — though prx's own published
  benchmarks suggest sub-second indexing/search even at large codebase scale,
  that's for code search, not this use case's specific access pattern.
- **Concurrent access.** Only sequential calls were measured. A production
  Fabrica deployment would have many agents querying the same persistent
  process concurrently — contention/throughput wasn't tested.
- **The actual Civitas-supervised wrapper.** This spike drove `prx mcp`
  directly from a script; it did not build or test a real `GenServer` child
  process wrapper, restart-on-crash behavior, or how tool results flow back
  into a Civitas `AgentProcess`.
- **Why the true cold start is ~260ms** — disk I/O was inferred as the cause
  but not confirmed (e.g., via `dtrace`/`fs_usage` or deliberately dropping
  page cache). Treat as a plausible explanation, not a proven one.

## Recommendation

**Persistent-process integration (a supervised `prx mcp` child) is the
production-shape answer; subprocess-per-call is an acceptable, simpler
fallback for library/dev mode.** Neither is disqualifying on latency grounds.
Before `plan-work`:

1. Design the Civitas-supervised wrapper as a `GenServer` child, not ad hoc
   subprocess calls scattered through `find_tools` code.
2. Carry the cold/warm host-page-cache question into `isolation.md`'s
   warm-pool design — it may already be solved by whatever base-image sharing
   strategy the Firecracker pool uses, or it may need its own answer.
3. The still-blocked Firecracker spike (needs Linux + KVM) remains the next
   real gap — this spike only clarifies what to test once that environment is
   available.
