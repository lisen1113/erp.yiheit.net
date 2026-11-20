import type { components } from "@yihe/contracts";

type ErrorPayload = components["schemas"]["ErrorResponse"];
type FieldError = components["schemas"]["FieldError"];
export const TRACE_HEADER = "X-Request-ID";
export const IDEMPOTENCY_HEADER = "Idempotency-Key";

const statusMessages: Partial<Record<number, string>> = {
  401: "请登录后重试。",
  403: "你没有权限执行此操作。",
  404: "找不到请求的资源。",
  409: "操作与当前状态冲突，请刷新后重试。",
  422: "请检查填写的字段。",
  500: "系统暂时无法处理此请求，请稍后重试。",
};

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code?: ErrorPayload["code"],
    readonly traceId?: string,
    readonly fieldErrors: FieldError[] = [],
  ) {
    super(
      status === 0 ? "网络连接失败，请重试。" :
      status >= 500 ? statusMessages[500] :
      statusMessages[status] ?? "请求未能完成，请重试。",
    );
    this.name = "ApiRequestError";
  }
}

function isFieldError(value: unknown): value is FieldError {
  if (typeof value !== "object" || value === null) return false;
  const field = value as Partial<FieldError>;
  return typeof field.field === "string" && typeof field.message === "string" &&
    typeof field.code === "string";
}

export async function apiRequest<T>(
  path: `/api/${string}`,
  init?: RequestInit,
): Promise<{ data: T; traceId?: string; replayed?: true }> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch (error) {
    if (init?.signal?.aborted) throw error;
    throw new ApiRequestError(0);
  }

  const traceId = response.headers.get(TRACE_HEADER) ?? undefined;
  if (!response.ok) {
    const raw: unknown = await response.json().catch(() => null);
    const payload = typeof raw === "object" && raw !== null ? raw as Partial<ErrorPayload> : null;
    throw new ApiRequestError(
      response.status,
      typeof payload?.code === "string" ? payload.code : undefined,
      traceId ?? (typeof payload?.trace_id === "string" ? payload.trace_id : undefined),
      Array.isArray(payload?.field_errors) ? payload.field_errors.filter(isFieldError) : [],
    );
  }
  return {
    data: (response.status === 204 || init?.method?.toUpperCase() === "HEAD"
      ? undefined
      : await response.json()) as T,
    traceId,
    ...(response.headers.get("Idempotency-Replayed") === "true" ? { replayed: true } : {}),
  };
}

export function mapApiFields(error: ApiRequestError) {
  if (error.status !== 422) return [];
  const fields = new Map<string, string[]>();
  for (const field of error.fieldErrors) {
    const messages = fields.get(field.field) ?? [];
    messages.push(field.message);
    fields.set(field.field, messages);
  }
  return Array.from(fields, ([path, errors]) => ({
    name: path.split(".").map((part) => /^\d+$/.test(part) ? Number(part) : part),
    errors,
  }));
}


type IdempotentInit = Omit<RequestInit, "method" | "body" | "signal"> & {
  method: "POST" | "PUT" | "PATCH" | "DELETE";
  body?: string;
};

/** New instance per business operation. Keep it in memory and call send again after a timeout. */
export function createIdempotentOperation<T>(path: `/api/${string}`, init: IdempotentInit) {
  const headers = new Headers(init.headers);
  headers.set(IDEMPOTENCY_HEADER, globalThis.crypto.randomUUID());
  const request = { ...init, headers };
  return {
    send(signal?: AbortSignal) {
      return apiRequest<T>(path, { ...request, signal });
    },
  };
}
