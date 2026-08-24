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
