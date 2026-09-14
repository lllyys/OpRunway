"""张量列表（`type: tensors`）算子的约束器示例——**输入里有 `aclTensorList*` 时，
把 `after_case_config` 里那三段抄走**。单独跑也行（把注册名改成 `<Op>_constraint`）。

## 为什么这类算子一定要写约束器

ATK 对 `tensors` 的默认行为有三处与 `aclTensorList` 的语义对不上，**每一处都不报错，
都在 NPU 侧才炸**：

| ATK 干了什么 | 与文档冲突在哪 | 不修的后果 |
| --- | --- | --- |
| 列表里**每个元素各自独立抽 dtype**（`get_tensors` 对每个元素调 `generate_with_index`） | 文档基本都写「该参数中所有 Tensor 数据类型保持一致」 | 实测 210 条里 94 条是 `[bf16, int8]` 这类混合列表，NPU 侧按 `561002` **整批假失败** |
| 两个列表参数**各自独立抽 `tuple_numbers`** | 按序号逐对运算的算子要求两侧等长 | 短的那侧配不齐，用例非法 |
| 列表总量超预算时**把元素预算减半重来**（`parameter_tensors.py:73-81`） | —— | 预算越砍越小，接受率归零打满 300s 守卫；且长度分布被系统性挤向 1 |

前两条在这里修。第三条的逃逸出口是 **`dim_values` 里必须留一个 `0`**（`numel == 0`
无条件通过预算检查），文档写「支持空 Tensor」时它同时也是一条正经用例。

## 三段各修什么

1. **列表内 dtype 归一**——所有元素跟 `x1[0]`
2. **两个列表等长 + 逐张量对齐**——长度取较小者，再整表跟随
3. **高秩显式写形状时不要截长度**——`x1[:] = x1[:1]` 会让「秩 5–8 × 多元素列表」
   一条都不剩，而这正是这类算子最该测的组合。真机上踩过，`check_coverage.py`
   的组合空缺里表现为 `<x1>.rank × <x1>.列表长度` 缺一整列

秩 ≥5 的轴长怎么算见 `high_rank_constraint.py`，`large` 档怎么显式构造见
`references/case-strategy.md`「规模档」。这两段与本文件正交，按需拼。
"""

from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.case_generator.generator.generate_types.default import DefaultGenerator
from atk.configs.case_config import CaseConfig

# 改这个：两个张量列表参数在 inputs 里的下标，按 facts.json 的 signature 顺序数
X1_INDEX = 0
X2_INDEX = 1


@GENERATOR_REGISTRY.register("tensors_constraint")
class TensorsConstraint(DefaultGenerator):
    """两个 `aclTensorList*` 输入、按序号逐对运算的算子。"""

    def after_case_config(self, case_config: CaseConfig) -> CaseConfig:
        # `case_config.id` 在这一步恒为 0，要按序号分批就自己数（plugin-authoring.md）
        self._seq = getattr(self, "_seq", 0) + 1

        x1 = case_config.inputs[X1_INDEX]
        x2 = case_config.inputs[X2_INDEX]

        # ---- 段 1：列表内 dtype 归一 ----
        # 文档「该参数中所有 Tensor 数据类型保持一致」。ATK 逐元素独立抽 dtype，
        # 不归一就会出现混合列表，NPU 侧整批假失败。
        dtype = x1[0].dtype
        for tensor in x1:
            tensor.dtype = dtype

        # ---- 段 2：两个列表等长 ----
        # ATK 两侧独立抽 tuple_numbers，长度取较小者。切片整段赋值是序列参数
        # 改长度的正解（plugin-authoring.md「case_config 的结构」）。
        length = min(len(x1), len(x2))
        x1[:] = x1[:length]
        x2[:] = x2[:length]

        # ---- 这里插你自己的场景构造（高秩 / large / 异形列表）----
        # **不要写 x1[:] = x1[:1]**：截成单元素列表会让这一整档的
        # 「列表长度」轴塌成 len1，多元素列表 × 该场景一条都不剩。
        # 要改形状就逐元素改：
        #     for tensor in x1:
        #         tensor.shape = list(shape)

        # ---- 段 3：x2 整表对齐 x1 ----
        # 文档「x2 的数据格式和 shape 与 x1 一致」。放在最后，
        # 这样前面对 x1 的任何改动都会被带过来。
        x2[:] = x1[:]

        return case_config
