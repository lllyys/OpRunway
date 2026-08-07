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
| `upsample_nearest_exact2d.spec.json` · `upsample_nearest_3d.spec.json` | **shape_transform**（2026-07-23 新增）。输出形状由 `output_size` 属性直接给定。✅ **两者 gen_cases 层已通**（期2 C，2026-07-23 更正——旧记「跑不通」已 stale）：**a3 真 torch 各 21 case、`out_shape_source=golden.out_shape` 对账过**（rank≥5 已通、空 Tensor 冲突已解）。真机 NPU 验收（runner 编译跑测）另需 a3 build。im2col 同批 50 case 通（本地 torch shim 的 numpy unfold 对空输入局限、真 torch 无碍） |
| `catlass_basic_matmul.spec.json` | **synthetic demo**（catlass 库 example，无 task_doc/PR，非社区任务） |

⚠ **禁读纪律对新增的这三份同样适用**——本表只是索引，不是「可以读」的许可。

## ⚠ `runner_form`：这批样例里只有两份还能跑验收

2026-08-06 起，**验收裁决只由 `runner_form: "cpp_extension"` 产出**；`cpp` / `aclnn_py` 已停止准入，
`run_workflow.py` 连真机入口都不再给它们（详见仓根 `AGENTS.md` §4）。落到本目录：

| `runner_form` | 文件 | 现在能做什么 |
|---|---|---|
| `cpp_extension` | `median.spec.json` · `gaussian_blur.spec.json` | ✅ 可跑正式验收 |
| `cpp` | `isclose` · `sign` · `equal` · `neg` · `im2col` · `upsample_nearest_3d` · `upsample_nearest_exact2d` · `catlass_basic_matmul` | ⛔ 不产验收裁决。仍是合法的**结构参考**与测试夹具；`--mode mock` / `--mode catlass` 等显式非验收通路照跑 |

**为什么不把它们统一改成 `cpp_extension`**（2026-08-06 评估，结论：不改）：

1. **改了就是假话。** 这几份是 `cpp`（new_example）通路的历史见证——IsClose / Sign 就是在那条路上
   真机坐实的。把字段改写成 `cpp_extension` 等于声称它们在一条从未跑过的通路上验过。
   仓规 §5.8：不捏造。历史产物与历史声明**不改判**。
2. **改了它们会当场坏掉。** `cpp_extension` 形态要求 spec 声明 `call_variants`（aclnn 符号 +
   active attrs/outputs），本目录只有上面那两份有。给其余八份补 `call_variants` 需要**发明** ABI 事实
   ——没有任务书、没有 PR facts 可依。
3. **改了会破字节 pin。** `equal` / `isclose` / `neg` / `sign` 四份被
   `plugin/acc-common/test_gen_cases_dtype_attr.ExistingOpsByteIdenticalTest` 按 caseset sha256 钉住
   （两条 numpy 基线，其中 1.26 那条实测于验收真机）。`runner_form` 决定 gen_cases 查哪张 dtype 能力表，
   改了必然改摘要。

⚠ 所以看到 `"runner_form": "cpp"` 时的正确读法是「**这是历史形态的参考样例**」，
**不是**「这条通路还可选」。真要拿某个样例走正式验收，须重新按 `cpp_extension` 抽一份 spec
（含 `call_variants`），那是**新验收**，不是旧样例换个字段。

> 单元测试也从这里读真样例（真 op 名 → GOLDEN 可解析、真内容 → 断言稳定），
> 但**测试消费 ≠ acc-spec 产 spec 时可查阅**：禁读纪律只约束「产 spec」阶段。
