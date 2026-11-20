# T007 审计与安全事件

T004 运行日志用于排障。审计持久化在 PostgreSQL 中，分为公司 `audit_log` 和全局 `security_event`。后者独立授权，Host 匹配只记录线索。两表都没有修改或删除 API。

公司日志以 UUID 主键、必填公司 FK 和 UTC 时间记录操作者账户、员工及会话（FK）、trace、类别、动作、实体类型、可空实体 UUID/引用、结果、原因、before/after、metadata、IP、User-Agent 摘要、来源和保留类别。无认证主体的 SYSTEM / WORKER / MIGRATION 允许空 actor，业务用户 API 和 CLI 必须有操作者。单号通过受控 entity_ref（最多 128 字符）保存，禁止伪造身份或对象。

安全事件记录 UUID、UTC 时间、可空公司、归属来源、可空账户及会话、trace、类型、结果、IP、User-Agent 摘要、方法、路由模板、受控 Host/Origin、标识 HMAC 和受控 metadata。AUTHENTICATED_USER 取验证后的身份。仅固定 Host `erp.yiheit.net` 可通过受控环境变量 `ERP_AUDIT_HOST_COMPANY_ID` 指定真实公司 UUID，记录为 HOST_MAPPING。无映射时为 UNKNOWN，company_id 为 NULL。未受信任的 Host/Origin 只存占位符，未匹配路由不保存原路径。

登录标识先去首尾空白、做 Unicode NFKC 和 casefold，再用独立 Secret `ERP_AUDIT_IDENTIFIER_KEY` 做 HMAC-SHA256。密钥至少 32 字节，不写数据库或日志。未配置时不保存指纹，不退化为普通散列。生产环境应经秘密管理注入配置，由人工运维。

`audit_change(db, ...)` 复用调用者 Session，在业务提交前 flush。审计插入失败返回 AUDIT_WRITE_FAILED 并回滚业务。拒绝事件在响应发送前另开短事务，保留原错误、状态码和 trace ID。安全写入失败仅记录不含原数据的运行事件，不再次触发审计。实现覆盖登录成功、失败、锁定、MFA 设置/启停/验证、Refresh 重用、注销、改密、用户和角色变更、授权和职责分离拒绝及 bootstrap。未来完整银行账号、成本、毛利、敏感导出及原文件下载应在返回内容前调用审计服务。

payload 只保留登记的字段和值类型，在差异前脱敏。密码、密码或 Refresh 哈希、访问 Token、Cookie、MFA Secret 和验证码、数据库连接串、完整账号、身份证号、合同正文、文件和导入原文都禁止记录。银行信息限末四位、变更类型与对象 ID。密码仅标记 password_changed，MFA 仅记录阶段。权限变化记录 added/removed，超限时只记录计数。新增字段应先登记值约束并补敏感数据测试。动作采用模块.对象.动作或已登记的现有事件名，原因用受控代码，实体引用必须来源于已验证的业务标识。

公司查询权限为 system.audit.view。默认 AUDITOR 获得公司范围；未获 system.audit.view_sensitive_metadata 时省略安全 metadata、IP 和 UA 摘要。SYSTEM_ADMIN 默认拥有 system.security_event.view，全局事件不经公司日志端点返回。管理层与普通业务角色无默认全局权限。system.audit.export 已登记，供以后接入。默认查询最近 30 天，按时间和 UUID 降序，页大小上限 200。索引覆盖公司时间、操作者、实体及动作，另有两表 trace 索引和安全时间索引。标准审计在线至少 3 年，CRITICAL 建议保留 10 年；归档和清理在本阶段不执行。

## PostgreSQL 运行权限

迁移安装语句级触发器，拒绝 UPDATE、DELETE 和 TRUNCATE，即使误授 DML 权限。生产 API 应使用非表所有者、非超级用户且无 DDL 权限的现有应用角色，由迁移所有者按部署流程授予下面的权限。请用实际角色名替换示例名称。

```sql
REVOKE ALL ON TABLE audit_log, security_event FROM PUBLIC, erp_app;
GRANT SELECT, INSERT ON TABLE audit_log, security_event TO erp_app;
```

应用 API 再按 RBAC 检查 SELECT。账户不得继承迁移所有者权限。迁移所有者可维护 Schema 或回滚，此权限不应授给运行账号。
