"""Tests for Tracer/Span/NullTracer/traced() -- and a real structural-
Protocol proof against the actual civitas.observability.tracer.Tracer,
not just a hand-rolled fake pretending to match its shape.
"""

from __future__ import annotations

import pytest

from fabrica.observability import NullTracer, Tracer, traced


class _RecordingSpan:
    def __init__(self, name: str, trace_id: str, span_id: str, parent_span_id: str | None) -> None:
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.attributes: dict[str, object] = {}
        self.error: BaseException | None = None
        self.ended = False

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def set_error(self, exc: BaseException) -> None:
        self.error = exc

    def end(self) -> None:
        self.ended = True


class _RecordingTracer:
    """A fast, in-memory test double -- records every span started so
    tests can assert on names/attributes/nesting without any real OTEL
    or civitas dependency.
    """

    def __init__(self) -> None:
        self.spans: list[_RecordingSpan] = []

    def start_span(
        self,
        name: str,
        trace_id: str = "",
        parent_span_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> _RecordingSpan:
        span = _RecordingSpan(
            name, trace_id or "generated-trace", f"span-{len(self.spans)}", parent_span_id
        )
        span.attributes.update(attributes or {})
        self.spans.append(span)
        return span


def test_null_tracer_start_span_is_always_safe() -> None:
    tracer = NullTracer()
    span = tracer.start_span("fabrica.tool.find", attributes={"query": "x"})
    span.set_attribute("result_count", 3)
    span.set_error(ValueError("boom"))
    span.end()  # must not raise, must not do anything observable


def test_traced_starts_and_ends_a_span_with_given_attributes() -> None:
    tracer = _RecordingTracer()
    with traced(tracer, "fabrica.tool.find", query="hello", kind="tool") as span:
        span.set_attribute("result_count", 2)

    assert len(tracer.spans) == 1
    recorded = tracer.spans[0]
    assert recorded.name == "fabrica.tool.find"
    assert recorded.attributes == {"query": "hello", "kind": "tool", "result_count": 2}
    assert recorded.ended is True
    assert recorded.error is None


def test_traced_propagates_trace_and_parent_span_ids() -> None:
    tracer = _RecordingTracer()
    with traced(tracer, "fabrica.sandbox.acquire", trace_id="abc123", parent_span_id="parent-1"):
        pass

    recorded = tracer.spans[0]
    assert recorded.trace_id == "abc123"
    assert recorded.parent_span_id == "parent-1"


def test_traced_records_and_reraises_on_exception() -> None:
    tracer = _RecordingTracer()

    with pytest.raises(ValueError, match="boom"):
        with traced(tracer, "fabrica.tool.code_mode.run"):
            raise ValueError("boom")

    recorded = tracer.spans[0]
    assert recorded.ended is True
    assert isinstance(recorded.error, ValueError)


def test_recording_tracer_satisfies_the_real_tracer_protocol() -> None:
    tracer: Tracer = _RecordingTracer()
    span = tracer.start_span("x")
    span.set_attribute("a", 1)
    span.end()


def test_real_civitas_tracer_satisfies_the_protocol_structurally() -> None:
    """The real proof this Protocol exists for: a genuine
    civitas.observability.tracer.Tracer, not a hand-rolled fake, must
    satisfy this Protocol with zero adapter code.
    """
    civitas_observability = pytest.importorskip("civitas.observability")
    real_tracer: Tracer = civitas_observability.Tracer()

    span = real_tracer.start_span("fabrica.tool.find", attributes={"query": "x"})
    span.set_attribute("result_count", 1)
    assert span.trace_id
    assert span.span_id
    span.end()
