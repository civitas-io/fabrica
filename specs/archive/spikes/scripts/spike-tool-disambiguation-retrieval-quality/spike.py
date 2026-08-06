"""
SPIKE (throwaway): does keyword-only matching hit reasonable top-3 precision
over deliberately confusable tool descriptions — including paraphrased
queries with no lexical overlap — or does it fail specifically there? And
does prx's real embedded semantic-search engine (built for code, not short
API-style text) meaningfully outperform it on this out-of-domain task?

Held in a scratch location per instruction — not production code.
"""
import json
import subprocess
import shutil
from pathlib import Path

FIXTURE_DIR = Path("/tmp/prx-tool-bench")

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

# (query, ground_truth_tool, difficulty)
# "easy" = shares a distinctive word with the tool's own text.
# "hard" = paraphrased, no shared distinctive word — tests real semantic understanding.
BENCHMARK = [
    ("send a slack message to #ops", "send_slack_message", "easy"),
    ("let people know by mail that the release shipped", "send_email", "hard"),
    ("drop a note in the company chat app from microsoft", "post_teams_message", "hard"),
    ("run a sql query against the warehouse", "query_analytics_db", "easy"),
    ("look up a customer's info in the database", "query_customer_records", "hard"),
    ("find relevant docs about the new feature", "search_documents", "hard"),
    ("schedule a meeting for 3pm tomorrow", "create_calendar_event", "easy"),
    ("is the conference room free next tuesday", "check_availability", "hard"),
    ("what's the temperature in nyc", "get_weather", "easy"),
    ("convert this sentence into spanish", "translate_text", "hard"),
    ("file a bug ticket in jira", "create_jira_ticket", "easy"),
    ("put this file into cloud storage", "upload_file_to_s3", "hard"),
]


def build_fixture():
    if FIXTURE_DIR.exists():
        shutil.rmtree(FIXTURE_DIR)
    tools_dir = FIXTURE_DIR / "tools"
    tools_dir.mkdir(parents=True)
    for name, desc in TOOLS.items():
        (tools_dir / f"{name}.md").write_text(f"# {name}\n{desc}\n")
    subprocess.run(["prx", "index", "."], cwd=FIXTURE_DIR, capture_output=True)


def prx_search_top3(query: str, mode_flag: str | None) -> list[str]:
    cmd = ["prx", "search", query, ".", "--top-k", "3"]
    if mode_flag:
        cmd.append(mode_flag)
    out = subprocess.run(cmd, cwd=FIXTURE_DIR, capture_output=True, text=True).stdout
    data = json.loads(out)
    matches = data.get("data", {}).get("matches", [])
    return [Path(m["file"]).stem for m in matches]


def prx_default_top3(query: str) -> list[str]:
    """Default mode: fused literal + semantic + structural via RRF."""
    return prx_search_top3(query, None)


def prx_semantic_only_top3(query: str) -> list[str]:
    """Isolated semantic mode via --semantic — the real embedded model alone,
    no literal/keyword contribution."""
    return prx_search_top3(query, "--semantic")


def naive_keyword_top3(query: str) -> list[str]:
    """Same style of matcher as Spike 1 — literal word-overlap, no IDF weighting."""
    q_words = set(query.lower().split())
    scored = []
    for name, desc in TOOLS.items():
        text = (name.replace("_", " ") + " " + desc).lower()
        score = sum(1 for w in q_words if w in text)
        scored.append((score, name))
    scored.sort(key=lambda x: -x[0])
    return [name for score, name in scored[:3] if score > 0]


def evaluate(method_name: str, fn):
    hits_at_3 = 0
    hits_at_1 = 0
    hard_hits_at_3 = 0
    hard_total = sum(1 for _, _, d in BENCHMARK if d == "hard")
    print(f"\n=== {method_name} ===")
    for query, truth, difficulty in BENCHMARK:
        top3 = fn(query)
        at3 = truth in top3
        at1 = len(top3) > 0 and top3[0] == truth
        hits_at_3 += at3
        hits_at_1 += at1
        if difficulty == "hard" and at3:
            hard_hits_at_3 += 1
        mark = "OK " if at3 else "MISS"
        print(f"  [{mark}] ({difficulty:4}) '{query[:45]:45}' -> truth={truth:24} top3={top3}")
    n = len(BENCHMARK)
    print(f"  precision@3: {hits_at_3}/{n} ({100*hits_at_3/n:.0f}%)  "
          f"top1: {hits_at_1}/{n} ({100*hits_at_1/n:.0f}%)  "
          f"hard-only@3: {hard_hits_at_3}/{hard_total} ({100*hard_hits_at_3/hard_total:.0f}%)")


if __name__ == "__main__":
    build_fixture()
    evaluate("naive keyword (Spike-1-style word overlap)", naive_keyword_top3)
    evaluate("prx default (fused literal+semantic+structural via RRF)", prx_default_top3)
    evaluate("prx --semantic (isolated embedded model, code-tuned, no literal)", prx_semantic_only_top3)
