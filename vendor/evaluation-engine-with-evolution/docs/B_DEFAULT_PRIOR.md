# Matrix B：默认弱先验与证据特化

## 新定位

B不再承担“精确泛化描述所有Agent壳”的核心职责，而是C生成器的修正前先验：

```text
有同家族可用观测 → 用数据收缩特化B
没有对应证据      → 使用家族先验或默认B₀
持续使用产生J      → 由C吸收具体Agent、环境、Persona、Memory和部署偏差
```

A仍保持Current View、精确Model config和严格时效门禁。

已知主流Agent会先使用[稳定Agent家族特化先验](B_STABLE_AGENT_SPECIALIZATIONS.md)；本文件中的统一B₀只用于未知Agent。

## 文献证据

1. [SWE-agent](https://arxiv.org/abs/2405.15793)在相同GPT-4 Turbo下，SWE-bench Lite从Shell-only的11%提高到18%。对应log-odds增益约：

\[
logit(0.18)-logit(0.11)=0.575
\]

2. [AgencyBench](https://aclanthology.org/2026.acl-long.337.pdf)表4在六个固定模型上比较不同Scaffold。相对其自定义Scaffold的12个差值范围为−12.8到+20.5个百分点，算术均值约+1.2个百分点，说明平均略正但方差很大，且存在明显负收益。

3. [Corral](https://openreview.net/pdf?id=7cbwuA5k0T)报告ReAct与Tool-calling框架差异总体较小，Task–Tool alignment比框架选择更重要，框架也可能引入无效开销。

4. [Qwen Code Scaffold Evolution](https://arxiv.org/abs/2607.03691)固定LLM比较35个CLI版本，未发现随版本演进而出现统计显著的平均质量提升，且部分版本增加Token/Tool成本但没有质量收益。

这些研究不适合做正式Meta-analysis，但支持“弱正中心、宽不确定度、允许负效应”的先验。

## 默认B₀

```text
prior_id        literature-shrunk-harness-prior/v0.1
mean            +0.15 logit
prior SD        0.75 logit
axes            七轴相同
scale           canonical_logit/v0.1
```

\(+0.15\) logit对应odds ratio约1.16。在基础成功率20%、50%、80%附近，分别约变为22.5%、53.7%、82.3%。这只是保守默认，不是“Agent一定提升固定百分点”。

SD 0.75故意很宽，使先验允许明显负效应和较大正效应。当前没有可靠依据为六个轴分别设置不同均值，所以先使用同一弱先验，避免虚构轴级精度。

机器配置：[default_b_prior.json](../config/default_b_prior.json)。实现：[b_prior.py](../src/experience_evaluation/b_prior.py)。

## 数据特化

若数据库存在同一稳定Agent家族、同一Canonical Axis的家族级观测（`family_observation`；旧`exact_full_gate`仍可兼容），则在先验上做normal-normal收缩。匹配只认家族ID，不认release版本。

\[
\sigma_{post}^2=\left(\sigma_0^{-2}+SE_{obs}^{-2}\right)^{-1}
\]

\[
\mu_{post}=\sigma_{post}^2\left(\frac{\mu_0}{\sigma_0^2}+\frac{B_{obs}}{SE_{obs}^2}\right)
\]

输出状态：

- `default_prior_for_c`：未知Agent，没有家族观测；
- `document_specialized_prior_for_c`：已知家族，没有家族观测；
- `specialized_for_c`：家族先验或\(B_0\)已被家族级观测收缩。

`provisional_same_label_blocks`不能特化B。Harbor补测和Exact全字段对齐不是B的主线。

## 对C的影响

C允许使用默认B，但必须输出：

```text
warnings = ["b_default_prior_used"]
```

有家族级观测特化时输出：

```text
warnings = ["b_specialized_with_family_data"]
```

B的先验SD进入C的不确定度传播。默认B不再阻断C；A未校准、J不可发布或CalibrationContract缺失仍会阻断。
