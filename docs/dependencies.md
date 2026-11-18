# T001 新增依赖用途与许可证

完整解析版本和完整性记录见 uv.lock、pnpm-lock.yaml。以下为直接依赖；本任务不引入额外主要业务框架。

| 依赖 | 用途 | 许可证 |
| --- | --- | --- |
| FastAPI、Pydantic v2 | API 与响应模型 | MIT |
| SQLAlchemy 2.x、Alembic | 同步 ORM 与后续迁移框架 | MIT |
| psycopg / psycopg-binary | PostgreSQL 驱动 | LGPL-3.0-or-later；二进制包另附第三方组件许可证 |
| uvicorn | ASGI 开发服务 | BSD-3-Clause |
| httpx | API 测试客户端 | BSD-3-Clause |
| pytest、ruff、mypy | 后端测试、格式与类型检查 | MIT |
| hatchling | API wheel 和源码包构建 | MIT |
| Next.js、React、React DOM | App Router 前端与渲染 | MIT |
| Ant Design 5 | 统一企业界面组件 | MIT |
| TypeScript | 前端类型检查 | Apache-2.0 |
| openapi-typescript | 从 OpenAPI 生成前端类型 | MIT |
| Vitest、Testing Library React、jsdom | 契约测试与页面组件测试 | MIT |
| ESLint、eslint-config-next | 前端静态检查 | MIT |
| @types/node、@types/react、@types/react-dom | 开发类型声明 | MIT |

pnpm 仅允许 esbuild 必需的安装脚本。Celery、Redis、打印、认证和 Excel 依赖由对应后续任务按冻结选型加入，不在本次创建空壳实现。
CI 使用 Gitleaks 8.24.3（MIT）扫描源码密钥；迁移升级/回滚检查在 T003 加入。
