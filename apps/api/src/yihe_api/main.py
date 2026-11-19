"""Application entry point. Business modules are added by subsequent tasks."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from yihe_api.auth.router import router as auth_router
from yihe_api.errors import ERROR_RESPONSES, TRACE_HEADERS, install_error_handlers
from yihe_api.rbac.router import router as rbac_router
from yihe_api.request_logs import configure_api_logging
from yihe_api.request_middleware import RequestTraceMiddleware

configure_api_logging()
app = FastAPI(title="上海以和 ERP API", version="0.1.0", responses=ERROR_RESPONSES)
app.add_middleware(RequestTraceMiddleware)
install_error_handlers(app)
app.include_router(auth_router, prefix="/api/v1/auth")
# The development proxy strips the initial /api segment.
app.include_router(auth_router, prefix="/v1/auth", include_in_schema=False)
app.include_router(rbac_router, prefix="/api/v1")
app.include_router(rbac_router, prefix="/v1", include_in_schema=False)


class HealthResponse(BaseModel):
    status: Literal["ok"]


@app.get(
    "/health",
    response_model=HealthResponse,
    operation_id="getHealth",
    tags=["system"],
    responses={200: {"headers": TRACE_HEADERS}},
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
