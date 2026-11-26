"use client";

import Link from "next/link";
import { Card, Typography } from "antd";

export default function Home() {
  return <main><Card><Typography.Title level={1}>上海以和 ERP</Typography.Title>
    <Typography.Paragraph>业务管理系统工程基线已就绪。</Typography.Paragraph>
  <Link href="/business-partners">往来单位</Link>{" · "}<Link href="/customers">客户管理</Link>{" · "}<Link href="/suppliers">供应商管理</Link>{" · "}<Link href="/product-categories">商品分类</Link>{" · "}<Link href="/products">商品管理</Link>
  </Card></main>;
}
