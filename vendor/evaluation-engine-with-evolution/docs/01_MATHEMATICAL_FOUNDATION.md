# 基础数学规格 v0.1

状态：`provisional / implementation contract`

本文件冻结第一版符号、量尺、观测模型和约束。它不宣称模型已经通过现实数据验证；任何能力画像发布都必须经过后续测量门禁。

## 1. 估计对象

第一版估计对象不是“Agent 总分”，而是：

> 在固定 AgentRevision、任务领域与时间窗内，Agent 在多个能力维度上的潜在状态及其联合不确定度。

当前实现轴跟随 LiveBench 七类：

\[
K=\{\text{reasoning},\text{coding},\text{agentic\_coding},\text{mathematics},\text{data\_analysis},\text{language},\text{instruction\_following}\}
\]

这些维度是版本化构念，不假设相互独立。后续可以根据构念效度检验合并、拆分或加入 general factor。

## 2. 索引与对象

| 符号 | 含义 |
|---|---|
| \(r\) | AgentRevision |
| \(m\) | Model revision |
| \(h\) | Harness revision |
| \(i\) | TaskSpec / task instance |
| \(n\) | RunBundle / 一次执行 |
| \(d\) | Task domain |
| \(w\) | Time window |
| \(k\) | Canonical capability |
| \(c\) | Task-specific rubric criterion |
| \(g\) | Judge revision |
| \(v\) | Objective verifier revision |
| \(p\) | Objective property |
| \(\ell\) | Ordinal rating threshold |

## 3. AgentRevision

`AgentRevision` 至少由以下不可变标识构成：

\[
r=hash(m,h,prompt,persona,memory\_policy,tools,environment)
\]

重大配置变化创建新 revision，不把变化静默吸收到动态残差中。

项目和 Session 条件作为运行条件保存；它们默认不是长期 Agent 能力的一部分。

## 4. 统一量尺

数学内核只在无界潜变量量尺上推断：

\[
\boldsymbol\theta_{r,d,w}\in\mathbb R^K
\]

原始 Rubric 等级、Judge confidence、百分制和雷达图均不属于该量尺。

如需展示，使用单向变换：

\[
display_{r,d,w,k}=F_k(\theta_{r,d,w,k})
\]

其中 \(F_k\) 可以是固定参考总体 CDF、T-score 或显式版本化的非线性映射。展示值不得回流到推断层。

## 5. 基础先验

长期模型保留以下分解：

\[
\mathbf H_{0,r}
=
\boldsymbol\mu
+\mathbf A_m
+\mathbf B_h
+\mathbf I_{m,h}
+\mathbf U_r
\]

- \(\boldsymbol\mu\)：总体能力中心；
- \(\mathbf A_m\)：Model 主效应；
- \(\mathbf B_h\)：Harness 主效应；
- \(\mathbf I_{m,h}\)：Model–Harness 交互；
- \(\mathbf U_r\)：其余 revision-specific effect。

v0 不估计完整交互，默认：

\[
\mathbf I_{m,h}=\mathbf 0
\]

如果没有完成外部量尺链接，\(\mathbf H_0\) 使用宽先验或同领域历史后验，不从排行榜直接填数。

## 6. 当前状态

在固定 revision 内：

\[
\boldsymbol\theta_{r,d,w}
=
\mathbf H_{0,r}
+\mathbf C_{r,d,w}
\]

\(\mathbf C\) 是领域与时间窗条件下的 deployment residual，不等于“经验带来的因果增益”。

项目、Session 和临时环境影响通过运行条件 \(\mathbf x_n\) 单独进入观测模型：

\[
\boldsymbol\eta_n
=
\boldsymbol\theta_{r(n),d(n),w(n)}
+\Gamma\mathbf x_n
\]

## 7. 任务语义覆盖与区分度

任务是否涉及某能力由语义掩码表示：

\[
Q_{i,k}\in\{0,1\}
\]

任务在客观属性 \(p\) 上对能力的区分度单独表示：

\[
a^{obj}_{i,p,k}\ge 0,
\qquad
a^{obj}_{i,p,k}=0\;\text{if}\;Q_{i,k}=0
\]

不得把语义 Q、测量区分度、Evidence relevance 和统计 precision 当成同一个参数。

## 8. 客观结果通道

对二元客观属性：

\[
y_{n,p}
\sim
Bernoulli(\pi_{n,p})
\]

\[
logit(\pi_{n,p})
=
\mathbf a^{obj}_{i(n),p}{}^\top
\boldsymbol\eta_n
-b_{i(n),p}
\]

- \(b_{i,p}\)：任务在客观属性 \(p\) 上的难度；
- \(\mathbf a^{obj}_{i,p}\)：区分度向量；
- \(y\)：Verifier 输出，不由 Judge 改写。

多类别或连续 Objective 使用相应 likelihood，不强制压成一个 success bit。

## 9. Rubric–Capability 映射

任务 \(i\) 的 Criterion \(c\) 映射为：

\[
M_{i,c,k}\in[0,1]
\]

Criterion 对能力的经验区分度为：

\[
\lambda_{i,c,k}\ge0
\]

有效测量载荷：

\[
D_{i,c,k}=M_{i,c,k}\lambda_{i,c,k}
\]

并要求：

\[
D_{i,c,k}=0\;\text{if}\;Q_{i,k}=0
\]

低映射权重意味着低信息量，不能通过归一化变成完整能力证据。

## 10. Judge 有序评分通道

Judge 原始评分使用 Criterion-specific ordinal anchor，例如 \(0,1,2,3,4\)，以及独立的缺失状态。

对一次运行 \(n\)、Judge \(g\)、Criterion \(c\)：

\[
P(S_{n,g,c}\ge\ell)
=
\sigma\left(
\mathbf D_{i(n),c}^{\top}\boldsymbol\eta_n
-\delta_{i(n),c}
-\beta_{g,c,d(n)}
-\tau_{c,\ell}
\right)
\]

- \(S\)：原始有序评分；
- \(\delta\)：Criterion 难度；
- \(\beta\)：Judge 在 Criterion/Domain 上的 severity 或 bias；
- \(\tau\)：有序等级阈值；
- \(\mathbf D\)：Criterion 对 Canonical Capability 的有效载荷。

Judge confidence 单独存储，只有经过 calibration 后才能影响误差模型；它不直接等于 \(1/\sigma^2\)。

## 11. Evaluation Matrix 的正式定义

一次运行的 Evaluation Matrix 不是单个向量。它是测量包：

\[
\mathcal J_n
=
\{Q_i,A^{obj}_i,E_n,S_n,M_i,O_n,provenance_n\}
\]

其中：

- \(Q\)：任务能力语义矩阵；
- \(A\)：任务区分度；
- \(E\)：Evidence–Criterion 关系；
- \(S\)：Judge–Criterion 原始评分；
- \(M\)：Rubric–Capability 映射；
- \(O\)：Objective Outcome；
- `provenance`：所有版本、hash、缺失与可见性信息。

禁止使用：

\[
\mathbf C=\mathbf J-\mathbf H_0
\]

作为正式更新公式。

## 12. 联合后验

能力状态由两条通道联合推断：

\[
p(\boldsymbol\theta\mid O,S,Q,A,M,E,revision)
\propto
p(\boldsymbol\theta\mid\mathbf H_0)
\prod_{n,p}p(O_{n,p}\mid\boldsymbol\theta)
\prod_{n,g,c}p(S_{n,g,c}\mid\boldsymbol\theta)
\]

输出至少包含：

\[
\hat{\boldsymbol\theta}_{r,d,w}
=E[\boldsymbol\theta_{r,d,w}\mid data]
\]

以及联合协方差：

\[
\Sigma_{r,d,w}
=Cov(\boldsymbol\theta_{r,d,w}\mid data)
\]

第一版可以用 many-facet ordinal model、GLS 或 hierarchical Bayesian model；WLS 仅可作为 sanity-check baseline。

## 13. 信息量与发布门禁

每个领域的后验信息矩阵：

\[
\mathcal I_{r,d,w}
=
\Sigma_{r,d,w}^{-1}
\]

实际实现应区分 prior information 与 observed information。

只有同时满足以下条件时才发布正式能力单元：

- 存在适用的 Task/Rubric；
- 存在足够的可观察证据；
- Judge/Verifier 版本通过允许列表；
- calibration 适用于当前 Domain；
- posterior interval 与有效信息量达到预注册门槛；
- 未触发严重 DIF、数据泄漏或完整性警报。

否则输出状态而不是分数：`insufficient_evidence`、`not_applicable`、`not_calibrated` 或 `invalid`。

## 14. 动态状态

v0 先估计固定 revision、固定时间窗的静态状态。

通过静态门禁后，动态基线可使用：

\[
\mathbf C_{r,d,w}
=
F_{\Delta t}\mathbf C_{r,d,w-1}
+\boldsymbol\omega_w,
\qquad
\boldsymbol\omega_w\sim\mathcal N(0,Q_{process})
\]

重大 AgentRevision 变化建立新状态序列。EventLog 只提供 change-point prior，不自动证明因果关系。

## 15. 可识别性约束

至少要求：

1. \(\sum_m\mathbf A_m=0\)，\(\sum_h\mathbf B_h=0\)；
2. Model–Harness 观测二部图连通；
3. latent population 的中心和尺度固定，或固定一组 Anchor item 参数；
4. discrimination 非负并具有显式尺度约束；
5. 每个能力轴存在正向、反向和无关控制任务；
6. Rubric mapping 在 Agent 运行前冻结；
7. 外部 Benchmark 只有在存在 common task/common agent linking 时才能进入同一量尺；
8. 不完整 Model×Harness 网格使用 hierarchical shrinkage；
9. interaction 如启用，使用逐能力低秩张量而非产生标量的简单内积；
10. 参数恢复必须先通过 synthetic ground truth。

## 16. J 评估原子到 A 子任务的线性投影

A 的数据源固定为 LiveBench current 子任务。记：

- \(s\)：A 侧 LiveBench current 子任务，共 23 项，目录 `livebench-current-subtask/v0.1`；
- \(k\)：七个 canonical 轴；
- \(c\)：J 侧评估原子，目录 `eval-atom-seven-link/v0.1`。原子来自成熟评估项目的构念（FLASK、Harbor/Inspect、DevAI、SWE-PolyBench、IFEval、HELM、HealthBench 行为轴、BFCL），**不是题目**。

隶属矩阵 \(G\) 由 LiveBench 类目给出，一行只有一个 1：

\[
G_{s,k}\in\{0,1\},
\qquad
\sum_k G_{s,k}=1
\]

A 现行校准保持不变：

\[
p^{A}_{m,k}
=
\frac{1}{|S_k|}\sum_{s}G_{s,k}\frac{\mathrm{score}_{m,s}}{100},
\qquad
A_{m,k}=\operatorname{logit}(\mathrm{clip}(p^{A}_{m,k}))
\]

J 原子观测 \(o_{n,c}\in[0,1]\)。未观测的 \(c\) 不进入和式，不得记 0。未链接原子（当前为 BFCL 工具/记忆与 HealthBench `context_seeking`）不进入 \(T\)，因此也不进入 \(C\)。

原子到 A 子任务的映射 \(T\) 冻结在 `j-atom-to-livebench-subtask/v0.1`：

\[
T_{c,s}\in(0,1]
\]

缺边视为 0。**禁止按行归一化**：低权重表示低信息量。

轴载荷由子任务映射折叠：

\[
W_{c,k}=\max_{s:G_{s,k}=1}T_{c,s}
\]

任务语义掩码 \(Q_{i,k}\) 仍有效。有效轴权重：

\[
\widetilde W_{n,c,k}=Q_{i(n),k}\,W_{c,k}
\]

子任务投影与轴投影使用同一加权均值，只是权重不同：

\[
p^{J}_{n,s}
=
\frac{\sum_{c\in\mathcal O_n}T_{c,s}\,o_{n,c}}{\sum_{c\in\mathcal O_n}T_{c,s}},
\qquad
p^{J}_{n,k}
=
\frac{\sum_{c\in\mathcal O_n}\widetilde W_{n,c,k}\,o_{n,c}}{\sum_{c\in\mathcal O_n}\widetilde W_{n,c,k}}
\]

分母为 0 时该目标记 `insufficient_evidence`，不是 0。进入 canonical 量尺：

\[
J_{n,s}=\operatorname{logit}(\mathrm{clip}(p^{J}_{n,s})),
\qquad
J_{n,k}=\operatorname{logit}(\mathrm{clip}(p^{J}_{n,k}))
\]

\(C\) 仍只在七轴 logit 上计算：

\[
C_{n,k}=J_{n,k}-A_{m(n),k}-B_{h(n),k}
\]

仅当 \(J,A,B\) 在该轴都有值。子任务投影只用于覆盖诊断和未来 common-item linking，不直接代入 \(C\)。

本层不声称已从数据估计 IRT 区分度，也不声称 J 任务集已与 LiveBench 完成等值。

## 17. 明确不做

- 不计算 Agent 总分；
- 不把矩阵简单平均成百分制；
- 不将 `NA` 当作 0；
- 不把 Judge confidence 直接当统计权重；
- 不要求隐藏 Chain-of-Thought；
- 不让 Process Judge 读取最终 Objective Outcome；
- 不在校准门禁失败时更新长期能力状态。
