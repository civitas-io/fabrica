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

**Conclusion:** tier the `Sandbox` protocol; gVisor as safe default, Firecracker as
prod target, managed adapters (E2B/Modal) as zero-ops path.

## 4. Memory — mature, wrap don't build

Mem0 (OSS, widely deployed), Zep (temporal graph; **self-host CE deprecated Apr 2025,
cloud-only**), Letta/MemGPT (full runtime), Cognee (OSS graph+vector+relational),
LangMem (LangChain SDK). No single winner → `MemoryStore` protocol + adapters.

## 5. Skills — standardizing fast, unowned by runtimes

Claude Agent Skills (Oct 2025); open cross-platform `SKILL.md` standard (Dec 2025);
AGENTS.md under the Linux Foundation Agentic AI Foundation (Anthropic/Google/
Microsoft/OpenAI). Progressive disclosure is the shared pattern. **No agent runtime
owns skill loading** → Fabrica's strongest near-term product; conform to `SKILL.md`.

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
reimplementing vector retrieval. See [tool-execution.md](tool-execution.md#build-vs-wrap-the-retrieval-backend)
for the resulting package split.

## Sources

Selected grounding sources (via Vertex Gemini Google Search, Aug 2026):
anthropic.com, cloudflare.com, simonwillison.net, workos.com, docker.com, ibm.com,
composio.dev, github.com (E2B, firecracker), fly.io, modal.com, northflank.com,
letta.com, getzep.com, mem0.ai, arxiv.org. Redirect URLs captured in the research
session log; re-run before publishing any external-facing claim.
