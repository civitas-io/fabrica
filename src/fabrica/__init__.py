"""Fabrica -- the context layer for Python agents.

Distributed as `fabrica-context` on PyPI (fabrica itself is taken by an
unrelated project) -- the import name stays `fabrica`. See HANDOFF.md and
README.md for the full picture; docs/contracts/*.md for implementation-ready
signatures.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fabrica-context")
except PackageNotFoundError:
    # Editable/source checkout with no installed distribution metadata yet.
    __version__ = "0.0.0+unknown"
