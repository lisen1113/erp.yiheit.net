import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import BusinessPartners from "./page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  Reflect.deleteProperty(window, "matchMedia");
});

it("shows a dual-role partner and its masked account to a read-only user", async () => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: false, addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
    })),
  });
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((node) => getStyle(node));
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/auth/login")) return Response.json({
      status: "authenticated", access_token: "synthetic-browser-session",
    });
    if (path.endsWith("/permissions")) return Response.json({
      permissions: { "business_partner.view": ["COMPANY"] }, mfa_required_for: [],
    });
    if (path.startsWith("/api/v1/business-partners?")) return Response.json({
      total: 1, page: 1, page_size: 25,
      items: [{
        id: "synthetic-partner", code: "DEMO", name: "双角色示例单位",
        partner_type: "BOTH", is_active: true, version_no: 1,
        invoice_bank_account: "****8799",
      }],
    });
    throw new Error("Unexpected request");
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<BusinessPartners />);
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "demo-readonly" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "synthetic-password" } });
  fireEvent.click(screen.getByRole("button", { name: "登 录" }));
  expect(await screen.findByText("双角色示例单位", {}, { timeout: 5000 })).toBeTruthy();
  expect(screen.getByText("客户 / 供应商")).toBeTruthy();
  expect(screen.getByText("****8799")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /新\s*建|编\s*辑|停\s*用/ })).toBeNull();
});
