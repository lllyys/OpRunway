# msprof kernel 性能协议

## Contents

- [适用范围](#适用范围)
- [执行序列](#执行序列)
- [op_summary 解析](#op_summary-解析)
- [统计与比对](#统计与比对)
- [结果与退出码](#结果与退出码)
- [验收消费规则](#验收消费规则)
- [待实测边界](#待实测边界)

## 适用范围

| 规则 | 规定 | 依据 |
| --- | --- | --- |
| 性能期望集 | 任务包 CSV 中 `TC_PF_` 前缀且能配到非空 `gpu_ms` 基线的行 | 依据：项目策略 |
| 无基线的行 | 不跑，只把用例名写进 JSON 的 `ignored_no_ref` | 依据：项目策略 |
| 进程隔离 | 每个 case 的 warm-up 与每次采样都是独立进程 | 依据：项目策略 |
| 名字映射 | `--gtest_list_tests` 建 case_name 到完整名的映射 | 依据：项目策略 |
| 计时口径 | 只使用 msprof 的 kernel task duration | 依据：CANN 指南，待核 |
| 排除口径 | GTest 的 ms 含 host 准备与 golden，不作性能依据 | 依据：项目策略 |

`TC_PF_` 是 `case_name` 前缀，标记性能用例；其余 `TC_` 用例是精度用例。**期望集**是本轮
必须出结果的用例名集合，基线按**基线键**（`gpu_baseline.csv` 表头去掉 `id`、`gpu_ms`
后与 CSV 共有且非空的列）匹配。

## 执行序列

1. 直接运行一次 `<bin> --gtest_filter=<完整名>`，作为不计分 warm-up。
   依据：项目策略。
2. warm-up 非 0 退出时，该例记 `FAIL(warmup)`，不再采样。依据：项目策略。
3. 默认独立执行五次 msprof，每次使用独立输出目录。依据：项目策略。
4. 每次采样先采集再导出，两条命令共用同一输出目录：
   `msprof --application="<bin> --gtest_filter=<完整名>" --output=<目录>` 只产
   `task_time_*.csv` 与 sqlite，随后 `msprof --export=on --output=<目录>` 才生成
   `op_summary_*.csv`。依据：实测（A3，CANN 9.0.1，ascend910_93）。
5. 每次采到的 kernel 总时长除以 `--calls-per-case N`（默认 1）才记为本次样本。
   **`calls_per_case` 只计 harness 对被测 API 的调用次数，不计 kernel launch 数**：
   一次 API 调用可产生多个 launch（950 实测 coo2csr 每调用 2–4 个，随规模变化），
   样本 = 该用例全部保留 launch 的 duration 求和 ÷ `calls_per_case`，不得再除以
   `launches`。
   N 是 harness 一条 GTest 用例调用被测接口的次数：按 README 契约写的新 harness 只调一次，
   填 1；固定先 warm-up 一次再调一次的旧 harness 填 2。依据：项目策略。
6. `--repeats` 可覆盖五次采样数；每次仍保持进程隔离。依据：项目策略。
7. 原始目录默认保留，便于复核；`--keep-prof` 预留关闭策略。依据：项目策略。

msprof 查找顺序如下，均要求文件存在且可执行。依据：项目策略。

1. `--msprof` 显式覆盖。
2. PATH 中的 `msprof`。
3. `$ASCEND_TOOLKIT_HOME/tools/profiler/bin/msprof`。
4. `/usr/local/Ascend/ascend-toolkit/latest/tools/profiler/bin/msprof`。

全部找不到时退出 3，并写 `summary.reason=MSPROF_NOT_FOUND`。依据：项目策略。
这是环境失败，不进入任何性能采样。

## op_summary 解析

脚本把下列信息集中在表驱动常量中，换机型只改常量不改采集与裁决结构。
下表四项已在 A3（CANN 9.0.1，ascend910_93）用 sger 实测确认。

| 常量 | 当前值 | 依据 |
| --- | --- | --- |
| `OP_SUMMARY_GLOB` | `PROF_*/mindstudio_profiler_output/op_summary_*.csv` | 实测（export 后生成） |
| task type 列 | `Task Type` | 实测（第 8 列） |
| duration 列 | `Task Duration(us)` | 实测（第 10 列） |
| kernel task 类型 | `AI_CORE/AI_VECTOR_CORE/MIX_AIC/MIX_AIV` | 实测（sger 出 AI_VECTOR_CORE） |

`parse_op_summary(dir)` 遍历匹配文件，只保留 kernel task 类型。
每行的 duration 相加为
本次 `kernel_us`，保留行数为本次 `launches`。依据：项目策略；字段语义待核。

缺列、负 duration 或非数值 duration 都视为解析失败。
没有 kernel 行时记
`NO_KERNEL`，不能以 0 代替。依据：项目策略。

## 统计与比对

| 项 | 计算 | 依据 |
| --- | --- | --- |
| `samples` | 每次采样的 kernel duration 总和 ÷ `calls_per_case` | 依据：项目策略 |
| `kernel_us` | 所有 samples 的中位数 | 依据：项目策略 |
| `spread` | `(max(samples)-min(samples))/median` | 依据：项目策略 |
| `npu_ms` | `kernel_us/1000` | 依据：单位换算 |
| `ratio` | `gpu_ms/npu_ms` | 依据：项目策略 |
| PASS | `ratio >= 0.8` | 依据：项目策略 |

阈值固定 `0.8`，由 accept 渲染进脚本并写入 `manifest.json` 的 `threshold`；
它是项目策略，不是外部事实，任务包不能覆盖。

GPU 基线按基线键匹配：整数文本按 int 比较，其他值按原字符串比较。期望集里每行都有基线，
所以逐例 `NO_REF` 正常不会出现；期望集为空（`TC_PF_` 全无基线，或被 `--case/--filter`
收窄成空）时 summary 记 `NO_REF`，退出 0。依据：项目策略。

基线 `timing_scope` 不是 `kernel` 时，每例 verdict 加 `(scope caveat)`。
summary 写
`scope_caveat=true`，但不改变基础状态或退出码。依据：项目契约。

## 结果与退出码

结果原子写入 `results/performance_<run_id>.json`。依据：项目策略。
临时文件成功关闭后才替换目标文件。

顶层记录 `calls_per_case` 与 `ignored_no_ref`（无基线被忽略的用例名）。每例除身份字段外，
还记录 `kernel_us`、`samples`、`launches`、`gpu_ms`、`ratio`、`spread`、`status`、`verdict`
与诊断消息。这些字段共同保留原始样本、统计值和最终判定。依据：项目契约。

summary 记录计数、`status`、`timing_scope`、`threshold` 与 `scope_caveat`。
`status` 只取 `通过/不通过/NO_REF/证据不足`。依据：项目契约。

| 退出码 | 条件 | 依据 |
| --- | --- | --- |
| 0 | 无失败和证据缺口；期望集为空时 summary 为 NO_REF | 依据：项目策略 |
| 1 | 至少一个可比较用例 FAIL，且没有证据缺口 | 依据：项目策略 |
| 2 | 任一 NO_KERNEL、CRASH、TIMEOUT 或 MISSING | 依据：项目策略 |
| 3 | CSV、构建、二进制、列表、基线或 msprof 环境问题 | 依据：项目策略 |

证据不足优先于数值失败，因为存在未完成的期望用例。依据：项目策略。

## 验收消费规则

`accept.py verdict` 不重算 kernel 耗时或比值，但会重算 cases 状态计数，并校验期望集、
run-id、op、family、repo、soc、device、summary 与退出码，以及 A3/A4 两份 JSON 的
`csv_sha256`、`binary_sha256` 相同。期望集由 verdict 从运行时包的 CSV 与规范化基线重新算出，
与脚本口径一致。`NO_REF` 不能证明性能达标，总体结论为 `证据不足`；
`check.json` 的 `checks.perf.comparable_pf` 为 0 时不跑 A4、不要求性能 JSON，性能记 `通过`。

## 已实测与待实测边界

在 A3（CANN 9.0.1，ascend910_93）用 sger 实测确认：采集与导出分两步、
`op_summary_*.csv` 由 `--export=on` 生成、`Task Type` 与 `Task Duration(us)` 列名、
duration 单位为微秒、向量算子出 `AI_VECTOR_CORE`。

以下仍待在更多机型与算子上确认：

- Cube 类算子（gemm/herk）的 `Task Type` 是否为 `AI_CORE` 或 `MIX_AIC/MIX_AIV`。依据：待实测。
- MIX task 是否同时产生需去重的明细行。依据：待实测。
- 多设备或多 stream 时一个 case 是否产生多个 op_summary 文件。依据：待实测。
- ascend950（arch35）上文件名与列是否一致。依据：待实测。
