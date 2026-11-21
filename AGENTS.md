# AGENTS.md — 上海以和 Business ERP

> 唯一业务与技术依据：《上海以和ERP 规格书 V2.2（冻结版，2026-11-18）》，下称“规格书”。
> 本文件是规格书第115、118–120节在代码仓库中的落地版本。两者冲突时以规格书为准。

---

## 1. 开始任何任务前
1. 阅读本文件和任务提示中引用的规格书章节。
2. 任务提示必须包含：任务ID（如 T053）、引用章节、允许修改的目录、Done 标准。缺任何一项，先停下来询问。
3. 确认依赖任务已合并（Definition of Ready：依赖已合并、涉及的表和状态已在规格书中定义、验收用例已列出、相关开放问题已关闭）。

## 2. 冻结技术栈（规格书第107节，不得替换）
- 后端：Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2.x（同步会话）、Alembic；依赖管理 uv；ruff + mypy
- 数据库：PostgreSQL 16；Schema 只通过 Alembic 迁移变更
- 任务队列：Celery 5 + Redis 7（队列：default / import / export / print；print 与 export 并发=1）
- 前端：Next.js（App Router）+ TypeScript + Ant Design 5；pnpm；前端类型由 OpenAPI 生成
- PDF：Playwright + Chromium + Noto Sans CJK SC（独立 print worker 容器）
- Excel：openpyxl（导出用 write_only 流式模式）+ 标准 csv
- 认证：Argon2id 密码 + TOTP；短期 Access Token + httpOnly Secure Refresh Cookie
- 测试：pytest + 容器化 PostgreSQL、Vitest、Playwright E2E
- 新增第三方依赖须在 PR 说明用途与许可证；不得引入其他主要框架、数据库或微服务架构。

## 3. 范围边界（V1）
- **仅人民币**：本位币和交易币种固定为 CNY。核心金额表保留 `currency_code`，默认且仅允许 `CNY`（CHECK 约束）。
  - 不得创建：`exchange_rate`、`foreign_amount`、`base_currency_amount`、`exchange_gain_loss`。
  - 界面不提供币种选择。T097 已延期，不得实现。
- V1 不做：外币与汇兑、完整总账与会计凭证、税务申报、银企直联、Partner Center 实时对接、HR/工资、OA、移动 App、自动电子盖章、完整租赁会计、BI 数据仓库。

## 4. 不可违反的规则
- 金额 `numeric(18,2)`，数量 `numeric(18,4)`，单位成本 `numeric(18,6)`。任何层（数据库、API、前端、导出）禁止 float。
- 库存、AR/AP、资金、返利的变化**只能**经统一 posting 服务，在**单一数据库事务**内写入；接口层禁止直接写事实表。
- 事实表（inventory_transaction、bank_transaction、distributor_rebate_ledger、已过账 AR/AP）禁止 UPDATE/DELETE；错误只能写反向冲销。
- 所有过账、审批、导入提交、外部回调接口必须支持 `Idempotency-Key` 与 `version_no`（乐观锁）。
- 移动加权平均按**过账顺序**计算，不按业务日期回溯重算（B2）。
- 订单预计返利不形成应收；真实返利只进总代返利总账；返利抵扣不修改库存成本（第五部分）。
- RBAC 权限在后端执行：无成本权限时 API 不返回成本/毛利字段，不能只在前端隐藏。
- 制单人与审批人不得为同一人（付款、退款、返利、成本/价差调整、负库存、反结账）。
- 已结账期间禁止新增、修改、反审核单据。
- 公司银行账户唯一主表为 `company_bank_account`，外键统一命名 `company_bank_account_id`。
- 唯一约束默认包含 `company_id`。
- 关键业务变化与业务审计同事务写入；无可靠身份的拒绝事件进入独立安全事件记录。

## 5. 老系统迁移规则（规格书第112、119节）
- 来源：第三方二次开发的老版任我行分销ERP（SQL Server 2000，第三方托管）。**不提供数据库备份**。
- 主通道：任我行导出 Excel/CSV → `migration/raw`（原样归档）→ staging → 清洗映射 → PostgreSQL。不得依赖数据库备份。
- 导出文件编码为 GBK/GB18030；含损坏字节的行必须记入异常清单，不得静默丢弃。
- `migration/raw` 下的文件禁止修改；每个文件登记文件名、大小、SHA-256、导出日期、导出人、数据范围、行数。
- 每条迁入记录必须保存：来源系统、迁移批次、来源文件、Sheet、行号、原编码、映射目标ID。
- 所有清洗由可重复运行的脚本完成；人工判断（SKU 合并、分类纠正）以映射表形式留存，并记录审核人。
- **严禁编造历史关联**：无法从导出数据验证的关联关系，以“原系统历史记录，关联不可验证”只读迁入。
- 迁移数据一律不进入正式过账流程；已结历史记录 `legacy_readonly=true`。
- 开发和测试只使用合成数据或脱敏样本，真实导出文件不得提交到 Git 仓库。

## 6. 遇到规格未覆盖或有歧义
- **停止实现该点**，在 `docs/open-questions.md` 登记（任务ID、问题、可选方案、影响），继续完成其余部分。
- 不得自行推断业务规则并写入代码。
- 架构性决定写入 `docs/adr/NNNN-标题.md`。
- 影响库存、成本、往来、资金、返利、税额、SN 或迁移的规格变更，只能由人工通过变更单（CR-xxx）批准。

## 7. 每个 PR 必须包含
- 一个任务一个分支/PR
- Alembic 迁移（如有），且可升级并回滚一版
- OpenAPI 变更及重新生成的前端类型
- 测试：正常流程、部分执行、重复请求、并发、越权、回滚、边界金额
- 文档更新与自测证据（命令输出）
- CI 全绿（lint、类型检查、测试、迁移、密钥扫描）后才可合并

## 8. 数据与环境
- 只使用 `scripts/seed` 生成的数据；禁止真实银行账号、真实客户联系人进入开发和测试环境。
- 不得访问或操作生产数据库、生产密钥、生产服务器；部署和数据修复由人工执行。
- 环境：本地 Docker Compose（开发）→ `erp-test.yiheit.net`（测试）→ `erp.yiheit.net`（生产，独立服务器，最低 4C8G）。

## 9. 目录约定
```
apps/api            FastAPI 后端（sales / purchase / inventory / finance / invoice / rebate /
                    master_data / pricing / contract / reporting / printing / migration 模块）
apps/web            Next.js 前端
apps/worker         Celery 任务（import / export / print）
packages/contracts  OpenAPI 与生成的类型
infra/              docker-compose、nginx、部署脚本
migration/          raw（不入 Git）/ staging / mapping / reconciliation / scripts
scripts/seed        种子数据与业务剧本
docs/adr            架构决策记录
docs/open-questions.md
```

## 10. 执行顺序
1. **第一批**：T001 → T002 → T003 → T004 → T005 → T006（含 T101 职责分离基础校验）→ T007 → T008 → T009 → T010 → T011
2. **并行（人工主导）**：T000 任我行 Excel 样本验证（商品、客户、一个月销售与采购明细、库存、应收、少量 SN），不阻塞第一批。
3. 之后按规格书第30节及第116节顺序执行；T097 跳过。
4. 每批 1–3 个紧密相关任务；完成并通过 CI 与人工审查后再开始下一批。
