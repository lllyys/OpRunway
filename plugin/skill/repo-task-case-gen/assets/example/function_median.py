"""Median 的 CPU 标杆执行器。

为什么需要它：torch.median(x, dim, keepdim) 返回 torch.return_types.median 具名元组，
而 aclnn 侧要 valuesOut 与 indicesOut 两个独立输出张量。这里把具名元组摊成普通元组，
让 ATK 按两个输出去组织 aclnn 调用。只做形态适配，不改算子语义。
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
