# @@OP@@ 任务包

## 来源

@@SOURCE_TABLE@@

## 接口签名

```c
@@SIGNATURE@@
```

开发者头文件声明须与此语义一致，验收阶段做归一化比对。

## 六件清单

@@SIX_FILES@@

## 列契约

@@COLUMN_CONTRACT@@

完整表头（各行直接拼接）：

```text
@@HEADER@@
```

## param.h 读列要求

@@CONSUMPTION_RULES@@

## Golden 要求

@@GOLDEN_REQUIREMENTS@@

## 校验形态与阈值

@@VERIFY_TABLE@@

@@TOLERANCE_TEXT@@

## 用例块与覆盖

@@BLOCK_TABLE@@

@@PAIR_SUMMARY@@
精度集 = 非 `TC_PF_` 前缀。开发者自加的 `TEST_F` 不在验收集内。

## 精度验收

```bash
python3 verify_accuracy.py --repo <ops-blas-root> --soc <soc> --device 0
```

结果写到 `results/accuracy_<run_id>.json`。退出码如下：

| 退出码 | 含义 |
| --- | --- |
| 0 | 期望用例全部 PASS |
| 1 | 存在 FAIL、SKIP、TIMEOUT、CRASH 或 MISSING |
| 3 | CSV、构建、二进制或 GTest 列举环境问题 |

设备号由 `build.sh --device=N` 在编译期写入 `-DTEST_DEVICE_ID`。使用 `--skip-build`
时沿用上次编译的设备号。
GTest JSON 与构建日志写入 `results/<run_id>/accuracy/`；阶段目录必须是新目录，
重复 run-id 会退出 3，避免旧结果污染。

## 性能验收

```bash
python3 verify_performance.py --repo <ops-blas-root> --soc <soc> --device 0
```

一条 gtest 用例只调用被测接口一次，用例里不自行预热、不重复调用；预热与重复采样由
`verify_performance.py` 负责。msprof 采到的是整条用例的全部 kernel，多调一次就多算一次。

性能集只含本任务包 CSV 中能配到 GPU 基线的 `TC_PF_` 行，无基线的行不跑、只计数；每例
单独执行。每例先直接运行一次 GTest warm-up，再独立运行 5 次 msprof；每次把
`AI_CORE/AI_VECTOR_CORE/MIX_AIC/MIX_AIV` 的 `Task Duration(us)` 求和。最终 `kernel_us`
取五次和的中位数，`spread` 为 `(max-min)/median`。

性能键为 `@@PERF_KEY@@`。`npu_ms = kernel_us/1000`，有 GPU 基线时计算
`ratio = gpu_ms/npu_ms`；`ratio >= @@PERF_THRESHOLD@@` 才判 PASS。无匹配基线时记
`NO_REF`，只采集不评判；某次没有 kernel 行时记 `NO_KERNEL`，不能把耗时写成 0。

结果写到 `results/performance_<run_id>.json`。每例记录 `kernel_us`、五次 `samples`、
各次 `launches`、`gpu_ms`、`ratio`、`spread` 和 `verdict`。汇总记录状态、
`timing_scope` 与阈值。退出码如下：

| 退出码 | 含义 |
| --- | --- |
| 0 | 全部 PASS，或没有可比较的 GPU 基线 |
| 1 | 至少一个有基线用例 FAIL |
| 2 | 出现 NO_KERNEL、CRASH、TIMEOUT 或 MISSING，证据不足 |
| 3 | CSV、构建、二进制、GTest 列举、基线或 msprof 环境问题 |

GPU 基线的 `timing_scope` 不是 `kernel` 时，每例 verdict 带 `(scope caveat)`，汇总的
`scope_caveat` 也为 true。GTest 自带的 ms 含 host 准备与 golden，不作性能依据。

msprof 采集与导出两步、`op_summary_*.csv` 目录模式、列名与 kernel task 类型已在 A3
（CANN 9.0.1，ascend910_93）实测确认；表驱动常量与仍待其他机型确认的边界见
skill 的 `references/perf-protocol.md`。
原始 profile 与构建日志写入 `results/<run_id>/performance/`；重复 run-id 会退出 3。

## GPU 基线

@@GPU_BASELINE@@
