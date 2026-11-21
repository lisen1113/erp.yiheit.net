# ADR-0007 Audit vs Security Event

- Status: Accepted
- Date: 2026-11-21（入库记录日）
- Related Tasks: T007、T010
- Related Specification Sections: 第 10、25、89 节及 T007 最终裁决

## Context

认证前的安全拒绝可能无法确认公司或操作者。

## Decision

公司审计写 audit_log 并关联业务事务；全局 security_event 用独立事务记录拒绝。Host 映射仅是提示。

## Alternatives

把不确定的身份强行归入公司业务审计。

## Consequences

两表不可变、查询权限分离，未验证的事件不虚构身份。
