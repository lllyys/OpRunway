# 精度标准

社区任务书里那句「精度需满足 AscendOpTest 工具默认阈值」，对应 YAML 里的
`standard.acc: default`，也就是 ATK 的生态算子开源混合容差标准。

**不要凭记忆写 rtol/atol，也不要在 YAML 里手写阈值。** 填 `default` 就是全部，
ATK 从内置配置读阈值。本文给出阈值只为让你看懂报告，不是让你抄进 YAML。

## 配置

```yaml
standard:
  acc: default
  perf: not_key
```

| `acc` 取值 | 含义 | 何时用 |
| --- | --- | --- |
| `default` | 生态算子开源混合容差标准 | 默认，社区任务全部用这个 |
| `mixed_tolerance_bm` | 同上，显式写法 | 与 `default` 等价，没必要换 |
| `single_bm` | 旧单标杆标准 | 只为兼容老用例，新用例不用 |

`perf` 填 `not_key` —— 性能在跑测侧单独跑任务，用例设计阶段不设性能阈值。

## 判定逻辑

逐元素通过条件：

```text
|actual − golden| ≤ atol + rtol × |golden|
```

用例整体通过要同时满足两条：

1. `matched_ratio ≥ 0.99`（通过元素数占比）
2. `max_abs_error ≤ max_abs_error_limit`

## 阈值表

| dtype | rtol | atol | max_abs_error_limit |
| --- | --- | --- | --- |
| FLOAT16 | 2⁻⁹ (1.95e-3) | 2⁻¹⁴ (6.10e-5) | 1e-1 |
| BFLOAT16 | 2⁻⁶ (1.56e-2) | 2⁻¹⁰ (9.77e-4) | 1e-0 |
| FLOAT32 | 2⁻¹⁰ (9.77e-4) | 2⁻¹⁶ (1.53e-5) | 1e-2 |
| HiFLOAT32 | 2⁻¹⁰ (9.77e-4) | 2⁻¹⁶ (1.53e-5) | 1e-2 |
| FLOAT8 E4M3 | 2⁻² (0.25) | 2⁻⁴ (0.0625) | 1e-0 |
| FLOAT8 E5M2 | 2⁻¹ (0.5) | 2⁻³ (0.125) | 1e-1 |

`required_matched_ratio` 全部 dtype 都是 0.99。

## 整型与搬运类算子

**上表只适用于浮点计算类算子。** 整型计算类与搬运类算子不在该标准的讨论范围内。

ATK 的 `default` 比较器按每个输出张量的 dtype 分别选路：浮点走上表的混合容差，
整型自动落到逐元素相等。所以 `default` 对整型算子同样适用，不需要换标准。

三个例外要留意：

| 情形 | 现象 | 怎么办 |
| --- | --- | --- |
| int8 输出 | ATK 按量化输出处理，容忍 ±1 | 若算子不是量化语义，报告里单独说明 |
| 输出取自输入（median、topk） | 值域与输入相同，不产生新误差 | 精度失败基本是选序逻辑错，不是容差问题 |
| indices 类整型输出 | 平局时下标可能不同但值相同 | 文档写明「首个出现的下标」时，不同即缺陷 |

## 数据生成规则

ATK 按标准生成输入数据，YAML 里的 `ranges.valid.values: [[-5, 5]]` 对应的就是它：

| 分布 | 占比 | 值域 |
| --- | --- | --- |
| 均匀分布 | 50% | [-5, 5] |
| 正态分布 | 50% | μ ∈ [-5, 5]，σ ∈ [0.1, 2] |

要显式指定正态分布参数时写 `random_types`：

```yaml
random_types: [{name: nd, mean: [-5, 5], std: [0.1, 2]}]
```

不写就是 ATK 默认行为，社区算子任务一般不需要写。

## 标准来源

阈值真源是 CANN opbase 仓的算子精度实验标准：

<https://gitcode.com/cann/opbase/blob/master/docs/zh/ops_precision_standard/experimental_standard.md>

本文抄自 ATK submodule 的 `skill/atk-quality-guard/references/experimental_standard.md`。
上游标准更新时以链接为准，本文要跟着改。
