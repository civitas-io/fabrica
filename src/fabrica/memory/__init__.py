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
from fabrica.memory.store import BlobStore, InMemoryMemoryStore, MemoryStore, PersistedMemoryStore
from fabrica.memory.types import CompactionResult, MemoryItem, Message
from fabrica.memory.working_memory import InMemoryWorkingMemoryStore, WorkingMemoryStore

__all__ = [
    "BlobStore",
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
    "PersistedMemoryStore",
    "RecencyCompactor",
    "Summarizer",
    "WorkingMemoryQuotaExceeded",
    "WorkingMemoryStore",
]
