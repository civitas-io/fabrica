"""Scope -- reused by MemoryStore, PresidiumClient, and the usage-ledger span
attributes (memory.md, system-design.md §7). One type, not redefined per
surface.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    user_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    team_id: str | None = None  # shared with usage/budget rollups
