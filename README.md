# 上海以和 Business ERP

依据 docs/ 中 V2.2 冻结规格书及根目录 AGENTS.md。当前包含 T001 初始化 monorepo 与 T002 本地 Docker 开发环境。

## 环境

Node.js 24+、pnpm 11.25.0、uv 0.13.9、Python 3.12（由 uv 安装）。

```sh
uv sync --frozen --all-packages
pnpm install --frozen-lockfile
pnpm lint
pnpm test
pnpm build
pnpm contracts:check
```

开发：`uv run uvicorn yihe_api.main:app --app-dir apps/api/src --reload`；另一个终端运行 `pnpm --filter @yihe/web dev`。API `/health` 仅检查进程；数据库就绪检查在 T002 实施。

目录：apps/api（FastAPI）、apps/web（Next.js App Router + Ant Design 5）、packages/contracts（OpenAPI 与生成类型）、infra（后续 Docker 配置）、docs（规格与验收证据）。

修改 API 后运行 `pnpm contracts:generate` 并提交 OpenAPI 和生成类型。金额业务尚未实现；后续必须使用 Decimal，不得使用 float。

每次任务指令应列出任务编号、引用章节、允许修改目录、完成标准。每批 1–3 个相关任务，通过检查和人工审查后再进入下一批。

本地容器开发环境见 [Docker Compose 使用说明](infra/README.md)。
