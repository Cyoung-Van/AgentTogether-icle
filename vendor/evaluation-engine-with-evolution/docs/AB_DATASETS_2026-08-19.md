# 矩阵 A / B 独立数据集审计（2026-08-19）

## 数学口径

概念分解保留为：

\[
H=A+B+C
\]

- \(A\)：基础模型能力；
- \(B\)：Agent/Harness效应；
- \(C\)：部署、环境、Persona、Memory、Skill等残差状态；
- \(H\)：待测的实际能力状态。

评估矩阵 \(J\) 是对 \(H\) 的观测，不是无误差恒等式：

\[
J=\mathcal M(H,Task,Judge)+\epsilon
\]

工程上严禁直接用排行榜分数计算 `J=A+B+C`。

## 物理存储

运行时已增加`current/`与`history/`双视图。根目录完整文件保留审计兼容，注册器默认只加载current。

### Matrix A

目录：[A_models](../data/matrices/2026-08-19/A_models/)

- `model_catalog.jsonl`：33个官方/常见模型；
- `source_model_configs.jsonl`：LiveBench准确保存的44个模型配置/effort；
- `model_benchmark_observations.jsonl`：1,012条模型×子任务分数；
- 7类能力：Reasoning、Coding、Agentic Coding、Mathematics、Data Analysis、Language、Instruction Following。

主要实测源为 [LiveBench](https://github.com/LiveBench/new-livebench)，固定commit `fb3db47fd22d40740d2e6949623bd4bcca9182dd`。包含GPT-5.6 Sol/Terra/Luna、Claude Fable 5/Opus 4.8/Sonnet 5、DeepSeek V4 Flash/Pro、Kimi K3/K2.6、Gemini 3.5/3.6、Grok、GLM、Qwen、MiniMax、Muse等配置。

BFCL V4任务库已下载，后续补Tool Use模型结果。LiveCodeBench、HELM、LMArena、Artificial Analysis已登记，结果摄取待完成。

### Matrix B

目录：[B_harnesses](../data/matrices/2026-08-19/B_harnesses/)

- `harness_catalog.jsonl`：23个当前常见Harness；
- `crossed_agent_model_observations.jsonl`：246条身份已解析的Agent×Model观测；
- `crossed_task_outcomes.jsonl`：63,758条逐任务结果；
- `design_edges.jsonl`：236条Model–Harness–Benchmark–effort设计边。

数据来自Agent Psychometrics四个任务矩阵和Terminal-Bench 2.1验证提交。历史数据涉及76个Harness和91个模型，但很多是旧对象；不能据此声称23个当前Harness全部有当前数据。

当前目录中仍无公开表现数据的主要Harness包括Aider、Cline、Continue、Hermes Agent、Kimi Code/Agent、Pi、Qwen Code、Roo Code、Grok Build、MiniMax Agent。这些对象必须继续寻找专门评测或通过标准Harness补测，不能生成B值。

## 完整性结论

- A与B现已物理分开，不共享同一表现文件。
- A已具备较新的多类别模型基线，但仍是子任务汇总，不是全部逐题响应。
- B拥有较强的历史crossed design，但当前常见Harness覆盖仍不完整。
- 当前数据足以开始一维/分领域基线和二部图连通性研究；不足以发布“所有常见模型与Agent的最终能力矩阵”。
- 缺失数据必须显示为`catalog_only`或`unavailable`。
- Freshness Policy筛选后，A current为22个模型配置/506条观测；另外22个配置/506条观测进入history。
- B的7,725个provisional块全部进入history，不能特化运行时B；运行时B使用稳定家族先验或\(B_0\)。

## B 数据的比较纪律

后续不会把“同维度的不同Task”拼在一起直接反推B。B的主要数据单元定义为：

\[
Block=(BenchmarkVersion,TaskRevision,ModelRevision,Effort,Environment,Verifier)
\]

只有同一Block内不同Harness的结果才是直接B证据。优先级为：

1. 同Task、同Model快照、同effort、同环境、不同Harness；
2. 同Task且Model–Harness图可由一级锚点连接；
3. 只有同Benchmark汇总的数据仅作弱先验；
4. 不同Task即使属于相同维度，也禁止作为直接Harness对照。

每条B结果必须报告`matched_task_count`、`exact_model_match_count`、`linked_only_count`和不可比原因。

## Matched Block 数据产物

2026-08-19严格门禁重建后：

- `exact_matched_blocks.jsonl`：0个；当前公开快照没有记录同时满足全部Exact字段；
- `provisional_same_label_blocks.jsonl`：7,725个，仅代表Benchmark/Task/Model标签/effort标签相同，不是直接B证据；
- `pairwise_harness_comparisons.jsonl`：0条；不再从provisional块生成正式Pairwise；
- `pairwise_harness_summaries.jsonl`：0条；
- `rejected_comparisons.jsonl`：88,718条，每条包含拒绝阶段、主原因和全部原因；
- `rejection_summary.json`：按阶段、主原因和全部原因统计；
- `exact_anchor_report.json`：Exact anchor数量与零锚点后的行动要求。

原先7,814个候选块中，89个使用`multiple`模型标签，已从直接比较中排除；其余7,725个降级保存。63,402条已解析任务结果缺少Benchmark/Task/Model/Harness/环境/策略等Exact字段，24,960条身份未解析，356条使用不可比较模型身份。

当前任何Harness都没有通过严格Exact门禁。Claude Code、Droid、Gemini CLI、Goose、mini-SWE-agent、OpenCode、OpenHands、SWE-agent、Terminus 2和Warp仅拥有provisional共同任务候选。Codex的Terminal-Bench 2.1数据仍是submission summary；逐Trial ID虽存在，但还需导入Trial配置、Task revision、Verifier和Environment证据。

Exact对照的历史审计见[B矩阵Exact锚点历史审计](B_EXACT_ANCHOR_PLAN.md)。运行时B路径见[稳定Agent家族特化数据集](B_STABLE_AGENT_SPECIALIZATIONS.md)。
