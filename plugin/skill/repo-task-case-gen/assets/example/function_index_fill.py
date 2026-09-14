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
