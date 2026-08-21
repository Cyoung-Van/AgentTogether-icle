# 矩阵与参数规格 v0.1

## 1. 存储原则

Rubric 数量随任务变化，因此物理真源使用 append-only 长表；矩阵是按 TaskSpec、MeasurementRevision、Domain 和 TimeWindow 生成的只读投影。

每条长表记录至少包含：

```text
record_id
run_id
task_spec_id
agent_revision_id
measurement_revision_id
matrix_kind
row_key
column_key
value
value_status
provenance
created_at
content_hash
```

## 2. 缺失语义

所有矩阵统一使用以下状态，状态与数值分开存储：

| 状态 | 含义 |
|---|---|
| `observed` | 有合法观测 |
| `not_applicable` | Task/Rubric 不涉及该单元 |
| `not_observed` | 涉及但本次没有表现机会或证据 |
| `missing` | 数据应该存在但缺失 |
| `redacted` | 因隐私或安全被遮蔽 |
| `invalid` | 输入或计算不可信 |
| `not_calibrated` | 有原始评分但没有适用校准 |
| `insufficient_evidence` | 信息量不足，不能发布状态 |

数值 `0` 只表示合法量尺上的零，不能表示缺失。

## 3. 矩阵目录

| 矩阵 | 逻辑形状 | 值 | 来源 | 参数类型 |
|---|---|---|---|---|
| \(Q\) Task–Capability | \(T\times K\) | 0/1 | TaskSpec | 固定/审定 |
| \(A^{obj}\) Objective Discrimination | \(T\times P\times K\) | \(\ge0\) | calibration | 学习 |
| \(E\) Evidence–Criterion | \(N\times L_n\times R_i\) | polarity + strength | extractor | 观测 |
| \(S\) Judge–Criterion | \(N\times G\times R_i\) | 0..4/NA | judge/human | 观测 |
| \(M\) Rubric–Capability | \(T\times R_i\times K\) | 0..1 | TaskSpec/mapper | 固定后校准 |
| \(D\) Effective Loading | \(T\times R_i\times K\) | \(\ge0\) | \(M\odot\Lambda\) | 派生 |
| \(O\) Objective Outcome | \(N\times V\times P\) | status/value | verifier | 观测 |
| \(B^{judge}\) Judge Bias | \(G\times R\times D\) | real | calibration | 学习 |
| \(\Theta\) Capability State | \(Revision\times Domain\times Window\times K\) | real | posterior | 派生 |
| \(U\) Uncertainty | 同 \(\Theta\) | positive | posterior | 派生 |
| \(\Sigma\) Covariance | \(Revision\times Domain\times Window\times K\times K\) | PSD | posterior | 派生 |
| \(\mathcal I\) Information | 同 \(\Sigma\) | PSD | posterior | 派生 |
| \(COV\) Coverage | 同 \(\Theta\) | structured | evidence/projector | 派生 |

## 4. Task–Capability 矩阵 Q

### 定义

\[
Q_{i,k}\in\{0,1\}
\]

### 语义

- `1`：TaskSpec 确实创造了该能力的表现机会；
- `0`：不应从该任务推断该能力；
- `not_reviewed`：不能进入正式测量。

### 来源

- 人工锚定；
- 任务模板默认；
- LLM/规则只能生成 proposal；
- 进入评估前必须冻结。

### 约束

- 每项任务至少一个能力为 1；
- 每个能力轴必须有高覆盖 Anchor、低覆盖控制和无关控制；
- 同一 TaskSpec 的所有 Agent 共用 Q；
- Q 更新创建新 TaskSpec revision。

## 5. Objective Discrimination 矩阵 A

### 定义

\[
A^{obj}_{i,p,k}\ge0
\]

### 来源

- 初始阶段使用正值弱先验；
- 通过多个 AgentRevision×Task 的 Objective response matrix 学习；
- 数据不足时固定为简化基线，但必须标记 assumption。

### 约束

\[
A^{obj}_{i,p,k}=0\quad\text{if}\quad Q_{i,k}=0
\]

- discrimination 不允许从排行榜直接复制；
- 每个参数保存 posterior interval 与 calibration dataset hash；
- 负 discrimination 触发 TaskSpec 质量审查。

## 6. Evidence–Criterion 矩阵 E

### 逻辑值

每个单元至少包含：

```text
polarity: -1 | 0 | +1
strength: 0..1
evidence_ids: []
status
extractor_revision
```

### 语义

- `+1`：证据支持达到 Criterion；
- `-1`：证据反驳或显示失败；
- `0`：Evidence 与 Criterion 无直接关系；
- `NA`：不存在合法表现机会。

### 约束

- strength 不是评分；
- 同一原始事件的重复引用不得增加独立信息量；
- 每条 Evidence 必须能回溯到 event/artifact hash；
- Evidence 可以 cross-load，但所有关联必须显式；
- critical evidence 单独记录 gate，不被平均。

## 7. Judge–Criterion 矩阵 S

### 定义

\[
S_{n,g,c}\in\{0,1,2,3,4\}\cup\{NA\}
\]

### 评分锚点

通用名称仅作结构，具体行为由 Criterion 冻结：

| 等级 | 结构语义 |
|---|---|
| 0 | 与要求相反、造成损害或关键失败 |
| 1 | 基本未达到 |
| 2 | 部分达到、存在明显缺口 |
| 3 | 达到冻结要求 |
| 4 | 充分达到且证据完整、验证可靠 |

### Sidecar 参数

```text
self_reported_confidence
evidence_refs
counterevidence_refs
reason_code
prompt_hash
temperature
seed
visibility_mask
```

### 约束

- 不使用百分制；
- 不对不同 Criterion 的 raw rating 直接求平均；
- Judge confidence 不直接成为 precision；
- 无证据必须 NA；
- Judge 不可读取 Agent 身份和 Outcome pass/fail；
- 多 Judge disagreement 保留，不用多数票静默抹平。

## 8. Rubric–Capability 矩阵 M

### 定义

\[
M_{i,c,k}\in[0,1]
\]

### 来源等级

```text
human_anchor
template_anchor
rule_proposal
llm_proposal
learned_adjustment
```

### 约束

- 运行前冻结；
- 稀疏优先；
- 不要求每行和为 1，因为 Criterion 可以不充分测量任何长期能力；
- 不允许把唯一的 0.1 弱载荷归一化成完整分数；
- 与 Q 冲突时禁止发布；
- 保存 mapping uncertainty、review status 和 mapper revision；
- 同类任务重复生成必须通过稳定性与内容效度测试。

## 9. Effective Loading 矩阵 D

### 定义

\[
D_{i,c,k}=M_{i,c,k}\lambda_{i,c,k}
\]

\(\lambda\) 是通过 calibration 学习的 Criterion discrimination。

### 约束

- \(D\ge0\)；
- \(Q=0\Rightarrow D=0\)；
- 数据不足时 \(\lambda\) 使用收缩先验；
- D 影响 posterior information，不只是点估计；
- D 的版本与 calibration dataset 绑定。

## 10. Objective Outcome 矩阵 O

### 属性建议

```text
correctness
completeness
side_effects
reproducibility
safety
resource_limit
artifact_integrity
```

### 单元结构

```text
status: pass | fail | partial | unknown | invalid
observed_value
expected_value
unit
evidence_refs
verifier_revision
```

### 约束

- 不计算统一 Objective 总分；
- Verifier 异常为 unknown/invalid，不是 fail；
- 同一 property 可以有多个 Verifier，冲突必须保留；
- Process Judge 不读取 O；
- verifier/version/fixture hash 必须保存。

## 11. Judge Bias 矩阵

### 定义

\[
B^{judge}_{g,c,d}=\beta_{g,c,d}
\]

### 学习数据

- 人工 gold；
- 重复评分；
- corruption suite；
- style-only control；
- 自然成功/失败；
- 跨 Agent/Task/Judge family 设计。

### 约束

- calibration 有有效 Domain、language、time 和 Judge revision；
- 超出有效范围时标记 not_calibrated；
- position/style/verbosity/self-family bias 单独报告；
- 不假设 bias 跨版本稳定。

## 12. Capability State 矩阵 Theta

### 定义

\[
\Theta_{r,d,w,k}=E[\theta_{r,d,w,k}\mid data]
\]

### 语义

- 实数 latent scale；
- 0 表示参考量尺中心，不表示 0% 能力；
- 正负值表示相对锚定量尺的位置；
- 不跨未链接量尺比较。

### 约束

- 无总分列；
- Domain 间不自动补值；
- Revision 间不自动平滑；
- 必须与 U、Sigma、Coverage 同时发布；
- `insufficient_evidence` 单元不输出伪点估计。

## 13. Uncertainty 矩阵 U

### 定义

\[
U_{r,d,w,k}=SD(\theta_{r,d,w,k}\mid data)
\]

也可以附带预注册 credible interval。

### 约束

- unknown 不能写 0；
- 必须传播 H0、Task、Mapper、Judge 和 sampling uncertainty；
- 共享 Judge/Task 导致的相关误差不能当独立样本；
- posterior coverage 必须在 synthetic 和 locked test 中验证。

## 14. Covariance 矩阵 Sigma

### 定义

\[
\Sigma_{r,d,w}
=Cov(\boldsymbol\theta_{r,d,w}\mid data)
\]

### 约束

- 对称、半正定；
- 保留 Reasoning/Coding/Tool/Planning 的真实相关性；
- 不把所有 off-diagonal 强制设为 0；
- 若使用近似推断，必须报告近似方法；
- Sigma 是比较和 adaptive selection 的必要输入。

## 15. Information 矩阵

### 定义

\[
\mathcal I_{r,d,w}=\Sigma_{r,d,w}^{-1}
\]

实际实现分别保存：

```text
prior_information
observed_information
total_information
```

### 用途

- 判断是否可以发布能力单元；
- 选择下一项高信息任务；
- 区分“样本数量多”与“真正信息量高”。

## 16. Coverage 矩阵

每个 Domain×Capability 单元包含：

```text
applicable_task_count
valid_run_count
objective_observation_count
judge_observation_count
human_gold_count
distinct_task_family_count
distinct_agent_context_count
effective_information
latest_observation_at
measurement_status
missing_reasons
```

Coverage 不参与能力加分，只解释证据是否充分。

## 17. 时间状态张量

后期表示为：

\[
\Theta_{Revision,Domain,Window,Capability}
\]

要求：

- window 规则固定并版本化；
- 同一 revision 内才允许连续模型；
- revision 变更产生新的时间序列；
- EventLog 只提供变点候选；
- 动态变化必须报告 false-positive、detection delay 和 uncertainty。

## 18. Agent 比较矩阵

比较两个 AgentRevision 时输出：

\[
P_{d,k}(r_a>r_b\mid data)
\]

| Domain | Reasoning | Coding | Tool Use | Planning |
|---|---|---|---|---|
| Bugfix | probability | probability | probability | probability |
| Refactor | probability | probability | probability | probability |

比较结果必须带有效量尺、任务分布和证据范围，不生成“总胜率”。

## 19. 参数所有权

### 规范固定并版本化

- Capability ontology；
- TaskSpec/Q proposal；
- Criterion 行为锚点；
- Verifier contract；
- 缺失值规则；
- AgentRevision 规则；
- Schema 和 hash 算法。

### 必须由数据学习

- Task/Property difficulty \(b\)；
- Objective discrimination \(A^{obj}\)；
- Criterion discrimination \(\lambda\)；
- Judge severity/bias \(\beta\)；
- Rating thresholds \(\tau\)；
- Capability covariance \(\Sigma\)；
- Dynamic process parameters。

### 半人工、半学习

- Q；
- M；
- task/domain taxonomy；
- initial H0 linking。

### 只读派生

- D；
- Theta；
- U；
- Sigma；
- Information；
- Coverage；
- Agent comparison matrices。

## 20. 发布禁止项

API、报告和 UI 均不得将以下内容当成数学真源：

- 单一 Agent 总分；
- 各维简单平均；
- 未说明量尺的百分制；
- 用 0 代替缺失；
- 不带不确定度的能力单元；
- 不带 Domain 的全局能力结论；
- 不带 AgentRevision 的历史比较；
- 不带 Evidence 与 MeasurementRevision 的 Judge 结论。

## 21. Harness 矩阵 B 的共同任务块门禁

矩阵 B 只能从可比较的共同任务块中估计。一个有效比较块至少固定：

```text
benchmark_id
benchmark_version
task_id
task_revision / fixture_hash
verifier_revision
environment_revision
resource_limits
timeout_policy
context_policy
attempt / pass@k policy
```

### 一级证据：Exact Matched Block

同时要求：

```text
相同 task
相同 model revision
相同 provider route（如会影响行为）
相同 reasoning effort
不同 harness revision
```

在该条件下，Model与Task效应可以在成对/分块比较中抵消，是B的主要证据。

### 二级证据：Connected Crossed Design

模型不完全相同时，只有在Model–Harness二部图通过共同Model和共同Task保持连通，且存在一级锚点时，才允许通过层次模型估计B。该结果必须标记为`linked_inference`，不得描述为直接对照。

### 不可用证据

- 只有相同能力维度、但Task ID不同；
- Benchmark名称相同但版本/Fixture不同；
- 一个结果是pass@1，另一个是pass@5；
- Model名称相同但快照或reasoning effort未知；
- 不同资源、超时或网络策略；
- 只有排行榜总分，没有共同Task结果。

这些记录可以保留为目录或弱先验，但不能进入B的直接估计。

### 数学含义

在模型：

\[
logit\,P(Y_{m,h,t}=1)=\mu+A_m+B_h+I_{m,h}-b_t
\]

中，对同一Model与同一Task比较两个Harness：

\[
\Delta logit(Y)=B_{h_1}-B_{h_2}+I_{m,h_1}-I_{m,h_2}
\]

因此同Task消除任务难度，同Model消除模型主效应；若暂设交互为零或使用收缩先验，即可得到最可靠的B差异。
