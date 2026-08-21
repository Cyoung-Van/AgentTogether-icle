# 矩阵 B：Exact Anchor 历史审计（非主线）

状态：`historical audit / not the runtime B path`

运行时B已经改为稳定Agent家族弱先验：官方介绍页生成七轴先验，版本号不参与匹配，有同家族`family_observation`才收缩，否则用家族先验或\(B_0\)。具体部署偏差由C吸收。

本文只保留公开快照上Exact对照为什么做不成的审计，不再作为下一步建设计划。Harbor完整Trial或统一补测如需研究交叉设计，属于可选实验，不阻断C。

## 1. 公开快照审计结果

严格重建结果为：

```text
Exact anchor blocks                 0
Provisional same-label blocks   7,725
不可比较 multiple 模型块           89
正式 Pairwise                       0
```

这些provisional块不能特化运行时B，也不能当成精确Harness效应。机器可读结果位于：

- `data/matrices/2026-08-19/B_harnesses/exact_anchor_report.json`；
- `data/matrices/2026-08-19/B_harnesses/rejection_summary.json`。

## 2. 为何不再把它当主线

Exact合同要求Benchmark、Task、Fixture、Verifier、环境、资源、策略、Model revision、Provider、effort和Harness version全部对齐。公开排行榜几乎凑不齐这些字段；即便凑齐，也不该把项目重心放在泛化评估Agent×LLM。

项目重点是使用过程中的修正层C。B只需一个略正、很宽、按主流壳介绍页特化的先验。

## 3. 运行时B路径

```text
主流Agent介绍页
→ 稳定家族特征
→ 固定Rubric七轴先验
→ 有家族级观测则收缩
→ 没有则使用该先验或B₀
```

A继续保持current、精确Model config和120天时效。B不跟A同一套更新节奏。
