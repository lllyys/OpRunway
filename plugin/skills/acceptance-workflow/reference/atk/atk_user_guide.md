# ATK 最小闭环手册

目标：仅保留生成新算子 ATK 用例所必需的信息，使后续只靠这份手册就能写出：

- YAML
- `generate` 约束生成器
- `api_type` CPU/NPU 执行器
- `aclnn_api_type` pyaclnn 执行器
- `atk case`
- `atk node -b cpu task`
- `atk pytorch`
- `atk aclnn`

这份手册只解决“当前环境 ATK 版本怎么最小闭环”，不覆盖 ATK 全量功能。


## ATK 环境检查与降级方案

> 本 Skill 强依赖 ATK 完成用例 JSON 生成。在 Phase A 流程起点（环境确认之后），必须检查 ATK 是否可用并按情况降级。

### 检查方法

```bash
python -c "import atk; print(atk.__version__)" 2>&1
```

### ATK 可用

正常执行 Phase A 全流程，包括 `atk case` 生成用例 JSON。

### ATK 不可用（降级模式）

**降级执行**：仅生成 YAML/Constraint/执行插件源码，跳过 `atk case` 生成。必须用 `AskUserQuestion` 明确提示用户：

> 「检测到当前环境未安装 ATK pip 包。已生成源码文件（YAML/Constraint/执行插件），但**未生成用例 JSON**。请在 ATK 环境中执行以下命令完成用例生成：
>
> ```bash
> cd atk_test/test_<op>
> atk case -f test_<op>.yaml -p <op>_constraint.py
> ```
>
> 生成完成后，将 `result/<op>/json/all_<op>.json` 连同源码文件一并移交至 NPU 环境执行 Phase B。」

---

**1. 最小目录**

建议每个算子一个目录：

```ini
├── atk_test/
│   ├── atk-dev/                  # ATK 框架源码
│   └── test_<op>/                # 目标目录（需创建）
│       ├── test_<op>.yaml        # ATK 用例配置
│       ├── execute_<op>.py        # 自定义 API 执行插件
│       └── <op>_constraint.py    # 约束生成器
```
---

**2. 顶层 YAML 最小字段**

```yaml
name: torch.xxx
api: pytorch
api_type: my_api
version: v2.1
generate: my_generate
dtype_numbers: 30
extra_numbers: 8
standard:
  acc: mixed_tolerance_bm
  perf: not_key
# 禁止写 [] 
shape_distributions:
  - [0, 1.0]
inputs:
  - name: input
    type: tensor
    dtypes:
      values: [bf16, fp16, fp32]
```

当前版本真实可用的常用顶层字段：

- `name`
- `aclnn_name`
- `version`
- `expected_error_msg`
- `api`
- `api_type`
- `aclnn_api_type`
- `generate`
- `standard`
- `backward`
- `outputs`
- `inputs`
- `tensor_input`
- `method_inputs`
- `shape_distributions`
- `dtype_numbers`
- `extra_numbers`

注意：

- `DesignConfig` 是按文件路径加载，不是 `DesignConfig(**dict)`
- schema 是 `extra='forbid'`，乱写字段会直接报错
- 若后续要跑 `pyaclnn`，YAML 还必须正确填写 `aclnn_name`
- `shape_distributions` 极易导致死循环，默认必须写 `[0, 1.0]`
- `sdtype_numbers` 用于限制每个dtype取的用例数


**3. `inputs` 最小可靠写法**

当前版本支持的参数类型：

```text
tensor, tensors, tensor_tuple,
scalar, scalars, scalar_tuple,
attr, attrs, attr_tuple
```

`InputDesignConfig` 常用字段：

- `name`
- `type`
- `required`
- `dtypes`
- `ranges`
- `shapes`
- `tuple_numbers`
- `boundary`

常见 dtype：

- tensor/scalar: `fp16`, `bf16`, `fp32`, `int32`, `int64`, `bool`
- attr: `float`, `int`, `string`, `attr_bool`


**4. 最关键的 YAML 细节**

`dim_values` 不能写裸二维列表，必须符合 `RandomConfig`：

```yaml
shapes:
  dim_numbers:
    values: [2]
  dim_values:
    values: [[1, 64]]
  max_length: 4096
```

> **⚠️ 禁止 `range: [1, N]` + `step: M`（防止生成卡死）**：ATK 会按笛卡尔积展开所有维度组合，导致用例数爆炸。必须使用 `values` 离散列表方式。
>
> `dim_numbers` 默认值为 `[1, 2]`,取值应基于算子源码实际处理的维度 index，而非算子声明支持的最大维度数。若算子支持 8 维但实际代码按 ND（2 维）计算，则 `dim_numbers: values: [1, 2]` 即可。
> `max_length` 用于限制张量最大长度，默认值为 17179869184（2的34次方）。

张量范围：

```yaml
ranges:
  valid:
    values: [[-5, 5]]
```

属性范围：

```yaml
ranges:
  valid:
    values: [0, 1]
```

边界用例：

```yaml
boundary:
  has_empty: true
  has_infnan: true
  has_lower_border: true
  has_upper_border: false
```

说明：

- `boundary` 只用于 `tensor/tensors/tensor_tuple`
- 非 tensor 类型不要写 `boundary`


**5. `attr` / `attrs` / `attr_tuple`**

真实语义：

- `attr` -> 单个标量
- `attrs` -> `list`
- `attr_tuple` -> `tuple`

如果想生成结构化属性，不能把多个值直接塞进单个 `attr` 的 `range_values`。

错误示例：

```yaml
- name: normalizedShape
  type: attr
  dtypes:
    values: [int]
  ranges:
    valid:
      values: [[32, 16]]
```

这种写法会被当成单个 `attr`，而不是 tuple。

结构化 attr 的判断方法：

- 看生成后的 JSON
- 看 `OpsDataset` 还原后的真实输入


**6. 约束生成器最小接口**

YAML 顶层 `generate` 必须和注册名一致。

最小模板：

```python
from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.case_generator.generator.base_generator import CaseGenerator
from atk.configs.case_config import CaseConfig


@GENERATOR_REGISTRY.register("my_generate")
class MyGenerator(CaseGenerator):
    def after_case_config(self, case_config: CaseConfig) -> CaseConfig:
        return case_config
```

`CaseConfig` 最常用字段：

```python
case_config.inputs[i].name
case_config.inputs[i].dtype
case_config.inputs[i].shape
case_config.inputs[i].range_values
```

约束生成器只做三类事：

- 修 dtype
- 修 shape
- 修 attr 的 `range_values`

约束来源优先级：

1. 算子源码硬校验
2. 算子文档 shape/dtype 约束
3. ATK 自身生成限制


**7. 执行器最小接口**

YAML 顶层 `api_type` 必须和注册名一致。

如果只跑 CPU / `atk node -b npu task`，写 `api_type` 执行器即可。

如果要跑 `atk aclnn` / `pyaclnn`，还必须提供：

- `aclnn_name`
- `aclnn_api_type`

最小模板：

```python
from atk.tasks.api_execute import register
from atk.tasks.api_execute.base_api import BaseApi
from atk.configs.dataset_config import InputDataset


@register("my_api")
class MyApi(BaseApi):
    def __call__(self, input_data: InputDataset, with_output: bool = False):
        if with_output:
            return ...
        return None
```

`InputDataset` 常用字段：

- `input_data.args`
- `input_data.kwargs`
- `input_data.method_args`
- `input_data.method_kwargs`
- `input_data.tensor_args`

CPU 执行器建议：

- 尽量用 PyTorch 原生算子写 golden
- 低精度输入先转 `fp32` 算，再 cast 回目标 dtype
- 不依赖 NPU 专属类

NPU 执行器最小要求：

- 同一个 `api_type` 插件里按 `self.device` 分支
- `self.device == "npu"` 时走 NPU 路径
- `self.device == "cpu"` 时走 CPU golden 路径
- 若 NPU 路径需要导入算子发布物，先 `sys.path.insert(0, publish_path)`

最小分支模板：

```python
import sys
import torch
from atk.tasks.api_execute import register
from atk.tasks.api_execute.base_api import BaseApi


@register("my_api")
class MyApi(BaseApi):
    def __call__(self, input_data, with_output=False):
        if self.device == "npu":
            # sys.path.insert(0, publish_path)
            # return Xxx(...).forward_v2(...)
            ...
        if self.device == "cpu":
            # return torch 原生 golden
            ...
        raise RuntimeError(f"Unsupported device: {self.device}")
```

`InputDataset` 常用字段：

- `input_data.args`
- `input_data.kwargs`
- `input_data.method_args`
- `input_data.method_kwargs`
- `input_data.tensor_args`


**8. pyaclnn 最小接口**

真实 pyaclnn 路径不是走 `api_type`，而是走 `aclnn_api_type`。

最小必需信息：

- `aclnn_name`：不能为空；若没写 `acl` 前缀，ATK 会自动补成 `aclnn<name>`
- `aclnn_api_type`：默认是 `aclnn_function`
- `atk aclnn ...` 默认后端是 `pyaclnn`，默认对比后端是 `cpu`

最小 YAML 片段：

```yaml
name: torch.xxx
api: pytorch
api_type: my_api_cpu_npu
aclnn_name: SomeOp
aclnn_api_type: aclnn_function
```

默认 `aclnn_function` 什么时候够用：

- JSON 里的输入顺序已经和 `aclnnXxxGetWorkspaceSize` 的 C++ 签名一致
- 输出张量可以由 ATK 根据 benchmark 输出自动构造
- 不需要特殊 format / storage_shape / 额外中间输出

这时通常不必自定义 `aclnn_api_type` 插件。

什么时候必须自定义 `aclnn_api_type`：

- pyaclnn 入参与 JSON 输入顺序不一致
- 需要自己组装 list/tensorlist/额外输出
- 需要自定义 format 或 storage_shape
- 需要手工跳过某些非参数输入

最小模板：

```python
from atk.tasks.api_execute import register
from atk.tasks.api_execute.aclnn_base_api import AclnnBaseApi


@register("my_aclnn_api")
class MyAclnnApi(AclnnBaseApi):
    pass
```

如果默认实现不够，再覆写：

- `init_by_input_data`
- `after_call`
- `get_format`
- `get_storage_shape`

这里最关键的源码事实：

- `pyaclnn` 后端会用 `aclnn_name` 去找 `<op>GetWorkspaceSize` 和 `<op>`
- `aclnn_api_type` 注册类需要继承 `AclnnBaseApi`
- `-cp` 会开启 pyaclnn C++ 签名校验


**9. 最小命令**

小规模 dry-run：

```bash
timeout 60 atk case -f test_<op>.yaml -p <op>_constraint.py -dt 1 -en 2
```

生成用例：

```bash
atk case -f test_<op>.yaml -p <op>_constraint.py
```


CPU 单条冒烟：

```bash
atk node -b cpu task \
  -c result/<yaml_name>/json/all_<yaml_name>.json \
  -p execute_<op>.py \
  -s 0 -e 1 \
  -tk accuracy
```

NPU 单条冒烟（仅验证 NPU 能否产出输出，不比对）：

```bash
atk node -b npu --devices 0 task \
  -c result/<yaml_name>/json/all_<yaml_name>.json \
  -p execute_<op>.py \
  -s 0 -e 1 \
  -tk accuracy
```

NPU vs CPU 双端：

```bash
PYTHONPATH=/path/to/test_dir atk pytorch \
  result/<yaml_name>/json/all_<yaml_name>.json \
  -p execute_<op>.py \
  --task accuracy \
  -s 0 -e 1 \
  --devices 0
```

说明：

- `atk pytorch` 默认主后端是 `npu`，对比后端是 `cpu`
- 若同目录下只有一个 `execute_*.py`，可不显式传 `-p`

pyaclnn vs CPU 双端：

```bash
PYTHONPATH=/path/to/test_dir atk aclnn \
  result/<yaml_name>/json/all_<yaml_name>.json \
  -p execute_<op>.py \
  --task accuracy \
  -s 0 -e 1 \
  --devices 0
```

pyaclnn 参数签名校验：

```bash
PYTHONPATH=/path/to/test_dir atk aclnn \
  result/<yaml_name>/json/all_<yaml_name>.json \
  -p execute_<op>.py \
  --task accuracy \
  -s 0 -e 1 \
  --devices 0 \
  -cp
```

**10. `atk case` 输出位置**

默认输出在当前执行目录下，不在 YAML 同目录下：

```text
result/<yaml文件名去后缀>/json/all_<yaml文件名去后缀>.json
result/<yaml文件名去后缀>/csv/<yaml文件名去后缀>.csv
result/<yaml文件名去后缀>/excel/<yaml文件名去后缀><timestamp>.xlsx
```
这点必须注意。最常见误判就是：

- 新 case 生成到了仓库根目录下的 `result/...`
- 实际跑任务时却用了 `atk_test/.../result/...` 里的旧文件

task 前至少确认一次：

1. `atk case` 日志里的 `save case json file: ...`
2. `-c` 指向的是否是这次新文件


**11. 最常见的 7 个坑**

1. YAML schema 错误

- 常见报错：`ValidationError`、`extra fields not permitted`
- 优先检查 `dim_values`、`boundary`、`ranges`

2. 插件注册失败

- `generate` / `api_type` 和装饰器注册名必须一致
- `atk case/task` 的 `-p` 必须传对

3. CPU 冒烟日志里出现 aclnn/ASCEND_TOOLKIT_HOME 报错

- 纯 CPU 冒烟下通常可忽略
- 只要 `cpu_run` worker 正常、最终 `success 1, failed 0` 即可

4. `attr_tuple` 被误写成 `attr`

- 结构化属性必须用 `attr_tuple`
- 否则 case json / dataset 阶段会被当普通标量属性处理

5. NPU 路径和 CPU 路径混写

- `api_type` 插件必须按 `self.device` 分支
- CPU golden 不要走 NPU 类
- NPU 路径缺发布物导入时，`cpu` 常能过，`npu` 会直接失败

6. pyaclnn 缺少 `aclnn_name` 或 `aclnn_api_type`

- `atk aclnn` 不只看 `api_type`
- 少了 `aclnn_name`，后端会直接报错
- `aclnn_api_type` 注册名不对，插件不会被 pyaclnn 使用

7. 误用旧 JSON

- 这是最常见的闭环误判来源
- CPU 冒烟前先核对 `result/.../all_*.json` 的真实路径


**12. NPU/pyaclnn 真机执行前最少检查项**

1. 环境

- `torch_npu` 可导入
- CANN / Ascend 工具链环境变量正确
- 真实设备可见

2. `atk pytorch`

- `api_type` 插件已注册
- NPU 分支能导入算子发布物
- `forward_v2` / NPU 实际接口参数顺序与 JSON 一致

3. `atk aclnn`

- YAML 含 `aclnn_name`
- `aclnn_api_type` 正确
- 如需签名校验，确保 `ASCEND_CUSTOM_OPP_PATH` 或 `ASCEND_OPP_PATH` 可用

4. benchmark 输出

- CPU golden 输出个数、shape、dtype 要能支撑 pyaclnn/NPU 侧构造输出
- 多输出算子先确认返回结构是否一致


**13. `sitecustomize.py` 什么时候需要**

只在当前 PyTorch / ATK 组合出现 `torch.load(..., weights_only=True)` 兼容问题时需要。

最小模板：

```python
import numpy as np
import torch.serialization

SAFE_GLOBALS = [
    np.core.multiarray.scalar,
    np.dtype,
    type(np.dtype(np.float16)),
    type(np.dtype(np.float32)),
    type(np.dtype(np.float64)),
    type(np.dtype(np.int32)),
    type(np.dtype(np.int64)),
    type(np.dtype(np.bool_)),
]

torch.serialization.add_safe_globals(SAFE_GLOBALS)
```

执行时带：

```bash
PYTHONPATH=/path/to/test_dir atk node -b cpu task ...
```


**14. 最小工作顺序**

1. 读算子源码/文档，提取 shape、dtype、attr 约束
2. 写最小 YAML，只定义候选空间
3. 写约束生成器，把跨输入关系修正到合法空间
4. 用 `atk case -dt 1 -en 2` 先生成少量 case
5. 检查生成 JSON 的前几条
6. 写 CPU 执行器
7. 用 `atk node -b cpu task -s 0 -e 1` 做单条 CPU 冒烟
8. 补 `self.device == "npu"` 分支后跑 `atk pytorch`
9. 若需要 pyaclnn，再确认 `aclnn_name/aclnn_api_type` 后跑 `atk aclnn -cp`


**15. 报告输出解读**

`atk pytorch` 默认主后端是 `npu`（被测），对比后端是 `cpu`（golden）。报告中的精度比对结果格式如下：

```
+-------+----------+
| id_XX | accuracy |
+-------+----------+
| npu_0 |    -     |
| cpu_0 |   True   |
+-------+----------+
```

字段含义：

- `npu_0` 旁边的 `-`：表示 NPU 作为被测设备，不作为比对标杆，此位置永远显示 `-`
- `cpu_0` 旁边的 `True`：表示 CPU 作为 golden 标杆，NPU 结果与 CPU 比对通过
- `cpu_0` 旁边的 `False`：表示 NPU 结果与 CPU golden 比对不通过

注意：不要将 `npu_0: -` 误判为 NPU 未执行。确认 NPU 是否实际执行应查看 atk.log 中的 `device_run_0` worker 日志，或检查日志中是否有 `==开始自定义npu api阶段==` 及执行成功的记录。


**16. 这份手册不能替代什么**

- 不能替代算子源码阅读
- 不能替代 NPU 发布物路径和真机环境检查
- 不能替代 pyaclnn C++ 签名核对
- 不能替代新版本 ATK 差异确认
