"""Types for PromptStore/PromptManager -- see docs/contracts/prompts.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fabrica.prompts.errors import InvalidCacheBoundaryError, PromptTooLargeError

# Real addition, closing contracts/prompts.md open item 3: an explicit
# ceiling for PromptTemplate.content, matching RunResult.stdout's own
# precedent of an explicit cap rather than leaving "how big can this get"
# unspecified. 256KB, a placeholder value like WorkingMemoryQuotaExceeded's
# ceiling (memory.md open item 2) -- no real deployment's prompt catalog
# has been measured against this yet; revisit with real data if it turns
# out too small or too generous.
MAX_PROMPT_CONTENT_BYTES = 256 * 1024


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: int
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    cacheable: bool = False
    cache_boundary: int | None = None
    """Author-declared. Validated at construction time against content's
    actual length -- open item 4, resolved: reject an invalid boundary
    rather than pass it through as-is (see InvalidCacheBoundaryError).
    """

    def __post_init__(self) -> None:
        """Real construction-time validation, closing contracts/prompts.md
        open items 3 and 4. Enforced here (on the type itself), not in
        PromptManager or any one PromptStore implementation, so every
        construction path -- any current or future backend -- gets the
        same guarantee for free, with nothing to duplicate or forget.
        """
        content_bytes = len(self.content.encode())
        if content_bytes > MAX_PROMPT_CONTENT_BYTES:
            raise PromptTooLargeError(
                f"PromptTemplate {self.name!r} content is {content_bytes} bytes, "
                f"exceeding the {MAX_PROMPT_CONTENT_BYTES}-byte ceiling"
            )
        if self.cache_boundary is not None and not (0 <= self.cache_boundary <= len(self.content)):
            raise InvalidCacheBoundaryError(
                f"PromptTemplate {self.name!r}: cache_boundary={self.cache_boundary} is "
                f"out of range for content of length {len(self.content)}"
            )
