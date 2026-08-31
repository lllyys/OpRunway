# sasum 任务包

## 来源

| 项目 | 值 |
| --- | --- |
| symbol | aclblasSasum |
| returns | aclblasStatus_t |
| family | asum |
| schema_version | 1 |
| generator_version | 1 |
| sources.params | taskdoc:§2.3/§2.4 |
| sources.cases | taskdoc:§3.5 n 集、§2.4 incx=1、§3.5 fill（[-10,10] 均匀/全零/常数 2.0） |
| sources.fill_boundary | taskdoc:§3.5 全负与有界交替符号图样超出 fill.h 词表，记为能力边界 |
| sources.perf | taskdoc:§3.1/§3.5 无 GPU 基线，NO_REF 只采集；补齐点沿 n 轴铺开凑满固定 200 |

## 接口签名

```c
aclblasStatus_t aclblasSasum(
    aclblasHandle_t handle,
    int n,
    const float* x,
    int incx,
    float* result
);
```

开发者头文件声明须与此语义一致，验收阶段做归一化比对。

## 六件清单

| 文件 | 用途 |
| --- | --- |
| gen_csv.py | 事实表 FACTS 与独立 CSV 生成器 |
| sasum_test.csv | CSV 驱动用例 |
| verify_accuracy.py | 精度验收入口 |
| verify_performance.py | msprof kernel 性能验收入口 |
| README.md | 开发与验收契约 |
| gpu_baseline.csv | GPU 性能基线与元数据 |

## 列契约

| 列 | 来源参数 | 取值 | 说明 |
| --- | --- | --- | --- |
| case_name | 控制列 | TC_L0_/TC_PW_/TC_ED_/TC_PF_ 加块内编号 | GTest 参数名，任务包内唯一 |
| description | 控制列 | 非空字符串 | 轴取值、edge 名或性能键值 |
| n | n | 整数 | 维度 |
| x_fill | x | 见下方 fill 词表 | x（in）的数据填充方式 |
| incx | incx | 整数 | x 的步长 |
| expect_result | 控制列 | 见下方状态词表 | 使用 csv_loader.h parseStatus 支持的全名 |
| random_seed | 控制列 | 正整数 | 各缓冲依次使用 seed、seed+1……派生随机填充 |

fill 词表（语法见 `fill.h` 的 `METHOD_PATTERN_VAL`）：

- `RANDOM_NORM_10`
- `VALUE_NORM_0`
- `VALUE_NORM_2.0`

`expect_result` 状态词表（`parseStatus` 全名）：

- `ACLBLAS_STATUS_SUCCESS`
- `ACLBLAS_STATUS_NOT_INITIALIZED`
- `ACLBLAS_STATUS_ALLOC_FAILED`
- `ACLBLAS_STATUS_INVALID_VALUE`
- `ACLBLAS_STATUS_MAPPING_ERROR`
- `ACLBLAS_STATUS_EXECUTION_FAILED`
- `ACLBLAS_STATUS_INTERNAL_ERROR`
- `ACLBLAS_STATUS_NOT_SUPPORTED`
- `ACLBLAS_STATUS_ARCH_MISMATCH`
- `ACLBLAS_STATUS_HANDLE_IS_NULLPTR`
- `ACLBLAS_STATUS_INVALID_ENUM`
- `ACLBLAS_STATUS_UNKNOWN`

完整表头（各行直接拼接）：

```text
case_name,description,n,x_fill,incx,expect_result,random_seed
```

## param.h 读列要求

每个投影列必须用 `ReadMap` 显式读取。框架的 `ReadMap` 会返回默认值，但本任务包契约要求
缺列或空值必须 `throw`，不得依赖默认值。

```cpp
auto require = [&](const char* key) -> std::string {
    auto value = ReadMap(m, key);
    if (value.empty()) {
        throw std::runtime_error(std::string("missing or empty CSV column: ") + key);
    }
    return value;
};
caseName = require("case_name");
```

## Golden 要求

- `golden.kind`: `cblas`
- `golden.symbol`: `cblas_sasum`
- golden dtype: `float32`

## 校验形态与阈值

| token | 校验对象 | 方式与阈值 |
| --- | --- | --- |
| scalar | 标量输出 | Verifier::verifyScalar |

| precision_row | rtol | atol | fixed_limit | mantissa_bits | emin |
| --- | --- | --- | --- | --- | --- |
| FLOAT32 | 2^-10 | 2^-16 | 1e-2 | 23 | -126 |

通过条件：逐元素 `|actual - golden| <= atol + rtol * |golden|`，
`matched_ratio >= 0.99`，且每个元素都满足 `abs_error <= max(fixed_limit,
32 * ULP_at_|golden|)`。ULP 使用表中的 mantissa_bits 与 emin。表值逐项来自
`test/frame/verify.h` 的 `getMixedToleranceDefaults` 与
`MixedToleranceStrategy::processElement`。

## 用例块与覆盖

| 块 | 条数 | 规则 |
| --- | --- | --- |
| L0 | 2 | op enum 全组合与 4/8 快速覆盖 |
| PW | 75 | 确定性 pairwise 且通过 constraints/footprint |
| ED | 0 | 逐条 edge_cases.set 物化 |
| PF | 200 | perf.rows 与可选 sweep |

pairs 覆盖 75/75，infeasible 0 对。
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

性能键为 `n`。`npu_ms = kernel_us/1000`，有 GPU 基线时计算
`ratio = gpu_ms/npu_ms`；`ratio >= 0.8` 才判 PASS。无匹配基线时记
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

元数据：

| 键 | 值 |
| --- | --- |
| timing_scope | unspecified |
| device | unspecified |
| library | unspecified |
| warmup | unspecified |
| statistic | unspecified |
| source | taskdoc:§3.5 的 25 个 n 加 175 个补齐点；§3.1 无 GPU 对标，只采集上报 |

基线行：

| id | n | gpu_ms |
| --- | --- | --- |
| sasum-base-001 | 1 | 只采集不评判 |
| sasum-base-002 | 2 | 只采集不评判 |
| sasum-base-003 | 3 | 只采集不评判 |
| sasum-base-004 | 4 | 只采集不评判 |
| sasum-base-005 | 5 | 只采集不评判 |
| sasum-base-006 | 6 | 只采集不评判 |
| sasum-base-007 | 7 | 只采集不评判 |
| sasum-base-008 | 8 | 只采集不评判 |
| sasum-base-009 | 9 | 只采集不评判 |
| sasum-base-010 | 10 | 只采集不评判 |
| sasum-base-011 | 12 | 只采集不评判 |
| sasum-base-012 | 15 | 只采集不评判 |
| sasum-base-013 | 16 | 只采集不评判 |
| sasum-base-014 | 17 | 只采集不评判 |
| sasum-base-015 | 18 | 只采集不评判 |
| sasum-base-016 | 20 | 只采集不评判 |
| sasum-base-017 | 24 | 只采集不评判 |
| sasum-base-018 | 30 | 只采集不评判 |
| sasum-base-019 | 31 | 只采集不评判 |
| sasum-base-020 | 32 | 只采集不评判 |
| sasum-base-021 | 33 | 只采集不评判 |
| sasum-base-022 | 36 | 只采集不评判 |
| sasum-base-023 | 40 | 只采集不评判 |
| sasum-base-024 | 48 | 只采集不评判 |
| sasum-base-025 | 50 | 只采集不评判 |
| sasum-base-026 | 60 | 只采集不评判 |
| sasum-base-027 | 63 | 只采集不评判 |
| sasum-base-028 | 64 | 只采集不评判 |
| sasum-base-029 | 65 | 只采集不评判 |
| sasum-base-030 | 70 | 只采集不评判 |
| sasum-base-031 | 72 | 只采集不评判 |
| sasum-base-032 | 80 | 只采集不评判 |
| sasum-base-033 | 96 | 只采集不评判 |
| sasum-base-034 | 100 | 只采集不评判 |
| sasum-base-035 | 120 | 只采集不评判 |
| sasum-base-036 | 127 | 只采集不评判 |
| sasum-base-037 | 128 | 只采集不评判 |
| sasum-base-038 | 129 | 只采集不评判 |
| sasum-base-039 | 144 | 只采集不评判 |
| sasum-base-040 | 160 | 只采集不评判 |
| sasum-base-041 | 192 | 只采集不评判 |
| sasum-base-042 | 200 | 只采集不评判 |
| sasum-base-043 | 240 | 只采集不评判 |
| sasum-base-044 | 255 | 只采集不评判 |
| sasum-base-045 | 256 | 只采集不评判 |
| sasum-base-046 | 257 | 只采集不评判 |
| sasum-base-047 | 288 | 只采集不评判 |
| sasum-base-048 | 300 | 只采集不评判 |
| sasum-base-049 | 320 | 只采集不评判 |
| sasum-base-050 | 384 | 只采集不评判 |
| sasum-base-051 | 480 | 只采集不评判 |
| sasum-base-052 | 500 | 只采集不评判 |
| sasum-base-053 | 511 | 只采集不评判 |
| sasum-base-054 | 512 | 只采集不评判 |
| sasum-base-055 | 513 | 只采集不评判 |
| sasum-base-056 | 576 | 只采集不评判 |
| sasum-base-057 | 640 | 只采集不评判 |
| sasum-base-058 | 700 | 只采集不评判 |
| sasum-base-059 | 768 | 只采集不评判 |
| sasum-base-060 | 960 | 只采集不评判 |
| sasum-base-061 | 1000 | 只采集不评判 |
| sasum-base-062 | 1023 | 只采集不评判 |
| sasum-base-063 | 1024 | 只采集不评判 |
| sasum-base-064 | 1025 | 只采集不评判 |
| sasum-base-065 | 1152 | 只采集不评判 |
| sasum-base-066 | 1280 | 只采集不评判 |
| sasum-base-067 | 1536 | 只采集不评判 |
| sasum-base-068 | 1920 | 只采集不评判 |
| sasum-base-069 | 2000 | 只采集不评判 |
| sasum-base-070 | 2047 | 只采集不评判 |
| sasum-base-071 | 2048 | 只采集不评判 |
| sasum-base-072 | 2049 | 只采集不评判 |
| sasum-base-073 | 2304 | 只采集不评判 |
| sasum-base-074 | 2560 | 只采集不评判 |
| sasum-base-075 | 3000 | 只采集不评判 |
| sasum-base-076 | 3072 | 只采集不评判 |
| sasum-base-077 | 3840 | 只采集不评判 |
| sasum-base-078 | 4095 | 只采集不评判 |
| sasum-base-079 | 4096 | 只采集不评判 |
| sasum-base-080 | 4097 | 只采集不评判 |
| sasum-base-081 | 4608 | 只采集不评判 |
| sasum-base-082 | 5000 | 只采集不评判 |
| sasum-base-083 | 5120 | 只采集不评判 |
| sasum-base-084 | 6144 | 只采集不评判 |
| sasum-base-085 | 7000 | 只采集不评判 |
| sasum-base-086 | 7680 | 只采集不评判 |
| sasum-base-087 | 8191 | 只采集不评判 |
| sasum-base-088 | 8192 | 只采集不评判 |
| sasum-base-089 | 8193 | 只采集不评判 |
| sasum-base-090 | 9216 | 只采集不评判 |
| sasum-base-091 | 10000 | 只采集不评判 |
| sasum-base-092 | 10240 | 只采集不评判 |
| sasum-base-093 | 12288 | 只采集不评判 |
| sasum-base-094 | 15360 | 只采集不评判 |
| sasum-base-095 | 16383 | 只采集不评判 |
| sasum-base-096 | 16384 | 只采集不评判 |
| sasum-base-097 | 16385 | 只采集不评判 |
| sasum-base-098 | 18432 | 只采集不评判 |
| sasum-base-099 | 20000 | 只采集不评判 |
| sasum-base-100 | 20480 | 只采集不评判 |
| sasum-base-101 | 24576 | 只采集不评判 |
| sasum-base-102 | 30000 | 只采集不评判 |
| sasum-base-103 | 30720 | 只采集不评判 |
| sasum-base-104 | 32767 | 只采集不评判 |
| sasum-base-105 | 32768 | 只采集不评判 |
| sasum-base-106 | 32769 | 只采集不评判 |
| sasum-base-107 | 36864 | 只采集不评判 |
| sasum-base-108 | 40960 | 只采集不评判 |
| sasum-base-109 | 49152 | 只采集不评判 |
| sasum-base-110 | 50000 | 只采集不评判 |
| sasum-base-111 | 61440 | 只采集不评判 |
| sasum-base-112 | 65535 | 只采集不评判 |
| sasum-base-113 | 65536 | 只采集不评判 |
| sasum-base-114 | 65537 | 只采集不评判 |
| sasum-base-115 | 70000 | 只采集不评判 |
| sasum-base-116 | 73728 | 只采集不评判 |
| sasum-base-117 | 81920 | 只采集不评判 |
| sasum-base-118 | 98304 | 只采集不评判 |
| sasum-base-119 | 100000 | 只采集不评判 |
| sasum-base-120 | 122880 | 只采集不评判 |
| sasum-base-121 | 131071 | 只采集不评判 |
| sasum-base-122 | 131072 | 只采集不评判 |
| sasum-base-123 | 131073 | 只采集不评判 |
| sasum-base-124 | 147456 | 只采集不评判 |
| sasum-base-125 | 163840 | 只采集不评判 |
| sasum-base-126 | 196608 | 只采集不评判 |
| sasum-base-127 | 200000 | 只采集不评判 |
| sasum-base-128 | 245760 | 只采集不评判 |
| sasum-base-129 | 262143 | 只采集不评判 |
| sasum-base-130 | 262144 | 只采集不评判 |
| sasum-base-131 | 262145 | 只采集不评判 |
| sasum-base-132 | 294912 | 只采集不评判 |
| sasum-base-133 | 300000 | 只采集不评判 |
| sasum-base-134 | 327680 | 只采集不评判 |
| sasum-base-135 | 393216 | 只采集不评判 |
| sasum-base-136 | 491520 | 只采集不评判 |
| sasum-base-137 | 500000 | 只采集不评判 |
| sasum-base-138 | 524287 | 只采集不评判 |
| sasum-base-139 | 524288 | 只采集不评判 |
| sasum-base-140 | 524289 | 只采集不评判 |
| sasum-base-141 | 589824 | 只采集不评判 |
| sasum-base-142 | 655360 | 只采集不评判 |
| sasum-base-143 | 700000 | 只采集不评判 |
| sasum-base-144 | 786432 | 只采集不评判 |
| sasum-base-145 | 983040 | 只采集不评判 |
| sasum-base-146 | 1000000 | 只采集不评判 |
| sasum-base-147 | 1048575 | 只采集不评判 |
| sasum-base-148 | 1048576 | 只采集不评判 |
| sasum-base-149 | 1048577 | 只采集不评判 |
| sasum-base-150 | 1179648 | 只采集不评判 |
| sasum-base-151 | 1310720 | 只采集不评判 |
| sasum-base-152 | 1572864 | 只采集不评判 |
| sasum-base-153 | 2000000 | 只采集不评判 |
| sasum-base-154 | 2097151 | 只采集不评判 |
| sasum-base-155 | 2097152 | 只采集不评判 |
| sasum-base-156 | 2097153 | 只采集不评判 |
| sasum-base-157 | 2359296 | 只采集不评判 |
| sasum-base-158 | 2621440 | 只采集不评判 |
| sasum-base-159 | 3000000 | 只采集不评判 |
| sasum-base-160 | 3145728 | 只采集不评判 |
| sasum-base-161 | 4194303 | 只采集不评判 |
| sasum-base-162 | 4194304 | 只采集不评判 |
| sasum-base-163 | 4194305 | 只采集不评判 |
| sasum-base-164 | 4718592 | 只采集不评判 |
| sasum-base-165 | 5000000 | 只采集不评判 |
| sasum-base-166 | 5242880 | 只采集不评判 |
| sasum-base-167 | 6291456 | 只采集不评判 |
| sasum-base-168 | 7000000 | 只采集不评判 |
| sasum-base-169 | 8388607 | 只采集不评判 |
| sasum-base-170 | 8388608 | 只采集不评判 |
| sasum-base-171 | 8388609 | 只采集不评判 |
| sasum-base-172 | 9437184 | 只采集不评判 |
| sasum-base-173 | 10000000 | 只采集不评判 |
| sasum-base-174 | 10485760 | 只采集不评判 |
| sasum-base-175 | 12582912 | 只采集不评判 |
| sasum-base-176 | 16777215 | 只采集不评判 |
| sasum-base-177 | 16777216 | 只采集不评判 |
| sasum-base-178 | 16777217 | 只采集不评判 |
| sasum-base-179 | 18874368 | 只采集不评判 |
| sasum-base-180 | 20000000 | 只采集不评判 |
| sasum-base-181 | 20971520 | 只采集不评判 |
| sasum-base-182 | 25165824 | 只采集不评判 |
| sasum-base-183 | 30000000 | 只采集不评判 |
| sasum-base-184 | 33554431 | 只采集不评判 |
| sasum-base-185 | 33554432 | 只采集不评判 |
| sasum-base-186 | 33554433 | 只采集不评判 |
| sasum-base-187 | 37748736 | 只采集不评判 |
| sasum-base-188 | 41943040 | 只采集不评判 |
| sasum-base-189 | 50000000 | 只采集不评判 |
| sasum-base-190 | 50331648 | 只采集不评判 |
| sasum-base-191 | 67108863 | 只采集不评判 |
| sasum-base-192 | 67108864 | 只采集不评判 |
| sasum-base-193 | 67108865 | 只采集不评判 |
| sasum-base-194 | 70000000 | 只采集不评判 |
| sasum-base-195 | 75497472 | 只采集不评判 |
| sasum-base-196 | 83886080 | 只采集不评判 |
| sasum-base-197 | 100000000 | 只采集不评判 |
| sasum-base-198 | 100663296 | 只采集不评判 |
| sasum-base-199 | 134217727 | 只采集不评判 |
| sasum-base-200 | 134217728 | 只采集不评判 |
