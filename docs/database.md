# T003 PostgreSQL 数据库基线

依据 V2.2 第 23、24、25、33、87 节及 T003。修订 `0001_company_baseline` 建立最小公司主表。公司编码全局唯一是第 25 节明确规定的主表例外。当前仅有公司 UUID、编码、名称和公共审计/软删除字段。完整公司资料、账号与权限随对应任务交付，审计人本阶段已为必填 UUID，账号表上线后通过迁移补上外键。

| 项目 | 规则 |
| --- | --- |
| 命名 | 表和列使用英文小写 snake_case，表名用单数，外键用 `<entity>_id`；约束和索引按公共 metadata 命名。 |
| 主键 | PostgreSQL UUID，服务端 UUID4，ORM 使用 `uuid4`，直接 SQL 插入使用 PostgreSQL `gen_random_uuid()`。业务单号独立。 |
| 公司范围 | 核心业务模型使用 `CompanyScopeMixin`，必填且索引，引用 `company.id` 并限制删除。业务唯一约束通常以 `company_id` 开头。公司主表自身是范围根。 |
| 审计 | `created_at`、`updated_at` 为时区感知时间戳并默认当前时间，写入必须带 `created_by`、`updated_by`；`version_no` 默认 1，每次更新递增。当前公司模型已启用 ORM 乐观锁；后续直接 SQL 应在更新条件中核对版本并显式递增。 |
| 时区 | PostgreSQL 会话设 UTC，时间列用 `timestamptz` 表示绝对时刻；业务与前端展示转换到 `Asia/Shanghai`，独立业务日期用 `date`。 |
| 软删除 | `SoftDeleteMixin` 仅用于允许逻辑删除的普通记录，默认 false。 |
| 迁移兼容 | `LegacyMixin` 保留 `is_legacy`、`legacy_system`、`legacy_id`、`migration_batch_id`、`legacy_readonly`。适用模型按公司及来源系统/原 ID 加约束，迁移批次实体上线后补显式外键。 |
| 数值 | 金额 `numeric(18,2)`、数量 `numeric(18,4)`、单位成本 `numeric(18,6)`、税率 `numeric(7,4)`。Python 统一使用 `Decimal`，输入从字符串构造。 |
| 币种 | V1 只允许 CNY。未来核心金额表的 `currency_code` 必须必填、默认 CNY，并以 CHECK 拒绝其他值。本修订无金额表，因此没有新增币种列或外币结构。 |

库存流水、资金流水、总代返利流水及已过账的 AR/AP 禁止直接 UPDATE 或 DELETE。更正通过反向事实，禁止通过 `is_deleted` 隐藏。新增事实表时须审查数据库约束与统一 posting 事务中的写入路径。

同步 SQLAlchemy Session 由调用方界定提交/回滚边界。应用连接池每进程保留 5 个连接，可临时增至 7 个，连接池等待 10 秒。Alembic 使用单次连接并关闭。凭据取自 `POSTGRES_HOST`、`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD`，`POSTGRES_PORT` 默认 5432。

## 开发迁移

先按 infra/README.md 设置本地 .env。以下命令从根目录执行，使用 T002 的私有 PostgreSQL 网络，按需构建 API 镜像。

```sh
docker compose -f compose.yaml -f infra/compose.migrations.yaml up -d --build --wait api
docker compose -f compose.yaml -f infra/compose.migrations.yaml exec -T api alembic upgrade head
docker compose -f compose.yaml -f infra/compose.migrations.yaml exec -T api alembic downgrade -1
docker compose -f compose.yaml -f infra/compose.migrations.yaml exec -T api alembic upgrade head
```

本地新修订使用 `alembic revision --autogenerate -m "描述"`。以下在 API 镜像内运行生成命令，并将生成文件写回工作区。

```sh
docker compose -f compose.yaml -f infra/compose.migrations.yaml run --rm --no-deps -v ./apps/api/alembic/versions:/app/apps/api/alembic/versions api alembic revision --autogenerate -m "描述"
```

生成前先升级到 head；检查生成的升级和回滚，再运行 lint、PostgreSQL 验收及 `alembic check`。保持单一 head。所有持久 Schema 变更只走 Alembic。回滚可能删除数据，开发者应先确认目标是可重建数据库。CI 使用空 PostgreSQL 16 验证升级、回滚、重升、单 head 和模型一致性。
