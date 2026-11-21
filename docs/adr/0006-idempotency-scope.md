# ADR-0006 Idempotency Scope

- Status: Accepted
- Date: 2026-11-21（入库记录日）
- Related Tasks: T008、T010
- Related Specification Sections: 第 23、29.2、88 节及 T008 方案 A 裁决

## Context

重复 Key 应隔离公司、操作者、动作和对象。

## Decision

唯一范围用 company + actor + action + object_scope + key。范围内方法、路径或投影不同返回冲突。

## Alternatives

同一用户下所有动作共用 Key。

## Consequences

新操作生成新 UUIDv4；同一次重试复用原 Key。
