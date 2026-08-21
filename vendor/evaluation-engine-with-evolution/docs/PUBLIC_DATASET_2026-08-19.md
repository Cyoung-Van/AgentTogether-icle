# 常见 LLM 与 Agent 公开数据快照（2026-08-19）

## 纳入标准

模型满足至少一项：

- 当前官方目录明确列出，并面向 coding/agent/general workloads；
- 出现在 Terminal-Bench 2.1 验证提交；
- 在公开任务级响应矩阵中可用于连接新旧量尺。

Agent/Harness 满足至少一项：

- 官方维护的常见 coding/terminal/general Agent；
- 公开仓库活跃且具备明确项目来源；
- 出现在正式 Agent Benchmark 的验证记录中；
- 本地异构 Agent 生态需要的主要适配目标。

此目录不是“所有模型/Agent大全”，而是第一版可维护、可追溯的主流集合。

## 当前模型目录（33）

### OpenAI

- GPT-5.6 Sol
- GPT-5.6 Terra
- GPT-5.6 Luna
- GPT-5.5
- GPT-5.4
- GPT-5.3-Codex

官方 ID 与层级依据 [OpenAI Docs 模型目录](https://developers.openai.com/api/docs/models)。`gpt-5.6` 是 GPT-5.6 Sol 的别名；ChatGPT 产品身份不作为固定模型快照。

### Anthropic

- Claude Fable 5
- Claude Opus 4.8
- Claude Sonnet 5
- Claude Haiku 4.5
- Claude Opus 4.6（近期任务级连接数据）

来源：[Claude Models Overview](https://platform.claude.com/docs/en/about-claude/models/overview)。

### Google

- Gemini 3.1 Pro Preview
- Gemini 3.6 Flash
- Gemini 3.5 Flash
- Gemini 3.5 Flash-Lite

来源：[Gemini API Models](https://ai.google.dev/gemini-api/docs/models)。

### DeepSeek

- DeepSeek V4 Flash
- DeepSeek V4 Pro
- DeepSeek V3.2（历史任务级连接数据）

V4身份来源：[DeepSeek Models API](https://api-docs.deepseek.com/api/list-models)。当前快照中没有 V4 的已验证任务级 Model×Harness 响应，保持 `catalog_only`。

### Moonshot/Kimi

- Kimi K3
- Kimi K2.6

来源：[Kimi API Model Selection](https://www.kimi.com/help/kimi-api/api-model-selection)。历史矩阵包含 Kimi K2/K2.5 等旧组合，但不冒充 K3/K2.6。

### xAI

- Grok 4.5

来源：[xAI Grok 4.5](https://docs.x.ai/developers/models/grok-4.5)。

### Z.AI

- GLM-5.1
- GLM-5
- GLM-4.7

来源：[Z.AI Model Overview](https://docs.z.ai/guides/overview/overview)。

### Alibaba/Qwen

- Qwen3-Coder-480B-A35B-Instruct
- Qwen3 Coder Plus

来源：[Qwen3-Coder](https://qwenlm.github.io/blog/qwen3-coder/)。

### Mistral

- Codestral 25.08
- Mistral Medium 3.5

来源：[Mistral Codestral](https://docs.mistral.ai/models/model-cards/codestral-25-08)。

### Meta

- Llama 4 Scout
- Llama 4 Maverick
- Muse Spark 1.1

来源：[Meta Llama Developer Resources](https://ai.meta.com/llama/get-started/)。

### MiniMax

- MiniMax M2.7
- MiniMax M2.5

来源：[MiniMax API Models](https://platform.minimax.io/docs/api-reference/api-overview)。

## 当前 Agent/Harness 目录（23）

### 官方/通用 Coding 与 Terminal

- Codex
- Claude Code
- Gemini CLI
- Kimi Code
- Qwen Code
- OpenHands
- mini-SWE-agent
- SWE-agent
- Aider
- OpenCode
- Cline
- Roo Code
- Continue
- Goose
- Pi Coding Agent
- Hermes Agent

### 产品型或 Benchmark 中常见

- Cursor CLI
- Warp
- Factory Droid
- Terminus 2
- Grok Build

### General Agent

- Kimi Agent
- MiniMax Agent

公开仓库元数据已写入 `harness_catalog.jsonl`。例如当前快照记录 Codex、Claude Code、Gemini CLI、OpenHands、OpenCode、Hermes Agent、Pi、Aider 等仓库的 stars、forks、license、更新时间和 archived 状态；这些热度数据只用于目录纳入与维护，不进入能力评分。

## 表现数据覆盖

| Benchmark | Agent×Model观测 | 粒度 |
|---|---:|---|
| SWE-bench Verified | 134 | 逐任务，500项 |
| SWE-bench Pro | 14 | 逐任务，730项 |
| GSO | 15 | 逐任务，102项 |
| Terminal-Bench 2.0 | 112 | 逐任务，89项 |
| Terminal-Bench 2.1 | 20 | 验证提交汇总，含445 Trial IDs/提交 |

当前可直接确认的近期组合包括：

- Codex + GPT-5.6 Sol / Terra / Luna，`max`；
- Codex + GPT-5.5，`xhigh`；
- Claude Code + Claude Fable 5，`xhigh`；
- Claude Code + Claude Opus 4.8，`high`；
- Claude Code + Claude Sonnet 5，`high`；
- Claude Code + GLM-5.1，`max`；
- Gemini CLI/Terminus 2 + Gemini 3.1 Pro Preview，`high`；
- Cursor CLI + Grok 4.5；
- mini-SWE-agent + Muse Spark 1.1。

同一组合可能有不同日期提交；它们作为独立 Observation 保留，不挑最高分覆盖历史。

## 可靠性结论

- 当前已经形成可用于一维 IRT、任务难度和部分 Model/Harness crossed design 的数据基础。
- 只有63,758条逐任务记录同时具有已解析 Model/Harness 身份。
- 49个历史 Agent subject 无法按上游规则可靠拆分，保持 `unresolved`，不进入 A/B 分解。
- DeepSeek V4、Kimi K3/K2.6、Gemini 3.5/3.6、部分 Mistral/Meta/MiniMax 当前只有官方目录信息或汇总先验，不能生成任务级基础矩阵。
- 多维 \(A_m,B_h\) 尚不能直接发布：下一步必须建立 Task Q、Anchor 和跨 Benchmark linking。

## 文件位置

- [模型目录](../data/snapshots/2026-08-19/model_catalog.jsonl)
- [Agent/Harness目录](../data/snapshots/2026-08-19/harness_catalog.jsonl)
- [Agent×Model观测](../data/snapshots/2026-08-19/agent_model_observations.jsonl)
- [逐任务结果](../data/snapshots/2026-08-19/task_outcomes.jsonl)
- [覆盖报告](../data/snapshots/2026-08-19/coverage.json)
- [来源清单](../data/snapshots/2026-08-19/source_registry.json)
- [Manifest](../data/snapshots/2026-08-19/manifest.json)

