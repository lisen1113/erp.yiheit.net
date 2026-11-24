import type { components } from "@yihe/contracts";
import { apiRequest } from "./api-client";

type Schema = components["schemas"];
type LoginResult = Schema["Access"] | Schema["Challenge"];
const authPath = "/api/v1/auth";

export function createAuthSession() {
  let accessToken: string | undefined;
  let pendingRefresh: Promise<void> | undefined;

  async function post<T>(path: string, payload?: unknown) {
    const headers = new Headers();
    if (payload !== undefined) headers.set("Content-Type", "application/json");
    if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
    return (await apiRequest<T>(`${authPath}/${path}`, {
      method: "POST",
      headers,
      credentials: "same-origin",
      body: payload === undefined ? undefined : JSON.stringify(payload),
    })).data;
  }

  return {
    authorization() {
      return accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined;
    },
    async login(payload: Schema["Login"]): Promise<LoginResult> {
      accessToken = undefined;
      const result = await post<LoginResult>("login", payload);
      if (result.status === "authenticated") accessToken = result.access_token;
      return result;
    },
    async verifyMfa(payload: Schema["ChallengeVerify"]) {
      accessToken = (await post<Schema["Access"]>("mfa/verify", payload)).access_token;
    },
    async setupMfa(payload: Schema["ConfirmPassword"]) {
      return post<Schema["Setup"]>("mfa/setup", payload);
    },
    async enableMfa(payload: Schema["TotpCode"]) {
      accessToken = (await post<Schema["Access"]>("mfa/enable", payload)).access_token;
    },
    async refresh() {
      pendingRefresh ??= post<Schema["Access"]>("refresh").then(
        (result) => { accessToken = result.access_token; },
        (error: unknown) => { accessToken = undefined; throw error; },
      ).finally(() => { pendingRefresh = undefined; });
      await pendingRefresh;
    },
    async me() {
      const headers = accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined;
      return (await apiRequest<Schema["CurrentUser"]>(`${authPath}/me`, { headers })).data;
    },
    async logout() {
      try {
        await pendingRefresh;
        await post<void>("logout");
      } finally {
        accessToken = undefined;
      }
    },
  };
}
