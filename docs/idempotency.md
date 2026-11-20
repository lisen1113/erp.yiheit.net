# T008 PostgreSQL 幂等框架

审批、过账、冲销、收付款、库存及往来变化、返利、导入提交、外部回调和关键后台任务应接入统一幂等服务。本任务只提供公共服务和测试应用中的演示路由。生产应用不挂载演示或记录查询路由。

调用方先验证认证和权限，再从可信路由、服务端业务动作及规范化后的对象构造 Operation。外部回调和 Worker 未来接入时需要可信的服务身份，不能使用请求提供的公司或伪造用户。创建时使用 entity_type:create；已知对象使用 entity_type:UUID。创建成功后记录实际 resource_type 和 resource_id。客户端每个新操作生成 UUIDv4，每次重试调用同一内存操作实例。Key 上限 128，当前仅受理 36 字符 UUIDv4；拒绝空、重复或非法头。

唯一键为 company_id + actor_user_id + action + object_scope + idempotency_key。同一范围的 method、规范化 path 或指纹不一致时返回 409 IDEMPOTENCY_CONFLICT。不同公司、操作者、动作或对象互相独立。路由须先解析规范对象 ID，并排除反代路径别名等不稳定来源。执行中遇到相同范围的并发请求，返回 409 IDEMPOTENCY_IN_PROGRESS，事务结束后的再请求才对比完整指纹。

指纹使用 SHA-256 对结构化 method、path、action、业务投影计算。键排序，数组保持顺序，null、缺失、字符串和数字区分。Decimal 使用无上下文舍入的十进制系数和指数，禁止 float、非有限值和重复 JSON 键。调用方使用明确业务字段投影，包含版本号及所有影响业务的输入。不得传原始 URL、认证头、Cookie、密码、验证码或文件内容。文件仅投影已验证 SHA-256 与经过许可的元数据。

状态为 IN_PROGRESS、COMPLETED、FAILED_FINAL、FAILED_RETRYABLE、EXPIRED。用 PostgreSQL 事务级 try advisory lock 快速拒绝并发，完整唯一约束及原子插入兜底，行锁保护状态。数据库锁等待上限 3 秒。业务写入、审计、结果状态在同一外层事务内提交。业务回调只允许同数据库写入，不能提交外层事务或调用有不可回滚副作用的外部服务。后续外部副作用必须通过同事务 Outbox 和下游防重解决。

确定性失败先回滚 savepoint 再保存统一错误代码，重放原状态及错误码并使用当前请求 trace。明确的 RolledBackRetry 也先回滚 savepoint，随后记录可重试状态并返回 503。普通异常传播导致整个事务回滚，不写成功标记。进程在提交前退出时 PostgreSQL 释放事务锁并撤销整笔写入；提交后响应丢失仍可重放。发现历史或人工留下的持久 IN_PROGRESS，过期后仍拒绝运行，须核对业务事实和审计后人工裁决。禁止删除记录后盲目重跑。

有效期普通 7 天、外部回调 30 天，可按动作选择。过期后清除响应缓存，保留唯一记录防止旧 Key 重新执行业务。只有经批准保留等价永久防重凭证后才可归档。没有自动清理任务。

响应缓存仅允许固定小回执 resource_type、resource_id、状态、200/201/202 及指向该资源的同源 API Location。禁止 Set-Cookie、令牌、凭据、银行资料和文件。首次回执与重放状态和资源一致，重放增加 Idempotency-Replayed: true。T007 业务日志记录 created、replayed、conflict、in_progress、failed、expired，元数据仅含 Key SHA-256、请求摘要、动作、状态及关联 ID。数据库记录保留 UUID Key，不写原始请求和响应秘密。

前端可用 createIdempotentOperation(path, { method, body })。创建一次后保留返回对象，网络失败时再次调用 send()，不要重建对象，也不写入 localStorage。每次新意图重新创建。服务端无记录查询路由。
