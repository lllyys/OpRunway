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
| 性能集 | 部署 CSV 中 `TC_PF_` 前缀的行 | 依据：设计 |
| 进程隔离 | 每个 case 的 warm-up 与每次采样都是独立进程 | 依据：设计 |
| 名字映射 | 用 `--gtest_list_tests` 建 case_name 到完整 GTest 名的映射 | 依据：设计 |
| 计时口径 | 只使用 msprof 的 kernel task duration | 依据：CANN 文档 |
| 排除口径 | GTest 的 ms 含 host 准备与 golden，不作性能依据 | 依据：设计 |

## 执行序列

1. 直接运行一次 `<bin> --gtest_filter=<完整名>`，作为不计分 warm-up。
   依据：设计。
2. warm-up 非 0 退出时，该例记 `FAIL(warmup)`，不再采样。依据：设计。
3. 默认独立执行五次 msprof，每次使用独立输出目录。依据：设计。
4. 采样命令是
   `msprof --application="<bin> --gtest_filter=<完整名>" --output=<目录>`。
   依据：CANN 文档；命令参数组合待实测。
5. `--repeats` 可覆盖五次采样数；每次仍保持进程隔离。依据：设计。
6. 原始目录默认保留，便于复核；`--keep-prof` 预留关闭策略。依据：设计。

msprof 查找顺序如下，均要求文件存在且可执行。依据：设计。

1. `--msprof` 显式覆盖。
2. PATH 中的 `msprof`。
3. `$ASCEND_TOOLKIT_HOME/tools/profiler/bin/msprof`。
4. `/usr/local/Ascend/ascend-toolkit/latest/tools/profiler/bin/msprof`。

全部找不到时退出 3，并写 `summary.reason=MSPROF_NOT_FOUND`。依据：设计。
这是环境失败，不进入任何性能采样。

## op_summary 解析

脚本把下列未实测信息集中在表驱动常量中。
目标机 spike 后只改已证伪的常量，不改采集与裁决结构。

| 常量 | 当前值 | 依据 |
| --- | --- | --- |
| `OP_SUMMARY_GLOB` | `PROF_*/mindstudio_profiler_output/op_summary_*.csv` | 依据：CANN 文档；待实测 |
| task type 列 | `Task Type` | 依据：CANN 文档；待实测 |
| duration 列 | `Task Duration(us)` | 依据：CANN 文档；待实测 |
| kernel task 类型 | `AI_CORE/AI_VECTOR_CORE/MIX_AIC/MIX_AIV` | 依据：CANN 文档；待实测 |

`parse_op_summary(dir)` 遍历匹配文件，只保留 kernel task 类型。
每行的 duration 相加为
本次 `kernel_us`，保留行数为本次 `launches`。依据：CANN 文档与设计。

缺列、负 duration 或非数值 duration 都视为解析失败。
没有 kernel 行时记
`NO_KERNEL`，不能以 0 代替。依据：设计。

## 统计与比对

| 项 | 计算 | 依据 |
| --- | --- | --- |
| `samples` | 保存每次采样的 kernel duration 总和 | 依据：设计 |
| `kernel_us` | 所有 samples 的中位数 | 依据：设计 |
| `spread` | `(max(samples)-min(samples))/median` | 依据：设计 |
| `npu_ms` | `kernel_us/1000` | 依据：单位换算 |
| `ratio` | `gpu_ms/npu_ms` | 依据：任务书性能口径 |
| PASS | `ratio >= perf.threshold` | 依据：任务书 GPU 百分比惯例 |

`perf.threshold` 默认 `0.8`，FACTS 可按任务书覆盖。依据：GPU 80% 惯例。

GPU 基线按 `PERF_KEY` 匹配：整数文本按 int 比较，其他值按原字符串比较。
没有匹配行或该行 `gpu_ms` 为空时记 `NO_REF`，只采集不评判。依据：设计。

基线 `timing_scope` 不是 `kernel` 时，每例 verdict 加 `(scope caveat)`。
summary 写
`scope_caveat=true`，但不改变基础状态或退出码。依据：v5 契约。

## 结果与退出码

结果原子写入 `results/performance_<run_id>.json`。依据：设计。
临时文件成功关闭后才替换目标文件。

每例除身份字段外，还记录 `kernel_us`、`samples`、`launches`、`gpu_ms`、`ratio`、
`spread`、`status`、`verdict` 与诊断消息。
这些字段共同保留原始样本、统计值和最终判定。依据：v5 契约。

summary 记录计数、`status`、`timing_scope`、`threshold` 与 `scope_caveat`。
`status` 只取 `通过/不通过/NO_REF/证据不足`。依据：v5 契约。

| 退出码 | 条件 | 依据 |
| --- | --- | --- |
| 0 | 无失败和证据缺口；有 NO_REF 时 summary 为 NO_REF | 依据：设计 |
| 1 | 至少一个可比较用例 FAIL，且没有证据缺口 | 依据：设计 |
| 2 | 任一 NO_KERNEL、CRASH、TIMEOUT 或 MISSING | 依据：设计 |
| 3 | CSV、构建、二进制、列表、基线或 msprof 环境问题 | 依据：设计 |

证据不足优先于数值失败，因为存在未完成的期望用例。依据：设计。

## 验收消费规则

`accept.py verdict` 直接消费性能 JSON 的 `summary.status`、`timing_scope` 与
`scope_caveat`，不重算 kernel 耗时或比值。`NO_REF` 不能证明性能达标，因此总体结论为
`证据不足`。部署 CSV 没有 `TC_PF_` 行时不要求性能 JSON。

## 待实测边界

- msprof 是否接受当前 `--application` 引号与 `--output` 组合。依据：待实测。
- `PROF_*` 子目录相对 `--output` 的实际层级。依据：待实测。
- op_summary 文件名是否稳定使用当前 glob。依据：待实测。
- task type 与 duration 的列名、大小写及 duration 单位。依据：待实测。
- MIX task 是否同时产生需去重的明细行。依据：待实测。
- 多设备或多 stream 时一个 case 是否产生多个 op_summary 文件。依据：待实测。
