# archive_ops — 已验证算子案例库

> **材料仓的一部分**（`plugin/workflows/` 无 SKILL.md）。收录**已真机验收**的算子案例卡，供加新算子时对照参考。**如实标 verdict、绝不混称 PASS。**

## 入库判据（硬）

1. **仅收真机-verified**：案例须已过机器门并有可定位的真机产物；mock-only 不收。
2. **verdict 如实分类、不混称**：一个案例的裁决按类别标——`pass`（精度+性能皆过）/ `precision-pass·perf-fail`（精度过、性能未达成）/ …；**性能未达成的样本绝不标 PASS**。
3. **引用可解析**：`<op>.spec.json` 是**内联真实副本**（真样例已迁 `plugin/samples/specs/`、移出运行时路径，不再 symlink）；到 `plugin/samples/runners/oprunway_<op>_runner.cpp` 的 symlink 仍须能 `readlink` 解析（相对拓扑、不 sed 绝对路径）。
4. **输入与证据闭合**：current 案例只认调用方给定任务书/源码以及 taskdoc/content anchor → build → ELF →
   execution/evidence 完整链。历史上已明确作废的记录不重新入库，但不再对 current 输入做 PR 对应鉴权。

## case.md 最小字段 schema

每个 `archive_ops/<op>/case.md` 顶部须含下列字段（缺 PR 时用 from-scratch 标记）：

| 字段 | 含义 | 取值/示例 |
|---|---|---|
| `op` | 算子名 | `IsClose` / `Sign` |
| `taskdoc` | 任务书路径或链接（无则标 from-scratch） | 链接 / `from-scratch example（无社区任务）` |
| `pr` | 被测 PR（无则 from-scratch 标记） | `gitcode ops-math!2702` / `无（demo 算子）` |
| `verdict` | 裁决类别（**如实、不混称**） | `pass` / `precision-pass·perf-fail` |
| `evidence_path` | 真机证据落点（reports/… 或当前 validation 引用） | 必填 |
| `real_machine` | 哪台真机 | `a3` / `a5` |
| `caveat` | 需注意的口径/未闭合项 | 文本 |

## 入库校验清单（收录前逐条过）

- [ ] 真机-verified（有 a3/a5 实测证据、非 mock-only）？
- [ ] verdict 按类别如实标、性能未达成没被写成 PASS？
- [ ] `op` / `taskdoc`（或 from-scratch 标记）/ `pr`（或 from-scratch）/ `verdict` / `evidence_path` / `real_machine` / `caveat` 七字段齐？
- [ ] `<op>.spec.json` 是内联真实副本（内容与 `plugin/samples/specs/<op>.spec.json` 一致、非 symlink）？到 `plugin/samples/runners/oprunway_<op>_runner.cpp` 的 symlink 能 `readlink` 解析（相对路径、非绝对/私有）？
- [ ] 调用方输入、content anchor、build/ELF 与 execution/evidence 是否完整闭合？

## 当前收录（种子，覆盖薄——扩面卡真机验收）

| 算子 | verdict | 备注 |
|---|---|---|
| `isclose/` | **pass** | ⚠ 无社区任务 PR（pipeline 首建的 from-scratch demo 算子），精度+性能皆过 |
| `sign/` | **precision-pass·perf-fail** | PR ops-math!2702；`sign_004` 9.68us vs TBE 6.32us、ratio 0.653——**已实测·性能未达成，非 PASS** |

> Equal 作废不收（#2890 误配、任务未验收）；Neg 为 mock 端到端 demo（未真机），暂不收。
