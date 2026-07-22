# plugin/samples/specs —— spec 参考案例（**非运行时路径**）

这里放几份填满真值的 `<op>.spec.json`，作为**人读的参考案例**，帮助理解 spec 长什么样、字段怎么填。

## ⚠ acc-spec 产 spec 时的禁读纪律

**`acc-spec` 在为某个算子产 spec 时，不得查阅这里的任何同名算子样例**——尤其被验收算子恰好在样例里时，
读样例 = 先看到同一道题的标准答案（threshold / target_ratio / hardware / 语义改造 note 逐项都在），
之后的「推导」无法排除锚定（软污染）。

- **要看结构** → 看空模板 `plugin/acc-common/spec_schema_template.jsonc`（零真实数值）。
- **产 spec 只读** → `task_doc.md` + `pr_facts.json`（+ 空模板）。
- 这些样例**不在**任何运行时读取路径上（已从 `plugin/acc-common/specs/` 迁出），是纯参考物。

## 现存样例

| 文件 | 说明 |
|---|---|
| `isclose.spec.json` · `sign.spec.json` · `equal.spec.json` · `neg.spec.json` | 真实社区算子的参考 spec（人读示形用） |
| `catlass_basic_matmul.spec.json` | **synthetic demo**（catlass 库 example，无 task_doc/PR，非社区任务） |

> 单元测试也从这里读真样例（真 op 名 → GOLDEN 可解析、真内容 → 断言稳定），
> 但**测试消费 ≠ acc-spec 产 spec 时可查阅**：禁读纪律只约束「产 spec」阶段。
