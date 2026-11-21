# ADR-0004 CNY-only V1

- Status: Accepted
- Date: 2026-11-21（入库记录日）
- Related Tasks: T003、T097、T010
- Related Specification Sections: 第 23、118、120 节

## Context

V1 的本位币与交易币固定。

## Decision

核心金额表的 currency_code 仅允许 CNY，T097 延期。

## Alternatives

V1 引入多币种、汇率及汇兑。

## Consequences

外币需走获批 Change Request，当前依然按 Decimal 精度处理。
