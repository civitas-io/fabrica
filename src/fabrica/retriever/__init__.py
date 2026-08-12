"""Retriever -- the shared discovery engine behind tools + skills.

See docs/contracts/retriever.md for the full contract this implements.
"""

from fabrica.retriever.backend import RetrieverBackend
from fabrica.retriever.errors import (
    DuplicateIndexableError,
    RetrieverError,
    RetrieverUnavailableError,
)
from fabrica.retriever.keyword_backend import KeywordBackend
from fabrica.retriever.retriever import Retriever
from fabrica.retriever.types import Indexable, RankedMatch

__all__ = [
    "DuplicateIndexableError",
    "Indexable",
    "KeywordBackend",
    "RankedMatch",
    "Retriever",
    "RetrieverBackend",
    "RetrieverError",
    "RetrieverUnavailableError",
]
