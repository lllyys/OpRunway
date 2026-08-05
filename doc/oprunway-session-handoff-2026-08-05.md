# OpRunway 会话交接 · 2026-08-05

> **本文是当前交接入口。** 只写「接下来做什么、从哪开始、有什么坑」，不堆历史。
> 历史流水看 `doc/oprunway-changes-brief.md`；旧 handoff（`2026-07-26` / `2026-07-13`）仅作历史材料。

---

## 1 · 一句话

aclnnRoll complex64 试跑失败，问题定位完成，**两份实施方案已写好并过审，可以直接开工**。
本轮**只产文档，未改任何 `plugin/` 代码**。

---

## 2 · 从哪开始（按序）

| 步 | 做什么 | 文档 |
|---|---|---|
| **1** | 本地来源通路 + `runner_form` 收敛到 `cpp_extension` | `doc/oprunway-local-source-plan.md` |
| **2** | 修两个脚本 bug（小改，不修会绊住第 3 步的测试） | `doc/oprunway-roll-complex64-trial-findings.md` §2 **A1 / A2** |
| **3** | workflow 治理批（确认门 / 执行路径 / 耗时监控 / 数据确定性 / 轴集 / 样例隔离） | `doc/oprunway-workflow-governance-plan.md`，按其 §6 分批 |

想知道**为什么**要做这些：`doc/oprunway-roll-complex64-trial-findings.md`（问题清单，非 plan）。

---

## 3 · 第 2 步那两个 bug（本仓仍在，可立刻修）

```
plugin/acc-common/precision_policy.py:268-273
  derive_output_dtype 不解析 <from_input> 哨兵 → 单输出 spec 崩在
  "ascendoptest_default 无 dtype='<from_input>' 阈值"
  同文件 :373-381 的多输出路径处理是对的，把判断上提共用即可

plugin/acc-common/aclnn_runtime/aclnn_driver.py:266-267
  f-string 替换表达式跨行 = PEP 701，Python 3.12+ 才支持；真机是 3.11.6 → SyntaxError
  ⚠ 本地 ast.parse(feature_version=(3,11)) 检测不出来，要用真 3.11 解释器
```

---

## 4 · 三条不要再绕回去的判断

1. **不要加监工 agent。** 这次 Roll 的根因是「规则齐全但被绕过」——
   `SKILL.md:161`、`op-acceptance.md:63/67` 三条纪律都在，全被绕过。
   再加一个 agent 监工，它同样能自己决定「算了不管了」。**要硬门，不要纪律。**
2. **不要把 `dtype_deferred` 当成 gap 的通用写法。**
   它零硬校、且不进 `_FINDING_GAP_KINDS` → 会让 Q7 覆盖门放行且终态可以是干净 `pass`。
   把 gap 改成结构化之前，必须先补硬校 + 规定终态（governance-plan 的 C3 一节）。
3. **`producer.logic_sha256` 进 payload，所以改工具必然改 digest。**
   任何「断言 digest 不变」的回归都会按设计失败——正确写法是
   「payload 去掉 `producer` 后逐字节相同」。

---

## 5 · 待办与已知缺口

| 项 | 状态 |
|---|---|
| 远端 `/mnt/docker/libotao2/OpRunway-main` 上 3 处 infra 修改**未回流本仓** | `repo_adapter` 加 complex64、`precision_policy` 修 `<from_input>`、`aclnn_driver` 修 f-string。**后两处就是 §3 的两个 bug** |
| findings 的 **§6**（8 项目标现状核对）**未过 codex 审** | 文内已标注；引用的 file:line 经本 session 自查，设计判断未经独立检验 |
| `governance-plan` 批次 C（轴集） | 只出设计说明交评审，**不动 `gen_cases` 代码** |
| 产 `vendor_build_receipt` 的外部驱动 | 全仓只有消费方、无产出方；**文件名未知**，见 local-source-plan Step 4 的定位方法 |
| 本轮四份文档 | 未 commit |

---

## 6 · 审计留痕

```
.cc-suite/audits/audit-fix-20260805-121500-findings.md      findings §1-§5，16 条（含 1 Critical）
.cc-suite/audits/audit-fix-20260805-local-source-plan.md    18 条（含 3 Critical）
.cc-suite/audits/audit-fix-20260805-governance-plan.md      11 条（含 1 写过头的安全声明）
```

⚠ **codex 审计经验**：整份文档一次审必超时（实测连挂 3 次、0 字节输出）。
有效做法是**拆块 + 把代码原文内联进 prompt**，省掉 codex 的仓内搜索。
