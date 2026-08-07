# aclnnRoll 对当前 OpRunway workflow 的 gap 调研

**日期**：2026-08-07
**性质**：只读调研，不代表已实施或已验收
**输入**：Roll 任务书、本地 checkout、任务书附带材料（只作参考）、当前 `worktree-oprunway21` workflow

**用例来源裁定**：Roll、Bernoulli、Remainder 的正式测试用例均由 OpRunway 根据任务书要求与被测来源事实自行生成。任务书附带的 case JSON、golden 和源码自测只用于发现覆盖要求、冲突和 PR-impact 场景，不作为正式 caseset 输入，不决定 `case_target`，也不以其执行结果替代 OpRunway 验收证据。

## 1 · 结论

Roll 与本地 checkout 的算子身份和目标目录能够对应，但当前 workflow **尚不能可信地产出 Roll 的正式验收裁决**。阻断点不是 complex64 本身：`complex64` / `uint32` 的 transport 与精度策略已有基础；真正的 P0 是 cpp_extension 输出写入可信门、standard stage-2 的显式 ND、空 `aclIntArray`，以及 CANN 最低版本语义门。

Roll 同时证明旧 Bernoulli/Remainder 计划的实施顺序已经过时：在 `output_not_written` 修复前，cpp_extension 的精度 PASS 与 FAIL 都可能失真；而 ND、CANN 版本、rank 0、非连续、generated dtype 对账是三个算子的共用能力，应先一次性补齐，再做 per-op 见证。

## 2 · 任务书与被测来源事实

| 项目 | 任务书权威要求 | 本地源码事实 | 判断 |
|---|---|---|---|
| 算子/目录 | `aclnnRoll`，合入 `experimental/math/roll`（任务书 1、62 行） | checkout 分支 `aclnn-roll-complex64`，HEAD `ddfbc6630`；实现位于同目录 | 对应成立 |
| 硬件 | Atlas A2/A3/A5（6 行） | op_def 声明 `ascend910b`、`ascend910_93`、`ascend950`（37–39 行） | 静态相容；仍须目标机真实构建 |
| CANN | ≥ 8.5.0（8 行） | workflow 只记录版本字符串、仅拒绝 unknown | 存在 G-R4 |
| ABI | x + shifts + dims + out 的两段式 ACLNN（29–34 行） | header 的 workspace getter 四个业务参数，stage-2 是标准四参（21–31 行） | 标准两段式，可抽取 |
| dtype | 正文列 bf16/f16/f32/i8/u8/i32/u32/c64（29、32 行） | op_def 另含 bool（18–33 行） | 存在任务书正文/回归材料张力 |
| format/rank/layout | ND、rank 0–8、x 非连续（29–32 行） | op_def 为 ND；源码有 rank-0/非连续测试 | workflow 分别存在 G-R2/G-R6/G-R7 |
| attrs | shifts/dims 等长，dims 在合法范围（30–31 行） | shifts required ListInt；dims optional ListInt({})（34–35 行） | 空 dims 语义需显式对账 |
| oracle | complex64 对齐 CPU，AscendOpTest 默认阈值（50 行） | 自测 golden 使用 CPU `torch.roll` | 方法相容；阈值须锚定 |
| 性能 | 正文“无”（46 行）；扩展不得影响旧 dtype 性能（86 行） | 自测仓有 wall-clock benchmark | 正式证据须走 measure_only msprof，wall-clock 不可替代 |

任务书附带材料包含 `case_basic.json` 50 条和 `case_complex64_100.json` 100 条。README/op.json 声明 9 个 dtype（含 bool），并明确 `dims=[]` 表示 flatten；这些只作为任务意图与冲突证据，不进入正式 caseset。更早的 `conversion/roll` 事实还出现 int16/int64，因此“保持原类型”全集并不能只靠正文或新 op_def 猜定。workflow 不得静默挑一边：正文 8 dtype 是权威明列集，bool/int16/int64 是待对账的回归候选；空 dims 要保留解析记录，并由生成规则形成正式用例。

## 3 · 当前已有能力，不能重复建设

1. 在线 PR 与本地 checkout 已是平级来源：`git_pr→gitcode_pr`、`local_source→local_snapshot`，并由 source facts、build receipt 和 staged spec 绑定。Roll 只需分别验证，不另造第三条来源通路。
2. 正式准入仅 `runner_form=cpp_extension`；Roll 旧 spec 的 `aclnn_py` 不能产裁决，必须迁移，不能靠 experimental 逃生阀翻案。
3. `complex64`、`uint32` 已进入 cpp_extension dtype transport 与精度策略；这只证明工具声明能力，不证明 Roll 已验收。
4. 非空 `list[int]` 已能生成 `int[]` schema 和 `aclIntArray` 调用计划；缺口只在空数组及静态/per-case 类型一致性。
5. 显式 `precision.case_target`、torch-parity 完整矩阵记账、staged spec/receipt 变更门已存在。三个算子必须走 generated cases；`case_target` 由任务书要求矩阵、具有证据的排除和定向 supplement 账本推导后显式填写，不能取附带 JSON 的 150，也不能用无依据默认值。

## 4 · Gap 清单

### P0：未关闭前不得做正式 Roll 精度见证

| ID | gap 与证据 | 所需通用修复 | 关闭判据 |
|---|---|---|---|
| **G-R1** | `cpp_extension_driver._empty_output` 仍用 `torch.empty`。历史 Roll 288 例试跑中出现 287 条疑似未写入，且 uint8 脏值碰巧等于 golden 的假 PASS；详见 `oprunway-output-written-gate-handoff.md` | 用全 dtype 哨兵分配；回读前检查；独立 `output_not_written`；bool/empty 显式 skip；诊断证据留存 | mutation 能证明移除检查会恢复假 PASS；未写入进入 errored/harness 桶，不进 precision pass/fail；正常写入不误伤 |
| **G-R2** | Roll header 是 standard stage-2；任务书要求 ND。codegen 对 standard + 显式非默认 format 直接拒绝，ND 只在 extended 手写路径实现 | standard 路径增加字段驱动的显式 ND 转换，保持四参 stage-2 ABI | 生成物出现 `ACL_FORMAT_ND`，standard 正常调用；未声明 format 的旧 fixture 不漂移 |
| **G-R3** | `gen_cases._is_int_array` 与 codegen 同时要求“非空 list[int]”；Roll 的 `dims=[]` flatten 无法表达 | 数组类型从声明/compose 判定，不以非空值猜类型；允许空 int_array，同时拒绝异质数组/bool 元素 | `shifts=[k],dims=[]` 和 rank-0 合法 fixture 可生成、绑定、执行；坏数组在执行前 fail-closed |
| **G-R4** | 任务书要求 CANN ≥8.5.0；driver 只记录字符串，三级门只检查非空/unknown | spec 承载最低版本并做语义版本比较 | 低版本、不可解析、unknown 均阻断；等于/高于通过；不得硬编码 Roll 或 8.5.0 |

### P1：覆盖与权威输入

| ID | gap | 处理 |
|---|---|---|
| **G-R5** | 当前 generated 规则尚无 Roll 的 cyclic-shift 结构轴，也缺 requirement→case IDs 的 mandatory coverage 证明 | 新增 op-中立 `cyclic_shift/circular_index_remap` 原语规则和版本化生成 profile；由完整矩阵、排除与 supplement 三重记账推导显式 `case_target` |
| **G-R6** | 任务书明确 rank 0–8；旧生成链 historically 偏向 rank≥1 | 复用旧计划 B14 的真 rank-0 契约，shape 必须为 `()`，不得用 `(1,)` 代替 |
| **G-R7** | x 明确支持非连续；当前布局能力尚未形成全链正式证据 | 复用旧计划 B6 的 layout schema/driver/receipt/coverage ledger；只要求输入非连续，不扩写输出要求 |
| **G-R8** | 正文 8 dtype 与自测/op_def 9 dtype（bool）不一致；dims required/optional 也有张力 | 权威必测集、回归扩展集、来源冲突分开记；禁止用交集静默删要求或用 op_def 反向扩写任务书 |
| **G-R9** | complex64 transport/policy 已有，但尚无 Roll 的复数造数、CPU `torch.roll`、阈值锚和逐 case output dtype 的完整证据 | 自行生成输入与 CPU `torch.roll` golden；记录 AscendOpTest 默认阈值的可复核来源，无法锚定即阻断；附带 golden 只作交叉参考 |

### P1：执行身份与构建

| ID | gap | 处理 |
|---|---|---|
| **G-R10** | `CMakeLists.txt` 使用 `ACLNNTYPE aclnn_exclude`，尚未证明当前 vendor build 流程会产出并加载 Roll 的目标 op_api ELF | 在 CP-C0/build receipt 前核清构建入口、产物和两个 ACLNN 符号定义者；无目标 ELF/符号不得进入执行 |
| **G-R11** | 旧 Roll 试跑已显示调用看似 produced，但输出未写；根因可能在桥、加载身份或 DUT，尚未解耦 | G-R1 先分类，再以来源锚、双符号定义者、正常写入最小 case 做 root-cause；不得直接判 DUT 精度失败 |

### P2：性能与报告

| ID | gap | 处理 |
|---|---|---|
| **G-R12** | 任务书“无性能要求”但要求旧 dtype 性能不受影响；自测 wall-clock 不是 kernel-only 证据 | 走 `measure_only_authorization=no_perf_requirement`，对 complex64 与旧 dtype 分档做 msprof；“不影响”条款在本轮按“未验收”进入 `task_pr_gaps`，不设等待条件、不算 ratio、不宣称性能达标 |
| **G-R13** | 交付件对账尚未针对 Roll 跑过 | 设计文档、自测脚本/README、报告、仓链接按精确路径对账；认不出即 gap，不做模糊名称匹配 |
| **G-R14** | README 声称存在完整 `case.json`，实际随附目录缺失，且附带 case 内 golden 是机器绝对路径 | 作为附带材料不完整性记录；因正式用例自行生成，不为这些 JSON 建 intake/路径重写能力，也不让该缺口阻塞 generated cases |
| **G-R15** | 任务书/op_def 声明 ND，但 API 只拒 private format，UT 还有 NCL 成功例 | 正式要求仍按任务书 ND；把实现更宽的事实记冲突，不把 NCL 混进权威 caseset，也不据此声称格式缺陷 |
| **G-R16** | generated attr 轴会把 shifts/dims 独立展开，可能制造任务书未声明的非法组合 | 增加字段驱动的 atomic attr-row/constraint-group；等长规则与 `dims=[]` flatten 例外按来源逐行绑定 |
| **G-R17** | generated caseset 尚没有覆盖 Roll 全部任务书要求的 mandatory ledger | 每条任务书要求映射到 generated case IDs，或落带 reason/evidence 的排除/gap；附带材料“有 150 条”不能代替覆盖证明 |

## 5 · 推荐见证集

正式 caseset 完全由 OpRunway 生成。生成 profile 先覆盖任务书明确轴，再以源码/附带材料识别出的 PR-impact 场景形成有来源的定向 supplement：

- 调用可信：int8/uint8 单元素，专门复现并关闭历史假 PASS；bool 与 empty 验证 skip 可见性。
- ABI：非空多轴、重复/负 dim、大 shift、`dims=[]` flatten。
- 结构：rank 0、rank 8、空 tensor、非连续 x。
- dtype：正文 8 dtype 全覆盖；bool/int16/int64 在“保持原类型”对账完成后作为有来源解释的回归扩展；complex64 覆盖实部/虚部、fftshift/ifftshift 语义。
- 性能：complex64 + 各旧 dtype 至少一个有效 msprof case，并记录 kernel-only 范围与 provenance。

常规矩阵与定向 supplement 分账；每条用例须记录 rule、requirement、来源与 case identity。不得复制附带 JSON 后冒充自行生成，也不得把附带用例条数当 `case_target`。

## 6 · 明确不做与待真机验证

- 不把旧 `aclnn_py` 结果升级为正式验收。
- 不把自测 wall-clock 当 msprof，不做 GPU 比较。
- 不因 op_def 含 bool 就静默改写任务书正文。
- 不把 `ACLNNTYPE aclnn_exclude` 直接判成构建失败；它是必须在真实 build/ELF/符号链验证的风险。
- 本文未执行 build、pytest、用例生成、golden 或 NPU 跑测，因此所有“关闭判据”仍是计划，不是结果。
- 目标机多卡可用于正式 caseset 分片，但当前 workflow 尚未在本文中证明 shard manifest、无重无漏汇总及单卡/多卡等价性；该能力已纳入统一计划 N10，不能靠人工拆 case 后手工合计。
