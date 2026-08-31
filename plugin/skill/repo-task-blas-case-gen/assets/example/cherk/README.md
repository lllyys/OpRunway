# cherk 任务包

## 来源

| 项目 | 值 |
| --- | --- |
| symbol | aclblasCherk |
| returns | aclblasStatus_t |
| family | herk |
| schema_version | 1 |
| generator_version | 1 |
| sources.params | 任务书 §2.3 与 §2.4 |
| sources.golden | 任务书 §3.2 |
| sources.perf | 任务书 §3.3 的 4 个 GPU 点；其余 196 点为沿其参数轴补设计，gpu_ms 待填 |

## 接口签名

```c
aclblasStatus_t aclblasCherk(
    aclblasHandle_t handle,
    aclblasFillMode_t uplo,
    aclblasOperation_t trans,
    int n,
    int k,
    const float* alpha,
    const aclblasComplex* A,
    int lda,
    const float* beta,
    aclblasComplex* C,
    int ldc
);
```

开发者头文件声明须与此语义一致，验收阶段做归一化比对。

## 六件清单

| 文件 | 用途 |
| --- | --- |
| gen_csv.py | 事实表 FACTS 与独立 CSV 生成器 |
| cherk_test.csv | CSV 驱动用例 |
| verify_accuracy.py | 精度验收入口 |
| verify_performance.py | msprof kernel 性能验收入口 |
| README.md | 开发与验收契约 |
| gpu_baseline.csv | GPU 性能基线与元数据 |

## 列契约

| 列 | 来源参数 | 取值 | 说明 |
| --- | --- | --- | --- |
| case_name | 控制列 | TC_L0_/TC_PW_/TC_ED_/TC_PF_ 加块内编号 | GTest 参数名，任务包内唯一 |
| description | 控制列 | 非空字符串 | 轴取值、edge 名或性能键值 |
| uplo | uplo | UPPER, LOWER | 枚举短记号：UPPER, LOWER |
| trans | trans | N, C | 枚举短记号：N, C |
| n | n | 整数 | 维度 |
| k | k | 整数 | 维度 |
| alpha | alpha | 实数 | 实数标量 |
| a_fill | A | 见下方 fill 词表 | A（in）的数据填充方式 |
| lda | lda | 整数 | A 的前导维度，≥ max(1, rows) |
| beta | beta | 实数 | 实数标量 |
| c_fill | C | 见下方 fill 词表 | C（inout）的数据填充方式 |
| ldc | ldc | 整数 | C 的前导维度，≥ max(1, rows) |
| expect_result | 控制列 | 见下方状态词表 | 使用 csv_loader.h parseStatus 支持的全名 |
| nullAlpha | alpha | 0, 1 | 1 时传 nullptr 且不分配该参数 |
| nullA | A | 0, 1 | 1 时传 nullptr 且不分配该参数 |
| nullBeta | beta | 0, 1 | 1 时传 nullptr 且不分配该参数 |
| nullC | C | 0, 1 | 1 时传 nullptr 且不分配该参数 |
| random_seed | 控制列 | 正整数 | 各缓冲依次使用 seed、seed+1……派生随机填充 |

fill 词表（语法见 `fill.h` 的 `METHOD_PATTERN_VAL`）：

- `RANDOM_NORM_1`
- `VALUE_NORM_0`
- `RANDOM_ALTER`
- `RANDOM_EXTREME`

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
case_name,description,uplo,trans,n,k,alpha,a_fill,lda,beta,c_fill,ldc,expect_result,nullAlpha,
nullA,nullBeta,nullC,random_seed
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
- `golden.symbol`: `cblas_cherk`
- golden dtype: `complex64`
- in-place 参数先快照原值再计算 golden：C

## 校验形态与阈值

| token | 校验对象 | 方式与阈值 |
| --- | --- | --- |
| uplo_triangle | uplo 指定三角 | 只核指定三角 |
| non_uplo_exact | 另一三角 | 逐位相等（EXACT） |
| hermitian_diag | Hermitian 对角线 | 虚部绝对值不超过 atol |

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
| L0 | 8 | op enum 全组合与 4/8 快速覆盖 |
| PW | 169 | 确定性 pairwise 且通过 constraints/footprint |
| ED | 2 | 逐条 edge_cases.set 物化 |
| PF | 200 | perf.rows 与可选 sweep |

pairs 覆盖 1041/1041，infeasible 0 对。
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

性能键为 `n, k, uplo, trans`。`npu_ms = kernel_us/1000`，有 GPU 基线时计算
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
| source | unspecified |

基线行：

| id | n | k | uplo | trans | gpu_ms |
| --- | --- | --- | --- | --- | --- |
| cherk-base-001 | 64 | 64 | UPPER | N | 只采集不评判 |
| cherk-base-002 | 64 | 64 | UPPER | C | 只采集不评判 |
| cherk-base-003 | 64 | 64 | LOWER | N | 只采集不评判 |
| cherk-base-004 | 64 | 64 | LOWER | C | 只采集不评判 |
| cherk-base-005 | 64 | 32 | UPPER | N | 只采集不评判 |
| cherk-base-006 | 64 | 32 | UPPER | C | 只采集不评判 |
| cherk-base-007 | 64 | 32 | LOWER | N | 只采集不评判 |
| cherk-base-008 | 64 | 32 | LOWER | C | 只采集不评判 |
| cherk-base-009 | 96 | 96 | UPPER | N | 只采集不评判 |
| cherk-base-010 | 96 | 96 | UPPER | C | 只采集不评判 |
| cherk-base-011 | 96 | 96 | LOWER | N | 只采集不评判 |
| cherk-base-012 | 96 | 96 | LOWER | C | 只采集不评判 |
| cherk-base-013 | 96 | 48 | UPPER | N | 只采集不评判 |
| cherk-base-014 | 96 | 48 | UPPER | C | 只采集不评判 |
| cherk-base-015 | 96 | 48 | LOWER | N | 只采集不评判 |
| cherk-base-016 | 96 | 48 | LOWER | C | 只采集不评判 |
| cherk-base-017 | 128 | 128 | UPPER | N | 只采集不评判 |
| cherk-base-018 | 128 | 128 | UPPER | C | 只采集不评判 |
| cherk-base-019 | 128 | 128 | LOWER | N | 只采集不评判 |
| cherk-base-020 | 128 | 128 | LOWER | C | 只采集不评判 |
| cherk-base-021 | 128 | 64 | UPPER | N | 只采集不评判 |
| cherk-base-022 | 128 | 64 | UPPER | C | 只采集不评判 |
| cherk-base-023 | 128 | 64 | LOWER | N | 只采集不评判 |
| cherk-base-024 | 128 | 64 | LOWER | C | 只采集不评判 |
| cherk-base-025 | 160 | 160 | UPPER | N | 只采集不评判 |
| cherk-base-026 | 160 | 160 | UPPER | C | 只采集不评判 |
| cherk-base-027 | 160 | 160 | LOWER | N | 只采集不评判 |
| cherk-base-028 | 160 | 160 | LOWER | C | 只采集不评判 |
| cherk-base-029 | 160 | 80 | UPPER | N | 只采集不评判 |
| cherk-base-030 | 160 | 80 | UPPER | C | 只采集不评判 |
| cherk-base-031 | 160 | 80 | LOWER | N | 只采集不评判 |
| cherk-base-032 | 160 | 80 | LOWER | C | 只采集不评判 |
| cherk-base-033 | 192 | 192 | UPPER | N | 只采集不评判 |
| cherk-base-034 | 192 | 192 | UPPER | C | 只采集不评判 |
| cherk-base-035 | 192 | 192 | LOWER | N | 只采集不评判 |
| cherk-base-036 | 192 | 192 | LOWER | C | 只采集不评判 |
| cherk-base-037 | 192 | 96 | UPPER | N | 只采集不评判 |
| cherk-base-038 | 192 | 96 | UPPER | C | 只采集不评判 |
| cherk-base-039 | 192 | 96 | LOWER | N | 只采集不评判 |
| cherk-base-040 | 192 | 96 | LOWER | C | 只采集不评判 |
| cherk-base-041 | 224 | 224 | UPPER | N | 只采集不评判 |
| cherk-base-042 | 224 | 224 | UPPER | C | 只采集不评判 |
| cherk-base-043 | 224 | 224 | LOWER | N | 只采集不评判 |
| cherk-base-044 | 224 | 224 | LOWER | C | 只采集不评判 |
| cherk-base-045 | 224 | 112 | UPPER | N | 只采集不评判 |
| cherk-base-046 | 224 | 112 | UPPER | C | 只采集不评判 |
| cherk-base-047 | 224 | 112 | LOWER | N | 只采集不评判 |
| cherk-base-048 | 224 | 112 | LOWER | C | 只采集不评判 |
| cherk-base-049 | 256 | 256 | UPPER | N | 只采集不评判 |
| cherk-base-050 | 256 | 256 | UPPER | C | 只采集不评判 |
| cherk-base-051 | 256 | 256 | LOWER | N | 只采集不评判 |
| cherk-base-052 | 256 | 256 | LOWER | C | 只采集不评判 |
| cherk-base-053 | 256 | 128 | UPPER | N | 只采集不评判 |
| cherk-base-054 | 256 | 128 | UPPER | C | 只采集不评判 |
| cherk-base-055 | 256 | 128 | LOWER | N | 只采集不评判 |
| cherk-base-056 | 256 | 128 | LOWER | C | 只采集不评判 |
| cherk-base-057 | 320 | 320 | UPPER | N | 只采集不评判 |
| cherk-base-058 | 320 | 320 | UPPER | C | 只采集不评判 |
| cherk-base-059 | 320 | 320 | LOWER | N | 只采集不评判 |
| cherk-base-060 | 320 | 320 | LOWER | C | 只采集不评判 |
| cherk-base-061 | 320 | 160 | UPPER | N | 只采集不评判 |
| cherk-base-062 | 320 | 160 | UPPER | C | 只采集不评判 |
| cherk-base-063 | 320 | 160 | LOWER | N | 只采集不评判 |
| cherk-base-064 | 320 | 160 | LOWER | C | 只采集不评判 |
| cherk-base-065 | 384 | 384 | UPPER | N | 只采集不评判 |
| cherk-base-066 | 384 | 384 | UPPER | C | 只采集不评判 |
| cherk-base-067 | 384 | 384 | LOWER | N | 只采集不评判 |
| cherk-base-068 | 384 | 384 | LOWER | C | 只采集不评判 |
| cherk-base-069 | 384 | 192 | UPPER | N | 只采集不评判 |
| cherk-base-070 | 384 | 192 | UPPER | C | 只采集不评判 |
| cherk-base-071 | 384 | 192 | LOWER | N | 只采集不评判 |
| cherk-base-072 | 384 | 192 | LOWER | C | 只采集不评判 |
| cherk-base-073 | 448 | 448 | UPPER | N | 只采集不评判 |
| cherk-base-074 | 448 | 448 | UPPER | C | 只采集不评判 |
| cherk-base-075 | 448 | 448 | LOWER | N | 只采集不评判 |
| cherk-base-076 | 448 | 448 | LOWER | C | 只采集不评判 |
| cherk-base-077 | 448 | 224 | UPPER | N | 只采集不评判 |
| cherk-base-078 | 448 | 224 | UPPER | C | 只采集不评判 |
| cherk-base-079 | 448 | 224 | LOWER | N | 只采集不评判 |
| cherk-base-080 | 448 | 224 | LOWER | C | 只采集不评判 |
| cherk-base-081 | 512 | 512 | UPPER | N | 只采集不评判 |
| cherk-base-082 | 512 | 512 | UPPER | C | 只采集不评判 |
| cherk-base-083 | 512 | 512 | LOWER | N | 只采集不评判 |
| cherk-base-084 | 512 | 512 | LOWER | C | 只采集不评判 |
| cherk-base-085 | 512 | 256 | UPPER | N | 只采集不评判 |
| cherk-base-086 | 512 | 256 | UPPER | C | 只采集不评判 |
| cherk-base-087 | 512 | 256 | LOWER | N | 只采集不评判 |
| cherk-base-088 | 512 | 256 | LOWER | C | 只采集不评判 |
| cherk-base-089 | 640 | 640 | UPPER | N | 只采集不评判 |
| cherk-base-090 | 640 | 640 | UPPER | C | 只采集不评判 |
| cherk-base-091 | 640 | 640 | LOWER | N | 只采集不评判 |
| cherk-base-092 | 640 | 640 | LOWER | C | 只采集不评判 |
| cherk-base-093 | 640 | 320 | UPPER | N | 只采集不评判 |
| cherk-base-094 | 640 | 320 | UPPER | C | 只采集不评判 |
| cherk-base-095 | 640 | 320 | LOWER | N | 只采集不评判 |
| cherk-base-096 | 640 | 320 | LOWER | C | 只采集不评判 |
| cherk-base-097 | 728 | 728 | UPPER | N | 只采集不评判 |
| cherk-base-098 | 728 | 728 | UPPER | C | 只采集不评判 |
| cherk-base-099 | 728 | 728 | LOWER | N | 只采集不评判 |
| cherk-base-100 | 728 | 728 | LOWER | C | 只采集不评判 |
| cherk-base-101 | 728 | 364 | UPPER | N | 只采集不评判 |
| cherk-base-102 | 728 | 364 | UPPER | C | 只采集不评判 |
| cherk-base-103 | 728 | 364 | LOWER | N | 只采集不评判 |
| cherk-base-104 | 728 | 364 | LOWER | C | 只采集不评判 |
| cherk-base-105 | 768 | 768 | UPPER | N | 只采集不评判 |
| cherk-base-106 | 768 | 768 | UPPER | C | 只采集不评判 |
| cherk-base-107 | 768 | 768 | LOWER | N | 只采集不评判 |
| cherk-base-108 | 768 | 768 | LOWER | C | 只采集不评判 |
| cherk-base-109 | 768 | 384 | UPPER | N | 只采集不评判 |
| cherk-base-110 | 768 | 384 | UPPER | C | 只采集不评判 |
| cherk-base-111 | 768 | 384 | LOWER | N | 只采集不评判 |
| cherk-base-112 | 768 | 384 | LOWER | C | 只采集不评判 |
| cherk-base-113 | 896 | 896 | UPPER | N | 只采集不评判 |
| cherk-base-114 | 896 | 896 | UPPER | C | 只采集不评判 |
| cherk-base-115 | 896 | 896 | LOWER | N | 只采集不评判 |
| cherk-base-116 | 896 | 896 | LOWER | C | 只采集不评判 |
| cherk-base-117 | 896 | 448 | UPPER | N | 只采集不评判 |
| cherk-base-118 | 896 | 448 | UPPER | C | 只采集不评判 |
| cherk-base-119 | 896 | 448 | LOWER | N | 只采集不评判 |
| cherk-base-120 | 896 | 448 | LOWER | C | 只采集不评判 |
| cherk-base-121 | 1000 | 1000 | UPPER | N | 只采集不评判 |
| cherk-base-122 | 1000 | 1000 | UPPER | C | 只采集不评判 |
| cherk-base-123 | 1000 | 1000 | LOWER | N | 只采集不评判 |
| cherk-base-124 | 1000 | 1000 | LOWER | C | 只采集不评判 |
| cherk-base-125 | 1000 | 500 | UPPER | N | 只采集不评判 |
| cherk-base-126 | 1000 | 500 | UPPER | C | 只采集不评判 |
| cherk-base-127 | 1000 | 500 | LOWER | N | 只采集不评判 |
| cherk-base-128 | 1000 | 500 | LOWER | C | 只采集不评判 |
| cherk-base-129 | 1024 | 1024 | UPPER | N | 0.314 |
| cherk-base-130 | 1024 | 1024 | UPPER | C | 只采集不评判 |
| cherk-base-131 | 1024 | 1024 | LOWER | N | 只采集不评判 |
| cherk-base-132 | 1024 | 1024 | LOWER | C | 0.25 |
| cherk-base-133 | 1024 | 512 | UPPER | N | 只采集不评判 |
| cherk-base-134 | 1024 | 512 | UPPER | C | 只采集不评判 |
| cherk-base-135 | 1024 | 512 | LOWER | N | 只采集不评判 |
| cherk-base-136 | 1024 | 512 | LOWER | C | 只采集不评判 |
| cherk-base-137 | 1280 | 1280 | UPPER | N | 只采集不评判 |
| cherk-base-138 | 1280 | 1280 | UPPER | C | 只采集不评判 |
| cherk-base-139 | 1280 | 1280 | LOWER | N | 只采集不评判 |
| cherk-base-140 | 1280 | 1280 | LOWER | C | 只采集不评判 |
| cherk-base-141 | 1280 | 640 | UPPER | N | 只采集不评判 |
| cherk-base-142 | 1280 | 640 | UPPER | C | 只采集不评判 |
| cherk-base-143 | 1280 | 640 | LOWER | N | 只采集不评判 |
| cherk-base-144 | 1280 | 640 | LOWER | C | 只采集不评判 |
| cherk-base-145 | 1536 | 1536 | UPPER | N | 只采集不评判 |
| cherk-base-146 | 1536 | 1536 | UPPER | C | 只采集不评判 |
| cherk-base-147 | 1536 | 1536 | LOWER | N | 只采集不评判 |
| cherk-base-148 | 1536 | 1536 | LOWER | C | 只采集不评判 |
| cherk-base-149 | 1536 | 768 | UPPER | N | 只采集不评判 |
| cherk-base-150 | 1536 | 768 | UPPER | C | 只采集不评判 |
| cherk-base-151 | 1536 | 768 | LOWER | N | 只采集不评判 |
| cherk-base-152 | 1536 | 768 | LOWER | C | 只采集不评判 |
| cherk-base-153 | 1792 | 1792 | UPPER | N | 只采集不评判 |
| cherk-base-154 | 1792 | 1792 | UPPER | C | 只采集不评判 |
| cherk-base-155 | 1792 | 1792 | LOWER | N | 只采集不评判 |
| cherk-base-156 | 1792 | 1792 | LOWER | C | 只采集不评判 |
| cherk-base-157 | 1792 | 896 | UPPER | N | 只采集不评判 |
| cherk-base-158 | 1792 | 896 | UPPER | C | 只采集不评判 |
| cherk-base-159 | 1792 | 896 | LOWER | N | 只采集不评判 |
| cherk-base-160 | 1792 | 896 | LOWER | C | 只采集不评判 |
| cherk-base-161 | 2048 | 2048 | UPPER | N | 1.929 |
| cherk-base-162 | 2048 | 2048 | UPPER | C | 只采集不评判 |
| cherk-base-163 | 2048 | 2048 | LOWER | N | 只采集不评判 |
| cherk-base-164 | 2048 | 2048 | LOWER | C | 2.024 |
| cherk-base-165 | 2048 | 1024 | UPPER | N | 只采集不评判 |
| cherk-base-166 | 2048 | 1024 | UPPER | C | 只采集不评判 |
| cherk-base-167 | 2048 | 1024 | LOWER | N | 只采集不评判 |
| cherk-base-168 | 2048 | 1024 | LOWER | C | 只采集不评判 |
| cherk-base-169 | 2560 | 2560 | UPPER | N | 只采集不评判 |
| cherk-base-170 | 2560 | 2560 | UPPER | C | 只采集不评判 |
| cherk-base-171 | 2560 | 2560 | LOWER | N | 只采集不评判 |
| cherk-base-172 | 2560 | 2560 | LOWER | C | 只采集不评判 |
| cherk-base-173 | 2560 | 1280 | UPPER | N | 只采集不评判 |
| cherk-base-174 | 2560 | 1280 | UPPER | C | 只采集不评判 |
| cherk-base-175 | 2560 | 1280 | LOWER | N | 只采集不评判 |
| cherk-base-176 | 2560 | 1280 | LOWER | C | 只采集不评判 |
| cherk-base-177 | 3072 | 3072 | UPPER | N | 只采集不评判 |
| cherk-base-178 | 3072 | 3072 | UPPER | C | 只采集不评判 |
| cherk-base-179 | 3072 | 3072 | LOWER | N | 只采集不评判 |
| cherk-base-180 | 3072 | 3072 | LOWER | C | 只采集不评判 |
| cherk-base-181 | 3072 | 1536 | UPPER | N | 只采集不评判 |
| cherk-base-182 | 3072 | 1536 | UPPER | C | 只采集不评判 |
| cherk-base-183 | 3072 | 1536 | LOWER | N | 只采集不评判 |
| cherk-base-184 | 3072 | 1536 | LOWER | C | 只采集不评判 |
| cherk-base-185 | 3584 | 3584 | UPPER | N | 只采集不评判 |
| cherk-base-186 | 3584 | 3584 | UPPER | C | 只采集不评判 |
| cherk-base-187 | 3584 | 3584 | LOWER | N | 只采集不评判 |
| cherk-base-188 | 3584 | 3584 | LOWER | C | 只采集不评判 |
| cherk-base-189 | 3584 | 1792 | UPPER | N | 只采集不评判 |
| cherk-base-190 | 3584 | 1792 | UPPER | C | 只采集不评判 |
| cherk-base-191 | 3584 | 1792 | LOWER | N | 只采集不评判 |
| cherk-base-192 | 3584 | 1792 | LOWER | C | 只采集不评判 |
| cherk-base-193 | 4096 | 4096 | UPPER | N | 只采集不评判 |
| cherk-base-194 | 4096 | 4096 | UPPER | C | 只采集不评判 |
| cherk-base-195 | 4096 | 4096 | LOWER | N | 只采集不评判 |
| cherk-base-196 | 4096 | 4096 | LOWER | C | 只采集不评判 |
| cherk-base-197 | 4096 | 2048 | UPPER | N | 只采集不评判 |
| cherk-base-198 | 4096 | 2048 | UPPER | C | 只采集不评判 |
| cherk-base-199 | 4096 | 2048 | LOWER | N | 只采集不评判 |
| cherk-base-200 | 4096 | 2048 | LOWER | C | 只采集不评判 |
