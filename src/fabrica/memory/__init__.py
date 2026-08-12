"""Memory -- three facets: working memory, compaction, long-term MemoryStore.
See docs/contracts/memory.md.
"""

from fabrica.memory.compactor import Compactor, NullCompactor, RecencyCompactor, Summarizer
from fabrica.memory.errors import (
    CompactionError,
    CompactionUnavailableError,
    MemoryBackendError,
    MemoryError,
    WorkingMemoryQuotaExceeded,
)
from fabrica.memory.manager import MemoryManager
from fabrica.memory.store import InMemoryMemoryStore, MemoryStore
from fabrica.memory.types import CompactionResult, MemoryItem, Message
from fabrica.memory.working_memory import InMemoryWorkingMemoryStore, WorkingMemoryStore

__all__ = [
    "Compactor",
    "CompactionError",
    "CompactionResult",
    "CompactionUnavailableError",
    "InMemoryMemoryStore",
    "InMemoryWorkingMemoryStore",
    "MemoryBackendError",
    "MemoryError",
    "MemoryItem",
    "MemoryManager",
    "MemoryStore",
    "Message",
    "NullCompactor",
    "RecencyCompactor",
    "Summarizer",
    "WorkingMemoryQuotaExceeded",
    "WorkingMemoryStore",
]
