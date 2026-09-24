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
python3 verify_accuracy.py --repo <repo-root> --soc <soc> --device 0
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
python3 verify_performance.py --repo <repo-root> --soc <soc> --device 0
```

一条 gtest 用例只调用被测接口一次，用例里不自行预热、不重复调用。`verify_performance.py`
不另起预热进程，重复采样仅在显式 `--repeats` 时发生。一次采样采到的是该次进程实际发生的
全部 kernel launch，多调一次就多算一次。

性能集只含本任务包 CSV 中能配到 GPU 基线的 `TC_PF_` 行；配不到基线的行不执行，只记进
结果 JSON 的 `ignored_no_ref`。每例单独执行，默认独立采集 1 次，进程形态是：

```text
msprof op --output=<采样目录> --aic-metrics=BasicInfo --launch-count=512 \
         <被测二进制> --gtest_filter=<用例> --gtest_output=json:<证据路径>
```

选项必须排在被测二进制之前，其后一律当作被测程序的参数。产物布局取决于实际采到的
launch 数：采到 1 个是 `OPPROF_*/OpBasicInfo.csv`，采到多个是
`OPPROF_*/<kernel 符号名>/<序号>/OpBasicInfo_<时间戳>.csv`。

一次采样的读数是递归命中的全部 `OpBasicInfo*.csv`、全部数据行的 `Task Duration(us)`
之和。`kernel_us` 取各次读数的中位数（单次时即该值）；`spread` 为 `(max-min)/median`，
单次时为 0——表示无样本间差异可算，不是稳定性证明。

`--launch-count` 是单次采集的 kernel launch 上限，默认 512，合法区间 1 到 5000。采到的
行数达到该上限时无法区分「恰好这么多」与「被截断」，该例记 `NO_KERNEL`，措辞落在
「当前采集口径不支持该用例的 launch 规模」，不是算子失败。调大它可以解决，但产物体积
随实际 launch 数线性增长，约 2.2 MB 每 launch。

性能键为 `@@PERF_KEY@@`。`npu_ms = kernel_us/1000`，有 GPU 基线时计算
`ratio = gpu_ms/npu_ms`；`ratio >= @@PERF_THRESHOLD@@` 才判 PASS。采样后仍配不到基线的
用例记 `NO_REF`，只采集不评判。

失败判定按固定顺序走，先到先定：

| 顺序 | 条件 | 结果 |
| --- | --- | --- |
| 1 | 采集进程超时 | `TIMEOUT` |
| 2 | 采集工具退出非零，或日志出现 `Copy failed`、`Failed to save`、`No space left` | 整轮中止，退 3 |
| 3 | 执行成功证据 `r<N>.gtest.json` 缺失或不合格 | `CRASH` |
| 4 | 无 CSV、无数据行、缺 `Task Duration(us)` 列、值非有限正数 | `NO_KERNEL` |
| 5 | 采到的行数达到 `--launch-count` | `NO_KERNEL` |

第 2 条排在证据检查之前：磁盘写满时采集工具仍退 0、只刷 WARN 且不产 CSV，按算子问题
记会把排错方向带反。每次采样前按本次上限（`--launch-count` × 2.2 MB × 1.5）检查可用
空间，不足即整轮中止；采集目录不要落在 `/dev/shm` 这类小容量文件系统上。

结果写到 `results/performance_<run_id>.json`。每例记录 `kernel_us`、`samples`、
各次 `launches`、`gpu_ms`、`ratio`、`spread` 和 `verdict`。汇总记录状态、
`timing_scope` 与阈值。退出码如下：

| 退出码 | 含义 |
| --- | --- |
| 0 | 全部 PASS，或没有可比较的 GPU 基线 |
| 1 | 至少一个有基线用例 FAIL |
| 2 | 出现 NO_KERNEL、CRASH、TIMEOUT 或 MISSING，证据不足 |
| 3 | CSV、构建、二进制、GTest 列举、基线或采集环境问题 |

退 3 时汇总的 `reason` 记具体原因：`DISK_SPACE`、`DISK_WRITE_FAILED`、`PROFILER_FAILED`、
`MSPROF_NOT_FOUND`、`MSPROF_UNUSABLE`。逐例状态词表不因此扩张，仍是 PASS、FAIL、
NO_REF、NO_KERNEL、CRASH、TIMEOUT、MISSING 七个。

GPU 基线的 `timing_scope` 不是 `kernel` 时，每例 verdict 带 `(scope caveat)`，汇总的
`scope_caveat` 也为 true。GTest 自带的 ms 含 host 准备与 golden，不作性能依据。

采集后端从 `msprof` 换成 `msprof op`（`msprof op`）后读数系统性偏低：同机同用例实测
0.64 到 0.84 倍，绝对差 8.5 到 11 us。新后端重放 kernel，量的是稳态，旧后端量的是含
首次调用惩罚的单次冷调用，两者不可比，跨后端的两轮数不能放在一起看。

命令形态、两种产物布局、`OpBasicInfo.csv` 九列（无 `Task Type` 列）与截断判据已在 A3 机
（CANN 9.0.1，ascend910_93）实测确认，其他机型的边界尚未核对。原始 profile 与构建日志
写入 `results/<run_id>/performance/`；重复 run-id 会退出 3。

## GPU 基线

@@GPU_BASELINE@@
