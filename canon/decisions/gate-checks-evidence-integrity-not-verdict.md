---
title: Gate checks evidence integrity not verdict
updated: 2026-07-08
status: proposed
---

# Gate checks evidence integrity not verdict

**Decision.** [[Machine-verifiable acceptance gate]] 只校验「证据可信 + 完整」（覆盖全用例、阈值一致、scope=kernel_only、抗坏输入），**不重判精度/性能 pass-fail**——那是 validator/perf_compare 的职责（[[ADR 0007 — Verdicts come from a deterministic validator]]）。

**Why.** 落地接入时发现：若门也重判 verdict，**合法的精度 fail 会被门标成 `BLOCKED`、盖住真因**（本应显示 `FAIL(精度)`）。所以两者分工：门失败 = 「证据不可信」→ `BLOCKED`；validator 失败 = 「结果没过」→ `FAIL(精度)` / 性能未达成。总体裁决才能既挡假通过、又如实表达真因。实证：`--defect` 跑（精度真 fail）→ 门 PASSED、总体 `FAIL(精度)` 不被盖（exit 1）；篡改 evidence 成子集 → 门 FAILED、`BLOCKED`。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-08 续：P0 机器门语义收敛）
