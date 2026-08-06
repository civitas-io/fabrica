"""
SPIKE (throwaway): does find_tools-style two-turn retrieval keep schema-token
overhead bounded (~O(1)) as tool count N grows, vs sending all N schemas
upfront (O(N))?

Held in a scratch location per instruction — not production code, not part of
the fabrica package. Uses real Claude-on-Vertex calls (project already
authenticated via gcloud in this environment).
"""
import json
import subprocess
import sys

PROJECT = "fdl-c-gemini-apis"
REGION = "global"
MODEL = "claude-sonnet-4-6@default"  # cheap-ish, real tool-use capable model
URL = f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/publishers/anthropic/models/{MODEL}:streamRawPredict"


def token():
    return subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()


def call(body: dict) -> dict:
    """POST to Vertex Anthropic, return the parsed message JSON (handles both
    single-JSON and SSE-chunked responses defensively)."""
    with open("/tmp/spike_body.json", "w") as f:
        json.dump(body, f)
    out = subprocess.check_output([
        "curl", "-s", "-X", "POST", URL,
        "-H", f"Authorization: Bearer {token()}",
        "-H", "Content-Type: application/json",
        "-d", "@/tmp/spike_body.json",
    ]).decode()
    # Try direct JSON first (observed behavior: single JSON object even on
    # the streamRawPredict endpoint for short outputs).
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        pass
    # Fallback: SSE-ish lines — take the last parseable JSON object with a
    # "usage" field, or merge message_start (has input_tokens) + last delta.
    last_with_usage = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[len("data:"):].strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "usage" in obj or ("message" in obj and "usage" in obj.get("message", {})):
            last_with_usage = obj
    if last_with_usage is None:
        print("RAW OUTPUT (could not parse):", out[:500], file=sys.stderr)
        raise RuntimeError("no usage found in response")
    return last_with_usage


def usage_of(resp: dict) -> dict:
    if "usage" in resp:
        return resp["usage"]
    if "message" in resp and "usage" in resp["message"]:
        return resp["message"]["usage"]
    raise RuntimeError(f"no usage in {resp}")


# --- Build a pool of 50 realistic-ish tool schemas across domains ---

DOMAINS = [
    ("email", "send_email", "Send an email message to one or more recipients via the configured email provider.",
     {"to": "string (comma-separated recipient email addresses)", "subject": "string", "body": "string (plain text or HTML body)", "cc": "string (optional, comma-separated)"}),
    ("calendar", "create_event", "Create a calendar event with a title, start/end time, and optional attendees.",
     {"title": "string", "start_time": "string (ISO 8601)", "end_time": "string (ISO 8601)", "attendees": "array of string emails (optional)"}),
    ("slack", "post_slack_message", "Post a message to a Slack channel or direct message thread.",
     {"channel": "string (channel id or name)", "text": "string", "thread_ts": "string (optional, reply thread timestamp)"}),
    ("db", "query_database", "Run a read-only SQL query against the configured analytics database and return rows.",
     {"sql": "string (SELECT-only SQL)", "limit": "integer (optional, default 100)"}),
    ("weather", "get_weather", "Get the current weather and short-term forecast for a named location.",
     {"location": "string (city name or lat,lon)", "units": "string (metric|imperial, optional)"}),
    ("translate", "translate_text", "Translate a block of text from a source language to a target language.",
     {"text": "string", "source_lang": "string (ISO 639-1, optional, auto-detect if omitted)", "target_lang": "string (ISO 639-1)"}),
    ("calc", "evaluate_expression", "Evaluate a mathematical expression and return the numeric result.",
     {"expression": "string (arithmetic expression)"}),
    ("sheets", "append_spreadsheet_row", "Append a row of values to a named spreadsheet and sheet tab.",
     {"spreadsheet_id": "string", "sheet_name": "string", "values": "array of string|number"}),
    ("jira", "create_jira_ticket", "Create a Jira issue in a project with a summary, description, and issue type.",
     {"project_key": "string", "summary": "string", "description": "string", "issue_type": "string (Bug|Task|Story)"}),
    ("github", "create_github_issue", "Create a GitHub issue in a repository with a title, body, and optional labels.",
     {"repo": "string (owner/repo)", "title": "string", "body": "string", "labels": "array of string (optional)"}),
]


def make_schema(i: int) -> dict:
    domain, base_name, desc, props = DOMAINS[i % len(DOMAINS)]
    suffix = "" if i < len(DOMAINS) else f"_v{i // len(DOMAINS) + 1}"
    name = f"{base_name}{suffix}"
    properties = {}
    required = []
    for pname, ptype_desc in props.items():
        properties[pname] = {"type": "string", "description": ptype_desc}
        if "optional" not in ptype_desc:
            required.append(pname)
    return {
        "name": name,
        "description": desc + (f" (variant {suffix})" if suffix else ""),
        "input_schema": {"type": "object", "properties": properties, "required": required},
    }


ALL_SCHEMAS = [make_schema(i) for i in range(50)]
TARGET_INDEX = 0  # send_email — always present regardless of N-slice size
TARGET_QUERY = "send an email to the team letting them know the deploy finished"

FIND_TOOLS_SCHEMA = {
    "name": "find_tools",
    "description": "Search for available tools by capability. Returns matching tool schemas. Always call this before using a tool you haven't retrieved in this session.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language description of what you want to do"},
            "limit": {"type": "integer", "description": "Maximum tools to return (default 5)", "default": 5},
        },
        "required": ["query"],
    },
}


def keyword_match(query: str, schemas: list[dict], limit: int = 3) -> list[dict]:
    """Trivial keyword overlap — good enough for THIS spike (token counting),
    not a test of retrieval quality (that's spike 4)."""
    q_words = set(query.lower().split())
    scored = []
    for s in schemas:
        text = (s["name"] + " " + s["description"]).lower()
        score = sum(1 for w in q_words if w in text)
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:limit]]


def approach_static(n: int) -> int:
    """All N schemas sent upfront in the tools array."""
    schemas = ALL_SCHEMAS[:n]
    if not any(s["name"] == "send_email" for s in schemas):
        schemas = [ALL_SCHEMAS[TARGET_INDEX]] + schemas[: n - 1]
    body = {
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": 300,
        "tools": schemas,
        "tool_choice": {"type": "tool", "name": "send_email"},
        "messages": [{"role": "user", "content": TARGET_QUERY}],
    }
    resp = call(body)
    return usage_of(resp)["input_tokens"]


def approach_find_tools(n: int) -> int:
    """Turn 1: only find_tools in scope. Turn 2: only the matched schema(s)."""
    schemas = ALL_SCHEMAS[:n]
    if not any(s["name"] == "send_email" for s in schemas):
        schemas = [ALL_SCHEMAS[TARGET_INDEX]] + schemas[: n - 1]

    # Turn 1 — model forced to call find_tools
    turn1_body = {
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": 300,
        "tools": [FIND_TOOLS_SCHEMA],
        "tool_choice": {"type": "tool", "name": "find_tools"},
        "messages": [{"role": "user", "content": TARGET_QUERY}],
    }
    resp1 = call(turn1_body)
    turn1_tokens = usage_of(resp1)["input_tokens"]

    # Simulate the gateway: keyword-match against the registered pool (not
    # sent to the model — this happens gateway-side, off the token budget).
    matched = keyword_match(TARGET_QUERY, schemas, limit=3)
    if not any(m["name"] == "send_email" for m in matched):
        matched = [ALL_SCHEMAS[TARGET_INDEX]] + matched[:2]

    # Turn 2 — only the matched schema(s) in scope, forced call
    find_tools_call = resp1["content"][0]
    turn2_body = {
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": 300,
        "tools": matched,
        "tool_choice": {"type": "tool", "name": "send_email"},
        "messages": [
            {"role": "user", "content": TARGET_QUERY},
            {"role": "assistant", "content": [find_tools_call]},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": find_tools_call["id"],
                "content": json.dumps([{"name": m["name"], "description": m["description"]} for m in matched]),
            }]},
        ],
    }
    resp2 = call(turn2_body)
    turn2_tokens = usage_of(resp2)["input_tokens"]

    return turn1_tokens + turn2_tokens, turn1_tokens, turn2_tokens


if __name__ == "__main__":
    print(f"{'N tools':>8} | {'static (O(N))':>14} | {'find_tools total':>17} | {'  turn1':>8} | {'  turn2':>8}")
    print("-" * 70)
    for n in (5, 20, 50):
        static_tokens = approach_static(n)
        ft_total, t1, t2 = approach_find_tools(n)
        print(f"{n:>8} | {static_tokens:>14} | {ft_total:>17} | {t1:>8} | {t2:>8}")
