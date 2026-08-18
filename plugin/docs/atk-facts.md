<!-- 从 CLAUDE.md 抽出：参考数据，不进每次会话的上下文。维护 skill 时按需读。 -->

# ATK 事实基线

## 用例生成

| 事实 | 依据 |
| --- | --- |
| 总数 = `max(各输入 dtype 数) × dtype_numbers` + extra | `base_generator.py:196-204` |
| 各输入独立采样后按序 zip，**无跨输入笛卡尔积** | `base_generator.py:151-190` |
| seed 默认 1234，可复现 | — |
| 默认输出 `result/<yaml名>/json/all_<yaml名>.json` | `reports.py:369-381` |
| config 模型 `extra='forbid'`，多写字段直接 ValidationError | `design_config.py` |
| `dim_numbers` 默认 `[1..8]` | `design_config.py:51` |
| `max_length` 是**字节**预算不是元素数，默认 4GB/张量 | `parameter_tensor.py:41-51`（常量名 `MAX_NUMBER_OF_ELEMENTS` 是误导） |
| 上边界用例硬编码 `shape[i] = 2**31+1`，不受 `max_length` 约束，大轴位置轮转 | `parameter_extra_tensor.py:176-180` |
| 该值不合精度标准：§1.2 的 2²⁰ 是**维度值**上限，2³¹ 是**总元素数**上限，ATK 混用还加 1 → 单卡 OOM。constraint 必须夹回 2²⁰ | `experimental_standard.md` §1.2/§1.4 |
| ATK 只重掷不裁剪 shape：超限整条重抽，300s 超时报错 | `parameter_tensor.py:143-150` |
| `[[a,b]]` 在 `dim_values`/attr 是闭区间取整；在 tensor `ranges` 是数据范围 | `clc_interval` |
| 非 tensor 类型写 `boundary` 直接报错 | `design_config.py:369` |
| `ranges.valid` 不写全 → 正态取默认 `mean=[-100,100] std=[1,25]`，不符标准 | 静默坑 |
| `shape_distributions` 默认大 shape 配额，YAML 产不出就**死循环**；`[[0,1.0]]` 关闭，`[]` 崩 `ZeroDivisionError` | 静默坑 |

## YAML `outputs` 字段

| 事实 | 依据 |
| --- | --- |
| 不写（`None`）→ ATK 取函数**返回值** | `api_execute/function_api.py` |
| 非 `None` → ATK **丢弃返回值**，从 `input_data.args[outputs]` 取（in-place 语义） | `api_execute/base_api.py:32` |
| 多输出/非 in-place 写了它 → `IndexError`，被 `opp_tasks.py` 吞成 `RuntimeError: run opp task failed!` | 同上 |

规则：`outputs` 只对「输出张量由调用者传进 args/kwargs」的 in-place 算子声明索引，其余不写。

## 精度判定

| 事实 | 依据 |
| --- | --- |
| `default` == `mixed_tolerance_bm`，同类两个注册名 | — |
| **标杆含 nan/inf → 无条件判该用例通过** | `mixed_tolerance_benchmark_compare.py:207` |
| 整型输出自动回落旧标准，不需另指定 `equal` | 同上 `:150` |
| 回落后：int8→绝对误差≤1；uint8/int16/int32/int64→二进制一致；bool→逐元素相等 | `single_benchmark_compare.py:75,157,182` |
| case JSON 的 `standard.acc` 可用字典调松阈值 → **验收必查** | 同上 `:49` |
| ATK 代码的 hf32/fp8 阈值与真源不符 | 真源见 skill 的 `experimental_standard.md` |

## 性能

| 事实 | 依据 |
| --- | --- |
| warmup 300 次与 10 秒先到者为准 | `backend.py:65,299-310` |
| `per_times = min(max(1, 10.0/单次耗时), 20)` | `backend.py:179` |
| 波动校验 `CQV = IQR/median×100 > 30` 判不稳 | `npu_backend.py:135-151` |
| 不稳自动重测最多 3 次，只对 NPU/PYACLNN/ACLNN 生效 | `opp_executor.py:66,196` |
| **性能达标计算完全不看波动结果** | `perf_compare_tool.py:77-87` |
| `ratio = bm_perf / base_perf`，**无零值保护**（empty 用例会踩） | 同上 `:32` |
| `key` / `not_key` / `DSA` 三档数值完全相同 | `standard_config.py` |

## 后端与插件

| 事实 | 依据 |
| --- | --- |
| `aclnn` 与 `pyaclnn` 是**两个不同后端** | `nodetype_config.py` |
| `aclnn` 需编译好的 `aclnnTest` pybind 模块，按 `case.name` 绑定 | `aclnn_backend.py:66-85` |
| `aclnn_api_type` / `AclnnBaseApi` 只被 pyaclnn 消费 | `pyaclnn_backend.py:150` |
| `atk aclnn` 映射到哪个后端**随版本变** | `op_alias.py:31-34` |
| 插件自动加载只认 `function_*.py`，且只在 case 文件所在目录找 | `op_alias.py:161-166` |
| 插件目录只在 import 阶段临时进 `sys.path`，本地依赖须 import 期读完 | — |
| `atk` 包**没有** `__version__`，用它检查会误判 ATK 不可用 | `atk/__init__.py` |
| `561103` = `ACLNN_ERR_INNER_NULLPTR` | `aclnn/opdev/op_errno.h:24` |

## 报告

| 事实 | 依据 |
| --- | --- |
| ATK **无机器可读结论产物**，只有 xlsx/csv/控制台 | — |
| 节点级列带后端前缀（`cpu_0_精度通过`），用例级列不带（`编号`） | `report_title/` |
| 常规精度判定填在对比节点列，待验收算子节点整列 `-` → 按「哪列有数据」选 | — |
| **error-match 用例的判定填在待验收算子节点列**，常规用例填在基准节点列 → 单列读取必漏 | median 跑测：ATK 自报 83.33%，单列解析得 80.56% |
| 全角半角括号混用：`Device性能（us）` 全角、`端到端性能(us)` 半角 | 按原样匹配 |
| 三个内存列中文名带**前导空格**，与带前缀同名列语义不同 | — |
| 官方文档写「精度通过率」，实现叫「通过率」 | 以 skill 的 `reporting.md` 为准 |

只写**经过 ATK 框架源码验证的事实**，推测不写。

新增事实必须带可点击依据：`atk/configs/design_config.py:85`。

验收执行期间**不读**本文件涉及的 ATK 源码，只读本文件。

## 插件基类（2026-08-15 在 1.2 逐条 import 核实）

| 事实 | 依据 |
| --- | --- |
| `CaseGenerator.generate()` 里 `self.index` 在 `return after_case_config(case)` **之前**自增 → 取本条组合用 `self.index - 1` | `case_generator/generator/base_generator.py:151-190` |
| ATK 源码自注：`process_shape_restrict` 用 while，**可能死循环**，「想要避免，用户需要重写 generate」 | 同上 ~163 行注释 |
| `after_input_config(index, input_case)` / `after_case_config(case)` 是设计好的两个钩子；`generate()` 的 docstring 明说可覆写 | 同上 |
| `_get_case_numbers()` 默认 `max(各输入 dtype 数) × dtype_numbers`，覆写成 `len(必测集)` 即枚举模式 | 同上 |
| `CaseConfig` 字段：`id` `default_seed` `name` `aclnn_name` `triton_name` `kernel_name` `version` `expected_error_msg` `api` `api_type` `aclnn_api_type` `backward` `standard` `outputs` `inputs` `method_inputs` `tensor_input` `compute_times` `save_name` `is_boundary` `strategy` 等 | `configs/case_config.py:80` |
| **`CaseConfig` 没有 `coverage_tags`**，赋值直接 `ValueError: "CaseConfig" object has no field` | 实测 |
| `InputCaseConfig` 字段：`name` `type` `required` `dtype` `shape` `range_values` `backward` `align_32B` `outlier_values` | `configs/case_config.py:44` |
| `BaseApi.__call__(input_data: InputDataset, with_output: bool = False)` 是抽象方法；`self.output = case_config.outputs` | `tasks/api_execute/base_api.py` |
| `InputDataset` 字段：`args` `kwargs` `method_args` `method_kwargs` `tensor_args` `require_grad` `slice_contiguous` 等 | `configs/dataset_config.py:50` |
| `GENERATOR_REGISTRY` 在 `case_generator.generator.generate_types`（是**包**不是模块文件） | 实测 import 通过 |

## aclnn 执行器（同批核实）

| 事实 | 依据 |
| --- | --- |
| `AclnnBaseApi.init_by_input_data` 返回 `(input_args, output_packages)`；`input_args = [各输入 convert_input_data 展开...] + output_packages` | `tasks/api_execute/aclnn_base_api.py` |
| **一个 YAML 输入可能展开成多个 arg**（`convert_input_data` 返回 list 再 `extend`）→ 不能用魔数比 `len(input_args)` 定位 | 同上 |
| `output_packages` 由 `task_result.output_info_list` 逐条 `convert_output_data` 得到，元素是 `AclTensorStruct(tensor, addr, shape, dtype)` | 同上 |
| **workspace 与 executor 由后端固定追加**（`uint64_t* workspaceSize`、`aclOpExecutor** executor`），薄壳不得自己补 | `tasks/backends/pyaclnn_backend.py:375` |
| 三个覆写点：`init_by_input_data`（入参）、`after_call`（出参回收）、`get_cpp_func_signature_type`（签名自检） | `aclnn_base_api.py`、`pyaclnn_backend.py:380` |
| 默认 `aclnn_function`（`AclnnFunctionApi`）是纯 super 转发，没有额外逻辑 | `tasks/api_execute/function_api.py:61` |
| 运行时契约表 = `CPP_TO_PYTHON_TYPE`（33 条，`acl_wrapper`）+ `PYTYPE_TO_CTYPE`（14 条，`pyaclnn_backend`） | 实测条目数 |
