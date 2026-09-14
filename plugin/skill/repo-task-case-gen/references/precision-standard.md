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

用例整体通过要**同时**满足两条：

```text
① matched_ratio = mean(isclose(actual, golden, rtol, atol)) ≥ required_matched_ratio
② 每个元素都满足  abs_error ≤ max_abs_error_limit  或  abs_error ≤ 32 × ULP(golden)
```

条件 ② 的 ULP 分支容易被漏掉：**绝对误差超了 `max_abs_error_limit` 也未必判失败**，
只要它落在标杆值 32 个 ULP 之内就算过。大数值区间靠的就是这一支。

报告里的失败文案对应这两条：

| 文案 | 是哪一条没过 |
| --- | --- |
| `元素点通过率matched_ratio为X，低于预期阈值Y` | ① |
| `存在N个元素点的abs_error同时大于max_abs_error_limit阈值X和32倍ULP值` | ② |

## 阈值表

**以 ATK 代码为准**（`atk/configs/mixed_tolerance_benchmark_config.py`），跑测时读的
就是这里的数：

| dtype | rtol | atol | max_abs_error_limit |
| --- | --- | --- | --- |
| FLOAT16 | 1.95e-3 | 1.95e-3 | 1e-1 |
| BFLOAT16 | 1.56e-2 | 1.56e-2 | 1e0 |
| FLOAT32 | 9.77e-4 | 1.53e-5 | 1e-2 |
| HiFLOAT32 | 9.77e-4 | 1.53e-5 | 1e-2 |
| FLOAT8 E4M3 | 1.25e-1 | 1.25e-1 | 5e-1 |
| FLOAT8 E5M2 | 2.5e-1 | 2.5e-1 | 1e0 |
| COMPLEX64 | 9.77e-4 | 1.53e-5 | 1e-2 |

`required_matched_ratio` 全部 dtype 都是 0.99，`max_ulp_multiple` 全部是 32。

COMPLEX64 没有自己的阈值项，`get_threshold` 把它归一到 `fp32`，ULP 也按 fp32 取
（`mixed_tolerance_benchmark_config.py:186-190`、`:60-71`）。任务书写「complex64 的实部
和虚部分别按 float32 的混合容差参数比对」时，**这就是 `acc: default` 的既有行为，
不用另配**。

**注意 rtol 与 atol 相等的那四行不是笔误**：fp16、bf16 与两种 fp8 在 ATK 代码里
两者取同一个值。本文早先按「atol = rtol 再降几阶」写，六行里错了四行——那张表
只用来读报告，读错就会把通过的当成不通过。

用例集是否达标另有一层：`acc_pass = 1`（`atk/configs/standard_config.py`），
即**逐用例通过率必须 100%**，一条不过整批就不达标。

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

**跑测时真正生效的是 ATK 代码里的数**，不是标准文档里的数。上表照
`atk/configs/mixed_tolerance_benchmark_config.py` 抄，核对方式：

```bash
sed -n '/^class MixedToleranceBenchmarkConfig/,/def update/p' \
    third_party/ATK/atk/configs/mixed_tolerance_benchmark_config.py
```

标准文档是 CANN opbase 仓的算子精度实验标准，写规格时看它，读报告时看上表：

<https://gitcode.com/cann/opbase/blob/master/docs/zh/ops_precision_standard/experimental_standard.md>

**两者不一致时以 ATK 代码为准并在这里记一笔**——2026-08-31 就发现本文六行阈值
错了四行，因为一直照文档抄而没核代码。升 ATK submodule 之后重跑上面那条 `sed`。


## 标杆是谁决定结论说到哪一步

`accuracy.kind=plugin` 时 `baseline` 改填执行器的注册名（`function_<op>_cpu`），
与 YAML 的 `api_type` 逐字相同。写法见
[plugin-authoring.md](plugin-authoring.md)「执行器」。

这是 ATK 的正规形态，不是绕路：`DesignConfig` 的 `name` 是 `Optional`，
`api_type` 单独就能定执行器（`atk/configs/design_config.py:449, 456`）。

**降级与否按标杆是谁判，不按用没用执行器判。** `plugin` 这一档两种标杆都装得下，
结论差一整级：

| 标杆来源 | `accuracy.kind` | 结论能说到哪一步 |
| --- | --- | --- |
| 参数逐位对应的公开接口 | `torch` | 「待测实现正确」 |
| CANN 内置的同名 aclnn 接口 | `builtin` | 「与内置实现逐位一致」 |
| 公开接口，执行器只负责拼输入结构、换算值 dtype、分设备 | `plugin` | 「待测实现正确」，**不降级** |
| 自己按文档实现的参考算法 | `plugin` | **「与本轮这份参考实现一致」**，不是「正确」 |

判据是**算值那一步是谁跑的**：执行器里最终落到 `torch.<公开接口>` 的属第三行，
落到自己写的循环或公式的属第四行。第三行常见于稀疏、量化这类入参要先拼成一个
结构才调得动公开接口的算子——ATK 造不出那个结构，但算值的仍是 torch。

**只有第四行要降级。** 自写标杆错了就是假阳性，而精度、隔离复验、独立执行器三道
判据一道都发现不了——它们比的都是同一个标杆。所以落到第四行时：

- `baseline_source` 必须是 `taskdoc` 或 `opdoc`，**语义从文档取，不从被测实现取**
- 报告的精度栏要标注标杆是自实现、未经独立验证
- 有可逆算子或等价格式互转时，把自洽校验一并写进用例（转回去要等于原输入），
  那是不依赖标杆的证据

第三行不写这三条，但要在 `baseline` 之外记一句执行器替标杆做了什么
（拼了什么结构、把值升到哪个 dtype），落点是 `facts.json` 的 `baseline_params`。
