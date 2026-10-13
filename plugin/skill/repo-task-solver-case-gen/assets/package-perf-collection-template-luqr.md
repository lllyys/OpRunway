# 性能自测采集说明（{delivery_group} 交付页）

适用于本批交付的 {package_count} 个任务包。供开发者采集逐 case 耗时并用包内
`verify_perf.py` 做参考对照。正式性能达标判据按任务书 §3.3 执行（见文末）。

## 采集协议

- 逐 case 使用 `msprof op`，建议预热 5 次、正式采样 30 次，报**平均单次 kernel 耗时**；
  次数沿用现有采集工具约定，不是任务书额外要求；
- 按 profiler 的 kernel 耗时统计，不含首次编译、数据生成、H2D/D2H；
- workspace 与指针数组在正式采样期间复用；
- 统计对象是 **NPU kernel 耗时**，不是 API 墙钟。

## 采集工具参考

kernel 级归因用 `msprof op`，参数与协议的对应：`--kernel-name`（目标算子
kernel 归因）、`--warm-up`（预热次数）、`--launch-count`（正式采样次数）、
`--launch-skip-before-match`（跳过依赖接口的准备段调用，如 `*gqr`/`*mqr` 前的
`*geqrf`、`getri`/`getriBatched` 前的 `getrf`）。具体命令以现场
`msprof op --help` 为准。

未验证声明：该工具族在 Atlas 950 上的归因输出尚未经本流程实测校准；首次采集
前先用小 case 核对「样本只含目标 kernel」。

## 耗时 JSON 格式（`verify_perf.py --dut-perf` 的输入，二选一）

```json
[{"case_id": "{op}-0001", "avg_ms": 0.012, "min_ms": 0.011, "max_ms": 0.013}]
```

```json
{"{op}-0001": 0.012}
```

字典值与 avg_ms 均填写平均耗时；min_ms/max_ms 可省，省则对应比值不算。
一次接口调用包含多个目标 kernel 时，应统计该次调用的全部目标 kernel 耗时。
中位数不能填入 avg_ms 代替平均值。

## 对照的含义与边界

- 输出为逐 case 比值（被测 / 参考耗时），方向：>1 慢于参考、<1 快于参考；
- 参考耗时来自各算子 `bench_result.json` 的 CUDA 实测摘录（**T_GPU数据**，随包在
  `perf_baseline.json`），**仅作自测参考，不设通过门**；
- 正式性能达标按任务书 §3.3 机制：开发者以 `msprof op` 采集目标算子 kernel 均值
  作为 **T_NPU**，达标式 **T_NPU ≤ T_GPU数据 / 0.35**，由验收流程执行；
- bench 未提供 `perf` 的 case（见 DELIVERY_NOTE「已知边界」）无参考比值，正式
  判据仍以任务书 §3.3 为唯一依据。