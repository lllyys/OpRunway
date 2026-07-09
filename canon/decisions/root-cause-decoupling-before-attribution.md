---
title: Root-cause decoupling before attribution
updated: 2026-07-09
status: proposed
---

# Root-cause decoupling before attribution

验收里任何 FAIL 归因前，必须先用**被测物自己的东西**解耦，确认是「被测物 vs 我们的 harness」，才能下结论——不能凭 signature 猜、更不能在质疑下来回改口。

**全 0 输出 ≠ harness 嫌疑。** 「输出全 0 / 未被写」的特征既可能是 harness 没绑定/没回写，**也可能是被测 kernel 压根没执行/没写**。区分靠对照，不靠直觉：

- **同一份 runner 换内置 op 做对照**：输出正确 → runner/harness 清白、锅在被测 op；仍错 → 才轮到查 harness。
- **跑被测物自带的 example**（op 官方调用样例）：它对 → 被测物本身可用；它也错 → 被测物真坏。
- **查已编 vendor 包的制品**（op-info / kernel binary 是否真生成）：制品缺失即实锤。
- **dtype 逐个做 op 级测**：分清是整体不通还是某 dtype 路不通。

**源码「一行诊断」须经真机重编坐实范围。** 读源码得出的「缺一行、一处即修」是假设、不是结论——真机补上重编后可能暴露更深层缺陷（补一处后另一处仍炸）。范围以真机复现为准。

**技术判定 与 官方/程序口径 分开记。** 「实测是被测物缺陷」是技术判定、可下；「是否算官方验收失败 / 该不该对外上报」是程序口径，未确认前留口、不外发。

**验收职责边界**：验收把缺陷**定性**清楚即可，不替被测 PR 作者把算子修到底。

动因案例（Equal，2026-07-08→07-09 三度翻案）：真机 `out.bin` 全 0 的归因 refine 了三遍——op-bug → harness-bug → op「真阳性(A3 缺陷)」——**但都错在没质疑最上游**。2026-07-09 正式确认 **#2890 根本不是这个 Equal 任务的 PR（配错）、且该任务未验收通过**，故「真阳性」结论**整体作废**。教训分两层：本页的「先解耦再归因」（op vs harness）**仍成立、必要**，但**不充分**——解耦之前还得先验证「比对基准（哪份任务书 × 哪个 PR）」本身，见 [[Verify spec-PR correspondence before acceptance]]。（原缺陷报告 `doc/equal-a3-defect-report.md` 已删除。）

**Sources.** [[session 7f7b1411-e1d0-47aa-93d5-19ccd6fcd130 · 2026-07-08]]，[[session 37223d6d-c20e-48a9-84f5-99aeaddb7f51 · 2026-07-09]]（Equal 三度翻案：#2890 配错·任务未验收→前「真阳性」作废；解耦必要不充分、上游先验证对应）
