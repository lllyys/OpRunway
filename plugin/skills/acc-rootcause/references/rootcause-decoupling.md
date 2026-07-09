# acc-rootcause 详规 · FAIL 解耦纪律

> **定位 guard**：acc-rootcause 是 P2 规划的**纪律 skill**，**尚未接入 live 流、无脚本判定、不产 verdict**（裁决唯一归确定性脚本链，ADR 0007）。本文件只装归因纪律。载重前逐个 Read 引用页并**按 tier**（两页均 proposed·未 settle → 存疑、先核，勿当事实）。

## 0. canon 依据（按 tier）

| 页 | tier | 承载 |
|---|---|---|
| `decisions/verify-spec-pr-correspondence-before-acceptance.md` | **proposed·未 settle** | 最上游前提：先验证任务书↔PR 对应本身（issue 号 + 落点目录，非名字面匹配） |
| `decisions/root-cause-decoupling-before-attribution.md` | **proposed·未 settle** | 归因前先用被测物自己的东西解耦「被测物 vs harness」 |
| `decisions/task-spec-authoritative-over-pr.md` | （按 tier 载重前核） | 任务书权威（前提是先确认它对应被测 PR） |

> ⚠ 两页均 proposed：作方法论指针可用，但**不当已 settle 事实**；若与后续 review 冲突以 review 为准。

## 1. 归因层级（从上游到下游，逐层不可跳）

```
① 任务书↔PR 对应本身对不对?        ← 最上游（Equal 血教训：漏这层，下游全废）
   ├─ 配错 / 空任务 → 裁决作废，停；识别并跳过「未验收空任务」
   └─ 对应成立 → 进 ②
② 被测物 vs 我们的 harness（解耦）  ← 别凭 signature 猜
   ├─ 换内置 op 对照 / 跑自带 example / 查 vendor 制品 / dtype 逐个测 / 自 build+手算 golden
   └─ 定位到「被测物缺陷」或「harness 缺陷」
③ 缺陷定性（技术判定）             ← 可下；范围以真机重编为准
④ 程序口径（是否算官方验收失败/上报） ← 未确认前留口、不外发
```

## 2. 对应校验的三条证据（①层）

1. **改动落点目录**：PR 的 `target_dir` 对上任务书声明的算子目录（机器可比）；
2. **issue / 追踪号**：NL 读任务书与 PR title 的追踪号（**非算子名字面匹配**）；
3. **用户确认**：证据摆给用户拍板。
三者合断 → `confirmed` 才进解耦；`mismatch`/`empty_task` → 停、出程序结论（非 pass/fail）。

## 3. 解耦四对照（②层）· 全 0 输出决策树

**全 0 输出 ≠ harness 嫌疑**——可能是 harness 没绑/没回写，也可能被测 kernel 没执行/没写：

- **换内置 op 同 case 对照**：内置对 → runner/harness 清白、锅在被测 op；内置也错 → 查 harness。
- **跑被测物自带 example**：它对 → 被测物可用；它也错 → 被测物真坏。
- **查 vendor 制品**：op-info / kernel binary 是否真生成；缺失即实锤（如 Equal `equal_def.cpp` 漏 `AddConfig("ascend910_93")` → build 静默丢 A3 kernel → 全 0，但 aclnn 却 ACL_SUCCESS）。
- **dtype 逐个 op 级测**：整体不通 vs 某 dtype 路不通（如 Equal 补注册后 double 通、fp32/fp16 仍炸 → float 路没做完）。
- **自 build + 声明 dtype + 手算 golden**：小用例逐元素独立复现，坐实是被测物还是我方问题。

## 4. 归因红线（③④层）

- **源码「一行诊断」须真机重编坐实**：读源码得「一处即修」是假设；补上重编可能暴露更深缺陷（「一行修好」被证伪）。
- **技术判定 vs 程序口径分开记**：技术判定可下（实测缺陷）；程序口径（官方验收/上报）未确认前留口、不外发、**不来回改口**。
- **职责边界**：把缺陷定性清楚即可，不替 PR 作者修到底。
- **裁决归脚本**：解耦结论供理解；pass/fail 仍由 `validator.py` / `perf_compare.py` / `validate_acceptance_state.py` 出（ADR 0007）。

## 5. 反面案例（Equal，钉在这里防重犯）

真机 `out.bin` 全 0 → 归因 refine 三遍（op-bug → harness-bug → op「真阳性/A3 缺陷」），**每遍都错在没质疑最上游**。2026-07-09 确认 #2890 系误配、Equal 社区任务未验收 → 前「A3 未达标·真阳性」**整体作废**。教训分两层：解耦（②）**必要但不充分**——之前还得先验证对应（①）。原缺陷报告 `doc/equal-a3-defect-report.md` 已删除。
