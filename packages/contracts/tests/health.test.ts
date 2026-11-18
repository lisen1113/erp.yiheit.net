import { describe, expect, it } from "vitest";
import schema from "../openapi.json";
import type { components } from "../src/api";

describe("health API contract", () => {
  it("keeps the generated response aligned with OpenAPI", () => {
    const response: components["schemas"]["HealthResponse"] = { status: "ok" };
    expect(schema.components.schemas.HealthResponse.properties.status.const).toBe(response.status);
    expect(schema.paths["/health"].get.operationId).toBe("getHealth");
  });
});
