# 本地使用、真实执行接入与可发布画像

## 1. 这条路径做什么

```text
外部 Runner 导出 RunBundle
        ↓
校验 / 幂等入库 / 转 ExecutionResult
        ↓
同 TaskSpec 的 baseline_model 探针 → 本地 A
        ↓
Agent 结果 → J → C = J − A − B
        ↓
CStateStore 更新
        ↓
只读分轴画像（无总分、无排名）
```

本项目仍然不调度 Agent，不实现 LLM Judge，也不提供画像 UI。

## 2. 七轴 A 的来源

公开 LiveBench 合同 `livebench-current-axis-mean/v0.2` 一对一映射全部七类：

```text
Reasoning        → reasoning
Coding           → coding
Agentic Coding   → agentic_coding
Mathematics      → mathematics
Data Analysis    → data_analysis
Language         → language
IF               → instruction_following
```

当次使用仍可用`local-probe-same-taskspec/v0.1`覆盖同模型公开 A：用**同一批冻结 TaskSpec**跑 `role=baseline_model` 的基线模型结果，再把该 J 写成 A。这是同任务 common-item，不是 IRT，也不是和 LiveBench 的 linking。每轴至少 2 个已观测任务，且必须带 J 的 bootstrap 标准误。

## 3. RunBundle

最小合同：`experience-evaluation-run-bundle/v0.1`。

必填：`run_id`、`task_spec_id`、`task_revision`、`subject_id`、时间、`status`、`outcome.metrics`、`content_hash`。

- 哈希覆盖除`content_hash`外的规范化内容；
- 同一`run_id + hash`重复提交是幂等；
- 同一`run_id`不同哈希是冲突，拒绝入库；
- 适配器只把客观 verifier 指标转成`ExecutionResult`，不解释过程事件。

CLI：`scripts/ingest_run_bundles.py`。

## 4. 可发布画像

画像是 C 状态的只读投影，不是排行榜。

- 七轴始终列出；缺失保持`unavailable`，不填 0；
- 禁止总分、总排名和任何聚合分；
- 仅当已观测轴的`publication_scope`属于`local_controlled`或`real_evidence`时，状态为`publishable_local_portrait`；
- `shadow` / `synthetic_only` 不能发布。

CLI：`scripts/render_profile.py`。

## 5. 一次跑通

```bash
PYTHONPATH=src python3 scripts/run_local_usage.py \
  --output data/generated/local-usage-demo \
  --store data/generated/local-usage-c-state \
  --profile data/generated/local-usage-demo/portraits.json \
  --observed-at 2026-08-20T12:00:00+00:00
```

示例数据在`examples/local_usage/`。其中的分数是路径夹具，不是 GPT-5.6 的公开成绩。
