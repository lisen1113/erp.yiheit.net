# ERP 开发工作流

V2.2 是唯一规格基线。每次任务列明任务编号、章节、可修改路径和完成标准，确认前置任务已合并及开放问题已关闭，按一个任务一个分支和 PR 交付。执行顺序及红线以根目录 AGENTS.md 为准。

涉及架构决定时使用 docs/adr/0000-template.md。规格未明确时登记 docs/open-questions.md，保留历史问题，暂停有争议的部分。改变库存、成本、AR/AP、资金、返利、税额、SN 或历史迁移的冻结口径，按 docs/change-requests/CR-template.md 提交 CR。一般优化登记 Backlog。

提交前运行 lint、类型、单元和按需集成测试；有数据库变更时执行升级、回滚和再升级；有 OpenAPI 变化时重新生成前端类型。PR 记录范围、测试证据和回滚方式。CI 通过后由人工审查合并，生产操作由人工执行。种子及场景规则见 seed-data.md。
