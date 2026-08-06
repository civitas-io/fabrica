"""
SPIKE (throwaway): can Fabrica's MemoryStore protocol (write/search/get/forget,
Scope-based) actually wrap Mem0 cleanly, or does real integration friction
emerge -- mismatched signatures, a scope-model mismatch, or a dependency
footprint heavier than 'wrap, don't build' implies?

Run with the system Python (/usr/bin/python3) -- pip3 on this machine is tied
to a different interpreter than `python3` on PATH.

Held in a scratch location per instruction -- not production code.
"""
from dataclasses import dataclass
from typing import Optional
from mem0 import Memory


# --- Fabrica's actual MemoryStore protocol, from memory.md, for comparison ---
@dataclass
class Scope:
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    team_id: Optional[str] = None


class Mem0MemoryStore:
    """Thin wrapper attempt: Fabrica's MemoryStore shape over real Mem0."""

    def __init__(self, local_only: bool = True):
        if local_only:
            # Zero-API-key attempt: local embedder, local vector store,
            # infer=False on every write to avoid needing an LLM at all.
            config = {
                "embedder": {"provider": "fastembed", "config": {"model": "BAAI/bge-small-en-v1.5"}},
                "vector_store": {"provider": "chroma", "config": {
                    "collection_name": "fabrica_spike", "path": "/tmp/fabrica_mem0_chroma"}},
                # Mem0 requires an `llm` block to instantiate at all, even if
                # infer=False means it's never actually called at write time.
                "llm": {"provider": "openai", "config": {"api_key": "unused-if-infer-false"}},
            }
            self.mem = Memory.from_config(config)
        else:
            self.mem = Memory()  # real default: requires OPENAI_API_KEY
        self._local_only = local_only

    def write(self, scope: Scope, content: str) -> str:
        # Mem0 has no native team_id -- fold it into metadata, the only
        # place a scope dimension not in mem0's signature can go.
        result = self.mem.add(
            content,
            user_id=scope.user_id,
            agent_id=scope.agent_id,
            run_id=scope.session_id,          # mem0's closest analog to session_id
            metadata={"team_id": scope.team_id} if scope.team_id else None,
            infer=False,                       # skip LLM fact-extraction entirely
        )
        return result

    def search(self, scope: Scope, query: str, limit: int = 5):
        # DISCOVERED VIA SPIKE: unlike add(), search() rejects user_id/agent_id/
        # run_id as top-level kwargs and requires them wrapped in `filters=`.
        # A real API inconsistency within mem0 itself, not a Fabrica design issue.
        filters = {k: v for k, v in {
            "user_id": scope.user_id, "agent_id": scope.agent_id, "run_id": scope.session_id,
        }.items() if v is not None}
        return self.mem.search(query, filters=filters, limit=limit)

    def get(self, scope: Scope, memory_id: str):
        return self.mem.get(memory_id)

    def forget(self, scope: Scope, memory_id: str):
        return self.mem.delete(memory_id)


if __name__ == "__main__":
    print("=== Attempt 1: Memory() with zero config (the naive 'wrap, don't build' assumption) ===")
    try:
        Memory()
        print("Instantiated with zero config -- NOT what happened, see below")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")

    print("\n=== Attempt 2: fully local config (fastembed + chroma, infer=False) ===")
    store = Mem0MemoryStore(local_only=True)
    print("Instantiated OK with local embedder + local vector store.")

    scope = Scope(user_id="u1", session_id="s1", agent_id="agent1", team_id="team1")

    print("\n=== write() ===")
    write_result = store.write(scope, "The user prefers dark mode and lives in Bangalore.")
    print("write result:", write_result)

    print("\n=== search() ===")
    search_result = store.search(scope, "what theme does the user prefer?", limit=3)
    print("search result:", search_result)

    print("\n=== does team_id survive as metadata? ===")
    if isinstance(search_result, dict) and search_result.get("results"):
        for r in search_result["results"]:
            print("  metadata:", r.get("metadata"))
    elif isinstance(search_result, list):
        for r in search_result:
            print("  metadata:", r.get("metadata"))
