# C状态更新器

## 定位

C生成器输出单批残差观测；CStateStore把这些观测更新成随使用演化的持久状态：

```text
A + B + J → C observation
                  ↓
         decay + process noise
                  ↓
       precision-weighted update
                  ↓
        persistent CState revision
```

实现：[c_state.py](../src/experience_evaluation/c_state.py)。CLI：[update_c_state.py](../scripts/update_c_state.py)。

## 状态空间

每个`Subject × Canonical Axis`独立维护：

```text
mean / variance / sd
95% interval
evidence_count
last_observed_at
latest source/calibration/warnings
revision hash
```

轴固定为`livebench-capability-seven/v0.1`，尺度固定为`canonical_logit/v0.1`。

## 预测步骤

距离上次观测\(\Delta t\)天时，均值向0衰减：

\[
d=0.5^{\Delta t/T_{half}}
\]

\[
\mu^-_t=d\mu_{t-1}
\]

方差增加过程噪声：

\[
V^-_t=V_{t-1}+q\Delta t
\]

默认：

```text
half life                 180 days
process variance/day      0.001
initial mean              0
initial SD                1.0
minimum observation SD    0.05
```

## 更新步骤

若新C观测为\(y_t\)，观测方差为\(R_t\)：

\[
V_t=\left((V^-_t)^{-1}+R_t^{-1}\right)^{-1}
\]

\[
\mu_t=V_t\left(\frac{\mu^-_t}{V^-_t}+\frac{y_t}{R_t}\right)
\]

证据越精确，对状态影响越大；长期没有观测时均值逐步回到0，不确定度因过程噪声扩大。

## 持久化

```text
config.json         冻结更新合同
revisions.jsonl     append-only更新历史
rejections.jsonl    不可用、冲突或非法观测
current.json        可由Revision完全重建的当前投影
```

Revision形成SHA-256链：

```text
GENESIS → revision 1 → revision 2 → ...
```

修改历史Revision会导致`verify_and_rebuild()`失败。

## 幂等与拒绝

- C生成器的`cell_id`绑定不可变观测内容指纹；同一观测重复输入：跳过，计为duplicate；
- 同一`Subject × Axis`的不同批次会生成不同`cell_id`，可以进入增量更新；
- 同一`cell_id`但内容不同：拒绝为`cell_id_content_conflict`；
- C不可用、非七轴、量尺错误、无标准误：进入拒绝日志；
- 时间早于当前状态：拒绝，不倒序更新。

## CLI

更新：

```bash
python3 scripts/update_c_state.py \
  --store data/c_state/my-agent \
  --c-matrix path/to/c_matrix.jsonl \
  --observed-at 2026-08-20T12:00:00+08:00 \
  --source-bundle-id abjc-run-001
```

校验并重建：

```bash
python3 scripts/update_c_state.py \
  --store data/c_state/my-agent \
  --verify-only
```

`--verify-only`只打开已有状态库、校验Revision哈希链并返回状态；它不会创建目录或文件，也不会重写`current.json`。需要从Revision重建投影时，应通过Python调用`verify_and_rebuild(rebuild_projection=True)`。

状态库由 `scripts/run_atom_runtime.py` 或 `scripts/run_local_usage.py` 现场写入，不把示例库检入版本库。

## 当前边界

CStateStore已经可以真实持久化和增量更新。同一Subject × Axis的不同观测由内容型cell ID区分；同一观测重复输入保持幂等。但只有`status=available`、同轴同尺度且带不确定度的C观测才能进入。A current 七轴已校准；默认shadow示例仍不写C。本地使用入口是`scripts/run_local_usage.py`。
