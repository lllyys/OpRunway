# OpRunway · 用例轴集契约设计说明（交人评审）

> **状态**：设计说明，**不是实施方案**。本轮（步骤 11a）不动 `gen_cases.py` 一行代码。
> **要人拍板的**：下面 §1 的 6 个决定。文档给选项、给每个选项的用例数量级与覆盖代价，**不替人做决定**。
> **口径**：所有 `file:line` 都是 2026-08-06 在 `3c59d76` 上实读的；标「推断」的地方就是真没证据。
> **落地依赖**：步骤 11b（实现）还依赖步骤 8（dtype 造得出来）+ 步骤 9（deferred 不免检），**本轮都没做**。

---

## 1 · 要拍板的 6 件事（先看这张表，细节在后面各节）

| # | 决定 | 我给的取舍面 | 细节 |
|---|---|---|---|
| ① | **先修 shape 轴的取值，还是先扩轴？** | 现 shape 轴退化成 `(L,1,1,…,1)`，**1344 例里 46.4% 在归约一根长度为 1 的轴**。去退化**可以做到用例数不变、跑测时间不变** | §3、§8 选项 0 |
| ② | **`torch_parity` 要不要产特殊场景？** | 现在**一条都不产**（空/标量/上下边界全没有）。参考仓对 structural 类确实是 special=0，所以「照抄参考仓」和「本仓 legacy 一律保留边界」现在是**互相打架**的两条规矩 | §7.1 |
| ③ | **值域 regime 进不进笛卡尔？基数按什么定？** | 参考仓按 `operator_class` 定基数（floating=2 / structural=1）。照抄的话 median 一条不涨；一刀切 ×2 则直接翻倍 | §7.2 |
| ④ | **`case_target` 这个数从哪来、怎么写才可机核？** | 现在是人手算的一个魔数，等式 `case_target == ∏轴基数` 卡死。一旦有「有证据的排除项」这等式当场不成立 | §6 |
| ⑤ | **重复组合是删掉还是留着记账？** | 现 1344 例里 10.7% 是**同一 (dtype, shape, 解析后 attrs) 组合的重复**。删 → 上面那条等式破；留 → 报告不能再声称「1344 个不同组合」 | §7.4 |
| ⑥ | **同一条轴两处声明怎么办？** | median spec 里 `attr_matrix`（4 组）和 `torch_parity_matrix.attribute_profiles`（7 组）**同时存在、没有任何门交叉核对**，且前者在 `torch_parity` 下**根本不被消费** | §5 |

---

## 2 · 现状事实基线（先把「现在到底是什么」钉死）

### 2.1 两档造例规则并存

| 档位 | 轴集 | 选例方式 | 入口 |
|---|---|---|---|
| `legacy`（省略即此） | dtype × shape × 值域 regime × attr | 强制项全量 + 常规网格 **1-wise 采样**，`case_target` 封顶 | `gen_cases.py:2852` 起 |
| `torch_parity` | dtype × rank × shape_profile × attribute_profile | **完整笛卡尔、不采样**，`case_target` 必须精确等于矩阵大小 | `gen_cases.py:1374` `_torch_parity_plan` |

`_plan` 在 `gen_cases.py:2850` **提前返回**走 `torch_parity`，所以 legacy 那一整套（特殊场景、value_profile、attr_axis_lengths、attr_matrix）在 `torch_parity` 下**全都不执行**。

### 2.2 两档的轴取值（逐字读码）

| 轴 | `legacy` 取值 | `torch_parity` 取值 |
|---|---|---|
| dtype | spec 的 dtype 集；重点档 `KEY_DTYPES` 全阶梯，其余每种至多 2 条（`_dtype_shapes:2416`） | spec 的 dtype 集，**逐个全量** |
| rank | **不是独立轴**——由 shape 自带（`_REG_SHAPES` 的 11 条自带 rank 1~4，`_EXT_RANK_SHAPES` 补 rank 5） | **独立轴**，`ranks: [1..8]` |
| shape | 11 条具体 shape（`:2031`，numel 3~255，**全是 small 档**）+ 2 条大 shape（`:2040`）+ rank5 补 2 条（`:2039`） | 3 档 `shape_profiles`，每档只有一个 `leading_dim`；实际 shape = `(leading,) + (1,)*(rank-1)`（`:1469`） |
| 输出个数 | 由 attr 决定（`_select_call_variant:3100`），**不是轴** | 同左 |
| 值域 regime | 2 档 `("uniform","normal")`（`:2044`），进正交网格 | **没有这条轴**，硬写 `uniform`（`generator` 只收 `{kind:"uniform",min,max}`） |
| attr | `attr_matrix` 展开的 combo | `attribute_profiles` 显式列表 |
| 特殊场景 | 每 dtype 强制叠加（空/标量/下界/上界/inf/-inf/nan），**只配 `attr_combos[0]`** | **零条**（`:1504` `forced_special: 0`） |

Median 当前矩阵：`8 dtype × 8 rank × 3 shape × 7 attr = 1344`。

### 2.3 「11 种真实 shape」并进来会撞到的硬事实

⚠ **legacy 那 11 条 shape 覆盖不到 rank 6/7/8。** `_REG_SHAPES` 的 rank 集是 `{1,2,3,4}`，加 `_EXT_RANK_SHAPES` 也只到 5；`_shape_ladder:2396` 还会按 spec 的 rank 约束**过滤**。而 median 的 `ranks` 是 1..8。

所以「11 种 shape × 8 种 rank」这个笛卡尔**在字面上就构造不出来**——rank 6/7/8 那三列是空的。要并入，只能把 shape 轴改成**按 rank 参数化的 shape 族**（给定 rank 生成一条 shape），不能是一张扁平的具体 shape 表。这一条不是取舍，是前提。

---

## 3 · Q1：shape 轴为什么是退化的？有意还是历史欠账？

**答：有意的，是忠实照抄参考仓的冻结设计；但退化带来的后果没人审过。**

### 3.1 有意的证据

`dev-doc/oprunway-cannbot-alignment-plan.md:29-30` 记的是 4-agent recon 对参考仓 `cannbot-ops-input` 全部 **17 份**设计文件的实证结论，逐字：

> 【D·shape 阶梯】按 size class 取**一个基准维、其余补 1 到目标 rank**（全 17 份设计实证）：
> small 基准=31（2^5-1）、medium=2047（2^11-1）、large=262144（2^18）；rank r 的 shape = `[base]+[1]*(r-1)`，
> **numel 恒等于 base、与 rank 解耦**。

这句「numel 恒等于 base、与 rank 解耦」就是这个设计**要的东西**：它让 rank 轴和规模轴真正正交——「large」在 rank 1 和 rank 8 是同一个 numel，rank 涨不会把用例规模带着涨。这是个正经的设计目标，不是偷懒。`gen_cases.py:1381` 的 docstring 也照写了「其余轴补 1」。

同一份 recon 还给了 2026-07-25 的用户决策：**完整 faithful、不限量**。所以照抄本身也是被拍过板的。

### 3.2 没审过的后果（这部分是本文档的主要发现）

把 `shape=(L,1,…,1)` 和 median 的 7 个 attribute profile 摆在一起算，结论很硬：

**(a) rank 轴对内存布局是无效轴。** `(L,1,1,1)` 和 `(L,)` 的元素在内存里的排布**完全一样**（连续场景下 stride 也等价）。所以 24 个 shape 取值（8 rank × 3 档）只对应 **3 种不同的内存布局**。
⚠ 这条能证到哪、证不到哪：它证明**输入缓冲区的字节与布局相同**；它**不证明** kernel 走同一条路径——shape 元数据是传进 kernel 的，完全可能驱动不同的切分。所以准确说法是：**rank 轴保证测到 shape 推导 / 元数据路径，但保证测不到不同的数据切分路径。**

**(b) 46.4% 的用例在归约一根长度为 1 的轴。** 按 `_resolve_axis_class:1353`（`first=0`、`middle=(rank-1)//2`、`last=rank-1`）逐 rank 算被归约轴的实际长度：

| rank | `global` | `first` | `middle` | `last` | 本 rank 平凡 profile 数 |
|---|---|---|---|---|---|
| 1 | L | L | L (a=0) | L (a=0) | 0 |
| 2 | L | L | L (a=0) | **1** (a=1) | 2 |
| 3..8 | L | L | **1** (a≥1) | **1** | 4 × 6 rank = 24 |

每个 (dtype, 规模档) 单元格里平凡 profile 共 `0+2+24 = 26` 条 → `26 × 8 dtype × 3 档 = 624`。
**624 / 1344 = 46.4%**。对 median 这类归约取元素的算子，归约一根长度为 1 的轴 = 值输出等于输入、索引输出恒为 0。

**(c) 另有 10.7% 是同一组合的重复。** 低 rank 下轴类会塌：rank 1 时 first=middle=last=0（6 个 by-dim profile 只剩 2 个不同）；rank 2 时 first=middle=0（再塌 2 个）。按 (dtype, shape, **解析后**的 attrs) 数不同组合：`(4+2) × 8 × 3 = 144`，**144 / 1344 = 10.7%**。
⚠ 说清楚：这些**不是**字节重复。数据种子来自 `case_id`（`_case_rng`），profile 名进了 id，所以输入字节不同。它们是**同一个覆盖组合的额外随机样本**，不是新覆盖。多跑几个随机样本有价值，但**不能按「不同组合」计数**。

**(d) 两部分不重叠**，合计 `624 + 144 = 768`，即 **1344 例里 57.1% 要么在归约长度-1 的轴、要么是同组合重复**。

### 3.3 这条结论怎么泛化（不按算子身份）

退化对**不同接口能力**的算子后果完全不同：

| 接口能力 | 退化的代价 | 规则 |
|---|---|---|
| 纯 elementwise（只看 numel，无轴选择、无布局敏感） | **零代价**——`(L,1,1)` 和 `(L,)` 对它就是同一件事 | 保持退化即可 |
| 接口带轴选择器（`dim` / `axis` / `dims`，含归约、排序、索引、cumulative 类） | **代价就是上面的 46%** | 必须去退化 |
| 接口带布局/多维语义（卷积类 NCHW、im2col、broadcast 关系、转置/reshape 类） | 退化直接消掉被测语义 | 必须去退化 |

判据是**「spec 的 `params` 里有没有 io=attr 且被当轴下标用的参数 / 有没有多维布局语义」**，是接口能力，不是算子名。仓里已有现成的读法（`_axis_indices` / `attr_axis_lengths` 那套就是按 attr 值是不是轴下标来判的），可以复用同一个判别式。

---

## 4 · Q2：过万用例的真机跑测时间估算

### 4.1 唯一的锚（实测，非估算）

`dev-doc/oprunway-local-source-realmachine-validation.md:293` 的 A/B 两跑，同一份 1344 例 caseset、a3 容器、`cpp_extension`、Median：

| | A 跑 | B 跑 |
|---|---|---|
| 墙钟窗口 | 03:31:04 → 03:35:14 = **250 s** | 03:39:16 → 03:40:59 = **103 s** |
| 窗口覆盖 | Task1 生成 + Task2 NPU 跑测 + 验收门 | 同（复用 A 的 vendor `.so`，不含 build） |
| 产出 | `acceptance.json` / `verdict.json` **字节级相同** | 同 |

⚠ 两跑做的是同一件事、产出字节相同，但差 147 s，仓里**没有记录解释这个差**（合理猜测是首跑冷启动 / 缓存，未证）。所以**取区间、不取平均**：

> **每例 0.077 ~ 0.186 s**（= 103/1344 ~ 250/1344）。

### 4.2 外推（**估算**，下面每个数字都是估算）

| 矩阵规模 | 精度跑测估算 |
|---|---|
| 1,344（现状） | 103 ~ 250 s（**实测**） |
| 2,688 | 3.4 ~ 8.3 min |
| 4,032 | 5.2 ~ 12.5 min |
| 9,856 | 12.6 ~ 30.6 min |
| 30,000 | 38 ~ 93 min |

### 4.3 这个估算的四条边界（别拿它当承诺）

1. **只有一个算子、一次采样。** Median 一个见证，没有第二个算子的时间数据。换算子（尤其 golden 更贵的）不成立。
2. **不含性能采集。** 那次 Task3 `skipped_precision_gate` 跳过了。
3. **但性能耗时不随矩阵涨**——`perf.case_selection.max_cases = 50`（median spec），性能 case 是精度 case 的子集且封顶 50，`warmup 5 / repeat 20`。所以扩轴**不会**把性能那段时间带着涨。这是好消息，得说清楚。
4. **锚本身跑的是最便宜的形状。** 现 shape numel ∈ {31, 2047, 262144}，且近一半用例在归约长度-1 的轴（§3.2）。**去退化后同一例会变贵**（多少不知道，未测）——所以上表在 shape 去退化的场景下是**乐观下界**。
   ⚠ 但注意：**去退化不必然涨 numel**。保持 numel 不变、只把它在各轴间重新分配（如 large 在 rank 4 从 `(262144,1,1,1)` 改成 `(64,64,8,8)`），numel 恒等、golden 成本模型 `max(输入 numel, 输出 numel)` 的值也不变（预算 `_GOLDEN_COST_BUDGET = 2^26`，当前 large = 2^18，余量 256×）。真机 kernel 时间会不会变，未测。

---

## 5 · 现在就存在的两个账目问题（不扩轴也得解决）

### 5.1 声明了但不被消费的字段（结构性 fail-open）

`_plan` 在 `gen_cases.py:2850` 提前返回，导致 `torch_parity` 下这些字段**被静默忽略、且没有任何门检查它们是否被声明**：

| 字段 | median spec 里 | 在 `torch_parity` 下 |
|---|---|---|
| `attr_matrix` | 有，**4 组** | 不消费 |
| `precision.value_profiles` | 未声明 | 不消费（声明了也不会产 tie 用例） |
| `attr_axis_lengths` | 未声明 | 不消费 |
| `allow_empty_tensor` | `false` | 不消费（本来也不产空用例） |

今天不出事，只是因为 median 恰好没依赖它们。但这正是本仓最忌的形状：**账面声明了覆盖、实际一条没产**（`operator_class` 那处的 fail-closed 就是为这个加的）。轴集契约要么禁止在 `torch_parity` 下声明这些键，要么明确它们的语义并真消费——**不能维持现在这种「写了没人管」**。

### 5.2 同一条轴两处声明、互不核对

median spec 同时有 `attr_matrix`（4 组）和 `torch_parity_matrix.attribute_profiles`（7 组），**数目都对不上，也没有门去对**。轴集契约必须给一个答案：一条轴只允许一处声明，或者必须有交叉核对门。

---

## 6 · Q4：`case_target` 这个数从哪来？怎么写才是可机核的？

### 6.1 现状

- `case_target` **必填、无缺省**（步骤 4 已落地，读取点唯一 `_require_case_target:2782`）；
- `torch_parity` 下还有第二重：`gen_cases.py:1459-1463` —— `expected = len(dtypes)*len(ranks)*len(shape_rows)*len(profiles)`，`case_target != expected` 当场炸，报错原文「**两者必须相等，禁止静默抽样**」。

这套今天够用，是因为矩阵只有 4 条轴、每条轴的基数都是无条件常数。

### 6.2 轴一多就不够用的两个点

1. **`case_target` 变成人手算的魔数。** 4 条轴还能心算，6 条轴 + 按 `operator_class` 条件取基数（§7.2）之后，改任何一条轴都要人重算一个四位数并抄进 spec。抄错的方向只有一个——抄小了，门会炸；抄的是「按当前实现恰好等于 ∏」的数，门会过。它**保证不了这个数是被想过的**，只保证它和 ∏ 一致。
2. **一旦引入「有证据的排除项」，`== ∏` 这个等式直接不成立。** 而排除项是必然要有的：参考仓自己就是「完整笛卡尔**减去有证据的排除项**」（`design_contract.py:506-511`，如 Relu 的 `excluded_cases` 16 条「int 表达不了非有限值」）。本仓的 `DEFERRED_NP_BY_FORM` 挂账、§7.4 的重复组合，也都是同一类东西。

### 6.3 可机核的写法：三重记账

形式（**只给形式，字段名待评审定**）：

```jsonc
"precision": {
  "case_target": 1344,                    // 账面数，人写，仍必填、仍无缺省
  "case_matrix": {                        // 机器可复算的矩阵
    "axes": [                             // ⚠ 列「取值」不是列「基数」
      {"name": "dtype",   "values": ["float32", "float16", ...]},
      {"name": "rank",    "values": [1,2,3,4,5,6,7,8]},
      {"name": "shape",   "values": [/* 每档的完整定义 */]},
      {"name": "attr",    "values": [/* 每个 profile */]}
    ],
    "excluded": [                         // 每条排除必须带理由 + 证据
      {"combo": {"dtype": "int8", "attr": "..."}, "reason": "...", "evidence": "..."}
    ]
  }
}
```

门（**三个数必须相等，任一处漂移就炸**）：

```
∏ |axes[i].values|  −  |excluded 展开后的组合数|   ==   case_target   ==   实际 emitted
```

为什么是这个形式：

- **列取值而不是列基数**：`8` 说不出哪 8 个 dtype 被测了；`values` 列表一摊开，「哪些值进了矩阵」变成可读可审的数据，报告也能直接引它，不用再人肉转述；
- **`excluded` 必须带 `reason` + `evidence`**：缩水必须留痕。这是本仓已有的纪律（`golden_cost.skipped_shapes`、`dropped_combo_classes`、`DEFERRED_NP_BY_FORM` 都是这个形状），不是新发明；
- **三重相等**：账面数、矩阵定义、实产数，任意一处漂移都被逮住。现在只有前两者的等式，缺「实产数」这一环（今天靠「完整笛卡尔不采样」这个实现细节隐式保证，一旦有排除项就断了）。

⚠ **这不是把缺省值加回来。** `case_target` 仍必填、仍无缺省，步骤 4 的结论一字不动；这里加的是**它必须与矩阵对得上**的第二重约束。

⚠ **与在途的 `precision.case_target_source` 是什么关系**（2026-08-06 有并行改动正在给 median spec 加这个键，写的是一段散文，说明 1344 = 8×8×3×7 的来历）：那个字段解决的是**人读得懂这个数从哪来**，本节解决的是**机器能不能复算并逮住漂移**。两者不冲突、也不互相替代——散文字段没有消费方，写错不会有任何门报错。若采纳本节的三重记账，`case_target_source` 应降级为 `_`-前缀注释（本仓惯例：`_` 开头 = 纯注释、无消费方），避免看起来像一道门。

⚠ **实现时必须复用 `gen_cases.py:1459-1463` 那处精确校验，不要另造一套。** 那处已经把「禁止静默抽样」这条纪律的判据写死了，推广只需把右边从 `∏` 换成 `∏ − |excluded|`，并补上与实产数的第三重比较。另造一套的后果是两处判据会漂移，而漂移的方向一定是宽的那边赢。

---

## 7 · 那四样东西进不进笛卡尔

### 7.1 特殊场景 → **不进笛卡尔**（独立叠加）；但先要决定它到底存不存在

**结论：不进。** 依据不是省事，是参考仓的明文设计（转引自 alignment plan:15，原出处 `design_contract.py:447-449`）：「特殊用例**独立叠加、绝不与常规网格交叉**」，理由逐字就是**避免组合爆炸**。本仓 legacy 也是这么做的——特殊场景只配 `attr_combos[0]`（`gen_cases.py:2854` 起），不铺 attr。

代价要认：特殊场景只配一个代表 attr，意味着「空 Tensor × 按维归约」这种组合永远测不到。这是**已知且被参考仓接受**的取舍。

⚠ **但前置问题更大：`torch_parity` 现在一条特殊场景都不产**（`:1504` `forced_special: 0`）。这里有两条规矩在打架：

| 规矩 | 出处 | 对 median（structural）说什么 |
|---|---|---|
| 照抄参考仓 | alignment plan:27「实测 Sign/IsClose/Scatter/MinDim/GatherV2 等结构类 **special=0**」 | 零条特殊场景是**忠实**的 |
| 本仓 legacy 的口径 | `gen_cases.py:2544` 逐字：「⚠ 只砍这三条〔inf/-inf/nan〕：`empty` / `scalar` / `bndlo` / `bndhi`（空 / 标量 / 上下边界）**所有类别一律保留**」 | 应该有 4 类边界 |

**这个必须人拍板**：`torch_parity` 是（i）照抄参考仓、零特殊场景，还是（ii）保留本仓的 4 类边界叠加？
选 (ii) 的量级：`4 类 × 8 dtype = 32 条`（只配代表 attr），相对 1344 是 +2.4%，时间上可忽略。
选 (i) 的代价：`allow_empty_tensor`、`empty_axis` 这些字段在 `torch_parity` 下永久是死字段，应显式禁止声明（回到 §5.1）。

### 7.2 值域 regime → **进笛卡尔，但基数按算子类别定**（这是「按算子类别收窄」的标准样例）

参考仓的做法（alignment plan:14 逐字：「value_profiles floating=2（uniform/normal）、structural=1（semantic）」）：`value_profiles` 是笛卡尔的一条轴，但基数按 `operator_class` 定。

对应到本仓：`operator_class` 是**已有的、受控词表的、字段驱动的** spec 字段（`{floating_compute, integer_compute, structural}`），完全够当这条轴的基数依据，不需要任何算子身份分派。

| 做法 | median（structural）用例数 | 一个 floating 算子的用例数 | 覆盖代价 |
|---|---|---|---|
| 不加这条轴（现状） | 1344 | 1344 | 数值分布只有 uniform，正态尾部 / 溢出场景零覆盖 |
| 一刀切 ×2 | 2688 | 2688 | structural 类白涨一倍，参考仓认为对它无意义 |
| 按 `operator_class`（推荐面） | **1344（不涨）** | 2688 | 与参考仓一致 |

⚠ 选了「加轴」还要顺带决定**用哪个 normal**：本仓 legacy 的 normal 是**固定 μ=0/σ=1 + clip 到 [-5,5] + 锚定前 3 元素**（`gen_cases.py:2045`），参考仓是**每 case 从 μ∈[-5,5]、σ∈[0.1,2] 采样、不 clip 不锚定**。两者不是同一个东西，alignment plan:37 已把这条记为偏离。参考仓那种逐 case 采样在本仓仍然是确定性的（种子来自 `case_id`），不违反「同 id 同字节」。

### 7.3 多输出 → **不进笛卡尔**（它是 attr 轴的函数，不是自由轴）

代码已经证了：`_select_call_variant(variants, attrs, cid)`（`gen_cases.py:3100`）——**调用变体是从 attrs 选出来的**。median 就是 `dim=null` → 单输出（values），`dim=k` → 双输出（values + indices）。

所以「输出个数」不是一条能独立取值的轴，它是 attr profile 的确定性函数。把它当轴放进笛卡尔 = **重复计数**，还会造出「dim=null 且要求 indices 输出」这种压根不存在的组合。

`gen_cases.py:3241-3242` 已有的门也印证了这个方向：声明的输出数与 golden 实际返回数**必须恰好相等**，不接受更短的前缀（原文理由：by-dim 漏 indices 会整条丢掉 index 验证链却一路绿）。这是「输出是被 attr 决定的、必须逐字对上」的口径，别再在轴集里开第二个口。

### 7.4 去重 → **不是轴，是记账问题**；现有的「去重」名不副实

现状两处：

- `_mk_id:2004`：`(op, dtype, shape_tag, id_kind, attr_idx)` 拼出的 base 若碰撞 → **fail-fast**，不静默改名；
- `_entry_key:2581`：`(dtype, shape_tag, id_kind, attr_idx)`，供采样去重。

⚠ 关键：两者用的都是 `id_kind`，而 `torch_parity` 的 `id_kind = tp_r{rank}_{shape_name}_{profile_name}`（`:1491`）——**profile 名进了 key**。于是 §3.2(c) 那 144 条「解析后 attrs 完全相同」的重复组合**一条都不会被逮到**，因为它们的 profile 名不同。

也就是说：**现有的去重是「文件名唯一性」保证，不是「覆盖唯一性」保证。** 这不是 bug（防的是伪造覆盖的重名），但不能拿它当覆盖去重用。

要人拍板的两条路：

| 路 | 做法 | 后果 |
|---|---|---|
| (a) 折叠 | 按 (dtype, 实际 shape, **解析后** attrs, 值域) 去重，重复的不产 | 实产数 < ∏ → §6.2 的等式当场破，**必须**先有 §6.3 的 `excluded` 记账形式；且 1344 会变成 1200 |
| (b) 留着 + 记账 | 照产，但在 caseset 元数据里落「distinct_combos = 1200 / emitted = 1344 / duplicate = 144」 | 用例数不变、跑测时间不变；报告**不能再写「1344 个组合全覆盖」**，只能写「1344 例、1200 个不同组合」 |

(b) 更贴本仓「不静默缩水」的纪律，成本也低；(a) 更省机时但改动面大。⚠ 不管选哪条，**现在报告里那句 `complete_cartesian：dtype×rank×shape_profile×attribute_profile 全覆盖`（`:1517`）都需要改**——它今天读起来像 1344 个不同组合，实际不是。

---

## 8 · 选项与代价（每个选项给用例数量级 + 覆盖代价）

时间列一律用 §4.1 的每例 0.077~0.186 s 外推，**都是估算**，且不含性能采集（性能封顶 50、不随矩阵涨）。

### 选项 0 · 只修 shape 轴的取值，一条轴都不加

把 `(L,1,…,1)` 换成**同 numel、跨轴重新分配**的非退化 shape（如 large@rank4：`(262144,1,1,1)` → `(64,64,8,8)`）。

| 项 | 值 |
|---|---|
| 用例数 | **1344，不变** |
| 精度跑测时间 | **不变**（numel 恒等；kernel 时间会不会变未测） |
| golden 成本 | **不变**（成本模型只看 max(输入 numel, 输出 numel)） |
| 拿到什么 | §3.2 那 624 条平凡归约 → 0；rank 轴从「只测元数据路径」变成「也测真实多维布局」 |
| 代价 ① | **丢掉参考仓明确要的「numel 与 rank 解耦」性质**——large 在不同 rank 下不再是同一个 numel（除非精心配平）。这是对 §3.1 那个设计目标的正面推翻，得认 |
| 代价 ② | `shape_profiles` 的 `leading_dim`（一个标量）**表达不下**非退化 shape。schema 必须改：要么加 `layout` 子字段，要么改成「每 rank 一条显式 shape」的表（3 档 × 8 rank = 24 条显式 shape，最笨但最可核） |
| 代价 ③ | 参考仓的维度约束（alignment plan:31-32：每维必为 `2^n` 或 `2^n-1`、每维 ≤2^20、numel ≤2^31；size_class 边界 small ≤2^10 / medium 2^10~2^18 / large ≥2^18）会**卡住某些配平**：small 基准 31 是质数，8 个轴全 >1 就凑不出 31。要么放弃「numel 恒定」，要么把规模档从「一个基准数」改成「一个 numel 区间」（参考仓自己的 size_class 本来就是区间：small ≤2^10、medium 2^10~2^18、large ≥2^18） |
| 代价 ④ | **改变现有 caseset 的字节**——Median 那两组真机基线（1344/58 与 1152/51）都作废，不能再拿来对照。这是最贵的一条 |

### 选项 1 · 加一条 `layout` 子轴（保留退化档 + 增加非退化档）

`shape` 轴变成「规模档 × 布局档」，布局档如 `{leading_heavy(现状), balanced}`（K=2）或再加 `trailing_heavy`（K=3）。

| K | 用例数 | 精度时间估算 | 说明 |
|---|---|---|---|
| 2 | 2,688 | 3.4 ~ 8.3 min | 现有 1344 **原样保留**（`leading_heavy` 档字节不变）→ 老基线仍可对照，这是相对选项 0 的最大好处 |
| 3 | 4,032 | 5.2 ~ 12.5 min | 再加一档「大维在尾」，压 stride 非连续的归约路径 |

代价：用例数翻倍/三倍；`shape_profiles` schema 同样要扩（同选项 0 代价 ②③）；且 `leading_heavy` 那一档里 46% 的平凡归约**依然在**，只是被稀释了——报告得如实说「其中 N 条是长度-1 轴归约」。

### 选项 2 · 加值域 regime 轴，基数按 `operator_class`

| 算子类别 | 倍数 | median | 一个 floating 算子 |
|---|---|---|---|
| structural / integer_compute | ×1 | 1,344（不涨） | — |
| floating_compute | ×2 | — | 2,688 |

代价：几乎为零（对 median 完全不涨），但需要决定 §7.2 那个 normal 的定义。**这是性价比最高的一条。**

### 选项 3 · 全交叉（11 条 legacy shape × 2 值域）

`8 × 8 × 11 × 2 × 7 = 9,856`，精度时间估算 **12.6 ~ 30.6 min**。

⚠ **这个选项在字面上构造不出来**（§2.3：legacy 的 11 条 shape 够不到 rank 6/7/8）。要真做，等价于「选项 1 把 K 提到 11」，即需要一个能对任意 rank 生成 11 种布局的参数化族——而 legacy 那 11 条 shape 里真正互相独立的信息只有**布局形态**（rank、是否含长度-1 轴、长宽比），**规模上它们全在 small 一档**（alignment plan:40 逐字：`_REG_SHAPES` 11 条 numel 3-255「**全 small**」，且本仓「**无系统 medium 档**」）。

所以「并入 11 种真实 shape」这个提法本身要先纠一次：它带来的**不是 11 个新规模档**，而是**布局这一条新子轴**。K=11 相对 K=2/3 多买到的东西，本文档给不出证据说值不值——见下面 §9 的测法。

### 选项 4 · 按接口能力逐轴收窄（可与上面任一条组合）

每条轴的基数由 spec 已有的字段派生，不是全局常数：

| 轴 | 基数依据（已有字段） |
|---|---|
| 值域 regime | `operator_class` |
| shape 布局 | `params` 里有没有轴选择器 attr / 有没有多维布局语义 |
| rank | `params[].rank` |
| 特殊场景 | `operator_class` + `allow_empty_tensor` |

代价：spec → 矩阵的映射从「读几个列表」变成「按条件派生」，`case_target` 的人手复算更难 → **强化了 §6.3 三重记账的必要性**（人不该再手算这个数，机器算、人核）。

---

## 9 · 一步几乎免费的测量，建议在拍板前做

§3.2 是纯算术，硬。但「哪两条轴真有交互效应」这一问，本文档只能给到**推断**：

| 命题 | 证据等级 |
|---|---|
| rank × 轴类 attr 有交互 | **已证**（`_resolve_axis_class` 的解析公式，§3.2） |
| shape 布局 × 轴类 attr 有交互 | **已证**（归约轴长度由两者共同决定，§3.2） |
| 输出个数 = attr 的函数 | **已证**（`_select_call_variant`） |
| dtype × 规模 有交互（向量宽度 → 尾块分支） | **推断**，仓内无证据 |
| dtype × 归约长度 有交互（累加误差随长度增长） | **推断**，仓内无证据 |
| 值域 regime × dtype 有交互（fp16 溢出） | **推断**，仓内无证据 |

**判据（建议写进契约，且它是通用的、不按算子身份）**：
> 只有当两条轴**共同决定被测实现的分支或切分决策**时才必须交叉；只改变数值量级、不改变控制流的轴，边际覆盖就够。

**几乎免费的验证**：那 1344 条的 `verdict.json` 已经在 a3 上躺着（`reports/Median-inplace/verdict.json`，sha256 `2aa3c4685b5b97ab…`），58 条 fail 的**逐 case 裁决**都在里面。把它按 (dtype × rank × 规模 × attr profile) 做交叉表，就能直接读出：

- fail 全落在某一条轴的某个值上 → 那条轴**边际覆盖就够**，不必交叉；
- fail 只在特定**轴对**上出现 → **实测到交互效应**，那一对必须交叉；
- fail 均匀散布 → 说明现矩阵这几条轴都没抓到结构性问题，扩轴的收益要重新论证。

零新增跑测、零 NPU 占用，把「全交叉还是边际覆盖」从口水仗变成一张表。⚠ 前提是那份 `verdict.json` 还在（未核）；不在就得重跑一次 1344 例（103~250 s）。

---

## 10 · 给步骤 11b（实现）的硬约束

1. **复用现成的精确校验，不要另造一套。** `gen_cases.py:1459-1463` 那处「`case_target` 必须精确等于矩阵大小，**两者必须相等，禁止静默抽样**」是这条纪律的唯一判据。推广（加 `excluded`、加实产数比对）**在原处改**；新写一份的后果是两处判据必然漂移，而漂移方向一定是宽的那边赢。
2. **不得按算子身份分派。** 所有轴集规则的依据只能是 spec 已有的**接口能力 / 算子类别**字段（`operator_class`、`params[].rank`、是否有轴选择器 attr、`allow_empty_tensor`…）。不允许出现「Roll 用这套、GaussianBlur 用那套」——那两个都只是见证。
3. **fail-closed 优先。** 新轴的取值一律受控词表；词表外当场炸。任何「缩水」（排除项、折叠、降规模）必须带 `reason` + `evidence` 落账，沿用 `golden_cost.skipped_shapes` / `dropped_combo_classes` 已有的形状。
4. **`coverage_strength` 那句话要跟着改。** `:1517` 现在写 `complete_cartesian：… 全覆盖`，在 §7.4 拍板之前它是**过强**的表述。
5. **`legacy` 侧字节不许动。** 4 个已真机验收的 elementwise 算子 caseset 是 sha256 钉死的（`ExistingOpsByteIdenticalTest`），一切改动只在 `torch_parity` 档位内生效——这条是 `case_profile` 这个开关当初存在的全部理由。
6. **改判定逻辑就补测试 + mutation 校验**（红绿都要贴）。纯 schema 文档不必。

---

## 11 · 本文档证不到的（如实记账）

- **参考仓原文没读到。** `repos/cannbot-ops-input` 在本 worktree 不存在（`repos/` 是 ignored 且为空）。§3.1、§7.1、§7.2 引的参考仓事实**全部转引自** `dev-doc/oprunway-cannbot-alignment-plan.md`（2026-07-25 的 4-agent recon 记录，带 `file:line`）。要当门禁依据前应回原仓核一次。
- **1152 那组基线的 spec 未入仓**，所以「1152 和 1344 只差 global 一档」不可证（validation doc §6.6 已记）。本文档所有比例都基于**仓内可读的** `plugin/samples/specs/median.spec.json`（1344 例）。
- **时间外推只有一个算子、一次采样、且不含性能采集**（§4.3）。
- **「去退化后 kernel 时间怎么变」没测。**
- **dtype/regime 相关的交互效应全是推断**（§9 那张表），没有实测支撑。
