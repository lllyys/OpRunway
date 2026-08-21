# 覆盖与必测集设计

## 目录

- 覆盖来源
- 轴和策略
- 轴取值与门禁判据
- 算子类别
- 规模档
- 种子类参数
- 必测集
- 分面与门禁

## 覆盖来源

覆盖只来自任务书、已确认基线接口和生态精度标准。

不要按待验收算子实现的分支、tiling 或内部断言设计用例。

用语义等价类表达轴，不枚举具体数值。

常用轴：dtype、format、rank、shape_form、attr、广播/归约形态、规模、任务书点名场景和布局。

错误语义用 `expected_error_msg` 表达。

## 策略

默认使用 `anchored_interactions`：两两覆盖打底，主效应保证每个取值出现，交互组覆盖明确的联合语义，`targeted` 标记空输入和边界场景。

各交互组取并集，不把所有轴自动放入一个交互组。

每个交互组填写 `reason`，依据只能是三类覆盖来源。

`max_cases` 是设计预算，不是静默截断开关。

两两覆盖率低于 90% 或预算不足时，先修改设计再生成。

## 轴取值与门禁判据

语义轴的取值不是设计自由度，是覆盖率的分母。分母由设计者现写，门禁再拿它去核对
combos，等于自己出题自己答——真机上同一个算子验收七次，`rank` 写过 `[1..8]`、
`[1,2,3,5]`、`[1,2,3]`，也整根没写过，用例数从 76 条到 500 多条，而每次覆盖率都是 100%。

所以这几根轴的取值由 `_axis_binding.py` 的 `PINNED_AXIS_VALUES` 钉死：

| 轴 | 取值 | 依据 |
| --- | --- | --- |
| `rank` | `1` `2` `3` `4` `5` `6` `7` `8` | 生态标准「维度范围 1~8 维」 |
| `size_class` | `small` `medium` `large` | 本 skill 策略层，字节阈值见规模档一节 |
| `shape_form` | `normal` `empty` `single_element` `unaligned_tail` | 本 skill 策略层 |
| `op_axis_pos` / `reduce_axis_pos` | `first` `middle` `last` | 本 skill 策略层 |

每根钉死的轴只有两种合法写法：取满上表，或整根写 `["n/a"]` 表示对本算子不适用。

全张量归约没有归约轴位置，属于后者。

顺序也算，`itertools.product` 按声明顺序展开。

个别取值确实测不了写进 `infeasible` 并给 `why`，那条会从分母里扣掉且留痕；
直接把轴缩短是把分母悄悄改小，缩完照样 100%。

生成侧的 `dtype` 轴照任务书 §2.4 中「数据类型」为 `tensor` 的参数所列 dtype 写，
`--dtype-source` 给这份任务书；它的 sha256 必须与 `interface.json` 记录的摘要一致。

```bash
<python> scripts/make_must_cover.py -d <op>_decl.json -o <op>_must_cover.json \
  --dtype-source <任务书>.md --interface evidence/interface.json
```

验收侧或旧流程仍照待验收算子工程声明的数据类型表写，可给 README 或头文件。
`--env` 里的 `operator_project.path` 负责核对这份文件确实在工程目录里：

```bash
<python> scripts/make_must_cover.py -d <op>_decl.json -o <op>_must_cover.json \
  --dtype-source <工程>/README.md --env evidence/env.json
```

这张数据类型表和 dtype 轴**双向**核对：声明了却在表里找不到是凭空写，表里有却没声明是漏测。

只查一头挡不住漂移：8 种写成 5 种时覆盖率照样 100%，漏掉的三种根本没进过验收。

文件里出现的类型名不一定都是输入 dtype——median 的 README 里 `bool` 是 `keepDim`
属性的类型。这类误报写进声明的 `dtype_source_excludes`，每条附 `why`：

```json
"dtype_source_excludes": [
  {"dtype": "bool", "why": "keepDim 属性的 C++ 类型名，不是输入张量的 dtype"}
]
```

豁免只用来解释这类误报，不是给声明开的后门：豁免一个文件里根本没出现的 dtype 会被拒。

分面拆分造成的缺口同样走这里。int8 拆出去之后，主分面的 dtype 轴合法地不含 int8，
两边都要写清去向，全套 dtype 才拼得回来：

```json
{"dtype": "int8", "why": "拆到同接口的 int8 分面：mixed_tolerance_bm 对 int8 走量化标准"}
```

其余轴（`keepdim`、`dim_class` 等算子专属的）取值由签名和任务书决定，脚本不约束命名。

各门禁从哪推导，写在这里，不必去读量具源码：

| 门禁 | 判据来源 | 不生效的情形 |
| --- | --- | --- |
| 两两覆盖 ≥ 90% | `dims` 的全部轴对，分母扣掉 `infeasible` 明确禁掉的组合 | 无 |
| 规模配比 40/30/30 ±12pp | combos 的 `size_class` 取值分布 | combos 少于 20 条时不核算 |
| 浮点一种都不能漏 | 给了 `--dtype-source` 时：任务书张量 dtype 列或工程声明表里的每种浮点，要么在 `dtype` 轴上，要么写进 `dtype_source_excludes`；轴上的每个浮点还要至少被一条 combo 命中 | 没有 `dtype` 轴，或轴里一个浮点取值都没有 |
| 浮点占比 ≥ 70% ±12pp | 没给 `--dtype-source` 时的老判据，按 **`dtype` 轴声明的取值**算，不看比较器声明 | 同上，或已按上一行判过 |
| 定向标签 | `coverage_policy.targeted` 的每个标签都要在某条 combo 的 `coverage_tags` 里出现 | 没声明 `targeted` |
| 重复 combo | 全部 `dims` 键的取值组合逐条比对 | 无 |

浮点判据分两套，是因为 `dtype` 轴的性质变了。

比例门禁写在 `dtype` 轴还是设计自由度的年代：能自己挑取值，才谈得上按比例挑。
现在 `dtype` 轴照任务书张量 dtype 列或工程声明表抄，浮点几种、整型几种是**接口事实**。
对事实提比例要求，唯一的满足办法就是把整型拆到另一份分面去——真机上两个算子
的声明表都是 3 种浮点对 5 种整型，混排分面的占比是 37%~43%，结构上到不了 70%，
两轮跑测各因此多拆出一份本不该存在的分面。

所以给了 `--dtype-source` 时换判据：判的还是「做算术的算子精度问题只出在浮点上，
浮点不能漏测」，但换成对既定事实可满足的形式——**一种浮点都不能漏**，而不是占多大比例。

换判据不是放宽。漏一种浮点比占比低危险得多：占比低只是那类测得少，漏一种是
整类没进过验收。所以两条都判死——声明表里的浮点不在轴上要说明去向，在轴上却
一条 combo 都没生成同样报错。

纯整型分面（轴里一个浮点都没有）两套判据都不适用，走 `no_float_dtype` 豁免。
dtype 轴里出现浮点却声明逐元素相等，两套判据下都直接报错。

判据在 `_coverage_strategy.py` 的 `audit_coverage`（`comparator == "equal"` 撞
`dtype_axis_floats` 非空），由 `check_coverage.py` 调用，只对 `operator_class`
是 arithmetic 类（elementwise/reduction）生效，movement 类不判。

## 算子类别

声明一个 `operator_class`，由脚本推导必需轴、默认三轴组、精度判据和 dtype 配比。

取值是**开放的**，下表只是已经积累到、可以直接用的那几类。

命中就直接用，声明文件不得覆盖——那几类的画像是固化事实。

没命中不代表这类算子接不了，补一个 `class_profile` 块把画像写出来即可：

```json
"operator_class": "matmul",
"class_profile": {
  "axes": ["dtype", "size_class", "shape_form"],
  "group": ["dtype", "size_class", "shape_form"],
  "arithmetic": true,
  "comparator": "mixed_tolerance_bm",
  "why": "任务书要求覆盖的三方耦合；依据写在这里，不得从待验收算子实现反推"
}
```

`axes` 是必需轴（缺一根即覆盖盲区），`group` 是默认三轴交互组，`arithmetic` 决定要不要强制浮点占比，`comparator` 定整份用例集的比较器，`why` 记依据。

五项都要写：缺一项后面的判定就落空。

新类别沉淀稳定后，把画像固化进 `OPERATOR_CLASSES`，下一个算子就不用再写。

| 类别 | 必需轴 | 默认三轴组 | `comparator` |
| --- | --- | --- | --- |
| movement | size_class、shape_form、op_axis_pos | size_class × op_axis_pos × shape_form | `equal` |
| elementwise | dtype、size_class、shape_form | dtype × size_class × shape_form | `mixed_tolerance_bm` |
| reduction | dtype、size_class、reduce_axis_pos | reduce_axis_pos × size_class × dtype | `mixed_tolerance_bm` |
| generation | dtype、size_class、shape_form | dtype × size_class × shape_form | `equal` |

**比较器由类别定死，声明对不上直接报错。**

搬运类整份用 `equal`：输出是输入元素的精确拷贝，不做算术，每种 dtype 都该逐元素
相等，int8 自然跟着走 `equal`，**不必为它单独拆一份分面**。

`equal` 分面要关掉 `boundary.has_infnan`：ATK 的 `equal` 走 `torch.equal`，
只在整张都是 NaN 时特判，含部分 NaN 的用例会误判失败。

默认三轴组不写入声明文件。

这张表的真源是 `_coverage_strategy.py` 的 `OPERATOR_CLASSES`，测试逐格核对，别照记忆改。

不适用的必需轴声明为单值轴，并保留该适用性断言。

矩阵类暂不开放；不要用没有依据的组合凑数。

`arithmetic` 为真的类别要过浮点判据：给了 `--dtype-source` 时判「来源声明的浮点
一种都不能漏」，没给时才回落到「浮点占比至少 70%，容差 12 个百分点」。两套判据
的完整说明见本文「轴取值与门禁判据」一节。

随机生成类（`generation`）的输出不由输入算出，由随机数流决定。

它不做算术，所以不设浮点占比目标；能不能逐位比对取决于种子钉不钉得死，
见本文的「种子类参数」一节。

它的输入张量通常只提供 shape，所以「输入整张只有一个取值就测不出错」这条
**对它不成立**：那条判据的前提是输出由输入算出。`freeze_inputs.py` 拿
`--must-cover` 里的 `operator_class` 判这件事，`generation` 时只提示不拦。

带上 `--must-cover <物化后的组合表>` 跑冻结；不带就按拦截走，不会静默放宽。

真机实测（bernoulli，2026-08-17）：不带它，155 条里 34 条被判「测不出错」，
而把 `range` 撑宽只是让门禁闭嘴，一条多的缺陷也测不出来。

## 规模档

规模使用总输入字节数，不使用元素数：`small < 32 KB`，`medium 32 KB–2 MB`，`large > 2 MB`。

目标配比为 40/30/30，允许偏差 12 个百分点。

空张量、单元素和非对齐尾块属于 `shape_form`，不属于规模轴。

优先使用 `2^n` 和 `2^n-1` 的代表值。

单维不超过 `2^20`，总元素不超过 `2^31`，同时满足 golden 资源预算。

低 rank 窄 dtype 无法达到大档时写入 `infeasible`，并说明原因。

ATK 的上边界用例把某一轴硬编码成 `2^31+1`，不受 `max_length` 约束，会直接撑爆单卡。

物化脚本必须把该轴夹回 `2^20`，或用 `extra_numbers: 0` 关掉 ATK 自带的额外边界。

规模档摊到各轴的算法与算子无关，用 `scripts/_shapes.py` 的 `shape_for` 和 `axis_for`。

每个算子重写一份，就是把同一份知识放到两处维护。

**有 `shape_form` 轴时用 `shape_for_form`，不要自己分派四个取值。**

`shape_for` 默认 `ragged=True`，会让某一根轴取 `2^n-1`——那正是 `unaligned_tail`
的定义。normal 照默认参数物化就和它撞成同一个形状，重复 combo 门禁退回，
物化要重跑一遍。`shape_for_form` 保证四个取值两两不同，rank 大规模档小
真的分不开时抛 `ShapeFormDegenerate`，那条写进 `infeasible`。

钉死的轴之间还有几组结构上就分不开的组合：rank1 的中间轴末轴与首轴是同一根，
单元素和空张量形态下没有规模之分。`make_must_cover.py` 在生成时会把它们列出来，
附一段能直接粘进 `infeasible` 的 JSON，照粘即可，不必等物化跑完再被门禁退回来。

## 生成必测集

声明文件写 `operator_class`、`dims`、`coverage_policy`、`axes` 和 `extract`。

同时写 `parameters`、`yaml` 和可选 `infeasible`。

不要手写 combos。

```bash
<python> scripts/make_must_cover.py -d <声明JSON> -o must_cover.json
```

声明文件的完整可跑样例见 `assets/example/decl.json`，它由 `tests/` 守着，不会和脚本漂移。

物化脚本样例见 `assets/example/materialize.py`：规模档摊到各轴、归约轴位置转轴号、按 `targeted` 补标签用例，三件事都在里面。

脚本生成两两覆盖和交互组组合。

算子物化脚本逐条填写实际 shape、attr 和参数，再按 `targeted` 补齐边界用例。

物化后运行：

```bash
<python> scripts/make_yaml.py -m <must_cover.json> -o <operator>.yaml
```

YAML 是声明和物化组合的推导产物，不手写 `dim_values`、rank、dtype 或 tuple 长度。

每个 `parameters` 项说明 `element_kind`、`runtime_container`、`nullable` 和必要的 `dtype`。

`parameters` 的键就是 YAML 的输入名，必须等于基线函数的形参名。

**照 torch 的形参名写，不要照 C 头文件抄。** 两侧名字不同是常态：
C 侧写 `self`，`torch.median` 的形参叫 `input`；C 侧写 `x`，`torch.roll` 叫 `input`。
照 C 抄的第一版几乎必被 `make_yaml.py` 退回。名字对不上时它会把基线形参名列出来，
直接照那份改；同一份名单也在 `signature_alignment.json` 的对齐表里。

带 name 的输入在 dataset reload 之后全部进 kwargs，args 恒为空。

名字对不上基线调用直接 TypeError，`make_yaml.py` 会在推导时拒绝生成。

aclnn 独有的参数同样过不了这道门，它要由基线侧适配器 pop 掉。

`attr`/`scalar` 的 dtype 来自契约，不从 combos 猜测。

## 种子类参数

接口声明里出现随机数种子（`seed`、`offset`、`generator` 之类）时，
**用例数据里必须把它钉成同一个常量**，由 constraint 逐条覆写，不留取值区间。

哪个算子、哪种判据都一样。不钉死会一次性废掉三件事：

- 冒烟与全量拿到的随机数流不同，冒烟通过说明不了全量
- `repro.sh` 复现不出报告里的那条失败
- 与 CANN 内置实现逐位比对（builtin-baseline-design.md）直接不成立

种子不是覆盖轴，不进 `dims`，也不参与组合展开——它是一个被钉住的常数。

`validate_cases.py` 的 C7 从用例数据核对：同一个种子参数在全部用例里只能有一个取值，
且不能是 `[0, 100]` 这样的区间。`offset` 一类词在切片、嵌入等算子里是普通参数，
只有当同一条用例里已经出现 `seed` 时才按种子看。

种子写成张量时，`freeze_inputs.py` 的常量张量检查会把它报出来。那是误报，
在 `evidence/constraints.md` 记一句为什么，不要为了消掉它去改种子。

**接口声明里根本没有种子参数**的随机数生成类算子，随机数流由算子内部的全局状态决定，
钉不住。这种算子不能用逐位或逐元素判据，回 S1 按 `random-operator-signals.json`
的模板问清楚换哪种判据。

ATK 的 `default_seed`（`atk/tasks/backends/backend.py:150`）只管**输入数据生成**，
管不到算子内部的随机数流，不能拿它代替上面这件事。

## 必测集契约

`dims` 写清每根轴有哪些取值；`coverage_policy` 决定从这些取值里挑哪些组合；`axes` 和 `extract` 决定怎么从生成的用例里把轴值读回来。

每个 combo 必须包含全部 `dims` 键。

每个 `infeasible` 项必须有 `why`。

无法从用例读取的设计轴不放进 `axes`，只用于物化或标签。

多输入关系用 `shape_relation` 表达。

非连续是任务级执行开关，不写入 combo。

## 接口分面与门禁

「分面」是本 skill 的说法，ATK 没有这个概念，官方文档里也不讲一个算子该拆几份用例集。

分之前先问一句**这批用例该不该测**。

验收范围由任务书的精度与性能目标划定。同一个接口的另一种语义形态（某个出参
传空时换一套算法之类），来自任务书的功能描述或工程设计文档，属于背景说明，
默认**不构造数据、不进必测集**。用户明确要求测才测，并把这句话记进
`evidence/constraints.md`。

默认只按工程声明的那一份接口签名走：一份签名，一份用例集。

该测的确定了，再问装不装得下。

**判据只有一条：这批用例能不能写进同一份 YAML。**

能写进去就不分。

不是「要不要分」的权衡题，是「写不写得出来」的事实题。试着写成一份，
写不出来才分，并且要说得出是哪个字段装不下。

一份 YAML 里下面这些字段各自只有一个值（`atk/configs/design_config.py`
的 `DesignConfig`，顶层是一个 dict 不是列表）。两组用例在其中任何一个上
要求不同，就装不进同一份：

| 字段 | 一份 YAML 只能有一个 |
| --- | --- |
| `name` / `aclnn_name` / `kernel_name` | 一个待测接口 |
| `inputs` | 一套参数结构 |
| `outputs` | 一个输出位置 |
| `standard.acc` | 一种精度标准 |
| `api_type` / `aclnn_api_type` | 一个执行器 |
| `generate` | 一个生成器 |
| `expected_error_msg` | 一句预期报错 |

分开时把「哪个字段装不下」写进 `evidence/constraints.md`。

说不出是哪个字段，就是不该分。

「覆盖看起来更清楚」「浮点整型分开好看」不是字段。

多分一份要多一整套 YAML、必测集、用例 JSON、跑测和结论，还多一次「两轮之间
拆法不一致」的机会——真机上同一个算子验收七次拆过 1、2、4 份，用例数从 76 条
到 500 多条，而每次覆盖率都是 100%。

**命名直接用接口名**，别自造语义标签：`aclnnMedian` / `aclnnMedianDim`，
不是 `global_main` / `dim_main`——后者看不出测的是哪个接口，两轮验收之间也对不上。

按 dtype 分是最常见的误分。`standard.acc` 虽然只有一个值，但 ATK 按每个输出
张量自己的 dtype 分别选路——浮点走混合容差、整型落到逐元素相等，一份 YAML
里混着 values(浮点) 和 indices(整型) 完全装得下，判定表见
`experimental_standard.md#选哪个比较器`。

对照 median：

| 要分吗 | 哪个字段装不下 |
| --- | --- |
| 工程只声明了 `aclnnMedian` 一个接口名，任务书提到 `aclnnMedianDim` | 先别问字段：工程没有这个接口名，任务书那句是背景说明，默认不测 |
| 同一个 `aclnnMedian`，`indicesOut` 传空时算全局中位数 | 同上，语义形态默认不构造；用户要求测了才分，那时是 `outputs`（1 个 vs 2 个）装不下 |
| 工程真的声明了 `aclnnMedian` 与 `aclnnMedianDim` 两个接口名且都在验收目标里 | `aclnn_name`（两个接口）、`outputs`（1 个 vs 2 个） |
| `median_float` 与 `median_int` | 没有字段装不下，不分。浮点占比门禁不再是拆的理由：给了 `--dtype-source` 时判的是「浮点一种都不能漏」，混排分面照样过 |
| int8 单独一份 | 只有做算术的类别（elementwise / reduction）才谈得上：整份是 `mixed_tolerance_bm`，而该算子的 int8 输出是输入元素的精确选择，`standard.acc` 装不下两个值。搬运类整份就是 `equal`，int8 跟着走，**不拆** |

每份 YAML 独立持有必测集、用例 JSON 和结论。

每个分面只运行一次 `atk case`。

运行 `align_signatures.py`，签名门禁失败时不生成。

签名只能来自待验收算子工程目录，绝不去 CANN 内置算子工程找同名接口。

完整命令与理由见 `plugin-authoring.md` 的「签名只能从工程目录里读」。

对齐报告里的入参不靠眼睛抄进契约，写完 `decl.json` 立刻用 `check_signature_contract.py` 核对：

```bash
<python> scripts/check_signature_contract.py \
  -d <op>_decl.json -a evidence/signature_alignment.json \
  -o evidence/signature_contract.json
```

它比三件事：aclnn 声明里的入参一个都不能少、相对顺序要一致、多出来的入参只有
`aclnn_adapter.required` 为 true 时才合法。

`make_yaml.py` 只查基线侧，而且是子集判定，少写一个入参它不报——那正是 median 那轮
一路过到 S3 才炸的缺口。

`yaml_key` 取不到时这道门禁退出码 2，不放行：位置配对不可信的时候，「没发现差异」不等于「没有差异」。

可表达性判两次，判据是同一份。

`make_must_cover.py` 在读 decl 时先判一次：契约的语义组合、attr 的 dtype、轴取值的形态。

`make_yaml.py` 在填完具体取值之后再判一次，喂的是组合表里每根轴的实际取值。

不需要单独的能力门禁再查一遍，它查的会是脚本自己刚生成的东西。


能力矩阵随 skill 发布，验收期直接用，用 `atk_lookup.py` 查具体取值。

只有装机 ATK 版本与矩阵记录的不一致时才跑 `probe_atk_capabilities.py` 重探。

生成前后运行 `validate_cases.py`，再运行 `check_coverage.py`。

这两个脚本的 `-m` 传**物化后**的组合表（`*_materialized.json`），不是 `must_cover.json`。

`must_cover.json` 的 combo 只有语义轴，没有 shape 和 attr 取值，拿它当分母会命中 0 组。

以下几道是 S2 的内部门禁序列，不是独立阶段：签名、结构、覆盖。

全过才算 S2 完成，任一失败都停在 S2。
