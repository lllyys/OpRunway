# 算子形态分类学 —— 41 份社区任务书全覆盖

**一句话**：41 份社区任务书 = **44 个算子**（3 份任务书各写了 2 个算子），现在**全部分类完**。
真正「逐元素、同形出、单张量进」的只有 **15 个（15/44 = 34%）**；而这 15 个里，当前 `gen_cases` + runner 骨架
**结构上能加进来的仍只有 6 个（6/44 = 14%）**——Equal / IsClose / Neg / Sign 已有真机验收记录，
FmodTensor 差两个文件，InplaceRsqrt 要改骨架。

**上一轮（只覆盖 23 个）说的是「52% 是 elementwise」，这一轮补完剩下 21 个之后，比例掉到 34%。**
补进来的 23 个（ops-nn 16 + 其余 6 仓 7）里只有 3 个是 elementwise（Relu / InplaceSigmoid / Logit），
其余 20 个全部越界——上一轮那句「大概率会把 elementwise 比例拉低（推断）」已被证实，不再是推断。

**三个最刺眼的结论（都是准确计数，逐条可回溯到下面的总表）**：

1. **张量列表（TensorList）是第二大类，8 个，比 reduction(4) 还多**——8 个 Foreach 全族。
   当前 caseset schema 里「一个参数 = 一个 ndarray」，`是 N 个张量、各自 shape 不同、N 还能是 0`
   **连写都写不下来**。上一轮把它记成「桩自报的疑似第 6 类形态、未核」，本轮 8 份全部落实。
2. **18 / 44 在部署前就到不了真机**：17 个算子的输出形状不是「各输入广播」，被
   `repo_adapter.py:495` 那道 `np.broadcast_shapes` + `:501` 的 `golden.shape != out_shape → raise` 硬拒；
   再加 Sleep 根本没有输出张量。
3. **41 / 44 的任务书 dtype 集里含有 runner v1 收不下的类型**（int / uint / bool / double / complex）。
   整个 dtype 面完全落在 runner v1 三浮点 `{fp32, fp16, bf16}` 之内的，44 个里**只有 2 个**：
   Pdist 与 SlidingTileAttention（Sleep 无张量、不适用）。

**换句话说**：「加一个算子 = spec + golden + runner 三个文件」这句话，只在 elementwise 那一格里成立，
而那一格现在只占 34%、能跑的只占 14%。出了这一格，要动的是 Layer 0 契约（spec schema）与 Layer 1 引擎本身。

---

## 0. 这份文档能信到什么程度（先读这段）

**⚠ 标 `inferred` 的还没核，是按算子名 / 同名 PyTorch 语义 / 冲突未决推的。载重前（写进 spec、开跑、
当验收依据）必须先核。** 这仓栽过一次：Equal 被「按标题/算子名匹配」误配成 PR #2890，整条验收结论作废
（见 `doc/oprunway-task-pr-map.md` 的 Equal 更正）。**宁可标 inferred，也不许假装核过。**

### 0.1 核实度三档（本轮新增中间档）

| 档 | 含义 | 数量 |
|---|---|---|
| `verified` | 读过**官方原文**（社区任务书 + 可得的仓内 `op_def` / README / 源码），出处带行号 | **31 / 44** |
| `verified(单源·非官方)` | 官方任务书**没写形态**，结论只靠**参赛者设计文档**（`tasklist/**/docs/design.md`）撑着 | **10 / 44** |
| `inferred` | 按名推，或两源打架未决 | **3 / 44** |

- **中间档是我（合并员）加的，不是子 agent 报的。** ops-nn 那组把 8 个 Foreach + Logit + median +
  MaxUnpool3d 全报成 `verified`，理由是「设计文档里有明确原文、引号已抄进 evidence」。但设计文档是
  **参赛者写的、非官方**，且 `repos/ops-nn` 本机根本没 clone（我自己 `ls repos/` 核过，15 个仓里没有它），
  无法交叉核 `op_host/*_def.cpp`。按本仓「宁可标低」的规矩，我把这 10 条**下调**到中间档并注明理由——
  这是下调、不是上调，符合「原样透传、不上调」的原则。
- 标 `inferred` 的 3 个：**MaxUnpool2d**（形态按算子名推）、**ForeachAddScalarV2**（任务书目录名
  `foreach_add_scalar_list` 与设计文档 `foreach_add_scalar` 打架，scalar 到底是一个还是一列表未决）、
  **SlidingTileAttention**（任务书全文只有一句「bfloat16/float16、BNSD」，io_arity / rank / 输出形状
  全靠参赛者设计文档）。

### 0.2 哪些是我自己实读、可复现的

- **本次实读的代码行号**（2026-07-22，本机工作树）：
  `repo_adapter.py:476 / :478 / :495 / :501`、`gen_cases.py:362 / :374`。
  ⚠ **上一轮文档与子 agent 报的行号有 ±1 漂移**（旧记 `:494 / :500 / :477`、`gen_cases.py:365`）。
  文件在这期间动过。**下面 §2 一律用本次实读的号。**
- **本次实读的仓内证据**（我亲手 grep，不是透传）：
  - `repos/ops-cv/image/upsample_nearest3d/op_host/upsample_nearest3d_def.cpp`：`:19-21` `xDtype` 含
    `DT_UINT8` + `DT_DOUBLE`；`:31 / :37` 默认 config 的 Input/Output 只有 `{FLOAT, FLOAT16, BF16}`；
    `:46-47` `AddConfig("ascend910b")` + `AddConfig("ascend910_93")`；`:71-72 / :77` regbaseConfig 用
    `xDtype` 且 `AddConfig("ascend950")` —— **uint8/double 只在 950 通路成立、A2/A3 通路没有**，确认冲突。
  - `repos/ops-cv/image/upsample_nearest/op_host/upsample_nearest_def.cpp`：`:26 / :31` 只有
    `{BF16, FLOAT16, FLOAT}`，`:38-39` 只有 910b / 910_93，**没有 ascend950 config** —— 与 3d 版状态不同。
  - 两个 def **都另有** `AddConfig("ascend310p") / ("kirinx90") / ("kirin9030")`（`upsample_nearest3d_def.cpp:66-68`、
    `upsample_nearest_def.cpp:58-60`）。子 agent 没提这一支。
  - `plugin/acc-common/new_example/run_on_npu.sh:24` = `V="$OPP/vendors/${VEN}_math"` —— **仓名后缀写死**；
    而 `repos/ops-cv/build.sh:1508-1511` 产出的是 `opp/vendors/${VENDOR}_cv`。确认硬编码。
  - `repos/` 下实际 clone 15 个仓：AscendOpTest / amct / asc-devkit / cann-ops-competitions /
    cann-recipes-infer / cannbot-skills / catccos / catlass / hixl / oam-tools / ops-blas / ops-cv /
    ops-math / ops-sparse / shmem。**没有 ops-nn / ops-collections / ops-transformer / ops-solver。**
  - 全量 grep `300V`：`docs/202604 + 202605` 共 **2 份**命中，且是这 2 份：
    `Cast&EmbeddingDenseGrad_task_doc.md:10,92`「适配硬件：**Atlas 300V Pro**」（纯 300V Pro）与
    `SyncBatchNormGatherStats_task_doc.md:6`「适配硬件：**Atlas 800T A2、Atlas 300V Pro**」。
    → **CLAUDE.md 记的「涉及 300V Pro 的 2 份、须先停下确认目标平台」，本轮点名到具体是哪两份。**
- **算子形态字段（表 A/B/C）我没有重读任务书逐条复核**，只做合并、去重、按类归组、核实度分档。
  真要拿某一行当 spec 的依据，**先自己核那一行**。

### 0.3 与上一轮口径打架的地方（不抹平，逐条列）

| 冲突点 | 上一轮记法 | 本轮记法 | 谁的证据强 |
|---|---|---|---|
| **SPMV 的 dtype** | 「报缺 uint8 / complex64」（桩自报、**无行号**） | 任务书要 **int8**（`SPMV_task_doc.md:30-36` dtype 组合表 + `:95-103` 参数表），仓内现有 A2 实现是 **int32**（`ops-sparse/test/spmv/README.md` 组合表 + `src/spmv/arch22/kernels/spmv_kernel_i32_i32_i32.cpp` 文件名） | **本轮**：带行号 + 仓内交叉。上一轮那条「uint8/complex64」应视为**错记**、作废 |
| **SPMV 的 io_arity** | 「多输入 → 1 张量」 | **5 进 1 出、且 y_vec 是 in-out**（承载 `beta*y` 旧值再被覆盖）；而且**不是 op_def 算子**，是 aclsparse **库 API**、带 handle/描述符 + **三阶段调用**（GetBufferSize→Preprocess→SpMV） | **本轮**（`SPMV_task_doc.md:44-85` 三阶段 C 原型） |
| **shape_class 词表** | 7 类，`tensor_list` 只是桩自报的「疑似第 6 类、未核」 | **9 类**，新增 `tensor_list`（8 个，实证）与 `fused_comm`（1 个） | 本轮（8 份设计文档均写 `ParamType(DYNAMIC)`）|
| **Sleep** | 「0 输入 0 输出、other」 | 同左，**且补齐**：唯一参数 `cycles`(int64 属性)、`workspaceSize` 返回 0、判据是**时序断言**「实际延时周期数 ≥ 设定值」、任务书白纸黑字「精度要求：不涉及 / 性能要求：不涉及」 | 一致，本轮更全（`Sleep_task_doc.md:19-28,37,41`）|
| **引擎硬闸数** | 4 道 | **7 道**（新增：attr 只能标量 / 单输出 / 全输入共用一个 shape 且无 in-out） | 互补，不矛盾。新 3 道是新数据逼出来的，不是推翻旧 4 道 |
| **代码行号** | `repo_adapter.py:494/:500/:477` | `:495/:501/:476,:478` | **本轮**（我实读）。旧号仍指向同一段逻辑，只是漂了 1 行 |

---

## 1. 总表

44 行、按来源分三组（因为**核实度按组不同**，混在一张表里会掩盖证据强弱）。
每组两张窄表：**形态**（io_arity / 输出 shape 来源 / shape_class）与 **dtype + 证据 + 核实度**。

### 1.1 ops-math 组 —— 18 份任务书 / 21 个算子（上一轮已做，本轮原样保留）

> 这组是**唯一**做了「任务书 ↔ 仓内 `op_def` 双源交叉」的一组（`repos/ops-math` 已 clone）。

#### 表 A1 · 形态

| 算子 | 仓 | 输入 / 输出 | 输出 shape 从哪来 | shape_class |
|---|---|---|---|---|
| Equal | ops-math | 2 张量 → 1 张量 | 输入广播 | elementwise |
| IsClose | ops-math | 2 张量 → 1 张量 | 输入广播 | elementwise |
| Neg | ops-math | 1 张量 → 1 张量 | 同输入 | elementwise |
| Sign | ops-math | 1 张量 → 1 张量 | 同输入 | elementwise |
| FmodTensor | ops-math | 2 张量 → 1 张量 | 同 self（other 广播） | elementwise |
| FmodScalar | ops-math | 1 张量 + **1 标量** → 1 张量 | 同 self | elementwise（含标量操作数） |
| Gcd | ops-math | 2 张量 → 1 张量 | 输入广播 | elementwise |
| RightShift | ops-math | 2 张量 → 1 张量 | 同 input（shiftBits 广播） | elementwise |
| InplaceRsqrt | ops-math | 1 张量**原地读写** | 同输入 | elementwise（inplace） |
| Polar | ops-math | 2 张量 → 1 张量 | 输入广播（≤8D） | elementwise（实→复） |
| AngleV2 | ops-math | 1 张量 → 1 张量 | 同输入 | elementwise（复/整→实） |
| Cast | ops-math | 1 张量 → 1 张量 | 同输入 | elementwise（dtype 由 attr 定） |
| Pdist | ops-math | 1 张量(N,M) → 1 张量 | **归约**：1D，长 = N*(N-1)/2 | reduction |
| MinDim | ops-math | 1 张量 + dim + keepdim → **2 张量**（值 + 索引） | **沿 dim 归约**，随 keepdim 变 | reduction（双输出） |
| MaxDim | ops-math | 1 张量 + dim + keepdim → **2 张量**（值 + 索引） | 同 MinDim | reduction（双输出） |
| Arange | ops-math | **3 个标量**（start/end/step）→ 1 张量 | **看数值**：长 = ceil((end-start)/step) | generator |
| logspace | ops-math | **无输入张量**（start/end/steps/base 全是 attr）→ 1 张量 | 长 = steps（attr） | generator |
| bincount | ops-math | 1D self + 可空 weights + minlength → 1 张量 | **看数据内容**：长 = max(max(self)+1, minlength) | index_scatter |
| EmbeddingDenseGrad | ops-nn | grad + sort_indices → 1 张量 | 首轴 = numWeights(attr)，按索引 scatter-add | index_scatter |
| MaxUnpool2d | ops-math(任务书) | self + indices + output_size → 1 张量 | 由 output_size 定，按 indices 散回 | index_scatter |
| im2col | ops-math | 1 张量(3D/4D) + 4 组 int64[2] → 1 张量 | **属性公式**：outH/outW 由 ksize/stride/dilation/pad 推 | shape_transform |

#### 表 B1 · dtype 亮点与证据

| 算子 | dtype 亮点（超出当前引擎的地方） | 出处 | 核实度 |
|---|---|---|---|
| Equal | 输入 7 类含 int8/uint8/uint32 → **输出恒 BOOL** | `experimental/math/equal/op_host/equal_def.cpp:22-50`；`Equal_task_doc.md:19` | verified |
| IsClose | 输入 {bf16,fp16,fp32,int32} → 输出 BOOL；3 个 attr 定语义 | `.../is_close/op_host/is_close_def.cpp:18-38` | verified |
| Neg | 8 类，含 int64/int8/int16/uint8（uint8 走 256-x wrap） | `.../neg/op_host/neg_def.cpp:24-43`；`Neg_task_doc.md:19` | verified |
| Sign | 5 类 {bf16,fp16,fp32,int32,int16} | `.../sign/op_host/sign_def.cpp:24-31`；`Sign_task_doc.md:19` | verified |
| FmodTensor | 任务书要 int16、仓内 `Mod` op_def 是 int32 —— **对不上** | `FmodScalar&FmodTensor_task_doc.md:33-35`；`.../mod/op_host/mod_def.cpp:22-36` | verified（差异已记） |
| FmodScalar | 同上；且 other 是 `aclScalar` 不是张量 | `FmodScalar&FmodTensor_task_doc.md:26-28` | verified |
| Gcd | 任务书要 fp32/fp16/bf16，官方 `math/gcd` op_def **纯整型**（5 类） | `math/gcd/op_host/gcd_def.cpp:24-46`；`Gcd_task_doc.md:26-28` | verified（差异已记） |
| RightShift | 8 个整型，含 **uint64 / uint32 / uint16** | `math/right_shift/op_host/right_shift_def.cpp:24-56`；`RightShift_task_doc.md:28-30` | verified |
| InplaceRsqrt | 任务书 8 类（含 bool/int8/int16/uint8），op_def(`Rsqrt`) 只有 3 浮点 | `.../rsqrt/op_host/rsqrt_def.cpp:22-31`；`InplaceRsqrt_task_doc.md:26` | verified（差异已记） |
| Polar | 输入 float32 → **输出 complex64** | `.../polar/op_host/polar_def.cpp:24-41`；`polar_infershape.cpp:34-51` | verified |
| AngleV2 | 输入 9 类含 **complex64 / bool / int64** → 输出多数映射到 float32 | `math/angle_v2/op_host/angle_v2_def.cpp:22-44`；`aclnnAngleV2_task_doc.md:13` | verified |
| Cast | 输出 dtype = attr `dst_type`（REQUIRED Int），op_def 列的是**合法输入×输出配对** | `.../cast/op_host/cast_def.cpp:22-94` | verified |
| Pdist | {fp32,fp16}；attr `p` 只改语义不改 shape | `.../pdist/op_host/pdist_def.cpp:18-30`；`pdist_infershape.cpp:23-36` | verified |
| MinDim | 值输出 {fp16,fp32,int16,bf16}，**索引输出 INT32**（两个 dtype 并存） | `MinDim&MaxDim_task_doc.md:26-30` | verified（单源：仓内无实现） |
| MaxDim | 同 MinDim | `MinDim&MaxDim_task_doc.md:35-39` | verified（单源：仓内无实现） |
| Arange | op_def {fp32,fp16,int32,int64,bf16}；任务书写 int8/uint8/int16 —— **对不上** | `.../arange/op_host/arange_def.cpp:22-44`；`Arange_task_doc.md:26-29` | verified（差异已记） |
| logspace | op_def {float,fp16,bf16}；任务书多 int8/int16/int32/uint8 —— **对不上** | `.../log_space/op_host/log_space_def.cpp:27-39`；`logspace_task_doc.md:27-31` | verified（差异已记） |
| bincount | 任务书 out 含 **double**；weights 9 类；op_def(官方版) 只 int32/float | `bincount_task_doc.md:26-29`；`math/bincount/op_host/bincount_def.cpp:23-26` | verified（读的是官方版，见 §5） |
| EmbeddingDenseGrad | grad FLOAT + indices INT32 → out FLOAT（**输入两种 dtype**） | `Cast&EmbeddingDenseGrad_task_doc.md:101,122-164` | verified（单源：仓内无实现） |
| MaxUnpool2d | 任务书只写「self 增 bf16、indices int32/int64」，其余没写 | `MaxUnpool2d_task_doc.md:13,19,56` | **inferred**（形态按算子名推） |
| im2col | op_def 15 类，含 **complex32 / complex64 / double / uint64** | `conversion/im2col/op_host/im2col_def.cpp:14-31`；`im2col_infershape.cpp:55-78` | verified |

> 「差异已记」= 任务书声明的 dtype 与仓内 `op_def` 不一致。按 CLAUDE.md，这类应进 spec 的 `task_pr_gaps`，
> **不是读错**。共 6 个算子有此情况：FmodScalar / FmodTensor、Gcd、InplaceRsqrt、Arange、logspace。

### 1.2 ops-nn 组 —— 16 份任务书 / 16 个算子（本轮新增）

> ⚠ **`repos/ops-nn` 本机没有 clone**（我实核）。这一组**一个 `op_def` 都没核过**。
> 8 个 Foreach 的任务书是**纯模板文**——只写「当前不支持 DT_INT16/INT8/UINT8，改 `<某目录>` 使其支持」，
> **一个字都没写接口形态**。它们「是张量列表」这个判断，目前**只有参赛者设计文档（非官方）撑着**。

#### 表 A2 · 形态

| 算子 | 仓（任务书指向的目录） | 输入 / 输出 | 输出 shape 从哪来 | shape_class |
|---|---|---|---|---|
| ForeachAddListV2 | ops-nn `foreach/foreach_add_list` | **3 进 1 出**：x1 列表 + x2 列表 + alpha 标量张量 → y 列表 | 同输入；**输出张量个数 = 输入列表长度 n** | tensor_list |
| ForeachAddScalarV2 | ops-nn（任务书 `foreach_add_scalar_list` ↔ 设计文档 `foreach_add_scalar`，**打架**） | 2 进 1 出：x 列表 + scalar 张量([1]) → y 列表 | 同输入 | tensor_list |
| ForeachMulList | ops-nn `foreach/foreach_mul_list` | 2 进 1 出：x1 列表 + x2 列表 → y 列表（**无 alpha**） | 同输入 | tensor_list |
| ForeachRoundOffNumberV2 | ops-nn `foreach/foreach_round_off_number` | 2 进 1 出：x 列表 + **roundMode（INT8 输入张量，不是 attr）** → y 列表 | 同输入 | tensor_list |
| ForeachSubListV2 | ops-nn `foreach/foreach_sub_list` | 3 进 1 出：x1 列表 + x2 列表 + alpha 标量 → out 列表 | 同输入 | tensor_list |
| ForeachExp | ops-nn `foreach/foreach_exp` | 1 进 1 出：x 列表 → y 列表 | 同输入 | tensor_list |
| ForeachExpm1 | ops-nn `foreach/foreach_expm1` | 1 进 1 出：x 列表 → y 列表 | 同输入 | tensor_list |
| ForeachNeg | ops-nn `foreach/foreach_neg` | 1 进 1 出：x 列表 → y 列表（**支持空 TensorList**） | 同输入 | tensor_list |
| Logit | ops-nn `loss/logit` | 1 张量 + eps(double 标量) → 1 张量 | 同输入 | elementwise |
| InplaceSigmoid | ops-nn `activation/sigmoid` | 1 张量**原地读写**（CopyOut 直接覆盖 `selfGm`） | 同输入 | elementwise（inplace） |
| Relu | ops-nn `activation/relu` | 1 张量 → 1 张量 | 同输入 | elementwise |
| median | ops-nn `experimental/index/median` | **两个变体**：aclnnMedian 1 进 1 出（标量）／aclnnMedianDim 1 进 + dim/keepdim → **2 出**（values + indices INT64） | **沿 dim 归约**，keepdim 决定是否留轴；全局变体出标量 | reduction |
| IndexFillTensor | ops-nn `experimental/index`（改 `index/index_fill`） | 3 张量（self / index / value）+ dim(int64 attr) → 1 张量 | 同 self；但**写入位置看 index 的取值** | index_scatter |
| MaxUnpool3d | ops-nn（任务书 `index/gather_elements` ↔ 设计文档 `scatter_elements_v2`，**打架**） | 2 张量（self / indices）+ 3 个 aclIntArray（outputSize/stride/padding）→ 1 张量 | **属性公式**：D/H/W 由 `outputSize[3]` 定；落点看 indices 取值 | index_scatter |
| SyncBatchNormGatherStats | ops-nn `experimental/norm/sync_batch_norm_gather_stats` | **5 张量进**（[worldSize,C] / [worldSize] / [C] …）+ momentum/eps → **2~4 出**，且 mean/variance **原地更新** | **沿 worldSize 轴归约**：[worldSize,C] → [C] | fused_comm（归约形） |
| Sleep | ops-nn `control/sleep`（要求**新建**该目录） | **0 张量进 0 张量出**；唯一参数 `cycles`(int64 属性) | 无输出张量 | other |

#### 表 B2 · dtype 亮点与证据

| 算子 | dtype 亮点 | 出处 | 核实度 |
|---|---|---|---|
| ForeachAddListV2 | 加 INT16/INT8/UINT8；alpha 与 tensor dtype **成对映射**（tensor int16→alpha int32 等） | `ForeachAddListV2_task_doc.md:13,19` + `tasklist/04-10-.../NoOne/docs/design.md:50-70` | verified(单源·非官方) |
| ForeachAddScalarV2 | 加 INT16/INT8/UINT8；scalar 侧 `FLOAT16,FLOAT32,FLOAT32,INT32,INT32,INT32,INT32` | `ForeachAddScalarV2_task_doc.md:13,19` + `tasklist/04-11-.../GoOn/docs/design.md:59-61,200` | **inferred**（scalar vs scalar_list 未决） |
| ForeachMulList | 加 INT16/INT8/UINT8；现状 4 类 fp16/fp32/int32/bf16；tiling key 5/7/8 | `ForeachMulList_task_doc.md:13,19` + `tasklist/04-16-.../风车车/docs/design.md:33-45` | verified(单源·非官方) |
| ForeachRoundOffNumberV2 | **只加 INT16**（与其余 7 个 Foreach 不同）；roundMode 恒 INT8 | `ForeachRoundOffNumberV2_task_doc.md:13,19` + `tasklist/04-21-.../NoOne/docs/design.md:62-78,142,172-173` | verified(单源·非官方) |
| ForeachSubListV2 | 加 INT16/INT8/UINT8；**alpha 含 DOUBLE / INT64**；host 侧按 tensor dtype 转 alpha | `ForeachSubListV2_task_doc.md:13,19` + `tasklist/04-12-.../Krazy队/docs/design.md:17-22,113-114` | verified(单源·非官方) |
| ForeachExp | 加 INT16/INT8/UINT8；现状 **只 3 浮点、无 int32**（与 MulList/Neg 现状集不同） | `foreach_exp_task_doc.md:13,19` + `tasklist/04-18-.../GoOn/docs/design.md:18-19,74,80,174` | verified(单源·非官方) |
| ForeachExpm1 | 加 INT16/INT8/UINT8；现状 fp16/fp32/bf16 | `foreach_expm1_task_doc.md:13,19` + `tasklist/04-19-.../GoOn/docs/design.md:56-69` | verified(单源·非官方) |
| ForeachNeg | 加 INT16/INT8/UINT8；现状 4 类 | `foreach_neg_task_doc.md:13,19` + `tasklist/04-17-.../蓝的盆/docs/design.md:64-67` | verified(单源·非官方) |
| Logit | 加 INT16/INT8/UINT8；现状 fp16/fp32/bf16。eps 是 **double** | `Logit_task_doc.md:13,19` + `tasklist/04-8-Logit/天辰天辰/docs/design.md:42-44`、`菜鸟/docs/design.md:53-57,94` | verified(单源·非官方) |
| InplaceSigmoid | `FLOAT, FLOAT16, BFLOAT16, INT16, INT8, UINT8`（任务书自带参数表） | `InplaceSigmoid_task_doc.md:13,26-27,31,39` + `tasklist/05-19-.../GoOn/docs/design.md:51-52,87,114` | verified |
| Relu | `FLOAT, FLOAT16, INT8, INT16, INT32, INT64, BFLOAT16`（**含 INT64**） | `Relu_task_doc.md:13,26-27,31,39` + `tasklist/06-18-.../gcw_fiAo4tDr/docs/design.md:43-44,59,96` | verified |
| median | input 8 类含 int64/uint8/int8；**indices 恒 INT64** | `median_task_doc.md:13,19,28` + `tasklist/04-9-Median/看不见我/docs/design.md:21-22,54-66,109` | verified(单源·非官方) |
| IndexFillTensor | 现状 6 类 + 新增 **int16/int8/uint8/DOUBLE**；index INT32/INT64；共 10 类 | `IndexFillTensor_task_doc.md:13,14,28-32,45` + `tasklist/05-20-.../Aeolion/docs/design.md:27-31,96-100` | verified |
| MaxUnpool3d | self 加 **bf16**；indices **int32/int64** | `MaxUnpool3d_task_doc.md:13,19,28` + `tasklist/04-13-.../newnew/docs/design.md:11-21,53-120,133` | verified(单源·非官方) |
| SyncBatchNormGatherStats | sampleCount 从**仅 INT32** 扩到 fp16/fp32/int32；其余 fp16/fp32；momentum/eps 任务书列在「输入」但无 dtype | `SyncBatchNormGatherStats_task_doc.md:6,41-102,106-107,116,124-127` + `tasklist/05-07-.../xiao--hai/docs/design_docs_*.md:27-39,111` | verified |
| Sleep | **无张量 dtype**；`cycles` 是 int64。任务书原话「性能要求：不涉及 / 精度要求：不涉及」 | `Sleep_task_doc.md:19,22-24,27-28,37,41` + `tasklist/04-6-Sleep/今天要吃三碗饭/docs/design.md:20,56,89` | verified |

### 1.3 其余 6 仓组 —— 7 份任务书 / 7 个算子（本轮新增）

> ⚠ 7 个里**只有 3 个能核仓内**（ops-cv 两个 Upsample 做到了任务书 ↔ `op_def` **双源**；
> SPMV 做到了任务书 ↔ 仓内 README/源码，但它不是 op_def 算子、没有 `AddConfig` 可核）。
> `ops-collections` / `ops-transformer` / `ops-solver` **三仓未 clone**；`ops-blas` 里**搜不到任何 trsm 实现**。

#### 表 A3 · 形态

| 算子 | 仓 | 输入 / 输出 | 输出 shape 从哪来 | shape_class |
|---|---|---|---|---|
| UpsampleNearestExact1d & 2d | ops-cv `image/upsample_nearest` | 1 张量 → 1 张量；outputSize/scales 在 op_def 层是**属性** | **属性公式**：(N,C,L)→(N,C,outputSize) | shape_transform |
| UpsampleNearest3d | ops-cv `image/upsample_nearest3d` | 1 张量(rank 5) → 1 张量 | **属性公式**：D/H/W 由 `outputSize[3]` 定，N/C 沿用 | shape_transform |
| SPMV | ops-sparse `src/spmv/arch22` | **5 进 1 出且 y 是 in-out**：CSR 三数组 + x[K] + y[M]（承载 `beta*y` 旧值）；**aclsparse 库 API**，带 handle/描述符 + externalBuffer + **三阶段调用** | y 长 = M（trans 时变 N）；**看 CSR 结构** | sparse_linalg |
| aclblasTrsmBatched | ops-blas `experimental/aclblasTrsmBatched`（**仓内无实现**） | 2 进 0 出，**结果原地写回 b[]**；a[]/b[] 是 batchCount 个矩阵的**device 指针数组** | 同输入（原地） | other |
| aclsolverCheevj | ops-solver `experimental`（**仓未 clone**） | 1 矩阵进 → **最多 3 出**：a 被特征向量 V **原地覆盖** + w[n] + info(标量) | 多输出、且**输出集合由 jobz 属性决定**（'N' 不产 V） | other |
| SlidingTileAttention | ops-transformer `experimental/attention`（**仓未 clone**） | 3 张量(q/k/v, BNSD) → 1 张量 | 同输入 | other |
| dynamicMap | ops-collections（**仓未 clone**；任务书内部把合入路径写成 ops-transformer，**自相矛盾**） | **没有张量 IO**——是 device 侧并发哈希表**容器类**，暴露 Insert/Erase/Find/Contains/Reserve/InsertOrAssign | 无（Find 输出长度 = 入参 keyNum；Insert 返回依赖数据的标量计数） | other |

#### 表 B3 · dtype 亮点与证据

| 算子 | dtype 亮点 | 出处 | 核实度 |
|---|---|---|---|
| UpsampleNearestExact1d & 2d | 任务书要 fp32/fp16/bf16 + **UINT8**；仓内 op_def 现状**只 3 浮点**、且**没有 ascend950 config** → uint8 确实未做 | `UpsampleNearestExact1d&2d_task_doc.md:6,26-29,35-39,52`；`upsample_nearest_def.cpp:26,31,38-39`（**我实读**） | verified（**双源**） |
| UpsampleNearest3d | 任务书要 fp32/fp16/bf16 + **DOUBLE + UINT8**；仓内 uint8/double **只在 `AddConfig("ascend950")` 的 regbase config**，A2/A3 两个 config 仍只 3 浮点 | `UpsampleNearest3d_task_doc.md:6,26-31,35,44`；`upsample_nearest3d_def.cpp:19-21,31,37,46-47,71-72,77`（**我实读**） | verified（**双源·冲突**，见 §5.4） |
| SPMV | 任务书要 **int8**（csrVal/x）；仓内 A2 实现是 **int32**。compute_type 决定 (输入,计算,输出) **三元 dtype 组合**是否合法 | `SPMV_task_doc.md:6,14,30-36,44-85,95-103,106,119-120,145`；`ops-sparse/test/spmv/README.md`；`src/spmv/arch22/kernels/spmv_kernel_*.cpp` | verified（**双源** ，冲突已记） |
| aclblasTrsmBatched | S 版 float32；**C 版 complex64**（`complex<float>`）；alpha 相应实/复 | `aclblasTrsmBatched_task_doc.md:6,24-37,43-56,61,70-72,79-80,89-98,122-123` | verified（任务书单源，仓内无实现） |
| aclsolverCheevj | **单算子内混三种**：输入 complex64 + 特征值 float32 + info int | `aclsolverCheevj_task_doc.md:6,13,20-32,37,41-44,50-54,64-69,73-74,98` | verified（任务书单源，仓未 clone） |
| SlidingTileAttention | 只 bf16 / fp16（**44 个里唯一 dtype 面完全落在 runner v1 三浮点内的张量算子**）；中间累加 fp32 | `SlidingTileAttention_task_doc.md:6,13,19,30-33,37,61`（**全文唯一接口信息是 :19 那一句**）+ `tasklist/04-1-.../我是谁/docs/design.md:35,42-49,78-86,98-99,141-146` | **inferred** |
| dynamicMap | key/value 要 **uint16 / uint32 / uint64 / float32** | `dynamicMap_task_doc.md:6,7,17,27,49,58-59,154-155`（+ `:179,:185` 与 `:7` **仓名自相矛盾**）+ `tasklist/05-05-DynamicMap/Dryoung/docs/design.md:105-126` | verified（任务书单源·内有矛盾） |

### 1.4 汇总计数

#### shape_class 分布（44 个，互斥分桶）

| shape_class | 个数 | 占比 | 算子 |
|---|---|---|---|
| **elementwise** | **15** | 34% | Equal, IsClose, Neg, Sign, FmodTensor, FmodScalar, Gcd, RightShift, InplaceRsqrt, Polar, AngleV2, Cast, **Relu, InplaceSigmoid, Logit** |
| **tensor_list** | **8** | 18% | 8 个 Foreach 全族 |
| **index_scatter** | 5 | 11% | bincount, EmbeddingDenseGrad, MaxUnpool2d, **MaxUnpool3d, IndexFillTensor** |
| **other**（含无张量 IO / 非算子 / 线性代数） | 5 | 11% | Sleep, **aclblasTrsmBatched, aclsolverCheevj, SlidingTileAttention, dynamicMap** |
| **reduction** | 4 | 9% | Pdist, MinDim, MaxDim, **median** |
| **shape_transform** | 3 | 7% | im2col, **UpsampleNearestExact1d&2d, UpsampleNearest3d** |
| **generator** | 2 | 5% | Arange, logspace |
| **sparse_linalg** | 1 | 2% | SPMV |
| **fused_comm** | 1 | 2% | SyncBatchNormGatherStats |
| 合计 | **44** | 100% | |

> 粗体 = 本轮新增。`tensor_list` 与 `fused_comm` 是本轮新加的两格。
> `fused_comm` 我保留了 ops-nn 组自报的标签，但它的 `output_shape_source` 报的是 `reduction`——
> 形态上它就是「跨 worldSize 轴归约」，只是带分布式语义。**这个标签值不值得单独存在，需人裁**（§5.4）。

#### 卡点交叉计数（同一个算子可被多道闸卡住）

| 卡点 | 被卡个数 | 说明 |
|---|---|---|
| **dtype 面超出 runner v1 三浮点** | **41 / 44** | 只剩 Pdist、SlidingTileAttention 全在 `{fp32,fp16,bf16}` 内；Sleep 无张量不适用 |
| **输出形状 ≠ 输入广播**（闸 1 硬拒） | **17 / 44** | Pdist, MinDim, MaxDim, Arange, logspace, bincount, EmbeddingDenseGrad, MaxUnpool2d, im2col, median, MaxUnpool3d, SyncBN, 两个 Upsample, SPMV, Cheevj, dynamicMap（+ Sleep 无输出 = 18） |
| **张量列表（一个参数 = N 个张量）** | 8 / 44 | Foreach 全族 |
| **attr 是数组/结构体、不是标量** | 7 / 44 | im2col, MaxUnpool2d, MaxUnpool3d, 两个 Upsample, SlidingTileAttention(window_size), Cheevj(syevjInfo_t) |
| **in-out（某输入同时是输出、旧值参与计算）** | 6 / 44 | InplaceRsqrt, InplaceSigmoid, SyncBN, SPMV(y_vec), TrsmBatched(b[]), Cheevj(a) |
| **多输出** | 5 / 44 | MinDim, MaxDim, median(Dim 变体), SyncBN(2~4), Cheevj(w+V+info) |
| **各输入 shape 互不相同且相互约束** | 5 / 44 | SyncBN([worldSize,C]/[worldSize]/[C]), SPMV, IndexFillTensor, TrsmBatched, Cheevj |
| **索引/结构化输入不能随机造** | 5 / 44 | bincount, EmbeddingDenseGrad, MaxUnpool2d, MaxUnpool3d, IndexFillTensor（+ SPMV 的 CSR 自洽、Cheevj 的 Hermitian、Trsm 的三角非奇异 = 8） |
| **根本没有数值判据** | 2 / 44 | Sleep（时序断言）、dynamicMap（有状态容器，判的是操作序列） |
| **今天就能加进来（三个文件）** | **6 / 44** | Equal, IsClose, Neg, Sign（已验收）+ FmodTensor（差 golden/runner）+ InplaceRsqrt（要改骨架） |

---

## 2. 当前引擎的七道硬闸（后面各节都引用这里）

前四道是上一轮实读代码确认的（本轮我复核了行号，**有 ±1 漂移，下表用本轮的号**）；
后三道是本轮两组新数据逼出来的（子 agent 报的行号我抽查了 `gen_cases.py:374` 一处，确认存在）。
它们全是**结构性**的——不是调参数、加 dtype 就行。

| 闸 | 在哪（2026-07-22 实读） | 卡什么 | 被卡 |
|---|---|---|---|
| **闸 1 · 输出形状** | `repo_adapter.py:495` `out_shape = np.broadcast_shapes(*[a.shape for a in arrs])`；`:501` `golden.shape != out_shape → raise` | 输出形状**只能**是各输入广播的结果 | 17 |
| **闸 2 · 输出 dtype** | `repo_adapter.py:498` 一带 `exp_dt = np.bool_ if verify_mode=="exact" else _NP[输入dtype]`；`precision_policy.py:199-204` `derive_output_dtype` | 输出 dtype 只有两条路：① 同输入；② spec 里 out 允许集是单值（恒 bool）。complex64 / 由 attr 决定 / 值与索引双 dtype → 保守 `ValueError` | Polar, AngleV2, Cast, MinDim/MaxDim, median, Cheevj … |
| **闸 3 · 输入构造** | `gen_cases.py:251-280` `_build_inputs`；广播只有硬编码的 `(4,1) vs (1,5)`；`repo_adapter.py:478` `any(inp["dtype"] != dtn) → raise` | 只认「1~2 个**同形同 dtype** 张量」。没有标量操作数、可空输入、per-input dtype | 大多数 |
| **闸 4 · dtype 白名单** | `gen_cases.py:33` `_NATIVE={float32,float16,int32,int16}`（+bf16 位级特判）；`repo_adapter.py:476` `runner v1 仅支持 {float32,float16,bfloat16}；dtype 属 Track C → raise` | 造得出 fp32/fp16/int32/int16/bf16；**真机 runner v1 只收三浮点**。int8/uint8/int64/uint16/uint32/uint64/bool/double/complex 全无 | **41** |
| **闸 5 · attr 只能是标量**（本轮新增） | `gen_cases.py:374` `raise ValueError(f"attr_matrix[{k_idx}].{k}={v!r} 非标量（须 bool/int/float/str）")` | `outputSize` / `stride` / `window_size` 这类 `aclIntArray` 塞不进去——**而它们恰恰是决定输出形状的那个 attr** | 7 |
| **闸 6 · 单输出**（本轮新增） | `gen_cases.py` 每 case 只 `np.save(.../"golden.npy", golden)`、`expected.golden_path` 单值；`repo_adapter.py` 每 case 只拉一个 `out.bin`；`validator` 单份比对 | 多输出算子只能验第一个输出，等于**放过一半判据** | 5 |
| **闸 7 · 全输入共用一个 shape / 无 in-out**（本轮新增） | `_build_inputs(rng, in_params, shp, dtn, attrs, data_kind)` —— 一个 `shp` 派给所有输入；caseset 的 inputs 与 expected 严格分离 | 各输入不同 shape、shape 变量联动（worldSize/C）、以及「同一 buffer 既进又出」都写不下来 | 5 + 6 |

**另外有一个静默截断（上一轮实测复现，本轮两组均未复测）**：`_build_inputs` 在 arity ≥ 3 时，
常规数据路径只产 2 个输入就返回，第 3 个被无声丢掉；而 `empty` 路径按 arity 产满。
后果：给 3 输入算子（bincount、SPMV、IndexFillTensor、SyncBN…）写 spec 不会报错，
而是**悄悄少造一个输入**——违反本仓「fail-closed 优于静默降级」。**建议单开一个修复项。**

**还有一个与「零硬编码」约定直接冲突的点（本轮我实读确认）**：
`plugin/acc-common/new_example/run_on_npu.sh:24` 把 vendor 目录后缀写死成 `_math`
（`V="$OPP/vendors/${VEN}_math"`），而 `repos/ops-cv/build.sh:1508-1511` 产出的是 `${VENDOR}_cv`。
→ 现状下 new_example 跑 ops-cv 会去找一个不存在的 vendor 目录。**这个后缀应从仓名/构建产物探测而来。**

---

## 3. 按 shape_class 分组

### 3.1 elementwise —— 15 个（34%）

`Equal / IsClose / Neg / Sign / FmodTensor / FmodScalar / Gcd / RightShift / InplaceRsqrt / Polar /
AngleV2 / Cast / Relu / InplaceSigmoid / Logit`

这是当前流水线的舒适区，但**15 个里只有 6 个真能跑**。下表是把形态字段套进 §2 七道闸得出的**判断**
（闸的行号可查、形态字段来自表 A/B）：

| 算子 | 现在能跑吗 | 卡在哪 |
|---|---|---|
| Equal / IsClose / Neg / Sign | ✅ 能（已有真机验收记录） | dtype 覆盖被闸 4 削一刀（int8/uint8/uint32/int64 造不出） |
| FmodTensor | ✅ 结构上能，缺 golden + runner 两个文件 | 任务书要的 int16 受闸 4（runner v1 无 int 分支） |
| **Relu** | ⚠ 结构上能（1 进 1 出、同形、无 attr），缺三个文件 | 闸 4：任务书 dtype 含 INT8/INT16/INT32/**INT64** —— **本轮新增里最接近「三文件就能加」的一个** |
| InplaceRsqrt / **InplaceSigmoid** | ⚠ 形状/dtype 过闸，但 runner 骨架是 **out-of-place 双张量**（`oprunway_sign_runner.cpp:80-81` 分别建 x 和 y） | 闸 7：原地读写要改骨架，或约定「拷一份再调 inplace 接口」——**但那就验不到 inplace 本身写没写回** |
| **Logit** | ❌ | 闸 5 边缘：`eps` 是 **double** 标量属性（`bool/int/float/str` 里 float 能塞，但 double 精度语义要确认）；闸 4：要 int16/int8/uint8。另有「eps 是输入还是属性」两份设计文档打架 |
| Gcd / RightShift | ❌ | 闸 4：纯整型（RightShift 到 uint64），runner v1 只收三浮点 |
| Polar / AngleV2 | ❌ | 闸 2：complex64 进或出 |
| Cast | ❌ | 闸 2：输出 dtype 由 attr `dst_type` 决定 → `derive_output_dtype` 判歧义、直接 `ValueError` |
| FmodScalar | ❌ | 闸 3：`other` 是 `aclScalar`，`_build_inputs` 把每个 in 参数都当同形张量造 |

**spec schema 要加什么**（Layer 0）：

1. `params[].kind`：区分 `tensor` / `scalar` / `attr_shaped`。现在只有 `io: in|out|attr`，
   标量操作数无处安放（FmodScalar 的 `other`、Arange 的 start/end/step）。
2. `params[].optional` / `nullable`：bincount 的 `weights` 可传空指针。
3. **per-param dtype 绑定**：现在 dtype 集只从 `self`（或第一个 in 参数）取一份、全体输入共用；
   `repo_adapter.py:478` 对不同 dtype 直接 raise。EmbeddingDenseGrad 的 `grad=FLOAT + indices=INT32`
   表达不了；SyncBN 的 `sampleCount` 可以是 INT32 而其余是 FLOAT，也表达不了。
4. **输出 dtype 规则**要能写「由某 attr 决定」而不只是「同输入 / 单值」——否则 Cast 这类永远歧义。
5. `dtype` 词表要扩到 int8/uint8/int64/uint16/uint32/uint64/bool/double/complex64。
6. **`inplace: true` 与「原地是否要单独验」**：InplaceRsqrt / InplaceSigmoid 两个都写着「实际操作同一内存」，
   spec 得能声明它，runner 得能读回同一 buffer 再比。

**runner 骨架要加什么**（Layer 1/2）：

1. manifest 行格式要能带 **per-input dtype**（现在一行只有一个 dtype）。
2. ACL dtype 分支扩表（现在 `_NP` 只有三个浮点）。int / complex 分支要从算子自己的 example 抠 + 真机验证。
3. inplace 骨架变体（读写同一块 device 内存）。
4. 标量操作数怎么传：manifest 里当 attr 传，还是单独一列。

---

### 3.2 tensor_list —— 8 个（18%，本轮新增的第二大类）

`ForeachAddListV2 / ForeachAddScalarV2 / ForeachMulList / ForeachRoundOffNumberV2 /
ForeachSubListV2 / ForeachExp / ForeachExpm1 / ForeachNeg`

⚠ **这 8 个的形态全靠参赛者设计文档（非官方）**，`repos/ops-nn` 未 clone、`op_def` 一个没核。

**它们不是同构的**，别一刀切（这是本轮最容易被误读的地方）：

| 子形态 | 算子 | 差别 |
|---|---|---|
| 纯一元列表 | Exp / Expm1 / Neg | 1 列表进 1 列表出，无标量 |
| 二元列表 | MulList | 2 列表进，**无 alpha** |
| 二元列表 + alpha | AddListV2 / SubListV2 | 多一个 alpha **标量张量**（SubList 的 alpha 声明还含 DOUBLE/INT64） |
| 列表 + 标量 | AddScalarV2 | scalar 是 shape `[1]` 的张量……**还是一个标量列表？未决** |
| 列表 + 枚举输入张量 | RoundOffNumberV2 | roundMode 是 **INT8 输入张量**、不是 attr，**取值决定 golden 分支**（`CAST_FRAC` 时整型输出全 0，其余 roundMode 输出须逐位等于输入、0 误差） |

**卡在哪**（结构性）：

1. **闸 7 的根子上**：`gen_cases` 里一个 spec param 恒等于**一个** ndarray，落盘是 `x{j+1}.npy` 一个参数一份，
   caseset 的 inputs 项形如 `{name, shape, dtype, path}`。「一个参数 = N 个张量、各自 shape 不同、
   N 还可以是 0」这件事**结构上写不下来**。ForeachNeg 明确要求支持**空 TensorList**。
2. **输出个数是动态的**：输出 y 是 `DYNAMIC`，长度 = 输入列表长度 n。闸 6（单输出）挡死。
3. **n 是一根新的覆盖轴**：n=0（空列表）/ n=1 / n=大量小张量 / 列表内张量 shape 参差——
   这才是 Foreach 泛化验收的核心场景。当前 shape 阶梯 `_REG_SHAPES` 只有单张量的维度阶梯，没这一轴。
4. **闸 5 管不到「取枚举值的输入张量」**：RoundOffNumberV2 的 roundMode 是输入张量，
   `attr_matrix` 只在 `spec.params[io=='attr']` 上展开，对「某个输入张量遍历枚举值」无能为力。
5. **闸 4**：8 个全要 INT16/INT8/UINT8（RoundOffNumberV2 只要 INT16），runner v1 一个都收不下。

**spec schema 要加什么**：

1. `params[].is_list: true` + `list_len_axis`（把 n 声明成一根覆盖轴，含 `0`）。
2. 列表内每个张量的 shape 规则：`same_across_list` / `varied`（Foreach 的真实场景是 varied）。
3. 「某个输入张量是枚举量」这一档：`enum_values: [...]`，且允许 golden 按它分支。
4. 输出 arity = 某个输入的列表长度（`out_arity_from: x`）。

**runner 骨架要加什么**：

1. manifest 行要能表达「这一列是 n 个张量」——最省事的做法是 case 目录下放 `x1_0.bin … x1_{n-1}.bin`
   + manifest 带一列 `n` 与每个子张量的 dims。
2. runner 侧建 `aclTensorList`（而不是 `aclTensor`），并按 n 建同样长度的输出列表。
3. 比对侧要逐个子张量比，且**空列表要能过**（n=0 时不是「没跑」而是「合法地什么都不做」）。

---

### 3.3 index_scatter —— 5 个（11%）

`bincount / EmbeddingDenseGrad / MaxUnpool2d / MaxUnpool3d / IndexFillTensor`

**卡在哪**（这类三重卡）：

1. **输出形状**：bincount 长 = `max(输入最大值)+1`（**看数据内容**，官方 op_def 里 `size` 干脆是
   `ValueDepend` 输入）；EmbeddingDenseGrad 首轴 = `numWeights`(attr)；MaxUnpool2d/3d 由 `output_size` 定
   （3d 的 `outputSize` 还是长度 3 的 `aclIntArray` → 同时撞闸 5）。IndexFillTensor 例外：输出**形状**同
   self，只有**写入位置**依赖 index 取值——它是这 5 个里离通路最近的一个。
2. **输入数据不能随机造**：indices 必须是**合法索引**。`_make_varied` 对整型只在 `[-100, 100]` 附近的网格上
   随机取，而 IndexFillTensor 的 index 要求「元素值小于 self 对应 dim 的维度大小」（任务书原话），
   MaxUnpool3d 的 indices 必须落在 outputSize 展开后的合法范围内。**照现在这套造出来的索引必越界**——
   kernel 直接非法访问，golden 也算不出来。
3. **多 dtype 输入**：grad FLOAT + indices INT32（闸 3 在 `repo_adapter.py:478` 显式 raise）。

**spec schema 要加什么**：

1. 输入的**语义角色**：`role: data | index | weight`，并允许给 index 声明取值域
   （`range_from: {param: self, dim_from_attr: dim}` 之类）。**没有这个，用例生成器不可能造出合法索引。**
2. `output_shape: data_dependent` 这一档，并说明「靠什么算」——是 golden 算完才知道，
   还是 host 侧先扫一遍输入算出来。
3. 可空输入（bincount 的 weights）。

**runner 骨架要加什么**：

1. per-input dtype（见 3.1）。
2. 输出 shape 从 manifest 读，不能从输入 shape 推。
3. **数据依赖形状的部署顺序**：现在是 host 造完 golden → 校 shape → 部署 → 跑。数据依赖时 host 其实
   **可以**先算出输出长度（host 有输入数据），所以这条路是通的——把长度当 manifest 的一列传下去即可。
   **这点值得先确认再动手（建议）。**

---

### 3.4 reduction + fused_comm —— 5 个（11%）

`Pdist / MinDim / MaxDim / median`（reduction）+ `SyncBatchNormGatherStats`（fused_comm，形态上也是归约）

**卡在哪**：

- 闸 1 直接判死。Pdist 把 `(N,M)` 压成长度 `N*(N-1)/2` 的 1D，`broadcast_shapes` 算出来是 `(N,M)`，
  `:501` raise。**连部署都到不了。**
- 闸 6：MinDim/MaxDim 是 **2 输出**（值 fp16/fp32/bf16 + 索引 INT32）；median 的 `aclnnMedianDim` 是
  values + indices(INT64)；SyncBN 按任务书是 2 输出、按原 TBE 是 4 输出（**这个数需人裁**，§5.4）。
- 闸 7：SyncBN 的 5 个输入 shape 互不相同且**共享形状变量**（`[worldSize,C]` / `[worldSize]` / `[C]`），
  而且 mean/variance 是**原地更新**。
- 属性驱动形状：MinDim/MaxDim/median 的输出形状随 `dim` + `keepdim` 变，闸 1 也不认。
- **median 还多一层**：一个算子名对应**两个 aclnn 变体**（全局出标量 / 按轴出两个），
  spec 是 per-op 单形态，**没有「变体 / overload」这根轴**；硬拆成两个 op 又和任务书/PR 的一一对应脱节。

**spec schema 要加什么**：

1. `output_shape`（或 `shape_rule`）字段：至少要能表达 `same_as_input` / `broadcast` / `reduce(dim, keepdim)` /
   `formula` / `data_dependent` 五档。**这是整份分类学最核心的一个新字段。**
2. 多输出：`params` 里允许多个 `io: out`，每个带自己的 dtype 与 shape 规则。
3. 哪些 attr 是「影响形状的」要显式标出来（`dim`/`keepdim` 影响形状，`p` 不影响）——
   现在 `attr_matrix` 只在**一个代表 (dtype, shape)** 上改语义值，压根没有「attr 变了形状跟着变」这条路。
4. **变体 / overload 轴**（median）。
5. **形状变量**（SyncBN 的 worldSize / C）：允许在 spec 里声明符号维度，并让多个输入引用同一个符号。

**runner 骨架要加什么**：

1. 输出张量 shape 不能再复用输入 shape（`oprunway_sign_runner.cpp:81` 现在就是拿 `c.shape` 建 y）。
2. 多输出：写 `out1.bin` / `out2.bin`，`validator` 逐个比、允许不同 dtype 与不同判定口径
   （索引通常 exact，值走数值容差）。
3. 索引类输出的判定有个坑：**并列最值时索引可以合法地不同**——需人裁的口径问题，不是代码问题。
   median 的任务书还额外规定「偶数长度取下中位数、indices 取首个等值下标」，**这是可判定的**，
   与 MinDim/MaxDim 的并列歧义不同，别混为一谈。

---

### 3.5 shape_transform —— 3 个（7%）

`im2col / UpsampleNearestExact1d&2d / UpsampleNearest3d`

**这一组是「性价比最高的下一刀」**：3 个都是 1 进 1 出、同 dtype、无多输出、无 in-out，
唯一的结构性缺口就是「输出形状由属性公式推」+「attr 是整数数组」两条。

**卡在哪**：

- 闸 1：输出 H/W(/D) 由属性公式推（im2col 由 ksize/stride/dilation/pad；Upsample 由 `output_size`）。
- 闸 5：`output_size` / `stride` / `padding` / `ksize` 都是 `aclIntArray`，`gen_cases.py:374` 直接 raise。
  **而 `output_size` 恰恰就是决定输出形状的那个 attr——这是最致命的一条。**
- 输入 rank 被锁死（im2col 3D/4D、Exact1d 3、Exact2d 4、Nearest3d 5），而 `_plan` 的 shape 阶梯自由生成
  1~4 维、还无条件强塞空 Tensor `(0,)` 与标量 `(1,)` 条目——**两个 Upsample 的任务书都明写「不支持空 Tensor」**，
  那条强制条目与任务书直接冲突，会造出一堆非法用例。
- 闸 4：im2col dtype 谱 15 类（含 complex32/64/double/uint64）；两个 Upsample 的**任务增量本身就是 uint8**，
  UpsampleNearest3d 还要 double。**也就是说，就算把形状问题解决了，这两个任务的核心 dtype 仍然造不出来。**

**spec schema 要加什么**：

1. `input_rank_constraint`（`exactly: [3,4]` / `exactly: 5` / `exactly: 2`(Pdist) / `exactly: 1`(bincount)），
   **并且要能声明 `allow_empty_tensor: false`**。没有这个，shape 阶梯会给这些算子造非法 shape，
   **而且不会报错**——直到真机才炸。
2. 输出 shape 规则 `formula`，且要能引用 attr 名。这里有个取舍：把公式写进 spec（要设计一个小表达式语言）
   还是写进 per-op 的 golden.py（简单但知识散落）。**这是需要人拍板的设计分叉，我不替主控定。**
3. attr 值类型放开到 `list[int]`，并让 shape 推导能读它。
4. attr 里区分「影响形状的」与「只影响语义的」（同 3.4）。Upsample 的 `scales_*` 与 `exact_mode` 只影响语义，
   `output_size` 影响形状。

**runner 骨架要加什么**：输出 shape 独立传（同 3.4 第 1 条）；`aclIntArray` 类型的 attr 怎么在 manifest
里编码（现在 attr 被拍成位置字符串列表）。

---

### 3.6 generator —— 2 个（5%）

`Arange / logspace`

**卡在哪**：

- logspace **一个输入张量都没有**（`log_space_def.cpp:27-39` 注释原话就是「纯生成类算子：无输入 tensor」），
  四个标量全是 REQUIRED attr。`gen_cases` 从「第一个 in 参数」取 dtype 起步，没有 in 参数就无从开始；
  `derive_output_dtype` 也要求 in/out 都在（`precision_policy.py:184-185`），缺 in 直接 `ValueError`。
- Arange 的三个输入全是 `.Scalar()`。闸 3 会把它们当三个同形张量造——而且因为那个静默截断，**只造出 2 个**。
- 两者输出长度都由**数值**决定（`ceil((end-start)/step)` / `steps`），闸 1 不认。

**spec schema 要加什么**：允许 `params` 里没有 `io: in` 的张量；输出 shape 规则支持 `from_attrs`（logspace）
与 `from_scalar_values`（Arange）；用例轴从 `dtype × shape` 换成 `dtype × (start,end,step) 组合`
（`_plan` 整个是按 shape 阶梯铺的，得另开一条铺法）。

**runner 骨架要加什么**：不读 `x*.bin`，只读 manifest 里的标量，直接建输出张量；
输出 shape 由 host 侧算好写进 manifest 更稳——**别让 runner 自己推**，
否则 golden 与 NPU 输出可能按两套公式算，对不上时分不清是算子错还是推形错。

---

### 3.7 sparse_linalg —— 1 个（2%）

`SPMV`（ops-sparse）

> 上一轮这条是「只回了 1 条记录的桩」，信息很薄；**本轮已换成实读结果**，上一轮的 dtype 记法作废（§0.3）。

**它不只是「形态特殊」，它根本不是同一类工程制品**：

- 不是 op_def 算子，是 **aclsparse 库 API**：带 handle / `aclsparseConstSpMatDescr_t` / `DnVecDescr_t` /
  externalBuffer，且是**三阶段调用**（GetBufferSize → Preprocess(可选) → SpMV）。
- 输入是 **CSR 三数组**（rowPtr 须单调非减且首元 0、colInd 须落在 `[0,N)`、NNZ 与稀疏度联动）+ 稠密向量 x
  + **in-out 的 y**（承载 `beta*y` 旧值）。闸 3/4/7 全违反。
- 任务书要求覆盖**稀疏度 50%~99.9%**——这是当前完全没有的生成维度。
- **性能没有内置 TBE 基线可比**，任务书给的基线是 **GPU A100 数字**（要 0.5×A100）。
  这是性能通路的结构性缺口，不只是 adapter 的事。

**能不能走现成 new_example 通路**：**不能，必须新 adapter**。ops-sparse 的 `build.sh` 没有
`--vendor_name` / `--experimental`（只有 `--pkg --ops --soc`），产物是库 + `test/<算子>/…_test.cpp` 可执行，
不往 `opp/vendors/` 装 custom vendor；而 `run_on_npu.sh` 整条编排是围绕 custom-opp-vendor 形态设计的。
**好消息**：`ops-sparse/test/spmv/spmv_test.cpp` 自带 `GenerateCsr` + `SpmvCpu`/`SpmvTransCpu` CPU golden
与 MARE/MERE 判定。新 adapter 的正确姿势大概是「驱动仓自带的 test 可执行 + 喂它的参数文件」，
**而不是自造 runner**。

---

### 3.8 other —— 5 个（11%）

`Sleep / aclblasTrsmBatched / aclsolverCheevj / SlidingTileAttention / dynamicMap`

这一格里的 5 个彼此毫无共性，逐个说：

| 算子 | 为什么进 other | 需要什么 |
|---|---|---|
| **Sleep** | **0 进 0 出**，没有可比的数值。任务书原话「精度要求：不涉及 / 性能要求：不涉及」，唯一判据是**时序断言**「实际延时周期数 ≥ 设定值」 | 只能走 `verify_mode: behavioral`。⚠ 这条通路在 Layer 1 **实现了多少我没查**（§5.5） |
| **dynamicMap** | **根本不是算子**，是 device 侧并发哈希表**容器类**，验收本质是**操作序列**（构造 → 多批 Insert 触发扩容 → 按 MatchingRate 查 → Erase → 再查），容器状态跨调用累积 | 「一个 case = 一串有状态调用」这个模型当前**连表达形式都没有**。性能基线也是 A100 |
| **aclblasTrsmBatched** | 输入是 **device 指针数组**（batchCount 个矩阵），结果**原地写回 b[]**；四个 char 型模式旗标（side/uplo/transa/diag）要全排列覆盖，其中 `side` **决定 A 是 m×m 还是 n×n** | 指针数组形态 + in-out + 「attr 决定输入形状」。且输入必须是**三角且对角非零**，随机造不出来 |
| **aclsolverCheevj** | 3 输出 + **jobz 决定输出个数**；输入 complex64 被特征向量原地覆盖；输入必须 **Hermitian** | 除上述外还有一条**任务书自身的口径漏洞**（§5.4）：有简并特征值时特征向量不唯一，「与 python 结果一致」逐元素不可能成立 |
| **SlidingTileAttention** | 3 张量注意力；`window_size` 是长度 `3*N` 的整数数组、逐 head 不同；`seq_shape` 是字符串（如 `"30x48x80"`）**决定 tile 几何 → 决定 golden** | 闸 5（数组 attr）+「字符串 attr 参与 golden 计算」。**但它的 dtype（bf16/fp16）与输出形状（同输入）都在通路内**——是本组唯一「只卡 attr 一条」的 |

---

## 4. 一张图：从「加算子 = 三个文件」到「要改引擎」的分界线

```
                  这是张量算子吗？
                   ├── 否 ──▶ Sleep / dynamicMap：整条数值验收链不适用，走 behavioral / 操作序列（2 个）
                   └── 是 ──▶ 一个参数 = 一个张量吗？
                               ├── 否 ──▶ 闸 7 根子：tensor_list 表达位（8 个 Foreach）
                               └── 是 ──▶ 输出形状 = 输入广播？
                                           ├── 否 ──▶ 闸 1：spec 加 output_shape 规则 + repo_adapter 不再硬算 broadcast
                                           │           （reduction 4 + fused_comm 1 + generator 2 + index_scatter 5
                                           │            + shape_transform 3 + sparse 1 + other 1 = 17 个）
                                           └── 是 ──▶ 单输出、非原地、attr 全标量？
                                                       ├── 否 ──▶ 闸 5/6/7（多输出 5 / in-out 6 / 数组 attr 7，有重叠）
                                                       └── 是 ──▶ dtype 在 {fp32,fp16,bf16}？
                                                                   ├── 是 ──▶ ✅ 三个文件就行（6 个）
                                                                   └── 否 ──▶ 闸 4：扩白名单 + runner ACL 分支（41 个都沾）
```

---

## 5. 待核清单

### 5.1 仓没 clone、根本读不到的

- **`repos/ops-nn` 没有 clone**（我实核）→ **ops-nn 那 16 个算子一个 `op_def` 都没核**。
  8 个 Foreach 的「是张量列表」全靠参赛者设计文档。**落 spec 前必须 clone ops-nn 交叉核
  `op_host/*_def.cpp` 的 `ParamType(DYNAMIC)`。**
- **`ops-collections` / `ops-transformer` / `ops-solver` 三仓没有 clone** → dynamicMap /
  SlidingTileAttention / aclsolverCheevj 三条**仅任务书为据**，仓内工程形态、是否已有实现，一律未核。
- **`ops-blas` 里搜不到任何 trsm 实现**（`grep -ril trsm` 只命中 skill 文档与 CI 脚本，`experimental/` 是空的）。
  与任务-PR map 记的「PR #243 open」一致。
- `experimental/math/bincount`、`experimental/math/right_shift` **不在本机 clone 里**（对应 PR 仍 open）。
  表 B1 记的是**官方 `math/` 版本**的 op_def，与社区任务书声明有出入。
- MinDim/MaxDim、EmbeddingDenseGrad **仓内无实现**，形态只有任务书单源。

### 5.2 标了 inferred、载重前必须核的（3 个）

1. **MaxUnpool2d**：`io_arity` / `input_rank` / `output_shape_source` / `attrs` **全是按算子名 + PyTorch
   同名语义推的**。任务书只写了「self 增 bf16、indices int32/int64」，仓内也没有实现 PR（只有设计文档 #2831）。
2. **ForeachAddScalarV2**：任务书两处（`:19`、`:56`）把目录写成 `foreach/foreach_add_scalar_list`，
   字面像 PyTorch 的 `_foreach_add.ScalarList`（**每个张量配一个标量**）；设计文档通篇写 `foreach_add_scalar`、
   `opFile.value = "foreach_add_scalar"`，scalar 的 Shape 明确是 `[1]`（**单个**）。
   **这两种语义造出来的用例完全不同。**
3. **SlidingTileAttention**：任务书**没有参数表**，全文关于接口只有 `:19` 一句「数据类型 bfloat16 和 float16、
   数据格式 BNSD」。io_arity / rank / 输出形状全靠参赛者设计文档。

### 5.3 任务书自身有毛病 / 两源打架（需人核，8 处）

1. **UpsampleNearest3d 的硬件 ↔ dtype 双源冲突（本轮最需人核的一条，我实读确认）**：
   任务书 `适配硬件` = Atlas A2/A3，但 `op_def` 里 UINT8+DOUBLE **只在 `AddConfig("ascend950")` 那一支**，
   A2/A3 两个 config 仍是 `{FLOAT, FLOAT16, BF16}`。→ 「uint8 已支持」只在 950 通路成立。
   这任务到底是「在 A2/A3 上补 uint8」还是「已被 950 批量 PR 覆盖、任务已失效」？
   **对照组**：UpsampleNearestExact1d&2d 的 op_def **完全没有** ascend950 config、其 uint8 确实未做——
   **两者状态不同，不能混为一谈**。
2. **dynamicMap 任务书仓名自相矛盾**：`:7` 写开源仓 `cann/ops-collections`，但 `:179` 合入路径与 `:185`
   代码样例都写 `ops-transformer/tree/master/experimental/attention`（与 SlidingTileAttention 任务书**一字不差**）。
   几乎肯定是复制粘贴漏改。**若工作流按「合入路径」字段推 PR 目标仓，会推错仓。** 建议入 `task_pr_gaps`。
3. **SPMV 的 int8 vs int32**：任务书要 int8，仓内 A2 实现是 int32；且 `src/spmv` 只有 arch22、
   任务目标是 Ascend 950PR（而 `src/spmm` 已有 arch35）→ 这是「**跨 arch 移植 + 换 dtype**」双重工作，
   不是加一个 dtype。
4. **MaxUnpool3d 底层是 gather 还是 scatter**：任务书 `:19` 叫人改 `index/gather_elements`；
   设计文档 `:133` 说「底层调用的是 `scatter_elements_v2`」；任务-PR map 那一行本来就记「未找到主开发 PR」。
   **三方说法不一致。**
5. **SyncBatchNormGatherStats 的输出到底 2 个还是 4 个**：任务书参数表只列 batchMean/batchInvstd；
   设计文档说原 TBE 是「5 输入 + 4 输出」（另有 mean_update / variance_update），本次按 aclnn 接口改成原地更新。
   **验收按哪一版算，得定。**
6. **aclsolverCheevj 的精度口径本身可能不成立（推断）**：任务书 `:73-74` 要求「和 python 实现结果一致」，
   并指向 `ops-solver/test/cgetri`（那是 **getri 求逆**的测试、不是 cheevj）。而 Hermitian 特征问题在有
   **简并特征值**时特征向量不唯一（相位与子空间基都可任选），逐元素「与 python 一致」根本没法成立——
   只能验 `A*V = V*diag(w)` 残差与 V 的酉性。**这是任务书自身的验收口径漏洞。**
   另：`:24 cuComplex *a`、`:27 cuComplex *work` 直接照抄了 CUDA 类型名，未适配昇腾。
7. **两个 Upsample 任务书把 uint8 拼成 `unit8`**（`:13` 任务概述、`:19` 功能实现要求，两份都错）。
   **字面匹配 dtype 时会漏。** 另 Exact 那份 `:22` 与 `:31` 两个小节标题都写「Exact1d 参数说明」
   （第二个应为 2d）、`:39` 的 2d **输出**行数据格式写成「NCL、ND」、维度写成 3（应为 NCHW/NHWC/ND、4）——
   **spec 生成器若按字面抄输出行，会把 2d 的输出 rank 写成 3。**
8. 上一轮已记、仍未裁：MinDim/MaxDim 的 `dim` 任务书自己前后不一致（MinDim 表记「输入」`:27`、
   MaxDim 表记「属性」`:36`）；logspace 任务书 `:31` 写 out「维度 2-8」但纯生成应为 1D（疑笔误）；
   ForeachExp 设计文档 `:19` 输出行写「shape size ≥ x 的 shape size」（逐元素算子，疑笔误，
   **未采信**，仍记 same_as_input）；IndexFillTensor 任务书参数表 self 的「维度(shape)」列写作 `[1,8]`
   （疑为「1~8 维」的区间写法，设计文档写 0-8 维）；Logit 的 `eps` 是「输入」还是「属性」两份设计文档不一致。

### 5.4 需要人拍板的

**必须现在定（挡住下一刀）**：

1. **`output_shape` 规则写在哪** —— spec 里搞一个小表达式语言，还是交给 per-op 的 golden.py？
   17 个算子等着它，这是**整份分类学最核心的一个新字段**。（§3.5）
2. **attr 值类型放不放开到 `list[int]`** —— 7 个算子卡在这，且 Upsample 的 `output_size` 恰恰
   既是数组又决定形状。放开它 + 上一条，就能把 shape_transform 那 3 个（本组唯一「改已有成熟算子」
   的形态）拉进通路。
3. **`fused_comm` 这个标签留不留** —— SyncBN 的 `output_shape_source` 报的是 reduction，
   形态上就是跨轴归约。多一格分类要不要，影响 spec 的 `shape_class` 受控词表。
4. **300V Pro 那 2 份怎么办（已点名）** —— `Cast&EmbeddingDenseGrad`（**纯 Atlas 300V Pro**）与
   `SyncBatchNormGatherStats`（**Atlas 800T A2、Atlas 300V Pro**）。本仓无 300V Pro 硬件、无 de-risk 记录。
   SyncBN 还额外要求「接模型验证」（yolo-world + african-wildlife，box/cls/dfl loss 各不超 0.1），
   **这已超出算子级验收流水线的范畴**。
   （⚠ 补：两个 Upsample 的 op_def 里都另有 `AddConfig("ascend310p")`；300V Pro 是否即 op_def 口径的
   `ascend310p`，**我没核，这只是（推断）**。）
5. **6 个 task ↔ op_def dtype 冲突按谁判**（FmodScalar/FmodTensor、Gcd、InplaceRsqrt、Arange、logspace）：
   验收按任务书声明的 dtype，还是按仓内 op_def 实际支持的？这决定 `task_pr_gaps` 怎么写。
   **本轮又新增 2 例同类**：UpsampleNearest3d（uint8/double 只在 950 支）、SPMV（int8 vs int32）。

**可以 later**：

6. **索引类输出的判定口径** —— 并列最值时 NPU 与 golden 的索引可以合法地不同，算不算 FAIL？
   （median 的「取首个等值下标」是明确可判的，MinDim/MaxDim 没写、才是真歧义。）
7. **median 的「变体 / overload」轴** —— 一个算子名对应两个 aclnn 变体（输出数都不同）。
   spec 是 per-op 单形态；硬拆成两个 op 又和任务书/PR 的一一对应脱节。
8. **性能基线是 GPU A100 数字的那几个怎么办**（SPMV 0.5×A100、TrsmBatched Mean(t1/t2)≥0.8、
   Cheevj 0.8×A100、dynamicMap I32/I16 0.7×A100）——这些仓里**没有内置 TBE 基线可比**，
   与 Task 2 现有的「内置 TBE 对照」范式不兼容，属 Task 3 的范畴。
9. **`run_on_npu.sh:24` 的 `_math` 后缀硬编码**（我实读确认）——与「零硬编码」约定直接冲突，
   应从仓名/构建产物探测。顺带：aclnn 主头文件名也随仓变（`aclnn_ops_math` vs `aclnn_ops_cv`）。
10. **`_build_inputs` arity ≥ 3 静默截断**（上一轮实测复现，本轮未复测）——建议单开修复项。

### 5.5 我没查、但会影响结论的

- `verify_mode: behavioral` 这条通路 Layer 1 到底实现了多少（Sleep 这类只能走它）。
- **PR 侧一律没看**：本轮两组结论全部来自任务书 + 参赛者设计文档，**未读任何 PR diff**。
  任务-PR map 里记的「MaxUnpool3d 未找到主开发 PR」「median PR #6429 open」等状态，本轮未复核。
- **同一算子有多份参赛者设计文档时，只逐字读了一份**（ForeachSubListV2 读 Krazy队，另有 菜鸟/蓝的盆；
  MaxUnpool3d 读 newnew，另有 耄耋；IndexFillTensor 读 Aeolion，另有 sleepy；InplaceSigmoid 读 GoOn，
  另有 卡拉米；Relu 读 gcw_fiAo4tDr，另有 披荆斩棘；ForeachExpm1 读 GoOn，另有 jzy；SyncBN 读 xiao--hai，
  另有 hustding）。**若某条结论要载重，建议交叉另一份。**
- **表 A/B/C 的形态字段我（合并员）没有重读任务书复核**，只做合并、分档、归组。
  真要拿某一行当 spec 的依据，**先自己核一遍那一行**。
- **ops-cv 的 st 用例是 ATK 驱动的**（`image/upsample_nearest/tests/st/**/atk_*.json` + `executor_*.py`，
  golden 走 `torch.nn.functional.interpolate`），与 OpRunway 自己的 caseset 是两套东西。
  这些 json 里已带 shape/dtype/attr（含 `outputSize_int`、`scalesH_double`），**可以当用例语料来源**，
  但**不是可直接复用的执行通路**——这条是其余 6 仓那组自报的，我没打开这些 json 核。
