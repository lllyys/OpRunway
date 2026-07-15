---
title: Machine-verifiable acceptance gate
updated: 2026-07-15
status: verified
---

# Machine-verifiable acceptance gate

OpRunway 把「验证-才-信」从纪律变成**代码硬门**：`plugin/acc-common/validate_acceptance_state.py` 是三级（`--stage task1|task2|task3`）**完整性门**，只读**落盘的** `evidence.json` 等结构化机读证据（不认 md/LOG 文字），独立复核：

- **防跑子集报 100%**：evidence/perf 必须覆盖 caseset 全部用例、id 一一对应（`caseset id == evidence id`）。
- **防放宽阈值**：evidence 的 `precision.threshold` 必须与 caseset（spec 权威）一致；precision 缺失即判 FAILED。
- **防混 e2e**：非 blocked 的性能行 `scope` 必须 `kernel_only`（缺 scope 也判 FAILED）。
- **抗坏输入**：坏/缺字段的产物 → 累计成 error 判 `STATUS: FAILED`，绝不崩溃、绝不静默放过。

门接进 `run_workflow.py` 做**硬 blocker**：任一 stage `FAILED` → 总体 `BLOCKED` + `main` 非零退出（CI 可当硬失败）+ 落 `acceptance.json`（门控后的验收裁决，区别于 raw `verdict.json`）。判定脑子仍在 validator（见 [[ADR 0007 — Verdicts come from a deterministic validator]]）；门只管「证据可信 + 完整」、pass/fail 由 validator/perf_compare 判（见 [[Gate checks evidence integrity not verdict]]）。90 单测覆盖子集/放宽/混 e2e/合法 fail 不挡/抗坏输入/解析器/退出码/dtype 覆盖/oracle_source。是 cannbot `validate_workflow_state.py` 的 OpRunway 版（借架构、不借 ACLNN 专属 schema，见 [[cannbot orchestration and cross-CLI]]）。经 codex 9 维代码门审 12 项全修。

**续加两门（2026-07-15，Q7/Q9）**：① **dtype 覆盖门**——`dtype_required` 须被**真实用例**的 dtype 覆盖，未覆盖且无 `dtype_deferred` 挂账 → BLOCKED；覆盖率**用真实 cases 的 `inputs[0].dtype` 算、不信 caseset 自报 `dtype_tested`**（防「跑子集报全」），并交叉核 `dtype_tested` vs 实际。② **oracle_source 一致门**——`evidence.oracle_source` 须 ∈ 六枚举且 == 映射(`golden_source`)，防伪造 evidence 篡改 golden 来源。两门都朝 [[A gate must validate the object that actually takes effect]] 的方向修，但闭合度不同：oracle_source 门**彻底**堵住 [[oracle_source is a hardcoded constant not a recorded fact]] 的假常量；dtype 门**仅半闭合**——「实际测的 dtype」已从自报改成真实用例，但「任务书要求」侧仍由**可缺省的** caseset `dtype_required` 代传（`needs_user`/legacy/字段缺失 → 门不 BLOCK），且 `dtype_required` 的任务书权威来源（原 TBE 信息库）尚未接通，故 dtype 收窄这半边**未真正锚到任务书**、仍是保留缺口。

**Verified.** `plugin/acc-common/validate_acceptance_state.py`（三级门 + 抗坏输入 + dtype 覆盖门 + oracle_source 一致门）、接入 `plugin/acc-common/run_workflow.py`（`import validate_acceptance_state as gate` + `gate._GATES` + `BLOCKED` + `sys.exit`）、`test_validate_acceptance_state.py` 90 单测 OK — 2026-07-15（a3 真 torch 全量跑）。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-08 续：P0 机器门落地），[[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：Q7 dtype 覆盖门 + Q9 oracle_source 一致门）
