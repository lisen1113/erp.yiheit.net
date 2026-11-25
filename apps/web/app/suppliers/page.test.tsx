import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Suppliers from "./page";

const supplier = {
  id: "supplier-id", business_partner_id: "partner-id", supplier_no: "C-001",
  supplier_type: "HARDWARE", buyer_id: "employee-id", buyer_name: "示例采购",
  is_active: true, version_no: 3, payment_term_days: 15,
  partner: { code: "P-001", name: "双角色示例供应商", partner_type: "BOTH", version_no: 2,
    credit_limit: "1200.25", payment_terms_days: 30, invoice_bank_account: "****9876" },
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  Reflect.deleteProperty(window, "matchMedia");
});

async function login(editable: boolean) {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: vi.fn(() => ({
    matches: false, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })) });
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(node => getStyle(node));
  const requests: { path: string; method?: string; body?: unknown; key: string | null }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    requests.push({ path, method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined,
      key: new Headers(init?.headers).get("Idempotency-Key") });
    if (path.endsWith("/auth/login")) return Response.json({ status: "authenticated", access_token: "synthetic-session" });
    if (path.endsWith("/permissions")) return Response.json({
      permissions: { "supplier.view": ["COMPANY"], ...(editable ? {
        "supplier.create": ["COMPANY"], "supplier.edit": ["COMPANY"], "supplier.import": ["COMPANY"],
        "business_partner.create": ["COMPANY"], "business_partner.edit": ["COMPANY"],
      } : {}) }, mfa_required_for: [],
    });
    if (path.endsWith("/buyers")) return Response.json([{ id: "employee-id", name: "示例采购" }]);
    if (path.startsWith("/api/v1/suppliers?")) return Response.json({ items: [supplier], total: 1, page: 1, page_size: 25 });
    if (path === "/api/v1/suppliers/supplier-id") {
      return Response.json(init?.method === "PATCH" ? { resource_id: supplier.id } : supplier);
    }
    if (path === "/api/v1/suppliers" && init?.method === "POST") return Response.json({ resource_id: supplier.id }, { status: 201 });
    throw new Error("Unexpected request");
  }));
  render(<Suppliers />);
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "demo" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "synthetic-password" } });
  fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));
  await screen.findByText(supplier.partner.name, {}, { timeout: 5000 });
  return requests;
}

it("shows scoped supplier data and does not ask for a full account without permission", async () => {
  const requests = await login(false);
  expect(screen.getByText("¥1,200.25")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /新建供应商|编\s*辑/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /详\s*情/ }));
  expect(await screen.findByText("****9876")).toBeTruthy();
  expect(screen.getByText("客户 / 供应商")).toBeTruthy();
  expect(screen.getByRole("link", { name: "P-001" }).getAttribute("href")).toContain("partner_id=partner-id");
  expect(requests.some(request => request.path.includes("reveal_bank_account"))).toBe(false);
}, 25000);

it("opens the create form and submits edit with both versions and an idempotency key", async () => {
  const requests = await login(true);
  fireEvent.click(screen.getByRole("button", { name: "新建供应商" }));
  const create = await screen.findByRole("dialog", { name: "新建供应商" });
  expect(within(create).getByLabelText("主体编码")).toBeTruthy();
  expect(within(create).getByLabelText("信用额度（CNY）")).toBeTruthy();
  fireEvent.click(within(create).getByRole("button", { name: /取\s*消/ }));
  fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
  const edit = await screen.findByRole("dialog", { name: "编辑供应商" });
  fireEvent.change(within(edit).getByLabelText("供应商编号"), { target: { value: "C-UPDATED" } });
  fireEvent.click(within(edit).getByRole("button", { name: /保\s*存/ }));
  await screen.findByText(supplier.partner.name);
  await vi.waitFor(() => {
    const patch = requests.find(request => request.method === "PATCH");
    expect(patch?.body).toMatchObject({
      supplier_no: "C-UPDATED", version_no: 3, partner: { version_no: 2, credit_limit: "1200.25" },
    });
    expect(patch?.key).toBeTruthy();
  });
}, 25000);

it("shows the standard supplier import dialog", async () => {
  await login(true);
  fireEvent.click(screen.getByRole("button", {name: "导入供应商"}));
  const dialog = await screen.findByRole("dialog", {name: "导入供应商"});
  expect(within(dialog).getByLabelText("供应商导入文件")).toBeTruthy();
}, 25000);
