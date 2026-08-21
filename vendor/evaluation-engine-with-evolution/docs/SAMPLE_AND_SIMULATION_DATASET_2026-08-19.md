# 历史Task响应样本与模拟实验（v0.2，2026-08-20）

## 1. 目标与数据单位

本批建立“历史Task response → 有限描述实验 → 派生模拟 → 已知真值参数恢复”的闭环。上游Agent Psychometrics数据是逐Task二值response，不是一次独立Trial；因此不再使用`Run`命名，也不能据此分析单次执行波动、成本、耗时或轨迹。

真实响应和模拟数据物理分离。模拟记录不能增加真实证据覆盖、不能进入Agent排名，也不能作为矩阵B的Exact anchor。

## 2. 第一批典型响应组

按“同Benchmark标签、同Model标签、完全相同Task集合”选择全部六个可用Harness：

```text
Benchmark标签       Terminal-Bench 2.0
Model标签           anthropic-claude-sonnet-4-5
共同Task            89
典型响应组          6
观测Task response   534
```

- Claude Code；
- CAMEL-AI；
- mini-SWE-agent；
- OpenHands；
- Goose；
- Terminus 2。

选择规则是“该Benchmark/Model标签下所有唯一task-level Observation”，不再无说明排除CAMEL-AI。

## 3. 版本证据与工况门禁

解析器现在保留上游`detail_url`，并将URL路径中的版本信息保存为带证据状态的字段：

| Harness | Harness版本证据 | Model revision标签 | Provider标签 |
|---|---|---|---|
| Claude Code | `2.0.31` | `claude-sonnet-4-5-20250929` | `anthropic` |
| CAMEL-AI | `1.0` | `claude-sonnet-4-5` | `anthropic` |
| mini-SWE-agent | `unknown` | `claude-sonnet-4-5-20250929` | `anthropic` |
| OpenHands | `0.60.0` | `claude-sonnet-4-5-20250929` | `anthropic` |
| Goose | `stable`，不是精确版本 | `claude-sonnet-4-5` | `anthropic` |
| Terminus 2 | `2.0.0` | `claude-sonnet-4-5-20250929` | `anthropic` |

URL解析结果属于`parsed_from_source_url`，不是独立验证。`unknown/stable/latest`不会被提升成精确Harness revision。

样本门禁现在要求组间同时满足：Benchmark version、Model revision、Provider route、effort、Environment、资源、超时、Context、attempt policy、Task revision/Fixture和Verifier均已知且相同，同时每个Harness revision已知。

当前失败原因包括Model revision不一致以及Environment、Task/Verifier、资源和策略未知，所以：

```text
fit_status = exploratory_only
environment_consistency_status = unverified
```

## 4. 内容寻址与物理存储

当前实验目录：

`data/experiments/2026-08-19/tb20-claude-sonnet-4-5-six-harness-v2/`

```text
observed_responses/
  sample_groups.jsonl
  observed_task_responses.jsonl
  observed_outcome_matrix.jsonl
  sample_statistics.json
  limited_experiment_report.json
  manifest.json

simulated/
  bootstrap_responses.jsonl
  bootstrap_outcome_matrices.jsonl
  parametric_responses.jsonl
  parametric_outcome_matrices.jsonl
  simulation_parameters.json
  known_parameter_responses.jsonl
  known_parameter_outcome_matrices.jsonl
  known_parameters.json
  parameter_recovery_report.json
  simulation_experiment_report.json
  manifest.json
```

`sample_dataset_id`绑定来源快照内容哈希、完整Observation、Outcome内容和版本字段。上游结果发生变化时ID随之变化。根Manifest只保存相对目录，并记录两个子Manifest哈希；`experiment_manifest.sha256`保护根Manifest本身。

## 5. 有限描述实验

| Harness | 成功响应 | Task数 | 二值响应率 |
|---|---:|---:|---:|
| Claude Code | 38 | 89 | 0.4270 |
| CAMEL-AI | 42 | 89 | 0.4719 |
| mini-SWE-agent | 40 | 89 | 0.4494 |
| OpenHands | 38 | 89 | 0.4270 |
| Goose | 40 | 89 | 0.4494 |
| Terminus 2 | 39 | 89 | 0.4382 |

这些是二值response矩阵均值，不是官方排行榜Accuracy，也不是89次独立Trial成功率。Pairwise只是同Task描述性投影，共享Task结果的行不是独立观测。

## 6. 样本驱动模拟

Task block bootstrap每次抽中一个Task时，同时复制六个组的response，保留跨组共同任务结构：

\[
T_d^*\sim Uniform\{T_1,\ldots,T_{89}\}
\]

经验Logit模拟保留为启发式压力测试：

\[
logit(p_{gt})=logit(q_g)+logit(q_t)-logit(q_0)
\]

本批各生成100个重复数据集，每类53,400条response。经验Logit中的三个Beta量来自重叠样本，不构成完整联合后验，禁止把它称为正式参数恢复或SBC。

## 7. 已知真值参数恢复层

新增独立生成模型：

\[
Y_{gtr}\sim Bernoulli\left(\sigma(\mu+\alpha_g-b_t)\right)
\]

并施加：

\[
\sum_g\alpha_g=0,\qquad \sum_tb_t=0
\]

所有真实生成参数、seed和replicate数保存在`known_parameters.json`。本批生成53,400条known-parameter response，并使用Beta平滑cell-logit两向加性估计器做首次恢复：

```text
intercept absolute error   0.0086
group effect RMSE          0.0234
task difficulty RMSE       0.1002
```

该结论只证明当前估计器能恢复这套合成真值，不证明现实Agent效应已被恢复。完整SBC还需要对多批先验生成参数重复拟合并检查rank/coverage；SBC方法参考[Talts等](https://arxiv.org/abs/1804.06788)。

## 8. 与真实执行矩阵的关系

本批仍只建立历史客观响应矩阵，不是过程评价矩阵\(J\)。真正的`observed_trials/`必须从Harbor或统一补测接入Trial config、Environment、Verifier、成本、耗时和轨迹。Harbor的Job/Trial目录与ATIF可作为合同：[Harbor Evals](https://github.com/harbor-framework/harbor/blob/main/docs/content/docs/run-jobs/run-evals.mdx)、[ATIF规范](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md)。

## 9. 作为矩阵生成器 synthetic validation 输入

已知真值响应现在可以通过独立CLI接入矩阵生成器的标准`TaskSpec + ExecutionResult`合同：

```bash
python3 scripts/run_synthetic_validation.py \
  --responses path/to/known_parameter_responses.jsonl \
  --known-parameters path/to/known_parameters.json \
  --replicate -1 --bootstrap-replicates 500 \
  --output data/generated/synthetic-abjc-validation
```

这条路径使用`synthetic-axis-probe/v0.1`生成平衡的一热七轴TaskSpec。这个映射是为了验证Q/载荷、J canonical projection和C代数的fixture，不是对原始Terminal-Bench任务的语义能力标注；每个TaskSpec保存`semantic_capability_claim=false`。

适配器只把`outcome`放入`ExecutionResult.metrics.success`。`success_probability`、`true_linear_predictor`、`known_parameters.json`中的效应和难度只作为审计真值，不参与评分。结果强制为`evidence_origin=known_parameter_simulation`和`publication_scope=synthetic_only`，不增加真实证据覆盖，不开放真实CStateStore更新。

J仍按现有矩阵生成器规则计算：先在`normalized_0_1`上按Task/attempt聚合，再由J生成`canonical_logit/v0.1`。因此`logit(mean(p))`不等于生成模型的逐试验加性logit；验证报告必须分别检查采样误差和C的`J-A-B`机器精度代数，不应期待C自动等于零。

## 10. 重新生成

```bash
python3 scripts/build_sample_simulation.py --as-of 2026-08-19
```

构建器默认拒绝覆盖已有实验。改变数据、组选择、参数或seed时必须使用新的`experiment-id`。
