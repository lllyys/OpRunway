# Contract IR schema v3（冻结 2026-09-10，v1 起经方案乙演进）

**状态**：v3 冻结（CONTRACT_VERSION=3）。v1 首冻（Codex 四轮，/goal 授权）→ v3 经 Codex
review-plan 采纳方案乙演进（加 upload_guard、行为源二分）。改 schema 须 bump
CONTRACT_VERSION 并过 Codex 评审（§13）；sasum rev1 对拍基准已经 Step 3 真机复验生效。

**Contract IR**（下称 IR）是 H2 从 H1 快照编译出的机器可读可执行契约：生成的 C++ 按它
读列、调 golden、做 verify 与状态绑定；发射器只消费不判定，不做任何隐式推断。
CONTRACT_VERSION=3；IR 为内部产物，JSON 可序列化。本文由两份竞争草案经 29 项分歧裁决、
双盲实例对拍与 18 项对抗攻击修订而成（过程档在开发记录，不随分发）。

## 0. 装载准则与判定层次

- 改变生成面、或承担一条拒绝判定的事实，必有 IR 字段；恒值劈成单值闭枚举。
- 编译器零歧义可派生的量物化一次进 IR；同一事实不双写。
- 纯 provenance（哈希、指纹）住 H1 快照与渲染 manifest，不进 IR。

判定分四层，全 fail-closed，层名即拒绝载荷的 gate 值域：

| 层 | 查什么 | 停机码 |
| --- | --- | --- |
| pre-IR 三道门（H1）`pre_ir` | ABI 版本、装载纪律、表头同源 | 见 package-abi.md |
| 参数门 `param_gate` | 闭键校验、值域单值、标识符正则、mem/dir 对账 | UNSUPPORTED_CONTRACT |
| AST 门 `ast_gate` | shape/cond 节点白名单、语境约束 | UNSUPPORTED_CONTRACT |
| 签名表门 `signature_table` | symbol 在表、逐参映射齐全 | UNSUPPORTED_CONTRACT |

闭键校验作用于 **FACTS 参数键空间**，逐角色白名单精确冻结如下（共同基础键
{name, ctype, role}；白名单外键一律 param_gate 停机，field_path 指到该键——不丢键
降级，fail-open 是本 schema 明令禁止的失效形态）：

| role | 接受的 FACTS 参数键（基础键外） |
| --- | --- |
| handle, dim | （无） |
| enum | values, enum_kind |
| layout | kind, of |
| scalar | dtype, mem, nullable, values |
| vector | dtype, dir, len, inc, nullable |
| matrix | dtype, dir, rows, cols, ld, order, storage, nullable |
| out_scalar | dtype, mem, nullable |

配套规则：enum.values 必须消费进 IR；仅 scalar.values 显式忽略；顶层 constraints
显式忽略（塑形用例）。dtype_from、batch、producer、conditioning、uplo、diag、kl、ku
不在本期接受集；inout_scalar、fixed_vector、int_array 三个 role 在 role 判定处整体
拒绝，不按子键报错。

## 1. 对拍口径与基准修订

生成物以 spike 五件的 rev1 修订形态为逐字节对拍基准（reports/…/overlay-rev1/，
开发件）。rev1 相对 spike 原件的全部修订，Step 3 真机复验后生效：

1. wrapper 收敛单调用点（消 A2′ calls_per_case 歧义）。
2. test.cpp 正常路径 span 表达式统一为规范渲染序 `(n - 1) * incx + 1`（L1 sasum 形：该路径
   incx>0，无需 abs/int64；通用 cblas 路径的 span 是 int64 abs 形，见 §6）。

表达式规范渲染序由发射器统一实现；两处 span 文本同序，AST 单渲染器，无逐点特判。

## 2. 顶层

{version:3, facts_schema_version, op, family, symbol, returns, params, columns,
buffers, movement, golden, verify, status_plan, smoke, upload_guard}（15 键；dispatch
无字段，不设顶层键）

- returns 枚举 {aclblasStatus_t}，非此停机。
- op/family 过正则 `^[a-z][a-z0-9_]*$`，symbol 过 `^[A-Za-z_][A-Za-z0-9_]*$`
  （三者插值进路径与 C++ 标识符，参数门拦路径逃逸）。
- 不进 IR：arch_dir（渲染参数）、哈希与指纹、artifacts 命名（模板机械插值）、
  cmake 形制（恒 auto_discover，见 §13 翻案记录）、gates 留档、cases/perf/
  edge_cases/fill DSL/frame 容差常量。
- 编译入口 compile_contract(snapshot)：消费 H1 的 package.json 快照。

## 3. params[]（有序 = FACTS 序 = 实参序）

每项闭键集：{name, ctype, role, dir, mem, dtype?, enum?, layout?, shape?, order?,
storage?}（IR 形；FACTS 侧键面见 §0 白名单）。role 闭表 8 值：handle、enum、dim、
layout、scalar、vector、matrix、out_scalar。三条不变量：handle 首参、恰一输出
（dir=out 或 inout 的参数恰一个）、引用指向存在。

| 键 | 约束 |
| --- | --- |
| ctype | 闭表 {aclblasHandle_t, int, const float*, float*, aclblasOperation_t} |
| dir | 物化单值：handle/dim/enum/layout/scalar/vector{in} matrix{in,inout} out_scalar{out} |
| mem | 按角色单值：handle/scalar/dim/enum/layout=host；vector/matrix/out_scalar=device |
| dtype | vector/matrix/scalar/out_scalar 必填，闭表 {float32} |
| enum | enum 角色必填：{values, enum_kind} 照 FACTS 原文；cxx 映射归模板 |
| layout | layout 角色必填：{kind∈{inc,ld}, of}；编译器断言 of 与 shape 引用一致 |
| shape | vector:{len_ast}；matrix:{rows_ast, cols_ast, ld_ast} |
| order/storage | matrix 必填单值：{col_major}/{full}；CblasColMajor 由 order 派生 |

mem/dir 对账门：先按 facts-schema v1 默认**归一化**（mem 省略 → device；
vector/matrix 的 dir 省略 → in），再与本表指派值对账，不等 → 停机。推论：MVP 的
scalar 只支持 host，FACTS 必须显式写 mem:"host"，省略（归一为 device）即拒——
不改写 FACTS 语义，只拒绝（F-04）。nullable（省略/false 合法，true 停机）、index、
wide_ctype 不进 IR。

## 4. 表达式 AST

标签数组编码。shape 语境 7 节点（全部样本见证）：

    ["int",k] ["ref",p] ["mul",a,b] ["add",a,b] ["sub",a,b]
    ["max",a,b] ["cond",c,t,f]

cond 语境 = shape 节点 + {"le","lt","eq","or","and"}；另有语境节点 ["str",s]，
仅许作 eq 的一侧（enum 比较）。eq 两侧为 enum 引用与 str 字面量、或 int 语境
（数值等值，sgemm quick-return 见证）。bool 节点只许在
cond 语境；["cond",…] 的第一子式按 cond 语境校验。表外节点（ne/ge/gt/not 等
FS 合法节点在内）→ ast_gate 停机；扩节点 = CONTRACT_VERSION+1 加评审。

## 5. columns[]（列序 = 包 ABI = CSV 表头，断言恒真）

每项：{name, kind, binds, reader}。包 ABI 的 _column_specs kind 词表是
{id, description, enum, dim, scalar, layout, fill, expect, seed}；IR kind 由下表
映射（其余同名）：id→case_name、expect→expect_result、seed→random_seed。
IR kind 闭表 9 值：case_name、description、dim、enum、scalar、layout、fill、
expect_result、random_seed。binds 由 ABI 的 source 派生。
binds 三形的 JSON 字面：{"param":<名>} | {"base":<列名>} | {"seed":true}。
reader 由 kind 单源派生（基座列 reader 字面为 "base"）：

| kind | reader |
| --- | --- |
| dim, layout | int64 |
| fill | fill |
| enum | enum |
| scalar | float |
| expect_result | status（基类读） |
| case_name, description, random_seed | 基类读，reader=base |

require-throw 语义与读列全覆盖不变量照 SKILL.md 与包 ABI。

## 6. buffers[] 与 movement[]

buffers[] 每项：{param, span_ast, dtype, mem, dir}——span_ast 编译期从 shape 派生，
物化一次，发射器只读。movement[] 每项：{param, stage∈{upload, alloc_out, readback}}。

通用 cblas_call 路径的搬运是**设备权威 + 语义保持的降级搬运（方案 A′）**，两侧同一 span
公式同一上限：span_ast 以 int64 渲染（`(dim-1)*|inc|+1`，`|inc|` 用 `max<int64_t>(inc,
-inc)`，含 abs(INT_MIN) 全程不溢出），`ok = 1 ≤ span ≤ 单缓冲上限(1<<27)`，
`elems = ok ? span : 1`。**buffer 尺寸只看 span 合法性、不看 full/非 full**：span 合法且不超限
即按全量 elems 造数/搬运（哪怕是错误行——设备照样要收到真缓冲去裁决），span 非法/退化/超限才
降一元素哨兵——绝不按危险 span（INT_MIN/退化维度/超大）分配。另有全体缓冲元素总量上限
`kMaxTotalElems(1<<28)`，任何行超之即报 harness 基础设施错误（防多个合法中等缓冲叠加 OOM）。
M_NULLPTR 保持传 nullptr（指针语义保真）。wrapper 恒调真设备一次并返回其状态；仅在“全量搬运
（ok）且成功”时回读。test.cpp 按 expectResult 与 quick-return 分探针/全量：**分流只决定是否算
golden 与逐元素比对**（非 full 行只验设备返回的状态码即返回，full 行才快照→调→golden→比对），
不改变 buffer 尺寸规则。详见 [template-contract.md](template-contract.md)。L1 builtin_kernel
路径（sasum）不走此机，逐字节复现 rev1。

## 7. golden

{kind:"cblas", symbol, body, accum, args, ret}

- body ∈ {builtin_kernel, cblas_call}，由签名表给出。builtin_kernel 在签名表里只存
  **计算块与其数据依赖注释**的逐字文本（sasum：double 顺序累加）；函数签名、include
  区与 status checks 由模板与 status_plan 发射（拼接单源规则见 §10）。
- accum：body=builtin_kernel 时必填 {dtype, narrow_to}（sasum：float64→float32）；
  body=cblas_call 时恒 null，发射器不读（原生精度，实测归 Step 4）。
- args：序列即 cblas 实参序；元素 {param, transform∈{pass, cast_int, enum_map,
  deref}} 或 {const:<模板常量名>}（sgemm 首位 CblasColMajor，由 matrix.order 派生
  插入）。params 缺席于 args = 丢弃（handle、经 ret 走的 result）。
- ret：{kind∈{out_param, return_value}, param?}。
- symbol ∉ 签名表 → signature_table 停机。

## 8. verify[]

每项 [token, 参数名]，token ∈ {scalar, full}；唯一 out_scalar / 唯一 inout matrix
两条绑定规则。fn/snapshot 由 token 定（模板）；比较域恒 span；容差恒
MIXED_TOLERANCE（applyMixedTolerance(ACL_FLOAT, golden)），IR 无容差字段；上游
EXACT 特例不复制。演化走版本升级，不预埋字段。

## 9. status_plan / dispatch / smoke

status_plan：{vocab, checks, sync_fail_status}。vocab 用完整 C 枚举拼写
（ACLBLAS_STATUS_*，与 CSV expect_result 同词形）。checks 是**有序**判别式联合，
数组序 = 发射序：

    {kind:"null_check", param, status}
    {kind:"quick_return", cond_ast, status, writes_zero}
    {kind:"cond", cond_ast, status}

发射位点由 kind 定死（路由规则属 schema，不设 per 条目字段）。两路发射不同：
- **L1 路径**（builtin_kernel）：null_check(handle) → golden 与 wrapper；其余 null_check →
  仅 golden；quick_return → golden（返回并写零）、wrapper（跳过分配）、test 三路 dispatch。
- **通用 A′ 路径**（cblas_call）：null_check(handle) → golden 与 wrapper 前置安全检查；其余
  null_check 与所有 cond → **仅 golden**（wrapper 不发射，状态由设备裁决）；quick_return →
  golden 与 test 的 `full` 谓词（wrapper 不早返回；退化维度经哨兵 span 让设备裁决）。
writes_zero 是布尔语义位：out_scalar 置零的快速返回（sasum）为 true，空输出域的提前返回
（sgemm m=0/n=0）为 false。quick-return 单源住 checks，发射取 kind=quick_return 条目（无则无
该路径）。sgemm 的 quick-return 是 or(eq(m,0), eq(n,0))；k=0 留在 normal path。

dispatch 无顶层键：探针/全量二分（A′，§6）与设备权威搬运全是模板常量，`full` 谓词取自
expectResult 与 checks 的 quick-return 条目。

smoke：{null_handle:{expect, args:[…]}} 或 null。args 覆盖全部参数位（含 handle
槽，取 null——它正是被测的空指针）；其余按角色物化：dim→["int",5]、layout→
["int",1]、指针→null、out_scalar→"&local"。sasum 发射，sgemm 置 null（无上游先例，
Step 4 定）。

## 10. 签名表（唯一按名数据面）

**行为源二分（方案乙，v3）**：每个 golden.symbol 的行为（status_checks、smoke、
upload_guard、以及 builtin 的 kernel_block）恰有一个来源——签名表
`scripts/signature_table.json` **或** 状态侧车 `assets/status-plans/<symbol>.json`，
两有两无都 fail-closed。builtin_kernel 必须住签名表（手写 C++ 计算块是 F-06 信任边界）；
cblas_call 的 device 签名/args/ret 从 IR params 派生（不入任一源），其 status_checks/
smoke/upload_guard 住侧车（新 cblas 算子加一个白名单内新文件即可，零改既有——Step 5
sger 零改动的通路）。现有 cblas_sgemm 暂留签名表（不迁移免 bump）；新 cblas 算子走侧车。
侧车声明式、禁 kernel_block 或任何原始 C++；闭键、带 format 版本；文件名==内部 symbol==
FACTS golden.symbol 三者相等。compile 从行为源物化 status_plan/upload_guard，validate_ir
再与同源逐字复核，renderer 只消费已验 IR。

签名表表键 = golden.symbol，现行两条 {cblas_sasum(builtin), cblas_sgemm(grandfathered
cblas)}；每条绑定被测 symbol 与完整有序参数签名。条目二分。
**纯数据**（可随新算子加行）：cblas 原型与 args 变换映射。**行为**（新增或修改一律
CONTRACT_VERSION+1 且过 Codex 评审）：builtin kernel 计算块、status checks、
quick-return 谓词、smoke——builtin kernel 是任意 C++ 文本，归行为不归数据，堵住
Step 5 以「加表行」名义注入新计算（F-06）。

builtin kernel 的拼接规则（F-07，单源定死）：签名表只存**计算块与其数据依赖注释**；
函数签名、include 区、status checks 由通用模板与 status_plan 发射。同一代码区
禁止同时来自逐字块与 status_plan。

## 11. 拒绝载荷与 gate 对照

停机以 UnsupportedContract 异常抛出，载荷 {code, gate, field_path, observed,
allowed}；gate 值域 = §0 四层机器名 {pre_ir, param_gate, ast_gate, signature_table}。
多处违规报**首个命中**（按层序、层内按文档序，确定性）；field_path 记法为点号加
[i] 下标（如 params[2].mem），AST 违例定位到承载字段为止、不深入节点内。
拒绝矩阵各轴落点：

| 轴 | gate | 负例 |
| --- | --- | --- |
| schema v2 / ABI 未知 | pre-IR 门 1 | （loader 测试已覆盖） |
| golden.kind 非 cblas | pre-IR 预筛 | N1 |
| returns 非状态协议 | param_gate | N2 |
| AST 表外节点 | ast_gate | N3 |
| ctype/dtype 表外、复数 | param_gate | N4（complex64） |
| 白名单外参数键（batch 等） | param_gate | N5（batch） |
| mem/dir 观测≠指派 | param_gate | N6（mem=device 标量） |
| symbol 表外 | signature_table | N7 |

## 12. 模板契约与常量

发射器的全部非 IR 知识冻结在 [template-contract.md](template-contract.md)：五件的
文件骨架、param.h 成员/初值/require 骨架、wrapper 搬运状态机（L1 五步 / 通用 A′ 设备权威+
哨兵搬运，含 sgemm k>0 上传守卫）、test.cpp（L1 三路 / 通用 A′ 探针·全量 + int ABI 域门）与
造数入口（vector→makeBlasArray、matrix→makeBlasMatrix、seed offset 规则）、full verify 的
调用前快照、类名派生、CMake 脚手架。速查常量：
L1 sasum span `(n - 1) * incx + 1`，通用路径 int64 abs span 与探针/全量降级搬运（§6，A′）；
搬运上限单缓冲 1<<27、总量 1<<28 元素；占位初值表（dim→0、layout→1、fill→parseFill("RANDOM_10")）；`_cpu`/
`_npu` 后缀；smoke 字面量 n=5、incx=1。

## 11a. upload_guard（v3）

搬运守卫从行为源物化进 IR 顶层 `upload_guard`（sger 等无守卫算子为 null）。结构：
`{kind:"k_positive", dim:<dim 参数名>, guarded_params:[<matrix 参数名>…], _src?}`——
被守卫的 matrix 参数仅在 `dim > 0` 时上卡。validator 校验 dim 是 dim 角色参数、
guarded_params ⊆ upload movement。renderer 从 IR 读 dim，不硬搜名字。

## 12a. 跨段完整性不变量（编译期机械断言）

- buffers[].{dtype,mem,dir} 必须与同名 param 逐项相等（重复字段以相等断言闭合）。
- buffers 恰覆盖全部 mem=device 的数据参数（vector/matrix/out_scalar），不多不少。
- movement 与 dir 对应：in→[upload]、inout→[upload, readback]、
  out→[alloc_out, readback]，逐参恰一组。
- status_plan.checks 的 status 与 smoke.expect ∈ vocab；vocab 为公开头 12 值全集。
- golden.ret.kind=out_param 时 param 必须是恰一输出；verify 绑定参数必须是输出。
- AST 节点 arity 与类型按 §4 定义逐节点校验；smoke.args 长度 == params 长度。

## 13. 翻案与演化记录

- CMake 形制：方案 §2 原定显式目标形（gemm 写法）；spike 实证整目录替换下一行式
  auto_discover 可用且更简，v1 取 spike 形——此为对方案 §2 的翻案，已记开发记录。
- 扩 primitive（新 role 属性、AST 节点、verify token、golden body、签名表行为条目）
  一律 CONTRACT_VERSION+1 并过评审；禁 op 名特判。
- v3（2026-09-10，Codex review-plan 采纳方案乙）：加 upload_guard 顶层字段（搬运守卫，
  从行为源物化）；§7 行为源二分（签名表 XOR 状态侧车），cblas_call 脱表自足。
- 通用 cblas 后端 A′（2026-09-11，Codex A/B 审议采纳；非 schema 变更，CONTRACT_VERSION 仍 3）：
  wrapper/test 从旧「wrapper 发射 quick-return + test 三路降级缓冲」改为**设备权威 + 语义保持
  降级搬运**——span 非法/退化/超限的行降一元素哨兵（合法行仍全量）、int64 checked span、
  int ABI 域门、单/总缓冲量上限、恒一设备调用点、
  状态全由设备裁决（详见 §6 与 template-contract.md §3b/4b）。IR 字段不变，仅发射器语义演进。
  侧车加可选 matrix_phys（转置换位表），使转置型 cblas 算子仍可只加数据接入。
  新增一个 cblas symbol 的状态侧车 = 既有声明语言的目录扩展，过评审但不 bump 版本；
  改/删既有侧车、扩 AST/kind/侧车 schema 才 bump。

## 14. 实例与负例

正例 tests/ir-instances/{sasum.json, sgemm.json, sger.json}。sasum 逐字段映射 rev1 五件
（builtin_kernel/L1）。sger 是 cblas_call 通用路径的端到端可核正例：
`compile_contract(sger-facts-draft, sger-column-specs-draft) == sger.json` 已由
test_renderer.TestSgerVerticalSlice 断言，并用真实多行 CSV（sger-sample.csv，含负步长/
INT_MIN/quick-return/null 行）渲染核验（FACTS→IR→validate→render 全链）。sgemm 仍是
**推导样例**（hypothetical projection）：核心 API 事实已对上游逐条核实，但仓内尚无 sgemm
任务包，且真机受阻于 asc-devkit 版本——`compile_contract(fixture) == sgemm.json` 待任务包
与可用机落地，在此之前不称其为端到端真机可核正例。
负例 tests/ir-instances/negative-1..7.json 配 expected.json：每枚从可编译基线
单改一轴（F-02），由 ir_validator 实际执行并逐字匹配五元组。

**已知覆盖限制（诚实记账）。** 侧车若含标量指针的 null_check（如 sger 的 `alpha` 空检查），
当前 test.cpp 无法驱动它：标量实参恒渲成 `&p.<name>`（非空），CSV 无表达标量指针为
nullptr 的列。故设备对标量空指针的处理不被本 harness 覆盖；该 check 仍进 golden 参考与侧车
审查口径，但不生成对应设备测试行。要覆盖需给标量加 fill 型「NULLPTR」列（后续增量）。
