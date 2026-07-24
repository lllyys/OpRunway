# U7 泛化设计 —— 把用例生成/runner 从 elementwise 扩到所有算子形态

> **产出方式**：2026-07-23 只读设计 fanout（10 agent，Survey 5 路实读代码 → Design 4 类 → Synthesize；
> workflow run `wf_a624fa02-d58`，~1.23M token）。**全程只读、零改代码。**
> **地基**：`doc/oprunway-op-shape-taxonomy.md`（44 算子形态清点，2026-07-22 快照）。
> **本 doc 是 rule #1 的「先抛方案」产物** —— 待用户拍板 U7d 分期后才落地实施。

---

## ⚠ 最高律令（贯穿全文，用户 2026-07-23 再次强硬重申）

**绝不针对某个具体算子做优化 / 特判。所有设计必须泛化。**（承 CLAUDE.md 硬约束 ③）

- U7c runner 是**一份通用模板**，不给任何算子单独写一份、代码里**不得出现 `if op == "<名>"` 分支**。
- 算子专属位（aclnn 入口符号名 / 头文件 / `-l` 库名 / 入口参数序 / 有无 alpha·scalar 操作数）
  **只从「任务书 × op_def × example」接口探测器取**，运行时探测、不写死（承 B-core 探测器）。
- **本文点名的算子（im2col / Pdist / Foreach… 全部）一律是「见证算子（witness）」，不是优化目标。**
  选它们的唯一理由是**前置依赖已齐**（已 clone / op_def 已核 / 无人裁悬挂）——用来**跑通并证明通用机制真的通**；
  通用机制立起来后，同一份 runner/spec 换个算子探测即应能跑，**不为任何见证算子改一行专属逻辑**。
- 分期的单位是**「泛化能力」**，不是「算子」。见证算子只是恰好先把某个能力跑通的那个。

---

## 0. 一句话结论（先读这段）

**瓶颈是 U7c（共享真机 runner），不是 U7b（spec schema）。**
shape_transform 三算子（im2col + 两个 Upsample）2026-07-23 只落到 **spec + golden.py + gen_cases** 层、
真 torch golden 全绿，但**一次没上真机**——`samples/runners/` 只有 sign/equal/isclose 三份
三浮点单张量骨架，扩展 manifest（out_shape 独立传）**在 runner 侧无消费者**（isclose 遇 extra 字段直接 FAIL）。
而这套通用 runner 是**每一类非 elementwise 真机验收的共享硬前置**，至今空白。

→ 第一刀**不是「先做哪一类算子」**（见 §3：按类分期已被用户纠偏作废）。第一刀是**先冻结一份打满全结构包络的
通用契约 + 生成式 binding（§3.2/§3.5），再用「覆盖互补结构轴」的见证集验证**——见证集要挑**最压满结构轴**的
（多输出 + 列表），不挑最简单的 shape_transform（挑最简单=针对易做的算子优化）。
⚠ **这份契约本身经 codex 审出仍不够泛化**（表达不了 tensor_list/inout/data-dependent），正在按其 14 条重设计（§3.5）。

---

## 1. 当前真实状态 —— 修正「七道硬闸」快照（2026-07-22 → 07-23）

fanout 用 **blame commit + 本次实读行号**核实，快照里下列闸/记法**已 stale**，一律以本节为准：

| 快照旧记 | 现状（实读，带 commit） | 抬闸机制 |
|---|---|---|
| 闸 1 · 输出形状只能是各输入广播（`repo_adapter.py:495/501`） | **已解耦**：`repo_adapter.py:712/716/756` 声明优先、否则退回同形；行号已漂 | C1 `out_shape(in_shapes,attrs)` per-op golden.py |
| 闸 5 · attr 只能标量（`gen_cases.py:374` raise） | **已抬到 `list[int]`**：`_check_attr_value` `gen_cases.py:641-665`（`:374` 现已是别的报错文案） | C2 |
| `run_on_npu.sh:24` `_math` 后缀硬编码 | **已删**：`:24-38` 按 `OPRUNWAY_VENDOR_SUFFIX`→仓名正则→推不出 fail-closed | 2026-07-23 |
| `verify_mode==exact` 硬绑 bool 输出（im2col spec 记「当前无解」） | **已修**：`repo_adapter.py:732-752` 输出 dtype 从 `caseset.compare_dtype` 派生，不再从 verify_mode 猜 | 2026-07-23 |

**仍未抬的闸（U7b/U7c 的主战场）**：

| 闸 | 位置（实读） | 卡什么 | 属 |
|---|---|---|---|
| 闸 3 · 输入构造 | `gen_cases.py:404-434`（末尾写死 `return [x0,x1]`）、`:380-383`（arity>2 raise）、`repo_adapter.py:688-693`（异 dtype/非三浮点 raise） | 二元/同形/同 dtype/arity≤2；无标量操作数、无 per-input dtype、无列表 | U7b |
| 闸 4 · dtype 白名单 | `gen_cases.py:76`（`_NATIVE`）+ `:399-401`；`repo_adapter.py:19`（`_NP`=三浮点）；runner RunCase 只三浮点分支 | int8/uint8/int64/uint16/uint32/uint64/bool/double/complex 全无——**41/44 算子沾** | U7b+U7c |
| 闸 6 · 单输出 | `gen_cases.py:1264`（单 golden.npy）；`validator.py:693/726-751`（单 cdtype/单 policy）；runner 单 out.bin | 多输出算子只验第一个输出，放过一半判据 | U7b+U7c |
| **U7c runner 全空白** | `samples/runners/` 仅 sign/equal/isclose 三份 | 动态输出 buffer、扩展 manifest 消费者、aclTensorList、int dtype 分支、aclIntArray attr 全无 | U7c |

**关键：shape_transform 的 adapter 改动（out_shape 独立传、扩展 manifest）在 runner 侧目前无消费者**——
这正是「gen_cases 过 ≠ 真机过」半成品状态的根源。

---

## 2. ~~按 shape_class 的一般化设计~~（**已被 §3.5 正交契约 + 覆盖矩阵取代，降级为「结构轴的反例语料」**）

> ⚠ **本节按 codex F12 降级**：它按 shape_class 组织、带「最便宜子切/复用 40%/卡 clone」等**旧排期语言**，
> 会诱导「按类落机制」。**实施计划一律按 §3.5 契约能力 + 依赖关系排，不按算子类。** 下文保留仅作「各类算子给出哪些结构轴反例」的语料，
> 其中 effort/首切/按类 runner 增量的说法**作废**。

> 通则（用户已裁，不重开）：① out_shape→per-op golden.py，**不搞表达式语言**；② dtype 冲突以任务书为准、
> 差额入 `task_pr_gaps`、裁决 `PASSED_WITH_GAPS`；③ **泛化、绝不特判某算子**，接口/算子名/目标目录从
> 「任务书×op_def×example」通用探测；④ 新字段对 4 份 elementwise 样例**零变更**（缺省即现行为）。

### 2.1 tensor_list（8 Foreach，effort **xlarge**，最大一刀）
- **spec**：`params[].kind = tensor|scalar|tensor_list`；`list_len_axis`（把 n 声明成覆盖轴，含 0）；
  `list_parallel_to`（并列列表共享 n 与逐元素同形）；顶层 `allow_empty_list`（boolean-only、缺省 false）；
  `list_elem_shape=varied|uniform`；`enum_values`（RoundOffNumberV2 的 roundMode 输入张量取枚举）；
  per-param dtype + `dtype_paired_to`（alpha 随元素 dtype 联动）；输出侧 `io:out` 可 `kind=tensor_list`。
- **runner**：manifest 新方言（列表长 n + n 组子张量 dims）；建 `aclTensorList`；n=0 是合法 no-op；
  int8/int16/uint8 分支（从真 aclnn example 抠）；op 无关探测（不为 8 个各写一份 runner）。
- **validator**：逐子张量判 + n=0 合法空跑；perf `_case_numel` 改跨列表求和。
- **前置门（硬）**：`repos/ops-nn` **未 clone** → 8 份形态全靠**非官方参赛设计文档**，
  `op_def` 一个没核；scalar-vs-scalarList / roundMode-输入-vs-attr / alpha-dtype 映射三处未决。
  **clone + 交叉核未做前连 spec 都不能落**（撞 CLAUDE.md #1/#2）。
- **复用 shape_transform 约 40%**（C1 对账机制、allow_empty 模式、G4 预算、gap 挂账）；
  `_build_inputs`/单 golden/runner 固定槽/单 policy 四处是全新。

### 2.2 reduction（Pdist/MinDim/MaxDim/median，effort Pdist 首切 large、全类 xlarge）
- **C1+C3+G4 已把 G1 全部、G3 的 gen_cases 侧全部覆盖**：Pdist 只需 `rank:2` +
  `out_shape((N,M),attrs)->(N*(N-1)//2,)`；MinDim/MaxDim/median-dim 的 out_shape 直接读 `attrs['dim']/['keepdim']`
  （attr 驱动形状被 C1 免费覆盖，再证不搞表达式语言）。G4 O(N²) 爆内存已由降规模+留痕解决。
- **真增量**（C1–C5 未触及）：① 多输出（值+索引双 dtype）；② **G2 度量属性 `p` 作语义轴且能表达 `inf`**
  （sentinel 串 `'inf'`，核心验收场景就是 p=inf 精度）；③ `variants` overload 轴（median 一名两 aclnn 变体）；
  ④ `tie_break`（median=first 可判、MinDim/MaxDim=ambiguous 转人裁）；⑤ G3 的 runner.cpp（骑 U7c）。
- **Pdist 是最便宜子切**：单输出绕开闸 6、无 overload、无索引 → 避开全部 §5.4 later 人裁；MR2663 已合入真机可做。

### 2.3 index_scatter（bincount/EmbeddingDenseGrad/MaxUnpool2d/3d/IndexFillTensor，effort xlarge）
- **C1 覆盖 5 个里 4 个 out_shape**（IndexFillTensor=self、MaxUnpool=attr、EmbeddingDenseGrad 首轴=num_weights），
  仅 bincount 是真 data_dependent（长=max(self)+1）。
- **核心新机制**：输入 `role: data|index|weight` + `index_domain`（合法索引域，否则随机造必越界→kernel 非法访问）；
  per-input dtype（grad FLOAT + indices INT32）。
- **前置门重**：多数算子卡 clone（ops-nn/experimental 未 clone）/ 无实现 / 人裁（MaxUnpool3d gather-vs-scatter 三源打架）/
  EmbeddingDenseGrad 是**纯 300V Pro** 目标机须先确认平台。

### 2.4 generator（Arange/logspace，effort medium，算子少但结构独特）
- **C1 直接复用**：`out_shape([], attrs)` 签名本就允许 in_shapes 为空——logspace `->(steps,)`、
  Arange `->(ceil((end-start)/step),)`。
- **缺口**：无输入张量（gen_cases 从「第一个 in 取 dtype」起步会崩、`_plan` 围绕输入 shape 阶梯搭）；
  用例轴从 dtype×shape 换成 dtype×(start,end,step)；runner 不读 x*.bin、只读 manifest 标量。
- **op_def 已 clone 但目标机不同**：Arange→a3（ascend910b）、logspace→a5（ascend950），须分别验收。

### 其余（out-of-scope 本轮）
sparse_linalg（SPMV）/ other（Sleep/TrsmBatched/Cheevj/SlidingTileAttention/dynamicMap）—— 需另造 adapter、
外部 GPU A100 基线、或非数值判据，属 Task 3 范畴或另一量级，本轮不设计。

---

## 3. 架构：一份通用契约打满整个结构包络（**不按类分期「长」出来**）

> ⚠ **本节 2026-07-23 由用户纠偏后重写**。旧版把它排成「A shape_transform → B 归约 → C 列表 → D 索引」
> 的**按类分期**——那是错的、违反最高律令，理由见下。

### 3.1 旧「按类分期」错在哪（诚实认账）

- shape_transform 是非 elementwise 里**结构最简单**的一类（单进单出，只是输出形状不同）。
  「先按它建 runner」= 第一版 runner 只装得下**最窄的结构子集**，到列表/多输出时得**回头返工**。
  **建 4 个各按一类形状裁的机制、再把并集叫「通用」——就是把增量特判包装成能力分期。**
  「见证算子」这层皮救不了它：只要 runner 只覆盖那一类的结构，它就是被那几个算子的形状裁出来的。
- **真正通用的工具不该「一类一类长出来」**。分期的单位就不该是「先做哪一类」。

### 3.2 正确做法：先把**一份通用契约**设计到全结构包络

一次性把 spec-schema + manifest 格式 + **op 无关 runner 模板**设计到下面**全部结构轴**，
一律 **spec 字段 + 接口探测器**驱动 → **加任意算子 = 只写它的 `golden.py` + spec（纯数据），引擎/runner 零返工**：

| 结构轴 | 取值 | 现状 |
|---|---|---|
| 输入元数 | 0 / 1 / N / **列表（n 轴，n=0 合法）** | 二元写死 `return [x0,x1]`、arity>2 raise（`gen_cases.py:404-434/:380`）|
| per-input dtype | 各输入可不同 dtype | 全体共用 self dtype，异型 raise（`repo_adapter.py:688-693`）|
| 输出元数 | 1 / **多输出**（各自 dtype+shape+判据）| 单 golden.npy / 单 policy（`gen_cases.py:1264`、`validator.py:693`）|
| 输出形状来源 | 同形 / `golden.py out_shape()`（公式/归约/数据依赖）| **✅ C1 已通用** |
| in-out | 同 buffer 既进既出 | `io:'inout'` 是合法 schema 值但无消费者 |
| 操作数种类 | tensor / **aclScalar** / **aclIntArray attr** | aclIntArray 已到 manifest（C2）、runner 侧未解析；aclScalar 无 |
| dtype 分派 | **一张通用表** | `_NATIVE` 三浮点+bf16 写死、runner 三浮点分支 |

**对抗式自查**：设计完这份契约，必须逐条问「有没有任何字段/分支是按某一类形状裁的」——
凡「只有 X 类算子才走的路」都要么抽象成通用轴、要么明证它是**另一套工程制品**（见 3.4）。

### 3.3 剩下才谈「分期」，且只有两种合法形态

- **验证顺序（不是能力分期）**：第一批见证挑**最能压满结构轴**的算子——例如**一个多输出的 + 一个列表的**，
  逼通用机制从第一天就正确。**绝不挑最简单的 shape_transform**——挑最简单的正是「针对易做的算子优化」。
- **真正不同的工程制品**才配独立 adapter：稀疏库 API（SPMV 三阶段 handle/描述符）、有状态容器（dynamicMap）、
  无张量/时序（Sleep）——这不是特判某算子，是**另一套机器**，且照样「探测 + adapter、无 op 名分支」。

### 3.4 ⚠ 前提纠正：`ops-nn` **其实早已 clone**（2026-07-23 实核）

- `repos/ops-nn` = **285M 完整 git 仓、非 sparse、op_def 内容可读**（今日 10:26 已在），
  9 个 Foreach 目录 + `op_host/*_def.cpp` 全在，`index/index_fill`、`activation/{sigmoid,relu}`、`loss/logit` 均在。
- **「ops-nn 未 clone」是分类学 2026-07-22 的 stale 记载、fanout 照搬未鲜核**——taxonomy §0.2/§5.1 与本 doc 早前
  「C/D 因 clone 未做而推迟」的推理**均据此作废**。
- 直接后果：**列表轴（Foreach op_def）、多输出、in-out、per-input dtype 的 ground truth 现在全部可核**，
  通用契约可**一次性 grounded**、不必分期长。且 `foreach_add_scalar` 与 `foreach_add_scalar_list` **两目录都在**
  → ForeachAddScalarV2 的 scalar-vs-scalarList 悬案（§5.2 #2）现可实核裁定。

> **300V Pro（用户 2026-07-23 裁定：优先级降低、往后放）**：`Cast&EmbeddingDenseGrad`（纯 300V Pro）与
> `SyncBatchNormGatherStats`（A2+300V Pro）**维持挂起**；本仓无该硬件，`ascend310p` 是否即 300V Pro 未核、不落 a3/a5。

### 3.5 通用契约的具体实现：**正交契约 IR**（2026-07-23 重设计，回应 codex F1–F14）

> ⚠ **本节 2026-07-23 由 codex review 逼出重设计**。此前那版「18 字段」被审出**建在「普通张量计数」上、
> 表达不了 tensor_list / inout-alias / data-dependent 输出**，且 `list_parallel_to`/`tie_break`/`role` 等是**按类反推的专名**——
> 违反最高律令。现改成下面**少量正交模型**（每个原子字段带 provenance + fail-closed 状态机）。

**9 个正交 IR 元素**（每个都注明取代了旧哪个按类专名——replaces_专名）：

| IR 元素 | 结构（正交、通用） | 取代的旧专名 |
|---|---|---|
| **`parameter_descriptor[]`**（递归、按 ABI 位序、**单一真相源**） | `{abi_position, kind∈{tensor,tensor_list,scalar,scalar_list,array,attr,opaque}, element_descriptor, cardinality∈{single,list{length_symbol,min,domain}}, logical_value_id, direction∈{in,out,inout}, copy_in, copy_out, presence∈{required,optional_nullable}, absent_semantics, binding∈{device_tensor,host_scalar,host_array,attr}, allow_empty, provenance}` | role 词表、input/output_count（→由它 count 派生、删；防双份真相漂移）、把 tensor_list 从「普通张量计数」救出 |
| **`constraint_graph`**（参数关系图） | 节点=logical_value_id，边 `{length_equality(共享 length_symbol,表并列同长)/dtype_relation/shape_relation}`；并列关系靠 InferShape **位置性**推断（input0 实例数=输出数）非算子名 | `list_parallel_to`、`dtype_paired_to` 的「并列」部分 |
| **`value_domain`**（per-参/per-输出 dtype，tagged union） | `follows(ref)/enum(set,selector_ref)/fixed/promote(rule,operands)`；**绝不设算子级单一 dtype 规则** | `dtype_paired_to`（→follows/promote）、`role:data\|index\|weight`（→dtype 家族由值域表达） |
| **`shape_materialization`**（per-输出 extent 来源） | `mode∈{manifest, host_oracle, runtime_query}` + `value_dependency` + `shape_upper_bound` + `extent_readback`；**runner 只消费已物化 shape、不懂 broadcast/reduction** | `shape_transform_formula`/`from_broadcast`/`from_reduction`/`output_shape_rule`（这些建在「输出=f(输入 shape)」上，表达不了「输出=f(输入**值**)」如 bincount） |
| **`output_mapping`**（op_def 声明序→aclnn 槽序 二部图） | `edges{source_output_id→api_param_id, evidence, confidence}`；**默认不是恒等**；证据分级 glue std::get→ViewCopy 接线 > value_domain > name > example | `op_def_output_order_warning`（那只是告警标签、不承载映射） |
| **`acceptance_predicate`**（per-输出判据=**关系**非固定比较） | `pointwise{exact/isclose}` \| `equivalence_relation{relation_ref, over}`；并列最值歧义=退化的等价关系（`gather(self,dim,npu_idx)==value_out`），对各框架并列约定免疫 | `tie_break{FIRST,LAST,ANY}` 枚举 |
| **`storage_alias_layer`**（存储层，与逻辑值层分离） | `{storage_alias_group, alias_of, readback_binding, alias_kind∈{full,view_offset}}`；**唯一运行期 ground truth = example 那句 D2H 从哪个 buffer 回读**；`const` 不可信 | 「IN 拷进/OUT 拷出」二分（表达不了 inout 的 copy_in=T∧copy_out=T 第三格） |
| **`abi_signature`**（两段式**都**完整描述） | `stage1{ordered_params, trailing}` + `stage2{full_signature, abi_version_range, probe∈{matched,mismatch→unsupported}}`；**不假定恒 4 参** | `stage2_fixed_template`「恒 4 参」公理（F7） |
| **`provenance`**（贯穿每原子字段） | `{source∈{header,op_def,example,taskbook,glue,doc,infershape}, cross_check, conflict_resolution, state∈{resolved,needs_source,conflict→gap,out_of_domain}, fail_closed}` | 「18 字段复合标签」的不可实施性（F8）、「只从 example 不从 op_def」与三源律的矛盾（F13，改**按字段分源**：ABI 走 header/example、语义/dtype/硬件走 op_def×任务书交叉） |

**适用域（三条同时成立才自动处理）**：① 无状态 · ② 标准 aclnn 两段式（probe=matched）· ③ 无 opaque descriptor 生命周期，且每个输出 extent 至少一条物化通路可达。
**域外一律 fail-closed，标「不支持的接口能力」+ 具体轴，绝不自动归某类 adapter、绝不编造 size**（sparse handle / 有状态 / 非两段式 / data-dependent 三通路皆无 / nullable 解析不出 / direction 不能唯一定 / golden 非唯一却无等价关系）。

**〔F3 · proposed，唯一架构承诺、待你点头〕生成式 binding**：**不做「一个万能 runner 靠 manifest 运行时反射调任意 C++ 签名」**（`dlsym` 只给地址、给不了类型化调用点，pack 不了 aclTensorList、传不了 host aclScalar）。改成流水线：**探测器（只读 header×op_def×example×glue×doc×infershape）→ 规范化 IR（版本化 Schema）→ 唯一通用 codegen 模板 → 机械生成 per-entry 类型化薄 `binding.cpp` → 编译链接进 runner**。承诺从「runner 零返工」改为「**模板/引擎零改、binding 机械生成、禁手写 binding**」——编译器在**生成期**强制 ABI 正确（签名错/槽错编译期就挡，而非运行期静默读错 buffer）。tensor_list pack / alias 回读 / per-output shape 物化 / 多输出反转映射全在生成期落成确定代码、**运行期无反射无按类分支**。

**覆盖矩阵**（结构轴 × 见证 × {正例/边界/fail-closed}，源码核证级——**covered≠真机绿**）：tensor_list · inout/alias · data-dependent extent · optional/nullable · 多输出反转映射 · per-output value_domain · 等价关系判据 = **7 轴 covered**；abi/stage2 probe = **partial**（探测器未建）；opaque/有状态/稀疏 = **域外 unverified**（明划域外）。

**对抗式「按类专名」自查结论**：逐字段查过，**已无「只有 X 类算子才走的字段/分支」**。唯一残余是 `equivalence_relation` 与 `absent_semantics` 的**内容**须逐算子从「任务书×golden×doc」填——但那是**算子语义本质**（结构仍通用、契约只持槽+引用+provenance，非枚举），不是「按类长」。

⭐ 连带**泛化铁证**：im2col aclIntArray 真序 `kernelSize→dilation→padding→stride`（stride 末位）≠ 派单——**attr 顺序必从 ground truth 抠**。**目标机冲突**（im2col/MinDim/logspace op_def 仅 ascend950 vs 任务书 A2/A3）与 **MinDim/MaxDim「仓内无实现」stale**（实核实现齐全）见 §5 逐入口状态。

### 3.6 契约实测：方向坐实、但没零缺口站住（2026-07-23 IR 实测 fanout，5 agent 只读）

把 §3.5 契约拿去**对最硬的 4 个跨轴见证**（`foreach_add_list` 列表 / `InplaceSigmoid` inout / `bincount` data-dependent / `ArgMaxWithValue` 多输出反转）**从真源码填完整 IR 实例、对抗式核「单靠 IR 能否机械 codegen」**。裁决：

- ✅ **契约方向被正向坐实（三支柱）**：`output_mapping` 显式二部图（ArgMax 实测 op_def 序 `indice→values` ↔ aclnn 槽序 `out→indices` **反转 {0→1,1→0}**，两个都是 `aclTensor*` 无类型信号、只能靠 IR 预解析 glue `std::get→ViewCopy`——**证明做成显式二部图是对的**，否则把 indice 写进 values 缓冲、静默灾难）· `readback_binding` 锚 example 的 D2H 源 buffer（InplaceSigmoid 从 selfRef 回读，`const` 不可信已证）· `abi_signature` 逐入口 probe（实测 stage1 arity=3/6/7，**「恒 4 参」确被证伪**）。
- ❌ **但没零缺口站住**——词表深度不够，`bincount` 直接 **blocked**（`value_dependency` 词表表达不出 `reduce_max(self)+clamp(minlength)`，机械层给不出 out 尺寸就发不出调用）。

**5 个契约缺口（2 关键，本轮就地补进契约）**：

1. **〔最严·已补〕`shape_materialization.mode` 从单选改可复合 + per-execution-path**：`manifest ∧ host_oracle` 并带**一致性断言**（foreach 的 out 既是 caller 预分配又必须==InferShape 从 x1 推）；同算子 aclnn 单算子路径=host_oracle、图/GE 路径=数据依赖，须 per-path 限定。→ **解 bincount blocked**（能机械判定「out 尺寸须人给/真机回填」而非猜）。
2. **〔次严·已补〕`constraint_graph` 增两类边**：**index-zip 配对边**（`x1[i]↔x2[i]↔y[i]` 按同下标对齐 shape/dtype 相等）+ **intra-list 同构边**（`∀i dtype(x1[i])==dtype(x1[0])`）。→ 解 foreach「造合法测试数据」gap（否则撞 `OP_CHECK_SHAPE_NOT_EQUAL`）。
3. **〔待补〕`value_domain.selector` 三种键源**：keyed-on 另一参运行时 dtype（alpha←x1[0] 含 promote BF16→FLOAT）/ keyed-on 输出槽自身 dtype（indices 自指）/ `platform_predicate`（SocVersion 区间——dtype 白名单按硬件三分支、非参数驱动）。
4. **〔待补〕`acceptance_predicate` 跨输出引用 + 阈值来源降级 tier**：ArgMax 索引判据 `take_along_axis(self,dim,idx)==values_out` 引用另一输出；无随仓 taskbook 的算子（`needs_source`）须容「doc 产品表 + 语义推断」作降级来源并显式标 tier。
5. **〔待补〕输入侧映射 + 合成/被抑制参数 + `allow_empty` 拆层**：aclnn 参→op_def 输入的反向对应（bincount 的 `size` 是 glue 合成注入）· op_def attr 被 aclnn 抑制/重派（ArgMax `indice_dtype`）· `allow_empty` 拆 `empty_list` vs `empty_element(numel=0 tensor)` 且带 provenance/conflict 标记、不塌成单 bool。

**F3 de-risk 结论**：生成式 binding 在 3/4 轴**可机械 emit**（output_mapping/readback/probe/pack-list/inout 都能从 IR 落成确定代码）；**bincount 的 blocked 是契约词表缺口（已补 G1）、非架构缺陷** → **F3 生成式 binding 方向坐实、值得批。**

### 3.7 契约锁成版本化 Schema + G3–G5 补齐（2026-07-23，用户批 F3 后落地首步）

**用户 2026-07-23 裁定**：F3 批（生成式 binding）· 目标 a3/a5 两台 · 先补 G3–G5 再真机四见证。据此落地首步：

- **契约锁成版本化 JSON Schema** → `plugin/acc-common/contract_ir/contract_ir.schema.v1.json`（draft 2020-12，合法）。
  9 元素 + G1/G2 + **G3/G4/G5 全补进 Schema**：
  - **G3 ✅**：`dtype_selector` 三键源——`keyed_on_param_dtype`（alpha←x1[0] 含 promote 表）/ `keyed_on_output_dtype`（自指）/ **`platform_predicate`**（SocVersion 区间分支，**承 a3/a5 双机**：`[ascend910b,ascend910_93]`=a3 / `[ascend950,RegBase]`=a5）。
  - **G4 ✅**：`acceptance_predicate` 的 `equivalence.over` 可引用 inputs+other_outputs（跨输出）；`threshold_source.tier ∈ {taskbook,doc_support_table,semantic_inference,real_machine_pinned}` 降级链。
  - **G5 ✅**：`output_mapping.input_edges`（反向 + `synthesized_from`）· `parameter_descriptor.suppressed`（被 aclnn 抑制的 attr）· `allow_empty_list`/`allow_empty_element` 拆层 + `empty_conflict` 记账。
- **round-trip 正例已验**：`examples/foreach_add_list.ir.json`（探测器该产出的样子）**完全符合 Schema**（jsonschema Draft2020-12 校验通过）——tensor_list 递归 / index_zip+intra_list 边 / dtype selector / 复合 shape mode + 一致性断言 / const_untrusted 全在真实例上坐实。
- **仍未做**：其余 3 见证 IR 实例（InplaceSigmoid/bincount/ArgMax）· 探测器 · codegen 模板 · **真机四见证**（covered≠真机绿）。

**residual（须真机钉 / 逐算子定，covered≠真机绿）**：① 目标机逐算子由双源核定（contested 者停下人核）· ② 空用例覆盖（Schema 已能记 `empty_conflict`，覆盖策略待定）· ③ 无 taskbook 算子阈值走 `real_machine_pinned` tier· ④ bincount out 尺寸（Schema 已能标 unmaterializable、要用例给或真机回填）· ⑤ 全 4 见证纯静态实读、**未上真机**。

**residual（须用户拍/须真机钉，covered≠真机绿）**：① 目标硬件平台（Sigmoid/ArgMax dtype 白名单是 SocVersion 运行期三分支，codegen 期须知 a3 还是 a5）· ② 空用例覆盖（空 list vs numel=0 tensor，doc↔code 冲突）· ③ 无 taskbook 算子的 acc/perf 阈值（ArgMax 无权威 spec）· ④ bincount out 尺寸（离线 IR 给不出，须用例显式给或真机 reduce 回填）· ⑤ 全 4 见证纯静态实读、**未上真机**，裁决须 A3/A5 端到端坐实。

---

## 4. 需用户拍板的决策点

**① 架构确认 + 首批验证顺序 —— 仍待拍板**（旧「A→B 按类分期」已按用户纠偏作废）：
- **架构确认**：走「先设计**一份通用契约打满全结构包络**（§3.2 + §3.5 探测器契约）、再逐轴验证」，
  **不按 shape_class 一类一类长**。（用户 2026-07-23 已强推此方向，此项为落地前的最终确认。）
- **首批验证顺序**：验证第一批见证挑**最压满结构轴**的（建议**一个多输出 MinDim/MaxDim + 一个列表 Foreach**），
  逼通用机制从第一天正确；**不挑最简单的 shape_transform**。
  ⚠ **真机约束**（非「挑易做」）：im2col/MinDim&MaxDim/logspace **目标机冲突未决**、真机验证前须先人核定机；
  当前可直接上真机的无冲突算子 = Upsample(a3)/Arange(a3)/Pdist(a3)。故「压满结构轴」与「真机可跑」的交集需你定：
  是先在**本机**（gen_cases + 契约层，不上真机）压满 MinDim+Foreach 两根硬轴，还是先解目标机冲突再上真机。

**② 纯 300V Pro 的 2 份任务书 —— ✅ 已裁（用户 2026-07-23：优先级降低、往后放）**：
`Cast&EmbeddingDenseGrad`（纯 300V Pro）与 `SyncBatchNormGatherStats`（A2+300V Pro）维持挂起、从近期各期剔除；
EmbeddingDenseGrad 从 D 首批见证集剔除。310p 是否即 300V Pro 仅推断未核，不得擅自落 a3/a5。
**③ `fused_comm` 标签留不留 —— 仍待拍板（但不阻塞任何一期）**：SyncBN 形态就是跨 worldSize 轴归约。
建议去掉、归 reduction 带注记（多一格分类只增词表负担、无判定收益；等 SyncBN 真正排期再落字段）。

---

## 5. U7a 载重前必核的缺口（写进 spec / 开跑前）

- ✅ **`repos/ops-nn` 已 clone**（2026-07-23 实核纠正，§3.4）——16 算子 op_def 现**全部可核**，不再是硬前置。
  尚未逐个交叉核的具体项列为待办（非「仓缺失」统括）：8 Foreach + median + IndexFillTensor + Logit/Relu/InplaceSigmoid 的 op_def 逐份核。
- **3 个 inferred 必核**：MaxUnpool2d（全按名推）、ForeachAddScalarV2（`foreach_add_scalar` 与 `foreach_add_scalar_list` **两目录都在**、现可实核裁定 scalar vs scalarList）、SlidingTileAttention（无参数表）。
- **Foreach 四处待官方源坐实**：scalar-vs-scalarList、roundMode 输入-vs-attr + INT8 枚举全集、alpha dtype 映射、int8/uint8 无造数/收发通路。
- **aclnn 入口名 + 参数序**逐算子从真 `test_aclnn_*.cpp` example 探测（op_def 是 kernel 层、印证不了 aclnn 入口）。**7 算子已锚（§3.5）。**
- **双源交叉核验（任务书 适配硬件 ↔ op_def AddConfig）逐算子做定目标平台**（用逐入口状态表、非概括句）：
  已核无冲突 = Upsample1d/2d/3d→a3、Arange→a3、Pdist→a3；**已核有冲突须停下人核** = im2col / MinDim&MaxDim / logspace（op_def 仅 `ascend950` vs 任务书 A2/A3）。IsClose 早前已核。其余逐算子待核。
- **§5.3 挡近期的任务书毛病**：UpsampleNearest3d 硬件↔dtype 冲突、MaxUnpool3d gather-vs-scatter 三源打架、
  SyncBN 输出 2 还是 4、两 Upsample `uint8` 拼成 `unit8` + Exact2d 输出行 rank 误写 3。
- **index_scatter 的 `index_domain`** 逐算子从任务书原话核 lower/unique 边界（差一点就越界）。

---

## 附 · 已被裁定覆盖、不再问用户的（fanout `already_decided`）

out_shape→golden.py（不搞表达式语言，C1 已证）· attr `list[int]`（C2）· dtype 冲突→task_pr_gaps→PASSED_WITH_GAPS
（+ 第三类 gap `dtype_unsupported_on_target_hw` 已补）· mock 只产 non-acceptance（U6c/U6d 关闭）·
`--defect` 移出 CLI · 目标硬件/算子名走双源核验+探测通用机制 · U4 干净 session 隔离关闭 · catlass 真机降级 ·
shape_transform 三字段（allow_empty_tensor/empty_axis/bf16_bitexact）已落地。

---

## 6. 只读核实批结论（2026-07-24，fanout `weilj1w65`，3 agent 只读，产逐算子数据非算子优化）

> 承 CLAUDE.md #0：以下是**逐算子的验收数据**（目标机 / 语义 / dtype / gap），供通用工具消费 + 更正 taxonomy stale，非给某算子定制。

**A · 目标机冲突逐算子核（task#7）——4 CONFLICT 坐实、须人裁；3 NO-CONFLICT 确认 a3**
- **CONFLICT（op_def 只声明 `ascend950`、任务书『适配硬件』= A2/A3，目标平台完全不在 op_def 声明集内 → 比 Upsample 那种超集含 950 更严重：a3 上无本仓 AscendC 实现、只有 CANN 内置 TBE）**：
  im2col（`im2col_def.cpp:41`）· MinDim（`arg_min_with_value_def.cpp:57`）· MaxDim（`arg_max_with_value_def.cpp:57`）· logspace（`experimental/math/log_space/…_def.cpp:49`）。
  ⚠ **处置更正（用户 2026-07-24：任务书权威、PR≠任务书=可能选错 PR）**：这**不是「选 a3 还是 a5」的机器题**——**任务书是权威**，它要 A2/A3，而 op_def（被测物/PR）只有 ascend950、A2/A3 完全缺席。按**硬约束 #1（Equal 血教训）+ canon `task-spec-authoritative-over-pr`**，这更像**选错了 PR**（这个 op_def 不是该任务的交付 PR），或该任务**在 A2/A3 根本没落地**（未验收空任务）。**绝不按 op_def 自动定 a5 去跑**（那是拿被测物凑答案、验的不是任务书要的硬件）。正确处置：**先验证「任务书↔PR 对应」**（issue 追踪号 + 改动落点目录，别靠算子名字面匹配）→ 若对应错/未落地则该算子**整体挂起、不进验收**；对应真实才谈目标机。

  ✅ **对应核已做（2026-07-24 fanout `w6rs1z8xz`）——5 个全部对应不成立、全作废挂起、不进验收**（Equal 血教训在 5 op 重演）：
  - **im2col** `likely_wrong_pr`：被测 `conversion/im2col`(ascend950) 是任务书 `:89` 自列的「代码样例」参考算子、**非交付物**；任务合入目标是 `experimental/conversion`，其下另有独立候选 `im2_col`（A2/ascend910b，但 dtype 仅 fp/fp16、**无任务核心的 bool**）；`issue#255` 号段异常偏早。**选错了源。**
  - **logspace** `not_landed_on_target`：对应线程真（issue#2029、experimental/math），但 **PR #3496 仍 open**、op_def 仍 ascend950 基线、**任务核心的 int16/32/8/uint8 完全没实现**。A2/A3 没落地。
  - **MinDim / MaxDim** `likely_wrong_pr`：op_def=ArgMin/MaxWithValue 在**成熟生产树 `math/`**(ascend950)，任务要的 `experimental/math` 下**根本没这俩算子**、`task-pr-map` 已判「未找到、无 issue 号」；字面命中一个 950 生产算子。（`aclnnMinDim↔argminwithvalue` 映射本身无误，错在被测物选源/落点/平台。）
  - **bincount** `not_landed_on_target`：真交付 **PR #3640 open**、experimental 版缺失；clone 里读的是**官方 TF 式主线**(ascend950、array/size ABI)，**非任务的 torch 式**(self/weights/minlength/out) 交付。
  > ⚠ **连带（须标清）**：`argmax(=MaxDim)`/`bincount` 曾作 codegen 的**结构测试 fixture**（多输出反转 / data-dependent 轴，用真 aclnn 接口测**通用机制**）——那部分**仍有效**（测的是「能否 emit / 能否 fail-closed」）；但**作验收目标已作废**。**fixture ≠ 验收目标。**
  逐条待各自 spec 建时入 `task_pr_gaps`（im2col 已挂 `im2col.spec.json:189`）。
- **NO-CONFLICT = a3**：Upsample 家族（op_def 含 `ascend910b+ascend910_93`）· Arange（`ascend910b+ascend310b`，实读确为 **310b 非 310p**）· Pdist（`ascend910b+ascend910_93` 与任务书精确吻合，最干净样本）。

**B · Foreach 4 处未决语义坐实（task#9，ops-nn shallow c0bb2233 实读 op_def+binary.json 双层交叉）**
- **① ForeachAddScalarV2 = 单标量 scalar**（op『ForeachAddScalar』Input scalar REQUIRED 元素数 1，V2 只是把 aclTensor 改 aclScalar）——**悬案关闭**，taxonomy §5.2 #2 由 inferred 升 verified；scalarList 是另一颗独立算子 ForeachAddScalarList。
- **② roundMode = 输入张量**（Input REQUIRED，非 attr），dtype INT8。
- **③ alpha dtype 映射坐实**：add_list A2/A3 硬编码 7 对（int16/int8/uint8→int32）；helper 真名 **`DtypeScalarToTensor2`（带 2，无 2 的不存在）** 仅 4 对、对 int8/uint8 返回 DT_UNDEFINED；sub_list 全档 4 对无 int8/uint8。
- **④ 8 Foreach per-platform dtype 集机读表**：int8/uint8 在 **A2/A3 已实现 = AddList/AddScalar/Exp/Expm1**；**未实现 = MulList/SubList/Neg/RoundOff**（950 档全部无 int8/uint8/int16）。→ 未实现档**不得造 int8/uint8 例**（撞 Equal 血教训）。
- **⑤ 派生 stale-baseline gap**：master 上 AddList/AddScalar/Exp/Expm1 的 A2/A3 已声明 int8/uint8/int16，与任务书『当前不支持、请添加』前提冲突；⚠ shallow clone 无历史，**不断言 PR 已 merge**（PR #5213/#5667/#5454/#5214 均 open）。

**C · inferred 算子核实（task#8）**
- **MaxUnpool2d → verified**（实现在 `ops-nn/index/scatter_elements/aclnn_max_unpool2d`）：io = self+indices+outputSize(attr,2 int)→out；rank 3-4；out 的 H/W 由 outputSize、N/C 随 self；indices int32/int64 baseline 已支持；self dtype 无 bf16（**PR delta = 加 bf16**）。⚠ 无独立 op_def（lower 成 ScatterElements）→ **硬件双源核验做不成**、文档比任务书多声明 950 → 入 gap；取材路径以 scatter_elements 为准（任务书写 gather_elements 是反向）。
- **SlidingTileAttention → 仍 inferred**：所有已 clone 仓（含 ops-transformer）**零实现**（穷举 grep 空）；仅任务书可 grounding：dtype bf16+fp16 / 格式 BNSD(rank4) / 硬件 A2 训练（仅 A2）；io_arity/输入表/输出形状/attrs 缺源=实现本身 + 外部 FastVideo `ops.py`（未 clone）。**宁标 inferred 不假装核过。**

> ⚠ trust tier：正源（cann-ops-competitions/ops-math/ops-nn）被 gitignore、不入库；本批实读证据属**库外**，支撑上述判定但按纪律须人裁后方可升 canon tier。taxonomy §5.2/§5.3 的相应 inferred/待核项据此可更正（留 bureau task#10）。
