# DECISIONS — icle

## 2026-08-14 D1: 项目定位

icle = Personal Agent Experience & Routing System。以用户真实工作产生的 Agent 会话/任务/结果/反馈为经验来源，目标是「什么 Agent 在什么情况下对这个用户最有用」，不做工业级 Benchmark 排行。现行规则以本文件和 `docs/ARCHITECTURE.md` 为准。

## 2026-08-14 D2: 技术基座

- Python 3.11 纯标准库、src layout（PYTHONPATH=src）、unittest 驱动——沿用 agent-eval-prototype 验证过的工程纪律；
- 核心四对象自有协议：TaskEpisode / ExperienceLedger / AgentRegistry(+Revision) / ExecutionProvider；
- Deterministic Experience Core 不依赖 LLM；Intelligence Layer 后置且可插拔；
- DeepSeek Harness 仅作可插拔 ExecutionProvider，pin 版本，标记 experimental，不进核心依赖；
- agent-eval-prototype 保留为 calibration/legacy-eval 工具，icle 不与其代码耦合（ Replay 执行时可把它的 runner 作为一种 provider）。

## 2026-08-14 D3: 数据纪律

- 原始记录 append-only； ledger 可重建投影，算法 bug 可重算；
- LLM 输出只能是 proposal/evidence，经验证+策略后才写 State Plane；
- 不合并 Agent Memory / Project State / Task Episode / Experience Ledger 四个概念。

## 2026-08-21 D4: 能力评价真源是 A+B+C

能力评价不再由用户 accept 的收缩分充当模型后验。真源是 vendored `experience_evaluation` 的七轴残差：

- `C = J − A − B`，轴合同 `livebench-capability-seven/v0.1`，尺度 `canonical_logit/v0.1`。
- icle 继续当 runner：冻结 TaskSpec、导出 RunBundle、写入 `publication_scope=local_controlled`。
- 引擎只度量，不出总分、不做排名、不把缺失当 0。
- 用户 accept 不是公开 verifier，也不宣称与 LiveBench 有 common-item。
- Router / recommend / value 的标量化仍留在未来决策层；本批不把 C 送进路由。
- 上游代码与数据许可见 `vendor/evaluation-engine-with-evolution/NOTICE`；icle 不重新授权 `data/`。

## 2026-08-21 D5: Runner + Measurement + Decision

icle 收成三层 OS，不再用合成分当能力：

- Runner 只产运行事实。
- Measurement 只出七轴 A/B/J/C。
- Decision 的 `icle-decision-loss/v0.1` 才做 Q 加权标量化；不回流度量层。
- 路由质量禁止使用公开 A 或 SWE-bench 合成分；壳层 mark 只进 uncertainty。
- 协同轴另立合同，锚点是成员单干；本批只写 TeamRevision 身份。

## 2026-08-22 D6: 成本是本地账本

- 任务成本不经过智能层。执行器读 usage 或强制任务表，查价表，写 `cost_actuals.jsonl`。
- token 与墙钟时间都是成本事实，不是 J 的计分项。缺消耗不能接受。
- 未知价格保持 `null`，订阅/本地 CLI 不伪造成 `$0`。
- 产品 SKU 只映射到目录里的同名编码（如 `kimi-for-coding` → `kimi-k2.7-code`），不把 K2.7 静默当成 K3。
- 官方发票仍高于社区价表；用户覆盖最高。

## 2026-08-22 D7: MIT，最小公开面

- icle 源码与文档使用 MIT。
- 公开集：`README`、`LICENSE`、`DECISIONS`、`docs/ARCHITECTURE.md`、`docs/COORDINATION_MEASUREMENT.md`、`src/`、`web/`（不含 `dist` / `node_modules`）、`tests/`、`skills/`、`vendor/`、`requirements.txt`。
- 批次流水、接手笔记、历史计划书留在本地 `docs/internal/` 与 `docs/archive/`，不进 Git。
- vendor 引擎代码是 Apache-2.0；`vendor/.../data/` 不随 MIT 重新授权，见该目录 `NOTICE`。
