# 约束器与执行器

**默认一个都不写。** pyaclnn 从用例 JSON 的输入列表自己拼 aclnn 调用，
CPU 标杆用 `eval(name)(*args, **kwargs)`，标准算子两边都不需要人写代码。

只有下面的判据命中时才写，命中哪条写哪个。

## 判据表

| 判据 | 命中现象 | 写什么 | YAML 里怎么接 |
| --- | --- | --- | --- |
| 参数之间有依赖 | 一个参数的合法取值取决于另一个参数（长度一致、轴范围随秩变、dtype 要对齐） | 约束器 | `generate: <op>_constraint` |
| CPU 标杆不能直接 eval | `torch.<op>` 表达不出算子语义，或参数形态与 torch 不一致 | CPU 执行器 | `api_type: function_<op>_cpu` |
| aclnn 调用形态特殊 | 原地输出、输出不在参数尾部、可选指针切换语义 | NPU 执行器 | `aclnn_api_type: function_<op>_npu` |

**三条都不命中就什么都不写**，`generate` 留 `default`，`api_type` 留 `function`，
`aclnn_api_type` 留 `aclnn_function`。

### 三个目标算子的判定结果

| 算子 | 参数依赖 | CPU 标杆 | aclnn 形态 | 结论 |
| --- | --- | --- | --- | --- |
| roll | `shifts` 与 `dims` 必须等长，`dims` 取值随秩变 | `torch.roll` 收 list，直接可用 | 单输出，常规 | 只写约束器 |
| indexfill | `index` 元素值 < `self` 在 `dim` 维的长度 | **`torch.index_fill` 的 index 只收 int64 Tensor，收不了 list** | 单输出，常规 | 约束器 + CPU 执行器 |
| median | `dim` 取值随秩变 | **`torch.median` 返回具名元组，要摊成两个输出** | 双输出 | 约束器 + CPU 执行器 |

三个算子有两个要写 CPU 执行器，起因都一样：**`aclIntArray*` 在 ATK 里是 python
list，而 torch 的对应参数常常要 Tensor**；以及**多输出算子的 torch 返回值是
具名元组，ATK 要普通元组或 tuple 才能拆成多个输出张量**。

判断办法不是看文档，是直接试一次：

```bash
python -c "import torch; print(torch.index_fill(torch.rand(3,3), 0, [0,2], 0.0))"
```

报 `TypeError: received an invalid combination of arguments` 就是要写执行器。

## 约束器

### API

```python
from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.case_generator.generator.generate_types.default import DefaultGenerator
from atk.configs.case_config import CaseConfig


@GENERATOR_REGISTRY.register("roll_constraint")
class RollConstraint(DefaultGenerator):

    def after_case_config(self, case_config: CaseConfig) -> CaseConfig:
        # 在这里按参数间依赖修正整条用例
        return case_config
```

两个钩子，签名必须逐字对上，写错了不会报错——只是永远不被调用：

| 钩子 | 签名 | 什么时候用 |
| --- | --- | --- |
| `after_input_config` | `(self, index, input_case)` | 只改单个输入，不看其它参数 |
| `after_case_config` | `(self, case_config)` | 参数之间有依赖 |

`after_input_config` 的第二个参数是 `index`（输入序号），**不是** `case_config`。
ATK 自带 skill 的 `workflow.md` 这里写错了，以 `atk/case_generator/generator/base_generator.py:64`
的实际签名为准。

### case_config 的结构

`inputs` 与 YAML 的 `inputs` 同序。**标量类参数是一个对象，`attrs` / `scalars`
这类序列参数是一个列表**——列表长度就是 `tuple_numbers` 抽到的值。

```python
case_config.id                        # 用例序号
case_config.inputs[i].type            # "tensor" / "attr" / "attrs" / "scalar"
case_config.inputs[i].dtype           # "fp16" 之类
case_config.inputs[i].shape           # list[int]，仅 tensor 有
case_config.inputs[i].range_values    # 取值或值域，attr 与 scalar 靠它定值
```

**改具体取值改的是 `range_values`，不是 `value`。** 它既可以是二元值域
`[-8, 8]`（运行时随机取），也可以是一个具体数（运行时就用它）。要点名一个值
就直接赋标量。

序列参数改长度用切片赋值，改元素逐个赋 `range_values`：

```python
dims = case_config.inputs[2]     # 是个 list
dims[:] = dims[:3]               # 截到 3 个
for slot, value in zip(dims, [0, -1, 2]):
    slot.range_values = value
```

改就地改，改完 `return case_config`。

### roll 的约束器（真机验证过，可照抄改名）

```python
"""Roll 约束器：让 shifts 与 dims 等长、dims 落在 [-rank, rank) 且不重复。

约束来自 docs/aclnnRoll.md 的「函数原型」与「返回值」两节。不读 op_kernel / op_host。
"""

import random

from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.case_generator.generator.generate_types.default import DefaultGenerator
from atk.configs.case_config import CaseConfig


@GENERATOR_REGISTRY.register("Roll_constraint")
class RollConstraint(DefaultGenerator):

    def after_case_config(self, case_config: CaseConfig) -> CaseConfig:
        inputs = case_config.inputs
        x, shifts, dims = inputs[0], inputs[1], inputs[2]
        rank = len(x.shape or [])
        if rank == 0:
            return case_config

        # dims 去重并落进 [-rank, rank)：一半写成负轴，覆盖归一化路径
        # 长度取三者最小：shifts 可能比 dims 短，只截不补会留下长度不等的用例
        length = min(len(shifts), len(dims), rank)
        picked = random.sample(range(rank), k=length)
        chosen = [(axis - rank) if index % 2 else axis
                  for index, axis in enumerate(picked)]

        dims[:] = dims[:len(chosen)]
        for slot, value in zip(dims, chosen):
            slot.range_values = value

        shifts[:] = shifts[:len(chosen)]
        for slot, axis in zip(shifts, chosen):
            span = x.shape[axis]
            slot.range_values = random.randint(-span, span)

        return case_config
```

**两个序列参数要等长时，长度取双方与秩的最小值。** 只按一方截断，另一方短了
就补不齐——这条真机上踩过：180 条里有 34 条 `shifts` 比 `dims` 短，
CPU 标杆全部跑不出来。

### case_config.id 在约束器里恒为 0

**不要用 `case_config.id` 分批。** id 是钩子跑完之后才赋的，
`after_case_config` 里看到的每一条都是 0。用它取模，所有用例会命中同一个分支：

```python
# ✗ 每条用例的 id 都是 0，这个条件对全部用例都成立
if case_config.id % 8 == 0:
    shape[axis] = 1
```

真机上这个写法让 Median 的 200 条用例**全部**变成退化维场景，
dim 也全取了正轴——一整套用例集只覆盖了一种形态，而它看起来是 200 条。

要按序号分批就在生成器实例上自己数：

```python
@GENERATOR_REGISTRY.register("Median_constraint")
class MedianConstraint(DefaultGenerator):

    def after_case_config(self, case_config: CaseConfig) -> CaseConfig:
        # id 在这一步还没赋值，自己数
        self._seq = getattr(self, "_seq", 0) + 1
        ...
```

### 显式构造必测场景

随机采样撞不到的场景在这里点名构造，按自己数的序号分批：

```python
def after_case_config(self, case_config):
    self._seq = getattr(self, "_seq", 0) + 1
    x = case_config.inputs[0]
    # 每 8 条挑 1 条改成退化维场景：dim 维长度为 1
    if self._seq % 8 == 0 and x.shape:
        x.shape[-1] = 1
    return case_config
```

**改完一定要抽查生成结果，别只看用例条数。** 条数对不代表形态分布对：

```bash
python -c "
import json, collections
cases = json.load(open('cases.json'))
print(collections.Counter(len(c['inputs'][0]['shape'] or []) for c in cases))
print(collections.Counter(c['inputs'][1]['range_values'] >= 0 for c in cases))
"
```

哪些场景要点名构造见 case-strategy.md「必须单独构造的场景」。

### 约束器不能做的事

| 反模式 | 为什么 |
| --- | --- |
| 把可能失败的用例改成必然通过的形态 | 约束器的职责是产出**合法**用例，不是产出**通过**的用例 |
| 从工程 kernel 的 assert 反推修正逻辑 | 拿被测实现当判据 |
| 在这里补业务计算逻辑 | 那是执行器的活儿 |

## 执行器

### CPU 侧：类型适配（indexfill 真机版）

```python
"""IndexFillTensor 的 CPU 标杆执行器。

为什么需要它：aclnn 侧 index 是 aclIntArray*，ATK 生成的是 python list；
torch.index_fill 的 index 只收 int64 Tensor，直接 eval 会 TypeError。
这里只做类型适配，不改算子语义。
"""

import torch

from atk.configs.dataset_config import InputDataset
from atk.tasks.api_execute import register
from atk.tasks.api_execute.base_api import BaseApi


@register("function_index_fill_cpu")
class FunctionIndexFillCpu(BaseApi):
    def __call__(self, input_data: InputDataset, with_output: bool = False):
        self_t, dim, index, value = input_data.args[:4]
        index_t = torch.as_tensor([int(i) for i in index], dtype=torch.int64,
                                  device=self_t.device)
        if self_t.dtype == torch.bool:
            value = bool(value)
        elif not self_t.dtype.is_floating_point:
            value = int(value)
        return torch.index_fill(self_t, int(dim), index_t, value)
```

### CPU 侧：多输出摊平（median 真机版）

```python
"""Median 的 CPU 标杆执行器。

为什么需要它：torch.median(x, dim, keepdim) 返回 torch.return_types.median
具名元组，而 aclnn 侧要 valuesOut 与 indicesOut 两个独立输出张量。
这里把具名元组摊成普通元组，让 ATK 按两个输出去组织 aclnn 调用。
"""

import torch

from atk.configs.dataset_config import InputDataset
from atk.tasks.api_execute import register
from atk.tasks.api_execute.base_api import BaseApi


@register("function_median_cpu")
class FunctionMedianCpu(BaseApi):
    def __call__(self, input_data: InputDataset, with_output: bool = False):
        self_t, dim, keep_dim = input_data.args[:3]
        result = torch.median(self_t, dim=int(dim), keepdim=bool(keep_dim))
        return result.values, result.indices
```

注册名要与 YAML 的 `api_type` 逐字相同。CPU 执行器写**数学语义与类型适配**，
不写 aclnn 接口适配。

### NPU 侧

三个目标算子**都不需要**。`AclnnBaseApi` 已经处理 workspace 申请、executor
传递、张量与 acl 结构互转，标准两段式接口不用人写。

只有原地输出、输出不在参数尾部、可选指针切换语义这三种情况才要写：

```python
from atk.configs.dataset_config import InputDataset
from atk.tasks.api_execute import register
from atk.tasks.api_execute.aclnn_base_api import AclnnBaseApi


@register("function_xxx_npu")
class FunctionXxxNpu(AclnnBaseApi):
    def init_by_input_data(self, input_data: InputDataset):
        input_args, output_packages = super().init_by_input_data(input_data)
        # 在这里调整 input_args 的顺序或补 nullptr
        return input_args, output_packages
```

注册名要与 YAML 的 `aclnn_api_type` 逐字相同。**先调 `super()` 再改**，
不要从零重写。

### 两侧写在同一个文件

`function_<op>.py` 里同时注册 CPU 与 NPU 两个类，跑测时一个 `-p function_<op>.py`
两边都加载得到。文件名用 `function_*.py` 是 ATK 简化入口的约定。

## 加载与验证

```bash
# 约束器
atk case -f <op>.yaml -p <op>_constraint.py

# 执行器
atk aclnn cases.json -p function_<op>.py --task accuracy
```

**约束器没被加载不会报错，只会静默走 default。** 验证办法是在钩子里 print 一行，
dry-run 看有没有打出来；确认后删掉 print。

`generate` 字段的值与 `@GENERATOR_REGISTRY.register()` 的名字对不上是最常见的失败，
两处名字逐字比一遍。
