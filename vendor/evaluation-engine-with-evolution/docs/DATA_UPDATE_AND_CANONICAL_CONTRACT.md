# J主导的七轴/量尺合同与数据更新流程

## 1. J生成器是源矩阵真源

```text
Axis contract     livebench-capability-seven/v0.1
Scale contract    canonical_logit/v0.1
C generator       experience-evaluation-c-generator/v0.4
```

七轴固定为 LiveBench 的七类：`reasoning/coding/agentic_coding/mathematics/data_analysis/language/instruction_following`。J生成器负责源矩阵的轴与source/canonical量尺投影；A/B通过有版本的Adapter进入同一合同，C不能各自定义另一套最终轴或重复转换。实现位于[canonical_measurement.py](../src/experience_evaluation/canonical_measurement.py)和[evaluation_matrix.py](../src/experience_evaluation/evaluation_matrix.py)，机器合同位于[canonical_measurement_contract.json](../config/canonical_measurement_contract.json)。

## 2. 量尺

J生成器是唯一的源矩阵量尺入口。J能力单元同时保存原始`normalized_0_1`值和由J生成的`canonical_logit/v0.1`投影；A只提供外部参考值，B直接提供canonical additive effect，C只消费这些canonical字段，不在入口重复定义量尺。

J和A是0–1水平量，进入J/C合同前转换为log-odds：

\[
z=logit(p)=\log\frac{p}{1-p}
\]

标准误使用delta method：

\[
SE_z=\frac{SE_p}{p(1-p)}
\]

0和1使用`epsilon=10^{-6}`显式截断，并记录`boundary_clipped=true`。B不是成功率水平，而是相对Harness效应，必须直接表达为`canonical_logit/v0.1`加性效应。

\[
C^{logit}_{g,k}=J^{logit}_{g,k}-A^{logit}_{m,k}-B^{logit}_{h,k}
\]

数值表示统一不等于测量仪器已经校准。A通过`livebench-current-axis-mean/v0.2`把 current LiveBench 七类写成`calibrated_for_c`。B继续使用家族弱先验，不要求Exact对照。

## 3. A轴适配

公开 A 按 LiveBench 类目一对一映射：

```text
Reasoning        → reasoning
Coding           → coding
Agentic Coding   → agentic_coding
Mathematics      → mathematics
Data Analysis    → data_analysis
Language         → language
IF               → instruction_following
```

current 每轴至少 2 个子任务时，按子任务均值、样本标准误和 logit/delta-method 写成`calibrated_for_c`。这是仪器校准，不是 IRT，也不是与 J 任务集的 common-item linking。

当次使用仍可用`local-probe-same-taskspec/v0.1`：对同一批冻结 TaskSpec 跑`baseline_model`结果，把该 J 写成 A，并覆盖同模型的公开 A。这只对这一批 TaskSpec 构成 common-item，不写进公开 A 快照。

## 4. J轴适配

J生成器只接受`livebench-capability-seven/v0.1`的轴子集，并在生成阶段完成量尺投影。J同时保存：

- `j_matrix.jsonl`：J生成器输出的能力单元，包含source与canonical字段；
- `canonical_j_matrix.jsonl`：ABJC Bundle中对上述canonical字段的合同校验投影。

两者不应再出现“一个没有canonical字段、另一个临时转换”的分工。

## 5. B轴与量尺

运行时B是弱修正先验，不是精确对照矩阵。单元必须包含：

```text
agent_id / harness_id     # 稳定家族，不匹配版本
dimension_id ∈ canonical axes
value                     # additive logit effect
stderr
scale_id = canonical_logit/v0.1
scale_role = additive_effect
calibration_status ∈ {
  default_prior_for_c,
  document_specialized_prior_for_c,
  specialized_for_c
}
```

已知家族用官方介绍页生成的稳定先验；未知Agent用\(B_0=+0.15\) logit。只有同家族`family_observation`才会收缩特化。A仍要求current、精确Model config和校准；B不跟A同一套时效与覆盖要求。C不会因B缺失而阻断。当前示例的C仍因A未校准、J为shadow和CalibrationContract缺失而不可用。

## 6. 当前数据更新链路

### 第一步：更新公开数据和标准快照

```bash
python3 scripts/build_public_dataset.py --as-of YYYY-MM-DD
```

[build_public_dataset.py](../scripts/build_public_dataset.py)会查询Agent Psychometrics和Terminal-Bench 2.1当前HEAD，用返回commit下载固定版本数据；执行上游身份拆分器；下载TB2.1提交摘要；读取`config/catalog_seed.json`并补仓库元数据；写入`data/raw/<date>/`和`data/snapshots/<date>/`；记录commit、许可、SHA-256、Coverage和Manifest；最后更新`data/derived/latest.json`。

它拒绝覆盖同日期快照，日常更新应创建新日期版本。

### 第二步：生成A/B内置矩阵包

```bash
python3 scripts/build_ab_datasets.py --as-of YYYY-MM-DD
```

[build_ab_datasets.py](../scripts/build_ab_datasets.py)读取同日期Snapshot和Raw目录，生成`data/matrices/<date>/A_models/`、`B_harnesses/`、Coverage和Manifest，并按`livebench-current-axis-mean/v0.2`写入A current校准基线。默认拒绝覆盖；`--replace-output`只用于明确重建，不用于日常更新。只补A校准时可运行：

```bash
PYTHONPATH=src python3 scripts/calibrate_a_baselines.py --as-of YYYY-MM-DD
```

### 第三步：运行时自动识别

`BuiltinDatasetRegistry`扫描`data/matrices/*/manifest.json`，选择最新版本并重新验证全部哈希。默认只加载`A_models/current/`和`B_harnesses/current/`；历史研究必须显式`include_history=True`。详见[Freshness Policy](FRESHNESS_POLICY.md)。运行时不联网，也不修改内置数据。

### 第四步：J与C

J随每批TaskSpec/ExecutionResult现场生成，并直接输出七轴合同和source/canonical双层字段；A/B只读查询内置版本并适配到同一canonical合同；C只校验并消费J/A/B的canonical字段。运行Bundle写入`data/generated/`，不会反向修改A/B。

## 7. 当前更新机制的缺口

目前不是完整的一键更新：

1. `build_public_dataset.py`自动更新Agent Psychometrics和TB2.1，但没有自动下载LiveBench；
2. `build_ab_datasets.py`要求同日期Raw目录已经存在LiveBench table/categories；
3. BFCL、LiveCodeBench、HELM等已登记来源没有统一更新器；
4. `catalog_seed.json`的模型/Harness名单需要人工维护；
5. 没有定时任务，更新由命令手动触发；
6. 没有“所有构建成功后再原子切换latest”的事务编排器。

因此当前状态是：运行时自动发现和识别已经完成；数据采集与发布仍是半自动、分两步构建。

## 8. 推荐更新器

下一步应增加`update_builtin_datasets.py`：

```text
fetch registered sources
→ stage raw snapshot
→ validate licenses/hashes
→ build normalized snapshot
→ build A/B matrices
→ run regression tests
→ atomically publish version
→ update latest pointer
```

任一步失败都不切换运行时latest。
