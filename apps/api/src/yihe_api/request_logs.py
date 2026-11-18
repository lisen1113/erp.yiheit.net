"""Structured logging using an allowlist instead of logging untrusted values."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from yihe_api.tracing import trace_context

EVENTS = frozenset({"request.complete", "request.exception"})
METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}
)


def stack_frames(exc: BaseException, tb: TracebackType | None) -> list[dict[str, object]]:
    # Keep every frame and chained exception, but omit messages, source text and locals.
    chain: list[dict[str, object]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frames = []
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append(
                {
                    "file": Path(code.co_filename).name,
                    "function": code.co_name,
                    "line": tb.tb_lineno,
                }
            )
            tb = tb.tb_next
        chain.append({"type": type(current).__name__, "frames": frames})
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
        tb = current.__traceback__ if current else None
    return chain


class RequestJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = trace_context()
        event = record.msg if isinstance(record.msg, str) and record.msg in EVENTS else "api.log"
        data: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "event": event,
            "trace_id": context.trace_id if context else None,
            "method": context.method if context and context.method in METHODS else None,
            "path": context.path if context else None,
            "status_code": context.status_code if context else None,
            "duration_ms": context.duration_ms if context else None,
        }
        if record.exc_info and record.exc_info[1]:
            data["exception_stack"] = stack_frames(record.exc_info[1], record.exc_info[2])
        return json.dumps(data, ensure_ascii=False)


def configure_api_logging() -> None:
    logger = logging.getLogger("yihe_api")
    handler = logging.StreamHandler()
    handler.setFormatter(RequestJsonFormatter())
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
