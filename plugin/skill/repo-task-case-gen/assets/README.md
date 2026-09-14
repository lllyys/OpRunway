# 骨架与插件示例

两类东西，用途不一样：

| 文件 | 是什么 | 怎么用 |
| --- | --- | --- |
| `skeleton.yaml` | **唯一的 YAML 数值来源**。最简形态：一个 tensor 输入 + 若干 attr，ND，标准两段式 | `cp` 走改名，改完跑 `--dry-run` |
| `*_constraint.py`、`function_*.py` | **钩子 API 的用法示例**，不是某个算子的标准答案 | 照着 API 写自己的，不要按算子对号入座 |

**这里没有 `facts.json` 的样例。** 字段与判据在 `references/interface-facts.md`，
写完跑 `check_facts.py`，它逐字段报错，报到字段名。

## 骨架不覆盖的形态

`skeleton.yaml` 头部注释里有岔路表。核心一条：**它演示的是最简形态，不是「算子应该长成这样」。**
遇到没见过的形态，先查 `references/atk-surface.md` 的能力清单确认 ATK 支不支持，
再决定是读源码还是如实报告不支持。

## 插件示例各自演示什么

| 文件 | 演示的 API 用法 |
| --- | --- |
| `Roll_constraint.py` | `after_case_config` 的基本形态：**序列参数是 list，改长度用切片赋值，改取值赋 `range_values`** |
| `IndexFillTensor_constraint.py` | 按序号分批构造场景：**`case_config.id` 在钩子里恒为 0，要分批必须自己数**（`self._seq`） |
| `Median_constraint.py` | 参数取值随秩变时怎么写 |
| `tensors_constraint.py` | **输入是 `aclTensorList*`（`type: tensors`）时必抄**：列表内 dtype 归一、两个列表等长对齐、场景构造时不要截列表长度。ATK 逐元素独立抽 dtype，不归一在 NPU 侧按 `561002` 整批假失败 |
| `high_rank_constraint.py` | **秩 ≥5 的用例怎么显式构造**：高秩抽不出来，见 `references/case-strategy.md`「高秩不靠抽样」。文档 rank 上界 >4 时把它的 `after_case_config` 抄走 |
| `function_index_fill.py` | CPU 执行器的注册方式：torch 侧参数类型与 aclnn 对不上时的适配（list → int64 Tensor） |
| `function_median.py` | 多输出算子：torch 的具名元组要摊成普通元组 |
| `function_bernoulli.py` | `accuracy.kind: builtin` 时的退化执行器：**只给形状不给值**，`torch.zeros_like` 就够 |
| `function_foreach_mul_list.py` | **输出是一个张量列表**（`aclTensorList*` out）时的执行器：结果要包成「列表里一个元组」，否则 golden 的 `output_info.json` 会摊成每张量一项，NPU 侧参数个数对不上 |

## 三个静默的坑

这几处写错**不报错，只是永远不被调用**，所以插件示例留在这里：

| 坑 | 写错的现象 |
| --- | --- |
| `after_input_config(self, index, input_case)` 第二个参数是 `index` 不是 `case_config` | 钩子永不被调用，用例照生成，约束全丢 |
| 改取值要改 `range_values` 不是 `value` | 同上，静默 |
| `case_config.id` 在钩子里恒为 0 | 按 id 分批 → 全部用例落进同一分支，条数与 dtype 分布都看不出异常 |


## 改这里的纪律

`skeleton.yaml` 的任何数值改动，**必须在真机上用它跑一遍 `gen_cases.py --dry-run`**。
它是可执行文件不是文档代码块，就是为了这条纪律能落地——上一版骨架写在
`yaml-authoring.md` 的代码块里，改它零成本、跑它没门路，三代之内就烂成了错的。
