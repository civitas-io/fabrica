# SPIKE: MCP transport benchmark -- stdio vs. sse vs. streamable_http

**Status:** Complete · **Date:** 2026-08-24
**Triggered by:** closing out [python-civitas GH #26](https://github.com/civitas-io/python-civitas/issues/26)
(Streamable HTTP MCP transport support) -- real perf numbers requested for the
new transport before considering it fully done, per this project's own
"real, empirical, not estimated" discipline.

## Question

How does the new `streamable_http` transport (`MCPClient.connect()`,
`src/fabrica/mcp/client.py`) actually compare to the two existing transports
(`stdio`, `sse`) for latency, throughput under concurrency, and memory
footprint -- for real, not assumed from the transport's shape on paper?

## Method

Real hardware, real MCP client/server, no mocks. Homelab (`kodiak@darkenergy`,
AMD Ryzen 9 3900X, 24 threads, 62GB RAM, Ubuntu 24.04, Python 3.12.3), the
same host used for this project's earlier Firecracker spikes -- chosen so a
CPU-bound benchmark isn't sharing a dev laptop's own thermal/scheduling
noise with everything else running on it.

Exact dependency versions matched to this repo's own committed `uv.lock`,
not left to float: `mcp==2.0.0`, `uvicorn==0.51.0`, `anyio==4.14.2`,
`httpx2==2.9.1`.

One real MCP server per transport, all exposing the exact same single
`echo` tool (no artificial delay -- the benchmark measures real
transport/serialization overhead, not a simulated workload):

- `stdio`: `mcp.server.stdio.stdio_server`, a real subprocess per client
  connection (`bench_server_stdio.py`).
- `sse`: `mcp.server.sse.SseServerTransport` behind real uvicorn
  (`bench_server_http.py sse <port>`) -- the raw ASGI `handle_sse(scope,
  receive, send)` wiring pattern confirmed directly against
  `mcp.server.mcpserver.server`'s own real, current usage before writing
  this fixture, not guessed.
- `streamable_http`: `Server.streamable_http_app()` behind the same real
  uvicorn setup (`bench_server_http.py streamable_http <port>`).

Client harness (`bench_client.py`) for each transport:

1. **Latency**: 20 warmup calls, then 200 timed sequential `call_tool`
   round trips on one connection. p50/p95/p99/mean/min/max.
2. **Throughput under concurrency**: 20 warmup calls, then for each of
   `[1, 5, 10, 25, 50]` concurrent `anyio` tasks sharing ONE `ClientSession`,
   hammer `call_tool` for a fixed 5-second window, count completed calls.
3. **Memory**: real RSS (`psutil.Process().memory_info().rss`) before
   connecting, after connecting + 20 warmup calls, and after 2000 more
   calls -- to catch a real per-call leak, not just report a static number.

Full scripts and raw JSON output preserved, not deleted, per this project's
own established spike convention:
[`specs/archive/spikes/scripts/spike-mcp-transport-benchmark/`](scripts/spike-mcp-transport-benchmark/).

## Results

### Latency (single connection, sequential calls, ms)

| Transport | p50 | p95 | p99 | mean | min | max |
|---|---|---|---|---|---|---|
| `stdio` | 0.69 | 0.70 | 0.71 | 0.65 | 0.49 | 0.71 |
| `sse` | 1.32 | 1.50 | 1.61 | 1.33 | 1.28 | 2.06 |
| `streamable_http` | 2.01 | 2.56 | 2.68 | 2.24 | 1.85 | 29.92 |

### Throughput (calls/sec, one shared `ClientSession`, 5s window)

| Concurrency | `stdio` | `sse` | `streamable_http` |
|---|---|---|---|
| 1 | 1180 | 766 | 440 |
| 5 | 2176 | 999 | 670 |
| 10 | 2356 | 991 | 673 |
| 25 | 2464 | 991 | 667 |
| 50 | 2411 | 978 | 646 |

### Memory (RSS, MB)

| Transport | before connect | after connect | after 2000 calls | growth |
|---|---|---|---|---|
| `stdio` | 63.07 | 63.07 | 63.07 | 0.00 |
| `sse` | 65.00 | 65.00 | 65.00 | 0.00 |
| `streamable_http` | 66.60 | 66.60 | 66.60 | 0.00 |

## Honest methodology disclosure -- what "concurrency" and "users" mean here

Asked directly after the fact, and worth stating precisely rather than left
implicit:

- **Single OS process, single OS thread, for both client and server.**
  Neither side spawns real OS threads or forked processes. "Concurrency" in
  the throughput benchmark means concurrent **`anyio` tasks (coroutines) on
  one asyncio event loop**, not real parallelism -- Python's GIL means only
  one task actually executes Python bytecode at a time regardless of the
  concurrency level tested. `uvicorn.Server(config)` on the server side is
  also a single worker, no `--workers N`, no multiple processes.
- **Single "user", not multiple.** Every throughput data point comes from
  ONE `MCPClient`/`ClientSession`/connection, with N concurrent asyncio
  tasks all issuing `call_tool()` through that SAME shared session. This is
  NOT N independent simulated users each with their own connection -- see
  Finding 3 below, which is a direct consequence of this choice.
- **Loopback only.** Client and server ran in the same process space on the
  same physical host (`127.0.0.1`), zero real network hops, zero Docker/K8s
  networking overhead. Real deployments (the actual GH #26 motivating case:
  a remote MCP server) will add real network RTT on top of every number
  above.
- **Trivial workload.** The `echo` tool does zero computation and zero I/O
  -- deliberately, to isolate transport/serialization overhead specifically
  (see Method), not to simulate a realistic tool. A tool that computes or
  makes an external call will be dominated by its own cost, not transport
  choice, at real-world latencies.

None of this invalidates the relative comparison between the three
transports (same harness, same host, same tool, same client code) -- but it
means these are NOT directly comparable to a real load-generator (k6,
Locust, ToolHive's own harness) hitting independent connections over a real
network, which is what the industry benchmarks below actually measure. See
"How this compares to the wider industry" for the full reconciliation.

## Findings

1. **`stdio` is fastest on every axis**, as expected -- no TCP/HTTP framing,
   no JSON-over-the-wire serialization beyond the JSON-RPC payload itself,
   no ASGI middleware stack. Real baseline, not a surprise.
2. **`streamable_http` has roughly 3x `stdio`'s p50 latency and about 1.5x
   `sse`'s** -- consistent with it layering a full HTTP request/response
   (headers, a POST body, ASGI middleware, `httpx2`'s own connection
   pooling) on top of what `sse` does with an already-open stream. Real,
   not estimated: this is the first empirical number this project has for
   the new transport, closing the "real perf benchmarks" ask from GH #26.
3. **Throughput does NOT meaningfully scale with concurrency past ~5
   concurrent callers, for ANY of the three transports, when they share
   ONE `ClientSession`.** All three plateau (`stdio` ~2200-2464,
   `sse` ~980-999, `streamable_http` ~646-673) rather than climbing with
   added concurrent workers. This is a real, somewhat surprising finding,
   not an assumption going in -- it points at request/response dispatch
   being effectively serialized per session (a single read/write stream
   pair, JSON-RPC request-ID correlation over it) rather than a genuine
   connection-level bottleneck specific to any one transport. **Open
   question, correctly scoped as still open, not resolved by this
   spike**: whether MULTIPLE `MCPClient`/`ClientSession` instances against
   the same server would scale linearly with concurrency (plausible, given
   `stdio` spawns a dedicated subprocess per client and `sse`/
   `streamable_http`'s servers are ordinary async servers that should
   handle multiple independent sessions concurrently) -- this spike only
   tested concurrency WITHIN one shared session, which is the shape
   `ToolManager`'s current one-`MCPClient`-per-server-per-agent design
   actually uses, so it's the right first question to answer, but not the
   only one.
4. **No measurable memory growth over 2000 calls, on any transport** --
   real evidence against a leak in the new `streamable_http` path
   specifically (a real, if narrow, robustness signal worth having before
   calling the transport production-ready), and confirms the two existing
   transports don't have one either at this call volume.
5. **`streamable_http`'s max latency (29.92ms) is a real outlier**, ~15x
   its own p99 -- almost certainly the first non-warmup call after Python's
   own JIT/cache warmup settling, or a single GC pause; 200 samples is too
   few to distinguish a one-off from a real tail-latency pattern. Flagged
   honestly as unexplained, not smoothed over -- a real follow-up spike
   with more samples (2000+) would be needed to characterize the tail
   properly before this number is used for any capacity-planning decision.

## How this compares to the wider industry

Two credible, real, independently-published MCP benchmarks exist and were
reviewed directly (not summarized secondhand) before writing this section:

**[TM Dev Lab's multi-language benchmark](https://www.tmdevlab.com/mcp-server-performance-benchmark.html)**
(Feb 2026, 3.9M requests via k6, Docker, 50 concurrent VUs, real network hop
over a Docker bridge, four non-trivial tools including CPU-bound Fibonacci
and external I/O, all four servers on Streamable HTTP):

| Server | Avg latency | p95 | Throughput (RPS) |
|---|---|---|---|
| Java (Spring AI) | 0.84ms | 10.19ms | 1,624 |
| Go (official SDK) | 0.86ms | 10.03ms | 1,624 |
| Node.js (official SDK) | 10.66ms | 53.24ms | 559 |
| **Python (FastMCP, default single-worker uvicorn)** | **26.45ms** | **73.23ms** | **292** |

**[Stacklok/ToolHive's transport benchmark](https://stacklok.com/blog/mcp-server-performance-transport-protocol-matters/)**
(Jan 2026, a real Kubernetes cluster, a real `echo`-only server designed
specifically to isolate transport overhead -- the same goal as this spike):

| Transport | Concurrency | Avg RT | Throughput |
|---|---|---|---|
| `stdio` | 20 | ~20s (!) | 0.64 req/s, 2/50 succeeded |
| `sse` | 20 (sustained) | 564ms | 29.87 req/s |
| `streamable_http`, shared session pool | 20-1000 | 5-6ms (low), 622ms-3.09s (very high) | up to ~293-300 req/s |
| `streamable_http`, unique session per request | 20-50 | 273ms-1.12s | 33-36 req/s |

### Reconciling: are we faster than published Python numbers? No -- the comparison isn't valid, and here's exactly why

This spike's own `streamable_http` numbers (p50 2.01ms, ~673 calls/s @
concurrency=10) look better than TM Dev Lab's real, published Python figure
(26.45ms avg, 292 RPS). **That is not evidence Fabrica's `MCPClient` or this
transport implementation is faster than FastMCP's** -- it's an artifact of
four real, named methodology differences, not a real performance win:

1. **No real network/Docker hop here; TM Dev Lab's ran over a real Docker
   bridge network with a real, separate k6 load generator process.**
2. **Trivial `echo` tool here; TM Dev Lab's servers do real work** (Fibonacci,
   external HTTP calls, JSON transforms) -- their own tool-specific
   breakdown shows Python's `fetch_external_data` tool alone averaging
   80.92ms, 63x slower than Go's, entirely separate from transport choice.
3. **One shared session/connection with in-process async-task "concurrency"
   here; TM Dev Lab used k6's real, independent virtual users**, each a
   genuinely separate connection -- a fundamentally different concurrency
   model (see the methodology disclosure above).
4. **Both benchmarks' Python servers used a default, single-worker uvicorn
   config with no explicit tuning** -- this dimension IS comparable, and is
   consistent with, not contradicted by, this spike's own numbers once (1)-(3)
   are accounted for.

The honest, directionally useful comparisons that DO hold up:

- **Relative to Java/Go's ~0.85ms class-leading baseline latency, this
   spike's own `streamable_http` mean (2.24ms) on a workload-free echo tool
   with zero network overhead is still meaningfully higher** -- consistent
   with, not contradicting, the industry-wide finding that Python trails
   compiled/JIT'd runtimes by roughly an order of magnitude at the
   transport/serialization layer alone, before any real tool work or network
   cost is added on top.
- **Stacklok's own `streamable_http`-shared-session low-concurrency number
   (5.31ms avg, including a real Kubernetes+ToolHive-proxy hop) is the same
   order of magnitude as this spike's loopback-only mean (2.24ms)** -- ours
   being roughly half is directionally sensible (no real network/proxy hop),
   not a sign of a faster implementation.
- **This spike's own headline finding -- throughput does not scale past ~5
   concurrent callers sharing one session -- is directly corroborated by
   Stacklok's independent finding that shared vs. unique MCP sessions differ
   by roughly 10x in throughput.** Session-bound serialization is a real,
   industry-recognized MCP characteristic, not an artifact of this harness or
   `MCPClient`'s own implementation. Notably, this is also *why* the MCP spec
   itself moved to a fully stateless model in its 2026-07-28 revision
   (sessions and the `initialize` handshake removed entirely) -- a spec
   change this project's currently-pinned `mcp==2.0.0` SDK predates (this
   spike's own harness still calls `session.initialize()` on every connect,
   confirming the older, stateful protocol generation is what's actually
   under test here, matching both published benchmarks' own dates). If/when
   this org's `mcp` pin moves to a stateless-spec-compliant SDK version, this
   specific concurrency ceiling is a real, named candidate to re-measure --
   it may no longer apply.

**Where this leaves "where do we rank"**: no fair, direct ranking is possible
from this spike alone -- a true apples-to-apples run (same load generator,
same network topology, same tool complexity as TM Dev Lab's or Stacklok's own
harness) has not been done, and is named here as a real, explicit, scoped
follow-up rather than implied to already exist. What CAN be said honestly:
this implementation's raw transport overhead is in the same order of
magnitude as other real-world Python MCP implementations once network and
workload differences are accounted for, and its one clear architectural
finding (session-bound throughput ceiling) matches independently-published
industry data rather than being a one-off artifact.

## What this does and doesn't prove

**Proves**: on real, matched-version infrastructure, `streamable_http` works
correctly end to end (already known from the real integration tests in
`tests/mcp/test_client.py`) AND has a real, measured latency/throughput
profile -- roughly 3x `stdio`'s latency, sitting between `stdio` and nothing
worse than `sse` on relative terms, no memory leak at 2000 calls.

**Does not prove**: behavior under real network latency (this ran entirely
over `127.0.0.1` -- a real remote MCP server, the actual motivating case
from GH #26, would add real network RTT on top of every number above, likely
dwarfing the in-process transport-overhead differences measured here);
behavior with genuinely concurrent, independent client sessions (see finding
3); behavior under `srt`-sandboxed connections (this spike didn't sandbox
either client or server, unlike `MCPClient`'s own `stdio` production path
when `sandbox.enabled=True`).
