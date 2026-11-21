# 合成种子及业务剧本

种子仅用于专用开发或测试 PostgreSQL。使用名称 yihe_erp_seed_*，先将空库迁移到 head。配置 PostgreSQL 连接变量，设置 ERP_ENV=development 或 test，并通过环境变量 ERP_SEED_DEFAULT_PASSWORD 注入符合 12–128 字符策略的测试密码。命令不会打印密码、不会修改已有账户密码，也不会使用生产 bootstrap 的密码。

```sh
uv run --frozen alembic -c apps/api/alembic.ini upgrade head
uv run --frozen python -m yihe_api.cli seed-dev
uv run --frozen python -m yihe_api.cli seed-reset
uv run --frozen python scripts/seed/validate_fixtures.py
```

未知环境与 production 在连接数据库之前即拒绝。开发连接仅接受回环地址或 Compose 的 postgres 服务名。隔离容器 CI 可在 test 指定 ERP_SEED_TEST_DB_HOST 为预期的私网容器 IP。库中存在其他公司时拒绝。种子只调用 PostgreSQL，不需要 SMTP、COS、认证签名密钥或文件处理服务。

公司、员工、用户和角色绑定使用固定 UUIDv5。角色复用 T006 自然键与模板，首次按数据库惯例分配 UUID，连续重复运行保留现有 ID；重置再建时可变化。全局权限登记保留，避免误删共有权限。公司为上海以和测试公司，九人分别绑定八种现有角色，合成邮箱为 example.invalid。高敏岗位仍要求 MFA。Seed 重复执行保留已生成密码哈希和测试用户状态。固定业务日期为 2026-01-15。

reset 只删除固定公司下匹配的种子身份及角色映射。遇到非种子行、不可变审计或会话等外键引用，整笔回滚，保留历史。对已经使用过、有保留引用的测试库，由测试环境负责人另建空库，不通过此命令绕过外键或删除审计。

客户、供应商、部门、SKU、仓库、银行账户、品牌示例及 S001–S010 只保留 JSON 定义。T009 合成 Logo 可用 scripts/seed/brand_sample.py 生成，通过本地 T009 接口上传，seed-dev 不调用文件服务，也不向 file_asset 或 brand_asset 写未验证数据。Fixture 校验可独立执行，保持业务场景引用有效，金额为 Decimal 字符串，币种固定 CNY。后续正式 Schema 具备后再接对应入库适配器。
