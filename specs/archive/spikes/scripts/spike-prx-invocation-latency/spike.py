"""
SPIKE (throwaway): real latency of invoking prx's search — fresh subprocess
per call vs. a single persistent `prx mcp` process reused across calls — and
whether either is fast enough for a production tool-call hot path.

Held in a scratch location per instruction — not production code.
"""
import json
import shutil
import statistics as stats
import subprocess
import time
from pathlib import Path

FIXTURE_DIR = Path("/tmp/prx-latency-bench")

TOOLS = {
    "send_slack_message": "Post a message to a Slack channel or direct message thread. Parameters: channel, text, thread_ts (optional)",
    "send_email": "Send an email message to one or more recipients via the configured email provider. Parameters: to, subject, body, cc (optional)",
    "post_teams_message": "Send a chat message to a Microsoft Teams channel. Parameters: channel_id, text",
    "query_analytics_db": "Run a read-only SQL query against the analytics warehouse and return rows. Parameters: sql, limit (optional)",
    "query_customer_records": "Look up customer account records by id, email, or name in the CRM database. Parameters: search_term, field",
    "search_documents": "Full-text search across the internal knowledge base and wiki documents. Parameters: query, max_results (optional)",
    "create_calendar_event": "Create a calendar event with a title, start and end time, and optional attendees. Parameters: title, start_time, end_time, attendees (optional)",
    "check_availability": "Check whether a room or person is free during a given time window. Parameters: resource, start_time, end_time",
    "get_weather": "Get the current weather and short-term forecast for a named location. Parameters: location, units (optional)",
    "translate_text": "Translate a block of text from a source language to a target language. Parameters: text, source_lang (optional), target_lang",
    "create_jira_ticket": "Create a Jira issue in a project with a summary, description, and issue type. Parameters: project_key, summary, description, issue_type",
    "upload_file_to_s3": "Upload a local file to a cloud object storage bucket and return its URL. Parameters: file_path, bucket, key",
}

QUERIES = [
    "send a slack message to #ops",
    "let people know by mail that the release shipped",
    "drop a note in the company chat app from microsoft",
    "run a sql query against the warehouse",
    "look up a customer's info in the database",
    "find relevant docs about the new feature",
    "schedule a meeting for 3pm tomorrow",
    "is the conference room free next tuesday",
    "what's the temperature in nyc",
    "convert this sentence into spanish",
    "file a bug ticket in jira",
    "put this file into cloud storage",
    "send a slack message to #ops",           # repeat a few to fill N=15
    "run a sql query against the warehouse",
    "file a bug ticket in jira",
]


def build_fixture():
    if FIXTURE_DIR.exists():
        shutil.rmtree(FIXTURE_DIR)
    tools_dir = FIXTURE_DIR / "tools"
    tools_dir.mkdir(parents=True)
    for name, desc in TOOLS.items():
        (tools_dir / f"{name}.md").write_text(f"# {name}\n{desc}\n")
    subprocess.run(["prx", "index", "."], cwd=FIXTURE_DIR, capture_output=True)


def summarize(label: str, times_s: list[float]):
    ms = sorted(t * 1000 for t in times_s)
    n = len(ms)
    p50 = ms[n // 2]
    p95 = ms[int(n * 0.95) - 1] if n >= 20 else ms[-1]
    print(f"{label:45} n={n:3}  min={ms[0]:7.1f}ms  p50={p50:7.1f}ms  "
          f"p95={p95:7.1f}ms  max={ms[-1]:7.1f}ms")
    return {"min": ms[0], "p50": p50, "p95": p95, "max": ms[-1]}


# --- Option A: fresh subprocess per call ---

def bench_subprocess():
    times = []
    for q in QUERIES:
        t0 = time.perf_counter()
        subprocess.run(
            ["prx", "search", q, ".", "--top-k", "3"],
            cwd=FIXTURE_DIR, capture_output=True, text=True,
        )
        times.append(time.perf_counter() - t0)
    return times


# --- Option B: single persistent `prx mcp` process, reused across calls ---

class McpClient:
    def __init__(self, cwd: Path):
        self.proc = subprocess.Popen(
            ["prx", "mcp"], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self._id = 0

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _recv(self):
        line = self.proc.stdout.readline()
        return json.loads(line) if line.strip() else None

    def initialize(self):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "spike", "version": "0.0.1"}}})
        self._recv()
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_search(self, query: str, path: str = ".", top_k: int = 3):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                     "params": {"name": "search",
                                "arguments": {"query": query, "path": path, "top_k": top_k}}})
        return self._recv()

    def close(self):
        self.proc.terminate()


def bench_mcp():
    t_start = time.perf_counter()
    client = McpClient(FIXTURE_DIR)
    client.initialize()
    startup_s = time.perf_counter() - t_start

    times = []
    for q in QUERIES:
        t0 = time.perf_counter()
        client.call_search(q)
        times.append(time.perf_counter() - t0)
    client.close()
    return startup_s, times


if __name__ == "__main__":
    build_fixture()

    print("=== Option A: fresh subprocess per call ===")
    a_times = bench_subprocess()
    a_stats = summarize("subprocess (spawn + index-load + search each time)", a_times)

    print("\n=== Option B: single persistent `prx mcp` process, reused ===")
    startup_s, b_times = bench_mcp()
    print(f"one-time startup + initialize handshake: {startup_s*1000:.1f}ms (paid once, not per call)")
    b_stats = summarize("mcp tools/call (warm process, per-call only)", b_times)

    print("\n=== Verdict ===")
    print(f"subprocess p50: {a_stats['p50']:.1f}ms  |  mcp p50: {b_stats['p50']:.1f}ms")
    speedup = a_stats["p50"] / b_stats["p50"] if b_stats["p50"] else float("inf")
    print(f"warm MCP process is ~{speedup:.1f}x faster per call than spawning fresh each time")
