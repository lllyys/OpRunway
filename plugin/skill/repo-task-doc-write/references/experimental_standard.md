<!--
真源 https://raw.gitcode.com/cann/opbase/raw/master/docs/zh/ops_precision_standard/experimental_standard.md
2026-08-15 实抓核对：§2.2 阈值表与 §1.2 上限逐项一致。
上游 §2.2 是 HTML table，此处转写为 Markdown；required_matched_ratio 原为 colspan=6。
仓内 skill/atk-quality-guard 的同名文件是旧抓取，fp16/bf16/hf32 阈值与上游不符，不要采信。
禁止就地修正数值：上游有误应推动上游改后重抓。
-->

# 生态算子开源精度标准

## 目录

- 适用范围
- 选哪个比较器
- 用例生成
- 误差指标
- 阈值表
- 与 ATK 默认值的差异
- 判定

## 适用范围

该标准的阈值表用于浮点计算，整型不套用本表。

判定由 ATK 的比较器执行，不要自己重算 `matched_ratio`。

本文件不重复实现判定，它管三件 ATK 管不了的事：选哪个比较器、阈值与 ATK 内置常量差在哪、哪些覆盖写法被禁止。

ATK 内置常量在 hf32、fp8e4m3、fp8e5m2 上与本表不符，所以「用 ATK 的就够了」不成立，见下面的差异节。

## 选哪个比较器

YAML 的 `standard.acc` 每份用例集只有一个值，但**它不是逐用例的判定方式**：ATK
按每个输出张量自己的 dtype 分别选路。下表是实测结果（`atk_lookup.py
comparator_semantics`，真源是 `probe_atk_capabilities.py` 的差一探针，不是源码推断）：

| 输出 dtype | 声明 `mixed_tolerance_bm` 实际怎么判 | 拦得住差一 |
| --- | --- | --- |
| fp16 / bf16 / fp32 / hf32 / fp8e4m3 / fp8e5m2 / complex64 | 混合容差（本文件的阈值表） | — |
| uint8 / int16 / int32 / int64 / bool | ATK 内部转调 `equal`，与直接声明 `equal` 等价 | 是 |
| **int8** | **走量化标准：`\|diff\| <= 1` 全通过即算过** | **否** |

真值来自 CANN 内置实现时（`interface.json` 的 `baseline_kind` 是 `cann_builtin`）
这张表不适用：整份用例集声明 `equal`，浮点也一样。

两侧都在 NPU 上跑同一个 aclnn 接口，比的是「改动有没有改变输出」，不是
「算得对不对」，任何一位不同都是要抓的东西。

见 builtin-baseline-design.md#比较器。

由此得到三条：

**默认声明 `mixed_tolerance_bm`。** 浮点走混合容差，整型自动落到逐元素相等，
不需要为「整型要 equal」做任何额外声明。ATK 官方文档只给了
`default` / `mixed_tolerance_bm` / `single_bm` 三个取值，其中前两个等价。

**int8 要先判它是不是量化输出。** ATK 把 int8 输出**有意**当量化结果处理
（`single_benchmark_compare.py:75` 的注释原话是「量化计算：int8输出，绝对误差
小于等于1」），这不是缺陷。这个假设对量化类算子成立；对输出取自输入的算子
（median、topk、argmax、搬运类）不成立——那里差 1 就是错值，而量化标准放行。

判据是**算子的 int8 输出是不是量化值**，不是「int8 一律怎样」：

| int8 输出的语义 | 怎么办 |
| --- | --- |
| 量化计算的结果 | 用默认，ATK 的 ±1 就是为它设计的 |
| 输入元素的精确拷贝或选择 | 要逐元素相等，`mixed_tolerance_bm` 会放过差 1 |

**先看 `operator_class`，再谈 int8。** 比较器是整份用例集一个值，由类别定死
（`case-design.md#算子类别`），不是逐个 dtype 现判的。

搬运类（`movement`）整份就该是 `equal`：输出是输入元素的精确拷贝，每种 dtype
都要逐元素相等，int8 跟着走 `equal`，**一份分面都不用拆**。声明成
`mixed_tolerance_bm` 再把 int8 拆出去，是多了一整套 YAML、必测集、用例 JSON、
冻结目录和跑测命令，只为一个 dtype——真机上发生过一次，覆盖率一条也没多。

`equal` 分面要关掉 `boundary.has_infnan`：ATK 的 `equal` 走 `torch.equal`，
只在整张都是 NaN 时特判，含部分 NaN 的用例会误判失败。

只有**做算术的类别**（`elementwise` / `reduction`）才谈得上拆 int8：那时整份是
`mixed_tolerance_bm`，而这个算子的 int8 输出又是输入元素的精确选择（median、
topk、argmax），`standard.acc` 装不下两个值，才把 int8 拆出去单独用 `equal`。

`equal` 在 ATK 官方文档里没列，但在比较器注册表里（`atk_lookup.py comparator` 可查）。
这条判断要写进 `evidence/constraints.md`，不要当成默认动作。

**多输出不因 dtype 不同而拆。** `aclnnMedianDim` 一份用例集里 values 是浮点、
indices 是整型，声明 `mixed_tolerance_bm` 后 ATK 给 values 走混合容差、
给 indices 走逐元素相等，各判各的。拆不拆只看接口，判据见
`case-design.md#接口分面`。

声明 `equal` 而 dtype 轴里有浮点会被覆盖门禁拒绝：浮点的位级相等在 NPU 上不成立。

## 用例生成

本节只记标准对**数值分布**的要求，覆盖设计与规模档见 case-design.md，不在这里重复。

维度范围 1~8 维，单轴取值落在 `[1, 2^20]`，总元素数不超过 `2^31`。

数值分布要求均匀与正态各占一半：

| 分布 | 比例 | 值域 |
| --- | --- | --- |
| 均匀 | 50% | [-5, 5] |
| 正态 | 50% | μ ∈ [-5, 5]，σ ∈ [0.1, 2] |

这张表随本标准适用于**浮点**：连续取值下单张张量仍有近 numel 个不同值。

整型套用同一组参数会退化成常量张量——ATK 先采样再按 dtype 截断，σ ≤ 2 时整张只剩个位数取值，无符号 dtype 撞上负 μ 直接整张清零。

整型的取值范围由 `make_yaml.py` 按 dtype 容量推导，冻结时会核一次是否退化成常量张量。

「各占一半」要靠两个 `random_types` 才成立：ATK 在只写一项时恒取正态，均匀区间一次也不生效。

特殊场景不与常规用例正交组合：空 Tensor、标量 Tensor、上下边界、nan/inf/-inf。

输入生成使用有限值、非有限值和边界值的明确标签。

非有限输入是统计事实，不改变通过率分母。

## 误差指标

逐元素通过条件是 `|actual - golden| <= atol + rtol * |golden|`。

`matched_ratio` 为通过元素数除以总元素数。

用例通过要同时满足 `matched_ratio >= required_matched_ratio` 与 `max_abs_error <= max_abs_error_limit`。

## 阈值表

| 数据类型 | rtol | atol | required_matched_ratio | max_abs_error_limit |
| --- | --- | --- | --- | --- |
| FLOAT16 | 2⁻⁹ (1.95e-3) | 2⁻⁹ (1.95e-3) | 0.99 | 1e-1 或 32×ULP |
| BFLOAT16 | 2⁻⁶ (1.56e-2) | 2⁻⁶ (1.56e-2) | 0.99 | 1e-0 或 32×ULP |
| FLOAT32 | 2⁻¹⁰ (9.77e-4) | 2⁻¹⁶ (1.53e-5) | 0.99 | 1e-2 或 32×ULP |
| HiFLOAT32 | 2⁻⁹ (1.95e-3) | 2⁻¹⁰ (9.77e-4) | 0.99 | 1e-1 或 32×ULP |
| FLOAT8 E4M3 | 2⁻² (0.25) | 2⁻⁴ (0.0625) | 0.99 | 1e-0 或 32×ULP |
| FLOAT8 E5M2 | 2⁻¹ (0.5) | 2⁻³ (0.125) | 0.99 | 1e-1 或 32×ULP |

`32×ULP` 对应 ATK 的 `max_ulp_multiple = 32`，ATK 按逐元素逻辑或取较松者。

上游没有定义这个「或」的确切语义，报告里照实写口径，不要自行择一。

## 与 ATK 默认值的差异

ATK 内置默认值在三种 dtype 上与本表不符：

| dtype | 本表 rtol / atol / max_abs | ATK 默认 rtol / atol / max_abs |
| --- | --- | --- |
| hf32 | 1.95e-3 / 9.77e-4 / 1e-1 | 9.77e-4 / 1.53e-5 / 1e-2 |
| fp8e4m3 | 0.25 / 0.0625 / 1e-0 | 0.125 / 0.125 / 5e-1 |
| fp8e5m2 | 0.5 / 0.125 / 1e-1 | 0.25 / 0.25 / 1e-0 |

fp16、bf16 和 fp32 两边一致。

不要在用例里覆写阈值消除差异，验收政策禁止 `<dtype>_` 覆盖键，裁决会直接拒绝。

用到这三种 dtype 时，在报告的精度节写明实际生效的是 ATK 默认值以及与本表的差异。

## 判定

### 特殊值处理

**NaN：** `equal` 比较器在两侧输出**全为 NaN** 时无条件判通过（`atk/tasks/post_process/equal_compare.py:27`），不再比对。

搬运类算子的 `infnan` 定向用例若生成了全 NaN 输入，输出两侧也全是 NaN，该用例恒过且不携带任何信息。

**inf：** ATK 的 `check_invalid_value()` 把 inf 和 NaN 都视为"无效值"（`atk/tasks/post_process/utils.py:52-56`）。在容差比较（`single_benchmark_compare.py:142-149`）中：

| 场景 | ATK 判定 | 依据 |
| --- | --- | --- |
| 基线（golden）包含 inf | 直接判**通过** | `check_invalid_value(remote_output)` 为真时短路返回 pass |
| 待测输出包含 inf，但基线没有 | 判**失败** | 报错"NPU result contains nan/inf value" |
| **两侧都包含 inf** | **判通过** | 先检查基线，检测到 inf 后直接通过，不再比对是否相同 |

这个逻辑**不区分 `+inf` 和 `-inf`**，也不检查两侧 inf 的位置和符号是否一致。

**溢出场景：** 当计算结果超出 dtype 表达范围时，IEEE 754 标准规定产生 inf。如果待验收算子和基线都正确地溢出到 inf，ATK 会判定为通过。但如果一侧是 `+inf` 另一侧是 `-inf`，ATK 的当前实现仍会判通过（因为只检查基线是否包含 inf，不比对具体值）。

**验收策略：** 对于可能溢出的算子，在 `evidence/constraints.md` 中明确记录：
- 哪些输入组合会导致溢出
- 溢出是预期行为还是需要规避的边界
- 如果两侧都溢出视为通过，需说明这符合任务书要求

### 通过率判定

精度结果必须覆盖全部有效用例。

通过率为通过用例数除以有效用例数。

无效参数和资源预算项不进入分母，但必须单列。

未知失败不得改写为无效。
