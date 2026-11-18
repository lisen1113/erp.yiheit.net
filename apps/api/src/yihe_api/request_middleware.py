"""ASGI tracing keeps request state active through exception handling and logging."""

from time import monotonic_ns

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from yihe_api.errors import ErrorCode, error_response
from yihe_api.tracing import LOGGER, TRACE_HEADER, bind_trace


class RequestTraceMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = monotonic_ns()
        ids = Headers(scope=scope).getlist(TRACE_HEADER)
        with bind_trace(ids[0] if len(ids) == 1 else None, method=scope["method"]) as context:
            response_started = False

            def update_context() -> None:
                route = scope.get("route")
                context.path = getattr(route, "path", None) or "<unmatched>"
                context.duration_ms = (monotonic_ns() - started) // 1_000_000

            async def send_response(message: Message) -> None:
                nonlocal response_started
                if message["type"] == "http.response.start":
                    response_started = True
                    context.status_code = message["status"]
                    MutableHeaders(scope=message)[TRACE_HEADER] = context.trace_id
                await send(message)

            try:
                await self.app(scope, receive, send_response)
            except Exception:
                if not response_started:
                    context.status_code = 500
                update_context()
                LOGGER.exception("request.exception")
                if response_started:
                    # The server must abort a partial response without logging raw exception data.
                    raise RuntimeError("Response interrupted after headers were sent") from None
                await error_response(ErrorCode.INTERNAL_ERROR)(scope, receive, send_response)
            finally:
                update_context()
                LOGGER.info("request.complete")
