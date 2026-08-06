"""
SPIKE (throwaway): does a real model write correct code on its first attempt
against a tool-function namespace, execute it in a real (Tier-0 subprocess)
sandbox, and produce a final result with lower total token cost than the
traditional direct tool-calling loop -- for a task that genuinely needs
filtering/aggregation across many documents?

Task: "Find all Fabrica design docs that mention SKILL.md, and report the
total word count across just those files." Ground truth independently
verified: 6 of 10 docs, 6,854 words total.

Held in a scratch location per instruction -- not production code.
"""
import json
import subprocess
import os
import tempfile

PROJECT = "fdl-c-gemini-apis"
REGION = "global"
MODEL = "claude-sonnet-4-6@default"
URL = f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/publishers/anthropic/models/{MODEL}:streamRawPredict"
DOCS_DIR = "/Users/jeryn/workspace/projects/fabrica/docs"

TASK = "Find all Fabrica design docs that mention 'SKILL.md', and report the total word count across just those files."
GROUND_TRUTH_COUNT = 6
GROUND_TRUTH_WORDS = 6854


def token():
    return subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()


def call(body: dict) -> dict:
    with open("/tmp/codemode_body.json", "w") as f:
        json.dump(body, f)
    out = subprocess.check_output([
        "curl", "-s", "-X", "POST", URL,
        "-H", f"Authorization: Bearer {token()}",
        "-H", "Content-Type: application/json",
        "-d", "@/tmp/codemode_body.json",
    ]).decode()
    return json.loads(out)


def usage_of(resp: dict) -> dict:
    return resp["usage"] if "usage" in resp else resp["message"]["usage"]


# --- Real tool implementations, shared by both approaches ---

def list_docs_impl():
    return sorted(f for f in os.listdir(DOCS_DIR) if f.endswith(".md"))


def read_doc_impl(name):
    with open(os.path.join(DOCS_DIR, name)) as f:
        return f.read()


# ============================================================
# Approach A: traditional direct tool-calling loop
# ============================================================

LIST_DOCS_SCHEMA = {
    "name": "list_docs",
    "description": "List all markdown filenames in the Fabrica docs/ folder.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}
READ_DOC_SCHEMA = {
    "name": "read_doc",
    "description": "Read the full text content of one doc by filename.",
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "filename, e.g. 'isolation.md'"}},
        "required": ["name"],
    },
}


def run_approach_static(max_turns=15):
    messages = [{"role": "user", "content": TASK}]
    total_input = 0
    total_output = 0
    turns = 0

    while turns < max_turns:
        turns += 1
        body = {
            "anthropic_version": "vertex-2023-10-16",
            "max_tokens": 1024,
            "tools": [LIST_DOCS_SCHEMA, READ_DOC_SCHEMA],
            "messages": messages,
        }
        resp = call(body)
        u = usage_of(resp)
        total_input += u["input_tokens"]
        total_output += u["output_tokens"]

        content = resp["content"]
        messages.append({"role": "assistant", "content": content})

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            final_text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            return {
                "final_text": final_text, "turns": turns,
                "total_input": total_input, "total_output": total_output,
                "total": total_input + total_output,
            }

        tool_results = []
        for tu in tool_uses:
            if tu["name"] == "list_docs":
                result = json.dumps(list_docs_impl())
            elif tu["name"] == "read_doc":
                try:
                    result = read_doc_impl(tu["input"]["name"])
                except Exception as e:
                    result = f"ERROR: {e}"
            else:
                result = "ERROR: unknown tool"
            tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": result})
        messages.append({"role": "user", "content": tool_results})

    return {"final_text": "MAX TURNS EXCEEDED", "turns": turns,
            "total_input": total_input, "total_output": total_output,
            "total": total_input + total_output}


# ============================================================
# Approach B: code-mode -- model writes code, executes in a sandbox
# ============================================================

RUN_CODE_SCHEMA = {
    "name": "run_code",
    "description": (
        "Execute Python code in a sandbox. Two functions are pre-defined and "
        "available to your code: list_docs() -> list[str] (doc filenames), and "
        "read_doc(name: str) -> str (a doc's full text). Your code MUST print() "
        "the final answer -- only stdout is returned to you, not the data your "
        "code processes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "Python code to execute"}},
        "required": ["code"],
    },
}

SANDBOX_PREAMBLE = f'''
import os
DOCS_DIR = {DOCS_DIR!r}
def list_docs():
    return sorted(f for f in os.listdir(DOCS_DIR) if f.endswith(".md"))
def read_doc(name):
    with open(os.path.join(DOCS_DIR, name)) as f:
        return f.read()
'''


def execute_in_sandbox(code: str):
    """Tier 0: bare subprocess. Real isolation tiers (gVisor/Firecracker/srt)
    are validated separately in prior spikes -- this spike is about the
    code-writing mechanism, not re-testing isolation itself."""
    full_source = SANDBOX_PREAMBLE + "\n" + code
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(full_source)
        path = f.name
    try:
        result = subprocess.run(["python3", path], capture_output=True, text=True, timeout=15)
        return {"stdout": result.stdout.strip(), "stderr": result.stderr.strip(),
                "returncode": result.returncode}
    finally:
        os.unlink(path)


def run_approach_codemode(max_turns=5):
    namespace_doc = (
        "You have access to a code execution tool. Two Python functions are "
        "pre-registered in that sandbox: list_docs() -> list[str], and "
        "read_doc(name: str) -> str. Write code to accomplish the task; only "
        "the printed output of your code returns to you."
    )
    messages = [{"role": "user", "content": f"{namespace_doc}\n\nTask: {TASK}"}]
    total_input = 0
    total_output = 0
    turns = 0
    code_attempts = 0

    while turns < max_turns:
        turns += 1
        body = {
            "anthropic_version": "vertex-2023-10-16",
            "max_tokens": 1024,
            "tools": [RUN_CODE_SCHEMA],
            "messages": messages,
        }
        resp = call(body)
        u = usage_of(resp)
        total_input += u["input_tokens"]
        total_output += u["output_tokens"]

        content = resp["content"]
        messages.append({"role": "assistant", "content": content})

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            final_text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            return {
                "final_text": final_text, "turns": turns, "code_attempts": code_attempts,
                "total_input": total_input, "total_output": total_output,
                "total": total_input + total_output,
            }

        tool_results = []
        for tu in tool_uses:
            code_attempts += 1
            exec_result = execute_in_sandbox(tu["input"]["code"])
            if exec_result["returncode"] != 0:
                result_text = f"ERROR (exit {exec_result['returncode']}):\n{exec_result['stderr']}"
            else:
                result_text = exec_result["stdout"]
            tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": result_text})
        messages.append({"role": "user", "content": tool_results})

    return {"final_text": "MAX TURNS EXCEEDED", "turns": turns, "code_attempts": code_attempts,
            "total_input": total_input, "total_output": total_output,
            "total": total_input + total_output}


if __name__ == "__main__":
    print(f"Ground truth: {GROUND_TRUTH_COUNT} docs, {GROUND_TRUTH_WORDS} words\n")

    print("=== Approach A: traditional direct tool-calling ===")
    a = run_approach_static()
    print(f"turns: {a['turns']}")
    print(f"final answer: {a['final_text']}")
    print(f"input tokens: {a['total_input']}  output tokens: {a['total_output']}  TOTAL: {a['total']}")

    print("\n=== Approach B: code-mode execution ===")
    b = run_approach_codemode()
    print(f"turns: {b['turns']}  code attempts (incl. any retries): {b['code_attempts']}")
    print(f"final answer: {b['final_text']}")
    print(f"input tokens: {b['total_input']}  output tokens: {b['total_output']}  TOTAL: {b['total']}")

    print("\n=== Comparison ===")
    print(f"Approach A total: {a['total']} tokens")
    print(f"Approach B total: {b['total']} tokens")
    if b["total"] > 0:
        print(f"Reduction: {100 * (1 - b['total']/a['total']):.1f}%")
