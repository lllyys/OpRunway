# 性能验收

## 目录

- 前置条件
- 基线
- 选样
- 执行
- c_api 模式的口径边界
- 报告

## 前置条件

性能是 S4 的第二道子门禁，精度裁决完成后才进入。

精度不通过时不执行性能。

性能基线必须来自任务书或用户确认。

**无基线只是不做通过判定，不是不采集。**

任务书没有性能要求时照样跑一轮 `performance_device`，把待验收算子端绝对耗时落盘。

pyaclnn 没有 NPU 基线时用 CPU 节点产出元数据，`device_perf(us)` 就是各节点独立绝对值。

那份数据是交付给验收人员取用的，跳过采集等于本轮性能一节没有任何事实。

S4 必须给出性能状态，取值只有三种：

| 状态 | 触发条件 |
| --- | --- |
| 通过 | 精度通过且性能已执行 |
| 未执行(精度未通过) | 精度裁决为不通过 |
| 未执行(无基线) | 对比基线或绝对门槛均不可得，绝对耗时仍已采集 |

状态为空时 S4 不算完成，不得进入 S5。

状态由 `verdict.py` 从性能产物推导，不由报告作者自己写，见 reporting.md#裁决。

精度通过却拿不出 `performance_results.json` 时 verdict 拒绝裁决。

## 基线

| 模式 | 条件 | 判据 |
| --- | --- | --- |
| 对比 | 可运行 NPU 基线 | 耗时和性能比 |
| 绝对值 | 任务书给出门槛 | device 绝对耗时 |

绝对值模式的门槛记入 `evidence/constraints.md`（本轮的验收约束表），报告直接引用它。

对比模式使用同一批 shape 和 dtype；支持域不一致时取交集并记录排除项。

## 性能集

从精度全量集筛选。

全轮总数固定为 50 条，不是每个分面 50 条。

**选样合并，执行不合并。**

`select_perf_cases.py` 可以用多个 `-j` 把几个分面的用例集一起读进来配额，
50 条是全轮总数，不是每个分面 50 条——这一层照旧合并。

但**跑的时候必须按分面各跑一条命令**。ATK 要求 `(name, id)` 全局唯一
（`atk/tasks/main.py`），而各分面的 `name` 相同、`id` 各自从 0 自增，合成一份
必然撞号，报 `Found duplicate 'name' and 'id' combinations`。冻结输入也按分面
分目录存放，`--input_data` 一次只能指一个目录，改名也救不回来。

所以：一次选样 → 按分面切成几份用例集 → 每份配自己的冻结目录跑一条命令 →
性能产物按分面各自解析，再一起交给 `verdict.py`。

分面怎么切、总数怎么摊，写进 `evidence/constraints.md`。

格子是“字节规模 × dtype 位宽类”。规模轴使用 `input_bytes`：

```json
{
  "branch": {"axis": {"from": "input_bytes", "index": 0},
             "rules": [{"name": "small", "max": 32768},
                       {"name": "medium", "max": 2097152},
                       {"name": "large"}]},
  "dtype_classes": {"fp16": "2B", "bf16": "2B", "fp32": "4B"},
  "size_axis": {"from": "input_bytes", "index": 0}
}
```

`dtype_classes` 是必填键，缺了脚本直接 KeyError；用例集里出现的每个 dtype 都要有条目。

三档都要有用例。

```bash
<python> scripts/select_perf_cases.py -j <case-json> [-j <case-json>] \
  -g perf_grid.json -n 50 -x evidence/excluded_cases.json \
  -o cases/perf.json --manifest conclusion/perf_selection.json
```

优先取大档和中档；小档超过 10 条时记录性能覆盖缺口。

排除空张量、非有限输入、边界用例和已剔除用例。

可选用例不足 50 条时少取，不用重复规格静默回填。

## 执行与呈现

使用与精度相同的待验收算子后端和 `performance_device --fluctuation_check`。

无基线时执行拓扑不变，只是不做对比判定：

```bash
<python> scripts/run_atk_task.py -o evidence/performance.log -- \
  <atk-cli> node -b <backend> --devices <device> \
  node -b cpu task -c cases/perf.json -tk performance_device \
  --fluctuation_check --input_data <frozen> --cpp_func_signature_type_path
```

产出的 xlsx 交给 `parse_atk_report.py`（省略 `-c`）落成 `performance_results.json`。

对比模式的性能标杆必须是 NPU。

不使用 `performance_e2e` 作为判据。

## c_api 模式的口径边界

`performance_device` 的 device 口径是裁决数字，ctypes 只发生在 host 侧，不改变这个
口径。执行器的 NPU 分支不得调用 torch 计算算子，因为 profiling 窗口会把每个算子的
Task Duration 都加进结果。

host wall clock 混有 Python 与 ctypes 开销，不能作为性能证据。任务书若要求 host
latency，本轮能力不足，按能力边界报告，不自行换口径测量。

动态库内部的 `aclrtMalloc` 不经过 PyTorch 分配器，ATK 的 memory 列看不到。
这种情况报告写“统计不到”，不能把显示的 `0 MB` 解释成零内存占用。

呈现选中数、每格可用/选中数、排除数、device 耗时、性能比和波动结果。

波动失败单独标记，不自动剔除。
