import { afterEach, expect, it, vi } from "vitest";
import { createIdempotentOperation, IDEMPOTENCY_HEADER } from "./api-client";

afterEach(() => vi.unstubAllGlobals());

it("reuses a key after an ambiguous network failure and recognizes replay", async () => {
  const keys: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init: RequestInit) => {
    keys.push(new Headers(init.headers).get(IDEMPOTENCY_HEADER) ?? "");
    if (keys.length === 1) throw new TypeError("network failure");
    return Response.json({ accepted: true }, { headers: { "Idempotency-Replayed": "true" } });
  }));
  const operation = createIdempotentOperation("/api/v1/test", { method: "POST", body: "{}" });
  await expect(operation.send()).rejects.toMatchObject({ status: 0 });
  await expect(operation.send()).resolves.toMatchObject({ data: { accepted: true }, replayed: true });
  expect(keys[0]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(keys[1]).toBe(keys[0]);
  await createIdempotentOperation("/api/v1/test", { method: "POST", body: "{}" }).send();
  expect(keys[2]).not.toBe(keys[0]);
});
