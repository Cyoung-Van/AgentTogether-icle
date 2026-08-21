# Current/History数据治理与Freshness Policy

## 原则

原始历史证据不删除，但运行时默认只加载`current`。最新Snapshot日期不再被解释为“内部全部记录最新”。

策略文件：[freshness_policy.json](../config/freshness_policy.json)。

```text
policy_id                 experience-evaluation-current-evidence/v0.1
A freshness window        120 days
B freshness window        120 days
unknown date current      false
A requires current catalog link
provisional B current     false
```

## 状态

- `active_current`：同精确身份最新、在时间窗口内并通过当前目录门禁；
- `superseded`：相同精确身份存在更新记录；
- `stale`：最新记录也超过窗口；
- `unknown_date`：无法确定结果日期；
- `future_dated`：结果日期晚于Snapshot as-of；
- `historical_reference`：数据可能较新，但版本不属于当前Catalog或不满足当前门禁。

## A当前视图

A必须同时满足：

1. 同Benchmark item与精确Model config中是最新记录；
2. evidence date在120天内；
3. Model config能通过唯一最长前缀链接到`status=current`的Model Catalog身份。

例如：

```text
openai-gpt-5-6-terra-max
→ openai-gpt-5-6-terra
→ current

anthropic-claude-opus-4-5-... 
→ 当前Catalog没有Opus 4.5
→ historical_reference
```

## B当前视图

运行时B不走A那套时效滚动。稳定家族先验来自`B_harnesses/stable/`，低频维护、不匹配版本。公开快照里的provisional块只进history，不能特化B。若current里出现同家族`family_observation`，才用于收缩；没有则直接用家族先验或\(B_0\)。Exact matched block不再是B的发布条件。

## 存储

```text
A_models/current/
A_models/history/
B_harnesses/current/
B_harnesses/history/
freshness_summary.json
```

根目录的完整历史文件暂时保留用于兼容和审计，但`BuiltinDatasetRegistry`默认只读`current/`。只有Python调用显式设置`include_history=True`才加载历史视图。

## 当前结果

```text
A current model configs       22
A current observations       506
A current calibrated cells    44
A historical model configs    22
A historical observations    506
B runtime family priors        15 families / 90 cells
B current family observations   0
B historical provisional     7,725
B exact blocks (audit only)     0
```
