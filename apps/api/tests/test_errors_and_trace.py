import io
import json
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from yihe_api.errors import ERRORS, ApiError, ErrorCode, install_error_handlers
from yihe_api.request_logs import RequestJsonFormatter
from yihe_api.request_middleware import RequestTraceMiddleware
from yihe_api.tracing import bind_task_trace, outgoing_trace_headers


class ProbeBody(BaseModel):
    quantity: int


@pytest.fixture
def probe():
    app = FastAPI()
    app.add_middleware(RequestTraceMiddleware)
    install_error_handlers(app)

    @app.post("/probe/{record_id}")
    def validate(record_id: int, body: ProbeBody):
        return {"record_id": record_id, "quantity": body.quantity}

    @app.get("/conflict")
    def conflict():
        raise ApiError(ErrorCode.VERSION_CONFLICT)

    @app.get("/fail")
    def fail():
        logging.getLogger("yihe_api.test").warning("password=%s", "synthetic-password")
        raise ValueError(
            "SELECT * FROM secret; /private/service.py; token=synthetic-token; "
            "bank_account=6222021234567890123; cookie=synthetic-cookie"
        )

    with TestClient(app) as client:
        yield client


def test_validation_error_field_and_trace(probe) -> None:
    response = probe.post("/probe/1", json={}, headers={"X-Request-ID": "t004-validation"})
    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "t004-validation"
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": "请检查填写的字段。",
        "field_errors": [{"field": "quantity", "code": "REQUIRED", "message": "此字段为必填项。"}],
        "trace_id": "t004-validation",
    }
    print("422:", response.text)


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/missing", 404, "NOT_FOUND"),
        ("/conflict", 409, "VERSION_CONFLICT"),
    ],
)
def test_http_and_conflict_generate_correlated_id(probe, path, status, code) -> None:
    response = probe.get(path)
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert body["field_errors"] == []
    assert re.fullmatch(r"[0-9a-f]{32}", body["trace_id"])
    assert response.headers["X-Request-ID"] == body["trace_id"]


def test_safe_500_and_redacted_correlated_stack(probe, monkeypatch) -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(RequestJsonFormatter())
    logger = logging.getLogger("yihe_api")
    monkeypatch.setattr(logger, "handlers", [handler])
    logger.setLevel(logging.INFO)
    monkeypatch.setattr(logger, "propagate", False)

    response = probe.get(
        "/fail?token=synthetic-query",
        headers={"X-Request-ID": "t004-failure", "Cookie": "session=synthetic-cookie"},
    )
    assert response.status_code == 500
    assert response.json() == {
        "code": "INTERNAL_ERROR",
        "message": "系统暂时无法处理此请求，请稍后重试。",
        "field_errors": [],
        "trace_id": "t004-failure",
    }
    assert response.headers["X-Request-ID"] == response.json()["trace_id"]
    for secret in (
        "synthetic-password",
        "synthetic-token",
        "synthetic-query",
        "synthetic-cookie",
        "6222021234567890123",
        "SELECT *",
        "/private/service.py",
    ):
        assert secret not in response.text
        assert secret not in output.getvalue()

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(records) == 3
    assert all(record["trace_id"] == "t004-failure" for record in records)
    failure = next(record for record in records if record["event"] == "request.exception")
    assert failure["status_code"] == 500
    assert failure["exception_stack"][0]["frames"][-1]["function"] == "fail"
    completion = records[-1]
    assert completion["path"] == "/fail"
    assert completion["method"] == "GET"
    assert completion["time"].endswith("+00:00")
    assert completion["duration_ms"] >= 0
    print("500:", response.text)
    print("Log:", json.dumps(completion, ensure_ascii=False))


def test_malformed_request_id_is_replaced(probe) -> None:
    response = probe.get("/missing", headers={"X-Request-ID": "6222021234567890123"})
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])
    assert response.json()["trace_id"] == response.headers["X-Request-ID"]


def test_error_registry_and_worker_trace_context() -> None:
    assert len(ErrorCode.__members__) == len(ErrorCode) == len(ERRORS)
    assert set(ERRORS) == set(ErrorCode)
    assert outgoing_trace_headers() == {}
    with bind_task_trace({"trace_id": "from-caller"}):
        assert outgoing_trace_headers() == {"trace_id": "from-caller"}
    assert outgoing_trace_headers() == {}
