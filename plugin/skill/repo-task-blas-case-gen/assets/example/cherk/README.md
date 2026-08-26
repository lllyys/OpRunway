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
| sources.perf | 任务书 §3.3 |

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
| A_fill | A | 见下方 fill 词表 | A（in）的数据填充方式 |
| lda | lda | 整数 | A 的前导维度，≥ max(1, rows) |
| beta | beta | 实数 | 实数标量 |
| C_fill | C | 见下方 fill 词表 | C（inout）的数据填充方式 |
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
case_name,description,uplo,trans,n,k,alpha,A_fill,lda,beta,C_fill,ldc,expect_result,nullAlpha,
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
| PF | 4 | perf.rows 与可选 sweep |

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

性能集只含部署 CSV 的 `TC_PF_` 行，每例单独执行。每例先直接运行一次 GTest warm-up，
再独立运行 5 次 msprof；每次把 `AI_CORE/AI_VECTOR_CORE/MIX_AIC/MIX_AIV` 的
`Task Duration(us)` 求和。最终 `kernel_us` 取五次和的中位数，`spread` 为
`(max-min)/median`。

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

msprof 输出目录模式、列名和 task 类型集合尚待目标机实测。当前表驱动常量及变更边界见
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
| cherk-base-001 | 1024 | 1024 | UPPER | N | 0.314 |
| cherk-base-002 | 2048 | 2048 | UPPER | N | 1.929 |
| cherk-base-003 | 1024 | 1024 | LOWER | C | 0.25 |
| cherk-base-004 | 2048 | 2048 | LOWER | C | 2.024 |
