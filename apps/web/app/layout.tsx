import type { Metadata } from "next";
import type { ReactNode } from "react";
import "antd/dist/reset.css";
import "./globals.css";

export const metadata: Metadata = { title: "上海以和 ERP", description: "上海以和业务管理系统" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
