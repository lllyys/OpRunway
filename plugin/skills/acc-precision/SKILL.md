---
name: acc-precision
description: OpRunway 验收精度维的方法论薄壳——oracle 分层（谁当真值）+ dtype 分层度量（哪套标准、什么口径），供理解「一个算子的精度该怎么判、为什么」。P2 规划的原子能力 skill：尚未接入 op-acceptance live 流、勿自动触发、不落盘、不算 pass/fail（判定唯一归确定性 validator.py + precision_policy.py，ADR 0007）。需要读懂或扩展精度判定口径、排查精度裁决时阅读。
---

# acc-precision — 精度验收方法论（原子能力 skill）

**定位（诚实，P2 边界）**：本 skill 是 P2 规划的**原子能力 skill**，只装「精度该怎么判、为什么」的**方法论指针**。

- **不判定、不落盘**：pass/fail 的实算唯一归确定性脚本链——`${OPRUNWAY_PLUGIN_ROOT}/acc-common/precision_policy.py`（三标准 SSOT + 误差分布复算 `compute_metrics`）+ `${OPRUNWAY_PLUGIN_ROOT}/acc-common/validator.py`（读 evidence 出 `verdict.json` 的纯算术 judge）。`${OPRUNWAY_PLUGIN_ROOT}` = 本插件根中立变量，Claude 下等价 `${CLAUDE_PLUGIN_ROOT}`（harness 自动设），**Codex 等非 Claude 运行时须显式 `export OPRUNWAY_PLUGIN_ROOT=<插件根绝对路径>`**；落到可执行命令里时写自兜底形式 `${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}`，两种运行时都活。**本 skill 不复制阈值数字、不复刻 judge、不宣告通过**（ADR 0007 canonical）。
- **未登记进 AGENTS.md（诚实先例）**：本 skill **不列入** `plugin/AGENTS.md` 的 `skills:` 清单——登记 = 声称已接入 live 流，而本 skill 未接入（live 精度判定仍走 validator / precision_policy）。分发 / 发现由 `init.sh` 扇出保证（symlink `plugin/skills/` 下**全部** skill 目录、**不依赖 AGENTS.md 登记**）。待 P1 / 后续真接线再登记；本 skill 勿被自动触发。
- **阈值零复制**：AscendOpTest 默认表（逐 dtype `tolerance/error_rate`）、生态 MERE/MARE 的 `Th`（逐 dtype 2⁻ᵏ）等**判定阈值常量的唯一真源在 `precision_policy.py` + canon 页**；本 skill **不写死任何 pass/fail 判定阈值**，只描述「用哪套标准、按什么口径」。

## 方法论一 · oracle 分层（谁当真值 golden）

- **真值 = 更高精度参考**：CPU/numpy 高精度参考，或昇腾小算子拼接（生态标准的「单标杆」也认这条）。golden 由 `expect_func` / numpy 参考产，输出 dtype 须与算子输出一致。
- **中间精度按算子实际累加**：golden 默认 fp32 中间→降输出 dtype（对齐 Ascend Cube fp32 累加），**但按算子实际累加/任务书参考精度调整**（敏感归约/大 K/抵消可能需更高中间精度）——见 canon `Primitive-to-case rule library`（canonical）§③元规则。
- **单标杆 vs 双标杆**：生态标准单标杆不满足时，本可退 ATK 双标杆（`cv_fused_double_benchmark`）；⚠ **ATK 双标杆 fallback 当前 precision_policy 未实现、out-of-scope**（见 §能力边界）。
- **任务书写 GPU 真值口径 → 一律解析为同族 CPU**（AGENTS.md §5.11，用户 2026-08-05 定的全局解析规则）：
  实现是 `precision_policy.resolve_gpu_oracle_to_cpu(method_kind) → (resolved_kind, record|None)`，
  按**方法族**查受控映射表（数据不是代码分支），非 GPU 族原样返回、`record=None`，对既有口径零影响；
  **同族没有 CPU 对应 → 仍 fail-closed，不硬凑**，解析后的 `method_kind` 还得落在
  `RUNNABLE_METHOD_KINDS` 内（当前 `{torch_cpu, numpy_cpu, opencv_cpu}`；`gpu_lib` **仍不在**里面——
  别把解析层读成「GPU 现在能跑了」）。
  ⚠ **与 §5.10 是两件事，别照搬**：5.10（性能）是**取消比较**，条款按「未验收」进 `task_pr_gaps`；
  5.11（精度真值）是**解析口径**，条款**已被满足**、**不产生** gap。
  ⚠ **解析必须留痕**：报告据 `record`（`rule` / `declared_in_taskdoc` / `resolved_to` / `basis`）写成
  「原文写 GPU、按 §5.11 解析为同族 CPU」，**不得**直接写成「任务书要求 CPU」把这一步抹掉（5.8）。
  ⚠ **阈值不随口径搬家**：任务书阈值若按 NPU↔GPU 误差预算给出，套到 CPU↔NPU 未必成立
  （同一库的 CPU 与 GPU 实现并非逐位一致）。阈值来源与真值口径不同源时报告须标明、由人确认。

## 方法论二 · dtype 分层度量（哪套标准）

精度验收是**三层口径、非三选一**（ADR 0005 canonical）；报告同出 `catlass_compare_pass` / `standard_profile_pass` / `acceptance_precision_pass`，**放行只看 acceptance**：

1. **任务书显式验收目标**（交付契约，最优先）；
2. **平台层标准**（默认/补缺）——两套并列候选，由 `spec.precision.standard` + oracle 选（`select_standard`）：
   - `ascendoptest_default`：AscendOpTest 默认阈值实体（逐 dtype `tolerance`+`error_rate`、`|golden|≥1` 用相对/`<1` 用绝对、坏点占比 fail、inf→finfo.max、NaN==NaN 通过）——canon `AscendOpTest precision thresholds`（**canonical**）；
   - `ecosystem_mere_mare`：生态《算子开源精度标准》MERE(平均相对误差)<Th 且 MARE(最大相对误差)<10×Th，Th 逐 dtype——canon `Ecosystem precision standard MERE MARE`（**proposed·未 settle**）；
   - `exact`：bool/逐位精确（threshold=0）；`behavioral`：行为等价。
3. **catlass 内置阈值**只作仓内 smoke/回归，**不作最终放行**。

任务书目标**宽于**平台底线（acceptance 过、standard 平台底线不过）→ 不自动放行，overall `passed_with_risk` + 人工 CP（ADR 0005 / `Task 3 acceptance state machine` canonical）。

## 方法论三 · 真值缺席的两种形态（都不是 fail，也都不是 pass）

「算不出结论」与「算出了坏结论」是两件事，`validator` 把它们分开落终态——**报告不得把任一种说成通过**：

| verdict | 含义 | 谁来解 |
|---|---|---|
| `blocked_golden_unauthorized` | 真值**来路不明**：golden 授权链（引文出自任务书快照）核不实 | 人把授权补齐 |
| `blocked_golden_unavailable` | **压根没有真值**：参考实现算不出来（见证：通道数超 OpenCV `CV_CN_MAX`） | 换参考实现，或由人裁定这批 case 不在验收范围 |

`golden_unavailable` 的处置口径（脚本侧已定，本 skill 只描述、不重判）：这类 case 的身份**仍进 caseset**、
**允许无 golden 文件**、仍进 evidence，**其余 case 继续跑**；validator 把它们**移出 `fails`**，
单列 `overall.golden_unavailable` / `counts.golden_unavailable`。
⚠ **真实失败优先**——与真实精度失败并存时 verdict 仍是 `fail`（查得出的缺陷比查不动的空白更该被看见），
而这份名单照样原样进产物。
⚠ **报告红线**：「164/169 通过」**不等于**精度通过。据这份名单写成「169 条里 5 条无从判定」，
不得拿能判的那部分替整份用例集背书（AGENTS.md 5.8）。

## ⚠ 脚本能力边界（诚实标注 HEAD 现状——不声称脚本做不到的判定）

现状（T5 后，比早期 plan 快照更全，据代码核实）：

| 标准 | precision_policy/validator 现状 | 诚实口径 |
|---|---|---|
| `ascendoptest_default` | **已实现·settled**（`judge_ascendoptest`，15 dtype 表逐字快照 + 掩码语义） | 可判 |
| `exact` | **已实现·settled**（`judge_exact`，mismatch≤0 才过） | 可判 |
| `ecosystem_mere_mare`（MERE/MARE） | **已实现但全常量 `NOT_SETTLED`（proposed）**——`judge_mere_mare` 存在，但**单标杆不过 → `uncertain`/needs_review、不自动 fail**；**ATK 双标杆 fallback 未实现** | 「已能算 MERE/MARE 指标、但作为放行判据未 settle」；**不声称已可据 MERE/MARE 自动 fail** |

**口径以 spec 为权威**：validator 据 `spec.precision.standard` + case dtype 复算 canonical policy，要求 spec / caseset.expected / evidence.precision **三处结构化 policy 全等**（防两侧同步放宽绕过）。standard 或 acceptance 任一 `uncertain` → 至少 `needs_review`（不被 acceptance pass 吞）。

**详规见** `references/precision-methodology.md`（三层决策树 · oracle 分层 · 逐 dtype 标准 · 边界与待办，按 canon tier 引）。
