import type { components } from "@yihe/contracts";
import { apiRequest } from "./api-client";

type Snapshot = components["schemas"]["EffectivePermissions"];

export async function loadPermissions(accessToken: string): Promise<Snapshot> {
  const response = await apiRequest<Snapshot>("/api/v1/auth/me/permissions", {
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });
  return response.data;
}

// UI visibility only. Every request still goes through server authorization.
export function permissionState(snapshot: Snapshot, code: string) {
  const scopes = snapshot.permissions[code] ?? [];
  if (scopes.includes("COMPANY") || scopes.includes("SELF")) return "allowed";
  if (snapshot.mfa_required_for.includes(code)) return "mfa";
  if (scopes.includes("DEPARTMENT")) return "department-unavailable";
  return "denied";
}
