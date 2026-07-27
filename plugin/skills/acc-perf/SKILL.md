---
name: acc-perf
description: OpRunway 验收性能维的方法论薄壳——msprof kernel-only 采集 + timing_scope 必填同口径 + 基线按任务书参考源（TBE/GPU）+ 小 shape 例外，供理解「一个算子的性能该怎么比、为什么」。P2 规划的原子能力 skill：尚未接入 op-acceptance live 流、勿自动触发、不落盘、不算达标（比值/裁决唯一归确定性 perf_compare.py，ADR 0007 canonical；timing_scope 口径见 ADR 0006·proposed）。需要读懂或扩展性能对比口径、排查性能裁决时阅读。
---

# acc-perf — 性能验收方法论（原子能力 skill）

**定位（诚实，P2 边界）**：本 skill 是 P2 规划的**原子能力 skill**，只装「性能该怎么比、为什么」的**方法论指针**。

- **不算达标、不落盘**：ratio 与达标判定唯一归 `${OPRUNWAY_PLUGIN_ROOT}/acc-common/perf_compare.py`（`report['simulation']` 亦只此处产），渲染归 `${OPRUNWAY_PLUGIN_ROOT}/acc-common/perf_sim_plot.py`（只画不判）。`${OPRUNWAY_PLUGIN_ROOT}` = 本插件根中立变量，Claude 下等价 `${CLAUDE_PLUGIN_ROOT}`（harness 自动设），**Codex 等非 Claude 运行时须显式 `export OPRUNWAY_PLUGIN_ROOT=<插件根绝对路径>`**；落到可执行命令里时写自兜底形式 `${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}`，两种运行时都活。**本 skill 不复制阈值、不复刻比值、不宣告达标**（ADR 0007 canonical）。
- **未登记进 AGENTS.md（诚实先例）**：本 skill **不列入** `plugin/AGENTS.md` 的 `skills:` 清单——登记 = 声称已接入 live 流，而本 skill 未接入（live 性能对比仍走 perf_compare）。分发 / 发现由 `init.sh` 扇出保证（symlink `plugin/skills/` 下**全部** skill 目录、**不依赖 AGENTS.md 登记**）。待 P1 / 后续真接线再登记；本 skill 勿被自动触发。
- **阈值零复制**：`target_ratio` 取自 `spec.perf.target_ratio`；小 shape 例外阈值取自 `spec.perf.small_shape_exception`（`when_us_below` / `abs_gap_us_within`），**均不写死进本 skill**。

## 方法论一 · 计时口径（timing_scope 必填、双边同口径）

- **NPU 侧默认 `msprof op` 采 kernel-only Task Duration**（不含 H2D/D2H）；catlass 的 msTuner 是搜 tiling 的调优工具、**不用于验收**（canon `catlass acceptance mechanics`）。
- **`timing_scope` 必填枚举**：`kernel_only` / `device_e2e_no_h2d_d2h` / `host_e2e_with_h2d_d2h`。**NPU 与基线必须同 scope**；缺失/不一致 → 不出结论、只出「不可比」诊断 `BLOCKED_INCOMPARABLE_TIMING_SCOPE`（timing_scope 口径出 ADR 0006·**proposed·未 settle**；诊断态归 `Task 3 acceptance state machine`·**canonical**——两页 tier 不同，勿混）。
- **稳态**：warmup / iters / 取 median(p50)、保留 p90/min 属 policy（warmup≥门槛、迭代到方差收敛、排除首次编译/缓存）。

## 方法论二 · 基线按任务书参考源（不写死单一 GPU）

基线来源**由任务书按参考源/改动目标定**（`spec.perf.baseline` 驱动；canon `Performance baseline follows the reference source`·**proposed·未 settle**，载重前需核）：

- **重写类（参考内置 TBE）** → 基线 = TBE（`无劣化` / `≥ 任务书给定比例`；当前接入的 aclnn 类算子 isclose/sign/equal/neg 均 `perf.baseline=tbe`，catlass matmul 属对标类、为 synthetic demo 未定基线——**「均」仅限这批重写类算子，勿外推为全局**）；
- **移植类（对标 GPU 库）** → 基线 = GPU（如 A100，任务书给定比例区间）；
- **加 dtype 类** → 同 op 其他 dtype 不劣化；
- **任务书点名既有 ACLNN / 小算子拼接实现** → 先确认任务书实际指向的可执行对象。确实要求直接
  ACLNN 接口时用 `aclnn_builtin`；若任务书事实或用户明确确认“小算子拼接等价于 Torch 对应接口”，
  则用 `torch_npu` 执行该 Torch API。不能只凭名字猜等价，也不重复证明已确认事实。

**最短证据链规则（用户 2026-07-26 明确裁定）**：任务书已经给出一个可直接执行、可同口径计时的
性能对照物时，就直接测它；“直接”是指任务书实际定义的对照语义，不是拘泥于文字里出现的最低层 API 名。
任务书含混时询问并把用户确认写入 spec，不自动增加“等价性证明”作为常规验收步骤。
基线产物须记录实际库、版本文件指纹/sha256 和两段式符号定义方，证明“直接调的是谁”，而不是证明
另一层包装“可能等价”。

**通用性能 case 规则**：性能 case 必须从同一份精度 caseset 选择，不能另造一套输入；
`perf.case_source="precision_cases"` 与 `perf.shape_classification` 在存在性能维时必填。大小 shape 按目标
硬件 UB 单次承载边界、以全部输入物理载荷之和分类；A3 的边界为 256 KiB（含边界为小 shape）。
分类只用于分组报告，不免测、不改变 `target_ratio`，也不恢复 `trivial-met`。
caseset 还必须记录精度/性能总数、入选与排除 case_id、按 dtype 入选数，形成可机审选择账本。
性能报告固定输出 `small`、`large`、`overall` 的计划数、实测数、达标数、blocked 数、
NPU/baseline 中位耗时与 speedup；声明了分类策略却缺少分类时，证据门必须失败。

**ratio = baseline_us / npu_us**（>1 表示 NPU 更快）；**达标 = ratio ≥ `spec.perf.target_ratio`**（由 perf_compare 算）。

## 方法论三 · 小 shape 例外（数据驱动、绝不偷偷置达标 True）

任务书常带「小 shape 例外条款」（如 <N us 差 M us 需仿真图证明）。**这已在 `perf_compare.py`（T6）实现**：`小shape` tag 的性能用例，若 `max(NPU,基线) < when_us_below` 且 `|NPU−基线| ≤ abs_gap_us_within` → **达标保持 False** + 打 `exception` 标 + 记 `exception_detail`；`report['simulation']` 由 perf_compare 独家产（唯一事实源），`perf_sim_plot.py` 只渲染 SVG。status=exception **且先过 `gate_task3`**（simulation 图齐备 + 例外行↔图交叉一致 + SVG sha 钉死）→ 编排层才映射 `PASSED_WITH_RISK`（挂人工核仿真图，**绝不偷偷把达标置 True**）；**门未过 → `BLOCKED(验收门未过)`、非 PASSED_WITH_RISK**（run_workflow 先判门再判例外态）。

## ⚠ 脚本能力边界（诚实标注 HEAD 现状）

据代码核实（`perf_compare.py` / `perf_sim_plot.py` HEAD 现状，比早期 plan 快照更全）：

| 能力 | perf_compare 现状 | 诚实口径 |
|---|---|---|
| ratio + 达标（ratio≥target_ratio） | **已实现** | 可判（数字归脚本） |
| scope 一致性门 | **已实现**（不一致→`blocked_incomparable_timing_scope`） | 可判 |
| **小 shape 例外** | **已实现（T6）**——保持达标 False + `exception` 标 + `simulation` 块 | 可判「例外」态；**过 `gate_task3` 后**映射 `PASSED_WITH_RISK`（门未过→`BLOCKED(验收门未过)`；非 pass、非静默） |
| **GPU 标杆 consumer** | **已实现（T8）**——`expect_source∈{gpu,gpu_external}` 且缺基线 → `blocked_wait_gpu_benchmark`（正规挂起、非 fail） | 可消费外部 GPU 标杆；真数据由外部 Task 3 给 |
| 真机小 shape 真值 / 真 GPU 数据 | 未有 | mock/占位仅证管路；**真值待真机 + 外部标杆** |

**详规见** `references/perf-methodology.md`（timing_scope 枚举 · 基线谱系 · 小 shape 例外门 · Task3 blocked 路由，按 canon tier 引）。
