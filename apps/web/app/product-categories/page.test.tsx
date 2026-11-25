import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ProductCategories from "./page";

afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();
  Reflect.deleteProperty(window, "matchMedia");
});

it("shows the category tree, rule designer and two-stage import controls", async () => {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: vi.fn(() => ({
    matches: false, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })) });
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(node => getStyle(node));
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/auth/login")) return Response.json({ status: "authenticated", access_token: "synthetic-session" });
    if (path.endsWith("/permissions")) return Response.json({
      permissions: Object.fromEntries(["view", "create", "edit", "schema.manage", "import"]
        .map(action => [`product_category.${action}`, ["COMPANY"]])),
      mfa_required_for: [],
    });
    if (path.endsWith("?mode=tree")) return Response.json({
      items: [{ id: "root", code: "SERVICES", name: "专业服务", parent_id: null,
        is_active: true, sort_order: 0, attribute_schema: {}, version_no: 1 }],
    });
    if (path.endsWith("/schema-preview")) return Response.json({
      effective_schema: { type: "object", properties: { service_region: {type: "string", title: "交付地区"} } },
      errors: {},
    });
    throw new Error("Unexpected request");
  }));
  render(<ProductCategories />);
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "demo" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "synthetic-password" } });
  fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));
  expect(await screen.findByText("专业服务（SERVICES）")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "新建根分类"}));
  fireEvent.click(await screen.findByRole("button", {name: "添加属性"}));
  fireEvent.change(screen.getByLabelText("属性键"), {target: {value: "service_region"}});
  fireEvent.click(screen.getByRole("button", {name: "预览有效规则"}));
  expect(await screen.findByLabelText("交付地区")).toBeTruthy();
  expect(screen.getByLabelText("分类导入文件")).toBeTruthy();
  expect((screen.getByRole("button", {name: "提交导入"}) as HTMLButtonElement).disabled).toBe(true);
}, 25000);
