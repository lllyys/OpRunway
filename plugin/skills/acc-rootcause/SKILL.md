---
name: acc-rootcause
description: OpRunway 验收里任何 FAIL 归因前的解耦纪律薄壳——先验证「任务书↔PR 对应」本身，再用被测物自己的东西（自 build + 声明 dtype + 手算 golden + custom↔builtin 对照）解耦「被测物 vs 我们的 harness」，才下归因。P2 规划的原子能力 skill：尚未接入 op-acceptance live 流、勿自动触发、无脚本判定（纪律 skill，不产 verdict）。真机 FAIL 要归因、或复盘一次归因翻案时阅读。
---

# acc-rootcause — FAIL 解耦纪律（原子能力 skill）

**定位（诚实，P2 边界）**：本 skill 是 P2 规划的**纪律 skill**——只装「FAIL 该怎么归因、什么前提下才能下结论」的**推理纪律**，**无脚本判定、不产 verdict、不落盘**。裁决仍唯一归确定性脚本链（`validator.py` / `perf_compare.py` / `validate_acceptance_state.py`，ADR 0007 canonical）；本 skill 只约束「下结论前先做什么解耦」。

- **注册 ≠ 接线**：P2 把本 skill 列进 `plugin/AGENTS.md` 的 `skills:` 注册清单（跨 CLI 分发 / 发现），**但未接进 `op-acceptance` runtime 调用链**——live 归因由 `acc-verify-rootcause` subagent（P1，`dispatch_mode=rootcause`）承载，本 skill 勿被自动触发。
- 血教训来源：Equal（2026-07-08→07-09 三度翻案），见 canon `Verify spec-PR correspondence before acceptance`（proposed）+ `Root-cause decoupling before attribution`（proposed）。

## 0. 更上游前提（比解耦更早）· 先验证「任务书↔PR 对应」本身

**任何 FAIL 归因、乃至任何验收裁决之前**，先确认：

1. **这个 PR 确是这份任务书的交付 PR**（靠 **issue/追踪号 + 改动落点目录**，**不靠算子名字面匹配**——名字常是常见子串、易误配）；
2. **该任务确有已验收的交付**（不是「任务书要了但未落地/未验收」的空任务）。

配错对应、或对应的其实是空任务 → **下游一切裁决作废**，哪怕精度/性能门再严、解耦做得再干净。识别并跳过「未验收空任务」正是 OpRunway 的应有能力。（Equal：#2890 系误配、Equal 社区任务未验收，前「A3 真阳性」结论整体作废——refine 了三遍归因，却始终没质疑最上游。）

## 1. 解耦纪律（对应确认后，归因前）· 被测物 vs 我们的 harness

**「输出全 0 / 未被写」既可能是 harness 没绑定/没回写，也可能是被测 kernel 压根没执行/没写**——区分靠对照，不靠直觉：

- **同一份 runner 换内置 op 做对照**：输出正确 → runner/harness 清白、锅在被测 op；仍错 → 才轮到查 harness。
- **跑被测物自带的 example**（官方调用样例）：它对 → 被测物本身可用；它也错 → 被测物真坏。
- **查已编 vendor 包制品**（op-info / kernel binary 是否真生成）：制品缺失即实锤。
- **dtype 逐个做 op 级测**：分清整体不通还是某 dtype 路不通。
- **被测物自 build + 声明支持的 dtype + 手算 golden 独立复现**：小用例逐元素比，坐实范围。

## 2. 下结论的红线

- **源码「一行诊断」须经真机重编坐实范围**：读源码得「缺一行、一处即修」是**假设**、非结论——补上重编后可能暴露更深缺陷（补一处、另一处仍炸）。范围以真机复现为准。
- **技术判定 与 官方/程序口径 分开记**：「实测是被测物缺陷」是技术判定、可下；「是否算官方验收失败 / 该不该对外上报」是程序口径，未确认前留口、不外发、不来回改口。
- **验收职责边界**：把缺陷**定性**清楚即可，**不替被测 PR 作者把算子修到底**。
- **裁决归脚本**：本 skill 不产 pass/fail；解耦结论供人/编排理解，最终 verdict 仍由确定性脚本链出（ADR 0007）。

**详规见** `references/rootcause-decoupling.md`（对应校验三证据 · 解耦四对照 · 全 0 决策树 · 归因红线，按 canon tier 引）。
