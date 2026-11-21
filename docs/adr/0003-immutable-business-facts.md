# ADR-0003 Immutable Business Facts

- Status: Accepted
- Date: 2026-11-21（入库记录日）
- Related Tasks: T003、T007、T010
- Related Specification Sections: 第 23、26.1、87、88 节

## Context

库存、资金、总代返利与往来需要完整可追溯。

## Decision

库存、资金、总代返利和已过账 AR/AP 禁止 UPDATE/DELETE，只能写反向冲销。

## Alternatives

覆盖原记录或软删除。

## Consequences

posting 服务须在同一事务写入权威流水及关联信息。
