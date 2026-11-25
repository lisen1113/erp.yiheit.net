import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Customers from "./page";

const customer = {
  id: "customer-id", business_partner_id: "partner-id", customer_no: "C-001",
  customer_type: "enterprise", salesperson_id: "employee-id", salesperson_name: "示例销售",
  is_active: true, version_no: 3,
  partner: { code: "P-001", name: "双角色示例客户", partner_type: "BOTH", version_no: 2,
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
      permissions: { "customer.view": ["COMPANY"], ...(editable ? {
        "customer.create": ["COMPANY"], "customer.edit": ["COMPANY"],
        "business_partner.create": ["COMPANY"], "business_partner.edit": ["COMPANY"],
      } : {}) }, mfa_required_for: [],
    });
    if (path.endsWith("/salespeople")) return Response.json([{ id: "employee-id", name: "示例销售" }]);
    if (path.startsWith("/api/v1/customers?")) return Response.json({ items: [customer], total: 1, page: 1, page_size: 25 });
    if (path === "/api/v1/customers/customer-id") {
      return Response.json(init?.method === "PATCH" ? { resource_id: customer.id } : customer);
    }
    if (path === "/api/v1/customers" && init?.method === "POST") return Response.json({ resource_id: customer.id }, { status: 201 });
    throw new Error("Unexpected request");
  }));
  render(<Customers />);
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "demo" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "synthetic-password" } });
  fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));
  await screen.findByText(customer.partner.name, {}, { timeout: 5000 });
  return requests;
}

it("shows scoped customer data and does not ask for a full account without permission", async () => {
  const requests = await login(false);
  expect(screen.getByText("¥1,200.25")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /新建客户|编\s*辑/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /详\s*情/ }));
  expect(await screen.findByText("****9876")).toBeTruthy();
  expect(screen.getByText("客户 / 供应商")).toBeTruthy();
  expect(screen.getByRole("link", { name: "P-001" }).getAttribute("href")).toContain("partner_id=partner-id");
  expect(requests.some(request => request.path.includes("reveal_bank_account"))).toBe(false);
}, 25000);

it("opens the create form and submits edit with both versions and an idempotency key", async () => {
  const requests = await login(true);
  fireEvent.click(screen.getByRole("button", { name: "新建客户" }));
  const create = await screen.findByRole("dialog", { name: "新建客户" });
  expect(within(create).getByLabelText("主体编码")).toBeTruthy();
  expect(within(create).getByLabelText("信用额度（CNY）")).toBeTruthy();
  fireEvent.click(within(create).getByRole("button", { name: /取\s*消/ }));
  fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
  const edit = await screen.findByRole("dialog", { name: "编辑客户" });
  fireEvent.change(within(edit).getByLabelText("客户编号"), { target: { value: "C-UPDATED" } });
  fireEvent.click(within(edit).getByRole("button", { name: /保\s*存/ }));
  await screen.findByText(customer.partner.name);
  await vi.waitFor(() => {
    const patch = requests.find(request => request.method === "PATCH");
    expect(patch?.body).toMatchObject({
      customer_no: "C-UPDATED", version_no: 3, partner: { version_no: 2, credit_limit: "1200.25" },
    });
    expect(patch?.key).toBeTruthy();
  });
}, 25000);
