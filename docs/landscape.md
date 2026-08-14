# Competitive Landscape

**Status:** Research snapshot · **Captured:** 2026-08 (facts current to ~Dec 2025)
**Method:** Google Search grounding via Vertex Gemini. Treat vendor claims as vendor
claims; re-verify before major bets.

---

## 1. Tool retrieval / context reduction — the space moved

| What | Who | When | Bearing on Fabrica |
|---|---|---|---|
| **Tool Search Tool** (`defer_loading`, bm25/regex variants, `advanced-tool-use-2025-11-20`) | Anthropic | 24 Nov 2025 | **Commoditizes RFC 0001's `find_tools`.** 190K+ tokens saved. Claude-only. |
| **Code execution with MCP / Code Mode** (tools as filesystem of code APIs, sandboxed, intermediate results kept out of context) | Anthropic + Cloudflare | Nov 2025 | **The new frontier.** ~150K→~2K tokens (98.7%). Cloudflare = Workers-locked; Anthropic = Claude-locked. Fabrica's opening: vendor-neutral + self-hosted + runtime-native sandbox. |
| RAG-MCP and tool-retrieval research | academia | 2025 | validates the thesis |

**Conclusion:** don't ship `find_tools` as the headline; ship tools-as-code +
sandboxed execution, keep `find_tools` as fallback.

## 2. MCP gateways / registries — commoditized, do not build

| Product | Type | Selective retrieval? |
|---|---|---|
| Docker MCP Toolkit/Catalog | registry + gateway | dynamic discovery/composition |
| IBM ContextForge | OSS gateway/registry/proxy (MCP+A2A+REST) | proxy/registry |
| Kong AI Gateway | product gateway | proxy/governance |
| Cloudflare (remote MCP + Code Mode) | product | yes (Code Mode) |
| Azure API Management (MCP) | product | proxy/governance |
| Portkey | AI gateway | proxy |
| LiteLLM | OSS LLM gateway | proxy (+MCP bridge planned) |
| Smithery / Glama | registry + gateway | registry-first |
| Composio | universal MCP gateway | yes (intent → matching tools) |
| **agentgateway** | OSS AI-native proxy (LLM+MCP+A2A) | proxy/governance — **already wrapped by Presidium** |
| Official **MCP Registry** | OSS catalog/API | registry (preview 8 Sep 2025) |

**Conclusion:** a generic MCP gateway has no moat and Presidium already owns the
*governed* one. Fabrica does not build this.

## 3. Sandbox / isolation for agent code — the differentiator space

| Option | Isolation | Cold start | AI-sandbox use | Grade |
|---|---|---|---|---|
| Subprocess | none/OS | ~0 | dev only | — |
| **gVisor** | user-space kernel | ~100 ms | Modal, Google Agent Sandbox, Beam | prod |
| **Firecracker** | microVM (KVM), own kernel | 125 ms boot; ~4 ms restore | AWS Lambda, E2B, Fly.io, Vercel Sandbox, Replit | **prod** |
| **Kata Containers** | microVM in k8s | 60–150 ms | Northflank | prod |
| Cloud Hypervisor | microVM (Rust VMM) | ~200 ms | VMM under Kata | prod |
| V8 isolates (Cloudflare) | JS isolate | ms | Dynamic Workers | prod (JS-centric) |
| **Managed:** E2B / Modal / Daytona | Firecracker / gVisor / Kata | sub-30 ms (E2B snapshots); sub-90 ms (Daytona) | purpose-built agent sandboxes | prod |

Firecracker specifics for self-hosting: `jailer` (cgroups+namespaces+seccomp+chroot),
REST API over unix socket, `vsock` host↔guest, snapshot/restore + UFFD lazy loading
for warm pools, ext4 rootfs + `vmlinux`. E2B orchestrates with Nomad/Consul; Fly.io
with `flyd` + `containerd`. Requires KVM (bare-metal or nested virt).

### 3a. The hyperscalers ship this too, now -- not just startups

Researched directly (real docs fetched, not assumed from memory -- this space
moves fast), since the user's own instinct was right: **enterprises may
genuinely prefer a hyperscaler-native option over a third-party vendor
relationship**, for a reason distinct from isolation quality: AWS/Azure/GCP's
offerings authenticate through the exact same IAM/Entra/Workload-Identity
systems an enterprise already uses for everything else, where E2B/Modal
require a separate API-key vendor relationship. That's a procurement/security-
review axis, not a technical one, and it matters independently of latency or
isolation strength.

| Provider | Product | Shape | Isolation | Auth |
|---|---|---|---|---|
| **AWS** | Bedrock AgentCore Code Interpreter | Managed sessions, `code_session` context manager, pre-built per-language runtimes, results as streams. Default 15 min execution, extendable to 8 hours. | AWS-managed containers | IAM |
| **Azure** | Container Apps Dynamic Sessions | REST API over `https://<region>.dynamicsessions.io/...`, session-pool based (`identifier` reused across calls to continue a session), `POST .../executions` with inline code, prewarmed session pools | **Hyper-V** per session | Microsoft Entra (OAuth) tokens, `aud` claim `https://dynamicsessions.io` |
| **GCP** | Agent Sandbox (GKE) | **Not a simple SaaS call** -- Kubernetes-native: `SandboxTemplate`/`SandboxWarmPool` CRDs you deploy into your own GKE cluster, a Python client (`k8s-agent-sandbox`) that talks to an in-cluster Sandbox Router. Closer to "self-hosted, but on GCP's infrastructure" than to E2B/Modal/Azure's shape. | gVisor (`runtimeClassName: gvisor`, enforced by GKE Validating Admission Policies) | Kubernetes RBAC / Workload Identity Federation |
| **GCP** (alternative) | Vertex AI Agent Engine Code Execution | Fully managed sandbox-as-a-service, closer in shape to AWS/Azure's offerings than GKE Agent Sandbox -- in preview as of late 2025, not yet evaluated in depth here | Google-managed | IAM |

**A real architectural finding, not just a landscape note**: none of these five
providers (E2B, Modal, AWS, Azure, GCP) can satisfy Fabrica's *current*
`Sandbox` Protocol as written -- `execute(handle, code, on_tool_call, timeout)`
assumes `on_tool_call` is an in-process Python callback reachable over a local
socket (real ZMQ `ipc://` for `SubprocessSandbox`; `vsock` for a hypothetical
self-hosted Firecracker backend). A remote managed sandbox runs in someone
else's cloud with no such local socket path back to Fabrica's own process at
all. Every one of these providers DOES support outbound network access from
the sandbox (several with explicit domain/CIDR allowlisting built for exactly
this kind of callback -- e.g. Modal's `outbound_domain_allowlist`), so the real
fix is a network-reachable callback path, not a local one -- see
[contracts/managed-sandbox.md](contracts/managed-sandbox.md) for the actual
design. This is a genuine contract change, found by trying to plan the
managed-adapter work concretely, not a minor implementation detail.

**Conclusion:** tier the `Sandbox` protocol; gVisor as safe default, self-hosted
Firecracker as the long-term prod target. **Build sequencing, resolved, then
RE-resolved** (see [isolation.md](isolation.md) open question 1 for the full
reasoning trail): a managed-sandbox adapter was first decided to ship FIRST
(smaller build, real isolation sooner) -- **then reversed, after direct
discussion, in favor of self-hosted Firecracker shipping first instead**.
Deciding factor: self-hosted Firecracker is genuinely FREE per-execution;
every one of the five managed providers researched bills per API call
(`contracts/managed-sandbox.md`'s cost section). Combined with a real, live
homelab already available for the self-hosted build
(`kodiak@darkenergy` -- confirmed still live, with the entire prior
Firecracker spike environment intact), the original effort-asymmetry
argument no longer dominates. **Managed provider interfaces are still
designed now** (`ManagedSandboxAdapter`, `contracts/managed-sandbox.md`) --
only their implementation moves to second priority, after self-hosted
Firecracker is real. Which specific managed provider ships once that phase
starts remains open -- see `HANDOFF.md`.

## 4. Memory — mature, wrap don't build

Mem0 (OSS, widely deployed), Zep (temporal graph; **self-host CE deprecated Apr 2025,
cloud-only**), Letta/MemGPT (full runtime), Cognee (OSS graph+vector+relational),
LangMem (LangChain SDK). No single winner → `MemoryStore` protocol + adapters.

## 5. Skills — standardizing fast, unowned by runtimes

Claude Agent Skills (Oct 2025); open cross-platform `SKILL.md` standard (Dec 2025);
AGENTS.md under the Linux Foundation Agentic AI Foundation (Anthropic/Google/
Microsoft/OpenAI). Progressive disclosure is the shared pattern. **No agent runtime
owns skill loading** → Fabrica's strongest near-term product; conform to `SKILL.md`.

## 3a. Cross-platform isolation — the current tiers are Linux-only, and that's a real gap

**Reframing, from a direct product conversation:** Fabrica's isolation story
should not be "we use Firecracker." It should be "users get well-isolated
sandbox execution on whatever platform they're on" — nobody adopting Fabrica
cares which specific VMM or sandboxing primitive is underneath, only that
their problem (safe execution of model-generated/tool code) is solved. The
`Sandbox` protocol in [isolation.md](isolation.md) is already backend-pluggable
by design — this section is about what actually plugs in on each OS.

**The uncomfortable finding: Tier 1 (gVisor) is Linux-only too, not just
Tier 2 (Firecracker).** gVisor reimplements Linux syscalls in a user-space
application kernel — there is no macOS or Windows syscall surface for it to
intercept. This means the *entire* tiered design beyond Tier 0 (bare
subprocess, no real isolation) is currently Linux-specific. A Priya-persona
developer on a Mac laptop has no upgrade path today beyond Tier 0 as
currently written.

**What actually exists per platform** (research, not yet spiked/measured):

| Platform | Tier-1-equivalent (lightweight syscall/access control) | Tier-2-equivalent (microVM-class isolation) |
|---|---|---|
| **Linux** | gVisor (validated — see landscape §3 above) | Firecracker (validated — see [SPIKE-firecracker-boot-restore-latency.md](../specs/archive/spikes/SPIKE-firecracker-boot-restore-latency.md)) |
| **macOS** | `sandbox-exec` / Seatbelt profiles — deny-by-default, VFS-layer interception. **Real precedent**: CrowdStrike already uses Seatbelt specifically for AI-agent code sandboxing. **Concretely validated**: Anthropic's own `srt` (Sandbox Runtime), built on `sandbox-exec` — real enforcement confirmed (write/network denial), measured p50 152ms ([SPIKE-macos-isolation-srt-libkrun.md](../specs/archive/spikes/SPIKE-macos-isolation-srt-libkrun.md)). Also ships an untested Windows mode (`windows-install`), a candidate for the Windows Tier-1 gap below. | **libkrun** (open source, `Virtualization.framework`-based, explicitly positioned for AI/agent isolation with "VM-level security, container-like footprint") — **confirmed via spike: no snapshot/restore support at all**, a permanent ceiling, not a bug. Or Apple's own **Containerization framework** (untested), described as similar in isolation strength to Kata. |
| **Windows** | No clean equivalent found. Windows containers/job objects are not in the same isolation class as gVisor/Seatbelt — an open gap. | Hyper-V isolation / Windows Sandbox — real and hypervisor-backed, but meaningfully heavier/slower to boot than Firecracker or libkrun (seconds-to-minutes, not milliseconds, per earlier research) |

Also relevant: **SlicerVM** — not a new primitive, but proof the underlying
philosophy (fast, real-Linux-kernel microVMs) is portable: it's built
directly on Firecracker for Linux, and re-hosted on Apple's
`Virtualization.framework` for Mac, and WSL2 for local Windows dev.

**Implication for `isolation.md` (flagged, not yet applied):** the `Sandbox`
protocol's tier table should be reframed as platform-dispatched rather than
naming Firecracker/gVisor as *the* Tier 1/2 implementations — e.g. "Tier 2:
microVM-class isolation (Firecracker on Linux, libkrun on macOS, Hyper-V
isolation on Windows as the current best-available, unresolved gap for
a Tier-1 equivalent)." The Windows Tier-1 gap in particular has no answer
yet and should be named as an open problem, not silently skipped.

**What was NOT explored:** none of the macOS/Windows options above have been
spiked or measured — this is desk research via search grounding, same
methodology as the rest of this doc, not hands-on validation like the Linux
Firecracker spike. Treat as a prioritized list of what to spike next if
cross-platform isolation becomes a real requirement, not a validated claim.

## 6. Tool search / retrieval backends — a two-tier market

Distinct from the "MCP gateway" question (section 2) — this is specifically about
the *retrieval technique* behind a `find_tools`-style meta-tool.

**Tier 1 — mature, but bundled or commercial:**

| Project | What | Backend | Maintained |
|---|---|---|---|
| **LlamaIndex** `ToolRetriever`/`ObjectIndex` | dynamic tool retrieval over a `VectorStoreIndex` | embeddings | actively, widely adopted |
| **LangChain** `retriever_tool` + `EnsembleRetriever` | dynamic tool selection | hybrid BM25 + embeddings | actively, widely adopted |
| **Composio** `COMPOSIO_SEARCH_TOOLS` | hosted meta-tool, discovers tools across a large app ecosystem | undisclosed | commercial, active |

All three require adopting a mega-framework (LlamaIndex/LangChain) or a commercial
hosted platform (Composio) to get this behavior.

**Tier 2 — smaller/standalone repos claiming RAG-over-tools or MCP-native search:**
several surfaced (RAG-MCP-style projects, various `mcp-*-rag` servers). **Treat with
skepticism** — grounding returned generic "actively maintained" claims attached to
commit dates that read as stale rather than current; none were independently
verified. Re-check any specific one directly before relying on it.

**The gap:** no small, standalone, **framework-agnostic, MCP-native** library was
found that does just "one `find_tools` meta-tool, aggregate multiple sources,
keyword-first with optional embeddings" — i.e. exactly RFC 0001's shape. The
*technique* is commoditized inside mega-frameworks or sold as a hosted product;
the *lightweight, unopinionated building block* is not.

**Conclusion:** build the interface/aggregation layer (real gap, low risk of
redundancy) — but wrap Tier 1's embedding engines as optional backends rather than
reimplementing vector retrieval. See [retrieval.md](retrieval.md#backends--rust-for-the-built-parts-wrap-everything-else)
for the resulting package split — now unified with skill discovery, not tool-only
as originally scoped here.

## Sources

Selected grounding sources (via Vertex Gemini Google Search, Aug 2026):
anthropic.com, cloudflare.com, simonwillison.net, workos.com, docker.com, ibm.com,
composio.dev, github.com (E2B, firecracker), fly.io, modal.com, northflank.com,
letta.com, getzep.com, mem0.ai, arxiv.org. Redirect URLs captured in the research
session log; re-run before publishing any external-facing claim.
