"use client";

import { Alert, Button, Card, Checkbox, Form, Input, InputNumber, Select, Space, Table, Tabs, Typography } from "antd";
import type { components } from "@yihe/contracts";
import { useRef, useState } from "react";
import { apiRequest, createIdempotentOperation } from "../../lib/api-client";
import { createAuthSession } from "../../lib/auth-session";
import { permissionState } from "../../lib/authorization";

type Grants = components["schemas"]["EffectivePermissions"];
type Row = { id: string; version_no: number; [key: string]: unknown };
type Rule = { type: string; title?: string; default?: unknown; enum?: (string | number | boolean)[] };
type Schema = { properties?: Record<string, Rule>; required?: string[] };
type Category = { id: string; name: string; parent_id: string | null; display_path: string };
type Resource = "brands" | "units" | "spus" | "skus" | "legacy-mappings";
type ImportResult = { creates: number; errors: {row: number; field: string; message: string}[]; preview_token?: string };
const path = "/api/v1/products";
const sections: {key: Resource | "import"; label: string}[] = [
  { key: "spus", label: "SPU" }, { key: "skus", label: "SKU" },
  { key: "brands", label: "品牌" }, { key: "units", label: "计量单位" },
  { key: "import", label: "标准商品导入" }, { key: "legacy-mappings", label: "旧商品映射" },
];
const modes = ["quantity", "serial", "batch", "non_stock"].map(value => ({value}));

export default function Products() {
  const [auth] = useState(createAuthSession);
  const [grants, setGrants] = useState<Grants>();
  const [challenge, setChallenge] = useState<components["schemas"]["Challenge"]>();
  const [setupUri, setSetupUri] = useState<string>();
  const [data, setData] = useState<Partial<Record<Resource, Row[]>>>({});
  const [categories, setCategories] = useState<Category[]>([]);
  const [tab, setTab] = useState<Resource | "import">("spus");
  const [editing, setEditing] = useState<Row | null>();
  const [fields, setFields] = useState<Schema>();
  const [requests, setRequests] = useState<Row[]>();
  const [modeSku, setModeSku] = useState<Row>();
  const [error, setError] = useState("");
  const [file, setFile] = useState<File>();
  const [report, setReport] = useState<ImportResult>();
  const [importBody, setImportBody] = useState<{format: string; content_b64: string}>();
  const [templateCategory, setTemplateCategory] = useState<string>();
  const [form] = Form.useForm();
  const pending = useRef<{ identity: string; operation: ReturnType<typeof createIdempotentOperation> } | null>(null);
  const can = (permission: string) => !!grants && permissionState(grants, `product.${permission}`) === "allowed";
  const headers = () => ({ ...auth.authorization(), "Content-Type": "application/json" });
  function fail(value: unknown) { setError(value instanceof Error ? value.message : "请求失败。"); }

  async function load(resource: Resource) {
    const result = await apiRequest<Row[]>(`${path}/${resource}`, { headers: headers() });
    setData(all => ({...all, [resource]: result.data}));
  }

  async function login(values: { username: string; password: string; otp: string }) {
    try {
      if (challenge) {
        const payload = {challenge: challenge.challenge, code: values.otp};
        if (setupUri) await auth.enableMfa(payload); else await auth.verifyMfa(payload);
      } else {
        const result = await auth.login({username: values.username, password: values.password});
        if (result.status !== "authenticated") {
          if (result.status === "mfa_setup_required") {
            setSetupUri((await auth.setupMfa({challenge: result.challenge, password: values.password})).otpauth_uri);
          }
          setChallenge(result);
          return;
        }
      }
      const result = await apiRequest<Grants>("/api/v1/auth/me/permissions", {headers: headers()});
      setChallenge(undefined); setSetupUri(undefined); setGrants(result.data);
      await Promise.all([load("spus"), load("brands"), load("units"), load("skus")]);
      const tree = await apiRequest<{items: Category[]}>("/api/v1/product-categories?mode=tree&active=true", {headers: headers()});
      setCategories(tree.data.items);
    } catch (e) { fail(e); }
  }

  async function write(url: `/api/${string}`, method: "POST" | "PATCH", payload: object) {
    setError("");
    try {
      const body = JSON.stringify(payload);
      const identity = JSON.stringify([url, method, body]);
      if (pending.current?.identity !== identity) {
        pending.current = {identity, operation: createIdempotentOperation(url, {method, headers: headers(), body})};
      }
      await pending.current.operation.send();
      pending.current = null;
      setEditing(undefined); setRequests(undefined);
      if (tab !== "import") await load(tab);
      else { setReport(undefined); setFile(undefined); await Promise.all([load("spus"), load("skus")]); }
    } catch (e) { fail(e); }
  }

  async function skuRules(spuId: string) {
    const spu = data.spus?.find(item => item.id === spuId);
    if (!spu) return;
    try {
      const result = await apiRequest<Schema>(`/api/v1/product-categories/${String(spu.category_id)}/effective-schema`, {headers: headers()});
      setFields(result.data);
    } catch (e) { fail(e); }
  }

  function open(row: Row | null) {
    setEditing(row); setRequests(undefined); setFields(undefined); form.resetFields();
    if (row) {
      const attributes = Object.fromEntries(Object.entries((row.attributes ?? {}) as Record<string, unknown>)
        .map(([key, value]) => [key, Array.isArray(value) ? JSON.stringify(value) : value]));
      form.setFieldsValue({...row, attributes});
      if (tab === "skus") void skuRules(String(row.spu_id));
    } else {
      form.setFieldsValue({allow_purchase: true, allow_sale: true, product_type: "physical"});
    }
  }

  function save(values: Record<string, unknown>) {
    if (tab === "import") return;
    const input = {...values};
    if (tab === "skus") {
      const attributes = (input.attributes ?? {}) as Record<string, unknown>;
      try {
        input.attributes = Object.fromEntries(Object.entries(attributes)
          .filter(([, value]) => value !== undefined)
          .map(([key, value]) => [key, fields?.properties?.[key]?.type === "array" && typeof value === "string" ? JSON.parse(value) : value]));
      } catch (e) { fail(e); return; }
    }
    if (editing) {
      const fieldsByResource: Record<Resource, string[]> = {
        brands: ["name"], units: ["name"], spus: ["name", "name_en", "description"],
        skus: ["spec_name", "approved_name_cn", "approved_name_en", "attributes", "barcode", "allow_purchase", "allow_sale"],
        "legacy-mappings": ["status", "target_sku_id", "reason"],
      };
      const body = Object.fromEntries(fieldsByResource[tab].map(field => [field, input[field]]));
      const legacy = tab === "legacy-mappings";
      void write(`${path}/${tab}/${editing.id}${legacy ? "/review" : ""}`, legacy ? "POST" : "PATCH", {...body, version_no: editing.version_no});
    } else void write(`${path}/${tab}`, "POST", input);
  }

  async function preview() {
    if (!file) return;
    try {
      const format = file.name.toLowerCase().split(".").at(-1);
      if (!format || !["csv", "xlsx"].includes(format) || file.size > 256 * 1024) throw new Error("请选择不超过 256 KiB 的 Excel 或 CSV。");
      const bytes = new Uint8Array(await file.arrayBuffer());
      const payload = {format, content_b64: btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(""))};
      setReport((await apiRequest<ImportResult>("/api/v1/product-import/preview", {method: "POST", headers: headers(), body: JSON.stringify(payload)})).data);
      setImportBody(payload);
    } catch (e) { fail(e); }
  }

  async function template(format: "xlsx" | "csv") {
    if (!templateCategory) return;
    try {
      const response = await fetch(`/api/v1/product-import/templates/${templateCategory}?format=${format}`, {headers: headers()});
      if (!response.ok) throw new Error("下载模板失败。");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `products.${format}`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (e) { fail(e); }
  }

  function options(resource: Resource, label: string) {
    return (data[resource] ?? []).filter(row => row.is_active !== false).map(row => ({value: row.id, label: String(row[label])}));
  }

  const leaves = categories.filter(row => !categories.some(child => child.parent_id === row.id))
    .map(row => ({value: row.id, label: row.display_path || row.name}));
  const readTracking = async (row: Row) => {
    try {
      setEditing(undefined);
      setModeSku(row);
      setRequests((await apiRequest<Row[]>(`${path}/skus/${row.id}/change-tracking-mode`, {headers: headers()})).data);
    } catch (e) { fail(e); }
  };

  return <main>
    <Typography.Title level={2}>商品管理</Typography.Title>
    {error && <Alert type="error" showIcon message={error} />}
    {!grants ? <Form layout="vertical" onFinish={login}>
      {setupUri && <Typography.Paragraph copyable>{setupUri}</Typography.Paragraph>}
      {challenge ? <Form.Item name="otp" label="动态验证码" rules={[{required: true}]}><Input /></Form.Item> : <>
        <Form.Item name="username" label="用户名" rules={[{required: true}]}><Input /></Form.Item>
        <Form.Item name="password" label="密码" rules={[{required: true}]}><Input.Password /></Form.Item>
      </>}
      <Button type="primary" htmlType="submit">登录</Button>
    </Form> : <>
      <Button onClick={() => void auth.logout().catch(fail).finally(() => {
        setGrants(undefined); setData({}); setEditing(undefined); setRequests(undefined);
        setReport(undefined); setFile(undefined); setImportBody(undefined); pending.current = null;
      })}>退出</Button>
      <Tabs activeKey={tab} items={sections.filter(item => item.key === "import" ? can("import") :
        item.key !== "legacy-mappings" || can("legacy_mapping.manage"))} onChange={key => {
        const section = key as Resource | "import"; setTab(section); setEditing(undefined); setRequests(undefined);
        if (section !== "import") void load(section).catch(fail);
      }} />
      {tab === "import" ? <Card title="标准商品导入">
        <Space wrap>
          <Select aria-label="模板分类" options={leaves} value={templateCategory} onChange={setTemplateCategory} />
          <Button disabled={!templateCategory} onClick={() => void template("xlsx")}>下载 Excel 模板</Button>
          <Button disabled={!templateCategory} onClick={() => void template("csv")}>下载 CSV 模板</Button>
          <Input aria-label="商品导入文件" type="file" accept=".xlsx,.csv" onChange={event => {setFile(event.target.files?.[0]);setReport(undefined);setImportBody(undefined);}} />
          <Button disabled={!file} onClick={() => void preview()}>预检</Button>
          <Button disabled={!report?.preview_token || !importBody} onClick={() => {
            if (importBody && report?.preview_token && window.confirm("确认提交商品主数据？")) {
              void write("/api/v1/product-import/commit", "POST", {...importBody, preview_token: report.preview_token});
            }
          }}>提交导入</Button>
        </Space>
        {report && <ul>{report.errors.map((row, index) => <li key={index}>第 {row.row} 行 · {row.field} · {row.message}</li>)}</ul>}
      </Card> : <>
        {(tab === "legacy-mappings" ? can("legacy_mapping.manage") : can("create")) && <Button onClick={() => open(null)}>新建</Button>}
        <Table<Row> rowKey="id" dataSource={data[tab] ?? []} columns={[
          {title: "编号", render: (_, row) => String(row.sku_no ?? row.spu_no ?? row.legacy_product_id ?? row.code)},
          {title: "名称", render: (_, row) => String(row.approved_name_cn ?? row.legacy_name ?? row.name)},
          {title: "模式 / 状态", render: (_, row) => String(row.tracking_mode ?? row.mapping_status ?? (row.is_active ? "启用" : "停用"))},
          {title: "操作", render: (_, row) => <Space wrap>
            {(can("edit") || tab === "legacy-mappings") && <Button onClick={() => open(row)}>{tab === "legacy-mappings" ? "人工审核" : "详情 / 编辑"}</Button>}
            {can("disable") && ["spus", "skus"].includes(tab) && <Button onClick={() => {
              if (window.confirm("确认变更启停状态？")) void write(`${path}/${tab}/${row.id}/${row.is_active ? "disable" : "enable"}`, "POST", {version_no: row.version_no});
            }}>{row.is_active ? "停用" : "启用"}</Button>}
            {tab === "spus" && can("catalog.approve") && !row.approved_at &&
              <Button onClick={() => void write(`${path}/spus/${row.id}/approve`, "POST", {version_no: row.version_no})}>审核 SPU</Button>}
            {tab === "skus" && (can("tracking_mode.change") || can("tracking_mode.approve")) &&
              <Button onClick={() => void readTracking(row)}>模式审批</Button>}
          </Space>},
        ]} />
      </>}
      {editing !== undefined && tab !== "import" && <Card title={editing ? "详情 / 编辑" : "新建"}>
        <Form form={form} layout="vertical" onFinish={save}>
          {["brands","units","spus"].includes(tab) && <>
            {!editing && <Form.Item name={tab === "spus" ? "spu_no" : "code"} label="编码" rules={[{required: true}]}><Input /></Form.Item>}
            <Form.Item name="name" label="名称" rules={[{required: true}]}><Input /></Form.Item>
          </>}
          {tab === "spus" && <>
            {!editing && <>
              <Form.Item name="category_id" label="分类" rules={[{required: true}]}><Select options={leaves} /></Form.Item>
              <Form.Item name="brand_id" label="品牌"><Select options={options("brands","name")} /></Form.Item>
              <Form.Item name="product_type" label="产品类型"><Select options={["physical","software","service","subscription","lease"].map(value => ({value}))} /></Form.Item>
            </>}
            <Form.Item name="name_en" label="英文名"><Input /></Form.Item>
            <Form.Item name="description" label="描述"><Input.TextArea /></Form.Item>
          </>}
          {tab === "skus" && <>
            {!editing && <>
              <Form.Item name="sku_no" label="SKU编码" rules={[{required: true}]}><Input /></Form.Item>
              <Form.Item name="spu_id" label="SPU" rules={[{required: true}]}><Select options={(data.spus ?? []).filter(spu => spu.is_active && spu.approved_at).map(spu => ({value: spu.id, label: String(spu.name)}))} onChange={value => void skuRules(value)} /></Form.Item>
              <Form.Item name="manufacturer_part_no" label="厂商料号"><Input /></Form.Item>
              <Form.Item name="unit_id" label="单位"><Select options={options("units","name")} /></Form.Item>
              <Form.Item name="default_tax_rate" label="默认税率"><InputNumber min={0} max={1} stringMode /></Form.Item>
              <Form.Item name="tracking_mode" label="跟踪模式"><Select options={modes} /></Form.Item>
            </>}
            <Form.Item name="approved_name_cn" label="中文标准名称" rules={[{required: true}]}><Input /></Form.Item>
            <Form.Item name="approved_name_en" label="英文标准名称"><Input /></Form.Item>
            <Form.Item name="spec_name" label="规格描述" rules={[{required: true}]}><Input /></Form.Item>
            <Form.Item name="barcode" label="条码"><Input /></Form.Item>
            {Object.entries(fields?.properties ?? {}).map(([key, rule]) =>
              <Form.Item key={key} name={["attributes",key]} label={rule.title || key} initialValue={rule.default}
                rules={[{required: fields?.required?.includes(key)}]}
                valuePropName={rule.type === "boolean" && !rule.enum ? "checked" : "value"}>
                {rule.enum ? <Select options={rule.enum.map(value => ({value: typeof value === "boolean" ? String(value) : value, label: String(value)}))} /> :
                  rule.type === "boolean" ? <Checkbox /> : ["integer","number"].includes(rule.type) ?
                  <InputNumber precision={rule.type === "integer" ? 0 : undefined} /> : <Input />}
              </Form.Item>)}
            <Form.Item name="allow_purchase" valuePropName="checked"><Checkbox>允许采购</Checkbox></Form.Item>
            <Form.Item name="allow_sale" valuePropName="checked"><Checkbox>允许销售</Checkbox></Form.Item>
          </>}
          {tab === "legacy-mappings" && <>
            {!editing ? <>
              <Form.Item name="legacy_system" label="来源系统" rules={[{required: true}]}><Input /></Form.Item>
              <Form.Item name="legacy_product_id" label="旧编号" rules={[{required: true}]}><Input /></Form.Item>
              <Form.Item name="legacy_name" label="旧名称" rules={[{required: true}]}><Input /></Form.Item>
            </> : <>
              <Form.Item name="status" label="人工裁决" rules={[{required: true}]}><Select options={["CONFIRMED","REJECTED","RETIRED"].map(value => ({value}))} /></Form.Item>
              <Form.Item name="reason" label="原因" rules={[{required: true}]}><Input /></Form.Item>
            </>}
            <Form.Item name="target_sku_id" label="目标SKU"><Select options={options("skus","sku_no")} /></Form.Item>
          </>}
          <Button htmlType="submit" type="primary">保存</Button>
          <Button onClick={() => setEditing(undefined)}>取消</Button>
        </Form>
      </Card>}
      {requests && <Card title="跟踪模式审批记录">
        {modeSku && can("tracking_mode.change") && <Form layout="inline" onFinish={values =>
          void write(`${path}/skus/${modeSku.id}/change-tracking-mode`, "POST", {...values, version_no: modeSku.version_no})}>
          <Form.Item name="tracking_mode" label="目标模式" rules={[{required: true}]}>
            <Select options={modes.filter(mode => mode.value !== modeSku.tracking_mode)} style={{minWidth: 140}} />
          </Form.Item>
          <Form.Item name="reason" label="变更原因" rules={[{required: true}]}><Input maxLength={500} /></Form.Item>
          <Button htmlType="submit">提交申请</Button>
        </Form>}
        <ul>{requests.map(item => <li key={item.id}>
          {String(item.from_mode)} → {String(item.to_mode)} · {String(item.reason)}
          {can("tracking_mode.approve") && !item.approved_at && <Button onClick={() => {
            void write(`${path}/skus/${String(item.sku_id)}/change-tracking-mode/approve`, "POST", {request_id: item.id, version_no: item.sku_version});
          }}>批准</Button>}
        </li>)}</ul>
      </Card>}
    </>}
  </main>;
}
