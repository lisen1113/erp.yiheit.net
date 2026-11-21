# ADR-0001 Modular Monolith

- Status: Accepted
- Date: 2026-11-21（入库记录日）
- Related Tasks: T001、T002、T010
- Related Specification Sections: 第 1、107、108、114–117 节

## Context

系统为约 5–20 人，通常同时操作不超过 10 人。

## Decision

采用模块化单体，Docker Compose 编排应用、PostgreSQL、Nginx、Redis 及必要 Worker。

## Alternatives

微服务拆分或 Kubernetes。

## Consequences

减少分布式一致性与运维成本，保留业务模块边界。
