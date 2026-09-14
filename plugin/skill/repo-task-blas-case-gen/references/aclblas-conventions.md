# aclblas 事实约定

## 目录

- [enum 短记号](#enum-短记号)
- [状态码约定](#状态码约定)
- [参数与内存](#参数与内存)
- [fill 串语义](#fill-串语义)
- [缺少 C 原型时](#缺少-c-原型时)
- [任务书排除的场景](#任务书排除的场景)
- [精度阈值](#精度阈值)
- [性能元数据](#性能元数据)

填写 enum、状态码、nullable 和性能元数据前，必须先读本文件。**case-gen 运行时的输入只有
任务书，接口事实全部取自任务书**；本表只补充任务书未写明的仓内约定。

## enum 短记号

下表来自 `test/frame/csv_loader.h` 的解析表：FillMode 249–258 行、Diag 261–270 行、
Side 273–281 行、Operation 284–297 行、ComputeType 300–326 行、DataType 329–343 行。

| ctype                  | FACTS `values` 允许值             |
| ---------------------- | ------------------------------ |
| `aclblasFillMode_t`    | `UPPER`, `LOWER`               |
| `aclblasOperation_t`   | `N`, `T`, `C`                  |
| `aclblasSideMode_t`    | `LEFT`, `RIGHT`                |
| `aclblasDiagType_t`    | `NON_UNIT`, `UNIT`             |
| `aclDataType`          | `FP16`, `FP32`, `BF16`, `INT8` |
| `aclblasComputeType_t` | 见下方代码块                         |

```text
COMPUTE_16F, COMPUTE_16F_PEDANTIC, COMPUTE_32F, COMPUTE_32F_PEDANTIC,
COMPUTE_32F_FAST_16F, COMPUTE_32F_FAST_16BF, COMPUTE_32F_FAST_TF32,
COMPUTE_64F, COMPUTE_64F_PEDANTIC, COMPUTE_32I, COMPUTE_32I_PEDANTIC
```

不要写 `ACLBLAS_` 全名，也不要写 `U/L` 等单字母替代值。`ACL_FLOAT` 应写成 `FP32`。
`enum_kind=compute` 通常对应 `aclblasComputeType_t`；若任务书明确把 `executionType`
声明为 `aclDataType`，保留该声明并使用 `FP16/FP32/BF16/INT8` 短记号。

## 状态码约定

对 `test/**/*_test.csv` 的 `expect_result` 实际统计覆盖 74 个 CSV，其中 72 个含该列，
共 4278 行。按全名和短名归一化后：SUCCESS 3614、INVALID\_VALUE 584、
NOT\_SUPPORTED 51、INVALID\_ENUM 16、HANDLE\_IS\_NULLPTR 12、NOT\_INITIALIZED 1。
原始计数命令是 `grep expect_result test/**/*_test.csv` 所定位文件上的 CSV 行统计。

任务书写明状态码时任务书优先。任务书未写时按以下约定填：

| 场景          | `expect`                        |
| ----------- | ------------------------------- |
| 参数为 nullptr | `ACLBLAS_STATUS_INVALID_VALUE`  |
| 维度为负        | `ACLBLAS_STATUS_INVALID_VALUE`  |
| 维度为零        | `ACLBLAS_STATUS_SUCCESS`，按空操作处理 |
| `inc=0`     | `ACLBLAS_STATUS_INVALID_VALUE`  |
| ld 小于下界     | `ACLBLAS_STATUS_INVALID_VALUE`  |
| dtype 组合不支持 | `ACLBLAS_STATUS_NOT_SUPPORTED`  |

若 dgmm 类任务书明确允许零或负 inc，以任务书为准。非法 enum 在仓内新旧算子中分别
使用 `INVALID_ENUM` 与 `INVALID_VALUE`；CSV 无法表达词表之外的 enum，因此不写此类 edge。
`handle=nullptr` 由开发者 `TEST_F` 覆盖，期望 `HANDLE_IS_NULLPTR`，不进入 CSV。

## 参数与内存

- handle 恒为首参，写成 `aclblasHandle_t` 与 `role=handle`。
- 标量 `mem` 写 `device`。任务书写 Host/Device 皆可时仍选 device，并在 `sources` 注明。
- 参数名及大小写跟任务书的 C 原型或参数表声明。
- 矩阵名用大写，向量和标量名用小写，除非任务书声明使用了不同名称。

`sources.params` 写任务书里参数表或签名所在的章节，例如 `任务书 §2.3`；
其他 `sources` 键记录 golden、perf 等事实各自的任务书出处。

## fill 串语义

fill 串遵循 `METHOD_PATTERN_VAL...`。语法来自 `test/frame/fill.h:37-42`，默认值与
生成器选择来自同文件 147–180、221–260 行；四个默认 tier 的实际语义如下：

| token            | 实际生成值                                           |
| ---------------- | ----------------------------------------------- |
| `RANDOM_NORM_1`  | 由当前行 seed 驱动，在闭区间 `[-1, 1]` 均匀采样                |
| `VALUE_NORM_0`   | 每个元素固定为 `0`                                     |
| `RANDOM_ALTER`   | 按下标生成 `1,-2,3,-4,...`；ALTER 覆盖 RANDOM 方法        |
| `RANDOM_EXTREME` | 循环 `1,0,-1,FLT_MAX,FLT_MIN,-FLT_MAX,denorm_min` |

任务书要求的分布若不能由现有 token 表达，就在 `sources` 记录原要求并报告能力边界，
不要编造新 token。
只有修改 `fill.h`、生成器词表与消费侧后，才能新增分布语义。

## 缺少 C 原型时

case-gen 的输入只有任务书，接口事实全部取自任务书。任务书不足以唯一确定任一必需 FACTS
时，无法填 → 停止，报告能力边界，不猜造签名。
`op` 可从接口名确定：Ex 类接口（无 `s/d/c/z` 前缀）用稳定蛇形名，如 `aclblasAxpyEx` → `axpy_ex`。
`family` 走与停止条件同一规则——任务书给出，或无歧义 typed-BLAS 去前缀，否则停止；不因 Ex 推定。

## 任务书排除的场景

任务书明确说“验收不构造”的场景不应换一种方式偷偷生成。例如任务书排除 `incx != 1`
或 nullptr 时，不设 `nullable`、不写相应 edge；用 `constraints` 或 `cases.inc_tiers`
收窄轴，并在 `sources` 写明依据。任务书给出合法性约束但没有给失败状态码时，也只收窄
合法域，不生成越界 edge。

edge 必须同时有可追溯的 `expect`，不能按惯例猜状态码。

## 精度阈值

精度阈值不进入 FACTS；README 按生态固定阈值表渲染判据。FACTS 只描述接口事实，
不要为单个任务复制一份阈值。
阈值变化应修改统一生态表和消费侧，不在 sources 中覆盖。

## 性能元数据

任务书给出 GPU 型号、库或计时口径时，必须填写 `perf.meta` 的 `device`、`library`、
`timing_scope` 与 `source`。缺失字段会在 `gpu_baseline.csv` 中写成 `unspecified`，
验收侧必须给出 timing scope caveat，不能把未知口径当成可直接比较的基线。
