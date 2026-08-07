# aclnnBernoulli / aclnnRemainderTensorTensor 接入 gap 清单

**结论：当前验收不了这两个算子，卡在精度维与性能维的能力缺口上。**

- 基线：`96edacd`；指路：[gap revalidation findings](../.cc-suite/audits/audit-fix-20260806-gap-revalidation-findings.md)、[Bernoulli / Remainder plan](oprunway-bernoulli-remainder-plan.md)

## 1 · 待确认：3 项

**待确认 · 「不低于原算子性能」的「原算子」指哪个 baseline？**

推断：两个 PR 都是对现有算子做内存优化 → 原算子 = 优化前的当前实现 = `aclnn_builtin`（三候选中也只有它既在受控词表内、又已有 libopapi 指纹 + 符号定义证明机制）。⚠ **这是推断，未经确认**；确认后即可并入组 2。

- **G18**：Remainder 任务书要求 Atlas A2/A3 训练系列，但 PR 只为 `ascend950` 注册/构建；须由人确认如何处理该任务书与被测事实的冲突。
- **G20**：Remainder 同一任务书同时写 `math/floor_mod` 与 `experimental/math`；取材前须由人确认本轮权威目标目录。

其余原拍板项均已消化，保留去向如下以便追溯。

| 原拍板 | 消化去向 |
|---|---|
| **1 · BF16 / DOUBLE** | 任务书是验收权威，列了 BF16 与 DOUBLE 就全测；结果交确定性产物分类，仅确认属于 DUT 能力缺失时形成未通过裁决，harness 限制或证据不完整保持 `blocked` / `needs_review`。§5.1 的“三源冲突停下问用户”针对无法确定测什么，此处目标完全确定，不适用 |
| **2 · Bernoulli 比较关系** | 裁定 E 已分清来源：任务书指定同机 NPU `torch_npu` `Tensor.bernoulli_` + 相同 seed；人工裁定据用户事实主张取 `exact`。首次取证须先验证 RNG 消耗方式一致这一前提 |
| **4 · baseline 选型** | 移回待确认；当前 `aclnn_builtin` 仅为有依据的推断，未经人工确认 |
| **6 · promote 规则** | 任务书 `aclnnRemainderTensorTensor_task_doc.md:27` 已要求与 PyTorch `torch.remainder` / `Tensor.remainder` 语义一致，类型提升属于 torch 语义；缺的是混合 dtype case 生成能力，不是判据 |

### 本轮已裁定

| 裁定 | 口径 | 连带影响 |
|---|---|---|
| A · 核心验收维度 + GPU / 资源处置 | 核心验收维度永远是精度 + 性能（适用今后所有验收）。性能：不取 GPU 标杆、不算比值（§5.10）；精度真值：任务书指定 GPU 口径时按 §5.11 解析为同族 CPU，不是删除；内存/资源类不是验收轴（§5.12） | 报告如实写「本轮未做内存评估」；内存不是阻断项、不进 `task_pr_gaps` 当 gap、不影响裁决，也不得写成已达标；G9 随之出局 |
| B · taskdoc golden 常态化 | 采纳 taskdoc 侧口径：`self_test_case/<op>/golden.py` 由任务书提供并构成真值口径授权；今后任务书都会提供，workflow 按此方向泛化兼容。通用优先级：任务书显式指定 oracle 方法时，优先于附带的 golden；未显式指定时才消费自带 golden；两者缺失或冲突则 fail-closed | G2 授权已解、只余比较关系；<br>taskdoc 成为权威后，G14/G15 升为重点；<br>本轮 Bernoulli 是该通用规则的实例 |
| C · 任务书不提的不管 | Remainder 非连续、`prob` 越界、Remainder 除零均不纳入验收；Bernoulli 非连续因任务书明写 √ 仍保留 | G7 只留 Bernoulli；<br>G13 删除未规定行为 |
| D · rank 全局默认 1–8 | 这是任务书未明确时今后所有验收的默认口径，不只针对本轮两个算子；任务书明写 0–8 时以任务书为准 | G8 升为已裁定能力扩面；<br>落地必须同时轴集版本化、旧算子锁旧版本；Bernoulli 的 rank 0 另见 G21 |
| E1 · 任务书（提示词）指定 | 标杆 = 同机 NPU 的 `torch_npu` `Tensor.bernoulli_`；DUT 与标杆使用相同随机种子；授权强度为 tier 1 / `oracle_method` | 任务书显式指定的 oracle 优先于自带 `golden.py` |
| E2 · 人工裁定（用户 2026-08-07） | 依据「`Tensor.bernoulli_` 在 NPU 下，用相同随机种子的结果一致」这一事实主张，比较关系取 `exact` | 前提是 DUT 与标杆消耗 RNG 的方式一致。本 PR 的 `fill + DropoutDoMask` inplace 融合若改变掩码生成顺序或状态推进，两者可能合法地不一致；首次取证须先验证此前提，逐位不一致时不得直接判 DUT 未通过，须先归因是 DUT 缺陷还是前提不成立 |

## 2 · Gap 表

「影响哪条 case 来源」只有三种取值：`两条都影响`、`仅 generated`（taskdoc 通路已绕开）、`仅 taskdoc`。不确定性直接写在证据说明里。

**19 个在册 gap 项中，当前 3 个需要人工确认；15 个归入组 2、可由 agent 按既定方向推进，G16 本轮不做。**

### 组 1 · 需人工确认

| gap | 影响哪条 case 来源 | 待确认事项 |
|---|---|---|
| **G11a baseline 选型** | 两条都影响 | 确认「原算子」是否指优化前的当前实现 `aclnn_builtin`；当前仅为推断，依据见 §1，未经确认不得并入组 2 |
| **G18 Remainder 目标硬件冲突（Critical）** | 两条都影响 | 两份任务书 `:6` 均要求 **Atlas A2/A3 训练系列产品**；但仅 Remainder PR 在 `math/floor_mod/CMakeLists.txt:12` 与 `math/floor_mod/op_host/floor_mod_def.cpp:45` 只声明 `ascend950`。这是任务书与被测事实冲突，不是工具能力缺口；不得用 PR SoC 改写任务书。须先确认并在目标硬件真实构建、执行核验，之前 fail-closed、不得产通过裁决。Bernoulli 在 `experimental/random/bernoulli/CMakeLists.txt:11`、`op_host/bernoulli_def.cpp:67-68` 声明 `ascend910b` + `ascend910_93`，与 A2/A3 一致，不存在此问题 |
| **G20 Remainder 任务书目标目录矛盾（High）** | 两条都影响 | 同一任务书 `aclnnRemainderTensorTensor_task_doc.md:7` 的开源仓地址指向 `math/floor_mod`，`:68` 的 PR 申请合入目录却指向 `experimental/math`，实际被测代码位于 `math/floor_mod/`；两处冲突直接影响「任务书 ↔ PR 对应」核定和取材 `--target-dir`。取材前须确认本轮权威目标目录，未确认不得把当前 checkout 当作任务交付来源；这与已撤销的 G10 本地 provenance 不是一回事 |

### 组 2 · agent 可自主推进（方向明确，改完能自验证）

方向已由任务书或已有裁定给死，agent 可以实现并自验证，不需要人工再定标准。涉及通用能力改动的，仍须按 §5.2 先出方案再动手；一切真机 compute 仍须在 NPU 容器执行（§5.3）。下表按建议动手顺序排列。**G17 + G11b 是 Bernoulli 精度 oracle 的完整性前提，须落成机器可校验的依赖/收据状态，由确定性门在运行 oracle 前 fail-closed；缺失时禁止生成可用于裁决的精度证据。**

| gap | 影响哪条 case 来源 | 方向从哪来；做完怎么自验证（含现有证据 file:line） |
|---|---|---|
| **G17 baseline / oracle 被 DUT 污染** | 两条都影响 | 明确 bug；取证前由确定性脚本 fail-closed：DUT 侧 defining ELF 命中来源锚，标杆侧命中预期身份/指纹，两段式两个符号的实际定义者均获证明，且两侧 ELF 不同；故意注入即非 0 退出（展开见下）。它与 G11b 已从性能维卫生升级为精度 oracle 的完整性前提 |
| **G11b 符号隔离（半成品）** | 两条都影响 | 路径/环境/来源锚已由 PR #15 绑定；补正向身份校验：DUT 侧命中来源锚，标杆侧命中预期身份/指纹，并证明 `aclnnXxx` 与 `aclnnXxxGetWorkspaceSize` 两个符号的实际定义者（现在只有 `hasattr`，且漏后者）；机器可校验收据由确定性门在取证前强制对账，缺失/同源/指纹不符三种负向都阻断。须与 G17 一起先于 Bernoulli 精度取证完成 |
| **G2 Bernoulli 已定判据、待实现** | 两条都影响 | **机器前置：G17 + G11b 的依赖/收据状态通过确定性门。** 同机 NPU `torch_npu` `Tensor.bernoulli_` + 相同 seed；首次先验证 DUT 与标杆 RNG 消耗方式一致，成立后按人工裁定 `exact` 逐位比对；不一致先归因，不直接判 DUT 未通过。`RUNNABLE_METHOD_KINDS` 当前只有 `torch_cpu` / `numpy_cpu` / `opencv_cpu`（`precision_policy.py:863`），须新增“同机 NPU 参考” method_kind；这不违反 R3，任务书指定测试方法即走 tier 1 / `oracle_method`，不是绕过 CPU 兜底。`exact` 已有，无需新建；seed/offset 必须以完全相同的值喂给 DUT 与标杆，连带 G3 + G13 |
| **G12 dtype 三源事实不一致** | 两条都影响 | 按任务书列出的 dtype 全测；仅确认属于 DUT 能力缺失时形成未通过裁决；API 层拒绝、harness 限制、证据不完整须由确定性产物分类，后两者保持 `blocked` / `needs_review`，不得由 agent 直判算子未通过（展开见下） |
| **G6 dtype promote case 能力** | 两条都影响 | 任务书 `aclnnRemainderTensorTensor_task_doc.md:27` 的“语义一致”已将方向定为 torch 类型提升语义；补混合 dtype case 生成能力并按 torch 语义自验证（展开见下） |
| **G14 taskdoc output dtype 对账** | 仅 taskdoc | 明确 bug：只对首个 case 对账，第二个起可漂移不被拦（`taskdoc_caseset.py:828-836`、`gen_cases.py:1242-1257`）；output dtype 与每个 taskdoc case 的权威 `output_dtype` 字段逐项对账，覆盖漂移位于首/中/末及多 case 的顺序无关测试，任一漂移当场拒 |
| **G15 taskdoc dtype 词表对账** | 仅 taskdoc | 明确缺口。设 intake 归一化后的 canonical dtype 集为 `I`、当前 runner form 执行 dtype 集为 `E`、任务书必测 dtype 集为 `R`：`I ∩ E` 才允许进 caseset，且 dtype 逐 case 原样保留；`I − E`（intake-only）与 `E − I`（execution-only）都必须在执行前产结构化原因并终态 **BLOCKED**，不得静默过滤，也不得因 runner「能跑」就绕过任务书权威。总门：只要 `R` 任一 dtype 不在 `I ∩ E`，或 caseset 未逐参数覆盖 `R`，正式验收即 **BLOCKED**，不得产 PASS 或可用于裁决的精度证据；三类 fixture 均须覆盖并 fail-closed |
| **G4 fp64** | 两条都影响 | 任务书要 DOUBLE（Bernoulli 三源一致），但 `repo_adapter.py` 全文无 `float64`；任务书必测 dtype 不得靠现有 `DEFERRED` 收口，合法出路仅为真机证实后进 `SUPPORTED`，或形成会阻断验收裁决的明确未通过证据；除非先把 `DEFERRED` 补成带硬校且会阻断的门，否则不得用它承接任务书必测项 |
| **G3 host 标量入参** | 两条都影响 | ABI 写死 `aclScalar prob`，但 `gen_cases`/`cpp_extension_codegen` 均无 `host_scalar`/`aclScalar`，taskdoc 具体在哪层拦住未核；逐层（schema→生成→codegen→adapter→真机收据）正负各一；同时保证 Bernoulli seed/offset 与标杆完全相同 |
| **G13 Bernoulli 边界语义** | 两条都影响 | 任务书明写 `offset%4`、`prob=0/1`、属性绑定；generated 待核，taskdoc 部分随 case 带入；逐项核验后并入对应轴集批次，并保证 seed/offset 与标杆完全相同。⚠ `prob=0/1` 不进入随机路径，不能替代 G19 要求的 `(0,1)` 域内 case |
| **G19 Bernoulli 随机路径未覆盖（High）** | 两条都影响 | 已核 `experimental/random/bernoulli/op_api/aclnn_bernoulli.cpp:424-435`：`prob=0/1` 分别走常量填充/zeros/ones，完全不消费 `seed`/`offset`，仅 `0 < prob < 1` 进入 `BernoulliRandom`。caseset 至少加入一个严格位于 `(0,1)` 的 `prob`，在相同 shape/dtype/seed/offset 下同时跑 DUT 与标杆并做 `exact` 比对，以验证裁定 E 的 RNG 消耗一致前提；前提未验证前随机精度维 **BLOCKED** |
| **G7 Bernoulli 非连续** | 两条都影响 | 任务书参数表明写 √；落盘一律 `ascontiguousarray`（9 处），无 stride 表达；in/out 分账，覆盖连续 / 非连续 / 保不住布局三类测试 |
| **G5 广播（G3 的 shape 维）** | 仅 generated | 把写死的 `(4,1)/(1,5)`（`gen_cases.py:1939`）换成字段驱动；taskdoc 逐 case 自带 `shapes`（`:1258`），不走 shape 池。默认矩阵随 §5.2 方案提交评审，评审属常规流程、非标准缺失，不再单列拍板项 |
| **G8 rank 全局默认 1–8** | 仅 generated | 裁定 D 已给死；当前 `_REG_SHAPES`（`:2031`）仅 1–4 维，`_EXT_RANK_SHAPES`（`:2039`）仅两条 5 维且只在 spec 点名 rank 时并入，taskdoc 自带 shape；必须捆绑轴集版本化（旧算子锁旧版本），否则撞 `ExistingOpsByteIdenticalTest` |
| **G21 Bernoulli rank 0 未覆盖（High）** | 两条都影响 | 已核任务书 A 参数表：`self`/`out` 维度为 `0-8`，而裁定 D 的 1–8 只是任务书未明确时的默认。caseset schema、生成器和执行通路都须支持真正的 `shape=()`；rank 0 必须有真实执行与精度证据，不得用单元素一维 `(1,)` 冒充，缺失即 fail-closed |

**G12 展开 · BF16 与 DOUBLE 的事实方向相反：**

| dtype | 任务书 | header（API 层文档注释） | op_def（kernel 注册） | 含义 |
|---|---|---|---|---|
| **BF16** | ✅ | ❌ | ✅ | **底层有、上层文档没写** |
| **DOUBLE** | ✅ | ✅ | ❌ | **上层说有、底层没实现** |

BF16：`floor_mod_def.cpp` 有 `DT_BF16` kernel 注册证据，**端到端可执行性未证**；`aclnn_remainder.h` 注释的推导类型未列它，API 层可能拒绝，须实测并交确定性证据链分类。DOUBLE：header 明写支持，kernel 却未注册 `DT_DOUBLE`；真跑可能找不到 kernel，也可能 API 层 cast 成 float 后再算而改变精度语义。**两者必须分开取证，按任务书全测；仅确认属于 DUT 能力缺失时形成未通过裁决，API 层拒绝须单列，harness 限制或证据不完整保持 `blocked` / `needs_review`。**（INT32/INT64/FP16/FP32 三源一致；Bernoulli 三源完全一致。）

**G6 的含义：** dtype promote 是两个输入 dtype 不同时结果类型的提升规则，如 `int32 + int64 → int64`、`float16 + float32 → float32`、`int32 + float16 → float16`。Remainder header 要求 self/other 遵循「数据类型推导规则」、out 取推导类型，但 `[数据类型推导规则](#)` 均为空锚点、两仓无明文定义；任务书 `aclnnRemainderTensorTensor_task_doc.md:27` 已以 PyTorch 语义一致确定规则，工具仍因一 case 只有一个 dtype 而造不出混合 dtype case。

**G17 展开 · Critical fail-open，落在唯一准入形态上：**

```python
# perf_msprof.py:2635   cpp_extension 的 dut_lib 恒为 None
dut_lib = (resolve_plan_dut_lib(...) if custom_kind == "aclnn_py" else None)
# :2680                 于是这个分支对 cpp_extension 永不进入
if dut_lib is not None:
    baseline_cfg["exclude_dut_vendor_root"] = ...
```

`exclude_dut_vendor_root` 是跑 baseline 时摘掉 DUT vendor 根的**唯一开关**，对 `cpp_extension` 永远不生效 → baseline 可能继承 DUT 的 `ASCEND_CUSTOM_OPP_PATH`，**性能对比变成自己比自己，而 ratio 看着正常**。代码形状已复核属实，运行时后果待实测。

对 Bernoulli 精度 oracle，风险更严重：`Tensor.bernoulli_` 走 torch_npu 派发，DUT 是装进 custom OPP 的 vendor `.so`；若跑标杆时 DUT vendor 根仍在 `ASCEND_CUSTOM_OPP_PATH`，标杆会调到 DUT 自己。性能维自己比自己会得到约 1.0 的正常 ratio，尚且难以察觉；精度维则会逐位完全相同、满分通过，且完全察觉不到。因此 G17 + G11b 必须先于任何 Bernoulli 精度取证。

**G8 的现状与代价：** 代码注释说明只补到 5 维，是因没有实际算子要求 6–8 维、避免笛卡尔积与 golden 开销白涨；也不能直接并入 `_REG_SHAPES`，否则会悄悄改变既有 elementwise 用例集。改为默认 1–8 必然改变所有既有 elementwise caseset，并撞上以 sha256 钉住字节的 `test_gen_cases_dtype_attr.ExistingOpsByteIdenticalTest`；必须同时给出轴集版本化方案，旧算子锁旧版本，否则历史验收不可复现。

### 组 4 · 本轮不做

| gap | 影响哪条 case 来源 | 情况 |
|---|---|---|
| **G16 taskdoc 多输出契约** | 仅 taskdoc | 代码里有意显式不支持（`gen_cases.py:1225-1231`）；本轮两算子均单输出，不受影响。记录在案，不列为待办 |

*G1、G9 已被裁定消化并删除，编号不复用。*

### 已撤销的

| gap | 情况 |
|---|---|
| **G10 provenance** | **原判错，已撤**：`source_provenance.py` 里声明 `local_source`→实得 `local_snapshot` 本就是 `complete` 档，**不需授权、无降级**。仅「不能证明等于线上某 PR」仍成立，那是输入形态边界，不是缺陷 |

### 待核（未回代码核实，不得当作已定 gap）

- **待核 · format ND 构造**：有说法称 `cpp_extension` 通路未显式指定 format 时会按 rank 将 3/4/5 维映射为 NCL/NCHW/NCDHW，而两份任务书参数表均要求 **ND**；若属实，正式验收通路可能产不出任务书要求的 ND 输入。**本文档未核实，须先核 `cpp_extension_codegen.py`。**
- **待核 · CANN 最低版本门**：两份任务书均要求 **CANN 8.5.0 及以上**（任务书 `:8`）；有说法称当前只把版本记为字符串，不做语义比较与阻断。**本文档未核实。**

## 3 · 红线

1. **不按算子名特判。** 修法只能落在通用能力（能力表、轴集、契约、adapter）。
2. **随机算子的标杆与 seed 口径以任务书指定为准；本轮 `exact` 来自人工裁定，且须先验证 DUT 与标杆消耗 RNG 的方式一致，不得自行改用统计判定或在前提未证时直判 DUT 未通过。**
3. **不用 `dtype_deferred` 敷衍 fp64**——它零硬校、能让终态干净 `pass`（⚠ 该结论出自旧 handoff，未复核现行代码）。
4. **内存不是验收轴（验收维度永远是精度 + 性能）；报告如实写明本轮未做内存评估，但不得把它当阻断项，也不得反过来宣称内存已达标。**
