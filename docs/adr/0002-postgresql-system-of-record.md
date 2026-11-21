# ADR-0002 PostgreSQL as System of Record

- Status: Accepted
- Date: 2026-11-21（入库记录日）
- Related Tasks: T002、T003、T010
- Related Specification Sections: 第 12、23、107 节

## Context

账务、库存事实和审计要能在一致事务内提交。

## Decision

PostgreSQL 16 是业务事实唯一数据库，Redis 可用于队列或缓存。

## Alternatives

用 Redis 或其他数据库作为账务事实源。

## Consequences

核心事务设计以 PostgreSQL 约束和 Alembic 为基础。
