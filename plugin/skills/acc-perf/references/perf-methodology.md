# acc-perf 详规 · 性能验收方法论

> **定位 guard**：acc-perf 是 P2 规划的原子能力 skill，**尚未接入 live 流、不落盘、不算达标**（比值/裁决/仿真块唯一归 `perf_compare.py`；渲染归 `perf_sim_plot.py`，只画不判）。本文件只装方法论；**不复制阈值**（`target_ratio` / 小 shape 阈值取自 spec）。载重前逐个 Read 引用页并**按 tier**。

## 0. canon 依据（按 tier）

| 页 | tier | 承载 |
|---|---|---|
| `decisions/0006-performance-timing-scope.md`（ADR 0006） | **proposed·未 settle** | timing_scope 必填枚举、双边同口径、默认 kernel-only、msprof op |
| `decisions/0007-deterministic-validator.md`（ADR 0007） | **canonical** | 比值/达标只从确定性脚本出 |
| `architecture/task3-state-machine.md` | **canonical** | 结论态：`PASSED / FAILED_PERFORMANCE / BLOCKED_WAIT_GPU_BENCHMARK`（边角 `PASSED_WITH_RISK` / `BLOCKED_INCOMPARABLE_TIMING_SCOPE`） |
| `architecture/perf-baseline-by-reference-source.md` | **proposed·未 settle** | 基线随任务书参考源（TBE/GPU/同 op），非固定 gpu_external；载重前必核 |
| `architecture/generated-harness-responsibilities.md` | **canonical** | 性能测量栈职责#4：双边同 scope、warmup/iters/median 自实现、比值归 validator |

## 1. timing_scope 枚举与不可比路由

- **枚举**：`kernel_only`（默认公平口径）/ `device_e2e_no_h2d_d2h` / `host_e2e_with_h2d_d2h`。
- **双边同 scope 铁律**：NPU 与基线必须同 scope；一边 kernel-only、一边含 H2D/D2H 墙钟 → **系统性偏差** → `BLOCKED_INCOMPARABLE_TIMING_SCOPE`，不出结论。
- **NPU 采集**：`msprof op` 采 kernel-only Task Duration。融合类走 device_e2e 时由 `--application` + 自解析 op_summary + 裁到算子窗口实现（`generated_harness` 职责#4）。
- **torch 小算子链基线**：us = **Σ(链各小算子 kernel-only us)**（非整条链 e2e 墙钟，避免把 Python 调度/launch/sync 塞进基线）；kernel-only 会低估融合省下的 launch/调度开销，报告可附 e2e 参考、判定用 matched kernel-only。

## 2. 基线谱系（`spec.perf.baseline` 驱动，不写死单一源）

| 改动类型 | 基线 | 备注 |
|---|---|---|
| 重写类（参考内置 TBE） | TBE，任务书给定比例（无劣化 / ≥ 给定百分比） | 当前接入算子 spec 均 `baseline=tbe` |
| 移植类（对标 GPU 库 cuSPARSE/cuBLAS…） | GPU（A100，任务书给定比例区间） | GPU 标杆数据由外部 Task 3 给 |
| 加 dtype 类 | 同 op 其他 dtype 不劣化 | 新 dtype 不劣于同宽既有 dtype |
| 可选单标杆 | 昇腾小算子拼接（torch 链） | 与生态精度标准单标杆同源 |

> ⚠ canon 张力（待 review 裁）：`acceptance-contract-evidence-chain` 的 `perf_baseline_source` 当前默认 `gpu_external`，与「基线随任务书参考源」有张力；真机三算子任务书原文均写 TBE、GPU 非必需 → 建议 review 裁定这批社区任务 GPU 对比层为可选。本 skill 只陈述、**不单方改 canonical**。

## 3. 小 shape 例外门（T6 已实现，数据驱动）

- **触发**：`小shape` tag 的性能用例；阈值 `when_us_below` / `abs_gap_us_within` 取自 `spec.perf.small_shape_exception`（对象；legacy 字符串正则兜底），**零硬编码**。
- **判定**：`max(NPU,基线) < when_us_below` 且 `|NPU−基线| ≤ abs_gap_us_within` → **达标保持 False** + `exception` 标 + `exception_detail`。
- **仿真图**：`report['simulation']` 由 `perf_compare` **独家产**（唯一事实源）；`perf_sim_plot.py` 只据此渲染 SVG（阈值线/容差带数据驱动 + XML escape），**不二次推断**。`gate_task3` 强制「有图 + 例外行↔simulation 交叉一致 + SVG sha256 + 路径钉死」才放行；删图/篡改/对不上 → FAILED。
- **映射**：status=exception → 编排层 `PASSED_WITH_RISK` + 挂人工 CP，**绝不偷偷把达标置 True**。

## 4. Task3 blocked 路由（GPU consumer，T8 已实现）

- `BLOCKED_WAIT_GPU_BENCHMARK`：任务书要 GPU 基线但缺外部 GPU 标杆 → 正规挂起、**非 fail**、`baseline=None` 不崩。触发 = `--gpu-baseline` 或 `spec.perf.baseline∈{gpu,gpu_external}`。
- `BLOCKED_INCOMPARABLE_TIMING_SCOPE`：双边 scope 不一致 → 不可比、不出结论。
- **GPU external 对比层**：`gpu_baseline.py` + `gpu_baseline_contract.json`（15 字段）解析外部标杆，按 case_id + 完整输入签名交叉核对、集合恰好覆盖；真数据由外部给，NPU↔GPU 对比在拿到数据前走 wait，**绝不显 PASS**。

## 5. ⚠ 能力边界与待办（诚实）

- **可判·已实现**：ratio+达标、scope 一致性门、小 shape 例外（T6）、GPU 标杆 consumer（T8）。
- **未有真值**：真机小 shape 真值、真 GPU 标杆数据——mock/占位仅证管路接通，**非真验收数字**。
- **红线**：本 skill 只描述口径；比值/达标/blocked 态归 `perf_compare.py`。`exception`（PASSED_WITH_RISK）**不当 pass**、blocked **不当 fail**。
