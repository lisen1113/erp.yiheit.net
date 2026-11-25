"use client";

import { Alert, Button, Card, Checkbox, Form, Input, InputNumber, Select, Space, Tree, Typography } from "antd";
import type { DataNode } from "antd/es/tree";
import type { components } from "@yihe/contracts";
import { useCallback, useRef, useState } from "react";
import { apiRequest, createIdempotentOperation } from "../../lib/api-client";
import { createAuthSession } from "../../lib/auth-session";
import { permissionState } from "../../lib/authorization";

type Category = {
  id: string; code: string; name: string; parent_id: string | null; version_no: number;
  is_active: boolean; sort_order: number; description?: string | null; attribute_schema: Schema;
};
type Rule = { type: string; title?: string; description?: string; [key: string]: unknown };
type Schema = { type?: string; properties?: Record<string, Rule>; required?: string[]; additionalProperties?: boolean };
type Preview = { creates: number; updates: number; conflicts: number; preview_token?: string; errors: { row: number; field: string; message: string }[] };
type Row = { name: string; type: string; title?: string; description?: string; required?: boolean; settings?: string };
type Editor = { code: string; name: string; sort_order: number; description?: string; parent_id?: string | null; rules?: Row[]; additional?: boolean };
type Grants = components["schemas"]["EffectivePermissions"];
const endpoint = "/api/v1/product-categories";
const supported = ["string", "integer", "number", "boolean", "array"];
const ruleSettings = ["default", "enum", "minimum", "maximum", "minLength", "maxLength", "pattern", "items"];

function design(values: Editor): Schema {
  const properties: Record<string, Rule> = {};
  const required: string[] = [];
  for (const row of values.rules ?? []) {
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(row.name) || Object.hasOwn(properties, row.name)) {
      throw new Error("属性键必须是唯一的小写英文技术标识。");
    }
    const settings: Record<string, unknown> = row.settings ? JSON.parse(row.settings) : {};
    if (!settings || Array.isArray(settings) || typeof settings !== "object" ||
        Object.keys(settings).some(key => !ruleSettings.includes(key))) {
      throw new Error("属性设置包含不受支持的关键字。");
    }
    properties[row.name] = { ...settings, type: row.type, ...(row.title ? { title: row.title } : {}),
      ...(row.description ? { description: row.description } : {}) };
    if (row.required) required.push(row.name);
  }
  return { type: "object", properties, required, additionalProperties: values.additional ?? false };
}

function tree(rows: Category[], parent: string | null): DataNode[] {
  return rows.filter(row => row.parent_id === parent).map(row => ({
    key: row.id, title: `${row.name}（${row.code}）${row.is_active ? "" : " · 停用"}`,
    children: tree(rows, row.id),
  }));
}

export default function ProductCategories() {
  const [auth] = useState(createAuthSession);
  const [grants, setGrants] = useState<Grants>();
  const [challenge, setChallenge] = useState<components["schemas"]["Challenge"]>();
  const [setupUri, setSetupUri] = useState<string>();
  const [rows, setRows] = useState<Category[]>([]);
  const [selected, setSelected] = useState<Category>();
  const [effective, setEffective] = useState<Schema>();
  const [previewSchema, setPreviewSchema] = useState<Schema>();
  const [editing, setEditing] = useState(false);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [file, setFile] = useState<File>();
  const [content, setContent] = useState<{ format: string; content_b64: string }>();
  const [report, setReport] = useState<Preview>();
  const [moveParent, setMoveParent] = useState<string>();
  const [form] = Form.useForm<Editor>();
  const pending = useRef<{ identity: string; command: ReturnType<typeof createIdempotentOperation> } | null>(null);
  const can = (permission: string) => !!grants && permissionState(grants, `product_category.${permission}`) === "allowed";
  const headers = useCallback(() => ({ ...auth.authorization(), "Content-Type": "application/json" }), [auth]);
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : "操作失败。");
  const refresh = useCallback(async () => {
    const result = await apiRequest<{ items: Category[] }>(`${endpoint}?mode=tree`, { headers: headers() });
    setRows(result.data.items);
    setSelected(row => row ? result.data.items.find(item => item.id === row.id) : undefined);
  }, [headers]);


  async function login(values: { username: string; password: string; otp: string }) {
    setError("");
    try {
      if (challenge) {
        const payload = { challenge: challenge.challenge, code: values.otp };
        if (setupUri) await auth.enableMfa(payload); else await auth.verifyMfa(payload);
      } else {
        const result = await auth.login({username: values.username, password: values.password});
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
      setGrants((await apiRequest<Grants>("/api/v1/auth/me/permissions", { headers: headers() })).data);
      await refresh();
    } catch (e) { fail(e); }
  }

  async function select(row: Category) {
    setSelected(row);
    setEditing(false);
    setMoveParent(row.parent_id ?? undefined);
    setPreviewSchema(undefined);
    setEffective(undefined);
    try {
      setEffective((await apiRequest<Schema>(`${endpoint}/${row.id}/effective-schema`, { headers: headers() })).data);
    } catch (e) { fail(e); }
  }

  function edit(row?: Category, parent?: string) {
    setError("");
    setSelected(row);
    setEditing(true);
    setPreviewSchema(undefined);
    form.resetFields();
    form.setFieldsValue({
      code: row?.code, name: row?.name, parent_id: row?.parent_id ?? parent,
      sort_order: row?.sort_order ?? 0, description: row?.description ?? undefined,
      additional: row?.attribute_schema.additionalProperties,
      rules: Object.entries(row?.attribute_schema.properties ?? {}).map(([name, rule]) => ({
        name, type: rule.type, title: rule.title, description: rule.description,
        required: row?.attribute_schema.required?.includes(name),
        settings: JSON.stringify(Object.fromEntries(Object.entries(rule).filter(([key]) => ruleSettings.includes(key)))),
      })),
    });
  }

  async function command(path: `/api/${string}`, method: "POST" | "PATCH", values: object) {
    setError("");
    try {
      const body = JSON.stringify(values);
      const identity = JSON.stringify([path, method, body]);
      if (pending.current?.identity !== identity) {
        pending.current = { identity, command: createIdempotentOperation(path, { method, headers: headers(), body }) };
      }
      await pending.current.command.send();
      pending.current = null;
      setFeedback("已保存。");
      setEditing(false);
      await refresh();
    } catch (e) { fail(e); }
  }

  async function save(values: Editor) {
    try {
      const shared = { name: values.name, sort_order: values.sort_order, description: values.description ?? null };
      const definition = can("schema.manage") ? { attribute_schema: design(values) } : {};
      if (selected) {
        await command(`${endpoint}/${selected.id}`, "PATCH", { ...shared, ...definition, version_no: selected.version_no });
      } else {
        await command(endpoint, "POST", { ...shared, ...definition, code: values.code, parent_id: values.parent_id ?? null });
      }
    } catch (e) { fail(e); }
  }

  async function checkDraft(sample?: Record<string, unknown>) {
    try {
      const values = form.getFieldsValue();
      const result = await apiRequest<{ effective_schema: Schema; errors: Record<string, string> }>(
        `${endpoint}/schema-preview`, {
          method: "POST", headers: headers(), body: JSON.stringify({
            parent_id: selected?.parent_id ?? values.parent_id ?? null,
            attribute_schema: design(values), sample,
          }),
        });
      setPreviewSchema(result.data.effective_schema);
      if (sample) {
        const issues = Object.entries(result.data.errors).map(([key, value]) => `${key} ${value}`);
        setFeedback(issues.join("；") || "示例属性验证通过。");
      }
    } catch (e) { fail(e); }
  }

  async function download(format: "xlsx" | "csv") {
    try {
      const result = await fetch(`${endpoint}/imports/template/${format}`, { headers: headers() });
      if (!result.ok) throw new Error("无法下载模板。");
      const url = URL.createObjectURL(await result.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `product-categories.${format}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (e) { fail(e); }
  }

  async function preflight() {
    if (!file) return;
    setReport(undefined);
    setError("");
    try {
      const format = file.name.toLowerCase().split(".").at(-1);
      if (!format || !["csv", "xlsx"].includes(format) || file.size > 256 * 1024) {
        throw new Error("请选择不超过 256 KiB 的 Excel 或 CSV 文件。");
      }
      const bytes = new Uint8Array(await file.arrayBuffer());
      const payload = { format, content_b64: btoa(Array.from(bytes, b => String.fromCharCode(b)).join("")) };
      const result = await apiRequest<Preview>(`${endpoint}/imports/preview`, {
        method: "POST", headers: headers(), body: JSON.stringify(payload),
      });
      setContent(payload);
      setReport(result.data);
    } catch (e) { fail(e); }
  }

  const folded = search.trim().toLowerCase();
  const matching = folded ? rows.filter(row => `${row.name} ${row.code}`.toLowerCase().includes(folded)) : [];
  return <main>
    <Typography.Title level={2}>商品分类</Typography.Title>
    {error && <Alert type="error" message={error} showIcon />}
    {feedback && <Alert type="info" message={feedback} showIcon />}
    {!grants ? <Form layout="vertical" onFinish={login}>
      {setupUri && <Typography.Paragraph copyable>{setupUri}</Typography.Paragraph>}
      {challenge ? <Form.Item name="otp" label="动态验证码" rules={[{ required: true }]}><Input /></Form.Item> :
        <>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password /></Form.Item>
        </>}
      <Button htmlType="submit" type="primary">登录</Button>
    </Form> : <>
      <Space wrap>
        <Input.Search aria-label="搜索分类" allowClear onSearch={setSearch} />
        {can("create") && <Button onClick={() => edit()}>新建根分类</Button>}
        <Button onClick={() => void auth.logout().catch(fail).finally(() => {
          setGrants(undefined); setSelected(undefined); setRows([]); setReport(undefined); setContent(undefined);
          pending.current = null; setFile(undefined); setEditing(false); setEffective(undefined); setPreviewSchema(undefined);
        })}>退出</Button>
      </Space>
      {folded ? <ul>{matching.map(row => <li key={row.id}>
        <Button type="link" onClick={() => void select(row)}>{row.name}（{row.code}）</Button>
      </li>)}</ul> : <Tree treeData={tree(rows, null)} onSelect={keys => {
        const row = rows.find(item => item.id === keys[0]); if (row) void select(row);
      }} />}
      {selected && <Card title={selected.name}>
        <Space wrap>
          {can("create") && <Button onClick={() => edit(undefined, selected.id)}>新建子分类</Button>}
          {can("edit") && <Button onClick={() => edit(selected)}>编辑分类</Button>}
          {can("disable") && <Button onClick={() => {
            if (!selected.is_active || window.confirm("确定停用此分类？")) {
              void command(`${endpoint}/${selected.id}/${selected.is_active ? "disable" : "enable"}`, "POST", { version_no: selected.version_no });
            }
          }}>{selected.is_active ? "停用" : "启用"}</Button>}
          {can("move") && <>
            <Select aria-label="目标父分类" allowClear value={moveParent} placeholder="根分类" onChange={setMoveParent}
              options={rows.filter(row => row.id !== selected.id && row.is_active).map(row => ({ value: row.id, label: row.name }))} />
            <Button onClick={() => void command(`${endpoint}/${selected.id}/move`, "POST",
              { parent_id: moveParent ?? null, version_no: selected.version_no })}>移动</Button>
          </>}
        </Space>
      </Card>}
      {editing && <Card title={selected ? "编辑分类与属性" : "新建分类"}>
        <Form form={form} layout="vertical" onFinish={save}>
          {!selected && <Form.Item label="分类编码" name="code" rules={[{ required: true }]}><Input maxLength={32} /></Form.Item>}
          <Form.Item label="分类名称" name="name" rules={[{ required: true }]}><Input maxLength={100} /></Form.Item>
          <Form.Item label="排序" name="sort_order"><InputNumber min={0} precision={0} /></Form.Item>
          <Form.Item label="分类描述" name="description"><Input maxLength={500} /></Form.Item>
          {!selected && <Form.Item name="parent_id" label="父分类">
            <Select allowClear options={rows.filter(row => row.is_active).map(row => ({ value: row.id, label: row.name }))} />
          </Form.Item>}
          {can("schema.manage") && <>
            <Form.Item name="additional" valuePropName="checked"><Checkbox>允许额外属性</Checkbox></Form.Item>
            <Form.List name="rules">{(fields, operations) => <>
              {fields.map(field => <Card key={field.key} size="small">
                <Form.Item name={[field.name, "name"]} label="属性键" rules={[{ required: true }]}><Input /></Form.Item>
                <Form.Item name={[field.name, "title"]} label="中文显示名"><Input /></Form.Item>
                <Form.Item name={[field.name, "type"]} label="类型" rules={[{ required: true }]}>
                  <Select options={supported.map(value => ({ value }))} />
                </Form.Item>
                <Form.Item name={[field.name, "required"]} valuePropName="checked"><Checkbox>必填</Checkbox></Form.Item>
                <Form.Item name={[field.name, "description"]} label="描述"><Input /></Form.Item>
                <Form.Item name={[field.name, "settings"]} label="默认值、枚举、范围、长度、正则及数组设置">
                  <Input.TextArea placeholder={'{"minimum": 1, "default": 2}'} />
                </Form.Item>
                <Space><Button disabled={field.name === 0} onClick={() => operations.move(field.name, field.name - 1)}>上移</Button>
                  <Button onClick={() => operations.remove(field.name)}>移除属性</Button></Space>
              </Card>)}
              <Button onClick={() => operations.add({ type: "string" })}>添加属性</Button>
            </>}</Form.List>
          </>}
          <Space>
            <Button type="primary" htmlType="submit">保存分类</Button>
            {can("schema.manage") && <Button onClick={() => void checkDraft()}>预览有效规则</Button>}
          </Space>
        </Form>
      </Card>}
      {(previewSchema ?? effective) && <Card title={previewSchema ? "草稿有效规则预览" : "有效属性规则"}>
        <pre>{JSON.stringify(previewSchema ?? effective, null, 2)}</pre>
        <Form layout="vertical" onFinish={async (values: Record<string, unknown>) => {
          if (!selected && !previewSchema) return;
          try {
            const activeRules = (previewSchema ?? effective)?.properties ?? {};
            const sample = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined)
              .map(([key, value]) => [
                key, activeRules[key]?.type === "array" && typeof value === "string" ? JSON.parse(value) : value,
              ]));
            if (previewSchema) { await checkDraft(sample); return; }
            if (!selected) return;
            const result = await apiRequest<Record<string, string>>(`${endpoint}/${selected.id}/validate-attributes`, {
              method: "POST", headers: headers(), body: JSON.stringify(sample),
            });
            const problems = Object.entries(result.data).map(([key, value]) => `${key} ${value}`);
            setFeedback(problems.join("；") || "示例属性验证通过。");
          } catch (e) { fail(e); }
        }}>
          {Object.entries((previewSchema ?? effective)?.properties ?? {}).map(([key, definition]) =>
            <Form.Item key={key} name={key} label={definition.title || key} initialValue={definition.default}
              valuePropName={definition.type === "boolean" && !definition.enum ? "checked" : "value"}>
              {Array.isArray(definition.enum) ? <Select options={definition.enum.map(value => ({ value, label: String(value) }))} /> :
                definition.type === "boolean" ? <Checkbox /> :
                ["number", "integer"].includes(definition.type) ? <InputNumber precision={definition.type === "integer" ? 0 : undefined} /> :
                definition.type === "array" ? <Input placeholder="JSON 数组，例如 [1, 2]" /> : <Input />}
            </Form.Item>)}
          {(selected || previewSchema) && <Button htmlType="submit">验证示例属性</Button>}
        </Form>
      </Card>}
      {can("import") && <Card title="导入分类与属性定义">
        <Space wrap>
          <Button onClick={() => void download("xlsx")}>下载 Excel 模板</Button>
          <Button onClick={() => void download("csv")}>下载 CSV 模板</Button>
          <Input type="file" accept=".xlsx,.csv" aria-label="分类导入文件" onChange={event => {
            setFile(event.target.files?.[0]); setReport(undefined); setContent(undefined);
          }} />
          <Button disabled={!file} onClick={() => void preflight()}>预检</Button>
          <Button type="primary" disabled={!report?.preview_token || !content} onClick={() => {
            if (content && report?.preview_token && window.confirm("确认提交通过预检的分类？")) {
              void command(`${endpoint}/imports/commit`, "POST", { ...content, preview_token: report.preview_token });
            }
          }}>提交导入</Button>
        </Space>
        {report && <>
          <Typography.Paragraph>新增 {report.creates}，更新 {report.updates}，冲突 {report.conflicts}，错误 {report.errors.length}</Typography.Paragraph>
          <ul>{report.errors.map((item, index) => <li key={index}>第 {item.row} 行 · {item.field} · {item.message}</li>)}</ul>
        </>}
      </Card>}
    </>}
  </main>;
}
