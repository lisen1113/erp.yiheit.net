# T006 RBAC 与 T101 职责分离

修订 `0003_rbac` 继承 `0002_auth_identity`，新增 `role`、`permission`、`user_role` 和 `role_permission`。角色编码在公司内唯一，权限编码全局唯一。用户与角色的映射使用公司复合外键，未撤销分配在公司、用户、角色组合上唯一，过期分配在重新授予时先归档撤销。角色与权限使用复合主键，每种授权只出现一次。

用户名保留原值供显示，数据库存储生成列 `username_normalized = lower(username)` 并设全局唯一。登录使用相同的 PostgreSQL lower 规则。迁移发现既有用户名产生折叠碰撞时会中止。维护人员须先按审批流程处理冲突。角色编码用大写下划线，权限编码用小写点分段和下划线；三段式用于新增权限，第 39 节已有两段式编码继续使用。

预置系统角色有 SYSTEM_ADMIN、MANAGEMENT、FINANCE、SALES_MANAGER、SALES、PURCHASE、WAREHOUSE、AUDITOR。系统管理员模板只含用户、角色和权限管理以及第 39 节允许的公司资料和模板权限。管理层模板有公司资料查看；财务模板有公司资料和银行账号查看；销售、采购按第 39 节有脱敏账号查看，销售有报价创建和渲染。其他模板为空。三类高敏角色 SYSTEM_ADMIN、MANAGEMENT、FINANCE 强制 MFA。未明确授权的财务审批、成本、毛利、完整账号导出等权限默认不授予。完整注册表在 `yihe_api.rbac.policy`，增加新权限时更新定义与角色模板，再通过受控种子流程登记。

每次请求查 PostgreSQL，权限既不写进 Access Token，也不做跨请求缓存。撤销、变更、停用和到期从下一次授权查询生效，无需 TTL 或刷新 Token。高敏权限及要求 MFA 的角色必须有 MFA 已启用且当前会话完成 MFA 的证据。升级前已有会话的该证据默认为 false，重新以 MFA 登录后恢复对应权限。多个角色只合并当前有效的独立授权。用户和员工停用沿用 T005 认证检查。

复用 `require_permission(code)` 检查动作，使用 `scope_filters` 构造数据库过滤，敏感字段通过 `project_fields` 在响应序列化前按记录公司和归属移除。COMPANY 匹配当前公司，SELF 同时匹配公司和用户或员工归属，DEPARTMENT 当前明确拒绝。数据库保留规格书小写存储枚举，API 返回大写枚举。无成本或毛利权限时不返回对应键；有脱敏查看权限时只保留账号末四位，无任何账号权限时省略字段。

管理接口为 `/api/v1/users`、`/api/v1/roles`、`/api/v1/permissions` 和 `/api/v1/auth/me/permissions`。写入先锁定公司，重新计算操作者实时权限，再检查 `version_no`（撤销通过查询参数）。只能委派本人已有的公司级权限。拒绝给自己分配及撤销角色、修改自己持有的角色、修改系统模板的内容。非强制模板可调整 MFA 要求；如要降低要求，操作者必须已经具备对应授权。不能通过这些接口给自己或他人引入本人没有的金融审批权限。系统模板与初次业务高敏授权由受控管理流程维护。变更写结构化安全事件，包含操作者、时间、目标和动作，省略权限清单、凭据和敏感字段。

T101 通过 `assert_separation_of_duties(maker, approver, action, final_approver_user_id=…)` 拒绝制单人兼任审批或终审。规则覆盖付款、退款、返利确认与调整、成本调整、负库存、反结账、公司资料、银行账号及客户信用额度变更。业务层应在自身提交事务内用持久化制单人调用。未知规则拒绝调用，本任务未创建审批业务表。

## 首次管理员

开发环境使用已经迁移的 PostgreSQL，指定主机、库、用户名、密码后，在交互终端运行：

```sh
uv run --frozen python -m yihe_api.cli bootstrap-admin
```

命令提示输入公司、员工、用户名和无回显密码。可复用名称一致且未删除的现有公司。事务内创建最小员工、账户、角色模板及管理员绑定，数据库事务锁防止并行或重复引导。首次正式使用前必须完成 MFA。没有默认用户名或密码，也没有公开 bootstrap API。

## 验证

`uv run --frozen pytest` 验证职责分离与现有单元测试。GitHub Actions 在隔离 PostgreSQL 16 上执行 migrations、authentication、authorization 集成测试，覆盖迁移往返、权限实时撤销、租户隔离、MFA 与继承的认证安全不变量。
