# icle 协同度量合同 v0.1

状态：`draft / identity only`

七轴锚在 LiveBench 公开类目上。协同行为（工具调用、共享记忆、索取上下文）接不上那些类目，所以 \(C=J-A-B\) 在协同维度上无定义。icle 不把团队压成总分。

## 要回答的问题

> 在固定任务合同下，把这些 Agent 按这个拓扑放在一起，相对**其中最强的成员单干**，多出（或少了）什么？

## 锚点

\[
C^{coord}_{r,k}=J^{team}_{k}-\max_{i}A^{solo}_{i,k}-B^{orch}_{k}
\]

- \(A^{solo}\) 来自同一批冻结 TaskSpec 上成员单独跑，不是 LiveBench。
- 取 `max` 而不是均值：问的是「团队是否打得过最强的那个成员」。
- 缺 solo baseline 时整轴 `unavailable`，不当 0。

## TeamRevision

`team_revision_id = hash(有序成员, topology, orchestrator, policy)`。

成员相同但拓扑不同，是两个 TeamRevision。本批只写身份：[`src/icle/coordination.py`](../src/icle/coordination.py)。协作运行挂上 `team_revision_id`，不写假 \(C^{coord}\)。

## 明确不做

- 不实现 solo baseline 执行器。
- 不发布团队总分或协同排行。
- 不把七轴 LiveBench A 借来当协同 A。
