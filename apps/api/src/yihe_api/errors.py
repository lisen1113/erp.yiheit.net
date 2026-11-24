"""Public API errors. Only registered messages may be sent to the client."""

from collections.abc import Mapping
from enum import StrEnum, unique
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from yihe_api.tracing import TRACE_HEADER, current_trace_id


@unique
class ErrorCode(StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    BUSINESS_PARTNER_TAX_NO_CONFLICT = "BUSINESS_PARTNER_TAX_NO_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    IDEMPOTENCY_KEY_INVALID = "IDEMPOTENCY_KEY_INVALID"
    IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
    IDEMPOTENCY_REPLAY_FAILED = "IDEMPOTENCY_REPLAY_FAILED"
    IDEMPOTENCY_RETRYABLE = "IDEMPOTENCY_RETRYABLE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    FILE_INVALID = "FILE_INVALID"
    FILE_NOT_AVAILABLE = "FILE_NOT_AVAILABLE"
    FILE_STORAGE_UNAVAILABLE = "FILE_STORAGE_UNAVAILABLE"
    SELF_ESCALATION_DENIED = "SELF_ESCALATION_DENIED"
    AUDIT_WRITE_FAILED = "AUDIT_WRITE_FAILED"
    AUDIT_ACCESS_DENIED = "AUDIT_ACCESS_DENIED"
    AUDIT_EVENT_INVALID = "AUDIT_EVENT_INVALID"
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_INVALID_MFA = "AUTH_INVALID_MFA"
    AUTH_ORIGIN_REJECTED = "AUTH_ORIGIN_REJECTED"
    AUTH_REFRESH_REUSED = "AUTH_REFRESH_REUSED"
    AUTH_PASSWORD_POLICY = "AUTH_PASSWORD_POLICY"
    MFA_REQUIRED = "MFA_REQUIRED"
    DATA_SCOPE_UNAVAILABLE = "DATA_SCOPE_UNAVAILABLE"
    SEPARATION_OF_DUTIES = "SEPARATION_OF_DUTIES"


ERRORS = {
    ErrorCode.BAD_REQUEST: (400, "请求格式有误。"),
    ErrorCode.UNAUTHENTICATED: (401, "请登录后重试。"),
    ErrorCode.FORBIDDEN: (403, "你没有权限执行此操作。"),
    ErrorCode.NOT_FOUND: (404, "找不到请求的资源。"),
    ErrorCode.METHOD_NOT_ALLOWED: (405, "此资源不支持该请求方法。"),
    ErrorCode.CONFLICT: (409, "操作与当前状态冲突，请刷新后重试。"),
    ErrorCode.BUSINESS_PARTNER_TAX_NO_CONFLICT: (
        409,
        "该发票税号已关联其他往来单位，请检查是否为同一主体。",
    ),
    ErrorCode.VERSION_CONFLICT: (409, "数据已更新，请刷新后重试。"),
    ErrorCode.IDEMPOTENCY_KEY_REQUIRED: (422, "缺少幂等请求标识。"),
    ErrorCode.IDEMPOTENCY_KEY_INVALID: (422, "幂等请求标识须为随机 UUIDv4。"),
    ErrorCode.IDEMPOTENCY_IN_PROGRESS: (409, "同一操作正在处理，请稍后重试。"),
    ErrorCode.IDEMPOTENCY_REPLAY_FAILED: (409, "无法安全重放，请联系支持核对原操作。"),
    ErrorCode.IDEMPOTENCY_RETRYABLE: (503, "操作未提交，请使用原请求标识重试。"),
    ErrorCode.IDEMPOTENCY_CONFLICT: (409, "请求与已提交的操作冲突，请核对后重试。"),
    ErrorCode.VALIDATION_ERROR: (422, "请检查填写的字段。"),
    ErrorCode.INTERNAL_ERROR: (500, "系统暂时无法处理此请求，请稍后重试。"),
    ErrorCode.HTTP_ERROR: (400, "请求未能完成。"),
    ErrorCode.FILE_INVALID: (422, "文件类型、大小或名称不符合要求。"),
    ErrorCode.FILE_NOT_AVAILABLE: (409, "文件当前不可使用。"),
    ErrorCode.FILE_STORAGE_UNAVAILABLE: (503, "文件存储暂不可用。"),
    ErrorCode.SELF_ESCALATION_DENIED: (403, "不能变更自己的授权。"),
    ErrorCode.AUDIT_WRITE_FAILED: (500, "无法保存审计记录，操作未提交。"),
    ErrorCode.AUDIT_ACCESS_DENIED: (403, "没有审计查询权限。"),
    ErrorCode.AUDIT_EVENT_INVALID: (422, "审计事件不符合记录规则。"),
    ErrorCode.AUTH_INVALID_CREDENTIALS: (401, "用户名或密码错误。"),
    ErrorCode.AUTH_INVALID_MFA: (401, "验证码无效，请重试。"),
    ErrorCode.AUTH_ORIGIN_REJECTED: (403, "请求来源不受信任。"),
    ErrorCode.AUTH_REFRESH_REUSED: (401, "发现已轮换的凭据被重用，请重新登录。"),
    ErrorCode.AUTH_PASSWORD_POLICY: (422, "新密码不符合账户密码规则。"),
    ErrorCode.MFA_REQUIRED: (403, "请先启用多因素认证，再重新登录。"),
    ErrorCode.DATA_SCOPE_UNAVAILABLE: (403, "部门数据范围尚未启用。"),
    ErrorCode.SEPARATION_OF_DUTIES: (403, "制单人与审批人或终审人必须不同。"),
}

HTTP_CODES = {
    status: code
    for code, (status, _) in ERRORS.items()
    if not code.name.startswith(("AUTH_", "AUDIT_", "SELF_", "IDEMPOTENCY_", "FILE_"))
    and code
    not in {
        ErrorCode.BUSINESS_PARTNER_TAX_NO_CONFLICT,
        ErrorCode.VERSION_CONFLICT,
        ErrorCode.IDEMPOTENCY_CONFLICT,
        ErrorCode.HTTP_ERROR,
        ErrorCode.MFA_REQUIRED,
        ErrorCode.DATA_SCOPE_UNAVAILABLE,
        ErrorCode.SEPARATION_OF_DUTIES,
    }
}

TRACE_HEADERS = {
    TRACE_HEADER: {"description": "请求追踪 ID", "schema": {"type": "string"}},
}

ERROR_RESPONSES: dict[int | str, dict[str, Any]]


@unique
class FieldCode(StrEnum):
    REQUIRED = "REQUIRED"
    INVALID_FORMAT = "INVALID_FORMAT"
    INVALID_VALUE = "INVALID_VALUE"


class FieldError(BaseModel):
    field: str
    code: FieldCode
    message: str


class ErrorResponse(BaseModel):
    code: ErrorCode
    message: str
    field_errors: list[FieldError]
    trace_id: str


ERROR_RESPONSES = {
    status: {"model": ErrorResponse, "description": ERRORS[code][1], "headers": TRACE_HEADERS}
    for status, code in HTTP_CODES.items()
}
ERROR_RESPONSES["default"] = {
    "model": ErrorResponse,
    "description": "其他错误响应",
    "headers": TRACE_HEADERS,
}


class ApiError(Exception):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code)


def error_response(
    code: ErrorCode,
    *,
    status: int | None = None,
    fields: list[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    default_status, message = ERRORS[code]
    body = ErrorResponse(
        code=code, message=message, field_errors=fields or [], trace_id=current_trace_id()
    )
    return JSONResponse(
        body.model_dump(mode="json"), status_code=status or default_status, headers=headers
    )


async def api_error(request: Request, exc: ApiError) -> JSONResponse:
    request.state.audit_error = exc.code
    return error_response(exc.code)


async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code = HTTP_CODES.get(exc.status_code)
    if code is None:
        code = ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else ErrorCode.HTTP_ERROR
    request.state.audit_error = code
    return error_response(code, status=exc.status_code, headers=exc.headers)


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    request.state.audit_error = ErrorCode.VALIDATION_ERROR
    fields = []
    for error in exc.errors():
        kind = error["type"]
        location = error["loc"]
        parts = location[1:] if location[:1] == ("body",) else location
        field = ".".join(map(str, parts)) or "body"
        if kind == "missing":
            code, message = FieldCode.REQUIRED, "此字段为必填项。"
        elif kind.endswith(("_type", "_parsing")) or kind == "json_invalid":
            code, message = FieldCode.INVALID_FORMAT, "字段格式不正确。"
        else:
            code, message = FieldCode.INVALID_VALUE, "字段值不符合要求。"
        fields.append(FieldError(field=field, code=code, message=message))
    return error_response(ErrorCode.VALIDATION_ERROR, fields=fields)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error)  # type: ignore[arg-type]
