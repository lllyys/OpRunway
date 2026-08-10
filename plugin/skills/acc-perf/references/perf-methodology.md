# acc-perf 详规 · 性能验收方法论

> **定位 guard**：acc-perf 是 P2 规划的原子能力 skill，**尚未接入 live 流、不落盘、不算达标**（比值/裁决/仿真块唯一归 `perf_compare.py`；渲染归 `perf_sim_plot.py`，只画不判）。本文件只装方法论；**不复制阈值**（`target_ratio` / 小 shape 阈值取自 spec）。

## 0. 当前实施依据

| 来源 | 承载 |
|---|---|---|
| 仓根 `AGENTS.md` §6.1、§6.3 | workflow 只做 NPU 性能取证；不运行或消费 GPU 数据；未实测条款结构化挂账 |
| `acc-common/perf_compare.py` | timing_scope、比值、例外和性能裁决的确定性实现 |
| `acc-common/perf_evidence_contract.py` | 性能证据字段与 scope 契约 |
| `acc-common/run_workflow.py`、`validate_acceptance_state.py` | 正式工件生成与证据完整性门 |

## 1. timing_scope 枚举与不可比路由

- **枚举**：`kernel_only`（默认公平口径）/ `device_e2e_no_h2d_d2h` / `host_e2e_with_h2d_d2h`。
- **双边同 scope 铁律**：NPU 与基线必须同 scope；一边 kernel-only、一边含 H2D/D2H 墙钟 → **系统性偏差** → `BLOCKED_INCOMPARABLE_TIMING_SCOPE`，不出结论。
- **NPU 采集**：`msprof op` 采 kernel-only Task Duration。融合类走 device_e2e 时由 `--application` + 自解析 op_summary + 裁到算子窗口实现（`generated_harness` 职责#4）。
- **torch 小算子链基线**：us = **Σ(链各小算子 kernel-only us)**（非整条链 e2e 墙钟，避免把 Python 调度/launch/sync 塞进基线）；kernel-only 会低估融合省下的 launch/调度开销，报告可附 e2e 参考、判定用 matched kernel-only。

## 2. 基线谱系（`spec.perf.baseline` 驱动，不写死单一源）

| 改动类型 | 基线 | 备注 |
|---|---|---|
| 重写类（参考内置 TBE） | TBE，任务书给定比例（无劣化 / ≥ 给定百分比） | 当前接入 aclnn 类算子 isclose/sign/equal/neg 均 `baseline=tbe`；catlass matmul 属对标类(synthetic demo、未定基线)——「均」仅限这批重写类 |
| 移植类（任务书以 GPU 库作比较口径） | NPU msprof 实测；GPU 比值条款记 `UNVALIDATED` | workflow 不连接、运行或消费 GPU 数据，不等待 GPU 标杆 |
| 加 dtype 类 | 同 op 其他 dtype 不劣化 | 新 dtype 不劣于同宽既有 dtype |
| ACLNN / 小算子拼接 | 按任务书事实或用户确认选 `aclnn_builtin` 或 `torch_npu` | 直接 ACLNN 才用前者；已确认等价于 Torch 接口则用后者，不重复证明 |

任务书中的比值、绝对门限或吞吐条款未实测时，必须进入 `task_pr_gaps` 标 `UNVALIDATED`；有 NPU 绝对耗时不等于这些条款达标。

## 3. 小 shape 例外门（T6 已实现，数据驱动）

- **通用 case 来源与大小分类**：只要存在性能维，就必须用
  `perf.case_source="precision_cases"` 声明性能 case 取自精度 caseset，并用
  本轮确定性精度裁决的 pass case_id 再筛一次；精度 fail/needs_review 不能进入性能比较。
  精度已通过意味着 DUT 在相同输入上的功能/精度执行已成立，若性能采集阶段再执行失败，应按
  DUT 回归或 harness/collector 异常解耦；它不意味着性能 ratio 必然达标，也不替代 baseline 证据门。
  同时用
  `perf.shape_classification={metric:"sum_input_bytes",small_max_bytes,hardware}` 按全部输入物理载荷之和
  标记“小shape/大shape”。A3 的 `small_max_bytes=262144`，边界计入小 shape。该分类只服务分组统计；
  没有任务书例外条款时，大小两类都正常测量和判定。
- **选择账本**：caseset 至少记录精度/性能总数、入选与排除的 `case_id`、按 dtype 入选数；
  验收门重算并核对，证明性能 case 的确取自精度 case。
- **补齐交叉覆盖**：需要补齐任务书接口/属性 × small/large 时，可通过
  `perf.case_selection.include_precision_tags` 选入匹配 tag 的既有精度 case；case_id、输入与 golden
  保持同一份，仍须先通过精度裁决。
- **固定报告视图**：按 `small`、`large`、`overall` 输出计划数、实测数、达标数、blocked 数、
  NPU/baseline 中位耗时和 speedup。任何声明了分类策略却缺少分类的 case 都不能静默跳过。
- **失败明细不丢行**：所有 ratio 未达标、blocked、exception、等待/缺失 baseline 的 case 都须在最终
  报告逐条记录 case_id、dtype、输入 shape、small/large、custom/baseline 行为与耗时、原因。
  汇总不能替代明细，也不能通过少算分母制造“全部通过”。
- **触发**：`小shape` tag 的性能用例；阈值 `when_us_below` / `abs_gap_us_within` 取自 `spec.perf.small_shape_exception`（对象；legacy 字符串正则兜底），**零硬编码**。
- **判定**：`max(NPU,基线) < when_us_below` 且 `|NPU−基线| ≤ abs_gap_us_within` → **达标保持 False** + `exception` 标 + `exception_detail`。
- **仿真图**：`report['simulation']` 由 `perf_compare` **独家产**（唯一事实源）；`perf_sim_plot.py` 只据此渲染 SVG（阈值线/容差带数据驱动 + XML escape），**不二次推断**。`gate_task3` 强制「有图 + 例外行↔simulation 交叉一致 + SVG sha256 + 路径钉死」才放行；删图/篡改/对不上 → FAILED。
- **映射（有门前置）**：status=exception 且 **`gate_task3` 过**（图齐备 + 例外行↔simulation 交叉一致 + SVG sha 钉死）→ 编排层 `PASSED_WITH_RISK` + 挂人工 CP，**绝不偷偷把达标置 True**；**门未过 → `BLOCKED(验收门未过)`、不 PASSED_WITH_RISK**（run_workflow 先判门、后判例外态）。

## 4. 性能 gap 与不可比路由

- `BLOCKED_INCOMPARABLE_TIMING_SCOPE`：双边 scope 不一致 → 不可比、不出结论。
- **GPU 比值条款**：不进入 workflow 输入或产物；未实测时写入 `task_pr_gaps` 标 `UNVALIDATED`，不得据 NPU 绝对耗时宣称已满足，也不得因缺 GPU 数据阻塞 NPU 侧执行。

## 5. ⚠ 能力边界与待办（诚实）

- **可判·已实现**：可执行 NPU 基线的 ratio+达标、scope 一致性门、小 shape 例外（T6）。
- **未有真值**：真机小 shape 数据——mock/占位仅证管路接通，**非真验收数字**。
- **红线**：本 skill 只描述口径；比值/达标/blocked 态归 `perf_compare.py`。`exception`（PASSED_WITH_RISK）**不当 pass**、blocked **不当 fail**。
