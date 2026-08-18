"""CPU golden 插件模板：基线接口名或返回结构与默认路径不一致时才需要。

默认路径够用就别写：`case.name` 能直接调通、返回值就是要比的输出时，
ATK 自己就能算 golden。

需要写的典型情形：
- 基线函数名和 `case.name` 不同
- 基线返回 namedtuple / 多输出，只有其中一个参与精度比对
- 基线对某个 dtype 没有直接实现，需要一层轻适配

文件名必须以 `function_` 开头，且与 case 文件同目录——ATK 只按这个规则自动加载。

加载到了不等于用上了。ATK 按 YAML 的 `api_type` 取执行器（默认 `function`，
就是内置那个），所以注册名必须同时写进声明文件的 `yaml` 块：

    "yaml": {..., "api_type": "example_cpu"}   ←→   @register("example_cpu")

两边对不上时 ATK 报「请检查用例yaml中的api_type字段是否有对应标杆API文件」。

注册名里不要出现 `tensor` 或 `method`：ATK 拿 `api_type` 做子串匹配决定要不要
再装载 `tensor_input.bin` / `method_input.bin`，没有对应输入组就是 FileNotFoundError。
"""

import torch

from atk.configs.dataset_config import InputDataset
from atk.tasks.api_execute import register
from atk.tasks.api_execute.base_api import BaseApi


@register("example_cpu")
class ExampleCpu(BaseApi):
    def __call__(self, input_data: InputDataset, with_output: bool = False):
        # args 来自 name 为空的输入，kwargs 来自 name 非空的输入。 # [L0:binding.inputs]
        # 所以 YAML 输入名必须等于基线函数形参名，否则这里直接 TypeError。
        result = torch.sum(*input_data.args, **input_data.kwargs)
        # 多输出基线在这里挑出参与比对的那个，例如 result.values。
        return result
