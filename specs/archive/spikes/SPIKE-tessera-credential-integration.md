# Spike: does Tessera resolve Fabrica's "credential injection into Sandbox" gap?

## Question

`self-reflection-report.md §3.3` flagged a real gap: no credential-injection
mechanism into `Sandbox` exists, despite being a named touchpoint for both
Marcus and Elena in `problem-definition.md`. Before designing one, the
question worth answering first: **does Tessera — a real, separately-built
agent-blind credential broker already part of the Civitas toolchain — already
solve this, without Fabrica needing any new subsystem at all?**

Two things needed checking, not assumed:

1. Is "inject a raw secret into the sandboxed process" even the right model,
   given Tessera's own security posture?
2. Does Fabrica's *existing* `MCPClient`/`MCPToolNamespace`/`ToolManager`
   stack actually compose with a real, unmodified `tsr mcp` process, or does
   this require new integration code?

## Result

**Both answered, and the answer changes the shape of the design more than
expected.**

1. **No** — Tessera's own interpreter/exec-wrapper denylist (`docs/DESIGN.md
   §4` in the `tessera` repo) structurally refuses (exit 67, non-overridable
   by any policy) to inject a secret into `python`/`bash`/`node`/etc.
   Fabrica's `Sandbox.execute()` is exactly that: a Python interpreter
   running arbitrary, model-generated code. Tessera would refuse this
   injection even if Fabrica built its own equivalent mechanism from
   scratch — the security posture on both sides agrees credentials must
   never reach an interpreter running untrusted code.
2. **Zero new integration code needed.** Fabrica's real, unmodified
   `MCPClient`/`MCPToolNamespace` connected to a real, unmodified `tsr mcp`
   subprocess, listed its real tool schemas, and successfully called `ls`
   through the full `ToolManager.run_code()` code-mode path — model-
   generated code running inside a real `SubprocessSandbox`, calling
   `namespace.call('ls', {})`, with only the tool's redacted JSON result
   crossing back into the sandbox's stdout.

## Setup

Real, unmodified binaries, not simulated:

- `tessera` repo (`~/workspace/projects/tessera`), built from source at the
  real `v0.6.1` tag: `cargo build --release` → `target/release/tsr`.
- An isolated, throwaway store (`TSR_STORE_DIR=/tmp/tsr-fabrica-demo`,
  `TSR_PASSPHRASE` set only for this spike) — see `setup_store.sh`.
- A real secret (`demo-api`) registered with an `http` policy pointing at
  `https://httpbin.org`.

## Findings

### `tsr mcp`'s real JSON-RPC protocol, driven directly first

Before touching Fabrica, a raw stdio JSON-RPC session against the real
binary confirmed the actual protocol shape (`initialize` → `tools/list` →
`tools/call`):

```
{"id":1,...,"result":{"capabilities":{"tools":{"listChanged":false}},"protocolVersion":"2024-11-05","serverInfo":{"name":"tessera","version":"0.6.1"}}}
{"id":2,...,"result":{"tools":[{"name":"http",...},{"name":"verify",...},{"name":"ls",...},{"name":"audit",...}]}}
{"id":3,...,"result":{"content":[{"text":"[{\"approval\":\"per-use\",\"created\":...,\"inject\":\"stdin\",\"name\":\"demo-api\"}]","type":"text"}],"isError":false}}
```

**`ls`/`audit` need no approval gate** — they're metadata-only, no secret
decryption required (`http`/`verify`/`exec` do, per Tessera's own gate).
This is what makes the rest of this spike runnable non-interactively; the
approval-gated tools would need a real human at a TTY or Touch ID, out of
scope for an automated spike, but ride the identical protocol path.

### Fabrica's real `MCPClient` against the real process — no adapter needed

```python
config = MCPServerConfig(
    name="tessera", transport="stdio",
    command=".../tsr", args=["mcp"],
    env={"TSR_STORE_DIR": ..., "TSR_PASSPHRASE": ...},
)
client = MCPClient(config)
await client.connect()
namespace = await MCPToolNamespace.create(client)
```

Real output — Fabrica's `MCPToolSchema` objects, populated from Tessera's
real tool descriptions, unmodified:

```
=== real schemas from a real tsr mcp process, via Fabrica's real MCPClient ===
  http: Make one authenticated HTTP request to the secret's allow-listed upstr...
  verify: Check whether a candidate value matches a stored secret. Requires out-...
  ls: List stored secret names and metadata (approval, injection, created). ...
  audit: Read the append-only audit log (actions and decisions; never secret va...
```

### The full code-mode path — the actual proof this spike was for

```python
tools = ToolManager(retriever, sandbox_pool, _AllowClient())
await tools.register(namespace)

code = (
    "result = namespace.call('ls', {})\n"
    "print(f\"secrets visible to the sandbox: {result['value']}\")\n"
)
result = await tools.run_code(code, agent_id="demo-agent", scope=Scope(agent_id="demo-agent"))
```

Real output:

```
=== code-mode result -- this is ALL that crossed back into the sandbox ===
success=True
secrets visible to the sandbox: [{"approval":"per-use","created":1786960445,"inject":"stdin","name":"demo-api"}]
```

The sandboxed process (a real, separate `SubprocessSandbox` child) never had
`TSR_PASSPHRASE`, `TSR_STORE_DIR`, or any secret value in its own
environment or memory — those exist only inside the `tsr mcp` subprocess,
launched and owned entirely by the **host-side** `MCPClient`, outside the
sandbox boundary. The sandboxed code only ever sees a tool name and a
redacted JSON result, exactly like any other registered tool.

## Implications for the design

- **Fabrica does not need a `Sandbox`-level credential-injection mechanism
  at all.** The correct architecture is that credentials never cross the
  sandbox boundary — real credentialed work happens entirely on the host
  side, either inside a hand-written `on_tool_call` implementation that
  shells out to `tsr exec`/`tsr http` directly, or — with zero new Fabrica
  code, as validated here — by registering `tsr mcp` as an ordinary
  `MCPToolNamespace`.
- This closes the "credential injection" half of `PLAN.md` item 6 as a
  **design decision + validated integration path**, not a new subsystem to
  build. See `docs/credentials.md` for the resulting design doc.
- The "usage/budget metering" half of item 6 is unrelated to this finding
  and remains open.

## What was NOT explored

- **The approval-gated tools** (`http`, `verify`, `exec`) — these require a
  real human at `/dev/tty` or Touch ID, not automatable in this spike.
  They ride the identical MCP protocol path `ls`/`audit` validated here, so
  the *plumbing* is not in question, only the interactive approval UX,
  which is Tessera's concern, not Fabrica's.
- **Service-mode / unattended production use.** Tessera's `tsr mcp` refuses
  to start under `TSR_NONINTERACTIVE=1` — there is currently no unattended
  approval story on Tessera's side. This is a real, honest gap for Marcus's
  persona, not resolved by this spike, and not Fabrica's to fix.
- **Real network egress from inside a Firecracker (Tier 2) sandbox.** This
  spike used `SubprocessSandbox` (Tier 0) throughout — the `on_tool_call`
  round trip crosses the sandbox boundary the same way regardless of tier
  (per `contracts/sandbox.md`'s existing design), so this is not expected
  to differ for Tier 2, but was not directly re-validated here.

## Recommendation

**Adopt this as the documented pattern, not a novel Fabrica feature.**
`docs/credentials.md` records the decision: Fabrica deliberately builds no
credential-injection mechanism into `Sandbox`; a real, working integration
path (Tessera's own MCP server, via Fabrica's existing `MCPClient`) already
exists and is validated end to end.
