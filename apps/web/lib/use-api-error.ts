"use client";

import { App } from "antd";
import type { FormInstance } from "antd";
import { ApiRequestError, mapApiFields } from "./api-client";

export function useApiError() {
  const { message } = App.useApp();
  return (error: unknown, form?: Pick<FormInstance, "setFields">) => {
    if (!(error instanceof ApiRequestError)) {
      void message.error("请求未能完成，请重试。");
      return;
    }
    if (form && error.status === 422) form.setFields(mapApiFields(error));
    const content = error.traceId ? `${error.message}（追踪 ID ${error.traceId}）` : error.message;
    void message.error(content);
  };
}
