# AgentTogether 架构（2026-08-22，批次 46）

AgentTogether（代码名 `icle`）= Runner + 控制面。度量内核是 vendored `experience_evaluation`。决策层单独标量化。

现行规则：`DECISIONS.md`。评价原则对齐 vendored 引擎 README。

## 三层

```text
                         USER
                          │
                          ▼
┌───────────────── CONTROL PLANE ─────────────────┐
│ task.py / replay.py / collab.py     Runner      │
│ evolution.py + vendor engine        Measurement │
│ decision.py / router.py / value.py  Decision    │
│ intelligence.py                     optional LLM│
└──────────────────────┬──────────────────────────┘
                       │
              ExecutionProvider
              ├── direct_cli
              ├── provider_api
              └── scripted
                       │
                       ▼
┌────────────────── STATE PLANE ───────────────────┐
│ capture-store/     原始 SessionEvent              │
│ experience-store/                                 │
│   episodes/ replays/ judgments/ marks/ ledger/    │
│   observations/    成本与耗时，不再当能力后验      │
│   cost_actuals     token × 价表 + duration_ms     │
│   evolution/       TaskSpec / RunBundle / C / J   │
│   collabs/         挂 team_revision_id            │
└──────────────────────────────────────────────────┘
```

## 不可变规则

1. Deterministic core 先行；LLM 只在 intelligence 层出 proposal。
2. 运行事实是真源；评价是可重算派生物，不覆盖 Run。
3. 度量层禁止总分和排名；缺失不是 0。
4. 标量化只发生在 `icle-decision-loss/v0.1`，不回流度量层。
5. 不把公开 A 或 Agent 壳层分当能力。
6. 不合并 Agent Memory / Project State / Task Episode / Experience Ledger。
7. 任务成本是本地账本，不经过智能层；未知成本不是 0。

## 数据流

Task Profile → 冻结 TaskSpec → accept 导出 RunBundle → J / C → 决策层按 Q 逐轴比较 → Router / Recommend 政策排序。
