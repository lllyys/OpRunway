# acc-precision 详规 · 精度验收方法论

> **定位 guard**：acc-precision 是 P2 规划的原子能力 skill，**尚未接入 live 流、不落盘、不判 pass/fail**（判定唯一归 `precision_policy.py` + `validator.py`，ADR 0007 canonical）。本文件只装方法论；**不复制阈值数字**（阈值 SSOT 在 `precision_policy.py` + canon 页）。载重前逐个 Read 下列引用页并**按 tier**（canonical 当事实；proposed 存疑、先核）。

## 0. canon 依据（按 tier）

| 页 | tier | 承载 |
|---|---|---|
| `decisions/0005-precision-three-layer.md`（ADR 0005） | **canonical** | 三层口径、非三选一；放行只看 `acceptance_precision_pass`；宽于底线→`PASSED_WITH_RISK`+人工 CP |
| `decisions/0007-deterministic-validator.md`（ADR 0007） | **canonical** | 判定只从确定性 validator 出；agent 只发现/解释、不宣告通过 |
| `architecture/ascendoptest-precision-thresholds.md` | **canonical** | 平台层「实体」：AscendOpTest 默认阈值 + 掩码语义（rel/abs 混合、坏点占比、inf/NaN） |
| `architecture/ecosystem-precision-standard.md` | **proposed·未 settle** | 平台层候选：MERE/MARE 逐 dtype Th；**载重前必核、勿当事实** |
| `architecture/primitive-to-case-rule-library.md` | **canonical** | golden 元规则（中间精度、compare 分支归属、数据同源） |
| `architecture/task3-state-machine.md` | **canonical** | `PASSED / FAILED_PRECISION / PASSED_WITH_RISK` 等结论态 |

## 1. 三层口径决策树（放行只看 acceptance）

```
                任务书有显式精度验收目标?
                   ├─ 有 → acceptance 口径 = 任务书目标（交付契约，最优先）
                   └─ 无 → acceptance 口径 = 平台层标准（下）
  平台层标准由 spec.precision.standard + verify_mode + oracle 选（precision_policy.select_standard）:
     verify_mode=exact                      → exact（bool/逐位）
     verify_mode=behavioral                 → behavioral（行为等价）
     numerical + oracle∈{ascendoptest,缺}   → ascendoptest_default
     numerical + oracle∈{mere_mare,atk_double} → ecosystem_mere_mare（proposed）
  三层 pass 同出: catlass_compare_pass / standard_profile_pass / acceptance_precision_pass
  放行 = acceptance_precision_pass；acceptance 过 & standard(平台底线) 不过 → 该 case risk、overall passed_with_risk
```

- **catlass 内置阈值**（仓内 smoke）**永不作最终放行**——只作回归冒烟。
- **spec 权威**：validator 据 `spec.precision.standard` + case 的 `compare_dtype` 复算 canonical policy，要求 **spec / caseset.expected / evidence.precision 三处结构化 policy 全等**；仅比 caseset↔evidence 不够（两侧同步放宽即可绕过）。

## 2. oracle 分层（谁当真值）

1. **高精度参考当 golden**：CPU/numpy 高精度，或昇腾小算子拼接（生态标准「单标杆」）。
2. **golden 中间精度**：默认 fp32 中间→降输出 dtype（对齐 Ascend Cube fp32 累加）；**敏感归约/大 K/catastrophic cancellation** 按算子实际累加调高（可能 fp64）。归一化 `1/sqrt` 若用硬件 Rsqrt 近似，golden 要么建模该近似、要么 policy 按 ULP 放宽——否则两端误报（`Primitive-to-case rule library` §③）。
3. **compare 分支归属**：每条 case 预估落相对(`|golden|≥1`)还是绝对(`<1`)分支；关键路径确保≥1 条落相对分支（否则 abs 阈值「假容易」）。
4. **数据同源**：同一份输入 bin 同源喂 kernel 与 golden（`generated_harness` 职责#3），特殊值分布可控可复现。

## 3. 逐 dtype 标准（阈值 SSOT 在 precision_policy.py，此处只指口径）

- **ascendoptest_default**（canonical 实体）：逐 dtype `tolerance`+`error_rate`；`|golden|≥1` 用相对误差、`<1` 用绝对误差（**共用同一 tolerance**）；`inf → finfo.max`、`NaN==NaN` 视为通过；仅当**坏点数 > numel × error_rate** 才整体 fail。数值常量逐 dtype 快照在 `precision_policy.py`（15 dtype，`accuracy_config.py` 内容指纹已记 `_verify.json`）。
- **ecosystem_mere_mare**（**proposed·未 settle**）：MERE = 平均相对误差、MARE = 最大相对误差（**MERE=平均、MARE=最大，勿对调**）；通过 = `MERE < Th 且 MARE < 10 × Th`，Th 逐 dtype（2⁻ᵏ，SSOT 在 `precision_policy.py`）。**全常量标 `NOT_SETTLED`**。
- **exact**：bool / 逐位精确，`exact_mismatch ≤ 0` 才过。
- **behavioral**：行为等价（输出满足语义约束，非逐元素数值）。

## 4. ⚠ 能力边界与待办（诚实，不 overclaim）

据代码核实（`validator.py` / `precision_policy.py` HEAD 现状）：

- **可判·settled**：`ascendoptest_default`、`exact`（`judge_ascendoptest` / `judge_exact` 已实现并 settled）。
- **已实现但未 settle**：`ecosystem_mere_mare`——`judge_mere_mare` 已能算 MERE/MARE 指标，但全常量 `NOT_SETTLED`（源页 proposed）；**单标杆不过 → `uncertain`/needs_review、不自动 fail**；`standard` 或 `acceptance` 任一 `uncertain` → 至少 `needs_review`（不被 acceptance pass 吞）。
- **未实现·out-of-scope**：**ATK 双标杆 fallback**（`cv_fused_double_benchmark`）—— precision_policy 未实现，本 skill **不声称可据双标杆判定**。
- **待办**（若要把 MERE/MARE 升为可自动 fail 的放行判据，或加 ATK 双标杆）→ 属 `precision_policy.py` / `validator.py` 的**独立后续 todo**（带自己的代码 + test 门），**不在本 P2 库交付范围**。

**红线**：本 skill 只描述「用哪套口径、为什么」；任何 pass/fail 数字与判定归 `precision_policy.py` + `validator.py`。`needs_review` / `uncertain` **不得当 pass**。
