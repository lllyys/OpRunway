# 检查点清单

<!-- 本文件由 scripts/render_views.py 从 references/artifact-contracts.json 渲染，不要手改。 -->

会拦下你的检查点全集，按阶段和量具分组。

动手写产物之前先读这一份，不要等着被逐个拦下来才知道有这道门。

每道门登记四件事：检查什么、为什么存在、前提是什么、前提不成立时谁接手。

## 目录

- 为什么有这么多
- 前提不成立时怎么办
- 换一类算子时先看这几道
- 各阶段检查点

## 为什么有这么多

检查点分四类，前三类每一条都由一次真实事故换来：

| 类别 | 拦什么 | 删掉会怎样 |
| --- | --- | --- |
| `transcription` | 两份数据本来对得上，中间用眼睛搬了一次 | 契约漏一个入参，一路过到 S3 绑定才炸 |
| `self_referential` | 判据与被检对象同源，断言恒过 | 覆盖率次次 100%，而用例数每轮不一样 |
| `atk_pitfall` | ATK 的反直觉行为，不是你的疏忽 | 插件静静不生效，报告照出、数字全错 |
| `structural` | 字段缺没缺、值在不在域里 | 后面每一步都建在读不出来的产物上 |

`atk_pitfall` 那些和判断力无关，纯粹是知识：读了就能一次写对。

它们集中在 `references/atk-pitfalls.md`，写产物之前先过一遍。

## 前提不成立时怎么办

判据大多诞生于某一类算子，换一类来就可能不适用。

**跳过不是默认选项。** 判别标准只有一句：

> 前提不成立时，这道门要拦的缺陷还可能不可能发生？

| 处置 | 什么时候 | 要求 |
| --- | --- | --- |
| `retarget` | 还可能。判据没错，量错了对象 | 说清换成量什么 |
| `delegate` | 还可能。这一侧判不了 | 点名交给哪个量具，答不上来就不许跳过 |
| `not_applicable` | 结构上不可能发生 | 必须落进证据，跳过的事实要留痕 |


## 换一类算子时先看这几道

它们的判据有前提，前提挂在哪个键上决定了换算子时要重新确认什么。

| 检查点 | 挂在 | 不成立时 |
| --- | --- | --- |
| `check_bundle.interface_conformance` | `interface_mode` | `not_applicable` |
| `make_must_cover.dtype_source` | `operator_class` | `delegate` |
| `expressibility.value_ranges` | `parameter_kind` | `retarget` |
| `make_yaml.baseline_binding` | `baseline_kind` | `delegate` |
| `check_signature_contract.three_judgements` | `baseline_kind` | `delegate` |
| `check_coverage.size_ratio` | `operator_class` | `not_applicable` |
| `check_coverage.float_dtype` | `operator_class` | `not_applicable` |
| `validate_cases.runtime_structure` | `atk_version` | `retarget` |
| `validate_cases.seed_pinned` | `operator_class` | `not_applicable` |
| `freeze_inputs.constant_tensor` | `operator_class` | `not_applicable` |

## S0 接收与环境

### `check_bundle.integrity`

量具 `check_bundle.py`　类别 `structural`

- bundle.json 存在且 schema_version 是量具认识的版本
- 逐文件重算 sha256，结果与 files 登记一致
- files 登记文件没有缺失，excluded 与 ignored_dirs 没有被扩张
- 新增文件只在 frozen_*/、result/ 与根目录冻结产物类型下判红

**为什么存在：** 交接包是生成侧与验收侧的唯一接口。
冻结纪律必须靠验收侧重算摘要落实，不能相信清单自述或 agent 声称文件没动

**前提：** 无，任何算子都适用。

### `check_bundle.task_doc`

量具 `check_bundle.py`　类别 `transcription`

- 交接包的 task_doc.sha256 等于本次给定任务书的 sha256

**为什么存在：** 一份任务书生成的交接包可以验收多个 PR，但不能挪给另一份任务书。
文件名相同不代表内容相同，必须按全文 sha256 绑定

**前提：** 无，任何算子都适用。

### `check_bundle.atk_version`

量具 `check_bundle.py`　类别 `structural`

- 验收机 env.json 的 ATK 版本等于 bundle.json 的 atk.version

**为什么存在：** 用例的可表达性判定绑定生成侧的 ATK 版本。
验收侧版本不同，能力矩阵与生成结果的成立前提就不再可靠

**前提：** 无，任何算子都适用。

### `check_bundle.interface_conformance`

量具 `check_bundle.py`　类别 `transcription`

- PR 头文件的全部业务参数（含出参）名称集合等于任务书签名的参数名集合
- 两边全部业务参数（含出参）的相对顺序一致
- 两边全部业务参数（含出参）的 C 类型、指针层数与 const 一致

**为什么存在：** 签名本来从 PR 抄，抄什么过什么，PR 偏离任务书现在测不出来。
任务书才是接口契约，PR 的公开声明必须在 S0 单独与它核对

**前提：** 接口模式是 aclnn，工程里有 GetWorkspaceSize 头文件可读（挂在 `interface_mode`）

**不成立时：** `not_applicable`
　证据键 `interface`

**注：** pytorch / kernel 模式及未给 --header、--aclnn-name 的 aclnn 模式没有 C 头文件可比。
结论写进 bundle_intake.json 的 interface 项

## S2 用例生成

### `make_must_cover.operator_class`

量具 `make_must_cover.py`　类别 `structural`

- operator_class 在 OPERATOR_CLASSES 里，否则必须给 class_profile
- class_profile 四项 axes/group/arithmetic/why 齐全且轴名合法
- 比较器与类别一致

**前提：** 无，任何算子都适用。

### `make_must_cover.pinned_axes`

量具 `make_must_cover.py`　类别 `self_referential`

- 钉死的轴取满 _axis_binding.PINNED_AXIS_VALUES，或整根写 n/a
- 取值顺序也算，itertools.product 按声明顺序展开
- 个别取值测不了走 infeasible 并给 why，不许直接缩短轴

**为什么存在：** 分母由设计者现写、门禁再拿它核对 combos，等于自己出题自己答。
median 验收七次 rank 写过三种取值，用例数 76~500，而每次覆盖率都是 100%

**前提：** 无，任何算子都适用。

### `make_must_cover.dtype_source`

量具 `make_must_cover.py`　类别 `self_referential`

- dtype 轴声明的每种类型都要在任务书 §2.4 张量 dtype 列或工程 --dtype-source 里按词边界找得到
- 反向再扫对应来源：任务书张量 dtype 列或工程声明里有而没声明的报出来，漏写同样拦
- 误报走 dtype_source_excludes，每条附 why；豁免文件里没出现的 dtype 会被拒

**前提：** 生成侧任务书 §2.4 写明张量 dtype，或验收侧工程有文字形态的数据类型表（README 或头文件）（挂在 `operator_class`）

**不成立时：** `delegate`
　交给 `check_signature_contract.py`

**注：** 生成侧任务书缺具体 dtype 时先用 repo-task-doc-write 补齐；旧流程取不到 --dtype-source 时回落到「浮点占比 ≥70%」的老判据，
dtype 与签名的一致性由签名契约三判承担

### `expressibility.contracts`

量具 `make_must_cover.py`　类别 `structural`

- element_kind 只能是 tensor/scalar/attr
- runtime_container 只能是 single/list/tuple
- 两者的组合要在能力域里

**前提：** 无，任何算子都适用。

### `expressibility.value_ranges`

量具 `make_must_cover.py`　类别 `atk_pitfall`

- 显式声明的 range 要装得进该参数自己的 dtype
- 越界部分会被 ATK 按 dtype 截断，无符号 dtype 撞负值整张清零

**前提：** 拿去量的 dtype 是该参数自己的 dtype（挂在 `parameter_kind`）

**不成立时：** `retarget`
　换成量 `attr/scalar 用契约自己的 dtype，只有 tensor 才用 dims 的 dtype 轴`

**注：** 
真机死锁 F2：种子是 int64_t 的 attr，判据拿张量 dtype 轴（含 uint8）去量，而 case-design.md#种子类参数 要求钉成常量 range [S, S]，
唯一合法写法被判越界，报错给的两条出路都违反规范

### `expressibility.axis_values`

量具 `make_must_cover.py`　类别 `atk_pitfall`

- attr 轴的取值 ATK 表达得出来：不许空复合组
- 同一个输入不能既是平面 attr 又是复合 attr
- 序列语义必须声明 runtime_container=list 或 tuple

**前提：** 无，任何算子都适用。

**注：** 只看 extract 规则写 from: attr 的轴，别的轴由数据生成那一侧管，边界已划清

### `make_yaml.baseline_binding`

量具 `make_yaml.py`　类别 `transcription`

- YAML 输入名必须等于基线函数的形参名
- 照 torch 形参名写，不照 C 头文件抄：C 侧 self，torch 侧 input

**前提：** 基线是能反射出形参名的 torch 函数（挂在 `baseline_kind`）

**不成立时：** `delegate`
　交给 `check_signature_contract.py`

**注：** 真机死锁 F3：baseline_kind 为 cann_builtin 时没有 torch 基线，真值来自先跑一轮内置存盘。
子集判定拒掉 prob/seed/offset，而那是唯一正确的写法。
移交后由三判承担，且三判强于子集判定

### `make_yaml.header_keys`

量具 `make_yaml.py`　类别 `structural`

- YAML 头部不许出现未知键
- dtype_numbers/extra_numbers/shape_distributions 由脚本固定写，不许手写

**前提：** 无，任何算子都适用。

### `check_signature_contract.three_judgements`

量具 `check_signature_contract.py`　类别 `transcription`

- 缺参数：aclnn 声明里有、契约里没有，调用少喂必挂
- 乱顺序：pyaclnn 按声明顺序绑定，顺序错了是静默错绑
- 多参数：只有 aclnn_adapter.required 为 true 时才合法

**为什么存在：** median 那轮契约漏一个 dim，基线侧子集判定合法放行，一路过完 S2 三道门禁、冻结、构建，S3 按声明顺序绑 pyaclnn 才炸

**前提：** 签名对齐给得出每个入参的 yaml_key（挂在 `baseline_kind`）

**不成立时：** `delegate`
　交给 `align_signatures.py`

**注：** yaml_key 取不到时退出码 2 不放行：位置配对不可信时「没发现差异」不等于「没有差异」，回去补 semantic_review

### `check_adapter_binding.wiring`

量具 `check_adapter_binding.py`　类别 `atk_pitfall`

- 适配器判定为必需，但用例集里接线字段还是默认值
- determinable=false 时不放行：没找到理由不等于不需要适配器
- 可空指针预警：有用例把它置空却没写 typed null 执行器
- 重载数与变长签名脚本判不了，只记 requires_manual_review 不拦

**为什么存在：** 接线字段在用例 JSON 里，S2 冻结之后改它要重跑 atk case。
roll 那轮判断「S3 冒烟再说」，正撞上冻结纪律

**前提：** 无，任何算子都适用。

**注：** 判据全部从用例 JSON 推导，不问 agent；判不了的那两类显式标记而不硬拦，这是处置得当的样板

### `check_coverage.denominator`

量具 `check_coverage.py`　类别 `self_referential`

- 两两覆盖 ≥ 90%，分母扣掉 infeasible 明确禁掉的组合
- combo 的轴值都在 dims 里，且不命中 infeasible
- 重复 combo 逐条比对
- 定向标签每个都要在某条 combo 的 coverage_tags 里出现

**前提：** 无，任何算子都适用。

**注：** audit_coverage 明确拒绝拿策略比对自己的产物：模型把策略和 combos 一起编错时那种断言照样通过

### `check_coverage.size_ratio`

量具 `check_coverage.py`　类别 `self_referential`

- 规模配比 40/30/30，允许偏差 12 个百分点

**前提：** combos 至少有 20 条，比例才有统计意义（挂在 `operator_class`）

**不成立时：** `not_applicable`
　证据键 `size_ratio`

**注：** 少于 20 条时不核算，报告里 size_ratio 为 null

### `check_coverage.float_dtype`

量具 `check_coverage.py`　类别 `self_referential`

- 给了 --dtype-source 时：工程声明表里的每种浮点都不能漏
- 没给时回落到浮点占比 ≥ 70%，容差 12 个百分点
- dtype 轴有浮点却声明 equal 比较器，两套判据下都直接报错

**前提：** 算子做算术，精度问题出在浮点上（挂在 `operator_class`）

**不成立时：** `not_applicable`
　证据键 `float_ratio`

**注：** 只对 arithmetic 类（elementwise/reduction）生效，movement 类不判；纯整型分面走 no_float_dtype 豁免

### `validate_cases.registration`

量具 `validate_cases.py`　类别 `atk_pitfall`

- C1 注册名落在作者写的那个类上，子类数等于装饰器数
- C2 枚举表真的被加载进那个类了
- C2b 覆写真的会被调用，不是仍解析到 CaseGenerator

**为什么存在：** 注册名对不上时 ATK 不报「插件没加载」，而是静静走内置默认执行器

**前提：** 无，任何算子都适用。

**注：** C2 在生成器不用类属性存表时 skip 而不是拦，处置得当

### `validate_cases.case_set`

量具 `validate_cases.py`　类别 `atk_pitfall`

- C3 覆盖率
- C4 缺失组合的最近邻诊断，是诊断不是判据
- C5 用例集只含枚举组合，ATK 没有自行扩展

**前提：** 无，任何算子都适用。

**注：** C5 靠 dtype_numbers:1 与 extra_numbers:0 让 ATK 的默认展开退化成恒等

### `validate_cases.runtime_structure`

量具 `validate_cases.py`　类别 `atk_pitfall`

- C6 按版本化能力矩阵检查生成后的运行期参数结构

**前提：** 装机 ATK 版本与能力矩阵记录的一致（挂在 `atk_version`）

**不成立时：** `retarget`
　换成量 `版本不一致时跑 probe_atk_capabilities.py 重探，拿实测结果替换矩阵`

**注：** 矩阵随 skill 发布，验收期直接用；只有版本对不上才重探

### `validate_cases.seed_pinned`

量具 `validate_cases.py`　类别 `atk_pitfall`

- C7 同一个种子参数在全部用例里只能有一个取值
- 不能写成 [0, 100] 这样的取值区间

**为什么存在：** 种子不钉死会一次性废掉三件事：冒烟说明不了全量、repro.sh 复现不出失败、与内置实现逐位比对不成立

**前提：** 接口声明里有种子参数（挂在 `operator_class`）

**不成立时：** `not_applicable`
　证据键 `seed_parameters`

**注：** 没有种子参数就不触发。
有随机数流却没有种子参数的算子钉不住，回 S1 按 random-operator-signals.json 换判据

### `freeze_inputs.constant_tensor`

量具 `freeze_inputs.py`　类别 `self_referential`

- 整张只有一个取值的输入测不出错

**前提：** 输出由输入算出（挂在 `operator_class`）

**不成立时：** `not_applicable`
　证据键 `constant_input_check`

**注：** 真机死锁 F5：bernoulli 的输出由随机数流决定，self 只提供 shape，155 条里 34 条被判测不出错。
把 range 撑宽只是让门禁闭嘴。
带 --must-cover 读 operator_class，generation 类只提示不拦；取不到类别按拦截走，不静默放宽

### `freeze_inputs.baseline_plugin`

量具 `freeze_inputs.py`　类别 `atk_pitfall`

- 基线插件真的跑通，CPU 节点产出了 golden
- 冻结目录没有被别的分面占用

**前提：** 无，任何算子都适用。

**注：** 真机摩擦 F4 不是前提问题：atk node 入口不自动加载 function_*.py（只有 atk aclnn / atk pytorch 别名入口会），
155 条基线全挂而诊断指向 YAML 输入名。
要修的是 reference 的事实错误与诊断指向

### `seal_bundle.completeness`

量具 `seal_bundle.py`　类别 `structural`

- S1、S2 登记的产物全部在盘上，条件产物按 interface.json 与 signature_alignment.json 判定，核对时排除清单自身
- 每个 YAML 分面按用例集摘要恰好配到一份覆盖、冻结、校验与适配器报告，签名契约报告全局通过
- 清单覆盖五个排除文件与 __pycache__ 目录之外的全部普通文件

**为什么存在：** 交接包把用例生成与 PR 验收解耦，生成侧必须一次交齐可直接消费的 S1、S2 产物。
缺件后再补会破坏封印，验收侧也不得回头重新生成

**前提：** 无，任何算子都适用。
