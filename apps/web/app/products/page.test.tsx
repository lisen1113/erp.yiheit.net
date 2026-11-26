import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Products from "./page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  Reflect.deleteProperty(window, "matchMedia");
});

it("loads the SPU and SKU catalog and opens legacy and import tools for an authorized user", async () => {
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
      permissions: Object.fromEntries(["view", "create", "edit", "import", "legacy_mapping.manage"].map(
        action => [`product.${action}`, ["COMPANY"]],
      )),
      mfa_required_for: [],
    });
    if (path.includes("/product-categories?")) return Response.json({items: []});
    if (path.endsWith("/spus")) return Response.json([{id: "spu", version_no: 1, spu_no: "LAB", name: "合成服务器", is_active: true}]);
    if (path.endsWith("/skus")) return Response.json([{id: "sku", version_no: 1, sku_no: "LAB-SKU", approved_name_cn: "机架服务器", tracking_mode: "serial"}]);
    return Response.json([]);
  }));
  render(<Products />);
  fireEvent.change(screen.getByLabelText("用户名"), {target: {value: "demo"}});
  fireEvent.change(screen.getByLabelText("密码"), {target: {value: "synthetic-password"}});
  fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));
  expect(await screen.findByText("合成服务器")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", {name: "SKU"}));
  expect(await screen.findByText("机架服务器")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", {name: "标准商品导入"}));
  expect(await screen.findByLabelText("商品导入文件")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", {name: "旧商品映射"}));
  fireEvent.click(screen.getByRole("button", {name: /新\s*建/}));
  expect(await screen.findByLabelText("旧编号")).toBeTruthy();
}, 25000);
