"""ForeachMulList 的 CPU 标杆执行器。

为什么需要它：aclnnForeachMulList 的 out 是**一个** aclTensorList（张量列表），
而 ATK 默认执行器把基线返回的 list 当成逐元素输出，golden 的 output_info.json
会写成 [[{t0}], [{t1}], ...]；NPU 侧据此为每个元素各建一个 aclTensorList，
aclnn 第一段接口拿到 4+n 个参数，参数个数对不上，全部用例执行失败。

这里把 n 个结果张量包成「列表里一个元组」[(t0, ..., tn)]：ATK 的
get_output_data_infos 对列表逐项递归再整体 append，恰好得到每用例一项、
内含 n 个张量描述的 [[{t0}, ..., {tn}]]，NPU 侧据此建**一个** out 张量列表。
数值上逐张量就是 torch.mul（torch._foreach_mul 的语义，y_i = x1_i * x2_i）。
"""

import torch

from atk.configs.dataset_config import InputDataset
from atk.tasks.api_execute import register
from atk.tasks.api_execute.base_api import BaseApi


@register("function_foreach_mul_list_cpu")
class FunctionForeachMulListCpu(BaseApi):
    def __call__(self, input_data: InputDataset, with_output: bool = False):
        x1, x2 = input_data.args[:2]
        result = [torch.mul(a, b) for a, b in zip(x1, x2)]
        return [tuple(result)]
