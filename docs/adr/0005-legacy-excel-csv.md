# ADR-0005 Legacy Migration via Excel/CSV

- Status: Accepted
- Date: 2026-11-21（入库记录日）
- Related Tasks: T000、T010
- Related Specification Sections: 第 112、119 节

## Context

遗留任我行由第三方托管，SQL Server 2000 备份不可作为交付依赖。

## Decision

Excel/CSV 原样归档至 migration/raw，经 staging、清洗映射与对账迁移。原文件禁止改动。

## Alternatives

强制等待数据库备份；手工清洗原文件。

## Consequences

保留批次与行级来源，无法验证的关联只读、如实标记。
