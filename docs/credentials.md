# Credentials: why they never cross the `Sandbox` boundary

**Status:** Design, validated by spike · **Last updated:** 2026-08
**Depends on:** [isolation.md](isolation.md) (the `Sandbox` protocol this decision
constrains), [tool-execution.md](tool-execution.md) (the `on_tool_call` seam this
relies on)
**Evidence:** [SPIKE-tessera-credential-integration.md](../specs/archive/spikes/SPIKE-tessera-credential-integration.md)

---

## The gap this closes

`problem-definition.md` names credential handling as a touchpoint for two
personas: Marcus's "zero credential leakage" success metric, and Elena's
"credential injection into the sandbox." A self-reflection audit
(`self-reflection-report.md §3.3`) found no such mechanism existed anywhere
in the codebase — not even a design sketch, just a name in `HANDOFF.md`'s
deferred-items list.

Before designing one, a real, working, independently-built project already
solves the adjacent problem: **[Tessera](https://github.com/civitas-io/tessera)**
(`tsr`), an agent-blind credential broker already part of the Civitas
toolchain, already named in `context-layer.md`'s "does NOT own" table
(`Credential vault / token exchange | Presidium (+ tessera at CLI)`). The
real question wasn't "how should Fabrica inject credentials" — it was
"does Fabrica need to inject credentials into anything at all."

## The decision

**Fabrica builds no credential-injection mechanism into `Sandbox`.**
Credentials never cross the sandbox boundary, at any tier. This is not a
scope cut — it is the *correct* architecture, for a reason external
validation makes concrete, not just asserted:

**Tessera's own security model structurally refuses to inject a secret into
an interpreter running caller-supplied code** (`tsr`'s interpreter/
exec-wrapper denylist — `python`, `bash`, `node`, etc. — exit 67,
non-overridable by any policy). `Sandbox.execute()` runs exactly that: a
Python interpreter executing arbitrary, model-generated code. Two
independently-designed systems, built for different purposes, arrived at
the identical rule: **an interpreter about to run untrusted code must never
hold a raw secret in its own process memory.** If Fabrica had built its own
credential-injection mechanism for `Sandbox`, it would have needed to
either violate this rule or reinvent Tessera's own reasoning for why not to.
Neither is worth doing.

## The real, validated pattern

Credentialed work happens entirely on the **host side** — the same place
`on_tool_call` already runs today, outside the sandbox, per the existing
code-mode architecture (`tool-execution.md`, `system-design.md §3`). Two
integration paths, both real:

1. **A tool author writes an `on_tool_call` implementation that shells out
   to `tsr exec`/`tsr http` directly.** No Fabrica involvement beyond the
   `ToolNamespace` protocol that already exists — the credentialed HTTP
   call or subprocess run happens inside ordinary host-side Python, using
   `tsr` as a subprocess the way any other CLI tool would be used.
2. **Register Tessera's own MCP server (`tsr mcp`) as an ordinary
   `MCPToolNamespace`, via Fabrica's existing `MCPClient`.** Validated end
   to end in the spike above: model-generated code running inside a real
   `SubprocessSandbox` called `namespace.call('ls', {})`; only the tool's
   redacted JSON result crossed back into the sandbox. The sandboxed
   process never had `TSR_PASSPHRASE`, the store path, or any secret value
   in its own environment or memory at any point — those existed only
   inside the separate `tsr mcp` subprocess, launched and owned by the
   host-side `MCPClient`.

Path 2 requires **zero new Fabrica code** — the exact composition validated
in the spike is available today, to any Fabrica user, with nothing more
than an `MCPServerConfig` pointing at a real `tsr` binary.

## What this means for the personas

- **Marcus's "zero credential leakage" metric is now actually achievable**,
  not aspirational: since no secret is ever injected into the sandbox at
  all, there is no leakage path through the sandbox boundary to defend
  against in the first place. The remaining surface (a compromised
  *host-side* tool implementation) is Tessera's own threat model, already
  documented in `tessera/docs/DESIGN.md`, not a new Fabrica concern.
- **Elena's "credential injection into the sandbox" touchpoint is
  resolved by *not building one*** — the audit/governance value comes from
  Tessera's own append-only audit log (`tsr audit`) plus Fabrica's own
  `fabrica.presidium.check_grant` span (`system-design.md §7`) gating the
  `namespace.call()` that reaches Tessera in the first place, not from a
  new Fabrica-side credential ledger.

## The honest gap this does NOT close

**Tessera's approval model requires a human present** — `/dev/tty` or
Touch ID — and `tsr mcp` **refuses to start** under
`TSR_NONINTERACTIVE=1`. There is currently no unattended, service-mode
production story for credentialed tool calls. This is real, and matters
for Marcus's persona specifically (production, not just local dev), but it
is **Tessera's gap to close, not Fabrica's** — nothing about Fabrica's own
architecture blocks it; a future Tessera session-broker daemon or an
alternative non-interactive-but-still-audited approval mode would close it
without Fabrica changing anything on its side. Tracked here so it isn't
silently assumed solved, not tracked as Fabrica work.

## What this contract deliberately does not cover

- **Which specific secrets a given deployment needs** — entirely a matter
  of what tools/skills a deployment registers; Fabrica has no opinion.
- **Tessera's own approval UX, backend choice (scrypt vs. keychain), or
  roadmap** — external to this project, referenced, not owned.
- **A first-party "credentials" `ToolNamespace` shipped by Fabrica** —
  deliberately not built; the MCP integration path already covers this
  without Fabrica needing to own a credential-specific abstraction.
