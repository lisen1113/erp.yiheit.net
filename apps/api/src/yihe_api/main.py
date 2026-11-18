"""Application entry point. Business modules are added by subsequent tasks."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="上海以和 ERP API", version="0.1.0")


class HealthResponse(BaseModel):
    status: Literal["ok"]


@app.get("/health", response_model=HealthResponse, operation_id="getHealth", tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")
