# OpRunway 设计评审（Codex 外部第二意见）

> 评审者：Codex CLI v0.140.0 · model **gpt-5.5** · read-only · 2026-06-30
> 评审对象：`doc/oprunway-design.md` + `CLAUDE.md` + canon 的 3 个 ADR + 4 个 architecture 页。
> 性质：**外部评审意见**，非 canon。认可并采纳的点，再经 `bureau:note → compile → review` 入 canon。

## 总体判断

设计方向整体成立，尤其把 Task1 用例集作为 Task2/Task3 的共享契约是对的。但当前最大隐患不是编排形态，而是**「验收口径尚未被契约化」**：golden、精度阈值、性能计时边界、GPU 标杆 schema、PR 与用例映射都还停在开放问题层面。若不先收敛，后续 workflow/agents/skills 会变成自动化外壳，结论仍不可复核。

## 1. 三段式流水线 + Task1 脊柱
- **严重**：Task1 用例集作脊柱成立，但 §3 schema 不足以支撑「验收结论」。缺 `case_origin`、`spec_clause_ref`、`pr_change_ref`、`applicability`、`oracle_source`、`tolerance_policy_id`、`timing_policy_id`。没有这些，无法回答「这个用例为什么存在、覆盖任务书哪条、是否覆盖 PR 改动、判据来自哪里」。
- **严重**：golden 来源需从「CPU float32」再分层。GEMM 可用 CPU float32，但随机数/非确定性/近似算法/特殊 dtype 量化/layout-stride/融合/溢出语义不一定。建议 oracle 策略枚举：`analytical_ref`/`cpu_ref`/`torch_ref`/`catlass_existing_ref`/`task_spec_expected`/`external_ref`，每个精度用例绑定一个 oracle。
- **重要**：PR↔用例映射被低估。输入是「任务书+PR」，但设计更像「任务书→用例」。Task1 应加 **PR 影响面分析**（新增/改 kernel、example、模板参数、dtype、shape family、性能路径、边界），再映射到新增/加权用例。否则 PR 只是元数据，证明不了验收覆盖了改动。
- **重要**：Task3 只做性能对比、不做 GPU 精度对齐是对的，但要写清：**GPU 是性能标杆，不是 Task2 的精度 oracle**，避免误用 GPU 数据当数值正确性依据。

## 2. ADR 0002（以 catlass + 任务书为准）
- **重要**：主判断正确，`ops-test` 的「跑没跑崩」只能当执行健康检查，不能替代数值/性能验收。
- **严重**：例外要补进 ADR。catlass 机制只适合 catlass GEMM；泛化到 ops-blas/ops-cv/tilelang 时 golden/构建/运行/性能入口都变。建议改写为：**「首仓 catlass 以 catlass 机制为仓适配器默认实现；正式验收以任务书 + 统一验收策略为上层规范，仓机制只是执行后端」**——别把 catlass 机制误升为所有仓的总规范。
- **次要**：`ops-test` 可保留为 Task2 前置 **smoke gate**（环境/编译/运行/明显崩溃），字段标 `smoke_only: true`，非验收证据。

## 3. 精度口径冲突 Q3
- **严重**：采用**三层口径而非三选一**：① 任务书显式验收目标优先（交付契约）；② 统一平台标准作默认/补缺（可借 cannbot 分类）；③ catlass 内置阈值只作仓内 smoke/回归，不作最终放行（除非任务书明确引用）。
- **严重**：区分「工程回归阈值」与「验收阈值」。catlass fp16≈`2^-8` 比 cannbot MERE `2^-10` 宽，直接用会放过平台不合格结果。报告同时输出 `catlass_compare_pass` / `acceptance_precision_pass` / `standard_profile_pass`，**最终结论只看 `acceptance_precision_pass`**。
- **重要**：任务书目标若**宽于**统一标准，不能自动放行——标注「任务书通过 / 平台标准风险」，由人工 CP 决策。
- **重要**：MERE/MARE/RMSE、NaN/Inf、零值附近绝对误差、小值阈值必须写进 schema，否则不同脚本对同一结果给不同结论。

## 4. 性能口径对齐 Q4
- **严重**：NPU↔GPU 必须默认同一计时边界。强制 `timing_scope`：`kernel_only`/`device_e2e_no_h2d_d2h`/`host_e2e_with_h2d_d2h`；缺失则 Task3 **不出结论、只出「不可比」诊断**。
- **严重**：GPU 标杆 schema 是 Task3 硬依赖，**不应拖到 M4**。至少先定最小字段：`case_id`/`device`/`dtype`/`shape`/`attrs`/`timing_scope`/`warmup`/`iters`/`sync_policy`/`statistic`/`unit`/`value`/`tool`/`clock/power mode`/`data_transfer_included`。
- **重要**：默认公平口径 = kernel-only。NPU 用 msTuner `task_duration(us)`，GPU 用 CUDA event/等价 kernel event；均 warmup 后多次迭代取 median/p50，保留 p90/min。端到端作补充，不作默认加速比。
- **重要**：warmup/迭代从「建议值」升级为 **policy**：warmup≥10、iters≥30 或方差收敛；每次计时前后同步；输入驻留 device；排除首次编译/缓存/初始化；记录频率/功耗/驱动/CANN/CUDA 版本。

## 5. catlass 接法歧义与仓适配器
- **严重**：已有 example 与 PR 新增 kernel/模板是**两类完全不同的接入路径**。当前 Task2 只覆盖 example + msTuner，覆盖不了「只有 kernel/模板、无调用壳」的 PR。仓适配器接口至少含：`discover`/`build`/`materialize_case`/`run_correctness`/`run_perf`/`parse_results`/`collect_artifacts`。
- **重要**：catlass 适配器支持三模式：`existing_example`（映射已有 example）/`new_example`（PR 自带 example，直接构建运行）/`generated_harness`（OpRunway 按用例生成调用壳）。**第三种风险最高**，必须有模板边界 + 人工确认。
- **重要**：适配器别只围绕 `m/n/k`。schema 保留 layout/transpose/stride/batch/epilogue/bias/scale/workspace/alignment/splitK/quant params 扩展位，否则泛化返工。

## 6. ADR 0003 发布形态
- **重要**：自维护插件仓 + skills external-sync 是合理选择。
- **重要**：**发布不应早于接口稳定**。M1–M3 先内部插件仓，不急于登记 external-sync；等 schema/状态文件/证据 JSON/最小 catlass 端到端跑通再同步，避免把未稳定契约暴露成公共入口。
- **次要**：skills 同步后避免「只有 skill 没有 workflow/agent 时用户误以为可独立完整运行」——每个 SKILL.md 标明是否可单独用、缺顶层插件时的功能边界。

## 7. ADR 0004 编排选型
- **重要**：对发布成品，cannbot 式编排取舍正确（比 Workflow 工具更适合发布/人工 CP/证据固化）。
- **严重**：「AI 编排 + JSON 证据」**必须把最终判定交给确定性校验器**——所有 PASS/FAIL 由 validator 依据 JSON schema + 原始日志 hash 计算，**agent 只生成解释和建议，不能直接宣告通过**。
- **重要**：Task3 缺 GPU 标杆时的「挂起」要定义**状态机**：`BLOCKED_WAIT_GPU_BENCHMARK`/`BLOCKED_INCOMPARABLE_TIMING_SCOPE`/`FAILED_PRECISION`/`FAILED_PERFORMANCE`/`PASSED_WITH_RISK`/`PASSED`。

## 8. 组件拆分粒度
- **重要**：1 workflow + 3 skill 主拆分合理。
- **严重**：新增共享 **`acceptance-schema` / `acc-common`** 组件（统一 JSON schema、状态校验器、case/result/report 数据模型），否则三个 skill 各自解释字段、脊柱漂移。
- **重要**：`acc-npu-run` 内部再拆「**仓适配器**」与「**验收判定器**」（执行 vs 依统一 policy 判定），否则泛化时复制逻辑。
- **次要**：子 agent 保持克制（解析/跑测/评测三个角色够了）。真正要拆的是确定性脚本和 schema，不是角色数量。

## 9. 最大风险与 Q1–Q9 排序
- **严重**：最大风险 = **「看似自动化，实则不可比、不可复核」**。Q3/Q4/Q5/Q6 不解决，报告里的通过/失败没有稳定含义。
- **重要**：第二大风险 = 首个真实任务书/PR 太晚介入。Task1 解析、case schema、PR 映射必须被真实样例「打穿」。
- **重要**：优先级 **Q3 > Q4 > Q5 > Q6 > Q1 > Q2 > Q8 > Q9 > Q7**（Q3 直接决定 PASS/FAIL 最先定；Q7 发布形态对验收正确性影响最小）。

## 最该先解决的 3 件事
1. 定稿统一 **case/result/gpu-benchmark/decision JSON schema**，尤其补齐精度 policy、性能 timing policy、PR 覆盖映射、证据来源。
2. 明确**精度与性能判定规则**：任务书、平台标准、catlass 内置阈值各自的位置，以及 kernel-only 默认对比口径。
3. 用一个真实 catlass 任务书 + PR 做 **M1 摸底**，验证 Task1 能否生成可执行用例、Task2 能否映射到 example / new_example / generated_harness。

> **一句话建议**：先别急着做插件外壳，先把「同一个 `case_id` 从任务书、PR、NPU 跑测、GPU 标杆到最终判定」这条**证据链**钉死。
