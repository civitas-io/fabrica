"""Docs structure checks (2026-08-27, LLM-council-reviewed docs audit).

Fabrica's docs/ has a two-stage structure: top-level docs/*.md are "Design" status
(exploratory, may be superseded); docs/contracts/*.md are "Contract -- implementation-ready"
status that formalize a specific design into exact types/signatures. Four pairs share an
identical filename across the two directories (mcp-integration.md, mcp-server.md, memory.md,
prompts.md) -- genuinely different content, not duplicates, but a real collision risk: an
agent that greps by filename alone and lands on the Design version first has no signal that a
more current, implementation-ready Contract version exists elsewhere.

This is a proof, not a convention -- an LLM council audit found the Contract file always links
back to its Design doc ("Depends on: [X.md](../X.md)"), but none of the four Design docs linked
forward to their Contract counterpart, before this same audit added it. This test makes sure
that link can't silently disappear again.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
CONTRACTS_DIR = DOCS_DIR / "contracts"

_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _same_name_pairs() -> list[str]:
    design_names = {p.name for p in DOCS_DIR.glob("*.md")}
    contract_names = {p.name for p in CONTRACTS_DIR.glob("*.md")}
    return sorted(design_names & contract_names)


def _links_to(doc_path: Path, target_name: str) -> bool:
    text = doc_path.read_text(encoding="utf-8")
    return any(target_name in href for href in _LINK_RE.findall(text))


PAIRS = _same_name_pairs()


def test_same_named_design_and_contract_docs_exist() -> None:
    """A canary for this test file itself: if the collision set changes shape (a pair is
    renamed away, or a new same-named pair appears), the parametrized checks below need
    re-running against the new set -- this just confirms the set isn't accidentally empty."""
    assert PAIRS, "Expected at least one same-named docs/X.md + docs/contracts/X.md pair"


def test_design_doc_links_forward_to_its_contract() -> None:
    missing = [name for name in PAIRS if not _links_to(DOCS_DIR / name, f"contracts/{name}")]
    assert not missing, (
        f"docs/{{{','.join(missing)}}} don't link to their docs/contracts/ counterpart -- "
        "an agent reading the Design doc first has no signal a more current, "
        "implementation-ready version exists. Add a 'Formalized by' pointer."
    )


def test_contract_doc_links_back_to_its_design_doc() -> None:
    def _links_back(name: str) -> bool:
        path = CONTRACTS_DIR / name
        return _links_to(path, f"../{name}") or _links_to(path, f"({name})")

    missing = [name for name in PAIRS if not _links_back(name)]
    assert not missing, (
        f"docs/contracts/{{{','.join(missing)}}} don't link back to the docs/ Design doc they "
        "formalize -- readers of the Contract can't find the reasoning behind it."
    )
