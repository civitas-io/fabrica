"""Types for PromptStore/PromptManager -- see docs/contracts/prompts.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: int
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    cacheable: bool = False
    cache_boundary: int | None = None
    """Author-declared, never validated against content's actual length --
    open item 4: PromptManager has no way to verify the claim and doesn't
    try to, consistent with never parsing content's templating syntax.
    """
