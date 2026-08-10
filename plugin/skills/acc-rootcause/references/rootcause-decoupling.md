# acc-rootcause 详规 · FAIL 解耦纪律

> **定位 guard**：acc-rootcause 是 P2 规划的**纪律 skill**，**尚未接入 live 流、无脚本判定、不产 verdict**（裁决唯一归确定性脚本链）。本文件只装归因纪律。

## 0. 当前实施依据

| 来源 | 承载 |
|---|---|---|
| 仓根 `AGENTS.md` §1 | 调用方断言任务书与源码对应；任务书是语义与验收权威，源码/op_def 是 ABI 与被测事实 |
| 仓根 `AGENTS.md` §5.8、§7 | FAIL 归因前复核内容锚、build、ELF、加载对象、调用和输出写入，再解耦 DUT 与 harness |
| `source_provenance.py`、`vendor_build_receipt.py` | caller-trusted 输入与 current build receipt 的确定性契约 |
| `validate_acceptance_state.py` | facts → build → ELF → execution 的正式复核门 |

## 1. 归因层级（从上游到下游，逐层不可跳）

```
① 本轮内容与执行绑定是否闭合?        ← 任务书摘要 / content_anchor / build / ELF / 调用 / 输出
   ├─ 缺失或漂移 → 停止归因，按证据门产物报告
   └─ 绑定成立 → 进 ②
② 被测物 vs 我们的 harness（解耦）  ← 别凭 signature 猜
   ├─ 换内置 op 对照 / 跑自带 example / 查 vendor 制品 / dtype 逐个测 / 自 build+手算 golden
   └─ 定位到「被测物缺陷」或「harness 缺陷」
③ 缺陷定性（技术判定）             ← 可下；范围以真机重编为准
④ 程序口径（是否算官方验收失败/上报） ← 未确认前留口、不外发
```

## 2. 内容绑定的三组证据（①层）

1. **输入绑定**：任务书摘要、源码 `content_anchor` 与 CP-A `source_facts.json` 一致；
2. **构建与加载绑定**：build 前重算 anchor、current vendor receipt、实际 ELF 与双符号 owner 一致；
3. **执行绑定**：调用、case、输出写入和 evidence 能闭合到同一轮内容链。
调用方给定的任务书/源码关联不再由 workflow 重新鉴权；locator 元数据只作 transport observation。

## 3. 解耦四对照（②层）· 全 0 输出决策树

**全 0 输出 ≠ harness 嫌疑**——可能是 harness 没绑/没回写，也可能被测 kernel 没执行/没写：

- **换内置 op 同 case 对照**：内置对 → runner/harness 清白、锅在被测 op；内置也错 → 查 harness。
- **跑被测物自带 example**：它对 → 被测物可用；它也错 → 被测物真坏。
- **查 vendor 制品**：op-info / kernel binary 是否真生成；缺失即实锤（如 Equal `equal_def.cpp` 漏 `AddConfig("ascend910_93")` → build 静默丢 A3 kernel → 全 0，但 aclnn 却 ACL_SUCCESS）。
- **dtype 逐个 op 级测**：整体不通 vs 某 dtype 路不通（如 Equal 补注册后 double 通、fp32/fp16 仍炸 → float 路没做完）。
- **自 build + 声明 dtype + 手算 golden**：小用例逐元素独立复现，坐实是被测物还是我方问题。
- **异常固定位型须脱桥复现**：原 harness 若以未初始化内存承接输出，固定的全 0、最大整数等位型只证明
  实际读回异常，不能证明由谁写入。冻结一个原失败 case，在独立 direct/官方 example 调用中先把输出
  预填为可识别 sentinel，再调用、同步并读回；同输入另跑 stock 实现。direct 异常且 stock 正常才归 DUT，
  direct 正常则查原 harness。这个最小对照不重造 case、不放宽标准，也不要求重跑完整矩阵。

## 4. 归因红线（③④层）

- **源码「一行诊断」须真机重编坐实**：读源码得「一处即修」是假设；补上重编可能暴露更深缺陷（「一行修好」被证伪）。
- **技术判定 vs 程序口径分开记**：技术判定可下（实测缺陷）；程序口径（官方验收/上报）未确认前留口、不外发、**不来回改口**。
- **职责边界**：把缺陷定性清楚即可，不替 PR 作者修到底。
- **裁决归脚本**：解耦结论供理解；pass/fail 仍由 `validator.py` / `perf_compare.py` / `validate_acceptance_state.py` 出。

## 5. 反面教训

真机异常不能靠源码阅读或单次输出直接归因。Current workflow 接受调用方给定的任务书/源码配对，不再鉴定
PR 对应关系；但必须先核任务书摘要、源码 `content_anchor`、build receipt、实际加载 ELF/符号和输出写入，
再用独立 golden 解耦 DUT 与 harness。历史误配案例只留在 Git 与 changes brief，不作为现行身份门。
