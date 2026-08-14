# Contract: `ManagedSandboxAdapter` (E2B / Modal / AWS / Azure / GCP)

**Status:** Contract — interfaces designed, implementation deliberately
SECOND PRIORITY behind self-hosted Firecracker (`isolation.md` open question
1, re-decided: self-hosted is genuinely free per-execution, every managed
provider bills per call) · also blocked on API credentials, see `HANDOFF.md`
· **Last updated:** 2026-08
**Depends on:** [contracts/sandbox.md](sandbox.md) (`Sandbox` Protocol this
implements), [isolation.md](../isolation.md) open question 1 (build-sequencing
decision this contract exists to execute), [landscape.md §3a](../landscape.md)
(the real provider research this contract is grounded in)

---

## The problem this contract exists to solve

`contracts/sandbox.md`'s `Sandbox.execute(handle, code, *, on_tool_call, timeout)`
assumes `on_tool_call` — an in-process Python coroutine — is reachable from
inside the running sandbox. For `SubprocessSandbox` this is real and already
built: a ZMQ `ipc://` socket, same host, same filesystem namespace. A
self-hosted Firecracker backend would use `vsock` the same way — still the
same physical host.

**None of the five real managed providers researched in
[landscape.md §3a](../landscape.md) can satisfy this as written.** E2B, Modal,
AWS Bedrock AgentCore, Azure Container Apps Dynamic Sessions, and GCP Agent
Sandbox all run the guest in a genuinely separate network — someone else's
cloud, not Fabrica's host machine. There is no local socket path of any kind
between the sandboxed code and Fabrica's own process. This is a real,
structural gap found by trying to plan the managed-adapter work concretely,
not a minor porting detail — every `ManagedSandboxAdapter` needs a
fundamentally different `on_tool_call` bridge than every `Sandbox`
implementation built so far.

## Resolved: a network callback bridge, not a local one

Every provider researched supports real outbound network access from the
sandbox (several with explicit domain/CIDR allowlisting built for exactly
this shape of use case — Modal's `outbound_domain_allowlist`, for one).
**Resolved: Fabrica exposes a real, short-lived HTTP endpoint per execution,
and the code injected into the remote sandbox POSTs tool-call requests to
it instead of calling a local socket.** This is the same fundamental shape
`FabricaMCPServer` already uses (a real HTTP server, `uvicorn`) — not a new
transport family for this codebase, an extension of one that already works.

```python
class CallbackBridge:
    """Shared by every ManagedSandboxAdapter implementation -- the actual
    network callback mechanism, built once, not reinvented per provider.
    Each adapter's execute() composes one of these instead of implementing
    its own HTTP server.
    """

    def __init__(self, *, host: str, port: int) -> None:
        """host/port must be reachable FROM the managed provider's network
        -- a real deployment requirement, not hidden. For a Fabrica
        deployment with a real public or VPC-peered address, this is
        immediate. For local development (a laptop with no public
        address), this requires a tunnel (ngrok, Cloudflare Tunnel, or
        equivalent) -- the SAME pattern GKE Agent Sandbox's own docs use
        for local dev (`kubectl port-forward`), not a workaround unique
        to Fabrica.
        """

    async def start_for_run(self, on_tool_call: ToolCallCallback) -> tuple[str, str]:
        """Starts listening (or registers a route on an already-running
        listener) for exactly one execution. Returns (callback_url,
        run_token) -- run_token is a real, unguessable per-run secret
        (not a predictable sequence), required as a bearer token on every
        inbound request to this run's callback path.

        SECURITY, matching this platform's fail-closed-by-default pattern
        (NullPresidiumClient.allow_ungoverned, MCPClient.allow_unsandboxed,
        FabricaMCPServer's WeakIsolationError): without a real, unguessable
        per-run token, ANY network caller who discovers the callback URL
        could invoke arbitrary registered tools by guessing it. The token
        is not optional, not configurable to be skipped -- there is no
        allow_* escape hatch for this one, since there is no legitimate
        "run without it" use case the way there is for governance/
        isolation strength.
        """

    async def stop_for_run(self, run_token: str) -> None:
        """Tears down this run's route/token -- called unconditionally in
        execute()'s finally block, whether the run succeeded, failed, or
        timed out. A leaked, still-valid callback route after the sandboxed
        code has finished would be a real, lingering attack surface.
        """

    def inject_namespace_shim(self, code: str, *, callback_url: str, run_token: str) -> str:
        """Prepends a small, provider-agnostic shim to the user's code that
        makes namespace.call(tool, params) issue a real HTTP POST to
        callback_url with run_token as a bearer token, and parse the JSON
        response -- the remote-execution equivalent of
        fabrica/sandbox/_guest_shim.py's local ZMQ version. One shim,
        reused by every ManagedSandboxAdapter -- not reimplemented per
        provider, since the wire protocol (HTTP POST, JSON body, bearer
        auth) is the same regardless of which cloud is running the code.
        """
```

## `TunnelProvider` — resolved, a clean interface, three backends, in priority order

`CallbackBridge.__init__`'s `host`/`port` need to be reachable FROM the
managed provider's cloud. For a Fabrica deployment with a real public or
VPC-peered address, this is direct — no tunnel needed. For local dev or a
private/NAT'd deployment (a laptop, a homelab), it is not — resolved with a
small, swappable `TunnelProvider` Protocol, same shape as every other
pluggable backend in this codebase (`Sandbox`, `RetrieverBackend`,
`PromptStore`'s `BlobStore`):

```python
class TunnelProvider(Protocol):
    """Establishes a real, externally-reachable URL for a local
    CallbackBridge port -- swappable, never hardcoded to one tool.
    CallbackBridge composes one of these when host/port aren't already
    publicly reachable; when they are (a real deployment with a public or
    VPC-peered address), no TunnelProvider is needed at all.
    """

    async def start(self, *, local_port: int) -> str:
        """Establishes the tunnel. Returns the externally-reachable URL
        that resolves to local_port -- this becomes CallbackBridge's
        externally-advertised callback_url, not local_port's own bare
        address."""
        ...

    async def stop(self) -> None:
        """Tears down the tunnel. Called whenever CallbackBridge itself
        shuts down -- not per-execution; a tunnel is a longer-lived
        resource than one run's callback route (`start_for_run`/
        `stop_for_run` operate underneath it, on the same underlying
        tunnel)."""
        ...
```

**Three backends, in the decided priority order: Tailscale, then Cloudflare,
then ngrok.** Real, provider-specific detail worth stating precisely, not
glossed over:

1. **`TailscaleTunnelProvider`** — needs **Tailscale Funnel** specifically
   (`tailscale funnel <port>`), not plain tailnet membership. Plain
   Tailscale only makes a service reachable to OTHER devices on the same
   tailnet — a managed provider's sandbox (E2B/Modal/AWS/Azure/GCP's own
   cloud) is never a member of anyone's tailnet, so it could never reach a
   plain Tailscale address at all. Funnel specifically exposes a tailnet
   service to the public internet over HTTPS — the actual capability this
   contract needs. First priority since it's already part of this
   project's own infrastructure story (the homelab used for the
   Firecracker spikes is reachable this way) and needs no separate
   third-party account.
2. **`CloudflareTunnelProvider`** — `cloudflared tunnel run` (or the
   quick-tunnel mode, no named tunnel/account setup required for local
   dev). Second priority: free, no account strictly required for a
   throwaway quick tunnel, widely used for exactly this local-dev-callback
   shape already (this is the same category of tool GKE Agent Sandbox's
   own docs use `kubectl port-forward` for, just for a non-Kubernetes
   deployment).
3. **`NgrokTunnelProvider`** — `ngrok http <port>`. Third priority: the
   most widely known option, but requires a free-tier account/authtoken
   for anything beyond a very short-lived session in current ngrok
   versions, a real adoption-friction difference from the other two worth
   naming, not the reason it's ranked third by default preference alone.

None of these three are implemented yet — the `TunnelProvider` Protocol
itself is the resolved, durable part; which concrete backend a given
deployment uses is a runtime choice, not a build-time one.

## `ManagedSandboxAdapter`

```python
class ManagedSandboxAdapter(Protocol):
    """Implements contracts/sandbox.md's Sandbox Protocol exactly -- the
    same shape SubprocessSandbox already satisfies. ToolManager/SkillManager
    cannot tell a ManagedSandboxAdapter apart from any other Sandbox
    backend; the network-callback machinery above is entirely internal to
    each implementation's execute(), never visible at the Sandbox Protocol
    boundary itself.
    """

    @property
    def tier(self) -> int:
        """Always 2 -- every provider researched offers hardware-grade
        isolation (Firecracker, gVisor, or Hyper-V), meeting
        WeakIsolationError's Tier-2 bar without an opt-in, unlike
        SubprocessSandbox today."""
        ...

    async def boot_clean(self) -> SandboxHandle:
        """Maps to the provider's own fast-create-from-template/snapshot
        mechanism (E2B: Sandbox.create(template=...); Modal:
        Sandbox.create(image=...); Azure: a session pool claim; AWS: a
        code_session start; GCP: a SandboxWarmPool claim) -- NOT a
        from-scratch cold boot. This is philosophically consistent with
        SandboxPool's own warm-pool design (contracts/sandbox.md): Fabrica
        still owns warm_size/max_concurrent bookkeeping, but the actual
        "fast restore to clean state" mechanism is delegated entirely to
        the provider's own snapshot/template system, not reimplemented --
        the same "wrap, don't build" thesis applied to isolation
        infrastructure specifically, for the first time.
        """
        ...

    async def execute(
        self, handle: SandboxHandle, code: str, *, on_tool_call: ToolCallCallback, timeout: float
    ) -> RunResult:
        """Composes a CallbackBridge for the duration of this one call:
        start_for_run() -> inject_namespace_shim() -> submit the shimmed
        code to the provider's real run/exec primitive (E2B:
        commands.run(); Modal: exec(); Azure: POST .../executions; AWS:
        code_session execution; GCP: sandbox.commands.run()) -> await the
        provider's result -> stop_for_run() in a finally block,
        unconditionally.
        """
        ...

    async def terminate(self, handle: SandboxHandle) -> None:
        """Maps to the provider's own teardown call (E2B: kill(); Modal:
        terminate(); Azure: DELETE .../session; AWS: closing the
        code_session; GCP: releasing the SandboxClaim). Called by
        SandboxPool on every release() -- SandboxPool's own
        always-terminate-never-reuse rule (contracts/sandbox.md) applies
        identically here; a managed sandbox that ran arbitrary code is
        never handed to a different task or agent, regardless of how
        cheap the provider makes reuse look.
        """
        ...

    async def health_check(self) -> bool:
        """A lightweight is-alive check (E2B: is_running(); Modal: poll();
        Azure/AWS/GCP: a session-status query) -- never a real code
        execution, matching contracts/sandbox.md's existing intent for
        this method on SubprocessSandbox."""
        ...
```

## A cost dimension every other `Sandbox` backend so far has not had

Every `boot_clean()`/`execute()` call against a managed provider is a real,
billed API call — unlike `SubprocessSandbox` (free, local) or a self-hosted
Firecracker fleet (amortized infrastructure cost, not per-call). This is
directly relevant to Marcus's own stated success metric
(`problem-definition.md`: *"a bad run can't... blow the budget"*) in a way
no other backend built so far has been: **an uncapped, runaway warm-pool
refill loop against a managed adapter is a real, uncapped dollar cost, not
just a resource-exhaustion risk.** `SandboxPool`'s existing bounded-overflow
design (`warm_size`/`max_concurrent`) already caps concurrent usage, but this
is worth stating explicitly as a reason those bounds matter *more*, not
differently, for a `ManagedSandboxAdapter` — not a new mechanism, a
sharper reason the existing one is load-bearing here.

## Open items for implementation

1. ~~Which provider ships first is not yet decided.~~ **Re-scoped, not
   fully resolved**: managed-provider implementation is now second
   priority overall, after self-hosted Firecracker (`isolation.md` open
   question 1). When that phase starts, E2B remains the most narrowly
   agent-focused, cheapest-to-integrate option (Firecracker underneath --
   the same isolation tech `isolation.md` targets for self-hosting,
   making its behavior the most directly comparable); AWS/Azure/GCP
   matter for enterprise IAM-native procurement, a real, distinct axis
   (see `landscape.md §3a`). Still not decided which ships first within
   that later phase; tracked in `HANDOFF.md`.
2. ~~The local-dev tunnel requirement is real, not hidden, and not yet
   chosen.~~ **Resolved**: a `TunnelProvider` Protocol (this doc's own
   section above), three concrete backends in a decided priority order --
   Tailscale (specifically Funnel, not plain tailnet membership --
   real distinction, see above), then Cloudflare Tunnel, then ngrok.
   None implemented yet; the Protocol and priority order are the
   resolved, durable part.
3. ~~Per-provider network-egress-allowlist configuration is
   provider-specific and not designed here.~~ **Resolved: explicitly
   deferred, per-provider, not designed generically.** Each provider's
   config shape differs enough (Modal's `outbound_domain_allowlist` has
   no obvious generic equivalent across all five) that designing one
   now, before any single provider is actually being implemented, would
   be speculative generality with nothing real to validate it against --
   same "ship the default, revisit if forced" logic used throughout this
   project. Decide the exact config surface when whichever provider
   ships first is actually being built, not before.
4. **No credentials exist to build/test any of this for real yet** (same
   shape as `PresidiumClient`'s blocker, see `HANDOFF.md`) — this contract
   was written from real, current, sourced provider documentation, not
   assumed from memory, but has zero implementation or live-API validation
   behind it.
5. ~~`RunResult.stdout`-only return shape may be a worse fit here than for
   `SubprocessSandbox`.~~ **Resolved: stays stdout-only for now,
   explicitly.** Several providers (E2B, Azure) have first-class
   support for returning richer results (files, images, binary/structured
   data) that today's contract discards -- flagged as a real, NAMED
   future extension (binary/file return support), not designed or built
   now. `RunResult`'s stdout-only shape was itself a deliberate,
   spike-validated decision (`SPIKE-code-mode-execution.md`); extending
   it to carry binary/file results is real future work that needs its
   own design pass when it's actually prioritized, not assumed to be a
   drop-in addition that can't undermine what the spike already validated.
