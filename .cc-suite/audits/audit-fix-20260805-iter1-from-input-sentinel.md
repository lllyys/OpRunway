# Audit Findings

**Run**: audit-fix 20260805 iter1 | **Scope**: commit `200ef6d` 的 diff（`precision_policy.py` / `aclnn_runtime/aclnn_driver.py` / `test_precision_policy.py`）| **Audit type**: full
**Model**: gpt-5.6-sol | **Effort**: high | **Audit thread**: `019fd213-7079-7770-99b1-99c08d350038` | **Verify thread**: `019fd21a-5a22-7cc1-8544-d7f6c5453747`
**Status values**: open | fixed | not-fixed | partial | regressed | skipped

| # | File | Line | Severity | Dimension | Finding | Suggested fix | Status | Round |
|---|------|------|----------|-----------|---------|---------------|--------|-------|
| 1 | plugin/acc-common/precision_policy.py | 244 | Critical | 3 · 正确性 | 新共用函数把「`in_dt ∈ allowed` → 返回 `in_dt`」引入了**多输出路径**（原本没有），放宽判定：`values.dtype=["float16","float32"]` 且 in=float16 时旧实现拒绝、新实现猜成 float16 —— 允许集本身没表达「随输入」，属把证据不足静默升级为可裁决 | 多输出保持原规则，或用明确策略参数区分 | fixed | 1 |
| 2 | plugin/acc-common/precision_policy.py | 242 | Critical | 2 · 判据可绕过 | 哨兵无条件优先导致混合集合 fail-open：`["<from_input>","bool"]` + in=float32 直接返回 float32，而 spec 同时允许固定 bool —— 旧单输出路径会拒绝该形状 | 哨兵与具体项分别解析成候选，并集唯一才接受 | fixed | 1 |
| 3 | plugin/acc-common/test_precision_policy.py | 1298 | High | 7 · 测试 | 5 条测试只锁住原始 bug，未覆盖新引入的两个行为；`test_single_and_multi_output_paths_agree` 只把**单输出** spec 喂给两个 API，并非真实多输出形状 | 补真实 values+indices 反例、多具体 dtype 歧义拒绝、混合集合的拒绝/唯一解析边界 | fixed | 2 |

## 处置

- **1 / 2**：`resolve_out_dtype_from_allowed` 增加**必填关键字** `allow_input_membership`——
  单输出传 `True`（`sign`/`neg` 的 spec 把 `out.dtype` 写成 `["float32","float16"]`，靠它挑 in_dt），
  多输出传 `False`（`median.valuesOut` 显式写 `<from_input>`，历来严格口径）。
  两条路径的差异从「各写一份、悄悄分叉」变成「一份实现 + 调用方显式声明」。
  哨兵改为「解析成 in_dt 后与具体项求并，并集唯一才返回」，混合矛盾声明 fail-closed。
- **3**：补到 9 条测试，含真实 `values+indices` 形状、多输出 membership 不得放宽、
  混合集合在 in 不匹配时两条路径都拒绝 / 匹配时唯一解析（单、多输出各一例）。
- verify 第 2 轮把 #3 从 partial 补齐（缺的是 `["<from_input>","bool"]` + in=bool 的正例
  与多输出侧的唯一解析）。

## 回归证据（a3 容器，Python 3.12.13）

```
main 基线（git archive 干净树）： 5 failed, 1678 passed, 10 skipped
本轮改动后：                     5 failed, 1687 passed, 10 skipped
```

同样 5 个失败项、且在**未改动的基线上同样失败**（逐项复跑确认），与本轮无关：

```
test_gen_cases_case_profile.py::CaseProfilePlanMetaTest::test_meta_reports_declared_profile  (SUBFAILED profile='torch_parity')
test_gen_cases_dtype_attr.py::EffectiveStandardTest::test_bf16_needs_exact_equal_else_spec
test_gen_cases_dtype_attr.py::MockGateExpandedTest::test_defect_on_int_precision_fail_gate_not_blocked
test_gen_cases_dtype_attr.py::MockGateExpandedTest::test_expanded_one_to_one_and_gates_pass
test_precision_policy.py::FailFastAndRoutingTest::test_unsupported_dtype_fail_fast
```

另：`aclnn_driver.py` 的 PEP 701 改动用**真 Python 3.11.15**（a5 `oprunway_gb` 容器）
对全 `plugin/` 106 个 `.py` 做过只读语法门，全过。
