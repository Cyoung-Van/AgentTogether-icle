# Public dataset

当前快照：`data/snapshots/2026-08-19/`

运行时A/B默认读取`data/matrices/<date>/*/current/`；历史数据保留在`history/`及兼容根文件中，不参与默认查询。A current 七轴由`livebench-current-axis-mean/v0.2`校准，状态为`calibrated_for_c`。

可复现的 J/C 模拟与画像由 `scripts/run_atom_runtime.py`、`scripts/run_combo_simulation.py` 写入 `data/generated/`，不进版本库。

运行时B不要求Exact baseline。已知家族使用`B_harnesses/stable/`中的介绍页先验；未知Agent使用`config/default_b_prior.json`的\(B_0\)。只有同家族`family_observation`才会收缩特化。这些先验不是实测胜率，也不增加B的真实证据覆盖。

构建阶段从`data/builtin_b/latest.json`复制稳定家族特化数据到内置矩阵；运行时由`BuiltinDatasetRegistry`读取`data/matrices/<date>/B_harnesses/stable/`。当前覆盖15个家族、七轴 B 先验。

C状态库由`config.json/revisions.jsonl/rejections.jsonl/current.json`组成，由`scripts/run_atom_runtime.py`或`scripts/run_local_usage.py`现场写入。

实验数据严格分为：

- `observed_responses/`：534条历史二值Task response及89行`Task × TypicalGroup`结果矩阵；
- `simulated/`：53,400条Task块自助、53,400条经验参数模拟和53,400条已知真值模拟，以及各自矩阵投影；
- 模拟数据不计入真实证据覆盖，不用于正式Agent排名或Matrix B拟合。

## 产物

| 文件 | 内容 |
|---|---|
| `model_catalog.jsonl` | 33个当前或近期常见模型的严格身份目录 |
| `harness_catalog.jsonl` | 23个常见 Agent/Harness，含公开仓库健康元数据 |
| `agent_model_observations.jsonl` | 295条 Agent×Model×Benchmark 观测 |
| `task_outcomes.jsonl` | 88,718条逐任务0/1/unknown结果（审计产物，不随仓库分发） |
| `coverage.json` | 身份、粒度、模型和 Harness 覆盖/缺口 |
| `source_registry.json` | 来源、commit、许可、下载状态和用途 |
| `manifest.json` | 输入文件哈希、输出指针和 `audit_artifacts` |

## 审计产物（不随仓库分发）

运行时和测试都不读取下列文件，只有重建脚本会读写它们。为了让 clone 保持在几十MB量级，它们不进版本库，但 SHA-256、字节数和行数保留在所属快照 `manifest.json` 的 `audit_artifacts` 中，重建后的副本仍然可验证。

```text
data/snapshots/<date>/task_outcomes.jsonl                                   88,718 行
data/matrices/<date>/B_harnesses/rejected_comparisons.jsonl
data/matrices/<date>/B_harnesses/crossed_task_outcomes.jsonl
data/matrices/<date>/B_harnesses/provisional_same_label_blocks.jsonl
data/matrices/<date>/B_harnesses/history/provisional_same_label_blocks.jsonl
```

`BuiltinDatasetRegistry` 对 `files` 严格校验（缺失即拒绝快照），对 `audit_artifacts` 只在文件存在时校验哈希，缺失记为 `not_distributed`。被篡改的审计产物仍然会导致快照拒绝。

本地重建：

```bash
PYTHONPATH=src python3 scripts/build_public_dataset.py --as-of YYYY-MM-DD
PYTHONPATH=src python3 scripts/build_ab_datasets.py --as-of YYYY-MM-DD
```

把已有大文件转成审计产物：

```bash
python3 scripts/mark_audit_artifacts.py --delete
```

## 证据粒度

数据集严格区分：

- `task_level`：有逐任务结果，可以进入 IRT/MIRT response matrix；
- `submission_summary`：只有验证提交汇总、运行次数、成本和 Trial ID，不能伪装成逐任务结果；
- `catalog_only`：官方目录确认模型/Agent 存在，但没有可用表现数据；
- `unresolved`：上游数据存在，但 Model/Harness 身份不能可靠拆分。

当前统计：

```text
33 catalog models
23 catalog harnesses
295 agent-model observations
275 task-level observations
20 Terminal-Bench 2.1 submission summaries
88,718 task outcomes
63,758 task outcomes with resolved Model/Harness identity
49 unresolved historical identities
```

## 主要来源

1. [Agent Psychometrics](https://github.com/dariakryvosheieva/agent-psychometrics)，MIT：SWE-bench Verified、SWE-bench Pro、GSO、Terminal-Bench 2.0任务级响应矩阵。
2. [Terminal-Bench 2.1](https://github.com/harbor-framework/terminal-bench-2-1)，Apache-2.0：当前验证提交、精确 Agent/Model/effort/version、Token、成本和 Trial ID。
3. [AgentRewardBench](https://huggingface.co/datasets/McGill-NLP/agent-reward-bench)：已登记但未下载38GB轨迹；后续用于 Judge 校准，不进入基础 Model/Harness 矩阵。
4. [DevAI / Agent-as-a-Judge](https://github.com/metauto-ai/agent-as-a-judge)：已登记，后续用于 Evidence–Criterion 和轨迹研究。

OpenAI 模型身份依据 [OpenAI Docs](https://developers.openai.com/api/docs/models)；其他模型均保存各自官方文档 URL。

## 重新构建

```bash
PYTHONPATH=src python3 scripts/build_public_dataset.py --as-of YYYY-MM-DD
```

采集器会：

- 解析源仓库 HEAD commit；
- 使用 commit-pinned Raw URL；
- 下载上游身份解析器并记录其 SHA-256；
- 保留上游无法拆分的对象为 `unresolved`；
- 从 GitHub API获取公开 Agent 仓库的 stars、forks、license、更新时间和 archived 状态；
- 为每个原始文件保存 SHA-256；
- 检查 Catalog、Observation 和 Outcome ID 唯一性；
- 拒绝覆盖同日期快照。

当前终端可能包含失效的本地代理环境变量；构建器仅在自身下载和 Git 子进程中忽略这些变量，不修改系统网络设置。

## 使用纪律

- 汇总准确率不能直接作为能力值；
- `ChatGPT`、`Codex`、`GPT-5.6` 是不同层级的身份；
- `Claude Code` 必须与具体 Claude/第三方模型、CLI版本和 effort 组合；
- 同名模型跨 Provider 不自动合并；
- 旧模型记录不回填为新模型先验；
- `unresolved` 不参与 Model/Harness 分解；
- 当前数据只能支持一维或领域内基线；多维矩阵仍需 Task Q 与 Anchor 校准。
