# 无LLM评估矩阵生成器

## 1. 定位

生成器读取版本化TaskSpec和执行结果，现场生成：

```text
TaskSpec + ExecutionResult + GeneratorConfig
                      ↓
Objective Matrix O + Capability Matrix J_result
                      + Coverage + Bootstrap uncertainty + Audit trail
```

它不调用LLM，不加载历史A/B/J数据集，也不生成单一总分。真实运行不依赖历史样本与模拟数据；另有独立的synthetic validation CLI消费已知真值模拟数据，仅用于验证合同接线，不进入真实证据覆盖。

当前实现：[evaluation_matrix.py](../src/experience_evaluation/evaluation_matrix.py)；CLI：[generate_evaluation_matrix.py](../scripts/generate_evaluation_matrix.py)。

## 2. 主流项目对照

| 设计 | 参考做法 | 本项目实现 |
|---|---|---|
| Sample score与aggregate分开 | [Inspect Scoring](https://inspect.aisi.org.uk/scoring.html)将原始输出转为sample Score，再聚合成metric | 先生成`objective_matrix`，再投影到能力矩阵 |
| 缺失不等于零 | [Inspect Score](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/scorer/_metric.py)提供unscored值，聚合时跳过 | Infrastructure failure保持`null`；subject failure是否记0由显式策略决定 |
| 任务/指标配置化 | [lm-evaluation-harness Task Guide](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/new_task_guide.md)使用YAML定义metric与micro/macro聚合 | TaskSpec定义metric范围、方向、权重和Q载荷；attempt reducer显式配置 |
| Bootstrap不确定度 | [lm-evaluation-harness evaluator](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/lm_eval/evaluator.py)通过bootstrap iterations计算stderr | 所有Subject共享同一组Task block抽样，输出stderr和95%区间 |
| 多指标与覆盖透明 | [HELM](https://crfm.stanford.edu/helm/v1.0/)强调多指标、标准化、覆盖和明确缺失 | 六维分别输出value、coverage、任务数和publish status，不压成总分 |
| Task/Verifier结果分层 | [Harbor Evals](https://www.harborframework.com/docs/run-jobs/run-evals)分别保存Task、Trial result和Verifier evidence | ExecutionResult只接收确定性Verifier指标；原始证据由result_id/provenance追溯 |

本项目没有复制这些项目代码，只复用公开的工程模式。

## 3. 输入合同

### TaskSpec

```json
{
  "task_id": "implement-feature",
  "task_revision": "1.0.0",
  "task_family": "coding",
  "weight": 1.0,
  "capability_loadings": {
    "coding": 0.7,
    "agentic_coding": 0.3
  },
  "metrics": [
    {
      "metric_id": "tests_passed",
      "min": 0,
      "max": 10,
      "direction": "maximize",
      "weight": 3,
      "required": true
    }
  ]
}
```

`capability_loadings`就是任务Q矩阵的一行。必须在查看执行结果前冻结。

### ExecutionResult

```json
{
  "result_id": "run-001",
  "subject_id": "agent-alpha",
  "task_id": "implement-feature",
  "task_revision": "1.0.0",
  "status": "completed",
  "metrics": {
    "tests_passed": 9
  },
  "provenance": {}
}
```

状态包括：

- `completed`：根据指标计算；
- `subject_failure`：按显式`subject_failure_score`计分，默认0；
- `infrastructure_failure`：保留记录但不计0；
- `invalid`：进入拒绝日志。

JSON Schema位于[schemas](../schemas/)目录，运行时另有语义校验。

GeneratorConfig还包含发布门禁。合成验证可以额外声明`evidence_origin=known_parameter_simulation`和`publication_scope=synthetic_only`；此时能力单元的状态是`synthetic_only`，不是`publishable`。

```json
{
  "comparability_verified": true,
  "comparison_contract_id": "controlled-env-v1"
}
```

只有外部执行系统已经验证Model、Harness、Environment、Verifier和策略合同一致时才能设为`true`；生成器不会仅凭覆盖率猜测可比性。`synthetic_only`不等于真实可比性，必须由独立的synthetic calibration contract显式隔离。

## 4. 计算方法

### 指标标准化

最大化指标：

\[
s_m=\frac{x_m-min_m}{max_m-min_m}
\]

最小化指标：

\[
s_m=1-\frac{x_m-min_m}{max_m-min_m}
\]

超出声明范围的值直接拒绝，不静默截断。一个Task存在多个指标时按TaskSpec中的metric weight做加权平均。

### 重复attempt

同一Subject、Task、revision存在多个有效结果时，使用配置指定的`mean`或`max` reducer。输出保留全部`source_result_ids`和attempt score。

### 客观结果矩阵

\[
O_{t,g}\in[0,1]\cup\{null\}
\]

`null`表示没有可用观测，不等于失败。

### 能力矩阵

对Subject \(g\) 和能力维 \(k\)：

\[
J^{result}_{g,k}
=
\frac{\sum_t M_{t,g}w_tQ_{t,k}O_{t,g}}
{\sum_t M_{t,g}w_tQ_{t,k}}
\]

覆盖率独立计算：

\[
Coverage_{g,k}
=
\frac{\sum_t M_{t,g}w_tQ_{t,k}}
{\sum_t w_tQ_{t,k}}
\]

低于`min_coverage`的单元仍保留value，但标记`insufficient_coverage`。Coverage充分但未提供可比性合同时标记`shadow_only_unverified_comparability`；两道门禁都通过才是`publishable`。

## 5. 不确定度

生成器按Task ID成块Bootstrap，同一个replicate中的所有Subject共享相同Task抽样，避免破坏配对关系。输出：

```text
stderr
ci_low / ci_high
bootstrap_replicates_requested / used
uncertainty_status
```

不足两个观测Task时不输出伪造的`stderr=0`，而是标记`insufficient_task_diversity`。Bootstrap衡量的是任务抽样不确定度，不包含模型API随机性；后者需要真实重复Trial。

## 6. 输出Bundle

```text
objective_matrix.jsonl     Subject × Task结果
evaluation_matrix.jsonl    Subject × Capability结果
rejected_results.jsonl     不可用结果及原因
summary.json               版本、数量、内容哈希和配置
generator_config.json      实际生成配置
manifest.json              输入与输出SHA-256
```

每个能力单元包含：

- `value/source_value`：J生成器的`normalized_0_1`源值；
- `canonical_value/canonical_stderr`：J生成器按统一合同生成的`canonical_logit/v0.1`投影；
- `axis_contract_id=livebench-capability-seven/v0.1`；
- coverage、任务数、权重、不确定度、发布状态和来源Objective row ID。

`j_matrix.jsonl`已经包含source/canonical双层字段；ABJC不再由入口私自替J定义转换。没有`total_score`字段。

## 7. 使用方法

```bash
python3 scripts/generate_evaluation_matrix.py \
  --tasks examples/matrix_generator/task_specs.jsonl \
  --results examples/matrix_generator/execution_results.jsonl \
  --config examples/matrix_generator/config.json \
  --output data/generated/my-evaluation
```

## 8. 当前可靠性边界

目前可用于：

- 离线、确定性生成\(O\)和\(J^{result}\)；
- 多指标归一、任务权重和Q矩阵投影；
- 缺失/失败/基础设施错误分离；
- Coverage门禁、Task Bootstrap和审计追溯；
- Coverage与外部工况可比性双门禁；
- ICLE或其他执行系统的结果适配目标。

目前不等于：

- 经IRT标定的latent capability；
- Harness效应矩阵B；
- 部署残差矩阵C；
- 基于轨迹和Rubric的\(J^{process}\)；
- 跨不同TaskSpec版本可直接比较的长期画像。

v0.4采用透明加权聚合，并由J生成器统一输出七轴和source/canonical量尺字段，适合离线基础功能和shadow evaluation。后续IRT/MIRT只能作为可选Reducer，不应替换当前可审计基础层。

Synthetic合同验证：

```bash
python3 scripts/run_synthetic_validation.py \
  --responses path/to/known_parameter_responses.jsonl \
  --known-parameters path/to/known_parameters.json \
  --replicate -1 --bootstrap-replicates 500 \
  --output data/generated/synthetic-abjc-validation
```

该路径将每个已知真值response作为标准`ExecutionResult`，但只读取`metrics.success`评分；真值概率和线性预测量只保存在provenance。它还显式报告`logit(mean(p))`与逐试验加性logit不可交换，因此C代数通过不等于潜变量已恢复。
