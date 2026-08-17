"""Tracer/Span -- structural Protocols for real span emission, closing
the biggest gap found in docs/self-reflection-report.md §3.3: nine spans
are named in docs/system-design.md §7, but only one call site emitted
anything (a logger.info stand-in), covering two of the nine.

A real, important finding while designing this: `civitas.observability
.tracer.Tracer`/`Span` (civitas>=0.11, exported from `civitas
.observability.__all__`) do NOT use OpenTelemetry's global TracerProvider
registry -- Civitas holds an instance-scoped provider and propagates
`trace_id`/`parent_span_id` explicitly via its own message envelope
fields, not OTEL's context-propagation machinery. A plain
`opentelemetry.trace.get_tracer()` call inside Fabrica would NOT actually
route through Civitas's own span pipeline (`SpanQueue`/`OTELAgent`) --
only calling into a real `Tracer` instance does. That is why this module
defines a structural Protocol matching Civitas's real, public shape
instead of depending on the OTEL API directly.

Depend on shapes, not packages (architecture.md §1a) -- a real
`civitas.observability.tracer.Tracer` satisfies `Tracer` below
structurally, with zero adapter code, while every manager stays
importable and testable with no `civitas` installed at all
(library-first: this module itself imports nothing from `civitas`).

`CivitasBridge.build()` is the one place licensed to wire in a REAL
`civitas.observability.tracer.Tracer` (service mode) -- every other
constructor default is `NullTracer()`, matching the
`NullPresidiumClient`/`NullCompactor` pattern: span emission is always
safe to call, and does nothing observable when nothing is configured to
collect it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol


class Span(Protocol):
    """Structural match for `civitas.observability.tracer.Span`'s public
    surface -- only what Fabrica actually uses."""

    trace_id: str
    span_id: str

    def set_attribute(self, key: str, value: Any) -> None: ...
    def set_error(self, exc: BaseException) -> None: ...
    def end(self) -> None: ...


class Tracer(Protocol):
    """Structural match for `civitas.observability.tracer.Tracer`'s public
    surface -- only `start_span`, the one method every span site needs."""

    def start_span(
        self,
        name: str,
        trace_id: str = "",
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Span: ...


class _NullSpan:
    """A span that goes nowhere -- every method is a safe no-op."""

    trace_id = ""
    span_id = ""

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_error(self, exc: BaseException) -> None:
        return None

    def end(self) -> None:
        return None


class NullTracer:
    """The default `Tracer` for every component below `CivitasBridge` --
    library mode and any direct-construction test path get this unless a
    real one is explicitly injected.
    """

    def start_span(
        self,
        name: str,
        trace_id: str = "",
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        return _NullSpan()


@contextmanager
def traced(
    tracer: Tracer,
    name: str,
    *,
    trace_id: str = "",
    parent_span_id: str | None = None,
    **attributes: Any,
) -> Iterator[Span]:
    """Starts a span, yields it so the caller can add more attributes once
    a result is known, and always ends it -- including recording the
    exception via `set_error()` if the body raises, then re-raising
    unchanged. A span must never swallow or alter caller behavior.

    `None`-valued attributes are dropped before reaching the real
    `Tracer` -- a real behavior found while wiring this up: a genuine
    `opentelemetry-sdk` span (which a real `civitas.observability.tracer
    .Tracer` may hold internally) does not error on a `None` attribute
    value, but silently drops it and logs a warning per call. Several
    real call sites here have legitimately optional attributes
    (`Retriever.search()`'s `kind`, `execute_in_sandbox`'s `skill_name`
    for a code-mode run) -- filtering here once avoids that warning
    noise at every one of them individually.
    """
    clean_attributes = {k: v for k, v in attributes.items() if v is not None}
    span = tracer.start_span(
        name, trace_id=trace_id, parent_span_id=parent_span_id, attributes=clean_attributes
    )
    try:
        yield span
    except BaseException as exc:
        span.set_error(exc)
        raise
    finally:
        span.end()
