# OpRunway workflow 设计 v1

据全部任务书 + PR（`oprunway-spec-pr-analysis.md`）设计，遵循**约束 A（跨运行时可移植）**。这是提案，未落代码。

## 0. 设计原则

**三层**：`Layer 0 数据契约(JSON) — Layer 1 确定性脚本(核心脑子) — Layer 2 per-tool 编排薄壳`。

**铁律**：
- stage 之间**只传 JSON / 数据文件**，不传 agent 记忆。
- **Layer 0 + 1 工具中立、100% 可移植**（是资产）；**Layer 2 每家运行时一套薄壳**（可替换）。
- 唯一绕不开的 NL 步（解析任务书）其**输出是中立 `spec.json`**，故照样可换壳。
- 换 Claude Code → Codex → Antigravity，只换 Layer 2，核心不动。

---

## 1. Layer 0 — 数据契约（脊柱，5 个 JSON）

字段照语料定；每个都是 stage 间的唯一接口。

### `spec.json`（任务书解析产物 · Task 1 输入）
```
op, repo, 硬件[]
reference: { type: tbe | gpu_lib | existing_op, ref, path? }   # 参考实现三类
change:    { kind: new_op | add_dtype | semantic | bugfix, dtypes_added[] }
pr_impact: { def, kernel, tiling, tests }                       # 加dtype 4处影响面(辅助 gaps 判定/用例聚焦)
params:    [ { name, io, dtype[], format[], shape_rank, noncontiguous, constraints } ]
generalize: bool                                                # 泛化必须?
verify_mode: numerical | behavioral | exact                     # 数值/行为(Sleep)/精确(bool/整数二进制)
precision:  { oracle: ascendoptest | mere_mare | torch | np_isclose | scipy | std_exact | atk_double | none,
              threshold_source, dtype_thresholds? }             # 精度口径(多态,含 np.isclose/scipy/整数精确/ATK双标杆/无)
perf:       { baseline: tbe | gpu_a100 | same_op_dtype | npu_torch_unfused_chain | none,
              target, small_shape_exception }                   # 性能基线(随任务书/参考源/改动目标)
                                                                # T6(待散文门)：small_shape_exception 为对象
                                                                #   {text, when_us_below, abs_gap_us_within, requires}
                                                                #   (机读阈值供 perf_compare；legacy 字符串向后兼容)
deliverables[]                                                  # 交付件清单
task_pr_gaps[]                                                  # 任务书↔PR 落差(待确认)
```

### `caseset.json`（用例集 · 脊柱，Task 2/3 都消费）
```
cases: [ {
  id, dims: [功能|精度|性能],                                    # 一 case 可覆盖多维
  inputs: [ { shape, dtype, format, gen: {dist, range, seed} } ],
  attrs, 
  expected: { golden_source, threshold },                       # golden 来源逐算子(见决策5)
  tags: [常规|边界|泛化|大shape|精度敏感|转置|广播|...]
} ]
```

### `evidence.json`（跑测证据 · runner/repo_adapter 产出，纯采集不判定）
```
per_case: { status, 
  precision: { metric, value, threshold, golden_path, out_path },
  perf: { scope: kernel_only|e2e, us, e2e_us? },   # 判定用 kernel-only；e2e_us 可选、仅供参考
  build_log, run_log, raw_paths }
```

### `verdict.json`（裁决 · validator 产出，确定性）
```
per_case: { 功能: pass|fail, 精度: pass|fail|uncertain, 性能: pass|fail|na, 判据, evidence_ref }
overall: { verdict: pass|fail|needs_review, uncertain[], 按维度小结 }
```

### `baseline.json`（性能基线数据 · Task 3 外部给或自测）
```
source: gpu_a100 | tbe | same_op_dtype | npu_torch_unfused_chain | ...
scope:  kernel_only                          # 与 target 同口径（见「性能对比口径」）
per_case: [ { case_id, us, env } ]           # npu_torch_unfused_chain 时 us = Σ(链各小算子 kernel-only us)
```

### `perf_report.json`（Task 3 · NPU↔基线对比）
```
per_case: { npu_us, baseline: {source, us}, ratio, 达标 }
summary
```

---

## 2. Layer 1 — 确定性脚本（核心脑子，工具中立）

| 脚本 | 输入 → 输出 | 职责 |
|---|---|---|
| `gen_cases` | spec.json → caseset.json | 按规格 × 泛化生成用例（dtype×shape×属性组合、边界、精度敏感、加dtype 聚焦新 dtype 路径）。用 rule-catalog（primitive→case 模式） |
| `repo_adapter`（接口，3 模式实现） | caseset + 仓 → evidence.json | `materialize_case · build · run_correctness · run_perf · parse_results`。吃**工程范式三分** + 各仓不同 build/run/golden/perf |
| `validator` | spec + caseset + evidence.json → verdict.json | 确定性裁决（ADR 0007）。按 `verify_mode` × `precision.oracle` × `perf.baseline` 分支判定。UNCERTAIN 不阻塞流水线出产物，但 overall 记 needs_review、**不可直接 PASS** |
| `perf_compare` | evidence.json + baseline.json → perf_report.json | NPU 性能 vs 基线（按 `spec.perf.baseline`：GPU/TBE/同op dtype/torch 小算子拼接）算比值、判达标 |

> **repo_adapter 三模式 ≠ 工程范式三分（不一一对应）**：标准 GE、experimental 库式、纯头文件库这三种范式**都优先复用 `existing_example` / `new_example`**（PR/仓自带可运行工程，直接 build/run——如 SPMV/Trsm 自带 run.sh、dynamicMap 自带 Catch2）；**只有「无现成可运行壳」或 catlass/aclnn 桥接场景才用 `generated_harness`**（自造调用壳，见 `catlass-to-aclnn-bridge`）。社区任务这批基本 `new_example`。**每仓一份小 adapter 配置**（build 命令 / golden 方法 / perf 方法），接口不变、实现按仓换。

> **性能对比口径（ADR 0006 · matched timing scope）**：对比双方 `scope`（ADR 0006 称 timing_scope）必须一致，**默认 kernel-only**（msprof/msTuner，不含 H2D/D2H）。**target 与 baseline 都按 kernel-only 量**——`npu_torch_unfused_chain` 基线的 `us` = **拼接链各小算子 kernel-only us 之和**、**非整条链 e2e 墙钟**（否则把 Python 调度、launch/sync、队列等待塞进基线、失真）。⚠ 取舍：kernel-only 会**低估融合带来的 launch/调度/同步收益**；但中间 device 访存的减少通常**仍会反映在各 kernel 的 duration 差值里**。→ 报告可**附一条 e2e 供参考、判定仍用 matched kernel-only**。

---

## 3. Layer 2 — per-tool 编排薄壳（可替换）

**Claude Code 一套**（首个落地）：
- `op-acceptance`（顶层编排 command/skill）：串起 Task 1→2→3，只搬 JSON、调 Layer 1 脚本。
- `task-doc-parse`（agent，**唯一必需的 NL 步**，其余 NL 皆可选且不入核心）：任务书 md → `spec.json`（含 task_pr_gaps）。
- `acc-casegen`（skill）：薄壳，驱动 `gen_cases`（泛化覆盖规则沉在 `gen_cases`/rule-catalog 里）；NL 复核（若做）只产 `case_review.json` 审阅意见、**不改 `caseset.json`**（保核心脚本可移植）。
- `acc-npu-run`（skill）：薄壳，在 NPU 上驱动 `repo_adapter`，产 `evidence.json`。
- `acc-perf-compare`（skill）：薄壳，驱动 `perf_compare`。
- `eval`（agent）：独立复核 validator 标 UNCERTAIN 的 case + 证据质量（不改裁决，只提审阅意见）。

**Codex / Antigravity**：各自一套等价薄壳，**驱动同一 Layer 0/1**。差异仅在「怎么调脚本、怎么 spawn 子任务」，核心零改。

---

## 4. 三段流水线映射

```
Task 1  任务书.md ─[task-doc-parse agent]→ spec.json ─[gen_cases]→ caseset.json
                                                        (acc-casegen 复核泛化)
Task 2  caseset.json ─[repo_adapter 在 NPU]→ evidence.json ─[validator]→ verdict.json
                        (acc-npu-run 驱动)
Task 3  evidence.json(NPU perf) + baseline.json(按 spec.perf.baseline，同 scope=kernel_only) ─[perf_compare]→ perf_report.json
                        (acc-perf-compare 驱动)
```

---

## 5. 语料发现如何塑造设计（关键决策）

1. **任务书权威** → `spec.json` 从任务书生成、`task_pr_gaps` 显式记落差；validator 按 spec 判、不按 PR。gen_cases 以 spec 为准。
2. **证据自产** → repo_adapter **必跑并产 evidence**，从不信 PR 自带测试；golden 由 OpRunway 按 `precision.oracle` 产。
3. **工程范式三分** → repo_adapter 一接口、按仓换实现 + 每仓小配置（build/run/golden/perf）；模式（existing/new/generated）按「有无现成可运行壳」选、非按范式。
4. **契约多样性** → `spec.json` 带 `verify_mode` + `precision.oracle` + `perf.baseline`；validator 与 gen_cases 据此分支（Sleep 走 behavioral、bool/整数走 exact、移植类走 gpu_a100 基线…）。
5. **整型语义逐算子** → `caseset` 的 `golden_source` 指向该算子的**参考实现**（TBE/torch/scipy/std），不设全局假设（AddList 饱和 vs MulList wraparound 各自对齐）。
6. **加 dtype 影响面 4 处** → 对 `change.kind=add_dtype`，gen_cases **聚焦新 dtype 路径 + 在其上泛化**；task-doc-parse 可据 4 处（def/kernel/tiling/测试）估 PR 影响面、辅助 gaps 判定。

---

## 6. 与 canon 的关系 & 开放问题

- **印证/不改**：`component-breakdown`（1 编排 + acc-common + skills + agents）、`repo-adapter` 三模式、ADR 0007（裁决唯一来自 validator）、`acceptance-pipeline` 三段式——本设计是它们的**落地细化**，A 只是把「中立核心 vs 薄壳」缝划清、把 skill 的脑子下沉到脚本。
- **待 compile 进 canon**（本会话新得）：性能基线随参考源变（非单一 gpu_external）、工程范式三分、契约多样性四维、验证模式枚举。
- **开放问题**：
  1. `spec.json` 的 `precision.oracle` 具体阈值表（AscendOpTest 默认值 / MERE·MARE 分级）要坐实到数字。
  2. gen_cases 的 rule-catalog（primitive→case）覆盖哪些算子类别、泛化策略怎么定量。
  3. GPU 标杆 schema（Task 3 对比口径）外部未给——已用 `baseline.json` 契约占位，待外部对齐字段。
  4. `generated_harness` 只 catlass 需要，社区任务这批基本 `new_example`——是否先只做 new_example、catlass 桥另线。
  5. 首个真机验证目标：宜选 merged 干净的（Sign/IsClose/SPMV），不选交付存疑的 upsample。
