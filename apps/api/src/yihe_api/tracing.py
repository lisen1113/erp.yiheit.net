"""Request context shared by logs and future worker entry points."""

import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

TRACE_HEADER = "X-Request-ID"
TRACE_KEY = "trace_id"
_TRACE_PATTERN = re.compile(r"(?![0-9]{12,}$)[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


@dataclass
class TraceContext:
    trace_id: str
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None


_context: ContextVar[TraceContext | None] = ContextVar("erp_trace", default=None)


def trace_context() -> TraceContext | None:
    return _context.get()


def current_trace_id() -> str:
    context = trace_context()
    if context is None:
        raise RuntimeError("No trace context is active")
    return context.trace_id


@contextmanager
def bind_trace(
    request_id: str | None = None, *, method: str | None = None
) -> Iterator[TraceContext]:
    trace_id = request_id if request_id and _TRACE_PATTERN.fullmatch(request_id) else uuid4().hex
    context = TraceContext(trace_id, method)
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


def outgoing_trace_headers() -> dict[str, str]:
    """Attach to task headers when a worker integration is added."""
    context = trace_context()
    return {TRACE_KEY: context.trace_id} if context else {}


@contextmanager
def bind_task_trace(headers: Mapping[str, object]) -> Iterator[TraceContext]:
    raw = headers.get(TRACE_KEY)
    with bind_trace(raw if isinstance(raw, str) else None) as context:
        yield context


LOGGER = logging.getLogger("yihe_api.request")
