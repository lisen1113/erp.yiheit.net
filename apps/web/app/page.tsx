"use client";

import Link from "next/link";
import { Card, Typography } from "antd";

export default function Home() {
  return <main><Card><Typography.Title level={1}>上海以和 ERP</Typography.Title>
    <Typography.Paragraph>业务管理系统工程基线已就绪。</Typography.Paragraph>
  <Link href="/business-partners">往来单位</Link>
  </Card></main>;
}
