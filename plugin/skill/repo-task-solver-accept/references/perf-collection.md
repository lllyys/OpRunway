# 性能自测采集说明

供开发者采集逐 case 耗时并用包内 `verify_perf.py` 做参考对照。正式性能达标不在
此流程内（见文末）。

## 采集协议（任务书 §3.3 原文要求）

- 每 case 预热 ≥10 次、正式采样 30 次，报**中位数**；
- 每轮 Device 同步后计时；不含首次编译、数据生成、H2D/D2H；
- workspace 与指针数组在正式采样期间复用；
- 统计对象是 **NPU kernel 耗时**，不是 API 墙钟。

## 采集工具参考

kernel 级归因用 `msprof op`，参数与协议的对应：`--kernel-name`（目标算子
kernel 归因）、`--warm-up`（预热次数）、`--launch-count`（正式采样次数）、
`--launch-skip-before-match`（跳过依赖接口的准备段调用）。具体命令以现场
`msprof op --help` 为准。

未验证声明：该工具族在 Atlas 950 上的归因输出尚未经本流程实测校准（标 SA-15
未验证）；首次采集前先用小 case 核对「样本只含目标 kernel」。

## 耗时 JSON 格式（`verify_perf.py --dut-perf` 的输入，二选一）

```json
[{"case_id": "spotrf-0001", "avg_ms": 0.012, "min_ms": 0.011, "max_ms": 0.013}]
```

```json
{"spotrf-0001": 0.012}
```

字典值按 avg_ms 理解；min_ms/max_ms 可省，省则对应比值不算。中位数填入 avg_ms
位（包内参考耗时只有 min/avg/max 三档，中位数对 avg 档比较并在报告备注）。

## 对照的含义与边界

- 输出为逐 case 比值（被测 / 参考耗时），方向：>1 慢于参考、<1 快于参考；
- 参考耗时来自竞品 A100 实测摘录，**仅作自测参考，不设通过门**；
- 正式性能达标按任务书 T_A100 机制：开发者在 A100 实测填入 T_A100（附 CUDA
  版本与采集脚本），达标式 T_NPU ≤ T_A100 / 0.8，由验收流程执行。
