# coo2csr 任务包

## 来源

| 项目 | 值 |
| --- | --- |
| symbol | aclsparseXcoo2csr |
| returns | aclsparseStatus_t |
| family | conversion |
| schema_version | 2 |
| generator_version | 1 |
| sources.params | include/cann_ops_sparse.h aclsparseXcoo2csr（@5b2a5ba:2027） |
| sources.cases | test/coo2csr/{param.h,arch35/coo2csr_test.csv} 列契约与取值词表 |
| sources.perf | 无 GPU 对标（R1 预期 NO_REF），m 阶梯 2^k±1 加大值，nnz 上界见 case_controls 注 |

## 接口签名

```c
aclsparseStatus_t aclsparseXcoo2csr(
    aclsparseHandle_t handle,
    const int* cooRowInd,
    int nnz,
    int m,
    int* csrRowPtr,
    aclsparseIndexBase_t idxBase
);
```

开发者头文件声明须与此语义一致，验收阶段做归一化比对。

## 六件清单

| 文件 | 用途 |
| --- | --- |
| gen_csv.py | 事实表 FACTS 与独立 CSV 生成器 |
| coo2csr_test.csv | CSV 驱动用例 |
| verify_accuracy.py | 精度验收入口 |
| verify_performance.py | msprof kernel 性能验收入口 |
| README.md | 开发与验收契约 |
| gpu_baseline.csv | GPU 性能基线与元数据 |

## 列契约

| 列 | 来源参数 | 取值 | 说明 |
| --- | --- | --- | --- |
| case_name | 控制列 | TC_L0_/TC_PW_/TC_ED_/TC_PF_ 加块内编号 | GTest 参数名，任务包内唯一 |
| m | m | 整数 | 维度 |
| n | 控制列 | 1, 16, 256, 4096 | harness 造数控制（档位），值为原始字符串 |
| sparsity | 控制列 | 0.0, 0.5, 0.9, 0.995 | harness 造数控制（档位），值为原始字符串 |
| empty_row_prob | 控制列 | 0.0, 0.5, 0.8 | harness 造数控制（档位），值为原始字符串 |
| pattern | 控制列 | random, diag, allsame | harness 造数控制（枚举），值为原始字符串 |
| idx_base | 控制列 | 0, 1 | harness 造数控制（枚举），值为原始字符串 |
| expect_result | 控制列 | 见下方状态词表 | 取值必须在下方本算子精确词表内 |
| seed | 控制列 | 正整数 | harness 以该种子驱动本用例造数（派生细节由 harness 定义） |

`expect_result` 状态词表（本算子精确词表）：

- `SUCCESS`

完整表头（各行直接拼接）：

```text
case_name,m,n,sparsity,empty_row_prob,pattern,idx_base,expect_result,seed
```

## param.h 读列要求

本仓惯例是 csv_loader.h 的 `fillCustom(csv_map)` 通路：param.h 逐列
`parseString/parseInt/parseDouble(row, "列名")`。本包发射的每一列都必须
被读取；param.h 解析但本包未发射的列（如阈值列）由 csv_loader 缺列回退
默认值（0/0.0/空串），这是仓内既有契约，不要求 throw。

## Golden 要求

- `golden.kind`: `harness`
- golden 由开发者 harness 自带（golden.h），任务包不产外部 golden 实现
- golden dtype: `int32`

## 校验形态与阈值

校验由开发者 harness 自带（golden.h 与测试断言），任务包不声明 verify 面。

精度判定由开发者 harness 自带（golden.h；阈值列若存在，语义也由 harness 定义）。任务包不附加容差表。

## 用例块与覆盖

| 块 | 条数 | 规则 |
| --- | --- | --- |
| L0 | 2 | op enum 全组合与 4/8 快速覆盖 |
| PW | 54 | 确定性 pairwise 且通过 constraints/footprint |
| ED | 0 | 逐条 edge_cases.set 物化 |
| PF | 200 | perf.rows 与可选 sweep |

pairs 覆盖 309/309，infeasible 0 对。
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

一条 gtest 用例只调用被测接口一次，用例里不自行预热、不重复调用；预热与重复采样由
`verify_performance.py` 负责。msprof 采到的是整条用例的全部 kernel，多调一次就多算一次。

性能集只含本任务包 CSV 中能配到 GPU 基线的 `TC_PF_` 行，无基线的行不跑、只计数；每例
单独执行。每例先直接运行一次 GTest warm-up，再独立运行 5 次 msprof；每次把
`AI_CORE/AI_VECTOR_CORE/MIX_AIC/MIX_AIV` 的 `Task Duration(us)` 求和。最终 `kernel_us`
取五次和的中位数，`spread` 为 `(max-min)/median`。

性能键为 `m, n, sparsity`。`npu_ms = kernel_us/1000`，有 GPU 基线时计算
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
| source | 任务书无 GPU 基线，200 点按 m 三桶×(n,sparsity) 组合铺开（大 m 配小 n 封 nnz ≤ 2^24），全待填 |

基线行：

| id | m | n | sparsity | gpu_ms |
| --- | --- | --- | --- | --- |
| coo2csr-base-001 | 1 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-002 | 1 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-003 | 1 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-004 | 1 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-005 | 1 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-006 | 1 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-007 | 1 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-008 | 1 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-009 | 2 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-010 | 2 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-011 | 2 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-012 | 2 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-013 | 2 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-014 | 2 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-015 | 2 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-016 | 2 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-017 | 3 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-018 | 3 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-019 | 3 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-020 | 3 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-021 | 3 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-022 | 3 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-023 | 3 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-024 | 3 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-025 | 15 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-026 | 15 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-027 | 15 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-028 | 15 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-029 | 15 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-030 | 15 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-031 | 15 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-032 | 15 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-033 | 16 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-034 | 16 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-035 | 16 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-036 | 16 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-037 | 16 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-038 | 16 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-039 | 16 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-040 | 16 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-041 | 17 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-042 | 17 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-043 | 17 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-044 | 17 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-045 | 17 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-046 | 17 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-047 | 17 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-048 | 17 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-049 | 63 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-050 | 63 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-051 | 63 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-052 | 63 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-053 | 63 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-054 | 63 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-055 | 63 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-056 | 63 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-057 | 64 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-058 | 64 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-059 | 64 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-060 | 64 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-061 | 64 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-062 | 64 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-063 | 64 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-064 | 64 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-065 | 65 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-066 | 65 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-067 | 65 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-068 | 65 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-069 | 65 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-070 | 65 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-071 | 65 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-072 | 65 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-073 | 255 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-074 | 255 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-075 | 255 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-076 | 255 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-077 | 255 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-078 | 255 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-079 | 255 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-080 | 255 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-081 | 256 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-082 | 256 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-083 | 256 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-084 | 256 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-085 | 256 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-086 | 256 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-087 | 256 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-088 | 256 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-089 | 257 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-090 | 257 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-091 | 257 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-092 | 257 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-093 | 257 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-094 | 257 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-095 | 257 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-096 | 257 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-097 | 1023 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-098 | 1023 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-099 | 1023 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-100 | 1023 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-101 | 1023 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-102 | 1023 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-103 | 1023 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-104 | 1023 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-105 | 1024 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-106 | 1024 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-107 | 1024 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-108 | 1024 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-109 | 1024 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-110 | 1024 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-111 | 1024 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-112 | 1024 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-113 | 1025 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-114 | 1025 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-115 | 1025 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-116 | 1025 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-117 | 1025 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-118 | 1025 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-119 | 1025 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-120 | 1025 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-121 | 4095 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-122 | 4095 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-123 | 4095 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-124 | 4095 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-125 | 4095 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-126 | 4095 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-127 | 4095 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-128 | 4095 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-129 | 4096 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-130 | 4096 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-131 | 4096 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-132 | 4096 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-133 | 4096 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-134 | 4096 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-135 | 4096 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-136 | 4096 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-137 | 4097 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-138 | 4097 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-139 | 4097 | 4096 | 0.5 | 只采集不评判 |
| coo2csr-base-140 | 4097 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-141 | 4097 | 4096 | 0.9 | 只采集不评判 |
| coo2csr-base-142 | 4097 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-143 | 4097 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-144 | 4097 | 4096 | 0.995 | 只采集不评判 |
| coo2csr-base-145 | 16383 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-146 | 16383 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-147 | 16383 | 16 | 0.9 | 只采集不评判 |
| coo2csr-base-148 | 16383 | 16 | 0.995 | 只采集不评判 |
| coo2csr-base-149 | 16383 | 256 | 0.0 | 只采集不评判 |
| coo2csr-base-150 | 16383 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-151 | 16383 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-152 | 16383 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-153 | 16384 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-154 | 16384 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-155 | 16384 | 16 | 0.9 | 只采集不评判 |
| coo2csr-base-156 | 16384 | 16 | 0.995 | 只采集不评判 |
| coo2csr-base-157 | 16384 | 256 | 0.0 | 只采集不评判 |
| coo2csr-base-158 | 16384 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-159 | 16384 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-160 | 16384 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-161 | 16385 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-162 | 16385 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-163 | 16385 | 16 | 0.9 | 只采集不评判 |
| coo2csr-base-164 | 16385 | 16 | 0.995 | 只采集不评判 |
| coo2csr-base-165 | 16385 | 256 | 0.0 | 只采集不评判 |
| coo2csr-base-166 | 16385 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-167 | 16385 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-168 | 16385 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-169 | 65535 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-170 | 65535 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-171 | 65535 | 16 | 0.9 | 只采集不评判 |
| coo2csr-base-172 | 65535 | 16 | 0.995 | 只采集不评判 |
| coo2csr-base-173 | 65535 | 256 | 0.0 | 只采集不评判 |
| coo2csr-base-174 | 65535 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-175 | 65535 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-176 | 65535 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-177 | 65536 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-178 | 65536 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-179 | 65536 | 16 | 0.9 | 只采集不评判 |
| coo2csr-base-180 | 65536 | 16 | 0.995 | 只采集不评判 |
| coo2csr-base-181 | 65536 | 256 | 0.0 | 只采集不评判 |
| coo2csr-base-182 | 65536 | 256 | 0.5 | 只采集不评判 |
| coo2csr-base-183 | 65536 | 256 | 0.9 | 只采集不评判 |
| coo2csr-base-184 | 65536 | 256 | 0.995 | 只采集不评判 |
| coo2csr-base-185 | 262144 | 1 | 0.0 | 只采集不评判 |
| coo2csr-base-186 | 262144 | 1 | 0.5 | 只采集不评判 |
| coo2csr-base-187 | 262144 | 1 | 0.9 | 只采集不评判 |
| coo2csr-base-188 | 262144 | 1 | 0.995 | 只采集不评判 |
| coo2csr-base-189 | 262144 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-190 | 262144 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-191 | 262144 | 16 | 0.9 | 只采集不评判 |
| coo2csr-base-192 | 262144 | 16 | 0.995 | 只采集不评判 |
| coo2csr-base-193 | 1048576 | 1 | 0.0 | 只采集不评判 |
| coo2csr-base-194 | 1048576 | 1 | 0.5 | 只采集不评判 |
| coo2csr-base-195 | 1048576 | 1 | 0.9 | 只采集不评判 |
| coo2csr-base-196 | 1048576 | 1 | 0.995 | 只采集不评判 |
| coo2csr-base-197 | 1048576 | 16 | 0.0 | 只采集不评判 |
| coo2csr-base-198 | 1048576 | 16 | 0.5 | 只采集不评判 |
| coo2csr-base-199 | 1048576 | 16 | 0.9 | 只采集不评判 |
| coo2csr-base-200 | 1048576 | 16 | 0.995 | 只采集不评判 |
