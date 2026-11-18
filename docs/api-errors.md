# T004 API 错误与追踪

第 23 节和第 29.1 节的公共错误格式为 `{code, message, field_errors, trace_id}`。`field_errors` 始终为数组，元素包含 `field`、`code`、`message`。字段路径使用点分段及十进制数组索引，例如 `lines.0.quantity`。正文校验省略 `body` 前缀；query、path 等保留参数来源。字段提示由服务器固定生成，省略输入原值和底层校验消息。

错误码采用大写下划线命名，语义稳定。公共码在 `yihe_api/errors.py` 的 `ErrorCode` 和 `ERRORS` 同步登记。新增业务错误使用模块语义命名，登记状态码及安全的中文消息，通过 `ApiError(ErrorCode.…)` 抛出。HTTP 错误保留状态码及协议头，底层 detail 不回传。执行 `pnpm contracts:generate` 并提交 OpenAPI 及类型生成结果。

| HTTP | 公共码 |
| --- | --- |
| 400 | BAD_REQUEST |
| 401 | UNAUTHENTICATED |
| 403 | FORBIDDEN |
| 404 | NOT_FOUND |
| 405 | METHOD_NOT_ALLOWED |
| 409 | CONFLICT、VERSION_CONFLICT、IDEMPOTENCY_CONFLICT |
| 422 | VALIDATION_ERROR |
| 500 | INTERNAL_ERROR |
| 其他未登记客户端状态 | HTTP_ERROR |

字段码为 REQUIRED、INVALID_FORMAT 和 INVALID_VALUE。

## 请求链路

Nginx 沿用格式有效的 `X-Request-ID`，缺失或无效时用 Nginx 的请求 ID。直连 API 时按相同规则校验，无可用值时生成随机 UUID4 十六进制。调用方应使用专用随机标识，字符限字母、数字、连字符和下划线，最长 64 字符，拒绝纯数字的长号码。响应头为 `X-Request-ID`，错误正文为 `trace_id`。Nginx 访问日志记录最终响应的同一 ID。应用层可用 `outgoing_trace_headers()` 取得任务元数据，未来 worker 可用 `bind_task_trace(headers)` 在任务执行期间恢复上下文。

API 日志使用 UTC ISO 时间、级别、事件、trace_id、方法、路由模板、状态和毫秒耗时。未匹配请求和未完成字段使用占位值或 null。允许列表限制输出字段，丢弃日志自由文本、格式化参数、查询串、原始路径参数、请求头、正文、数据库连接信息。500 在服务端保留异常类型、链和完整帧序列的文件名、函数、行号，省略异常消息、局部变量、源码和绝对路径。客户端只收到通用中文提示及追踪 ID。生产入口应同样关闭 Uvicorn 默认访问日志，避免重复记录未脱敏请求目标。

## 前端调用

组件在 Ant Design `App` provider 内使用统一请求封装和提示 hook。例如：

```tsx
const showApiError = useApiError();
try {
  await apiRequest<HealthResponse>("/api/health");
} catch (error) {
  showApiError(error, form);
}
```

`form` 可省略。传入时，422 通过 `form.setFields` 映射字段，其他状态通过 Ant Design 消息显示通用提示和可复制的请求 ID。请求封装保留成功响应的 trace ID，也接受调用方在请求头传入 ID。

测试命令为 `uv run pytest -s apps/api/tests/test_errors_and_trace.py`、`pnpm --filter @yihe/web test` 和 `bash infra/verify-compose.sh`。Compose 通过 Nginx 发出 404，核对响应头、响应体、API 和 Nginx 两侧日志中的同一个 ID。
