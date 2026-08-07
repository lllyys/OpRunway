# aclnnBernoulli / aclnnRemainderTensorTensor 接入实施方案

> 唯一依据是 `dev-doc/oprunway-bernoulli-remainder-gap-todo.md` 的定案版 gap 清单。
> 本文只定义实施顺序、依赖和机械完成判据；未经用户同意不实施。

## 0 · 边界与已生效裁定

1. **泛化优先。** 所有能力按字段、契约或稳定接口能力实现，不按算子身份分支；具体算子只作见证。
2. **执行边界。** 本地只编辑、Git 和只读探测；build、pytest、用例生成、golden、真机跑测与 profiler 均在 NPU 目标环境执行。
3. **确定性裁决。** agent 不重判 pass/fail；完成判据必须落实为退出码、字段值和允许/禁止生成的产物集合。
4. **裁定 A。** 验收维度只有精度与性能；资源类不是验收轴。性能侧不取 GPU 标杆、不算 GPU 比值；
   精度侧若任务书写 GPU 真值，按 §5.11 解析为同族 CPU，而不是删除。报告只写“本轮未做内存评估”，不得宣称内存达标。
5. **裁定 B。** 任务书显式指定 oracle 方法时优先于自带 golden；未指定才消费自带 golden；
   两者缺失或冲突均 fail-closed。规则按 oracle 声明字段实现，不按算子名实现。
6. **裁定 C。** 任务书未规定的语义不进入验收：Remainder 非连续、`prob` 越界、Remainder 除零不纳入；Bernoulli 非连续因任务书明写而保留。
7. **裁定 D。** rank 在任务书未明确时全局默认 1–8；任务书明写 0–8 时必须覆盖 rank 0，不能用默认范围覆盖明文要求。
8. **裁定 E。** 随机算子标杆为同机 NPU `torch_npu` `Tensor.bernoulli_`，DUT 与标杆使用相同 seed，
   比较关系为人工裁定的 `exact`；成立前提是二者消耗 RNG 的方式一致。

## 1 · 已裁定的三项及其记账要求

| 裁定 | 实施口径 | 风险与记账要求 |
|---|---|---|
| **F · G11a** | 「原算子」解释为优化前的 CANN 内置实现；两份 spec 的性能配置显式写 `aclnn_builtin`。B8 绑定预期 libopapi 身份、ELF 指纹及 `aclnnXxx` / `aclnnXxxGetWorkspaceSize` 两个 ACLNN 符号定义者；msprof 只比 NPU 实测 | 报告须注明这是解释及其依据：与「对现有算子做内存优化」语义一致，且现有词表、libopapi 指纹与双符号定义者证明可复用。身份、指纹或任一符号证明不符即 fail-closed，不得退换其他 baseline |
| **G · G18** | Remainder 仍以任务书的 A2/A3 为验收目标；spec 保留 A2/A3，并记录 `math/floor_mod/CMakeLists.txt:12` 与 `op_host/floor_mod_def.cpp:45` 中 PR 仅声明 `ascend950` 的冲突，在 A2/A3 上执行构建前门与真实构建。构建失败或无对应注册交确定性链形成受控非通过/阻断态，禁止 PASS；`ascend950` 最多作标记为 development 的诊断 | 静态声明未必完整，不得仅凭源码直接判 DUT 失败。报告须同时写明任务书目标、PR 声明、实际硬件与真实构建结果；未完成真机核验前保持 fail-closed |
| **H · G20** | Remainder 取材使用 `--target-dir math/floor_mod`；source_facts/spec 同时记录任务书 `:7` 的 `math/floor_mod` 与 `:68` 的 `experimental/math` 冲突，交付物对账按实际取材目录执行 | 目录自相矛盾与「实际交付未位于 `experimental/math`」均列入 `task_pr_gaps`，不得宣称目录条款已满足 |

当前不再保留人工拍板项。BF16/DOUBLE 按任务书全测，promote 按任务书指定的 PyTorch 语义，随机 oracle 与 `exact` 已由裁定 E 给定。

## 2 · P0：待核前置（只读，不改代码）—— **已于 2026-08-07 全部完成**

完整证据链：`.cc-suite/audits/audit-p0-readonly-findings-20260807.md`。

- **P0-a · format ND。** ✅ RESULT=属实 → 已转 **G22**，批次见 B16。
- **P0-b · CANN 8.5.0+。** ✅ RESULT=属实 → 已转 **G23**，批次见 B17。
- **P0-c · dtype 分类契约。** ✅ RESULT=未区分（需 B4 先补契约）→ 四类成因的具体断链位置已钉死在归档文件里，B4 直接按那四行改，不必重新分析。
- 三项均已列出实际控制流、入口和可复核证据，无「未能确认」项。P0 本身**不改代码、不生成验收裁决**，
  只产核验记录——真正的代码改动在 B16/B17/B4 里。

## 3 · Phase A：覆盖硬门

### A0 · 权威轴声明承载链

- **来源与落点：**由 G4/G5/G7/G8/G12/G13/G15/G21 的任务书覆盖要求推导；落在 spec schema 的版本化轴字段、来源冲突记录字段及其 Layer 1 准备态校验入口与契约测试。
- **做什么：**分开承载任务书必测集 `R` 与事实集合：`R` 是唯一权威要求，只从任务书派生；intake 能力 `I`、执行能力 `E` 只用于记录冲突和能力分类，不进入权威轴声明，也不得增减 `R`。
- **完成判据：**三类轴各有缺字段负例；另断言 `R` 的来源只含任务书，被测事实只写冲突记录字段；失败时退出码非 0、错误字段为受控确值，且验收裁决产物集合为空。

### A1 · 覆盖退化终态核验

- **来源与落点：**服务于 A0 所列各 gap 的通用硬门；落在 Layer 1 终态契约与对应契约测试。
- **做什么：**先只读核定“声明轴为零条或仅退化哨兵”对应的唯一受控阻断分类、退出码和产物集合，再据此固化断言。
- **完成判据：**核验结果中三个确值均唯一；不得以“任意非 pass”或占位符代替。

### A2 · 覆盖账本硬化

- **来源与落点：**服务于 A0 所列各 gap 的覆盖可追溯性；落在覆盖账本 schema、Layer 1 终态入口及退化/正常 fixture。
- **做什么：**声明轴为零条或仅退化哨兵时写结构化事实，并由终态链 fail-closed。
- **完成判据：**退化 fixture 的账本字段等于 A1 核定值、进程退出码匹配、裁决文件不存在；正常 fixture 的轴计数大于零。

## 4 · Phase B：通用能力与权威输入

### B11 · 修 G14：taskdoc 每 case output dtype 对账

- **做什么：**不再只对首个 case；每个 taskdoc case 的实际 output dtype 与权威 `output_dtype` 逐项对账。落点为 taskdoc caseset 归一化、`gen_cases` 的逐 case 校验入口及其测试。
- **完成判据：**漂移位于首/中/末的多 case fixture 均退出非 0，错误含 case id、期望值和实得值，且 caseset 与裁决产物均不存在；调整 case 顺序不改变判定。

### B12 · 修 G15：taskdoc dtype 三类对称 fail-closed

- **做什么：**在 taskdoc dtype 对账 schema、Layer 1 执行前总门及契约测试中，令 intake 归一化 dtype 集为 `I`、runner 执行集为 `E`、任务书必测集为 `R`；只有 `I∩E` 可进入 caseset并逐 case 保留 dtype。`I−E` 与 `E−I` 均给结构化原因并终态 BLOCKED。
- **完成判据：**三类 fixture 均有断言：`I∩E` 的 `coverage_status=READY`、退出码 0，且 caseset dtype 内容逐字等于该交集；后二者退出非 0、状态字段为 BLOCKED、无可用于裁决的精度证据。总门另断言：`R` 任一项不在 `I∩E`，或 caseset 未逐参数覆盖 `R` 时，正式验收为 BLOCKED、退出非 0，且禁止生成任何可用于裁决的精度证据。

### B3 · 修 G3 / G6：多输入异构（B2 前置）

- **落点：**本批第一步逐层核验 schema、生成器、codegen、adapter/driver、收据与测试，产出准确落点清单后再改，不预填未经核实的文件清单。
- **B3-a：**schema → 生成器 → codegen → adapter/driver → 收据逐层支持 `host_scalar`、seed/offset 属性绑定和每输入独立 shape。
- **B3-b：**每输入独立 dtype，并按任务书指定的 PyTorch `torch.remainder` / `Tensor.remainder` 语义生成 promote case。
- **完成判据：**正例计划逐输入记录 kind/dtype/shape，调用桥按 ABI 传 `aclScalar*` 与 int64 属性；缺绑定、非法标量 dtype 或缺 promote 结果的负例退出非 0，且执行与裁决产物均不存在。

### B2 · 修 G5：广播轴字段驱动（依赖 B3-a）

- **做什么：**以 caseset schema 的输入独立 shape 字段驱动生成器，替换固定 shape；taskdoc 自带 shapes 的通路保持逐 case 消费，测试落在广播轴 fixture。
- **完成判据：**改变字段会机械改变 shape 对；账本逐广播模式计数均大于零；必需模式缺失时退出非 0；旧轴集 fixture 去除 `producer` 后 payload 逐字节相同。

### B4 · 修 G4 / G12：dtype 能力与三源冲突

- **做什么：**按算子分别对账：Bernoulli 的任务书 `DOUBLE` 归一化为 canonical `float64`；Remainder 的任务书 `BF16` / `DOUBLE` 各自归一化为 canonical dtype。任务书、header、op_def 与端到端事实分开记录；落点为 dtype 能力表、P0-c 核定的 Layer 1 分类入口及四类 fixture。
- **完成判据：**任务书必测 dtype 只有真机全链证实后进入 `SUPPORTED`，或按 P0-c 分类两条出路。B4 先补齐 P0-c 标出的 Layer 1 契约缺类；四类 fixture 均逐字断言该表的状态字段、退出码和产物集合：API 拒绝、harness 限制为各自受控 `BLOCKED` 值且非零退出；证据不完整为受控 `needs_review` 值且非零退出；只有已确认 DUT 能力缺失允许形成受控未通过裁决，其退出码和产物集合也须等于分类表确值。前三类无 PASS、无裁决产物、无可用于裁决的精度证据。
  另设专门负例：任务书必测 dtype 被路由到现有 `DEFERRED` 时，非零退出、状态为 P0-c 核定的受控阻断值，无 PASS、无裁决产物、无可用于裁决的精度证据。

### B5 · 修 G8：rank 默认 1–8 与轴集版本化

- **做什么：**轴集版本只由 spec/caseset 的显式版本化字段选择，缺字段走受控默认；不得按 op 名称或隐式历史名单分支。旧版与默认 1–8 的新版在轴 schema、生成入口及版本 fixture 同批落地。
- **完成判据：**旧版 fixture 去除 `producer` 后 payload 逐字节不变；新版账本 rank 1–8 计数均大于零；版本字段、case 总数和预算字段一致，超预算退出非 0。

### B14 · 修 G21：真正的 rank 0（与 B5 同批或紧邻）

- **做什么：**当任务书明确包含 0 时，caseset schema、生成器与执行通路支持真正的 `shape=()`，不以 `(1,)` 代替；测试落在 rank 0 正负 fixture。
- **完成判据：**计划、caseset、执行收据和精度证据均表达真正的 `shape=()`，rank 字段为 0；
  把它改成 `(1,)` 或删去 rank 0 时退出非 0，且不得生成裁决产物。

### B6 · 修 G7：任务书明写的非连续轴

- **做什么：**在布局 schema、生成/执行入口及布局 fixture 中表达输入/输出 stride、storage offset 与 base storage 尺寸，in/out 分账；只覆盖任务书明写的非连续要求。
- **完成判据：**连续、非连续、无法保持布局三类 fixture 中，前两类 in/out 计数分别大于零；无法保持或任一侧被物化连续时退出非 0，且裁决产物不存在。

### B7 · 修 G2：同机 NPU oracle method_kind 与证据契约

- **做什么：**新增“同机 NPU 参考”的受控 method_kind，使任务书显式指定的 `torch_npu` `Tensor.bernoulli_`
  以 tier 1 / `oracle_method` 进入可运行方法集；接入相同 seed 与 `exact` 的证据契约。
  此举符合 R3 第一档“任务书指定的测试方法”；落点为 oracle method_kind 词表、精度证据 schema、Layer 1 预检入口及其契约测试。
- **完成判据：**方法字段、设备身份、seed 与 predicate 分别等于受控确值；缺字段、词表外方法、非同机设备或 seed 不同均在 oracle 执行前退出非 0，且无可用于裁决的精度证据。

### B9 · 修 G13：任务书边界与属性绑定

- **做什么：**先逐条核清任务书中 `offset%4` 的准确语义，再严格按原文在属性 schema、生成/绑定入口及测试中建立正负例；同时覆盖任务书明写的 `prob=0/1` 与 seed/offset 属性绑定。`prob=0/1` 只算常量分支覆盖，不能替代 B13。
- **完成判据：**正负例逐条对应任务书原文；账本中各要求计数均大于零，属性收据中的 int64 值与 case 字段逐字相等；不预设拒绝层级，也不扩充任务书未定义的非法集合；任一必需计数为零时不得开始随机精度取证。

### B13 · 修 G19：`prob∈(0,1)` 的 RNG 前提验证

- **做什么：**caseset 至少包含一个严格位于 `(0,1)` 的 prob；在相同 shape/dtype/seed/offset 下同时运行 DUT 与同机 NPU 标杆并做 `exact`，只产独立的“RNG 前提验证收据”。该收据与正式精度证据物理分离，标记为不可用于裁决，且裁决链不得消费；落点为独立收据 schema/入口及消费隔离测试。
- **完成判据：**收据中的 prob 满足 `0 < prob < 1`，两侧字段逐字相同、逐元素关系为 `exact`，且 `evidence_grade=precondition`、`usable_for_verdict=false`；只有收据通过后才允许开始正式 Bernoulli 精度取证。未执行或逐位不一致时状态为 BLOCKED、退出非 0、无正式精度证据和精度裁决；不一致只进入归因流程，不直接判 DUT 未通过。

### B8 · 修 G11a：性能 baseline `aclnn_builtin`

- **做什么：**按裁定 F，在两份 spec 的性能字段显式写 `aclnn_builtin`；Layer 1 取证前门绑定预期 libopapi 身份、ELF 指纹及 `aclnnXxx` / `aclnnXxxGetWorkspaceSize` 两个 ACLNN 符号定义者。性能侧只用 msprof 比较 NPU 实测，不取 GPU 标杆。
- **完成判据：**来源锚缺失、baseline kind 不是 `aclnn_builtin`、预期 libopapi 身份/ELF 指纹不符或任一 ACLNN 符号定义者不符时，均在取证前退出非 0，且无性能裁决；不得退换其他 baseline。合法收据中的 baseline kind、身份、defining ELF 指纹与两个符号定义者逐字等于预期值。

### B16 · 修 G22：cpp_extension 显式产出 ND（P0-a 确认属实）

- **来源标注**：`cpp_extension_codegen.py:99-105` 是**代码里描述 op-plugin 外部行为的说明性注释**，不是本仓自己的映射实现；真正的默认路径调用点在 `:474-477`（走官方 `ConvertTypes(...)`），显式 ND 转换器在 `:389-405`，缺省来源判定在 `:250-252`，standard 拒绝分支在 `:537-545`。
- **做什么：**spec 新增显式 `aclnn_tensor_format` 声明入口（对齐现有字段，值域含 `nd`）；`cpp_extension_codegen` 按声明选中支持 ND 的派发路径。
  ⚠ **当前只有 `extended` stage-2 实现了显式 ND 转换器**（`:389-405`），`standard` 遇非默认 format
  在生成期即拒（`:537-545`）。**若某个接口的实际派发形态是 `standard`（由 header/preflight 判定，
  `:208-241`），仅仅「声明 nd 就报错退出」不构成 G22 的修复**——那只是把静默猜错换成了显式拒绝，
  任务书要求的 ND 用例依然一条都产不出来。本批必须先核清两个算子实际落在哪种派发形态：
  若均可合法走 `extended`，则按声明路由过去即可关闭 G22；若有算子的接口只能是 `standard`，
  则必须**同时**给 `standard` 实现等价的显式 ND 支持（在 stage1 转换段插入 `ACL_FORMAT_ND` 覆盖，
  不改 4 参 ABI），否则 G22 对该算子仍是未关闭状态，必须如实记录、不得算作完成。
- **完成判据：**① 先产出「两个算子的接口各自落在 standard 还是 extended」的核验结论（file:line 证据）；
  ② 声明 `nd` 且实际可达路径存在时，生成产物的 tensor format 字段逐字等于 `ACL_FORMAT_ND`；
  ③ 声明 `nd` 但确无可用路径（且未按上一条补齐 standard 支持）时，生成期退出非 0、错误信息指向
  具体缺口，不产任何用例产物，且**报告需如实写「G22 对该算子仍未关闭」**，不得含糊带过；
  ④ 未声明 format 时行为不变（回归护栏，见 §7）。

### B17 · 修 G23：CANN 最低版本语义门（P0-b 确认属实，通用化）

- **做什么：**在 `cpp_extension_driver.py`（`:473-482` 读取 `cann_version` 处）与
  `validate_acceptance_state.py`（`:1029-1034` 非空检查处）接入语义化版本比较，替换现有
  「非空即通过、仅 `"unknown"` 阻断」的判据。⚠ **不得把 `8.5.0` 写死进通用门**——按 §5.1
  泛化优先，最低版本要求本身来自任务书，须新增字段从任务书/spec 读取「要求的最低版本」，
  门比较的是「要求值 vs 实测值」，不是硬编码某个具体版本号。落点为上述两文件、
  spec schema 的最低版本字段、及现有测试 `test_validate_cpp_extension_receipt.py:156-186`
  （须同步改造该处的非语义化字符串 fixture）。
- **完成判据：**spec 声明最低版本要求时，实测版本低于该要求（含无法解析的非语义化字符串）时退出非 0、
  状态为受控阻断值、无 PASS；实测版本 `≥` 要求值时正常通过；`"unknown"` 仍按原有语义阻断（回归，
  不得放松）；未声明最低版本要求时的行为需显式定义（缺省放行还是 fail-closed，二选一写清楚，
  不留隐式默认）。

## 5 · Phase C：取证身份可信门

### C2 · 修 G11b：双符号正向定义者证明

- **做什么：**在已有路径/环境/来源锚绑定上，证明两段式 `aclnnXxx` 与 `aclnnXxxGetWorkspaceSize` 两个符号的实际定义者；不能以 `hasattr` 代替。落点为身份收据 schema、Layer 1 取证前门及双符号 fixture。
- **完成判据：**收据逐符号记录 defining ELF 与指纹；DUT 两个符号均命中来源锚。任一缺失、定义者不符或指纹不符时，在取证前退出非 0，且精度、性能裁决产物均不存在。

### B10 · 修 G17：DUT / 标杆正向身份隔离（依赖 C2）

- **做什么：**DUT 必须命中来源锚，标杆必须命中预期身份/指纹，两侧两个符号的定义者均获证明，且两侧 ELF 不同；取证环境不得让标杆继承 DUT vendor 根。落点为身份隔离收据字段、Layer 1 取证前门及污染注入 fixture。
- **完成判据：**确定性门在取证前对账上述字段；缺失、同源、标杆命中 DUT、来源锚或指纹不符均退出非 0，且不生成精度或性能证据。正例收据中 DUT/标杆身份、双符号定义者和指纹均与预期逐字相同。

C2 → B10 不仅是性能卫生，也是精度 oracle 完整性前提：标杆走 `torch_npu` 派发时，
若仍加载 DUT vendor `.so`，会形成自己与自己逐位相同的假通过。

## 6 · 顺序与见证依赖

```text
P0-a + P0-b + P0-c（已全部完成，2026-08-07）
  → B16（G22 format ND）                             （落地前必做，其后 B3-a/B2 才在同一文件基础上改）
  → B17（G23 版本门）                                  与 B16 平行，与主链无文件重叠，可随时插入
  → C2 → B10
  → A0 → A1 → A2
  → B11 → B12
  → B3-a → B2；B3-b                                    （B16 之后再动 cpp_extension_codegen.py，避免同文件冲突）
  → B4（先按 P0-c 归档的四行断链证据补契约）；B5 ↔ B14；B6；B7 → B9 → B13（RNG 前提验证收据）→ 正式 Bernoulli 精度取证
  → B8（按裁定 F 使用 `aclnn_builtin`）
  → 真机见证
```

- P0 三项均已完成且均需后续批次（P0-a/b 属实 → B16/B17；P0-c 未区分 → B4 先补契约）。
- `C2 → B10` 必须早于任何真机精度或性能取证；B10 的标杆正向身份校验同时保护性能 baseline 与精度 oracle。
- `B11 → B12` 必须早于任何 taskdoc 见证；`B3-a → B2` 不得倒置；B5 与 B14 同批或紧邻。
- B13 只产不可用于裁决的 RNG 前提验证收据；该收据通过后才能开始正式 Bernoulli 精度取证，未通过时保持 BLOCKED。
- Remainder 的真机见证（build/exec）依赖 B16 落地后 spec 才能显式声明 ND；未落地前 Remainder 的 format 仍是隐性猜测，不构成任务书要求的证据。
- Remainder 按裁定 G 在 A2/A3 上执行构建前门与真实构建；构建失败或无对应注册交确定性链形成受控非通过/阻断态，禁止 PASS。`ascend950` 最多作 development 诊断，不作验收证据。
- Remainder 按裁定 H 使用 `--target-dir math/floor_mod` 取材，并在 source_facts/spec 与 `task_pr_gaps` 中保留 `experimental/math` 冲突和目录条款未满足记录。

| 见证 | 硬依赖 |
|---|---|
| **Remainder** | P0；裁定 H：以 `--target-dir math/floor_mod` 取材并记录目录冲突；裁定 G：以 A2/A3 为验收目标并真实构建；C2 → B10；A0 → A1 → A2；B11 → B12；B3-a → B2，B3-b；B4；B5；裁定 F → B8 |
| **Bernoulli** | P0；C2 → B10；A0 → A1 → A2；B11 → B12；B3-a；B4；B5 ↔ B14；B6；B7 → B9 → B13；裁定 F → B8 |

## 7 · 回归护栏与明确不做

1. 轴集扩面必须版本化；未命中新能力的旧 fixture 不得静默变化。工具变化导致 `producer.logic_sha256` 改变时，只比较去除 `producer` 后的 payload。
2. 不新增监工 agent，不按算子名改通用规则，不由真机能力反推任务书范围，不把 `(1,)` 当 rank 0。
3. 不在本地 build、跑测试或做验收 compute；不得把 F/G/H 已裁定写成相关 gap 已解决或验收已通过。
4. 不做资源类评估或 GPU 性能标杆；任务书若写 GPU 精度真值，按同族 CPU 解析并留下解析记录。
5. 不验任务书未规定的行为，不把“裁定不做”写成“已解决”，不把待核项写成已确认 gap。
6. G16 保持有意不支持；本轮两算子单输出，不扩多输出契约。

## 8 · 定案 gap 处置对照

| gap | 定案处置 | 批次 / 状态 |
|---|---|---|
| **G2** | 同机 NPU oracle method_kind；相同 seed、`exact`；先验 RNG 前提 | B7 + B13 |
| **G3** | host scalar 与 seed/offset 贯通全链 | B3-a |
| **G4** | fp64 真机全链；只准 SUPPORTED 或阻断裁决的未通过证据 | B4 |
| **G5** | 每输入 shape 字段驱动广播 | B2 |
| **G6** | 按任务书指定的 PyTorch 语义生成混合 dtype | B3-b |
| **G7** | 只做任务书明写的 Bernoulli 非连续，in/out 分账 | B6 |
| **G8** | 未明确时默认 rank 1–8，强制轴集版本化 | B5 |
| **G10** | 原判错已撤；不作为待办 | 已撤销 |
| **G11a** | 按裁定 F 使用 `aclnn_builtin`，绑定预期 libopapi 身份、ELF 指纹与双符号定义者；不符即 fail-closed，不得换 baseline | B8 |
| **G11b** | DUT 来源锚、标杆身份/指纹与双符号定义者正向校验 | C2 → B10 |
| **G12** | BF16/DOUBLE 分开全测并由确定性证据分类 | B4 |
| **G13** | 只核任务书明写边界和属性绑定 | B9 |
| **G14** | taskdoc output dtype 逐 case 对账 | B11 |
| **G15** | `I∩E` / `I−E` / `E−I` 对称 fail-closed，加 `R` 总门 | B12 |
| **G16** | 有意缺口；本轮单输出不受影响 | 本轮不实施 |
| **G17** | 取证前隔离 DUT/标杆并正向校验身份 | C2 → B10 |
| **G18** | 按裁定 G 保留 A2/A3 验收目标与 `ascend950` 冲突；在 A2/A3 上真实构建，失败或无注册交确定性链形成受控非通过/阻断态 | §1，Remainder 真机见证 |
| **G19** | 以 `(0,1)` prob 验证 RNG 消耗一致前提 | B13 |
| **G20** | 按裁定 H 以 `math/floor_mod` 取材，同时把 `experimental/math` 冲突与目录条款未满足列入 `task_pr_gaps` | §1，Remainder 取材 |
| **G21** | 真正 `shape=()` 的 rank 0 全链 | B14 |
| **G22** | spec 显式声明 ND，codegen 按声明选中支持路径，选不到即 fail-closed | B16 |
| **G23** | 接入语义版本比较，低于 8.5.0 阻断 | B17 |

## 9 · 挂账与回炉条件

- G18 按裁定 G 仍以 A2/A3 为验收目标：源码仅声明 `ascend950` 的事实不能单独判 DUT 失败；须在报告同时记录任务书目标、PR 声明、实际硬件与真实构建结果。真实构建失败或无对应注册交确定性链形成受控非通过/阻断态，禁止 PASS；真机核验前保持 fail-closed，`ascend950` 诊断只能标记为 development。
- G20 按裁定 H 使用 `--target-dir math/floor_mod`，但任务书 `:68` 的 `experimental/math` 与实际交付目录矛盾仍须写入 source_facts/spec；目录自相矛盾与「实际交付未位于 `experimental/math`」列入 `task_pr_gaps`，不得宣称目录条款已满足。
- P0 的 format ND（G22）与 CANN 8.5.0+ 版本门（G23）已于 2026-08-07 核实，均属实，批次见 B16/B17；
  证据链归档 `.cc-suite/audits/audit-p0-readonly-findings-20260807.md`。
- B4 实施前先读 P0-c 归档文件里的四行断链证据（① API 拒绝混入普通执行失败、②harness 限制可合法落成
  `fail`、③ 证据不完整拆成两套语义且与①混用、④ `dtype_unsupported_by_op_def` 映射成
  `passed_with_gaps` 非「未通过」且 `dtype_unsupported_on_target_hw` 未接入 `validator.py`），
  不必重新分析这四类现状，直接从那四行开始改。
- B13 若证明 DUT 与标杆的 RNG 消耗方式不一致，随机精度维保持 BLOCKED，裁定 E 的 `exact` 前提须回炉；不得直接把逐位差异判成 DUT 未通过。
- G11a 按裁定 F 固定为 `aclnn_builtin`；报告须标明这是对「原算子」的解释及其依据。预期 libopapi 身份、ELF 指纹或两个 ACLNN 符号定义者任一不符即 fail-closed，不得退换其他 baseline。
- B5 的轴集版本与规模预算须在实施方案获准后定稿；不得以扩面为由破坏旧 caseset 可复现性。
