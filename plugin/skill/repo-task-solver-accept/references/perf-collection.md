# 性能自测采集说明

供开发者采集逐 case 耗时并用包内 `verify_perf.py` 做参考对照。正式性能达标不在
此流程内（见文末）。口径与对外发布版 PERF_COLLECTION_SUPPLEMENT 一致
（2026-10-08 同步，任务书定稿版）。

## 采集协议（任务书 §3.3 要求）

- 每 case 正式采样 30 次，报**中位数**（预热由 `msprof op` 的 `--warm-up`
  参数控制，不另行规定预热次数）；
- 每轮 Device 同步后计时；不含首次编译、数据生成、H2D/D2H；
- workspace 与指针数组在正式采样期间复用；
- 统计对象是 **NPU kernel 耗时**，不是 API 墙钟。

## 采集工具参考

kernel 级归因用 `msprof op`，参数与协议的对应：`--kernel-name`（目标算子
kernel 归因）、`--warm-up`（预热次数，工具自带预热）、`--launch-count`
（正式采样次数）、`--launch-skip-before-match`（跳过依赖接口的准备段调用）。
具体命令以现场 `msprof op --help` 为准；首次采集建议先用小 case 核对
「样本只含目标 kernel」。

未验证声明：该工具族在 Atlas 950 上的归因输出尚未经本流程实测校准（标 SA-15
未验证）。

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
- 参考耗时来自 CUDA 参考实现（cuSolver）实测摘录，**仅作自测参考，不设通过门**；
- 正式性能达标按任务书执行：以任务书性能对比表中的 GPU 参考数据为基准
  （取自各算子目录 `bench_result.json` 的 `perf.avg_ms`，发布预采集），
  T_NPU ≤ T_GPU数据 / 0.35，NPU 侧耗时以 `msprof op` 采集 `OpBasicInfo.csv`
  按 kernel 名求平均，由验收流程执行。


## cmatinv 共享样例采集

`cmatinv_batched` 的 n=8、batch=4 样例有独立采集入口：

```bash
python3 scripts/harness/profile_cmatinv_sample.py \
  --repo /absolute/path/to/ops-solver \
  --provenance /absolute/path/to/run-001/build.json \
  --gen-dir /absolute/path/to/repo-task-solver-case-gen/scripts \
  --device 0 --out /absolute/path/to/new-perf-run
```

先运行共享样例完成构建，source 现场 CANN 环境，再使用该次 build.json。
输出目录必须不存在。入口使用 `msprof op` 的 kernel replay（由 profiler 重放同一 kernel 的采集模式）、5 次工具预热与
30 次目标 kernel 采样，读取 `OpBasicInfo*.csv` 的 `Task Duration(us)`，
统计 mean/median/min/max（毫秒），不使用执行器的 API 墙钟时间。
Host 接口内部的分配、搬运和准备不计入该 kernel-only 指标，不能据此评价 API 总延迟。

当前实现针对已核 n8/b4 单 kernel 通路，默认匹配真实符号
`_Z22cmatinv_batched_kernelPhS_S_S_S_S_S_`；不同编译版本可通过
`--kernel-name` 指定相同 kernel 的实际符号。多 kernel 或转发路径需要另行定义
完整采集范围，不能直接套用本样例。CANN 版本若改变 CSV 格式，解析错误会拒绝产成功结果。

结果 `performance.json` 中 `status=COLLECTED` 只表示完整收到了30条采样。
报告保留全部样本和原始 CSV 路径；工具退出成功但应用失败、漏采、重复导出、
错误 kernel/device 或非法耗时均退2。`performance_verdict=NOT_EVALUATED`
表示未做性能达标判定：该样例未绑定同规格、同口径 GPU 基线。
不能把 Cholesky 的 GPU/0.35 门槛直接套到 cmatinv 样例。
