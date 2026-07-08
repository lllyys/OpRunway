---
title: Machine-verifiable acceptance gate
updated: 2026-07-08
status: verified
---

# Machine-verifiable acceptance gate

OpRunway 把「验证-才-信」从纪律变成**代码硬门**：`plugin/acc-common/validate_acceptance_state.py` 是三级（`--stage task1|task2|task3`）**完整性门**，只读**落盘的** `evidence.json` 等结构化机读证据（不认 md/LOG 文字），独立复核：

- **防跑子集报 100%**：evidence/perf 必须覆盖 caseset 全部用例、id 一一对应（`caseset id == evidence id`）。
- **防放宽阈值**：evidence 的 `precision.threshold` 必须与 caseset（spec 权威）一致；precision 缺失即判 FAILED。
- **防混 e2e**：非 blocked 的性能行 `scope` 必须 `kernel_only`（缺 scope 也判 FAILED）。
- **抗坏输入**：坏/缺字段的产物 → 累计成 error 判 `STATUS: FAILED`，绝不崩溃、绝不静默放过。

门接进 `run_workflow.py` 做**硬 blocker**：任一 stage `FAILED` → 总体 `BLOCKED` + `main` 非零退出（CI 可当硬失败）+ 落 `acceptance.json`（门控后的验收裁决，区别于 raw `verdict.json`）。判定脑子仍在 validator（见 [[ADR 0007 — Verdicts come from a deterministic validator]]）；门只管「证据可信 + 完整」、pass/fail 由 validator/perf_compare 判（见 [[Gate checks evidence integrity not verdict]]）。28 单测覆盖子集/放宽/混 e2e/合法 fail 不挡/抗坏输入/解析器/退出码。是 cannbot `validate_workflow_state.py` 的 OpRunway 版（借架构、不借 ACLNN 专属 schema，见 [[cannbot orchestration and cross-CLI]]）。经 codex 9 维代码门审 12 项全修。

**Verified.** `plugin/acc-common/validate_acceptance_state.py`（三级门 + 抗坏输入）、接入 `plugin/acc-common/run_workflow.py`（`import validate_acceptance_state as gate` + `gate._GATES` + `BLOCKED` + `sys.exit`）、`test_validate_acceptance_state.py` 28 单测 OK — 2026-07-08。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-08 续：P0 机器门落地）
