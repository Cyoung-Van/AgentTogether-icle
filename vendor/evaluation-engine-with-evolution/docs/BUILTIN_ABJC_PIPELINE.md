# A/B内置注册器、身份解析与A/B/J/C流水线

## 1. 目标

统一入口完成：

```text
自动发现并验证内置A/B快照
→ 保守解析Subject的Model/Harness身份
→ 查询A/B能力基线
→ 从本地任务结果生成J
→ 在同轴、同量尺、已校准时反推C
```

实现位于[matrix_pipeline.py](../src/experience_evaluation/matrix_pipeline.py)，CLI位于[run_abjc_pipeline.py](../scripts/run_abjc_pipeline.py)。

J生成器拥有源矩阵的最终轴与source/canonical量尺投影；A/B通过Adapter进入同一合同，C只校验并消费这些canonical字段。详见[七轴/量尺合同与数据更新流程](DATA_UPDATE_AND_CANONICAL_CONTRACT.md)。

## 2. 内置数据注册器

默认扫描：

```text
data/matrices/<as-of>/
├── manifest.json
├── coverage.json
├── A_models/
└── B_harnesses/
```

注册器只识别包含Manifest的版本目录，自动选择最新`as-of`，并在加载任何数据前验证全部SHA-256。Manifest缺文件、路径越界或哈希不一致会整体拒绝快照。

内置表示“随项目分发的版本化数据包”，不是把分数写死在Python代码里。

## 3. 身份自动识别

SubjectIdentity可以提供：

```text
subject_id
model / model_id / model_revision / model_config_id
provider
harness / harness_id / harness_revision
```

Model匹配等级：

- `exact_dataset_config`：精确匹配A数据的source model config；
- `catalog_identity_only`：只确认模型目录身份，没有精确配置数据；
- `unresolved`：不能可靠识别。

Harness匹配等级：

- `stable_family_id`：匹配稳定Agent家族ID；
- `stable_family_alias`：匹配官方别名；
- `catalog_identity_only`：旧Catalog对象但尚无稳定家族特化；
- `generic_default`：未知Agent，使用通用B。

Release版本只保留审计，不参与稳定B匹配。

解析器只做规范化后的精确匹配，不做编辑距离、包含关系或LLM猜测。

## 4. A查询

优先读取 current 视图中的：

```text
A_models/current/capability_baselines.jsonl
```

只有`calibration_status=calibrated_for_c`的基线可进入C。公开合同是`livebench-current-axis-mean/v0.2`：校准 LiveBench 全部七类。当次使用若启用`local_probe_a`，同模型、同TaskSpec的本地探针A会覆盖公开A。未校准轴保持缺失。若没有校准基线，运行时仍可生成`uncalibrated_reference`供审计，但不能进 C。

## 5. B查询

运行时B不是精确Harness矩阵。查询顺序：

```text
稳定家族先验          B_harnesses/stable/
家族级可用观测        B_harnesses/current/capability_baselines.jsonl
通用默认B₀            literature-shrunk-harness-prior/v0.1
```

匹配只认`stable_family_id` / `stable_family_alias` / `generic_default`，release版本只记`observed_version`。有同家族`family_observation`时收缩特化；没有则直接用家族先验或\(B_0\)。Provisional和Exact全字段对齐都不作为B主线。详见[稳定Agent家族特化数据集](B_STABLE_AGENT_SPECIALIZATIONS.md)。

## 6. J生成

J由无LLM生成器从TaskSpec和ExecutionResult现场产生。生成器只接受七轴合同的轴子集，并在生成阶段同时输出：

- `value/source_value`：`normalized_0_1`源值；
- `canonical_value/canonical_stderr`：`canonical_logit/v0.1`投影；
- `axis_contract_id`、`source_scale_id`和`canonical_scale_id`。

注册器不会加载历史J数据，也不会让A/B影响本地任务评分。

## 7. C推断

概念公式：

\[
C_{g,k}=J_{g,k}-A_{m,k}-B_{h,k}
\]

实际计算同时要求：

1. J单元可发布；
2. A在该Canonical Axis有可用于C的精确校准基线；B在该轴有允许进入C的家族先验、默认先验或家族级特化；
3. A标记`calibrated_for_c`，B标记`calibrated_for_c`、`default_prior_for_c`、`document_specialized_prior_for_c`或`specialized_for_c`；
4. CalibrationContract已验证；
5. A/B/J均已适配为`canonical_logit/v0.1`，且J的canonical字段必须通过一致性校验；
6. 不确定度合同明确。

独立误差假设下：

\[
SE_C=\sqrt{SE_J^2+SE_A^2+SE_B^2}
\]

任一门禁失败，C输出`unavailable`及全部原因，不将缺失A/B视为0，也不截断负残差。

## 8. CLI

```bash
python3 scripts/run_abjc_pipeline.py \
  --tasks examples/matrix_generator/task_specs.jsonl \
  --results examples/matrix_generator/execution_results.jsonl \
  --identities examples/matrix_pipeline/subject_identities.jsonl \
  --config examples/matrix_pipeline/pipeline_config.json \
  --output data/generated/my-abjc-bundle
```

输出：

```text
snapshot.json
identity_resolution.jsonl
a_baselines.jsonl
b_baselines.jsonl
objective_matrix.jsonl
j_matrix.jsonl
canonical_j_matrix.jsonl
c_matrix.jsonl
rejected_results.jsonl
summary.json
manifest.json
```

## 9. Synthetic验证路径

真实内置A/B注册器不加载模拟数据。若需要验证J/C合同算术，使用独立的[synthetic合同验证](SAMPLE_AND_SIMULATION_DATASET_2026-08-19.md)路径。它的Bundle带有`publication_scope=synthetic_only`，不能进入真实A/B快照、Agent排名或CStateStore。

## 10. 当前真实运行结果

可复现入口：`scripts/run_atom_runtime.py`（原子 J→C→画像）与 `scripts/run_local_usage.py`。

```text
内置快照              2026-08-19，Manifest通过
J维度                 7
Model匹配             exact_dataset_config
Harness匹配           stable_family_id
A                     七轴 calibrated_for_c
B                     stable family prior（按Agent家族，SD 0.75）
默认示例 C            shadow 且无 CalibrationContract 时不发布
本地使用 C            公开七轴 A 可写；本地探针可覆盖同模型公开 A
```

默认示例仍诚实拒绝发布。本地使用需显式`comparability_verified`和CalibrationContract；C只在已校准的A轴上生成，缺轴不当 0。

```bash
PYTHONPATH=src python3 scripts/run_usage_update.py \
  --tasks examples/matrix_generator/task_specs.jsonl \
  --results examples/matrix_generator/execution_results.jsonl \
  --identities examples/matrix_pipeline/subject_identities.jsonl \
  --config examples/matrix_pipeline/local_usage_config.json \
  --output data/generated/my-usage-bundle \
  --store data/c_state/my-agent \
  --observed-at 2026-08-20T21:00:00+08:00
```

单元测试另构造了同轴、同量尺、Exact A/B基线，验证C数值和误差传播能够正确计算。
