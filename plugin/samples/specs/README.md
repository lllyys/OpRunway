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
| `isclose.spec.json` · `sign.spec.json` · `equal.spec.json` · `neg.spec.json` | **elementwise** 真实社区算子（输出同输入形状，对应 golden 不导出 `out_shape`） |
| `im2col.spec.json` | **shape_transform**（2026-07-23 新增）。输出形状由属性公式推：`L = ∏ floor((spatial+2p−d(k−1)−1)/s + 1)`，且**输出 rank 随输入 rank 跳变**（3 维入→2 维出、4 维入→3 维出）。C1/C2/C3 三条契约的正例：`out_shape()` · `list[int]` attr · `rank: [3,4]` |
| `upsample_nearest_exact2d.spec.json` · `upsample_nearest_3d.spec.json` | **shape_transform**（2026-07-23 新增）。输出形状由 `output_size` 属性直接给定。⚠ **两者当前都跑不通**（3d 卡 rank=5 超出 `_REG_SHAPES` 阶梯、2d 卡引擎强制的空 Tensor 用例与任务书「不支持空Tensor」冲突）——保留是因为**它们准确记录了引擎缺口在哪**，见各自的 `task_pr_gaps` |
| `catlass_basic_matmul.spec.json` | **synthetic demo**（catlass 库 example，无 task_doc/PR，非社区任务） |

⚠ **禁读纪律对新增的这三份同样适用**——本表只是索引，不是「可以读」的许可。

> 单元测试也从这里读真样例（真 op 名 → GOLDEN 可解析、真内容 → 断言稳定），
> 但**测试消费 ≠ acc-spec 产 spec 时可查阅**：禁读纪律只约束「产 spec」阶段。
