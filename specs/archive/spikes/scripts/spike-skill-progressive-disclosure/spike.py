"""
SPIKE (throwaway): using the REAL bigpowers SKILL.md catalog (81 skills), does
a two-level progressive-disclosure loader (index of name+description -> full
body on demand) keep token cost flat as the candidate pool grows, and does a
real model correctly select among genuinely overlapping real descriptions
(not synthetic ones) when given only the index?

Held in a scratch location per instruction — not production code.
"""
import json
import re
import subprocess
from pathlib import Path

SKILLS_DIR = Path("/Users/jeryn/.pi/agent/npm/node_modules/bigpowers/.pi/skills")
PROJECT = "fdl-c-gemini-apis"
REGION = "global"
MODEL = "claude-sonnet-4-6@default"
URL = f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/publishers/anthropic/models/{MODEL}:streamRawPredict"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
NAME_RE = re.compile(r'^name:\s*(.+)$', re.MULTILINE)
DESC_RE = re.compile(r'^description:\s*"?(.+?)"?\s*$', re.MULTILINE)


def token():
    return subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()


def call(body: dict) -> dict:
    with open("/tmp/skill_spike_body.json", "w") as f:
        json.dump(body, f)
    out = subprocess.check_output([
        "curl", "-s", "-X", "POST", URL,
        "-H", f"Authorization: Bearer {token()}",
        "-H", "Content-Type: application/json",
        "-d", "@/tmp/skill_spike_body.json",
    ]).decode()
    return json.loads(out)


def usage_of(resp: dict) -> dict:
    return resp["usage"] if "usage" in resp else resp["message"]["usage"]


def load_skills() -> dict:
    skills = {}
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        m = FRONTMATTER_RE.match(text)
        if not m:
            continue
        fm, body = m.groups()
        name_m = NAME_RE.search(fm)
        desc_m = DESC_RE.search(fm)
        if not name_m or not desc_m:
            continue
        skills[name_m.group(1).strip()] = {
            "description": desc_m.group(1).strip(),
            "body": body.strip(),
        }
    return skills


ALL_SKILLS = load_skills()
print(f"loaded {len(ALL_SKILLS)} real skills from bigpowers catalog")

TARGET = "spike-prototype"
QUERY = "I want to run a disposable spike experiment for an unclear problem, what should I do?"

LOAD_SKILL_SCHEMA = {
    "name": "load_skill",
    "description": "Load the full instructions for a named skill.",
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The skill name to load"}},
        "required": ["name"],
    },
}


def slice_with_target(n: int) -> dict:
    names = list(ALL_SKILLS.keys())[:n]
    if TARGET not in names:
        names = [TARGET] + names[: n - 1]
    return {k: ALL_SKILLS[k] for k in names}


def approach_static(n: int) -> int:
    """All N skills' FULL bodies concatenated into the prompt upfront."""
    subset = slice_with_target(n)
    blob = "\n\n".join(f"## {name}\n{data['description']}\n\n{data['body']}" for name, data in subset.items())
    body = {
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": f"Available skills:\n{blob}\n\nTask: {QUERY}\nName exactly one skill to use."}],
    }
    resp = call(body)
    return usage_of(resp)["input_tokens"]


def approach_progressive(n: int):
    """Turn 1: index only (name+description). Turn 2: matched skill's full body only."""
    subset = slice_with_target(n)
    index_blob = "\n".join(f"- {name}: {data['description']}" for name, data in subset.items())

    turn1_body = {
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": 200,
        "tools": [LOAD_SKILL_SCHEMA],
        "tool_choice": {"type": "tool", "name": "load_skill"},
        "messages": [{"role": "user", "content": f"Available skills (index only):\n{index_blob}\n\nTask: {QUERY}"}],
    }
    resp1 = call(turn1_body)
    turn1_tokens = usage_of(resp1)["input_tokens"]
    load_call = resp1["content"][0]
    picked = load_call["input"]["name"]

    full_body = subset.get(picked, subset[TARGET])["body"]
    turn2_body = {
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": 200,
        "messages": [
            {"role": "user", "content": f"Available skills (index only):\n{index_blob}\n\nTask: {QUERY}"},
            {"role": "assistant", "content": [load_call]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": load_call["id"], "content": full_body}]},
        ],
    }
    resp2 = call(turn2_body)
    turn2_tokens = usage_of(resp2)["input_tokens"]
    return turn1_tokens + turn2_tokens, turn1_tokens, turn2_tokens, picked


def disambiguation_check(query: str):
    """Force load_skill against the FULL 81-skill real index — qualitative check."""
    index_blob = "\n".join(f"- {name}: {data['description']}" for name, data in ALL_SKILLS.items())
    body = {
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": 200,
        "tools": [LOAD_SKILL_SCHEMA],
        "tool_choice": {"type": "tool", "name": "load_skill"},
        "messages": [{"role": "user", "content": f"Available skills (index only):\n{index_blob}\n\nTask: {query}"}],
    }
    resp = call(body)
    picked = resp["content"][0]["input"]["name"]
    tokens = usage_of(resp)["input_tokens"]
    return picked, tokens


if __name__ == "__main__":
    print(f"\n{'N skills':>8} | {'static (O(N))':>14} | {'progressive total':>18} | {'  turn1':>8} | {'  turn2':>8} | picked")
    print("-" * 80)
    for n in (10, 30, 81):
        static_tokens = approach_static(n)
        prog_total, t1, t2, picked = approach_progressive(n)
        print(f"{n:>8} | {static_tokens:>14} | {prog_total:>18} | {t1:>8} | {t2:>8} | {picked}")

    print("\n=== Real-world disambiguation check (full 81-skill index, genuinely overlapping) ===")
    hard_queries = [
        "I found a bug in production, help me fix it",
        "I want to break a big feature into vertical slices before building it",
        "I want to run multiple independent tasks in parallel without waiting between them",
        "the agent seems stuck and isn't making progress, what happened",
    ]
    for q in hard_queries:
        picked, tokens = disambiguation_check(q)
        print(f"  '{q}'\n    -> picked: {picked}  (index cost: {tokens} tokens)")
