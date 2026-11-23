import type { Metadata } from "next";
import { connection } from "next/server";
import { App } from "antd";
import type { ReactNode } from "react";
import "antd/dist/reset.css";
import "./globals.css";

export const metadata: Metadata = { title: "上海以和 ERP", description: "上海以和业务管理系统" };

export default async function RootLayout({ children }: { children: ReactNode }) {
  await connection();
  const isTest = process.env.ERP_ENV === "test";
  return <html lang="zh-CN"><body>{isTest && <aside role="status">TEST / 测试环境</aside>}<App>{children}</App></body></html>;
}
