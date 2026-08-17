# ATK 内部事实（写 design 与插件时要用的）

上游文档不写内部注册表，所以这些只能从代码得到。本文是**派生结论**，不是逐字副本。

**版本绑定：ATK 26.5.14，commit `7220f27`。换版本这一整份都要重核。** 下面每条都标了文件与行号，
核起来是几秒的事；不核就用等于把一份可能过期的结论当事实。

一次真机验收里，「读文档 → 发现答不上 → 逆向这些注册表」花掉 11 分钟，占那一步的 2/3。本文就是为了
把这段时间省掉。

## 目录

- [精度比较器与阈值通路](#精度比较器与阈值通路)
- [mixed_tolerance_bm 的内建默认值与标准不符](#mixed_tolerance_bm-的内建默认值与标准不符)
- [nan/inf：两个比较器行为相反](#naninf两个比较器行为相反)
- [边界用例的生成事实](#边界用例的生成事实)
- [执行桥的三个坑](#执行桥的三个坑)

## 精度比较器与阈值通路

注册了四个名字（`atk/tasks/post_process/`）：

| 名字 | 文件 |
| --- | --- |
| `equal` | `equal_compare.py:24` |
| `single_bm`、`default` | `single_benchmark_compare.py:36-37` |
| `mixed_tolerance_bm` | `mixed_tolerance_benchmark_compare.py:34` |

**阈值写在 design 的 `standard.acc` 里，不用 CLI 参数。** `StandardConfig.acc` 接受字符串或字典
（`configs/design_config.py:378-381`）。写成字典时，字典的值原样展开成比较器构造函数的 `**kwargs`：

```python
# atk/tasks/executors/compare_excutor.py:174-177
acc_name = list(self.task_result.case_config.standard.acc.keys())[0]
return AccuracyFactory.get_accuracy(acc_name)(
    self.config, **self.task_result.case_config.standard.acc[acc_name]
)
```

`mixed_tolerance_bm` 只挑前缀为 `fp16_` `bf16_` `fp32_` `hf32_` `fp8e4m3_` `fp8e5m2_` 的键
（`mixed_tolerance_benchmark_compare.py:45-56`），可用后缀是 `rtol` `atol`
`required_matched_ratio` `max_abs_error_limit` `max_ulp_multiple`。

**键名写错会 `raise ValueError`**（`configs/mixed_tolerance_benchmark_config.py` 的 `update()`），
不是静默忽略——这一点比 `single_bm` 好，后者的 `*_thd` 覆盖键会静默失效。

`complex64` 不需要单独配置：`get_threshold()` 把它归一到 `fp32` 那一列，且
`compute_complex64_mixed_tolerance()` 把实部与虚部分别判定，两边都过才算过
（`mixed_tolerance_benchmark_compare.py:163-186`）。

## mixed_tolerance_bm 的内建默认值与标准不符

`configs/mixed_tolerance_benchmark_config.py` 里的 dataclass 默认值，与
《生态算子开源精度标准》§2.2 逐格对照：

| dtype | 标准 rtol / atol | ATK 内建 rtol / atol | |
| --- | --- | --- | --- |
| fp16 | 1.95e-3 / 1.95e-3 | 1.95e-3 / 1.95e-3 | 一致 |
| bf16 | 1.56e-2 / 1.56e-2 | 1.56e-2 / 1.56e-2 | 一致 |
| fp32 | 9.77e-4 / 1.53e-5 | 9.77e-4 / 1.53e-5 | 一致 |
| hf32 | **1.95e-3 / 9.77e-4** | 9.77e-4 / 1.53e-5 | **不一致** |
| fp8e4m3 | **0.25 / 0.0625** | 0.125 / 0.125 | **不一致** |
| fp8e5m2 | **0.5 / 0.125** | 0.25 / 0.25 | **不一致** |

`required_matched_ratio` 六种都是 0.99，与标准一致。

**所以阈值必须逐字写进 design，不能靠默认值。** 前三种恰好相同，正因如此这个坑很难被发现——
算子只用 fp16/bf16/fp32 时一切正常，一旦碰到后三种就静默用错标准。

## nan/inf：两个比较器行为相反

| 比较器 | 行为 |
| --- | --- |
| `single_bm` | **CPU 真值含 nan/inf → 该 case 无条件判过**，根本不读 DUT 输出。`has_infnan` 默认为 `true`，于是这些用例毫无判别力 |
| `mixed_tolerance_bm` | **NPU 结果含 nan/inf → 判不过**，`error_info` 为 `The NPU result file ... contains nan/inf value`（`mixed_tolerance_benchmark_compare.py:208-211`） |

标准 §1.4 要求 INF/-INF/NAN 对每种 dtype 都覆盖。**这条只有在 `mixed_tolerance_bm` 下才真正成立**；
走 `single_bm` 就必须显式关掉 `has_infnan` 并写明理由，否则是在拿一批必然通过的用例充数。

## 边界用例的生成事实

**上边界是硬编码的 21 亿元素**：

```python
# atk/case_generator/generator/parameter_types/parameter_extra_tensor.py:173-177
elif extra_tensor_type == ExtraTensorType.UPPER_BORDER:
    shape = [1 for _ in range(dim)]
    shape[special_index] = 2 ** 31 + 1
```

不看 `max_length`，没有设备装得下。标准 §1.2 的上边界覆盖在本工具上落不了地——`has_upper_border`
只能关掉，并在 spec 里记 `UNVALIDATED`，不是随手关的。

下边界是各维取 1（`:170-172`）；空 Tensor 是把某一维置 0（`:178-183`）。

各类边界用例的条数由 `case_generator/utils/static_utils.py:235-250` 算出，例如
`upper_border` 与 `empty` 都是 `max(len(dtypes), sum(dim_numbers))`，`infnan` 是 `max(len(dtypes), 4)`。

## 执行桥的三个坑

写 execution plugin 时会撞到，三条都在真机上复现过。

**一、`TORCH_TO_ACLTYPE` 漏了 `torch.uint32`。** `acl_wrapper.py:209-229` 的映射表里有
`uint8` `uint16` `uint64` `complex32` `complex128`，唯独没有 `uint32`；而 `AclDataType.ACL_UINT32 = 8`
存在（`:125`），`ACLTYPE_TO_CTYPE` 也有它（`:239`），`pyaclnn_backend.py:76` 同样有。
**是映射表漏了一行，不是能力缺失**，插件里补上即可。

**二、`bind_function` 按符号名全局缓存 argtypes。**

```python
# atk/tasks/backends/lib_interface/lib_manager.py:96-101
if self.lib_name not in LibManager._func_bindings: ...
if func_name in LibManager._func_bindings[self.lib_name]:
    return LibManager._func_bindings[self.lib_name][func_name]   # 新的 arg_types 被忽略
```

同一符号的第二种调用形态拿到的是第一次的绑定。同进程里混用两种 ABI 形态（例如某参数一会儿传数组、
一会儿传空）必崩，且崩在后面的用例上，看起来像 DUT 问题。要么统一形态，要么按形态拆成独立进程。

**三、可选数组参数缺省时要传零长 `aclIntArray`，不是 NULL。** 传 `c_void_p(0)` 会与上一条的缓存签名
冲突，把后续用例一起带崩。

## 一条读法提醒

上游 `skill/atk-quality-guard/references/` 下也有一份《生态算子开源精度标准》，**已被改动**——
总元素数上限 2^31 写成 2^34，忽略空白后 26 行差异。以 `reference/experimental_standard.md` 为准。
