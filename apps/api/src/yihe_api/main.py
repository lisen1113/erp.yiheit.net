"""Application entry point. Business modules are added by subsequent tasks."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from yihe_api.audit.router import router as audit_router
from yihe_api.auth.router import router as auth_router
from yihe_api.errors import ERROR_RESPONSES, TRACE_HEADERS, install_error_handlers
from yihe_api.files.router import router as files_router
from yihe_api.master_data.customer_import_router import router as customer_import_router
from yihe_api.master_data.customer_router import router as customers_router
from yihe_api.master_data.router import router as partners_router
from yihe_api.master_data.supplier_import_router import router as supplier_import_router
from yihe_api.master_data.supplier_router import router as suppliers_router
from yihe_api.product_categories.imports import router as product_categories_router
from yihe_api.rbac.router import router as rbac_router
from yihe_api.readiness import router as readiness_router
from yihe_api.request_logs import configure_api_logging
from yihe_api.request_middleware import RequestTraceMiddleware

configure_api_logging()
app = FastAPI(title="上海以和 ERP API", version="0.1.0", responses=ERROR_RESPONSES)
app.add_middleware(RequestTraceMiddleware)
install_error_handlers(app)
app.include_router(readiness_router)
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


app.include_router(audit_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/v1", include_in_schema=False)

app.include_router(files_router, prefix="/api/v1")
app.include_router(files_router, prefix="/v1", include_in_schema=False)

app.include_router(partners_router, prefix="/api/v1")
app.include_router(partners_router, prefix="/v1", include_in_schema=False)

app.include_router(customer_import_router, prefix="/api/v1")
app.include_router(customer_import_router, prefix="/v1", include_in_schema=False)
app.include_router(customers_router, prefix="/api/v1")
app.include_router(customers_router, prefix="/v1", include_in_schema=False)

app.include_router(supplier_import_router, prefix="/api/v1")
app.include_router(suppliers_router, prefix="/api/v1")

app.include_router(product_categories_router, prefix="/api/v1")
