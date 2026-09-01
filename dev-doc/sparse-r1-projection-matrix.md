# M0.5 投影矩阵盘点（机器生成清单，钉板蓝图）

> 来源：只读探查 agent 对模板 gen_csv.py 与 package.py 的全量分支盘点，行号基于 feature/sparse-r1 @ bbe29f4。
> 本文件是数据附录，不受 prose-style 行长约束；六组合成 FACTS 清单是 §2.2 fixture 的构建依据。

Both files read in full. Below is the matrix.

Paths (absolute, referenced afterwards as `T:` and `P:`):
- `T` = `/Users/ll/Desktop/workspace-ascend/OpRunway/.claude/worktrees/oprunway-sparse-r1/plugin/skill/repo-task-blas-case-gen/assets/template/gen_csv.py`
- `P` = `/Users/ll/Desktop/workspace-ascend/OpRunway/.claude/worktrees/oprunway-sparse-r1/plugin/skill/repo-task-blas-case-gen/scripts/package.py`
- Examples: `.../assets/example/cherk/gen_csv.py`, `.../assets/example/sasum/gen_csv.py`

Five consumption surfaces, canonical entry points:
1. 表头 `T:470-506` (副本 `P:1221-1257`)
2. 行物化/body `T:344-421` (`_materialize`) + `T:509-547` (`_body_mapping`) + `T:792-803` (`_assemble_rows`)
3. 轴 `T:145-228` (`build_axes`) + helpers `T:119-133`, `T:136-142`
4. edge_cases 键校验 `P:927-991` (+ `P:891-899` `_control_columns`, `P:902-924` `_fixed_vector_element`)
5. perf.key 校验 `P:994-1026` (+ rows `P:1027-1058`)

---

## A. role 矩阵

| role | 表头 (1) | body/物化 (2) | 轴 (3) | edge set (4) | perf.key (5) | 最小 FACTS 片段 |
|---|---|---|---|---|---|---|
| `handle` | `T:480` 跳过，永不出列 | 完全不参与 | 无轴 | 名字不在 `direct`(`P:932`)；`ROLE_KEYS["handle"]=set()` (`P:118`) 使 `nullable` 非法 → **无法造 nullHandle 列** | `P:1023` 拒（role 不在 enum/dim/layout） | `{"name":"handle","ctype":"aclblasHandle_t","role":"handle"}`（`P:1170-1179` 强制为 params[0]） |
| `enum` (enum_kind=op/algo) | `T:482-483` 出 1 列 `name` | `T:356-357` state=selection；`T:519-520` 写列 | `T:168` 轴 kind=`op_enum`，值=`values`；`T:573` L0 全组合 | `P:962` 值必须 ∈ `values` | `P:1023` 允许；行值再查 `values` (`P:1053`) | `{"name":"trans","ctype":"aclblasOperation_t","role":"enum","values":["N","T"]}` |
| `enum` (enum_kind=dtype/compute) | 仍出 1 列（表头不看 enum_kind） | **不由 selection 赋值**；值来自 `profile["assign"]` (`T:350`) | `T:157-167` 不建自身轴，改插一条名为 `profile` 的轴（仅首个、且 `dtype_profiles` 非空时） | 同上（可被 set 覆盖，`T:383-385`） | `P:1025-1026` 显式拒，必须改用虚拟键 `"profile"` (`P:1018-1021`) | `{"name":"dt","ctype":"aclDataType","role":"enum","enum_kind":"dtype","values":["FP16","FP32"]}` + 顶层 `dtype_profiles` + 某参数 `dtype_from:"dt"` |
| `dim` | `T:482-483` 出 1 列 | `T:358-359`；`T:519-520` | `T:169-182` 三分支择 tier 表（见 B 表 rows/cols/len/batch.count 行） | `P:964` 必须 int | `P:1023` 允许；`P:1055` 行值必须 int | `{"name":"n","ctype":"int","role":"dim"}` |
| `layout` | `T:482-483` 出 1 列 | 两趟：非 stride `T:387-398`，stride `T:400-409` | `T:183-191` 按 `kind` 取 LD/STRIDE/inc/batch tiers，轴 kind = 该 `kind` 字符串 | `P:964` 必须 int，且**被物化诚实采纳**（`T:392-393`/`T:404-405` 的 `name in overrides` 短路） | `P:1023` 允许 int，但 **ld/stride 的值会被 `T:396-397`/`T:409` 丢弃重算**（非 `"min"` 一律 rows+5 / minimum+7）→ 声明与实际不符的陷阱 | `{"name":"lda","ctype":"int","role":"layout","kind":"ld","of":"A"}` |
| `scalar` / `inout_scalar` | `T:484-490` 三分支：`dtype` 复数 → `x_re`/`x_im`；`dtype_from` 且任一 profile `scalar_dtype` 复数 → `x_re`/`x_im`；否则 1 列 | `T:363-372`（含 tier 索引反解）+ `T:521-530`（复数拆分；实 profile 补 `(v,0.0)`） | `T:192-194` → `T:136-142` 三分支（values / dtype / dtype_from-tier） | `P:966-969` 用 `param.get("dtype")` 判形，**dtype_from 标量只接受裸数字** | `P:1023` 拒（scalar 不在 enum/dim/layout） | `{"name":"alpha","ctype":"const float*","role":"scalar","dtype":"float32","mem":"device"}` |
| `out_scalar` | `T:480` 跳过 | 不参与 | 无轴（`build_axes` 无该分支） | 名字不在 `direct`；但 `nullable:true` 时 `P:895-896` 仍生成 `null<Name>` 合法键 | `P:1023` 拒 | `{"name":"result","ctype":"float*","role":"out_scalar","dtype":"float32","mem":"device"}` |
| `vector` | `T:491-493` `dir∈{in,inout}` 且无 `producer` → 1 列 `<name.lower()>_fill` | `T:373-380` 写 `<lower>_fill`；`T:531-535` | `T:195-214` 同条件 → 轴 `<lower>_fill`，kind=`fill`，值=`cases.fill_tiers` | 名字不在 `direct` → 直接 set 该参数名报「未知键」`P:991` | `P:1023` 拒 | `{"name":"x","ctype":"const float*","role":"vector","dtype":"float32","dir":"in","len":"n","inc":"incx"}` |
| `fixed_vector` | `T:496-497` `dir∈{in,inout}` → 展开 `len` 个列 `<name>0..<name>{len-1}`（用 **`len`**，不是 `len(samples)`） | `T:381-382` state=样本副本；`T:536-538` 逐元素写列；`T:414-419` 支持 `<name><i>` 覆写 | `T:215-222` 轴名=参数名，值=`range(len(samples))`，kind=`fixed_vector`；描述渲染成 `tierN` (`T:554-555`) | `P:981-990` `<name><i>` 元素键：越界、复数 dtype、非数值三种报错；最长前缀匹配 `P:923` | `P:1023` 拒 | `{"name":"pv","ctype":"float*","role":"fixed_vector","dtype":"float32","dir":"in","len":2,"samples":[[1.0,2.0],[0.0,-1.0]]}` |
| `matrix` | `T:491-495` 同 vector，另在 `conditioning` 真值时**追加** `<name>_matrix_type`（注意大小写：fill 列小写、matrix_type 列原名） | `T:373-379`：有 conditioning 时 fill 被钉死为 `fill_tiers[0]`，只有 matrix_type 随轴动；`T:531-535` | `T:199-214` 二选一：有 conditioning → 轴 `<name>_matrix_type` kind=`matrix_type`；否则 fill 轴 | 同 vector：不可直接 set | `P:1023` 拒 | `{"name":"A","ctype":"const float*","role":"matrix","dtype":"float32","dir":"in","rows":"m","cols":"n","ld":"lda"}` |
| `int_array` | `T:480` 跳过（与 handle/out_scalar 同列） | 不参与 body；但 `T:445-458` 计入 footprint，dtype 走 `param.get("dtype","int32")` (`T:449`) | 无轴；但其 `len` 表达式里的名字被塞进 **vector_names** (`T:128-129`)，从而把某个 dim 定成 `vec_dim` 档 | 名字不在 `direct`；`nullable` 仍给出 null 列 | `P:1023` 拒 | `{"name":"ipiv","ctype":"int*","role":"int_array","dir":"out","len":"n"}`（dir=in/inout 时 `P:638` 强制 `producer`） |

---

## B. 参数属性矩阵（以代码实际分支为准）

| 属性条件 | 影响面与位置 | 分支行为一句话 | 最小片段 |
|---|---|---|---|
| `enum_kind` 缺省/`"op"`/`"algo"` | 3 `T:156-168`；2 `T:356` | 建 `op_enum` 轴且 state 由 selection 驱动 | `"role":"enum","values":[...]`（不写 enum_kind） |
| `enum_kind:"dtype"` 或 `"compute"` | 3 `T:157-167`；2 `T:350`；5 `P:1025` | 取消自身轴、插 `profile` 轴、列值改由 profile.assign 提供 | 见 A 表 dtype enum 行；`P:554`/`P:557-566` 另绑 ctype |
| `values`（enum） | 3 `T:168`；4 `P:962`；5 `P:1053`；轴唯一性 `T:224-227`（`P:531` 已保证 unique） | 直接成为轴取值域与两处白名单 | `"values":["UPPER","LOWER"]` |
| `layout.kind:"ld"` | 3 `T:186`（`["min","pad"]`）；2 `T:395-397`（min→`max(1,rows(of[0]))`，pad→rows+5）；2 footprint `T:430` | 轴是字符串档位，物化时解析成整数 | `"role":"layout","kind":"ld","of":"A"` |
| `layout.kind:"stride"` | 3 `T:187`；2 **第二趟** `T:400-409`（min→buffer 基元素数，pad→+7） | 依赖已算好的 ld/len，所以单开一趟 | `"kind":"stride","of":"A"` + 目标 `batch.model:"strided"` |
| `layout.kind:"inc"` | 3 `T:188`（`cases.inc_tiers`）；2 `T:398`（整数直用）；2 footprint `T:435-437` | 轴值即整数，body 原样落列 | `"kind":"inc","of":"x"` |
| `layout.kind:"batch"` | 3 `T:189`（`batch_tiers`，轴 kind=`batch`）；L0/ED/PF 钉 2 (`T:591`,`T:727`,`T:751`)；`P:570` 唯一允许省略 `of` 的 kind | 批量数轴 | `"kind":"batch"`（可无 `of`） |
| `layout.of` 是**列表** | 2 `T:332-335` `_first_target` | ld/stride 只用 `of[0]` 算 min/pad，其余目标被忽略 | `"of":["A","B"]` |
| `dtype` ∈ complex64/128（标量） | 1 `T:485-486`；2 `T:523-528`；3 `T:140`（COMPLEX_SCALAR_TIERS）；4 `P:966`（要求 `[re,im]` 两元列表） | 一切复数分支的第一触发器 | `"role":"scalar","dtype":"complex64"` |
| `dtype` ∈ complex（buffer） | 2 footprint `T:449,458` + `DTYPE_BYTES` `T:63-76` | 只改 footprint 字节数，不改列 | `"role":"matrix","dtype":"complex128",...` |
| `dtype_from`（标量） | 1 `T:487-488`（**任一** profile 复数就出 re/im）；2 `T:364-371` + `T:524-527`（实 profile 补 `0.0` 虚部）；3 `T:142`（轴变成 tier **索引** `[0,1,2,3]`，kind=`scalar_tier`）；4 `P:966` 只认裸数字 | profile 驱动的标量：轴是索引、列可能翻倍 | `"role":"scalar","dtype_from":"dt"` + `dtype_profiles[*].scalar_dtype` |
| `dtype_from`（buffer） | 2 `T:329` → `_dtype_token` `T:311-321`（只认 FP16/FP32/BF16/INT8） | 只改 footprint dtype | `"role":"matrix","dtype_from":"dt",...` |
| `dtype_from` + `values` 同时给（标量） | 2 `T:364` 条件为假 → **跳过 tier 反解**，值直接用；3 `T:137-138` kind=`scalar_value`；`P:848-863` 逐 profile 校验形状 | 混合实/复 profile 下必然校验失败，只能同质 | `"dtype_from":"dt","values":[[1.0,0.0]]` |
| `values`（标量，配 `dtype`） | 3 `T:137-138`；轴唯一性 `T:224-227`（**`P:424-436` 不查重复** → 重复值 → GeneratorError） | 轴取值域被显式接管 | `"dtype":"float32","values":[1.0,0.0,-1.5]` |
| `mem` | 无（仅 `P:580-584` 词表校验） | 不参与任何投影 | `"mem":"host"` |
| `nullable: true` | 1 `T:499-503`（`expect_result` 之后追加 `null<Name>`）；2 `T:540-545`（恒填 0）；4 `P:895-896`+`P:971-973`（值 ∈ {0,1,False,True}） | 唯一的空指针控制列来源；对 **任意 role**（含 out_scalar/int_array）生效，只受 `ROLE_KEYS` 限制 | 任一非 handle 参数加 `"nullable":true` |
| `nullable: false`/缺省 | 同上（假分支） | 不出列 | — |
| `dir:"out"`（vector/matrix） | 1 `T:492`；2 `T:375`；3 `T:196-197` | 三面同时静默：无 fill 列、无 fill 轴、state 无 `_fill` 键 | `"role":"matrix","dir":"out",...` |
| `dir` 缺省（=`"in"`, `T:479`) / `"inout"` | 同上（真分支） | 出 fill 列并建 fill 轴 | 省略 `dir` 即可 |
| `dir:"out"`（fixed_vector） | 1 `T:496`；3 `T:215`；4 `P:909`（元素键不再合法）；`P:440-443` 禁 `samples` | 完全退出投影 | `"role":"fixed_vector","dir":"out","len":2` |
| `producer` 存在 | 1 `T:492`；2 `T:375`；3 `T:197` | 与 `dir:"out"` 等效地抑制 fill 列/轴（数据由 producer 产） | `"producer":"lapacke_sgetrf(A)"` |
| `conditioning`（非空 list） | 1 `T:494-495`（**追加**第二列）；2 `T:376-378`（fill 钉 `fill_tiers[0]`）；3 `T:199-206`（fill 轴换成 matrix_type 轴）；轴唯一性 `T:224-227`（`P:629` 未查重 → 重复即崩） | 矩阵条件数模式取代填充模式作为轴 | `"role":"matrix","conditioning":["WELL_COND","ILL_COND"],...` |
| `conditioning: []` | 空列表 falsy → 走 else 分支 | 等同未给（`P:629` 允许空列表） | `"conditioning":[]` |
| `batch`（dict 存在） | 1 `T:504-505`（追加 `<name>_batch_pattern`）；2 `T:544-545`（恒 `UNIFORM`）+ footprint `T:452-457`；3 `T:130-132`（`batch.count` 名进 batch_names → 该 dim 改用 `batch_tiers`，kind=`batch_dim`）；4 `P:897-898`+`P:975-979`（值 ∈ `BATCH_PATTERNS` 或 `NULL_ELEMENT_\d+`） | 唯一能改「dim 用哪张 tier 表」且能追加控制列的属性 | `"batch":{"model":"contiguous_implicit","count":"batchCount"}` |
| `batch.model:"strided"` | 2 footprint `T:454-455`（`+= (count-1)*stride`，与其它 model 的 `*= count` 不同）；`P:501` 强制 `stride`；`P:723-726` 反向引用 | 步进批与指针表批的容量公式分岔 | `"batch":{"model":"strided","count":"bc","stride":"sa"}` |
| `batch.model:"ptr_array"` | 2 `T:456-457`；`P:505` 才允许 `table_mem` | 元素数直接乘批数 | `"batch":{"model":"ptr_array","count":"bc","table_mem":"device"}` |
| `batch.count` 指向 `layout kind=batch` | 3 `T:130-132`（名进 batch_names，但该参数 role 是 layout → 走 `T:183-191`，轴 kind=`batch`）；`P:711-722` | 批数既可以是 dim 也可以是 layout，两条路径轴值同为 `batch_tiers` | `"count":"bc"` + `{"name":"bc","role":"layout","kind":"batch","of":"A"}` |
| `rows`/`cols`（matrix） | 3 `T:124-127` → 名字进 matrix_names → 该 dim 用 `dim_tiers`（kind=`mat_dim`）；2 `T:396`,`T:428-430`；`P:776-779` 表达式白名单 | 决定 dim 走矩阵档还是向量档（**matrix 优先于 vector**，`T:171-177`） | `"rows":"m","cols":"n"` |
| `len`（vector / int_array，表达式） | 3 `T:128-129` → 名字进 vector_names → dim 用 `vec_dim_tiers`（kind=`vec_dim`，含 100003/1050001） | 归约类算子的大尺寸档只有这条路能触发 | `"role":"vector","len":"n",...` |
| `len`（fixed_vector，正整数） | 1 `T:497`（列数）；4 `P:911-912,984`（越界判定） | 列宽与 edge 元素键上界 | `"len":4` |
| `inc` = layout 名 vs 整数字面量 1 | 2 `T:435-437`（`state[inc]` vs 直用）；3 无（轴由那个 layout 自己产）；`P:594` 只许这两种 | 字面量 1 时该 vector 不带 inc 轴 | `"inc":1` |
| `ld` + `storage:"packed"` | 2 `T:427-429`（`rows*(rows+1)//2`，不用 ld）；`P:614-617`（packed 禁 ld，非 packed 必须 ld） | packed 矩阵没有 ld 参数 → 少一根轴、少一列 | `"storage":"packed"`（并删掉 `ld`） |
| `storage` 其它值（`hermitian`/`triangular`/`banded`/…） | 无投影影响；仅 `P:620-627` 触发 `uplo`/`diag`/`kl`/`ku` 必填 | 只增加交叉引用要求 | `"storage":"triangular","uplo":"uplo","diag":"diag"` |
| `uplo`/`diag`/`kl`/`ku` | 无（仅 `P:727-737` 指向 enum/dim 校验） | 不进表头、不进轴 | `"uplo":"uplo"` |
| `order` | 无（仅 `P:618` 词表） | 不参与投影 | `"order":"row_major"` |
| `ctype`（含复数 ctype 如 `aclblasComplex*`） | **不参与任何投影**；只在 `P:536-566`（enum 值词表、dtype/compute 绑定）与 README 签名 `P:1369-1382` 起作用 | 复数与否由 `dtype`/`scalar_dtype` 决定，与 ctype 无关 | `"ctype":"const aclblasComplex*"` |
| `samples`（fixed_vector） | 3 `T:220`（轴值 = `range(len(samples))`）；2 `T:382`；`P:449-459`（每条长度必须 == `len`） | 样本条数决定轴基数；样本内容不参与轴去重 | `"samples":[[1,2],[3,4]]` |
| `name` 大小写 | 1 `T:493`（`name.lower()+"_fill"`）vs `T:494`（原名 `_matrix_type`）vs `T:502`（`null` + 首字母大写） | 同名不同 case 的两个参数会产出**同名重复列**，无处校验 | 两个参数 `"a"` 与 `"A"` 同时 `dir:"in"` |

### 顶层键（同样改变投影/轴/校验，非 params 属性但必须进 fixture）

| 键 | 面 | 行为 |
|---|---|---|
| `dtype_profiles` 存在 | 1 `T:471-474`（复数判定源）；2 `T:348-351,368-370,526`；3 `T:158-166`（插 `profile` 轴）；5 `P:1012-1021`（放行虚拟键 `profile`） | 唯一能在 params 之外插入一根轴的顶层键 |
| `dtype_profiles[*].scalar_dtype` 复数 | 1 `T:472-474`+`T:487-488`；2 `T:526-527` | 让所有 `dtype_from` 标量列一分为二 |
| `dtype_profiles[*].assign` | 2 `T:350`（dtype/compute enum 的列值全部来自这里） | 未被 assign 覆盖的 dtype enum → body `KeyError` |
| `cases.dim_tiers` / `vec_dim_tiers` / `batch_tiers` / `inc_tiers` / `fill_tiers` / `max_footprint_bytes` | 3 `T:95-106,182,188,191,211`；2 `T:377,466-467` | 替换默认档位表；`fill_tiers` 另受 `P:1103-1111` 的 `METHOD_PATTERN_VAL` 词法校验 |
| `constraints` | 2 `T:462-467`（行有效性 → L0/PW 丢行、PF 直接报错 `T:760-764`） | 决定哪些物化行活下来 |
| `perf.sweep: true` | 2/3 `T:769-779`：只把 **mat_dim** 轴推到 `PF_SWEEP_SIZES`（**vec_dim 轴不动**） | 纯向量算子开 sweep 得到 7 条重复行 |
| `perf.key` 含 `"profile"` | 5 `P:1018-1021`；2 `T:758` 直接进 partial，命中 `profile` 轴名 | 类型轴的唯一合法性能键写法 |
| `perf.threshold` / `perf.meta` | 无投影影响（`P:1059-1073` 校验 + 渲染） | — |
| `edge_cases[*].set` / `expect` | 2 `T:721-740`（partial 钉 dim=16/batch=2/ld,stride=min，再叠 overrides） | ED 块的全部行为 |

---

## C. 两份 `_header_columns` 的差异与生成/校验口径分叉（fixture 必须能钉住的行为）

| 项 | 位置 | 说明 |
|---|---|---|
| `profile["scalar_dtype"]` vs `profile.get("scalar_dtype")` | `T:473` / `P:1224` | 合法 FACTS 下等价（`P:810-811` 保证有 profile 就有 scalar_dtype）；非法 FACTS 下模板抛 `KeyError` 而 package 静默 |
| `role in {"scalar","inout_scalar"}` vs `role in SCALAR_ROLES` | `T:484` / `P:1234` | 等价：`out_scalar` 已在 `T:480`/`P:1230` 提前 `continue` |
| 其余逐字相同 | — | `P:1773` 每次生成都断言两份结果相等，任何 role 新分支必须两处同改 |
| **陷阱 1**：`enum_kind:"dtype"` 但无人 `dtype_from` | `P:798-802` 放行（禁止 `dtype_profiles`）→ `T:157-160` 不插轴也 `continue` → `T:520` `state[name]` **KeyError** | 校验通过、生成崩 |
| **陷阱 2**：`perf.key` 含 `kind=ld/stride` 的 layout | `P:1055` 要求整数 → `T:396-397`/`T:409` 无条件重算 | 声明值被静默丢弃 |
| **陷阱 3**：`dtype_from` 标量 + 复数 profile + edge_cases 里 set 该标量 | `P:966`（`param.get("dtype")` 为 None）只收裸数字 → `T:528` 对 float 做二元解包 | 校验通过、生成 `TypeError` |
| **陷阱 4**：`conditioning` / 标量 `values` / `cases.*_tiers` 出现重复值 | `P:629`/`P:424-436`/`P:1085-1111` 均不查重 → `T:224-227` 抛 `GeneratorError` | 校验通过、生成失败 |
| **陷阱 5**：`x_fill`、`profile` 作为 edge_cases set 键 | `P:991` 判「未知键」，但 `T:410-412` 其实会接受（`key in state`） | 校验比生成器严 |
| **陷阱 6**：`_scalar_is_complex`（`P:1398-1404`）是死代码 | 与 `P:1237` 的判定重复但无人调用 | 复数标量判定实际只有 `_header_columns`/`_body_mapping` 两处 |

---

## D. 条件的完整枚举 + 两例覆盖情况

「会改变投影/轴/校验行为的属性名全集」共 27 个 params 属性条件 + 11 个 role + 12 个顶层条件。✓=已覆盖，✗=两例都没有。

### role（11）

| role | cherk | sasum | 缺口 |
|---|---|---|---|
| handle | ✓ | ✓ | — |
| enum | ✓ (uplo, trans) | ✗ | — |
| dim | ✓ (n,k) | ✓ (n) | — |
| layout | ✓ (ld×2) | ✓ (inc) | — |
| scalar | ✓ (alpha,beta) | ✗ | — |
| inout_scalar | ✗ | ✗ | **✗ 必补** |
| out_scalar | ✗ | ✓ (result) | — |
| vector | ✗ | ✓ (x) | — |
| fixed_vector | ✗ | ✗ | **✗ 必补** |
| matrix | ✓ (A,C) | ✗ | — |
| int_array | ✗ | ✗ | **✗ 必补** |

### 属性条件（27）

| 条件 | cherk | sasum | 缺口 |
|---|---|---|---|
| `enum_kind` op/缺省 | ✓ | ✗ | — |
| `enum_kind` dtype | ✗ | ✗ | **✗** |
| `enum_kind` compute | ✗ | ✗ | **✗** |
| `enum_kind` algo | ✗ | ✗ | **✗** |
| `layout.kind:"ld"` | ✓ | ✗ | — |
| `layout.kind:"inc"` | ✗ | ✓ | — |
| `layout.kind:"stride"` | ✗ | ✗ | **✗** |
| `layout.kind:"batch"` | ✗ | ✗ | **✗** |
| `layout.of` 为列表 | ✗ | ✗ | **✗** |
| 标量 `dtype` 复数 | ✗ | ✗ | **✗**（cherk 是复数 **buffer**+实标量，没有复标量列） |
| buffer `dtype` 复数 | ✓ (A,C complex64) | ✗ | — |
| `dtype_from`（标量） | ✗ | ✗ | **✗** |
| `dtype_from`（buffer） | ✗ | ✗ | **✗** |
| `dtype_from`+`values` 并存 | ✗ | ✗ | **✗** |
| 标量 `values` | ✗ | ✗ | **✗** |
| `mem` | ✓ | ✓ | —（无投影影响） |
| `nullable:true` | ✓ (alpha,beta,A,C) | ✗ | — |
| `dir:"out"`（buffer） | ✗ | ✗ | **✗** |
| `dir:"inout"` | ✓ (C) | ✗ | — |
| `producer` | ✗ | ✗ | **✗** |
| `conditioning` | ✗ | ✗ | **✗** |
| `batch`（任一 model） | ✗ | ✗ | **✗**（三个 model 全缺） |
| matrix `rows`/`cols` 表达式（含 IfExp） | ✓ | ✗ | — |
| vector/int_array `len` 表达式 | ✗ | ✓ (`"n"`) | — |
| `inc` 字面量 1 | ✗ | ✗ | **✗**（sasum 用 layout 名） |
| `storage:"packed"` | ✗ | ✗ | **✗** |
| `storage` 其它非 full（hermitian） | ✓ (C) | ✗ | —（packed 以外都无投影影响） |
| `samples`（fixed_vector） | ✗ | ✗ | **✗** |
| 同名不同 case 的列冲突 | ✗ | ✗ | **✗**（`nullA`/`a_fill` 碰撞） |

### 顶层条件（12）

| 条件 | cherk | sasum | 缺口 |
|---|---|---|---|
| `dtype_profiles`（含 `assign`/`scalar_dtype`/`golden_dtype`/`precision_row`） | ✗ | ✗ | **✗** |
| `dtype_profiles` 含复数 `scalar_dtype` | ✗ | ✗ | **✗** |
| `constraints` | ✓ | ✓ | — |
| `cases.dim_tiers` | ✗ | ✗ | **✗** |
| `cases.vec_dim_tiers` | ✗ | ✓ | — |
| `cases.batch_tiers` | ✗ | ✗ | **✗** |
| `cases.inc_tiers` | ✗ | ✓ | — |
| `cases.fill_tiers` | ✗ | ✓ | — |
| `cases.max_footprint_bytes` | ✗ | ✗ | **✗** |
| `edge_cases`（非空） | ✓（`dim` set + `null*` set 两种键） | ✗（空列表） | 仍缺 **enum 键 / layout 键 / scalar 键 / `_batch_pattern` 键 / fixed_vector 元素键** 五类 |
| `perf.key`（enum+dim 组合 / 单 dim） | ✓ (n,k,uplo,trans) | ✓ (n) | 缺 **`"profile"` 虚拟键 / layout 键** |
| `perf.sweep:true` / `perf.threshold` / `perf.meta` | sweep=False，无 threshold/meta | sweep=False，有 meta | 缺 **`sweep:true`**、**`threshold`** |

### 合成用例清单（两例都没覆盖，fixture 必补）

按「一条合成 FACTS 能同时钉住多少缺口」归并成 6 组：

1. **profile 组**：`enum_kind:"dtype"` + `enum_kind:"compute"` + `dtype_profiles`（含一个 `scalar_dtype` 复数、一个实数）+ 标量 `dtype_from`（无 values）+ buffer `dtype_from` + `perf.key:["profile", ...]`。钉住：`profile` 轴插入位置、`scalar_tier` 轴、`x_re/x_im` 列的 dtype_from 路径、`(v,0.0)` 补虚部、`P:1025` 拒类型参数。
2. **batch 组**：`batch.model` 三值各一（`strided` 配 `layout kind=stride`、`ptr_array` 配 `table_mem`、`contiguous_implicit`）+ `layout kind=batch`（一条带 `of`、一条不带）+ `cases.batch_tiers` + edge_cases 的 `<name>_batch_pattern`（`UNIFORM` 与 `NULL_ELEMENT_3` 各一）。钉住：`batch_dim` vs `batch` 轴 kind、两套 footprint 公式、stride 第二趟物化。
3. **fixed_vector / int_array 组**：`fixed_vector` `dir:"in"`（`len` 与 `len(samples)` 故意不等长的合法组合：len=3、samples 2 条）+ `dir:"out"`（无 samples）+ `int_array` `dir:"out"` 与 `dir:"inout"`+`producer` + edge_cases 的 `<name><i>` 元素键（含越界一条作为负例）。
4. **conditioning / packed / producer 组**：matrix `conditioning:[a,b]`（验证 fill 列被钉死为 `fill_tiers[0]` 而 matrix_type 成轴）+ `storage:"packed"`（无 ld）+ 带 `producer` 的 matrix（验证 fill 列/轴同时消失）+ `dir:"out"` 的 vector + `layout.of` 写成列表。
5. **标量取值组**：`inout_scalar` + 标量 `dtype:"complex64"`（复标量列的 dtype 路径）+ 标量 `values`（显式轴）+ `inc:1` 字面量 + edge_cases 的 `enum`/`layout`/`scalar` 三类 set 键。
6. **负例组（校验通过但生成崩 / 口径分叉）**：陷阱 1~6 各一条最小 FACTS——`enum_kind:"dtype"` 无消费者、`perf.key` 含 ld、复数 profile 下 set dtype_from 标量、`conditioning` 重复值、edge set 写 `x_fill`、参数名 `a` 与 `A` 并存。这一组决定 fixture 是「记录现状」还是「暴露 bug」，建议单独标注期望值。
