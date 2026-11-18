# T005 认证与会话

迁移 `0002_auth_identity` 从 `0001_company_baseline` 增量创建最小员工主表和认证记录。后续员工主数据通过 Alembic 扩展此表，禁止删除重建。员工号在公司内唯一。用户与员工一对一，`employee_id` 必填且唯一，复合外键阻止跨公司绑定。`username` 是全局唯一登录名，登录时不要求用户提供公司代码。初始账号应通过受控运维流程创建，写入 Argon2id 哈希并绑定现有员工；此阶段没有公开注册接口。

| 表 | 字段 |
| --- | --- |
| employee | id、company_id、employee_no、name、email、mobile、is_active、created_at/by、updated_at/by、version_no |
| app_user | id、company_id、employee_id、username、password_hash、status、failed_login_count、failure_window_at、locked_until、mfa_required、mfa_enabled、mfa_secret_encrypted、mfa_last_step、mfa_setup_expires_at、password_changed_at、last_login_at、created_at/by、updated_at/by、version_no |
| auth_session | id、company_id、user_id、created_at、expires_at、revoked_at、last_used_at |
| auth_refresh_grant | token_hash、session_id、consumed_at |
| auth_login_challenge | token_hash、user_id、expires_at、consumed |

认证 API 位于 `/api/v1/auth`。接口包括 POST login、mfa/verify、refresh、logout、logout-all、mfa/setup、mfa/enable、mfa/disable、change-password 和 GET me。密码确认错误和未知用户名使用一致的提示；未知用户使用 Argon2id 哑哈希验证。在 15 分钟窗口内连续失败 5 次会锁定 15 分钟并撤销现有会话。完整登录成功清零。过期窗口重新累计。账户或员工停用时，服务端拒绝继续刷新及使用 Access Token。

密码使用 Argon2id v19，64 MiB、3 轮、4 并行通道、16 字节随机盐、32 字节哈希。新密码 12–128 字符，拒绝纯空白和与用户名相同的密码。数据库只保存密码哈希。改密、登出全部、MFA 变更和账户锁定会撤销相关会话及未用挑战。

HS256 Access Token 有效期 900 秒，包含 issuer、sub、user_id、company_id、session_id、iat、exp、jti。每次使用都检查对应会话及账户状态。Refresh Session 固定 7 天，轮换不延长。每次轮换会消耗旧授权并创建新授权；数据库只存随机凭据的 SHA-256 摘要。重放已消耗授权时，在数据库提交整条会话的撤销后返回 AUTH_REFRESH_REUSED。用户行和会话行锁保证并发中只有一个轮换成功，竞争的重放仍会导致该会话撤销。

刷新 Cookie 为 `__Secure-erp_refresh`，使用 HttpOnly、Secure、SameSite=Lax、Path=/api/v1/auth、按剩余会话秒数设置 Max-Age，无 Domain。浏览器必须使用 HTTPS。所有认证 POST 检查唯一的 Origin 和 Host，二者都必须与独立配置的 HTTPS 来源匹配。通过代理时传递公开 Host，禁止信任任意客户端提供的转发头。Access Token 放在 Bearer 请求头；Web 会话助手只在内存保存它。

MFA 使用 6 位、30 秒周期的 RFC 6238 TOTP。前后各接受一周期，成功后记录步号，阻止同一用户重放。认证挑战随机生成、只保存摘要、5 分钟有效；未完成 MFA 无正式 Access 或 Refresh。已登录用户重新确认密码后可开始配置；强制 MFA 账户可在密码挑战内完成首次配置。配置阶段只返回一次新生成的 otpauth URI，启用后禁止再次读取。MFA secret 用独立密钥的 Fernet 进行认证加密。强制 MFA 账户不能自行禁用。管理员、财务、管理层选择机制留给后续权限任务，当前通过账户级 mfa_required 表达。

配置 API 运行时 `ERP_AUTH_ORIGIN`、`ERP_AUTH_JWT_KEY`（至少 32 字节的高熵值）、`ERP_AUTH_MFA_KEY`（Fernet 生成值），以及 T003 数据库变量。密钥应由环境或密钥服务注入，.env.example 留空。T002 的 HTTP 入口用于开发健康检查，真实浏览器认证需要 HTTPS 终止点。开发可按 T003 的 Compose overlay 启动数据库，另以受控环境文件传入 API 服务的认证变量；严禁降低 Cookie 安全属性。生产密钥和 MFA 密文轮换由独立运维流程管理。

失败记录保留 UTC 时间、T004 trace_id、受控客户端 IP 和最多 512 字符的 User-Agent SHA-256 摘要；不记录账号、密码、验证码、URI、Cookie 或原始 Token。认证成功和错误沿用统一响应追踪。CI 用随机合成账户、独立密钥及 PostgreSQL 16 测试，并运行依赖与密钥扫描。

直接依赖及锁定版本：argon2-cffi 25.1.0（MIT，密码哈希）；PyJWT 2.15.1（MIT，签发和验证安全 Token）；PyOTP 2.12.0（MIT，TOTP）；cryptography 50.0.2（Apache-2.0 OR BSD-3-Clause，Fernet 加密）。
