"""Bernoulli 的 CPU 标杆执行器——只给形状，不给值。

为什么需要它：本算子的精度基线是 CANN 内置的 aclnnBernoulli（facts.json 的
accuracy.kind=builtin），冻 golden 时 pyaclnn 节点算不出 out 该多大——aclnn 的
out 是第一段接口的入参，调用方要先申请好。ATK 只能从标杆节点的返回值拿输出的
shape 与 dtype，所以这一轮必须带一个 cpu 节点。

它返回的**值没有意义**，只有 shape 与 dtype 有意义：随机数算子的 CPU 与 NPU
随机流本来就不同，torch 侧再怎么算也不可能与 NPU 逐位一致。golden 里
cpu_* 那份是废数据，基线是 pyaclnn_* 那份，manifest.json 的 baseline_dir 记着。

torch.bernoulli 接不住 (self, prob, seed, offset) 这四个参数，而且 int/bool
dtype 上根本不能调。zeros_like 是表达「只要形状」最直白的写法。

**这个执行器只在 accuracy.kind=builtin 时成立。** 换回 torch 基线要重写它。
"""

import torch

from atk.configs.dataset_config import InputDataset
from atk.tasks.api_execute import register
from atk.tasks.api_execute.base_api import BaseApi


@register("function_bernoulli_cpu")
class FunctionBernoulliCpu(BaseApi):
    def __call__(self, input_data: InputDataset, with_output: bool = False):
        self_t = input_data.args[0]
        return torch.zeros_like(self_t)
