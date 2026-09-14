# ATK 能力面

**本文只回答「有没有这个东西」，不回答「怎么用」。** 用法在别的 reference，
清单里标「未展开」的去读源码。

ATK 是钉死版本的 submodule（26.8.8），所以下面是**常量不是猜测**。
升版本时重跑「重查流程」那两条命令，替换输出。

## 清单外的处理

| 处境 | 怎么做 |
| --- | --- |
| 清单里有，`references/` 写了用法 | 用 `references/`，**不要翻源码** |
| 清单里有，`references/` 没写用法 | **去读那个字段/类型的源码**，读完用 `gen_cases.py --dry-run` 小规模验一次 |
| 清单里没有 | 重跑下面的命令确认。确实没有就是 ATK 不支持，**如实报告，不要绕** |

**YAML 里写错键名会直接报错**，不会静默吞掉：`DesignConfig` 与 `InputDesignConfig`
都是 `extra='forbid'`（`atk/configs/design_config.py:441, 340`）。所以拿不准的键
可以写进去跑一次 dry-run，报错本身就是答案。

## 参数类型：YAML 能写 9 种

`type:` 字段的合法取值（`design_config.py:28`，写错报 `is not a valid type`）：

| `type` | 是什么 | 用法在哪 |
| --- | --- | --- |
| `tensor` | 单个张量 | yaml-authoring.md |
| `tensors` / `tensor_tuple` | **张量列表，个数可变**（`aclTensorList*`） | yaml-authoring.md「动态输入」 |
| `scalar` | 单个标量（`aclScalar*`） | yaml-authoring.md |
| `scalars` / `scalar_tuple` | 标量列表 | **未展开**，同 `tensors` 用 `tuple_numbers` 控个数 |
| `attr` | 单个属性（`int64_t`、`bool`、枚举） | yaml-authoring.md |
| `attrs` / `attr_tuple` | 属性数组（`aclIntArray*`） | yaml-authoring.md |

`_tuple` 后缀的差别只有一处：生成的数据是 `tuple` 不是 `list`
（`parameter_tensors.py:49`）。

注册表里还有 `extra_tensor` / `extra_tensors` / `extra_tensor_tuple` / `sample`
四个，**不能写进 `type:`**——前三个由 `extra_numbers` 自动派生，`sample` 是别的路径。

## 顶层字段：27 个

| 字段 | 一句话 | 用法在哪 |
| --- | --- | --- |
| `api` `api_type` `aclnn_api_type` `generate` | 执行方式与约束器的注册名 | yaml-authoring.md |
| `name` `aclnn_name` `version` | 接口名 | yaml-authoring.md |
| `inputs` | 输入参数列表 | yaml-authoring.md |
| `outputs` | 社区算子固定 `null` | yaml-authoring.md |
| `dtype_numbers` `extra_numbers` | 用例条数与边界用例总闸 | case-strategy.md |
| `standard` | 精度与性能标准 | precision-standard.md |
| `size_distributions` | 按字节配比生成 | case-strategy.md「规模档」 |
| `shape_distributions` | 按元素数配比，**与 `size_distributions` 互斥** | case-strategy.md |
| `tensor_input` | 单独一个主张量，与 `inputs` 并列 | **未展开** |
| `method_inputs` | 方法调用形态的入参（`Tensor.xxx()` 那种） | **未展开** |
| `shape_inputs` | 配 `generate: catccos` 用的 M/K/N 表 | **未展开**，矩阵类算子才有 |
| `dt_config` | `catccos` 的配套配置 | **未展开** |
| `compute_times` | 重复计算次数 | **未展开** |
| `backward` | 反向算子开关，缺省 `false` | **未展开**，社区任务都是前向 |
| `expected_error_msg` | 期望的报错文本，用于负向用例 | **未展开** |
| `triton_name` `triton_api_type` `kernel_name` `kernel_api_type` `fusion_api_type` `dist_api_type` | 别的后端，社区算子用不到 | 不用管 |

## inputs 每一项：13 个字段

| 字段 | 一句话 | 用法在哪 |
| --- | --- | --- |
| `type` | 上面 9 种之一 | 本文 |
| `name` `required` | 参数名与必填标记 | yaml-authoring.md |
| `dtypes` | dtype 候选 | yaml-authoring.md |
| `ranges` | 取值范围，含 `valid` / `invalid` / `valid_weights` | yaml-authoring.md（`invalid` 与 `valid_weights` **未展开**） |
| `shapes` | `dim_numbers` / `dim_values` / `max_length` | case-strategy.md |
| `boundary` | 5 个 `has_*` 分项，**只有 tensor 类能设**，别的类型设了直接报错 | yaml-authoring.md |
| `tuple_numbers` | 列表长度，**只有 6 种列表类型能设**，别的设了报错 | yaml-authoring.md |
| `align_32B` | 总字节数是不是 32 的倍数，缺省 `None`。**与非连续无关** | case-strategy.md |
| `outlier_values` | 离群值，**必须正好两个**，否则报错 | **未展开** |
| `aclnn_name` `backward` `expected_error_msg` | 同名顶层字段的逐参数覆盖 | **未展开** |

`RandomConfig`（`dtypes` / `tuple_numbers` / `dim_values` 都是它）有 6 个字段：
`values` `type` `weights` `random_types` `custom_dist_ratio` `invalid_values`。
后四个**未展开**。

## 约束器基类：4 个

`generate:` 的合法取值是 `default` 加上你自己注册的名字。ATK 自带
`Default` / `default` / `catccos` / `reduce`——`catccos` 是矩阵类，`reduce` 是归约类，
两者都**未展开**，社区算子目前都用 `default` 派生。

## 重查流程

参数类型与约束器基类：

```bash
python - <<'PY'
import importlib, pkgutil
import atk.case_generator.generator.parameter_types as P
for m in pkgutil.iter_modules(P.__path__):
    importlib.import_module(f"{P.__name__}.{m.name}")
from atk.case_generator.generator.parameter_types import PARAMETER_REGISTRY as R
print(sorted(R.get_register_keys()))
PY
```

YAML 字段面：

```bash
python -c "
from atk.configs import design_config as D
for n in ('DesignConfig','InputDesignConfig','InputsShape','BoundaryConfig',
          'RangeConfig','RandomConfig','StandardConfig','InputSize'):
    print(n, sorted(getattr(D, n).model_fields))"
```

**跑之前要 `export TORCH_DEVICE_BACKEND_AUTOLOAD=0`**，否则装了 torch_npu 的机器
没 source CANN 时 `import torch` 抛 RuntimeError。

清单出处：ATK 26.8.8 真机实测，2026-08-27。
