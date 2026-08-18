# ATK 源码事实（十条）

**本文件记录的是直接从 ATK 源码读出的行为事实。** 它不是上游文档的副本或转述，也不包含任何处置规则。
每条只写「ATK 客观如此」加上可复核的 `文件:行号`。

| | |
|---|---|
| ATK 仓 commit | `7220f27e83740cd43b300ed2d4362e12d6cc0d42` |
| 对应 `atk --version` | `26.5.14`（`atk/__init__.py:21`：`PACKAGE_VERSION = "26.5.14"`） |
| 行号基准 | 上述 commit 下的 `atk/` 子树 |

版本一旦不同，本文件整体失效，需回源码逐条重核。行号对得上不等于语义没变。其中第 1 条和第 7 条描述的是
代码缺陷行为，上游修复后结论会反向。

本文件只收录十条，不是 ATK 全部行为的清单。

---

## 1. `single_bm` 的五个阈值覆盖键写入后不生效

**事实**：`fp16_thd` / `bf16_thd` / `fp32_thd` / `fp8e4m3_thd` / `fp8e5m2_thd` 写进 `standard.acc` 的
`single_bm` 配置后，不改变任何阈值，也不抛错。同一个 `update()` 里只有 `mare_ratio` 会真正改到实例属性。
五个 dtype 键在 `__init__` 里已经存在，`dict.setdefault` 对已存在的键是空操作。

`atk/configs/single_benchmark_config.py:29-35`、`:38-55`；配置入口 `atk/configs/design_config.py:378-380`
（`StandardConfig.acc: Optional[Union[str, dict]] = "default"`）；注册名 `single_bm` 在
`atk/tasks/post_process/single_benchmark_compare.py:36`，kwargs 在 `:42-43` 传入 `update()`。

```python
self.threshold = {                      # :29-35 五个 dtype 键此处已建立
    torch.float16: 2 ** -10, torch.bfloat16: 2 ** -7, torch.float32: 2 ** -13,
    torch.float8_e4m3fn: 2 ** -3, torch.float8_e5m2: 2 ** -2,
}
attr_map = {"mare_ratio": None, "fp16_thd": self.threshold.setdefault, ...}   # :39-46
for key, value in kwargs.items():
    if key in attr_map:                 # :48 不在表内的键被静默忽略
        target = attr_map[key]
        if target is None: setattr(self, key, value)   # :50-51 只有 mare_ratio 走这条
        else:              target(torch.__dict__[...], value)  # :52-55 setdefault 空操作
```

## 2. CPU 标杆张量含 nan/inf 时该 case 直接判 `result=True`

**事实**：只要 CPU 标杆输出出现任意 nan 或 inf，该 case 立刻以 `result=True` 返回，不做任何数值比较，
不读 DUT 输出。`single_bm` 与 `mixed_tolerance_bm` 两条比对路径都是这个顺序：先查标杆、再查 DUT。

`atk/tasks/post_process/single_benchmark_compare.py:141-143`、
`atk/tasks/post_process/mixed_tolerance_benchmark_compare.py:206-207`；
`check_invalid_value` 定义在 `atk/tasks/post_process/utils.py:52-56`（`isnan().any() or isinf().any()`）。

```python
if check_invalid_value(remote_output):        # remote_output 是 CPU 标杆
    acc_result = AccuracyConfig(result=True, filename=data_file)
    return acc_result
if check_invalid_value(local_output):         # DUT 的检查排在标杆之后
    ...  result=False
```

产生这类输入的默认配置存在：`BoundaryConfig.has_infnan` 默认 `True`
（`atk/configs/design_config.py:302`），据此调用 `_generate_infnan_cases`
（`atk/case_generator/generator/parameter_types/parameter_extra_tensor.py:143-144`）。

## 3. `mixed_tolerance_bm` 的键名规则与默认阈值

**事实**：键名固定为 `<dtype 前缀>_<属性>`，两部分各取自一个固定集合；任一部分不在集合内时 `update()` 抛
`ValueError`（键里没有 `_` 也抛）。
前缀 6 个：`fp16` `bf16` `fp32` `hf32` `fp8e4m3` `fp8e5m2`；属性 5 个：`rtol` `atol`
`required_matched_ratio` `max_abs_error_limit` `max_ulp_multiple`。默认值：

| dtype | rtol | atol | required_matched_ratio | max_abs_error_limit | max_ulp_multiple |
|---|---|---|---|---|---|
| `fp16` | 1.95e-3 | 1.95e-3 | 0.99 | 1e-1 | 32 |
| `bf16` | 1.56e-2 | 1.56e-2 | 0.99 | 1e0 | 32 |
| `fp32` / `hf32` | 9.77e-4 | 1.53e-5 | 0.99 | 1e-2 | 32 |
| `fp8e4m3` | 1.25e-1 | 1.25e-1 | 0.99 | 5e-1 | 32 |
| `fp8e5m2` | 2.5e-1 | 2.5e-1 | 0.99 | 1e0 | 32 |

`atk/configs/mixed_tolerance_benchmark_config.py`：前缀集合 `:22-29`；属性集合与非法键报错 `:122-142`；
默认值 `:85-120`；`max_ulp_multiple` 缺省 32 在 `:80`。`complex64` 在 `get_threshold` 里被并入 `fp32` 组
（`:185-191`，`normalized == "complex64"` 时改写为 `"fp32"`），没有独立阈值字段。

判定式在 `atk/tasks/post_process/mixed_tolerance_benchmark_compare.py:217-238`：逐元素通过率达标
**并且**每个元素满足绝对误差上限或 ULP 兜底，两者同时成立才 `result=True`。

```python
close_mask = torch.isclose(local_output, remote_output, rtol=threshold.rtol,
                           atol=threshold.atol, equal_nan=False)          # :217-223
matched_ratio = float(close_mask.to(torch.float32).mean().item())         # :224
abs_error_mask = ((abs_error <= threshold.max_abs_error_limit)
                  | (abs_error <= threshold.max_ulp_multiple * spacing_remote))   # :228-231
result = (matched_ratio >= threshold.required_matched_ratio and abs_error_result) # :235-238
```

## 4. tensor 类输入不显式写 `ranges.valid` 时，一半 case 走正态分布

**事实**：对 `tensor/tensors/tensor_tuple/scalar/scalars/scalar_tuple` 六种类型，只要没有**显式**给出
`ranges.valid`，ATK 会追加一条正态分布通道并把配比设成 `"default"`（即 `[[0.5, 0.0], [0.5, 0.0]]`）。
配比第二组之和为 0.5，`nd_number = round(0.5 * len_cases)`，于是一半 case 不再是 `[-5, 5]` 均匀分布，
而是 mean 从 `[-100, 100]`、std 从 `[1, 25]` 各自均匀抽样得到的正态分布。

`atk/configs/design_config.py:335-341`（判据在 `:338`）、`:260-264`（`DefaultCustomDistConfig`）、
`:122-123`（`RandomTypesConfig` 的 mean/std 缺省区间）、`:235-236`（`nd_number` 计算）；
mean/std 的逐 case 抽样在 `atk/case_generator/generator/data_types/data_base.py:146-154`。

```python
if self.type in tensor_like_types and "valid" not in self.ranges.model_fields_set:   # :338
    self.ranges.valid.values = DefaultCustomDistConfig().default_range_values        # [[-5, 5]]
    self.ranges.valid.random_types = [RandomTypesConfig(name=RandomTypes.ND)]        # mean 缺省 [-100,100]
    self.ranges.valid.custom_dist_ratio = "default"                                  # [[0.5,0.0],[0.5,0.0]]
nd_number = round(sum(nd_ratio) * len_cases)                                         # :236
mean = np.random.uniform(low=mean[0], high=mean[1]) if len(mean) >= 2 else mean[0]   # data_base:149-151
```

**同一默认配置下不注入离群值**：`custom_dist_ratio` 两组的最后一位都是 0，
`atk/case_generator/generator/parameter_types/parameter_tensor.py:219-229` 算出的两个 outlier 比例都是
`0.0 / 0.5 = 0`；`:235` 与 `:239` 的判据是 `random.random() <= 0`，`random.random()` 取值域为 `[0.0, 1.0)`，
该条件几乎恒不成立。`default_outlier_values = [0.001, 1000]`（`design_config.py:263`）只有在用户写了非零
配比时才会被注入；正态分布那一半不受此影响。

## 5. aclnn 出参 buffer 按 CPU 标杆输出分配

**事实**：pyaclnn 后端在 `set_input_dataset` 时若 `task_result.output_info_list` 为空即抛 `ValueError`，
即标杆输出是执行的前置条件而不只是比对对象。被测端写入的输出
张量由标杆输出记录的 shape / dtype / stride 经 `torch.empty_strided`（有 stride 时）或 `torch.empty`
在 npu 上分配后交给算子填写，算子本身推导出的输出形状不参与这一步。

`atk/tasks/backends/pyaclnn_backend.py:264-265`、`:311-332`；调用点
`atk/tasks/api_execute/aclnn_base_api.py:69-70` 遍历的正是 `self.task_result.output_info_list`。

```python
if not self.task_result.output_info_list:
    raise ValueError("标杆输出为空，请检查标杆是否运行失败或者没有输出")            # :264-265
if output_data.stride:
    empty_tensor = torch.empty_strided(output_data.shape, output_data.stride,
                                       dtype=torch_dtype, device='npu')          # :322-324
else:
    empty_tensor = torch.empty(output_data.shape, dtype=torch_dtype, device='npu')  # :325-326
```

## 6. 不在 `--save_data` 中的产物被删除，profile 与 output 逐 case 就删

**事实**：`SAVE_DATA_LIST` 五项 —— `profile`、`input`、`input_final`、`output`、`db` —— 凡没有对应
`--save_data` 声明的，都会被删除。删除不只发生在收尾：`profile` 目录在每条 case 解析完性能数据后立刻
`shutil.rmtree`；`output` 与 `profile` 在每条 case 比对完后按节点逐一 `rmtree`。

`atk/configs/base_config.py:27`；收尾清理 `atk/tasks/main.py:242-260`；逐 case 删 profile
`atk/tasks/backends/npu_backend.py:203-209`；逐 case 清理入口 `atk/tasks/executors/compare_excutor.py:127-129`
（`clean_output_data()` 依次调 `_clean_output_data`、`_clean_profile_data`），两者实现在
`:192-217`、`:219-232`，
实际删除动作在 `:112-125` 的 `delete_data`（按 `get_output_path_by_case` 得到的 per-case 目录 `rmtree`）。

```python
SAVE_DATA_LIST = ["profile", "input", "input_final", "output", "db"]           # base_config:27
if (self.task_result.save_data and "profile" in self.task_result.save_data):
    logging.info(f"profiling result save in {profiler_result_path}")           # npu_backend:203-207
else:
    shutil.rmtree(profiler_result_path)                                        # npu_backend:208-209
items_to_keep = [item for item, formats in self.args.save_data.items()
                 if 'bin' in formats or 'txt' in formats]                      # main:255-259
OutputManager.remove_dir_by_env(save_data_list, save_data_list=items_to_keep)  # main:260
```

## 7. 报告里 `accuracy false cases` sheet 恒为空

**事实**：该 sheet 的筛选条件写成了对 pandas Series 做 `is False` 身份比较。`Series is False` 恒为 Python
`False`，与前一个布尔掩码 `&` 之后整张掩码全为 False。无论有多少条精度比对失败，这个 sheet 只会有表头。
同一函数里 `failed cases` sheet 用的是正常的 `==` 比较，不受影响。

`atk/tasks/report/csv_report.py:485-498`。

```python
failed_pd = df_statistic[df_statistic['运行结果'] == 'FAILED']                  # :487 正常比较
accuracy_false_ws = merged_workbook.create_sheet(title="accuracy false cases")  # :492
matched_cols = [col for col in df_statistic.columns if "精度通过" in col]        # :493
if matched_cols:
    target_col = matched_cols[-1]                                              # :495
    acc_false_pd = df_statistic[(df_statistic['运行结果'] == 'SUCCESS')
                                & (df_statistic[target_col] is False)]         # :496 恒 False
```

## 8. ATK 抛异常时进程退出码仍然是 0

**事实**：`atk task success!` 只表示 `MainProcess.run()` 没抛异常，与任何 case 的结果无关。异常被
`except BaseException` 捕获后只记 `atk task failed!` 日志，`run_task` 正常返回，进程退出码依旧是 0。
`KeyboardInterrupt` 走同样的处理。全仓 `grep -rn "sys.exit\|SystemExit\|ctx.exit" atk` 只命中
`atk/__main__.py:31`，任务执行路径上没有任何非零退出。

`atk/bin/base.py:231-253`；`atk/__main__.py:31`。

```python
try:
    process.init_all(); process.run()
except KeyboardInterrupt:
    logging.error("received Ctrl+C. start stop!"); logging.error("atk task failed!")  # :242-244
except BaseException as e:
    logging.exception(f"Error: {str(e)}"); logging.error("atk task failed!")          # :245-247
else:
    logging.info("atk task success!")                                                 # :248-249
```

## 9. device 时间的口径，以及 profiler 缺失时返回 0

**事实**：ATK 报的 `avg_time` 等于 profiler 窗口内 `op_statistic` CSV **所有行**的 `Total Time(us)` 求和，
再除以重复次数——不是被测算子单个 kernel 的时间。重复次数默认由 `get_per_times()` 给出，即
`min(max(1, int(duration_time / once_duration)), 20)`；`duration_time` 初值 10.0，`once_duration` 初值 0.1
且在预热后被改写为实测的单次平均耗时，因此这个次数随机器负载在 1~20 之间变化（调用方显式传入 `count`
时用传入值）。

两处 fail-open，各自只记一条 error 就返回 0：`op_statistic` / `op_summary` 任一 glob 落空返回 `0, 0, []`；
`msprof` 导出失败返回 `0, 0, 0, 0`。

`atk/tasks/backends/npu_backend.py:44-46`（`op_statistic` / `op_summary` / `Total Time(us)` 三个字面量）、
`:231-259`；`atk/tasks/backends/pyaclnn_backend.py:482-484`；重复次数
`atk/tasks/backends/backend.py:61-62`、`:153-154`、`:196`。

```python
if not file_list or not summary_list:
    logging.error("The op_summary is null"); return 0, 0, []          # npu_backend:241-243
for reader in readers:                                                # 遍历 op_statistic 全部行
    total_time_list.append(float(line_dic.get(self.total_time_title)))  # npu_backend:253
total_time = sum(total_time_list)                                     # :256
if count is None: count = self.get_per_times()                        # :257-258
avg_time = total_time / count                                         # :259
return min(max(1, int(self.duration_time / self.once_duration)), 20)  # backend:153-154
if not PyAclnnBackend.export_profile_result(profiler_result_path):
    logging.error("profiling result export failed"); return 0, 0, 0, 0  # pyaclnn:482-484
```

## 10. 不传 `--devices` 时缺省设备集来自 `/dev/davinci*`

**事实**：`atk aclnn` 与 `atk pytorch` 两个别名命令在 `--devices` 为 `None` 时，缺省设备集来自 glob
`/dev/davinci[0-9]*`，再经 `_normalize_default_devices` 规格化为 `list(range(N))`。宿主机上有 8 个设备节点
时，缺省就是 8 个逻辑设备。找不到任何设备节点时记一条 warning 并退化为 `[0]`。

这条路径读的是 `/dev` 下的设备节点，不读任何可见性环境变量：全仓 `grep -rn "VISIBLE_DEVICES" atk` 无命中。

`atk/bin/op_alias.py:31-34`（`DEFAULT_ALIAS_CONFIG` 定义 `pytorch` / `aclnn` 两个别名）、`:45-47`
（`--devices` 缺省 `None`）、`:152-155`、`:172-175`；`atk/common/resource_utils.py:60-81`。

```python
devices = node_kwargs.get("devices")
if devices is None:
    devices = ResourceUtils.get_device_ids()                       # op_alias:172-174
    devices = _normalize_default_devices(devices)                  # :175
def _normalize_default_devices(devices):
    if not devices: return [0]
    return list(range(len(devices)))                               # :152-155
ids = get_devices_by_pattern("/dev/davinci[0-9]*")                 # resource_utils:77
if not ids:
    logging.warning("NPU devices are not found in /dev"); return [0]   # resource_utils:78-80
```
