# 性能自测采集说明（{delivery_group} 交付页）

适用于本批交付的 {package_count} 个任务包。供开发者采集逐 case 耗时并用包内 `verify_perf.py` 做参考对照。正式性能达标不在
此流程内（见文末）。本说明于2026-10-10对齐验收性能流程，均值口径以任务书 §3.3 为准。

## 采集协议（任务书均值口径与工具采样约定）

- 每 case 由验收工具采集30次调用对应的目标 kernel，预热5次；30次与预热5次
  是工具协议，不是任务书额外要求。报告**算术平均耗时**，中位数仅作诊断；
- 每轮 Device 同步后计时；不含首次编译、数据生成、H2D/D2H；
- workspace 与指针数组在正式采样期间复用；
- 统计对象是 **NPU kernel 耗时**，不是 API 墙钟。

## 采集工具参考

kernel 级归因用 `msprof op`，参数与协议的对应：`--kernel-name`（目标算子
kernel 归因）、`--warm-up`（预热次数，工具自带预热）、`--launch-count`
（正式采样次数）、`--launch-skip-before-match`（跳过依赖接口的准备段调用）。
具体命令以现场 `msprof op --help` 为准；首次采集建议先用小 case 核对
「样本只含目标 kernel」。

未验证声明：该工具族在 Atlas 950 上的归因输出尚未经本流程实测校准。

## 耗时 JSON 格式（`verify_perf.py --dut-perf` 的输入，二选一）

```json
[{"case_id": "{op}-0001", "avg_ms": 0.012, "min_ms": 0.011, "max_ms": 0.013}]
```

```json
{"{op}-0001": 0.012}
```

字典值按 avg_ms 理解；avg_ms 必须是算术平均耗时。min_ms/max_ms 可省，
省则对应比值不算；中位数不可填入 avg_ms。

## 对照的含义与边界

- 输出为逐 case 比值（被测 / 参考耗时），方向：>1 慢于参考、<1 快于参考；
- 参考耗时来自 CUDA 参考实现（cuSolver）实测摘录，**仅作自测参考，不设通过门**；
- 正式性能达标按任务书执行：以任务书性能对比表中的 GPU 参考数据为基准
  （取自各算子目录 `bench_result.json` 的 `perf.avg_ms`，发布预采集），
  T_NPU ≤ T_GPU数据 / 0.35，NPU 侧耗时以 `msprof op` 采集 `OpBasicInfo.csv`
  按 kernel 名求平均，由验收流程执行。

## 正式性能验收入口

验收者使用外部 repo-task-solver-accept skill 的
`references/performance-acceptance.md` 与 `scripts/harness/run_performance.py`：
逐 case 用 `msprof op` 采集 → 按 case_id/bench_key 匹配冻结 GPU 基线 →
按任务书阈值判断 → 汇总 PASS/FAIL/INSUFFICIENT。缺采集或缺基线仍保留为证据不足。
包内 verify_perf.py 只做自测参考对照，不替代该验收链。

GPU 源数据 bench_result.json 更新后，已有任务包的 perf_baseline.json 不会自动变化；
采用新基线时须重新提取并更新 manifest 指纹，形成可追溯的新包版本，旧证据不可混用。
