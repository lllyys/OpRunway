# OpRunway 设计与流水线总览

> NPU 算子「验收（acceptance）」工作区。本文是设计基线，随分析推进持续更新。
> 当前阶段：**调研收口 + 经 Codex 评审收敛（v3）**。组件尚未实现。
> 评审来源见 `doc/oprunway-codex-review.md`（Codex gpt-5.5，2026-06-30）。

---

## 0. 第一原则：契约先行（contract-first）

**最大隐患不是编排形态，而是「验收口径未被契约化」。** 若 golden、精度阈值、性能计时边界、GPU 标杆 schema、PR↔用例映射停在「开放问题」，后续 workflow/agents/skills 就是「看似自动化、实则不可比、不可复核」的外壳。

**核心承诺：先把一条 `case_id` 从「任务书条款 → PR 改动 → NPU 跑测 → GPU 标杆 → 最终判定」串到底（证据链），再做插件外壳。** 详见 §3。

---

## 1. 定位与目标

把「**算子任务书 + PR → 验收结论**」做成可复用、可泛化的自动化流水线，覆盖**功能/精度/性能**三类验收，产出 **NPU↔GPU 性能对比报告**。先以 `cann/catlass` 一个算子跑通端到端，再泛化。形态是一组 Claude Code **workflow + agents + skills**，自维护成插件仓（见 §10）。承诺：零硬编码、产物落 CWD、全程中文、副作用先确认、数据不捏造。

---

## 2. 三段式验收流水线

由 **Task 1 产出的「测试用例集」** 作为脊柱串联，Task 2/3 消费同一份用例。

```
  PR + 任务书 →[Task1 用例生成(ST)]→ ★测试用例集(机读契约) →┬→[Task2 NPU 跑测]→ NPU 精度对比 + 性能
                                                          └→[GPU 标杆侧(外部)]→ GPU 标杆数据
                                              [Task3] 逐 case_id 对齐 NPU vs GPU → 性能对比报告 + 验收结论
```

| Task | 输入 | 处理 | 输出 | 落地 |
|---|---|---|---|---|
| **1 用例生成(ST)** | PR + 任务书(md) | 解析任务书 → 算子契约 + **PR 影响面分析** → 生成功能/精度/性能用例（每条带 origin/oracle/policy） | 测试用例集（机读契约+人读） | `reports/<repo>/<op>/<pr>/cases/` |
| **2 NPU 跑测** | Task1 用例 + 含 PR 的 catlass | 仓适配器 build → 按用例跑：精度 vs oracle、性能采集 → **validator 判定** | NPU 精度对比 + 性能 + 判定证据 | `reports/<repo>/<op>/<pr>/npu/` |
| **3 性能对比** | Task1 用例 + 外部 GPU 标杆 + Task2 NPU 性能 | 校验 timing_scope 一致 → 逐 case_id 对齐对比 | NPU↔GPU 对比报告 + 状态机结论 | `reports/<repo>/<op>/<pr>/perf-compare/` |

**Task 1 新增「PR 影响面分析」**：识别 PR 改了什么（新增/改 kernel、example、模板参数、dtype、shape family、性能路径、边界处理），映射到新增或加权用例——否则 PR 只是元数据，证明不了「验收覆盖了改动」。

**Task 3 边界**：GPU 是**性能标杆**，**不是 Task 2 的精度 oracle**；不拿 GPU 数据当数值正确性依据。

---

## 3. 共享数据契约（脊柱 + 证据链，最优先定稿）

一条 `case_id` 贯穿全程。契约由 `acc-common` 组件统一定义（§9），三个 skill 都吃它，避免字段漂移。

```yaml
# cases/<op>.cases.yaml （骨架，字段随首份真实任务书校准，勿空想镀金）
op: <算子名>
repo: catlass
pr: {ref: <pr 链接/编号>, change_summary: ...}
spec_ref: <任务书来源>
policies:                              # 复用的判据策略（id 化，被 case 引用）
  precision: {<id>: {profile: spec|standard|catlass, mere: ..., mare: ..., rmse: ..., nan_inf: ..., small_val_atol: ...}}
  timing:    {<id>: {scope: kernel_only|device_e2e_no_h2d_d2h|host_e2e_with_h2d_d2h, warmup: 10, iters: 30, statistic: median, sync: ...}}
cases:
  - id: <case_id>                      # ★ 全程主键
    kind: functional|precision|performance
    case_origin: spec_clause|pr_change|boundary|regression   # 这条为什么存在
    spec_clause_ref: <任务书第几条>
    pr_change_ref: <对应 PR 哪处改动，或 none>
    applicability: {arch: 3510|2201, dtype: fp16, ...}
    dtype: fp16
    shape: {m: 256, n: 512, k: 1024}   # 保留扩展位：layout/transpose/stride/batch/epilogue/bias/scale/workspace/alignment/splitK/quant
    attrs: {...}
    oracle_source: analytical_ref|cpu_ref|torch_ref|catlass_existing_ref|task_spec_expected|external_ref
    tolerance_policy_id: <-> policies.precision   # 仅 precision
    timing_policy_id: <-> policies.timing         # 仅 performance
    expect: {...}
```

**为什么这些字段是「严重」级**：没有 `case_origin/spec_clause_ref/pr_change_ref/oracle_source/tolerance_policy_id/timing_policy_id`，就回答不了「这条用例为什么存在、覆盖任务书哪条、是否覆盖 PR 改动、判据/oracle 从哪来」——验收结论就不可复核。

**golden/oracle 分层**：不要一刀切「CPU float32」。GEMM 可用 `cpu_ref` float32，但随机数/非确定性/近似算法/特殊量化/layout-stride/融合/溢出语义需别的 oracle。每个精度用例**必须绑定一个 `oracle_source`**。

---

## 4. catlass 落地细节（Task 2 事实依据，已调研）

> 仓在 `repos/catlass`（master）。catlass 机制是**首仓的「执行后端」，不是所有仓的总规范**（见 §6 ADR 0002 重构）。

- **build/run**：`scripts/build.sh <example> [-DCATLASS_ARCH=3510]`（950 必带 arch，默认 2201=A2/A3）→ `./output/bin/<example> m n k deviceId` → `Compare success./failed.`。复杂算子三段式 `gen_data.py → kernel → compare.py`。统一入口 `tests/test_example.py`；另有 `tests/optest/`（torch_npu+torch_catlass）。
- **精度 golden = CPU host float32**：`examples/common/golden/`（`matmul.hpp`/`compare_data.hpp`，可源码复用）。内置阈值 fp16 `1/256`~`1/128`、bf16 `1/128`~`1/64`、int 全等；另有三方 mare/mere/rmse。**注意：catlass 内置阈值仅作 smoke/回归，不是验收放行线（见 §6）。**
- **性能 = `msprof op`**（验收默认，profile 交付的 kernel → Task Duration，**kernel-only，不含 H2D/D2H**）。catlass 的 **msTuner（`tools/tuner/`）是「搜最优 tiling」的调优工具、不用于验收**（验收测 PR 交付的 kernel、不替它搜配置）。备选 optest 端到端、`aclrtEvent`。

---

## 5. 仓适配器（解决「catlass 接法歧义」Q2）

被验收算子有**三种接入模式**，适配器必须都覆盖（光有 example+msTuner 覆盖不了「只有 kernel/模板、无调用壳」的 PR）：

| 模式 | 含义 | 风险 |
|---|---|---|
| `existing_example` | 映射到仓里已有 example | 低 |
| `new_example` | PR 自带 example，直接构建运行 | 中 |
| `generated_harness` | OpRunway 按用例**生成调用壳/配置** | **高，必须模板边界 + 人工确认** |

**统一仓适配器接口**（泛化到 ops-blas/ops-cv/tilelang 时换实现、不换接口）：
`discover` · `build` · `materialize_case` · `run_correctness` · `run_perf` · `parse_results` · `collect_artifacts`。

**catlass `generated_harness` 有现成配方**（借自 cannbot `catlass-op-generator`，详见 `doc/oprunway-cannbot-catlass-reuse.md`）：host 调用壳（ACL 初始化 + Tiling + `<<<>>>` 启动 + 结果搬回 + verify）+ op_kernel（catlass `using` 链 → **直接调 PR 交付的 kernel**）+ CMake 注入（`-I catlass/include` + `-DCATLASS_ARCH`）+ 确定性 `verify_cmake_config.py` 构建门禁 + `run.sh` 流水。与 cannbot 差异：我们**包住 PR 现成 kernel**，它是从 DESIGN 现写 kernel。**接入 AscendOpTest**（它是 aclnn 导向、catlass 裸 kernel 不能直测）需 generated_harness 补桥：路线 A 封成 aclnn 自定义算子（终交付）/ 路线 B 自造 exe 遵守框架协议（快验证）。详见 `doc/oprunway-ascendoptest-probe.md`。generated_harness 的 **4 项通用职责**（bin-IO shim / layout 字节契约 / 数据注入 / 性能测量栈）见 canon `generated_harness responsibilities`——这是「怎么让任意用例在某仓 kernel × 某框架上真跑」的跨仓/跨框架抽象，bridge 是其 catlass×AscendOpTest 实例。

---

## 6. 精度验收策略（解决 Q3：三层口径，不是三选一）

正式判定顺序：

1. **任务书显式验收目标优先**（交付契约）；
2. **统一平台标准作默认/补缺**（借 cannbot `ops-precision-standard` 的 MERE/MARE 按 dtype 分类）；
3. **catlass 内置阈值只作仓内 smoke/回归**，**不作最终放行**（除非任务书明确引用）。

> **（M1 实体）** 当前任务书的「平台层」= **AscendOpTest 默认阈值**（FP16 `tolerance=1e-3` + `error_rate=1e-3`/允许 0.1% 坏点；判据 `|golden|≥1` 相对、`<1` 绝对）。**精度直接复用其 `compare.py`**，我们只供 `expect_func`（numpy 融合 golden）。⚠ FP16 融合经 Matmul 累加未必稳过 1e-3，可能需 fp32 中间 golden——M3 实测。详见 `doc/oprunway-ascendoptest-probe.md`。

**报告同时输出三个 pass，最终结论只看 acceptance**：

| flag | 来源 | 作用 |
|---|---|---|
| `catlass_compare_pass` | catlass 内置 compare | 仓内冒烟/回归兼容 |
| `standard_profile_pass` | 平台统一标准（cannbot 分类） | 平台底线 |
| **`acceptance_precision_pass`** | 任务书目标（缺则回落平台标准） | **放行依据** |

**关键规则**：任务书目标若**宽于**平台底线，**不自动放行**——报告标「任务书通过 / 平台标准风险」，由人工 CP 决策。MERE/MARE/RMSE、NaN/Inf、零值附近绝对误差、小值阈值都写进 §3 的 `precision` policy。

> **ADR 0002 重构**：catlass 自身机制是「首仓的仓适配器默认实现/执行后端」；**正式验收以任务书 + 统一验收策略为上层规范**，仓机制只是后端，别误升为所有仓的总规范。姊妹项目 `ops-test` 的「跑没跑崩」可保留为 Task 2 前置 **smoke gate**（字段标 `smoke_only: true`），非验收证据。

---

## 7. 性能验收与口径对齐（解决 Q4 / 提前化解 Q5）

- **`timing_scope` 必填枚举**：`kernel_only` / `device_e2e_no_h2d_d2h` / `host_e2e_with_h2d_d2h`。**NPU 与 GPU 必须同 scope**；缺失或不一致 → **Task 3 不出结论，只出「不可比」诊断**。
- **默认公平口径 = kernel-only**：NPU 用 **`msprof op`**（Task Duration；msTuner 是调优工具、不用于验收），GPU 用 CUDA event/等价 kernel event；均 warmup 后多次迭代取 **median/p50**，保留 p90/min。端到端作补充报告，不作默认加速比。
- **1.2× 加速比（AscendOpTest 部分复用）**：AscendOpTest 只给 kernel-only 采集、不算 speedup、无 torch 通路 → 任务书的「≥ torch 未融合链 1.2×」基线由 **generated_harness 自测、比值由 validator 算**。⚠ **同 timing_scope 对同 timing_scope**——torch 链是 host/e2e，别拿它 e2e 比融合算子 kernel-only；两侧要么都 kernel-only 求和、要么都 device-e2e。分母口径用户确认中。**AscendOpTest 能采 e2e**（内建 `msprof --application`，解析归我们）→ **device_e2e 可行且更贴合「整体性能」**，融合类算子倾向之。详见 `doc/oprunway-ascendoptest-probe.md`。
- **warmup/迭代 = policy（非建议值）**：warmup≥10、iters≥30 或方差收敛；每次计时前后同步；输入驻留 device；排除首次编译/缓存/初始化；记录频率/功耗模式/驱动/CANN/CUDA 版本。
- **GPU 标杆 schema 是 Task 3 硬依赖，现在就定「我们需要对方给什么」的最小契约**（外部交付时对齐，不空想定死）：
  `case_id` · `device` · `dtype` · `shape` · `attrs` · `timing_scope` · `warmup` · `iters` · `sync_policy` · `statistic` · `unit` · `value` · `tool` · `clock/power mode` · `data_transfer_included`。

---

## 8. 验收判定与状态机（确定性兜底）

- **判定归确定性校验器**：所有 PASS/FAIL 由 **validator 依据 §3 JSON schema + 原始日志 hash** 计算；**agent 只生成解释和建议，不能直接宣告通过**（AI 当发现器、脚本兜底）。这是 ADR 0004「cannbot 式编排」的硬约束。
- **Task 3 / 整体结论用状态机**（不只是流程描述）：
  `PASSED` · `PASSED_WITH_RISK`（如任务书宽于平台底线）· `FAILED_PRECISION` · `FAILED_PERFORMANCE` · `BLOCKED_WAIT_GPU_BENCHMARK`（缺标杆）· `BLOCKED_INCOMPARABLE_TIMING_SCOPE`（口径不一致）。

---

## 9. 组件规划

| 组件 | 类型 | 职责 |
|---|---|---|
| `op-acceptance` | **skill/command（编排剧本）** | 串 Task 1→2→3；CP 用 `AskUserQuestion` 卡人；按算子/用例派子 agent fan-out；缺 GPU 标杆→进 BLOCKED 状态。**不用 Workflow 工具当骨架** |
| **`acc-common`** | **共享组件/skill** | ★ 统一 JSON schema + 状态校验器(validator) + case/result/report 数据模型——脊柱的单一定义，防三 skill 字段漂移 |
| `acc-casegen` | skill | Task 1：任务书+PR(影响面分析) → 用例集 |
| `acc-npu-run` | skill | Task 2：内部再拆「**仓适配器**(执行)」+「**验收判定器**(依统一 policy 判定)」 |
| `acc-perf-compare` | skill | Task 3：用例 + GPU 标杆 + NPU 性能 → 对比报告 + 状态机结论 |
| 任务书解析 / 跑测 / 独立评测 | subagent | 三个角色够用，**别过早多拆**；真正要拆的是确定性脚本和 schema，不是角色数量 |

**编排 = 混合架构（ADR 0004，经官方文档核实 2026-06-30）**：用户入口走 skill/command，并行 fan-out 用子 agent，人工 CP 用 `AskUserQuestion`，判定归确定性 validator（§8）。**Claude Code 的 Workflow 工具不作产品骨架**——因为它 ① 不能随 plugin 分发（plugin 组件无 workflows，`plugin.json` 无该字段）、② 要 opt-in（v2.1.154+、Pro 默认关、可被禁）、③ 不支持中途人工 CP（官方「No mid-run user input」）、④ skill 不能直接调它。Workflow 工具仅作「可用则用、不可用降级为子 agent」的**内部并行加速器**。编排结构借鉴 cannbot `ops-registry-invoke`（CP3 精度/CP4 性能 ≈ 我们的 Task 2）；方法论借 `ops-precision-standard`/`ops-profiling`，只借不依赖；catlass golden 头文件可源码复用。

**两条通用能力（跨算子 / 跨仓，是真正要建的核心，非某算子专用）**：
- **`acc-casegen` = 「原语→case 规则库」**（跨算子）：算子=原语组合，每类原语（归约/matmul-分组/有界激活/elementwise）挂 case 模式 + 展开逻辑 + 元规则。见 canon `Primitive-to-case rule library`。
- **`repo-adapter/harness` = generated_harness 4 职责**（跨仓+跨框架）：bin-IO shim / layout 字节契约 / 数据注入 / 性能测量栈。见 canon `generated_harness responsibilities`。
> 洞察：**「生成用例」是易的一半（规则库），「让用例在某仓 kernel × 某框架上真跑」才是难点与通用价值**（落在 harness/repo-adapter）。手写的算子 case set 只是首个夹具，非产品。

---

## 10. 发布形态（ADR 0003，微调）

自维护 OpRunway 插件仓（skills+agents+workflows，`/plugin install` 分发）+ skills 部分 external-sync 进 awesome-ascend-skills（cannbot 同款；plugin 原生可带 agents，「只收 skills」是 awesome 自身策略）。**补充：发布不早于接口稳定——M1–M3 先内部仓，等 schema/状态文件/证据 JSON/最小 catlass 端到端跑通再登记 external-sync**，避免把未稳定契约暴露成公共入口。每个 SKILL.md 标明能否单独用、缺顶层插件时的功能边界。

---

## 11. 涉及代码仓（gitcode.com）

`cann/catlass`（重点）、`asc-devkit`、`ops-sparse`、`ops-blas`、`ops-cv`、`catccos`、`shmem`、`oam-tools`、`amct`、`hixl`、`cann-recipes-infer`(`ops/tilelang`)。均已 clone 到 `repos/`。泛化靠 §5 的仓适配器接口（换实现不换接口）。

---

## 12. 路线图（按验收正确性重排）

1. **M0 init** ✅ · **M0.5 调研收口** ✅ · **M0.7 Codex 评审收敛** ✅（本 v3）。
2. **M1 契约 + 摸底**（最优先）：拿真实任务书(md) + 一个 catlass PR；据此**定稿 §3 数据契约 + §6 精度策略 + §7 性能口径**，并验证 Task1 能否生成可执行用例、Task2 能否映射到 example/new_example/generated_harness。
3. **M2 Task 1 打通** → **M3 Task 2 打通**（catlass golden + msTuner + validator）→ **M4 Task 3 打通**（接一份 GPU 标杆）。
4. **M5 泛化 + 发布**：仓适配器扩到其它仓；接口稳定后自维护仓 + external 同步。

---

## 13. 开放问题（优先级 = Codex 排序）

- **Q3 精度口径** — ✅ **已定（§6 三层 + 三 pass）**，待首份任务书校准阈值。
- **Q4 性能口径** — ✅ **已定（§7 timing_scope 必填 + 默认 kernel-only）**，待与 GPU 标杆对齐。
- **Q5 GPU 标杆 schema** — 外部后给；**最小字段契约已先定（§7）**，交付时对齐。
- **Q6 验收判定规则** — 框架已定（§8 状态机 + validator 兜底）；阈值/豁免随任务书细化。
- **Q1 任务书格式** — md，待真实样例 → 定 Task1 解析与字段映射。
- **Q2 catlass 入口** — ✅ **已定（§5 三模式 + 仓适配器接口）**；`generated_harness` 风险最高需人工闸。
- **Q8 远程 NPU 环境** — 用哪台机、catlass 在哪 build、是否进 Docker；影响排期，**不反过来定义验收口径**。
- **Q9 多仓泛化** — 先保留仓适配器边界，catlass 端到端跑通后再扩。
- **Q7 发布形态** — ✅ 倾向已定（§10），对验收正确性影响最小，最后做。
