"""aclnn 执行器模板：ATK 默认拼装与提交 C 签名对不上时用。

先确认真的需要它。`align_signatures.py` 报告里 `aclnn_adapter.required == false`
就别写——默认的 `aclnn_function`（`AclnnFunctionApi`）只是纯 super 转发，
多一层薄壳只会多一处出错点。

**只有一次修正机会**：改动、依据和失败类别记进证据目录，第二次失败即停。
所以参数类型一律查运行时契约表，不要枚举常量、不要猜顺序。

## 默认拼装长什么样（AclnnBaseApi.init_by_input_data）

    input_args = [每个输入 convert_input_data 展开的元素...] + output_packages
    output_packages = 每条 task_result.output_info_list 转成的 AclTensorStruct

两件事必须记住：

1. **一个 YAML 输入可能展开成多个 arg**（`convert_input_data` 返回 list 再 extend），
   所以不要用 `len(input_args) == 某个魔数` 判断位置。出参永远在尾部，
   数量等于 `len(output_packages)`，按这个边界切分才稳。
2. **workspace 和 executor 不要自己补**。后端在绑定函数时固定追加
   `uint64_t* workspaceSize` 和 `aclOpExecutor** executor`；薄壳再补一次就错了。

## 三个覆写点，按差异类型选一个

| 差异 | 覆写 |
| --- | --- |
| 入参装配（缺可空出参、顺序不同） | `init_by_input_data` |
| 出参回收（输出包结构或顺序不符） | `after_call` |
| 签名类型自检 | `get_cpp_func_signature_type`（也可改用 `--cpp_func_signature_type_path`） |

## 运行时契约表

| 表 | 位置 | 内容 |
| --- | --- | --- |
| `CPP_TO_PYTHON_TYPE` | `atk.tasks.backends.lib_interface.acl_wrapper` | C 类型名 → ATK 运行时对象（`aclTensor` → `AclTensor`） |
| `PYTYPE_TO_CTYPE` | `atk.tasks.backends.pyaclnn_backend` | YAML 的 dtype 令牌 → `ctypes` 标量类型 |

typed optional pointer 用 `ctypes.POINTER(CPP_TO_PYTHON_TYPE["<C类型>"])()` 构造。
不要用 `ctypes.c_void_p(0)`——那是无类型空指针，签名校验会拦下。
"""

import ctypes

from atk.tasks.api_execute import register
from atk.tasks.api_execute.aclnn_base_api import AclnnBaseApi
from atk.tasks.backends.lib_interface.acl_wrapper import CPP_TO_PYTHON_TYPE


# 注册名写进 YAML 的 aclnn_api_type。该字段只被 pyaclnn 后端消费。
@register("example_aclnn")
class ExampleAclnn(AclnnBaseApi):
    def init_by_input_data(self, input_data):
        input_args, output_packages = super().init_by_input_data(input_data)

        # 出参在尾部，按 output_packages 的长度切开，不依赖入参个数。
        split = len(input_args) - len(output_packages)
        head, tail = input_args[:split], input_args[split:]

        # 本例差异：C 签名尾部还有一个可空出参，YAML 里没有对应输入，
        # 默认拼装少这一位。位置依据是提交的公开 C 声明（头文件或接口文档），
        # 不是待验收算子实现的内部代码，也不是试出来的。
        # 缺省用 default token，运行期转成 None 再由后端转指针。 # [L0:default_token=default]
        typed_null = ctypes.POINTER(CPP_TO_PYTHON_TYPE["aclTensor"])()
        return head + tail + [typed_null], output_packages

    # 出参结构不符时才覆写这个；只改回收方式，不改数量与顺序语义。
    # def after_call(self, output_packages):
    #     return super().after_call(output_packages)

    # 不要在薄壳里改输入取值、改参数顺序、或吞掉待验收算子的报错——
    # 那会让精度结论失去意义。薄壳只补位置。
