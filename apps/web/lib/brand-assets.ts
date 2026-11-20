import type { components } from "@yihe/contracts";
import { apiRequest, createIdempotentOperation } from "./api-client";

type Schema = components["schemas"];

export async function prepareBrandUpload(file: File, authorization: string) {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const sha256 = Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0")).join("");
  const payload: Schema["Upload"] = {
    asset_type: "BRAND", filename: file.name, media_type: file.type,
    size_bytes: file.size, sha256, sensitivity: "STANDARD",
  };
  const headers = { Authorization: authorization, "Content-Type": "application/json" };
  const request = createIdempotentOperation<Schema["Receipt"]>("/api/v1/files", {
    method: "POST", body: JSON.stringify(payload), headers,
  });
  async function transfer(id: string) {
    const { data: ticket } = await apiRequest<Schema["Ticket"]>(`/api/v1/files/${id}/upload-ticket`, {
      method: "POST", headers: { Authorization: authorization },
    });
    const result = await fetch(ticket.url, {
      method: "PUT", headers: ticket.required_headers, body: bytes, credentials: "omit",
    });
    if (!result.ok) throw new Error("对象上传失败，请重新领取上传票据。");
  }
  function confirmation(id: string, version: number) {
    return createIdempotentOperation<Schema["Receipt"]>(`/api/v1/files/${id}/confirm`, {
      method: "POST", body: JSON.stringify({ version_no: version }), headers,
    });
  }
  return { request, transfer, confirmation };
}

export function changeBrandState(id: string, version: number, active: boolean, authorization: string) {
  return createIdempotentOperation<Schema["Receipt"]>(
    `/api/v1/brand-assets/${id}/${active ? "activate" : "deactivate"}`, {
      method: "POST", body: JSON.stringify({ version_no: version }),
      headers: { Authorization: authorization, "Content-Type": "application/json" },
    },
  );
}


export const publicBrandLogoUrl = "/api/v1/brand-assets/public/logo";

export async function fetchBrandPreview(id: string, authorization: string): Promise<Blob> {
  const headers = { Authorization: authorization };
  const { data: asset } = await apiRequest<Schema["FileView"]>(`/api/v1/files/${id}`, { headers });
  if (asset.asset_type !== "BRAND" || !["image/png", "image/jpeg", "image/webp"].includes(asset.content_type)) {
    throw new Error("此文件不是受支持的品牌预览图。");
  }
  const { data: ticket } = await apiRequest<Schema["Ticket"]>(`/api/v1/files/${id}/download-ticket`, {
    method: "POST", headers,
  });
  if (!ticket.url.startsWith(`/api/v1/files/${id}/content?`)) {
    throw new Error("预览地址无效。");
  }
  const response = await fetch(ticket.url, { headers, cache: "no-store" });
  if (!response.ok) throw new Error("无法读取品牌预览图。");
  return new Blob([await response.arrayBuffer()], { type: asset.content_type });
}
