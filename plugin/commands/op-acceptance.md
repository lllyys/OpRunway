---
name: op-acceptance
description: 跑一个 NPU 算子的验收流水线（Task 1 用例生成 → Task 2 NPU 跑测+裁决 → Task 3 性能对比），产出 verdict/perf_report。
argument-hint: "<op 或 spec.json 路径> [--mode mock|new_example]"
---

# op-acceptance — 算子验收编排（Layer 2 薄壳）

这是**编排壳**：只搬 JSON、调 `acc-common/` 的确定性脚本，**不含判定脑子**（判定在 validator，ADR 0007）。

## 步骤

1. **定位 spec**。参数是 `<op>` → 找 `plugin/acc-common/specs/<op>.spec.json`；是路径 → 直接用。没有 spec → 先让 `task-doc-parse` agent 把任务书 md 解析成 `spec.json`（**唯一必需 NL 步**，输出中立 JSON）。
2. **选模式**。`mock`（本地干跑、无需真机，默认）或 `new_example`（真机 build/run PR 工程）。**`new_example` 需 NPU（ascend-a5/a3）+ 用户开 VPN——先提示用户**。
3. **跑**。`python3 plugin/acc-common/run_workflow.py <spec> --mode <mode> --out reports/<op>/`。它串 Task 1→2→3、产 `caseset/evidence/verdict/baseline/perf_report.json`。
4. **复核**。verdict `overall.verdict`：
   - `pass` → 报告过；`fail` → 列失败用例 + 判据；`needs_review` → 把 `uncertain` 用例交 `eval` agent 独立复核（不改裁决、只提意见）。
   - 别把 `needs_review` 当 pass。
5. **报告**。中文汇总：各维度（功能/精度/性能）通过数、失败用例、性能达标比、任务书↔PR 落差（`spec.task_pr_gaps`）。数字全引真实产物、推断项标「(推断)」。

## 约束

- 全程中文；副作用（真机 clone/build/跑测）先确认。
- 只认任务书为验收权威（[[Task spec is authoritative over PR]]）；不把「PR 有测试」当「验收过了」。
- 换运行时（Codex/Antigravity）：换本壳、`acc-common/` 不动。
