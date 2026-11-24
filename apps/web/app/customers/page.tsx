"use client";

import {
  Alert, Button, Card, Descriptions, Form, Input, InputNumber, Modal, Select, Space, Table, Tag,
  Typography,
} from "antd";
import type { components } from "@yihe/contracts";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest, createIdempotentOperation } from "../../lib/api-client";
import { createAuthSession } from "../../lib/auth-session";
import { permissionState } from "../../lib/authorization";

type Schema = components["schemas"];
type Customer = Schema["CustomerView"];
type Credentials = { username: string; password: string; otp: string };
type Filters = { customer_no?: string; name?: string; tax_no?: string; salesperson_id?: string; active: boolean };
type CustomerForm = {
  customer_no: string; customer_type: string; salesperson_id: string; delivery_address?: string;
  business_partner_id?: string; partner?: Schema["CustomerPartnerCreate"];
};
const endpoint = "/api/v1/customers";
const partnerFields = [
  ["name", "主体名称"], ["short_name", "简称"], ["unified_social_credit_code", "统一社会信用代码"],
  ["tax_no", "发票税号"], ["invoice_title", "开票抬头"], ["invoice_address_phone", "开票地址及电话"],
  ["invoice_bank_name", "结算银行"], ["contact_name", "默认联系人"], ["phone", "联系电话"],
  ["email", "电子邮件"], ["notes", "备注"],
] as const;

function formatMoney(value: string | null | undefined) {
  if (value == null) return "—";
  const [units, fraction = ""] = value.split(".");
  return `¥${units.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction.padEnd(2, "0")}`;
}

export default function Customers() {
  const [auth] = useState(createAuthSession);
  const [permissions, setPermissions] = useState<Schema["EffectivePermissions"]>();
  const [challenge, setChallenge] = useState<Schema["Challenge"]>();
  const [setupUri, setSetupUri] = useState<string>();
  const [rows, setRows] = useState<Schema["CustomerPage"]>();
  const [salespeople, setSalespeople] = useState<Schema["SalespersonOption"][]>([]);
  const [filters, setFilters] = useState<Filters>({ active: true });
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Customer | null>();
  const [source, setSource] = useState<"new" | "existing">("new");
  const [detail, setDetail] = useState<Customer>();
  const [disableTarget, setDisableTarget] = useState<Customer>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm<CustomerForm>();
  const [disableForm] = Form.useForm<{ reason: Schema["CustomerDisable"]["reason"] }>();
  const pending = useRef<{ payload: string; operation: ReturnType<typeof createIdempotentOperation> }>(null);
  const can = (code: string) => !!permissions && permissionState(permissions, code) === "allowed";
  const report = (err: unknown) => setError(err instanceof Error ? err.message : "请求失败，请重试。");

  async function login(values: Credentials) {
    setBusy(true);
    setError(undefined);
    try {
      if (challenge) {
        const payload = { challenge: challenge.challenge, code: values.otp };
        if (setupUri) await auth.enableMfa(payload);
        else await auth.verifyMfa(payload);
      } else {
        const result = await auth.login({ username: values.username, password: values.password });
        if (result.status !== "authenticated") {
          if (result.status === "mfa_setup_required") {
            const setup = await auth.setupMfa({ challenge: result.challenge, password: values.password });
            setSetupUri(setup.otpauth_uri);
          }
          setChallenge(result);
          return;
        }
      }
      setChallenge(undefined);
      setSetupUri(undefined);
      const headers = auth.authorization();
      const grants = await apiRequest<Schema["EffectivePermissions"]>("/api/v1/auth/me/permissions", { headers });
      setPermissions(grants.data);
      const employees = await apiRequest<Schema["SalespersonOption"][]>(`${endpoint}/salespeople`, { headers });
      setSalespeople(employees.data);
    } catch (err) { report(err); } finally { setBusy(false); }
  }

  const load = useCallback(async (signal?: AbortSignal) => {
    const query = new URLSearchParams({ page: String(page), active: String(filters.active) });
    for (const [key, value] of Object.entries(filters)) if (typeof value === "string" && value) query.set(key, value);
    try {
      const result = await apiRequest<Schema["CustomerPage"]>(`${endpoint}?${query}`, {
        headers: auth.authorization(), signal, cache: "no-store",
      });
      if (!signal?.aborted) setRows(result.data);
    } catch (err) { if (!signal?.aborted) setError(err instanceof Error ? err.message : "查询失败"); }
  }, [auth, filters, page]);

  useEffect(() => {
    if (!permissions) return;
    const controller = new AbortController();
    queueMicrotask(() => { if (!controller.signal.aborted) void load(controller.signal); });
    return () => controller.abort();
  }, [load, permissions]);

  function openEditor(row: Customer | null) {
    setError(undefined);
    setSource(can("business_partner.create") ? "new" : "existing");
    form.resetFields();
    if (row) {
      form.setFieldsValue({
        customer_no: row.customer_no, customer_type: row.customer_type, salesperson_id: row.salesperson_id,
        delivery_address: row.delivery_address ?? undefined,
        partner: {
          code: row.partner.code, name: row.partner.name,
          ...Object.fromEntries(partnerFields.map(([key]) => [key, row.partner[key]])),
          credit_limit: row.partner.credit_limit ?? "0.00", payment_terms_days: row.partner.payment_terms_days ?? 0,
        },
      });
    } else form.setFieldsValue({ customer_type: "enterprise", partner: { credit_limit: "0.00", payment_terms_days: 0 } });
    setEditing(row);
  }

  async function write(path: `/api/${string}`, method: "POST" | "PATCH", values: object) {
    setBusy(true);
    setError(undefined);
    const body = JSON.stringify(values);
    const payload = JSON.stringify([path, method, body]);
    if (pending.current?.payload !== payload) {
      pending.current = { payload, operation: createIdempotentOperation(path, {
        method, body, headers: { ...auth.authorization(), "Content-Type": "application/json" },
      }) };
    }
    try {
      await pending.current.operation.send();
      pending.current = null;
      setEditing(undefined);
      setDisableTarget(undefined);
      await load();
    } catch (err) { report(err); } finally { setBusy(false); }
  }

  async function read(row: Customer) {
    const query = can("business_partner.bank_account.view_full") ? "?reveal_bank_account=true" : "";
    try {
      const result = await apiRequest<Customer>(`${endpoint}/${row.id}${query}`, {
        headers: auth.authorization(), cache: "no-store",
      });
      setDetail(result.data);
    } catch (err) { report(err); }
  }

  const options = salespeople.map(person => ({ label: person.name, value: person.id }));
  const sharedFields = editing ? can("business_partner.edit") : source === "new";

  return <main>
    <Card title={<Typography.Title level={2}>客户管理</Typography.Title>}>
      {error && <Alert type="error" showIcon message={error} />}
      {!permissions ? <Form<Credentials> layout="vertical" onFinish={login}>
        {!challenge ? <>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input autoComplete="username" /></Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
        </> : <>
          {setupUri && <Input value={setupUri} readOnly aria-label="验证器设置 URI" />}
          <Form.Item name="otp" label="验证器验证码" rules={[{ required: true }]}><Input autoComplete="one-time-code" /></Form.Item>
        </>}
        <Button htmlType="submit" type="primary" loading={busy}>{challenge ? "验证" : "登录"}</Button>
      </Form> : <>
        <Form<Filters> name="customer_search" layout="inline" initialValues={filters} onFinish={value => { setPage(1); setFilters(value); }}>
          <Form.Item name="customer_no" label="客户编号"><Input allowClear /></Form.Item>
          <Form.Item name="name" label="客户名称"><Input allowClear /></Form.Item>
          <Form.Item name="tax_no" label="发票税号"><Input allowClear /></Form.Item>
          <Form.Item name="salesperson_id" label="销售人员"><Select allowClear options={options} style={{ minWidth: 140 }} /></Form.Item>
          <Form.Item name="active" label="状态"><Select options={[{ value: true, label: "启用" }, { value: false, label: "停用" }]} /></Form.Item>
          <Space><Button htmlType="submit">搜索</Button>
            {can("customer.create") && (can("business_partner.create") || can("business_partner.edit")) && <Button type="primary" onClick={() => openEditor(null)}>新建客户</Button>}
            <Button onClick={() => void auth.logout().catch(report).finally(() => {
              setPermissions(undefined); setRows(undefined); setDetail(undefined); setEditing(undefined);
              setDisableTarget(undefined); pending.current = null; form.resetFields();
            })}>退出</Button>
          </Space>
        </Form>
        <Table<Customer> rowKey="id" dataSource={rows?.items ?? []}
          pagination={{ current: page, pageSize: 25, showSizeChanger: false, total: rows?.total, onChange: setPage }}
          columns={[
            { title: "客户编号", dataIndex: "customer_no" },
            { title: "客户名称", render: (_, row) => row.partner.name },
            { title: "类型", dataIndex: "customer_type" },
            { title: "销售人员", dataIndex: "salesperson_name" },
            { title: "信用额度", align: "right", render: (_, row) => formatMoney(row.partner.credit_limit) },
            { title: "账期（天）", render: (_, row) => row.partner.payment_terms_days ?? "—" },
            { title: "状态", render: (_, row) => <Tag>{row.is_active ? "启用" : "停用"}</Tag> },
            { title: "操作", render: (_, row) => <Space>
              <Button onClick={() => void read(row)}>详情</Button>
              {can("customer.edit") && <Button onClick={() => openEditor(row)}>编辑</Button>}
              {can("customer.disable") && (row.is_active
                ? <Button danger onClick={() => { disableForm.resetFields(); setDisableTarget(row); }}>停用</Button>
                : <Button loading={busy} onClick={() => void write(`${endpoint}/${row.id}/enable`, "POST", { version_no: row.version_no })}>启用</Button>)}
            </Space> },
          ]} />
      </>}
    </Card>
    <Modal title={editing ? "编辑客户" : "新建客户"} open={editing !== undefined} okText="保存" cancelText="取消"
      onCancel={() => setEditing(undefined)} onOk={() => form.submit()} confirmLoading={busy} forceRender>
      {editing !== undefined && error && <Alert type="error" message={error} />}
      {editing === null && <Select aria-label="主体来源" value={source} onChange={setSource} options={[
        { value: "new", label: "新建往来主体", disabled: !can("business_partner.create") },
        { value: "existing", label: "关联现有往来主体", disabled: !can("business_partner.edit") },
      ]} />}
      <Form<CustomerForm> name="customer_editor" form={form} layout="vertical" onFinish={values => {
        const { partner, business_partner_id, ...profile } = values;
        const shared = partner ? { ...partner } : undefined;
        if (shared && !shared.invoice_bank_account) delete shared.invoice_bank_account;
        if (editing) {
          void write(`${endpoint}/${editing.id}`, "PATCH", {
            ...profile, version_no: editing.version_no,
            ...(sharedFields && shared ? { partner: { ...shared, version_no: editing.partner.version_no } } : {}),
          });
        } else {
          void write(endpoint, "POST", { ...profile, ...(source === "new" ? { partner: shared } : { business_partner_id }) });
        }
      }}>
        <Form.Item name="customer_no" label="客户编号" rules={[{ required: true, max: 32 }]}><Input /></Form.Item>
        <Form.Item name="customer_type" label="客户类型" rules={[{ required: true, max: 32 }]}><Input /></Form.Item>
        <Form.Item name="salesperson_id" label="销售人员" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" options={options} /></Form.Item>
        <Form.Item name="delivery_address" label="收货地址"><Input /></Form.Item>
        {editing === null && source === "existing" &&
          <Form.Item name="business_partner_id" label="往来主体 UUID" rules={[{ required: true }]}><Input /></Form.Item>}
        {sharedFields && <>
          <Form.Item name={["partner", "code"]} label="主体编码" rules={[{ required: true, max: 32 }]}><Input /></Form.Item>
          {partnerFields.map(([key, label]) =>
            <Form.Item name={["partner", key]} label={label} key={key} rules={[{ required: key === "name" }]}><Input /></Form.Item>)}
          <Form.Item name={["partner", "credit_limit"]} label="信用额度（CNY）"><Input inputMode="decimal" /></Form.Item>
          <Form.Item name={["partner", "payment_terms_days"]} label="默认账期（天）"><InputNumber min={0} precision={0} /></Form.Item>
          <Form.Item name={["partner", "invoice_bank_account"]} label="新结算账号" extra="留空保留原账号"><Input.Password autoComplete="off" /></Form.Item>
        </>}
      </Form>
    </Modal>
    <Modal title="确认停用客户" open={!!disableTarget} okText="确认停用" cancelText="取消" onCancel={() => setDisableTarget(undefined)}
      onOk={() => disableForm.submit()} confirmLoading={busy} forceRender>
      <Typography.Paragraph>{disableTarget?.partner.name}停用后不可用于新交易。</Typography.Paragraph>
      <Form form={disableForm} layout="vertical" onFinish={({ reason }) => {
        if (disableTarget) void write(`${endpoint}/${disableTarget.id}/disable`, "POST", { version_no: disableTarget.version_no, reason });
      }}>
        <Form.Item name="reason" label="停用原因" rules={[{ required: true }]}><Select options={[
          { value: "customer_request", label: "客户要求" }, { value: "duplicate", label: "档案重复" },
          { value: "inactive", label: "停止往来" }, { value: "other", label: "其他" },
        ]} /></Form.Item>
      </Form>
    </Modal>
    <Modal title={detail?.partner.name} open={!!detail} footer={null} onCancel={() => setDetail(undefined)}>
      {detail && <Descriptions column={1} items={[
        { key: "partner", label: "统一主体", children: <Link href={`/business-partners?partner_id=${detail.business_partner_id}`}>{detail.partner.code}</Link> },
        { key: "roles", label: "主体角色", children: detail.partner.partner_type === "BOTH" ? "客户 / 供应商" : "客户" },
        { key: "credit", label: "信用额度", children: formatMoney(detail.partner.credit_limit) },
        { key: "terms", label: "默认账期（天）", children: detail.partner.payment_terms_days },
        ...partnerFields.map(([key, label]) => ({ key, label, children: detail.partner[key] ?? "—" })),
        { key: "bank", label: "结算账号", children: detail.partner.invoice_bank_account ?? "—" },
        { key: "delivery", label: "收货地址", children: detail.delivery_address ?? "—" },
        { key: "created", label: "创建时间", children: detail.created_at },
        { key: "updated", label: "更新时间", children: detail.updated_at },
      ]} />}
    </Modal>
  </main>;
}
