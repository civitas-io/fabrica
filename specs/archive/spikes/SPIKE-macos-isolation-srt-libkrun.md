# Spike: macOS isolation — Anthropic's Sandbox Runtime (Tier 1) + libkrun/krunvm (Tier 2)

## Question

Can we actually install and run Anthropic's Sandbox Runtime (`srt`) as the
Tier-1-equivalent, and `libkrun`/`krunvm` as the Tier-2-equivalent, on real
macOS hardware — measuring real launch latency for both, and specifically
whether `libkrun` has anything resembling Firecracker's snapshot/restore
(since that's what Marcus's warm-pool design depends on)? And does either
integrate cleanly with Civitas's process-oriented `Sandbox` protocol, or does
it need a different shape?

Follow-up to the cross-platform isolation gap flagged in
[landscape.md §3a](../../../docs/landscape.md). Reframed mid-conversation from a
hand-rolled `sandbox-exec` profile to Anthropic's own `srt`, after confirming
`sandbox-exec` is deprecated-but-functional and Anthropic already ships a
production tool built on it.

## Result

**`srt` (Tier 1): fully answered — works well, real numbers.**
**`libkrun`/`krunvm` (Tier 2): partially answered — the most important part
(no snapshot/restore) is confirmed, but end-to-end VM boot was not reached
within the timebox.** Per the spike discipline, stopped at the 45-min mark
rather than push through — partial learning is still learning.

## Findings

### Part 1 — Anthropic's Sandbox Runtime (`srt`), Tier 1

Installed via `npm install -g @anthropic-ai/sandbox-runtime` — worked
instantly, no friction.

**Real enforcement, confirmed directly, not assumed:**

| Test | Result |
|---|---|
| Write to `/tmp` | **Denied** — `Operation not permitted`, exit code 1 |
| Network to an unlisted host | **Denied** — proxy returns `CONNECT tunnel failed, response 403` |
| Read `~/.gitconfig` | **Allowed** |

The read-allowed/write-denied/network-denied pattern is a deliberate,
sensible default posture for coding agents (read broadly to understand
code; restrict writes and network to prevent tampering/exfiltration) — not
a bug, though worth confirming intentionally rather than assuming.

**Latency (n=10, `srt echo hello`):**

| | Value |
|---|---:|
| min | 146.1ms |
| p50 | 152.1ms |
| max | 214.4ms |

This is **meaningfully heavier** than Linux gVisor (~100ms, per earlier
research) and notably heavier than Firecracker's own *restore* latency
(~8ms, measured in the previous spike) — a real, concrete cross-platform
asymmetry. Architecturally, `srt` spins up an HTTP+SOCKS proxy mux **per
invocation** (visible in `--debug` output) plus Node.js runtime startup —
both real, structural costs, not incidental slowness.

**Integration note:** `srt --help` reveals a `--control-fd` option
suggesting a persistent control-channel mode (JSON lines protocol) that
might avoid per-call setup cost, similar to how a persistent `prx mcp`
process beat fresh subprocess spawns in the earlier prx latency spike. **Not
explored** — flagged as the natural next optimization to check before
concluding 152ms is the real floor.

**Bonus, unplanned finding:** `srt` also ships a **Windows mode**
(`srt windows-install` — provisions a dedicated `srt-sandbox` user account +
Windows Filtering Platform network filters). This may directly fill the
Windows Tier-1 gap flagged as unresolved in `landscape.md §3a` — not tested
here, but worth a dedicated follow-up spike.

### Part 2 — libkrun/krunvm, Tier 2

**Confirmed via research before any hands-on testing:** libkrun/krunvm has
**no snapshot/restore or checkpoint/resume of a running microVM.** This is
the single most important fact about libkrun for Marcus's design — the fast
warm-pool restore mechanism that made Firecracker viable on Linux (~8ms) has
**no equivalent path here.** Every launch would be a cold start.

**Install:** `brew tap libkrun/krun && brew install krunvm` — worked
cleanly, no errors, ~15 dependencies pulled (buildah, gnupg, molten-vk,
virglrenderer, etc.). One new procedural step: recent Homebrew requires
`brew trust <tap>` for non-official taps before formulae will load — a
minor, one-time friction point, not a real blocker.

**Getting to an actual VM boot surfaced three separate, non-obvious
packaging issues**, each fixed without `sudo`:

1. `buildah` (which `krunvm` uses internally to pull OCI images) couldn't
   find `registries.conf` — Homebrew installs it to
   `/opt/homebrew/etc/containers/`, but buildah's hardcoded lookup expects
   `/etc/containers/`. **Fixed** by copying to the user-level XDG override
   path (`~/.config/containers/registries.conf`) — no root needed.
2. Same mismatch for `policy.json` (image signature policy). **Fixed** the
   same way.
3. Even with both config files resolvable, `buildah` defaulted to matching
   image manifests against the **host OS** (`darwin`) instead of `linux` —
   nonsensical for a Linux container image, but the default nonetheless.
   **Fixed** with an explicit `--platform linux/arm64` override on `buildah`
   directly.

After fix #3, **raw `buildah from docker.io/library/alpine:latest` succeeded**
(27.2s, including one transient TLS-handshake retry — network flakiness, not
a real issue). **This was `buildah` directly, not `krunvm` itself** —
`krunvm create` (the actual command under test) does not expose an
equivalent `--platform` override in its own CLI (`krunvm create --help` has
no such flag), so whether `krunvm`'s own wrapper hits the same OS-mismatch
problem — and whether it can be worked around at all without patching
`krunvm` — **was not resolved before the timebox ran out.**

## Evidence

Script/session log: `specs/archive/spikes/scripts/spike-macos-isolation/`
(held). Key raw outputs:

```
=== srt basic sanity ===
real	0m0.231s

=== srt write denial ===
/bin/bash: /tmp/srt-write-test.txt: Operation not permitted

=== srt network denial ===
curl: (56) CONNECT tunnel failed, response 403

=== srt timing, n=10 ===
min=146.1ms  p50=152.1ms  max=214.4ms
```

```
=== krunvm/buildah packaging fixes ===
Error: open /etc/containers/policy.json: no such file or directory
  -> fixed via ~/.config/containers/policy.json (no sudo)
Error: ... no image found in image index for architecture arm64, ... OS darwin
  -> fixed via buildah --platform linux/arm64
=== after both fixes ===
buildah --platform linux/arm64 from docker.io/library/alpine:latest
  -> exit 0, 27.2s (one transient TLS retry)
```

## Implications for the plan

- **`isolation.md`'s Tier 2 needs an explicit "no warm pool on macOS via
  libkrun" caveat.** The entire fast-restore story that makes Tier 2 viable
  on Linux does not transfer. If macOS Tier 2 is ever required, it needs
  either a different backend or an explicit "cold boot only, plan
  accordingly" caveat — not a silent assumption that "microVM" means the
  same performance profile everywhere.
- **`srt` is a credible, fast-to-adopt Tier 1 on macOS** — but slower than
  its Linux counterpart (gVisor) by a meaningful margin, and the
  per-call proxy setup cost is architecturally real, not a fluke. The
  `--control-fd` persistent mode is worth checking before accepting 152ms
  as final — directly mirrors the subprocess-vs-persistent-process lesson
  from the prx latency spike.
- **Homebrew packaging friction for `krunvm` is real integration cost**,
  not just "a few extra install steps" — three separate config/platform
  mismatches had to be diagnosed and fixed by hand before even reaching a
  basic image pull. If Fabrica ships a macOS Tier 2 backend, this friction
  either needs to be absorbed into Fabrica's own setup tooling, or a
  different macOS backend (Apple's own Containerization framework, not
  tested here) may integrate more cleanly.
- **Anthropic's Windows `srt` mode is a real, unplanned lead** for the
  still-open Windows Tier-1 gap from `landscape.md §3a` — worth a dedicated
  follow-up rather than treating Windows as unsolved by default.

## What was NOT explored

- **`krunvm create`/`start` end-to-end** — stopped at the timebox with raw
  `buildah` pull succeeding but `krunvm`'s own wrapper unconfirmed.
- **`srt`'s `--control-fd` persistent mode** — could substantially change
  the 152ms latency finding; not tested.
- **Apple's Containerization framework** (mentioned in `landscape.md §3a`
  as a Tier-2 alternative to libkrun) — not attempted at all this round.
- **`srt`'s Windows mode** — not tested, Windows environment not available
  here.
- **Civitas integration shape** — same open question as the Firecracker/prx
  spikes: whether either tool wants a subprocess-per-call wrapper or a
  persistent supervised child was not built or measured, only inferred from
  `srt`'s `--control-fd` hint.

## Recommendation

**`srt` is ready to prototype against as Tier 1 on macOS now** — real,
working, measured. **libkrun/krunvm as Tier 2 needs either more setup-time
investment (finish the `krunvm`-level platform-override problem) or a
serious look at Apple's Containerization framework as an alternative**,
and either way, the lack of snapshot/restore must be treated as a known,
permanent limitation of this path, not a gap to "fix" — it may simply not be
available on macOS via this route. Recommend a short, tightly-scoped
follow-up spike specifically on `srt --control-fd` (cheap, high-signal) before
investing further in `krunvm`'s deeper packaging issues.
