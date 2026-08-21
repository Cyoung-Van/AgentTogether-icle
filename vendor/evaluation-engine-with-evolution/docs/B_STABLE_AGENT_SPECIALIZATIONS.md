# Matrix B稳定Agent家族特化数据集

## 定位

B数据集不再追踪每个Agent release，也不以排行榜成绩作为主要来源。它根据主流Agent官方介绍中的稳定产品能力，经固定Rubric生成七轴弱先验。

```text
官方介绍/官方仓库
→ 功能特征标签
→ 固定Feature→Axis Rubric
→ canonical_logit七轴B先验
```

这些参数表达“壳提供了哪些稳定能力”，不是实测胜率。所有单元保留宽`prior SD=0.75`，具体使用偏差由C吸收。

## 覆盖

当前覆盖15个家族：Codex、Claude Code、Hermes Agent、OpenClaw、Gemini CLI、Qwen Code、OpenCode、OpenHands、Cline、Roo Code、Aider、Goose、Pi、SWE-agent、mini-SWE-agent。

数据位置：[data/builtin_b/2026-08-21](../data/builtin_b/2026-08-21/)。

```text
Agent families       15
Seven-axis cells     105
```

## 参数

以下均为`canonical_logit/v0.1`，不是0–1分数；每个单元SD均为0.75。官方介绍不对应的 LiveBench 轴（mathematics / data_analysis / language）保持基础值 +0.15。

| Agent | Reasoning | Coding | Agentic | Math | Data | Language | IF |
|---|---:|---:|---:|---:|---:|---:|---:|
| Aider | 0.21 | 0.45 | 0.26 | 0.15 | 0.15 | 0.15 | 0.20 |
| Claude Code | 0.27 | 0.45 | 0.60 | 0.15 | 0.15 | 0.15 | 0.20 |
| Cline | 0.23 | 0.45 | 0.60 | 0.15 | 0.15 | 0.15 | 0.20 |
| Codex | 0.27 | 0.45 | 0.60 | 0.15 | 0.15 | 0.15 | 0.20 |
| Gemini CLI | 0.27 | 0.44 | 0.60 | 0.15 | 0.15 | 0.15 | 0.20 |
| Goose | 0.21 | 0.40 | 0.43 | 0.15 | 0.15 | 0.15 | 0.20 |
| Hermes Agent | 0.23 | 0.19 | 0.60 | 0.15 | 0.15 | 0.15 | 0.15 |
| mini-SWE-agent | 0.21 | 0.40 | 0.23 | 0.15 | 0.15 | 0.15 | 0.20 |
| OpenClaw | 0.19 | 0.19 | 0.60 | 0.15 | 0.15 | 0.15 | 0.15 |
| OpenCode | 0.27 | 0.40 | 0.55 | 0.15 | 0.15 | 0.15 | 0.20 |
| OpenHands | 0.21 | 0.45 | 0.36 | 0.15 | 0.15 | 0.15 | 0.20 |
| Pi | 0.21 | 0.32 | 0.33 | 0.15 | 0.15 | 0.15 | 0.15 |
| Qwen Code | 0.27 | 0.44 | 0.60 | 0.15 | 0.15 | 0.15 | 0.20 |
| Roo Code | 0.27 | 0.45 | 0.60 | 0.15 | 0.15 | 0.15 | 0.20 |
| SWE-agent | 0.21 | 0.45 | 0.36 | 0.15 | 0.15 | 0.15 | 0.20 |

## Rubric

基础值为+0.15 logit。官方资料确认某项稳定能力时添加固定增量，例如：

- coding agent → coding +0.12；
- repository context → reasoning +0.06、coding +0.05；
- MCP / shell / plan / subagents → agentic_coding 增量；
- tests/verifier loop → coding + instruction_following。

完整规则：[b_specialization_rubric.json](../config/b_specialization_rubric.json)。Agent功能来源：[mainstream_agents.json](../config/mainstream_agents.json)。

## 官方来源示例

- [Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)；
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)；
- [OpenClaw](https://openclaw.ai/)；
- [Gemini CLI](https://github.com/google-gemini/gemini-cli)；
- [Qwen Code](https://github.com/QwenLM/qwen-code)；
- [OpenCode Agents](https://opencode.ai/docs/agents/)；
- [OpenHands](https://github.com/All-Hands-AI/OpenHands)。

每次构建会记录GitHub HEAD，只用于确认资料来源已检查，不参与Agent身份匹配。

## 匹配方式

```text
稳定Agent ID       → stable_family_id
官方别名           → stable_family_alias
未知Agent          → generic_default
```

`harness_version`只保留到`observed_version`供审计，匹配策略固定为`ignored_for_matching`。

例如：

```text
Codex / Codex CLI / OpenAI Codex → codex
Hermes / Nous Hermes             → hermes-agent
OpenClaw / Open Claw / Clawdbot  → openclaw
```

## 数据特化

没有数据库证据时直接使用家族特化先验，状态为`agent_specialized_prior`。存在同家族、同轴、带标准误的`family_observation`时，在家族先验上收缩，状态升级为`specialized_for_c`。

版本号不参与匹配，也不要求Exact全字段门禁。Provisional块不能特化。B不按A的时效窗口滚动更新；主流家族介绍页低频维护即可。
