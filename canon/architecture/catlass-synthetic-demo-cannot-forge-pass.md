---
title: Synthetic catlass demo cannot forge a PASS acceptance
updated: 2026-07-10
status: proposed
---

# Synthetic catlass demo cannot forge a PASS acceptance

「synthetic `catlass_mock` 能产出 PASS 的 `acceptance.json`」这一断言经实跑 **证伪（REFUTED）**。
据当前入口分析，**暂未发现**让 mock demo 伪造验收通过的可达路径：

- `run_workflow.py` 无条件先走 `gen_cases`，catlass 算子在此直接 `ValueError` 崩，走不到裁决；
- `catlass_adapter.main` 只产 **development-grade** evidence（显式带 `evidence_grade: "development"` 与
  `acceptance_note: "NON-ACCEPTANCE (mock evidence)"`），**不产 `verdict.json` / `acceptance.json`、不跑门**。

原 TODO 把它列为「待补接线」是措辞框错了。**但「不存在可达路径」是完整可达性判断，上述两条只是当前代码事实
的文本核验，不足以机械证明其充分性**——故本页 `proposed`。要升 `verified`，须补一条自动化**负向测试**，
实际断言 `catlass_mock` 无法生成干净 PASS 的 `acceptance.json`。

是否补纵深门（让三级门读 `evidence_grade`、拒 development-grade 产出干净 PASS，以 guard 未来真把 catlass
接进门的情形）**仍属设计取舍、未实施、未决**。

判定权归属不变：pass/fail 由 `validator.py` / `perf_compare.py` 裁，门只管证据可信完整（ADR 0007）。
被测对象的接入模式见 [[Repo adapter interface and modes]]。

**仓内可核部分**（2026-07-10）：`plugin/acc-common/catlass_adapter.py` 全文不含 `acceptance.json` 或
`verdict` 字样，且含 `evidence_grade` / `NON-ACCEPTANCE` 标记；`plugin/acc-common/run_workflow.py` 在
Task1 阶段无条件调用 `gen_cases`。这些是文本搜索可核的**当前代码事实**，不等于可达性结论成立。

**Sources.** [[session 64604f71-dd13-4256-9a74-072fec018b48 · 2026-07-09]]
