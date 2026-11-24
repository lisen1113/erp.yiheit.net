"use client";

import { Alert, Button, Card, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography } from "antd";
import type { components } from "@yihe/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest, createIdempotentOperation } from "../../lib/api-client";
import { createAuthSession } from "../../lib/auth-session";
import { permissionState } from "../../lib/authorization";

type Schema = components["schemas"];
type Partner = Schema["PartnerView"];
type FormValues = Schema["PartnerCreate"];
type AuthValues = { username: string; password: string; otp: string };
type Filters = { code?: string; name?: string; unified_social_credit_code?: string; role?: Schema["PartnerType"]; active: boolean };
const endpoint = "/api/v1/business-partners";
const roles = { CUSTOMER: "客户", SUPPLIER: "供应商", BOTH: "客户 / 供应商" };
const roleOptions = Object.entries(roles).map(([value, label]) => ({ value, label }));
const textFields = [
  ["short_name", "简称"], ["unified_social_credit_code", "统一社会信用代码"], ["taxpayer_type", "纳税人类型"],
  ["tax_registration_no", "税务登记号"], ["contact_name", "主要联系人"], ["phone", "电话"], ["email", "电子邮件"],
  ["registered_address", "注册地址"], ["business_address", "经营地址"], ["invoice_title", "发票抬头"], ["tax_no", "发票税号"],
  ["invoice_address_phone", "开票地址及电话"], ["invoice_bank_name", "结算银行"], ["notes", "备注"],
] as const;

export default function BusinessPartners() {
  const [auth] = useState(createAuthSession);
  const [permissions, setPermissions] = useState<Schema["EffectivePermissions"]>();
  const [challenge, setChallenge] = useState<Schema["Challenge"]>();
  const [setupUri, setSetupUri] = useState<string>();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<Filters>({ active: true });
  const [rows, setRows] = useState<Schema["PartnerPage"]>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<Partner | null>();
  const [detail, setDetail] = useState<Partner>();
  const [form] = Form.useForm<FormValues>();
  const pending = useRef<{ fingerprint: string; operation: ReturnType<typeof createIdempotentOperation> }>(null);
  const can = (action: string) => !!permissions && permissionState(permissions, `business_partner.${action}`) === "allowed";
  const report = (err: unknown) => setError(err instanceof Error ? err.message : "请求失败，请重试。");

  const loadPermissions = useCallback(async () => {
    const result = await apiRequest<Schema["EffectivePermissions"]>("/api/v1/auth/me/permissions", {
      headers: auth.authorization(), cache: "no-store",
    });
    setPermissions(result.data);
    const id = new URLSearchParams(window.location.search).get("partner_id");
    if (id && /^[0-9a-f-]{36}$/i.test(id)) {
      try {
        const current = await apiRequest<Partner>(`${endpoint}/${id}`, {
          headers: auth.authorization(), cache: "no-store",
        });
        setDetail(current.data);
      } catch (err) { setError(err instanceof Error ? err.message : "无法读取往来主体。"); }
    }
  }, [auth]);

  async function login(values: AuthValues) {
    setBusy(true);
    setError(undefined);
    try {
      if (!challenge) {
        const response = await auth.login({ username: values.username, password: values.password });
        if (response.status !== "authenticated") {
          if (response.status === "mfa_setup_required") {
            const setup = await auth.setupMfa({ password: values.password, challenge: response.challenge });
            setSetupUri(setup.otpauth_uri);
          }
          setChallenge(response);
          return;
        }
      } else if (setupUri) {
        await auth.enableMfa({ code: values.otp, challenge: challenge.challenge });
      } else {
        await auth.verifyMfa({ code: values.otp, challenge: challenge.challenge });
      }
      setChallenge(undefined);
      setSetupUri(undefined);
      await loadPermissions();
    } catch (err) { report(err); } finally { setBusy(false); }
  }

  const load = useCallback(async (signal?: AbortSignal) => {
    const query = new URLSearchParams({ page: String(page), active: String(filters.active) });
    for (const [key, value] of Object.entries(filters)) if (typeof value === "string" && value) query.set(key, value);
    setLoading(true);
    try {
      const result = await apiRequest<Schema["PartnerPage"]>(`${endpoint}?${query}`, {
        headers: auth.authorization(), cache: "no-store", signal,
      });
      setRows(result.data);
    } catch (err) { if (!signal?.aborted) report(err); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [auth, filters, page]);

  useEffect(() => {
    if (!permissions) return;
    const controller = new AbortController();
    queueMicrotask(() => { if (!controller.signal.aborted) void load(controller.signal); });
    return () => controller.abort();
  }, [load, permissions]);

  async function read(id: string, revealBank = false) {
    return (await apiRequest<Partner>(`${endpoint}/${id}${revealBank ? "?reveal_bank_account=true" : ""}`, {
      headers: auth.authorization(), cache: "no-store",
    })).data;
  }

  async function openEditor(row: Partner | null) {
    setError(undefined);
    try {
      const current = row ? await read(row.id) : null;
      form.resetFields();
      form.setFieldsValue(current ? {
        code: current.code, name: current.name, partner_type: current.partner_type,
        credit_limit: current.credit_limit ?? "0.00", payment_terms_days: current.payment_terms_days ?? 0,
        ...Object.fromEntries(textFields.map(([key]) => [key, current[key]])),
      } : { partner_type: "CUSTOMER", credit_limit: "0.00", payment_terms_days: 0 });
      setEditing(current);
    } catch (err) { report(err); }
  }

  async function write(path: `/api/${string}`, method: "POST" | "PATCH", values: object) {
    setBusy(true);
    setError(undefined);
    const body = JSON.stringify(values);
    const fingerprint = JSON.stringify([path, method, body]);
    if (pending.current?.fingerprint !== fingerprint) {
      pending.current = { fingerprint, operation: createIdempotentOperation(path, {
        method, body, headers: { ...auth.authorization(), "Content-Type": "application/json" },
      }) };
    }
    try {
      await pending.current.operation.send();
      pending.current = null;
      setEditing(undefined);
      await load();
    } catch (err) { report(err); }
    finally { setBusy(false); }
  }

  return <main>
    <Card title={<Typography.Title level={2}>往来单位</Typography.Title>}>
      {error && <Alert type="error" showIcon message={error} />}
      {!permissions ? <Form<AuthValues> layout="vertical" onFinish={login}>
        {!challenge ? <>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input autoComplete="username" /></Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
        </> : <>
          {setupUri && <Input value={setupUri} readOnly aria-label="验证器设置 URI" />}
          <Form.Item name="otp" label="验证器验证码" rules={[{ required: true }]}><Input inputMode="numeric" autoComplete="one-time-code" /></Form.Item>
        </>}
        <Button type="primary" htmlType="submit" loading={busy}>{challenge ? "验证" : "登录"}</Button>
      </Form> : <>
        <Form<Filters> layout="inline" initialValues={filters} onFinish={(value) => { setPage(1); setFilters(value); }}>
          <Form.Item name="code" label="编码"><Input allowClear /></Form.Item>
          <Form.Item name="name" label="名称"><Input allowClear /></Form.Item>
          {(can("view") || can("view_customer") || can("view_supplier")) && <Form.Item name="unified_social_credit_code" label="信用代码"><Input allowClear /></Form.Item>}
          <Form.Item name="role" label="角色"><Select options={roleOptions} allowClear style={{ width: 145 }} /></Form.Item>
          <Form.Item name="active" label="状态"><Select options={[{ value: true, label: "启用" }, { value: false, label: "停用" }]} /></Form.Item>
          <Space><Button htmlType="submit">搜索</Button>{can("create") && <Button type="primary" onClick={() => void openEditor(null)}>新建</Button>}
            <Button onClick={() => void auth.logout().catch(report).finally(() => { setPermissions(undefined); setRows(undefined); setDetail(undefined); setEditing(undefined); pending.current = null; })}>退出</Button>
          </Space>
        </Form>
        <Table rowKey="id" loading={loading} dataSource={rows?.items ?? []}
          pagination={{ current: page, pageSize: 25, showSizeChanger: false, total: rows?.total, onChange: setPage }}
          columns={[
            { title: "编码", dataIndex: "code" }, { title: "名称", dataIndex: "name" },
            { title: "角色", render: (_, row: Partner) => <Tag>{roles[row.partner_type]}</Tag> },
            { title: "状态", render: (_, row: Partner) => row.is_active ? "启用" : "停用" },
            { title: "结算账号", dataIndex: "invoice_bank_account" },
            { title: "操作", render: (_, row: Partner) => <Space>
              <Button onClick={() => void read(row.id, can("bank_account.view_full")).then(setDetail).catch(report)}>详情</Button>
              {can("edit") && <Button onClick={() => void openEditor(row)}>编辑</Button>}
              {can("disable") && <Button disabled={busy} onClick={() => void write(`${endpoint}/${row.id}/${row.is_active ? "disable" : "enable"}`, "POST", { version_no: row.version_no })}>{row.is_active ? "停用" : "启用"}</Button>}
            </Space> },
          ]} />
      </>}
    </Card>
    <Modal title={editing ? "编辑往来单位" : "新建往来单位"} open={editing !== undefined}
      onCancel={() => setEditing(undefined)} onOk={() => form.submit()} confirmLoading={busy}>
      {error && <Alert type="error" message={error} />}
      <Form<FormValues> form={form} layout="vertical" onFinish={(value) => {
        if (!value.invoice_bank_account) delete value.invoice_bank_account;
        void (editing
        ? write(`${endpoint}/${editing.id}`, "PATCH", { ...value, version_no: editing.version_no })
        : write(endpoint, "POST", value));
      }}>
        <Form.Item name="code" label="编码" rules={[{ required: true, max: 32 }]}><Input /></Form.Item>
        <Form.Item name="name" label="名称" rules={[{ required: true, max: 200 }]}><Input /></Form.Item>
        <Form.Item name="partner_type" label="角色" rules={[{ required: true }]}><Select options={roleOptions} /></Form.Item>
        {textFields.map(([key, label]) => <Form.Item key={key} name={key} label={label}><Input /></Form.Item>)}
        <Form.Item name="invoice_bank_account" label="新结算账号" extra="编辑时留空保留原账号"><Input.Password autoComplete="off" /></Form.Item>
        <Form.Item name="credit_limit" label="信用额度（CNY）"><Input placeholder="0.00" inputMode="decimal" /></Form.Item>
        <Form.Item name="payment_terms_days" label="付款账期（天）"><InputNumber min={0} precision={0} /></Form.Item>
      </Form>
    </Modal>
    <Modal title={detail?.name} open={!!detail} footer={null} onCancel={() => setDetail(undefined)}>
      {detail && <dl>{textFields.map(([key, label]) => detail[key] != null && <div key={key}><dt>{label}</dt><dd>{detail[key]}</dd></div>)}
        {detail.invoice_bank_account && <><dt>结算账号</dt><dd>{detail.invoice_bank_account}</dd></>}
        {detail.credit_limit != null && <><dt>信用额度（CNY）</dt><dd>{detail.credit_limit}</dd></>}
      </dl>}
    </Modal>
  </main>;
}
