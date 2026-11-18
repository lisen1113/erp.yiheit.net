import { afterEach, expect, it, vi } from "vitest";
import { ApiRequestError, apiRequest, mapApiFields } from "./api-client";

afterEach(() => vi.unstubAllGlobals());

it("retains the response request ID and maps 422 nested errors to Ant Design fields", async () => {
  const fetchMock = vi.fn(async () => Response.json({
    code: "VALIDATION_ERROR",
    trace_id: "trace-body",
    field_errors: [
      { field: "lines.0.quantity", code: "REQUIRED", message: "此字段为必填项。" },
      { field: "lines.0.quantity", code: "INVALID_VALUE", message: "字段值不符合要求。" },
    ],
  }, { status: 422, headers: { "X-Request-ID": "trace-header" } }));
  vi.stubGlobal("fetch", fetchMock);

  const error: unknown = await apiRequest("/api/example", {
    headers: { "X-Request-ID": "from-caller" },
  }).catch((failure: unknown) => failure);
  expect(fetchMock).toHaveBeenCalledWith("/api/example", {
    headers: new Headers({ Accept: "application/json", "X-Request-ID": "from-caller" }),
  });
  expect(error).toBeInstanceOf(ApiRequestError);
  if (!(error instanceof ApiRequestError)) throw new Error("Expected API error");
  expect(error.traceId).toBe("trace-header");
  expect(error.code).toBe("VALIDATION_ERROR");
  expect(mapApiFields(error)).toEqual([{
    name: ["lines", 0, "quantity"],
    errors: ["此字段为必填项。", "字段值不符合要求。"],
  }]);
});

it("handles a non-JSON proxy failure without showing raw response data", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("proxy details", {
    status: 502, headers: { "X-Request-ID": "proxy-trace" },
  })));
  await expect(apiRequest("/api/example")).rejects.toMatchObject({
    status: 502,
    message: "系统暂时无法处理此请求，请稍后重试。",
    traceId: "proxy-trace",
  });
});
