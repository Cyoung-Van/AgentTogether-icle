# 参考项目与复用边界

本项目只复用具有明确来源和许可的代码；论文方法可以独立重现，但不默认复制无许可仓库代码。

| 项目 | 主要用途 | 可复用内容 | 边界 |
|---|---|---|---|
| [Agent Psychometrics](https://github.com/dariakryvosheieva/agent-psychometrics) | Model+Scaffold 分解、held-out pair、response matrix | MIT 代码、公开响应数据、1PL/自适应实验 | 标量 coding 基线，不能直接证明四维 Agent 能力 |
| [IRT-Router](https://github.com/Mercidaiha/IRT-Router) | 多维能力、任务 difficulty/relevance | 论文与实验结构 | 路由问题不是长期 Agent 测量；复用代码前单独核对许可 |
| [EduCDM](https://github.com/bigdata-ustc/EduCDM) | Q-matrix、MIRT、ICD | Apache-2.0 模型实现 | 教育学习假设不能直接套用到 Agent revision |
| [py-irt](https://github.com/nd-ball/py-irt) | Bayesian 1PL/2PL/4PL | MIT Python 基线 | 不提供完整多维多面 Agent 模型 |
| [TAM](https://stat.ethz.ch/CRAN/web/packages/TAM/refman/TAM.html) | MIRT、Rating Scale、Partial Credit、Multi-Facets | 统计标定参考与验证基线 | R 依赖；生产内核可独立用 Stan/PyMC 实现 |
| [mirt](https://search.r-project.org/CRAN/refmans/mirt/html/00Index.html) | 多维、graded、bifactor IRT | 模型比较和参数恢复验证 | 不是 Agent 专用数据模型 |
| [Stan IRT](https://mc-stan.org/docs/2_27/stan-users-guide/item-response-models-section.html) | 层次 IRT 与可识别性 | 可审计概率模型模板 | 多面有序扩展需要本项目编写 |
| [PyMC Ordinal](https://www.pymc.io/projects/examples/en/latest/generalized_linear_models/GLM-ordinal-regression.html) | Judge 有序评分模型 | MIT 示例和 Bayesian workflow | 需增加 Task/Judge/Capability 多面结构 |
| [FLASK](https://github.com/kaistAI/FLASK) | 固定能力 taxonomy、instance skill composition | 评价轴与聚合思路 | 主要是 answer evaluation，不是 Agent trajectory |
| [AdaRubric](https://github.com/alphadl/AdaRubrics) | 动态 Rubric、逐步轨迹评分 | Apache-2.0 原型代码 | 自报 confidence 不是校准方差；没有长期 Capability Mapper |
| [Autorubric](https://arxiv.org/abs/2603.00077) | analytic rubric、ensemble、校准、可靠性 | Judge infrastructure 思路 | 仍需 Agent 轨迹和领域校准 |
| [Agent-as-a-Judge](https://github.com/metauto-ai/agent-as-a-judge) | 完整执行过程评价、DevAI | Trajectory schema、分层需求评价 | 不提供动态长期能力后验 |
| [AgentRewardBench](https://agent-reward-bench.github.io/) | Benchmark Judge | 专家标注 Web 轨迹、Judge 对比方法 | Web 域不能直接标定 coding 四维能力 |
| [Inspect AI](https://inspect.aisi.org.uk/eval-logs.html) | EvalLog、Transcript、Scorer、Sandbox | 运行日志与评估工程模式 | 本项目仍需冻结跨 Harness RunBundle |
| [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) | Task/metric配置、aggregation、bootstrap stderr | 评估矩阵生成器的聚合与不确定度工程模式 | 主要面向LLM benchmark，本项目增加Q矩阵、Coverage和Agent结果合同 |
| [HELM](https://crfm.stanford.edu/helm/latest/) | 多场景、多指标、标准化与覆盖透明 | 多维结果矩阵和明确缺失原则 | 不直接提供Agent Harness分解 |
| [Harbor](https://www.harborframework.com/docs/run-jobs/run-evals) | Task、Trial result、Verifier与trajectory产物 | ExecutionResult适配目标 | 当前生成器只消费结果，不负责运行Agent |
| [OpenTelemetry GenAI](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md) | Agent/Workflow/Tool spans | 事件命名与可观测性兼容层 | Agent 语义约定仍在 Development，不能作为唯一稳定真源 |
| [ATLAS](https://arxiv.org/abs/2511.04689) | Fisher information、自适应选题 | 后期 Scheduler 方法 | 只有 Item 参数可靠标定后才适用 |
| [DynAEsti](https://arxiv.org/abs/1909.03586) | Dynamic IRT | 动态状态参考 | 离线、单维、非 Agent revision 语义 |
| [DeepIRT](https://github.com/ckyeungac/DeepIRT) | 历史轨迹到动态能力 | 长期神经对照模型 | TensorFlow 1.x 旧代码；不作为 v0 依赖 |

## 当前原创重点

现有项目没有完整提供以下链路：

\[
\text{Auditable Evidence}
\rightarrow
\text{Task Criterion}
\rightarrow
\text{Canonical Capability}
\rightarrow
\text{Calibrated Matrix Posterior}
\]

因此本项目的主要原创工作是：

1. 跨 Harness 的 RunBundle 与双视图隔离；
2. Evidence–Criterion 的可审计关系；
3. 动态 Rubric 到固定能力空间的 M 与 D；
4. Objective 和 Judge 两通道的联合测量；
5. Revision-aware 的 Domain×Capability×Time 状态矩阵；
6. 不生成单一总分的比较和发布合同。
