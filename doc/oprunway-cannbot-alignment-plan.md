# OpRunway · 对标 cannbot-ops-input 对齐清单（torch 对标场景）

> 来源：2026-07-25 会话 180afdce 的 4-agent fanout recon（workflow `cannbot-alignment-recon`）。
> **用户决策（2026-07-25）**：范围/速度取舍 = **完整 faithful、不限量**（median accuracy 走完整笛卡尔 1728+ 例，接受真机跑测时间成倍涨，最忠实对齐 cannbot）。
> 落地前须走律令#1（抛方案）+ 律令#5（push 前审修门）。

## 四维差距概览（already_faithful + gaps）

### 测试用例生成规则（数量 / 值域 / 特殊值 / shape 阶梯 / operator_class 门 / perf 选例）—— cannbot-ops-input 的 operator-case-generation 对标 gen_cases.py
- **已忠实对齐**：False
- **cannbot 规则**：cannbot 的 case 生成是「设计文件先冻结、数据后机械生成」的两阶段，具体规则（给数字）：

【A·数量】accuracy 与 performance 是**两套独立数据集**。
· accuracy = **完整笛卡尔积、无上限**：dtype × format × rank × shape_profile × value_profile × attribute_profile 减去有证据的排除项（design_contract.py:506-511）。设计原则明写「覆盖数量由规格组合决定，不设固定的精度用例下限」（docs:95）。实证计数：IsClose 9×1×8×3×1×8=1728；Sign 8×1×8×3×1×1=192；Scatter 3384；Relu 336 常规。轴基数固定：shape_profiles 恒 3（small/medium/large）；value_profiles floating=2（uniform/normal）、structural=1（semantic）；ranks=完整区间（多为 1..8=8）；formats 多为 1（ND）；attribute_profiles=属性域的笛卡尔积（IsClose rtol×atol×equal_nan=2×2×2=8；Scatter 15；无属性=1）。
· 特殊用例**独立叠加、绝不与常规网格交叉**（避免组合爆炸；design_contract.py:447-449）。floating_compute 每 dtype 8 类特殊；实测每算子特殊总数 40-57（Relu 40、Abs 52、Heaviside 57、Neg 52），Pdist（小算子）5。
· performance = 设计里写 ≥50 候选，Phase2 **确定性恰选 50**（design_contract.py:436-437；performance_selection.py:10 TARGET=50）。

【B·值域】floating 常规值域是**硬编码强约束**（design_contract.py:412-421）：
· uniform value_profile → 必须恰为 {"kind":"uniform","min":-5,"max":5}；
· normal value_profile → 必须恰为 {"kind":"normal","mu":[-5,5],"sigma":[0.1,2]}，且 **mu 每 case 从 [-5,5] 均匀采样、sigma 从 [0.1,2] 均匀采样**（case_generator.py:48-53 `rng.uniform(*mu_spec)`），**不 clip、不锚定**。两个 profile 对 floating 都必产（SKILL.md:244-245）。
· 其他 generator：integers 用 spec 显式 min/max（`rng.integers(low,high,endpoint=True)`，case_generator.py:55-57）；zeros/ones（case_generator.py:33-36）；index 张量用 distribution_policy=semantic + semantic_constraint「Index within selected dim」+ generator zeros（GatherV2 实证，**始终 in-bounds、不注入越界索引**）。

【C·特殊值 = 按 operator_class 分档】受控词表 {floating_compute, integer_compute, structural}（design_contract.py:427）；**仅 floating_compute 调 `_validate_floating_rules`**（design_contract.py:512）强制以下矩阵。
· floating_compute 每支持 dtype 必产：scalar_tensor(shape[1])、shape_lower_boundary([1])、shape_upper_boundary([1048576]=2^20)、nan(值["nan"])、pos_inf(["+inf"])、neg_inf(["-inf"])、mixed_inf(["+inf","-inf",0])；empty(每 dtype/每输入名，shape[0]）（design_contract.py:379-393）。special_values **整张量循环填充**（np.resize，case_generator.py:37-42），非部分位置。
· **同 dtype 的非有限特殊值必须用不同 shape**（design_contract.py:391-393）——实测 nan=[3]/pos_inf=[7]/neg_inf=[15]/mixed_inf=[31]（皆 2^n-1）。
· 整数 dtype 的 nan/inf **显式排除并记 reason**「int cannot represent non-finite」（Relu excluded_cases 16 条）。
· structural / integer_compute **不产 nan/inf**；特殊值改从 torch 语义造（SKILL.md:252-255）：**极值、0/1/-1、重复值、负/越界/边界索引、广播关系、规约轴、饱和**。实测 Sign/IsClose/Scatter/MinDim/GatherV2 等结构类 special=0（靠 semantic_constraint + 常规网格见证）。

【D·shape 阶梯】按 size class 取**一个基准维、其余补 1 到目标 rank**（全 17 份设计实证）：
· small 基准=31（2^5-1，numel≤2^10）、medium=2047（2^11-1）、large=262144（2^18，numel≥2^18）；rank r 的 shape = [base]+[1]*(r-1)，**numel 恒等于 base、与 rank 解耦**。
· 正维度必为 2^n 或 2^n-1、每维 ≤2^20（design_contract.py:354-357,403-406；SKILL.md:242）；numel ≤2^31（design_contract.py:399）。
· size_class 边界：small numel≤2^10、medium 2^10<numel<2^18、large numel≥2^18（design_contract.py:32-34；SKILL.md:278-280）。ranks 覆盖完整 1..8 区间（design_contract.py:481-482）。

【E·perf 选例】performance_selection.py 确定性选 50：core float32/float16/bfloat16 **各 10**（缺则报错，:91-96）；每个声明的 complex dtype **各 ≥5**（:104-110）；float64 目标 5（:113-117）；其他声明 dtype（int/bool/uint）预算允许时 **各 ≥3**（:127-130）；配额 ≥5 的档保留 small+large（≥10 的档各 ≥3）、含 medium；禁 empty/非有限；最后确定性填满，不足 50 即失败（:153-155）。
- **我们现状**：gen_cases.py 是「op 驱动、按 spec 字段生成」的单套 caseset，每 case 打 dims 标签（功能/精度/性能），不分 accuracy/performance 两套。
· 数量：case_target 默认 50（gen_cases.py:949,2179）**封顶总用例数**；budget=max(case_target,|forced|)（:1852），常规网格用 `_one_wise_pick` **1-wise 采样**填到 budget（:1854）；coverage_strength 自己注明「50 封顶下 §1.1 100% 正交不可达」（:1872-1873）。强制项=§1.4 特殊场景(每 dtype)+白名单(key dtype×attr×大shape)。
· 值域：uniform [-5,5]（:357）与 cannbot 一致；normal 用**固定 _NORMAL_MU=0.0/_NORMAL_SIGMA=1.0**（:966）、**clip 到 [-5,5]**（:355）、且**锚定前 3 元素为 (-2,0,3)**（:360）；int 用自定义 [max(-100,min+1),min(100,max)] + 锚点(-2,0,3)/(0,1,3)（:343-352）。
· 特殊值 operator_class 门：`_OPERATOR_CLASSES` 词表与 cannbot 完全一致（:439），`_NONFINITE_CLASSES={floating_compute}`（:441）、`_emits_nonfinite`（:463-468），未声明=None→照产（向后兼容）。**这一子维忠实对齐 cannbot、且代码显式引用其 design_contract.py:512**（:83-93,594-597）。
· 特殊场景本体（:1476-1494）：empty(仅功能)、scalar[1]、bndlo(1,1,1)、bndhi=(1024,1024)=2^20、inf/ninf/nan 各一条(shape 都用 (16,))；special 数据是**前 1/4 位放特殊值 + 其余 uniform**（:427-432）。
· shape 阶梯：`_REG_SHAPES` 11 条 numel 3-255（全 small）（:952-953）、`_LARGE_SHAPES`=(1024,1024)/(65535,)（:961）、rank5 补 `_EXT_RANK_SHAPES`；`_LARGE_NUMEL`=2^16 门槛（:897）；**无系统 medium 档**；维度沿用 2^k/2^k-1。
· value_profile：自造 nan/tie 两枚（:478,499-519，借 generate_array 的 special_values/np.resize 机制），非 cannbot 的 uniform/normal/semantic 轴。
· perf 选例：`select_perf_cases`（aclnn_runtime/perf_msprof.py:1377）只挑所有 dims 含「性能」且过精度的 case，**无 dtype 配额、无恰 50 目标、无 small/large 保留**。
- **gaps**：
  - [HIGH] 数量口径根本不同：cannbot accuracy=完整笛卡尔无上限(1728/3384…)+perf 恰选50，两套；我们单套用 case_target=50 封顶 + 1-wise 采样，精度覆盖被压到 50 条边际采样
    - 偏离：gen_cases.py:1852-1854 budget=max(case_target,|forced|)、常规网格 _one_wise_pick 采样填充；:1872 自认「50 封顶下 100% 正交不可达」
    - 对齐改法：在 torch-对标 spec 分支下拆成两口径：accuracy 走 _plan 的完整正交网格全量展开(去掉 1-wise 封顶、去掉 case_target 上限)，另起 perf 子集恰 50。至少把 precision.case_target 语义从『总封顶』改为『perf 选例目标』，accuracy 恒全笛卡尔
  - [HIGH] shape 阶梯缺 medium 档、且非 cannbot 的『base×size×rank padding』结构：cannbot small/medium/large=31/2047/262144 每档 × 8 rank；我们常规全是 numel≤255 的 small、直跳 2^20 大，无 2047 medium 档；size 门槛 2^16 ≠ cannbot 2^18
    - 偏离：gen_cases.py:952-961 _REG_SHAPES(numel 3-255)/_LARGE_SHAPES(2^20)；:897 _LARGE_NUMEL=2^16
    - 对齐改法：把 _REG_SHAPES 换成按 size_class 的基准维阶梯 small=31/medium=2047/large=262144，每档 shape=[base]+[1]*(r-1) 覆盖 rank 1..8；_shape_class 边界改 small≤2^10 / medium 2^10..2^18 / large≥2^18（对齐 design_contract.py:32-34）
  - [MEDIUM] normal 值域偏离：cannbot 每 case 从 mu∈[-5,5]、sigma∈[0.1,2] 采样、不 clip、不锚定；我们固定 mu=0/sigma=1、clip[-5,5]、还锚定前 3 元素(-2,0,3)
    - 偏离：gen_cases.py:355,360,966 (_NORMAL_MU=0.0/_NORMAL_SIGMA=1.0 + np.clip + f[0:3]=-2,0,3)
    - 对齐改法：torch-对标分支下把 normal 改为 per-case `mu=rng.uniform(-5,5)`、`sigma=rng.uniform(0.1,2)`（对齐 case_generator.py:49-50），去掉 clip 与前 3 元素锚定（锚定是我们为 Sign 分支覆盖发明的、cannbot 无）
  - [MEDIUM] 非有限特殊值形态偏离：cannbot 4 kind(nan/pos_inf/neg_inf/mixed_inf)、同 dtype 各用不同 shape(3/7/15/31)、整张量循环填充；我们 3 kind(inf/ninf/nan)、都用 (16,)、前 1/4 填充+其余 uniform、无 mixed_inf
    - 偏离：gen_cases.py:1491-1493 (三 kind 同 shape(16,)) + :427-432 (前1/4填充)
    - 对齐改法：补 mixed_inf(值[+inf,-inf,0])；给 nan/pos_inf/neg_inf/mixed_inf 分配不同 shape([3]/[7]/[15]/[31] 等 2^n-1)；special 数据改整张量循环填充(np.resize，对齐 case_generator.py:37-42)；shape_upper_boundary 我们已是 2^20，对齐
  - [MEDIUM] 无 cannbot 式 perf 50 选例(dtype 配额)：cannbot 硬选 50、core fp32/16/bf16 各 10、complex 各 5、float64 5、其他 ≥3、留 small+large；我们只按『性能』tag 全选、无配额无 50
    - 偏离：aclnn_runtime/perf_msprof.py:1377 select_perf_cases 无配额逻辑
    - 对齐改法：若要对齐 perf 口径，新增按 dtype 配额的确定性 50 选例(移植 performance_selection.py 的 CORE_DTYPES=10/complex=5/float64=5/OTHER_MIN=3 与 small/large 保留)。注意我们 perf 走真机 msprof、判定模型本就不同，此项是否对齐需用户裁
  - [MEDIUM] 结构/整型类特殊值覆盖不足 parity：cannbot SKILL.md:252 要求从 torch 语义系统造 极值/0·1·-1/重复/负·越界·边界索引/广播/规约轴/饱和；我们结构类只保留 empty/scalar/bndlo/bndhi + 可选 tie，未提供极值/0·1·-1/越界索引/广播/饱和的 op-中立生成器
    - 偏离：gen_cases.py:1465-1494 结构类仅砍非有限、保边界；value_profile 只有 nan/tie(:478)
    - 对齐改法：扩 value_profile 受控词表，补 extrema/zero_one_neg/boundary_index/broadcast/saturation 等 op-中立生成器（tie 已覆盖『重复』一档）。注意 cannbot 实际数据集这些也由 agent 按算子自由裁量、多为 0 条，故更宜作为 spec 可声明的 profile 而非强制
  - [LOW] int 值域自定义：cannbot integers 用 spec 显式 min/max；我们写死 [max(-100,min+1),min(100,max)] 并锚点
    - 偏离：gen_cases.py:343-352
    - 对齐改法：允许 spec 传显式 int 值域(对齐 case_generator.py:55-57 的 integers min/max)；保留排除 dtype-min 的溢出保护即可
- **cannbot 证据**：skills/operator-case-generation/common/design_contract.py:32-34 (shape_size_class: small≤2^10 / large≥2^18 / else medium); skills/operator-case-generation/common/design_contract.py:412-421 (_validate_floating_rules 硬约束 uniform[-5,5]、normal mu[-5,5] sigma[0.1,2]); skills/operator-case-generation/common/case_generator.py:43-57 (generate_array: uniform/normal 每case采样mu·sigma/integers); skills/operator-case-generation/common/case_generator.py:37-42 (special_values 别名映射 + np.resize 整张量循环填充); skills/operator-case-generation/common/design_contract.py:379-393 (floating 特殊矩阵 8 类 + 非有限须不同 shape); skills/operator-case-generation/common/design_contract.py:427 与 :512 (operator_class 词表 + 仅 floating_compute 调 _validate_floating_rules); skills/operator-case-generation/common/design_contract.py:403-406,399 (维度 2^n/2^n-1 ≤2^20，numel ≤2^31); skills/operator-case-generation/common/design_contract.py:436-437,506-511 (perf≥50候选、accuracy=full Cartesian 减排除); skills/operator-case-generation/scripts/gen/performance_selection.py:10-14,91-130,153-155 (TARGET=50、core各10/complex各5/float64=5/其他≥3); skills/operator-case-generation/SKILL.md:252-255 (结构/整型特殊值: 极值/0·1·-1/重复/越界索引/广播/规约轴/饱和); docs/operator-case-generation.md:36-41,95 (IsClose 1728 全笛卡尔算例 + 不设固定精度用例下限); ops-bench/ops-eval-dataset/designs/aclnnRelu|aclnnSign|aclnnGatherV2/case_design.json (实证: small=[31]/medium=[2047]/large=[262144]、structural special=0、index用zeros+semantic_constraint)
- **我方证据**：plugin/acc-common/gen_cases.py:949 (_DEFAULT_CASE_TARGET=50); plugin/acc-common/gen_cases.py:2179 (case_target 读取), :1852-1854 (budget=max(target,forced)+_one_wise_pick 采样), :1872-1873 (coverage_strength 自认 100% 正交不可达); plugin/acc-common/gen_cases.py:355,360,966 (normal 固定 mu=0/sigma=1 + clip[-5,5] + 锚定前3元素-2,0,3); plugin/acc-common/gen_cases.py:343-352 (int 值域 [max(-100,min+1),min(100,max)] + 有/无符号锚点); plugin/acc-common/gen_cases.py:439-441,463-468 (operator_class 门忠实对齐 cannbot 词表 + 仅 floating 铺非有限); plugin/acc-common/gen_cases.py:1476-1494 (_special_entries: empty/scalar/bndlo/bndhi/inf/ninf/nan 皆 shape(16,)); plugin/acc-common/gen_cases.py:419-433 (_build_value_special: 前1/4特殊值+其余uniform，非整张量填充); plugin/acc-common/gen_cases.py:952-961,897 (shape 阶梯: 全 small 常规 + 直跳 2^20 大; _LARGE_NUMEL=2^16; 无 medium); plugin/acc-common/gen_cases.py:478,499-519 (value_profile nan/tie 自造); plugin/acc-common/aclnn_runtime/perf_msprof.py:1377 (select_perf_cases: 仅按「性能」tag 全选，无配额/无50)

### 精度测试方式方法（仅 torch 对标场景）：容差表 / 比对公式 / nan·inf / index 输出 / pass-fail 门 / 报告格式
- **已忠实对齐**：False
- **cannbot 规则**：重要前提：cannbot-ops-input 的**真实**精度判定全在 `skills/operator-evaluation/scripts/accuracy.py`，方法是**纯 `np.allclose` 逐 dtype 容差**——**MERE/MARE 在整个仓的 .py 里一处都没有**（`grep -rn "MERE|MARE" --include=*.py` 空；连 .md 也没有）。我们 precision_policy 注释里说的「cannbot 精度标准 MERE/MARE 按 dtype」指的是**另一个仓 cannbot-skills 的 ops-precision-standard**，不是本次要对标的 cannbot-ops-input。故 torch 对标场景 cannbot 的口径 = allclose，不是 MERE/MARE。

具体做法（全部带数字）：
1) 逐 dtype 容差表 `_ALLCLOSE_TOLS`（存 **(atol, rtol)** 顺序，判据 `|actual-golden| <= atol + rtol*|golden|`）：fp16=(9e-2, 2^-10)、bf16=(1e-1, 2^-7)、fp32=(1e-3, 2^-13)、fp64=(1e-6, 2^-30)、complex64=(1e-3, 2^-13)、complex128=(1e-6, 2^-30)。整型/bool（int8/uint8/int16/int32/int64/bool）走 `_EXACT_DTYPES` = **逐位精确**（tol=0，`np.array_equal`）。bf16 落盘按 `_DTYPES["bfloat16"]=np.float32`（fp32 存储、bf16 容差）。来源自注：fp16/bf16/fp32 抄自 tilelang2ascend `verification_ascendc.py`，fp64/complex 为外推。
2) 比对公式 `_matches_arr`：先 shape 必须相等（否则 False）→ 先试 `np.array_equal`（bit 精确捷径，覆盖 int/bool + 恰好逐位相等的浮点）→ 否则若非 exact 且 dtype 是 inexact，用 `np.allclose(actual, golden, rtol=rtol, atol=atol, equal_nan=True)`（golden 作参考=公式里的 |golden|）→ 其余 False。**无 error_rate / 无坏点占比**：allclose 是**全或无**（每个元素都须落容差内才 pass，等价 mismatch==0、容错率=0）。
3) golden = 冻结的 torch 参考在 **CPU** 上跑出来 `.numpy()` 落 golden.bin（bf16→fp32 存储）；判定时 `case_matches_golden` 只读 `case["golden_files"][0]`（**第一个输出**）。⚠ 容差 dtype 键取的是 `case["dtype"]`（**输入/case dtype**），golden 却按 `output_dtype` 读——非同型算子（如 IsClose float→bool）这里键错，是 cannbot 的潜在小瑕。
4) nan/inf：完全交给 `np.allclose(..., equal_nan=True)` 的原生语义——both-NaN→相等；同号 inf→相等；单侧 inf / 异号 inf→失配；有限位按公式。allclose 路径**不**做 inf→finfo.max 替换。
5) **index 类输出：cannbot 根本不判**。worker `accuracy_worker.py:73` 只保存 `result.outputs[0]`，评测只比 `golden_files[0]`。多输出算子（median / max.dim，torch 返回 (values, indices)）**只校 values、indices 完全不比**（indices 的 golden 虽造了但从不消费）。若真去比 index（int64），它会落 `_EXACT_DTYPES` → **下标逐位精确**（`np.array_equal`），即 cannbot 的**潜在** index 口径是「下标精确」，**没有 gather 值一致这套**。
6) pass/fail 门 + 报告（`evaluate_accuracy` 返回，schema_version=1）：`accuracy` 块含 total/executed/passed/failed/errored、`overall_pass_rate=passed/total`、`by_dtype:[{dtype,count,passed,failed,errored,atol,rtol,pass_rate}]`（**逐 dtype 回显实际用的 atol/rtol**）、`failures:[{case,dtype,pattern:"mismatch",archive}]`、`errors:[{case,dtype,error,archive}]`。**failed（跑了但数值错）与 errored（kernel 崩/调用错/golden 读不了）分开**：errored 不进 executed 但**进分母**。门：`exit.code = 0 iff 无 failures 且 无 errors`（严格全过）。`standard` 只是个 label 字符串（默认 "optest"），不参与阈值。

核心结论：torch-allclose 的**数值比对**（容差值、公式、nan/inf、equal_nan、int/bool 逐位、容错率=0）我们已忠实对齐；真正偏离在 index 判法（我们自造 gather 一致、cannbot 潜在是 exact-index）与 complex 支持（我们移除、cannbot 有）。
- **我们现状**：我们把 cannbot 的 allclose 口径落成 standard=`torch_allclose`：逐 dtype 容差 `_TA_DTYPE_TOLS`（存 **(rtol, atol)**，与 cannbot (atol,rtol) 顺序相反但值一一对上）fp16=(2^-10,9e-2) bf16=(2^-7,1e-1) fp32=(2^-13,1e-3) fp64=(2^-30,1e-6)；判据 `_allclose_close_mask` 用四象限**显式**实现 inf（同号 inf 相等 / 单侧·异号 inf 失配 / 有限位 `|o-g|<=atol+rtol*|g|` / equal_nan=True both-NaN 相等）——结果与 cannbot 的 np.allclose 语义等价、但更显式；judge `mismatch==0`（容错率=0）。整型/bool 由 `effective_standard` 强制 → EXACT（逐位）。容差 dtype 键取的是**据 spec IO 矩阵派生的输出 dtype**（`derive_output_dtype`），不是输入 dtype。多输出走契约：value 输出 torch_allclose、**index 输出用我们自造的 `index_value_consistency`——gather(self, idx) 两侧值 allclose**（tie 上允许下标不同但值一致）。complex64/complex128 已从容差表移除、SUPPORTED_COMPUTE_DTYPES 也不含 complex/bf16 真数组 → 复数输出 fail-closed。报告是三层口径（catlass_compare / standard_profile / acceptance_precision）逐 case 出 risk/gaps，粒度比 cannbot 的 by_dtype 聚合丰富。
- **gaps**：
  - [MEDIUM] index 类输出的判法与 cannbot 不同源：cannbot 只比第一个输出(values)、indices 从不比对，其潜在口径（若比）是「下标逐位精确」(int64→_EXACT_DTYPES)；我们用自造的 gather 值一致(index_value_consistency)，tie 容忍下标不同
    - 偏离：precision_policy.py:1156-1192 引入 gather-value-consistency，validator.py:133 把 INDEX 映射到 torch_allclose judge——这套在 cannbot-ops-input 里没有任何出处（accuracy_worker.py:73 只存 outputs[0]、accuracy.py:92 只读 golden_files[0]）
    - 对齐改法：在 precision_policy.py:126-135 及 index 判据处明确标注 index_value_consistency 系 OpRunway 原创、非 cannbot 口径，别让 provenance 注释暗示 cannbot；若要严格对齐 cannbot torch 对标，要么退回「多输出只校 value 输出」，要么显式声明我们比 cannbot 的潜在 exact-index 更宽(tie 容忍)并记差异，不冒充 faithful
  - [MEDIUM] complex64/complex128 容差被移除，复数输出 torch 对标算子 fail-closed；cannbot 支持 complex64(1e-3,2^-13)/complex128(1e-6,2^-30)
    - 偏离：precision_policy.py:131-135 把 complex 移出 _TA_DTYPE_TOLS，:146-149 SUPPORTED_COMPUTE_DTYPES 不含 complex → compute_metrics 对 complex 数组 fail-fast（cannbot accuracy.py:38-39,52-53 原生支持）
    - 对齐改法：要 faithful：把 complex64=(rtol=2^-13,atol=1e-3)/complex128=(2^-30,1e-6) 加回 _TA_DTYPE_TOLS，在 compute_metrics 的 TORCH_ALLCLOSE 分支实现按模长的 allclose（|o-g| 用复数绝对值），并把 complex64/complex128 加入 SUPPORTED_COMPUTE_DTYPES；否则在注释里把「有意收窄、非 cannbot 全集」写清楚
  - [LOW] 容差 dtype 键：cannbot 键在 case['dtype']（输入 dtype），我们键在 spec 派生的输出 dtype
    - 偏离：precision_policy.py:202-244 用 derive_output_dtype 取输出 dtype 作容差键；cannbot accuracy.py:93 用 case['dtype'] 输入 dtype（golden 却按 output_dtype 读，:92）
    - 对齐改法：这是我们改对了 cannbot 的一个潜在瑕（非同型算子如 IsClose float→bool 会键错）——不建议回退；只需在设计文档/注释里标明「此处有意偏离 cannbot、按输出 dtype 键更正确」，不要当成 bug 去对齐 cannbot
  - [LOW] 机读报告缺 cannbot 的逐 dtype atol/rtol 回显与 failed/errored 分桶
    - 偏离：我们报告是三层 per-case（catlass_compare/standard_profile/acceptance_precision + risk/gaps），无 cannbot 那种 by_dtype:[{...,atol,rtol,passed,failed,errored,pass_rate}] 聚合，也不像 cannbot 明确区分「跑了但错(failed)」与「kernel 崩(errored)」两桶（accuracy.py:648-665, 689-693）
    - 对齐改法：如需与 cannbot 证据对齐：在 validator 报告里补一个 by_dtype 聚合块，逐 dtype 回显实际 rtol/atol（threshold_for 已有）+ passed/failed/errored 分桶（errored 计入分母、不计 executed）、overall_pass_rate=passed/total；纯增字段、不动裁决逻辑
- **cannbot 证据**：repos/cannbot-ops-input/skills/operator-evaluation/scripts/accuracy.py:47-54 — _ALLCLOSE_TOLS (atol,rtol): fp16(9e-2,2^-10) bf16(1e-1,2^-7) fp32(1e-3,2^-13) fp64(1e-6,2^-30) complex64(1e-3,2^-13) complex128(1e-6,2^-30); accuracy.py:55 — _EXACT_DTYPES = int8/uint8/int16/int32/int64/bool（逐位精确）; accuracy.py:58-64 — _tols(): int/bool 返回 (0.0,0.0,exact=True); accuracy.py:71-82 — _matches_arr: shape 相等→np.array_equal(78)→np.allclose(rtol,atol,equal_nan=True)(81); accuracy.py:85-93 — case_matches_golden: 只读 golden_files[0](92)，容差键取 case['dtype'] 输入 dtype(93)，golden 按 output_dtype 读(91-92); accuracy.py:36-39 — _DTYPES['bfloat16']=np.float32（bf16 以 fp32 存储）; skills/operator-evaluation/scripts/accuracy_worker.py:73 — actual = np.asarray(result.outputs[0])（只取第一个输出，多输出的 indices 从不比对）; accuracy.py:648-665 — failed(数值错) vs errored(kernel 崩/调用错/golden 读错) 分桶; accuracy.py:672-701 — 报告 schema：overall_pass_rate=passed/total(688)，by_dtype 含 atol/rtol/pass_rate(689-693)，exit.code=0 iff 无 failures 且无 errors(699-700); accuracy.py:585 — standard: str = 'optest'（只是 label，不参与阈值）; grep -rnE 'MERE|MARE' --include=*.py repos/cannbot-ops-input → 空（MERE/MARE 不在 cannbot 代码里，torch 对标口径是 allclose）; skills/operator-evaluation/SKILL.md:133 — 'Verdict = per-dtype allclose tolerance ... (integers/bool exact)'；:252 passed=within atol/rtol；:264 errored 计入 pass-rate 分母
- **我方证据**：plugin/acc-common/precision_policy.py:136-141 — _TA_DTYPE_TOLS (rtol,atol) fp16/bf16/fp32/fp64，值与 cannbot accuracy.py:47-54 逐条对上（顺序相反）; precision_policy.py:126-135 — provenance 注释：抄自 cannbot accuracy.py:47-54；⚠ complex64/128 已移出（finding #9）; precision_policy.py:632-659 — _torch_allclose_tol：dtype_table/torch_default(1e-5,1e-8)/taskdoc 三源; precision_policy.py:662-673 — threshold_for(TORCH_ALLCLOSE)→{rtol,atol,equal_nan:True}; precision_policy.py:1015-1037 — _allclose_close_mask：inf 四象限显式 + equal_nan（对齐 np.allclose）; precision_policy.py:1131-1154 — compute_metrics(TORCH_ALLCLOSE)：mismatch/numel/max_abs/max_rel，不做 _replace_inf; precision_policy.py:1156-1192 — INDEX_VALUE_CONSISTENCY：gather 值 allclose（无 cannbot 出处，OpRunway 原创）; precision_policy.py:1040-1082 — _check_index_array + _gather_along_dim（下标越界/负数 fail-closed）; precision_policy.py:146-149 — SUPPORTED_COMPUTE_DTYPES 不含 complex，也不含 bfloat16 真数组; precision_policy.py:202-244 — derive_output_dtype：据 spec 派生输出 dtype 作容差键（非输入 dtype）; validator.py:114-133 — judge_torch_allclose：mismatch==0 才过；INDEX_VALUE_CONSISTENCY 复用同 judge; validator.py:169-200 — _precision_contract：spec/caseset/evidence 三处 canonical 全等

### 性能测试方式方法（perf test methodology · 仅 torch 对标 / Mode B 场景）—— cannbot perf_msprof+performance+performance_eval+render_report vs 我们 aclnn_runtime/perf_msprof.py + perf_compare.py
- **已忠实对齐**：False
- **cannbot 规则**：【采集器】msprof CLI「quick task-time」路径，命令固定 `msprof --output=<dir> --task-time=on --ascendcl=on --msproftx=on python <runner> ...`（perf_msprof.py:624-627）。延迟路径**明令不得带 `--aic-metrics`**（quick-runtime-design.md:70），也**不带 `--ai-core` 任何开关**（cannbot 只靠「不请求 aic-metrics 深指标」压掉硬件计数器，未显式关 ai-core）。PipeUtilization/ArithmeticUtilization/Memory/MemoryUB/MemoryL0/L2Cache/ResourceConflictRatio 全部 out-of-scope，留给官方 ops-profiling（quick-runtime-design.md:24-27）。

【warmup / repeat】默认 warmup=5、repeat=20（performance_eval.py:326-327；CLI evaluate.py:191-192/214-215 同值）。

【稳态怎么保证】① warmup 在 msprof 窗**之外**、用同一 persisted runner 在**同进程**先跑 max(0,warmup) 次（perf_msprof.py:399-403）；② warmup 后**重新物化新鲜 frozen 输入**（`call = make_call()`，注释 405-408：防 in-place/stateful 把 warmup 突变带进测量）；③ 只把被测 repeat 次迭代包进**设备 MSTX range**——ctypes `libms_tools_ext.so` 的 `mstxRangeStartA(range_name, torch.npu.current_stream().npu_stream)`，进出各一次 `torch.npu.synchronize()`（perf_msprof.py:409-430）；range 打不出直接 `raise`（421-421 fail-closed）。**custom 与 baseline 都走这同一条 ctypes-MSTX + msprof-CLI 通路**（同 runner，仅 `--side` 不同）。

【kernel 计时口径】纯 device kernel「Task Duration」= **kernel-only**，不含 H2D/D2H、不含 host wall（perf_msprof.py:82「must not use API launch duration as fallback」；custom_host_wall_us 已 deprecated，注释「CPU wall-clock intentionally not part of performance」645-652）。窗内解析：先用 msprof_tx_*.csv 的 `Device Start_time(us)/Device End_time(us)` 定唯一测量窗（_parse_measurement_window:41-82，多窗/缺窗→err），再在 task_time_*.csv 里过滤 `task_start(us)/task_stop(us)` 落在窗内的行。算法（_parse_task_time_measurement:112-176）：按 (kernel_name,kernel_type) 分组累计每次 launch 时长；**每 kernel 取 repeat 次的 median**（statistics.median，151）× **launches_per_invocation**（=len(times)/repeat，须整除否则 inconsistent 报错 147-150），**多 kernel 求和**（174）；`len(all_times) < repeat` 的一次性 setup/import kernel **剔除**（139-140）；launches 非整除 → 报 error 而非编数（147-150）。

【设备 kernel 白名单】`_DEVICE_KERNEL_TYPES = ("AI_VECTOR_CORE","AI_CORE","MIX_AIC","MIX_AIV","MIX","AI_CPU")`（perf_msprof.py:28-30，AI_CPU 是 device 上昇腾 kernel、非 host 回退）；`MEMCPY_ASYNC` 仅当**无任何计算 kernel**时单独作 `device_memcpy_only` 计时、**绝不加到已观察到的计算序列上**（164-173）。**无 HBM 带宽利用率、无矢量化比例**任何指标。

【行为五分类】npu / cpu_fallback / hybrid_host_device / no_device_kernel_observed / execution_failed（异常类 CpuFallbackDetected/HybridHostDeviceDetected/NoDeviceKernelDetected，perf_msprof.py:576-605；performance_eval.py:449-504 逐类兜）。**先查 CPU-fallback marker 再信任何解析出的 kernel**（739-746）；marker=`("npu_cpu_fallback","fall back to run on the CPU")`（573）——msprof 退 0 不代表在 device 跑。hybrid 靠 api_statistic_*.csv 数 `aclrtMemcpy` 次数（_parse_host_transfer_evidence:214-260，扣掉 tensor 参数一次性物化 allowance，剩余 ≥repeat 判 repeated host transfer），**custom 与 baseline 两侧都检测**。

【torch 基线（Mode B，当前唯一基线；Mode A/AscendOpTest builtin 已删】baseline = 冻结 torch reference，经 `make_schema_v3_baseline_call`（perf_runtime.py:107-135）在 **device(NPU) 上**跑、`torch.npu.synchronize` 包一次调用；torch API 用点分路径**通用解析**（`_resolve_torch_fn`，无 per-op 表，performance.py:42-53 / performance_eval.py:42-53）；kind ∈ {torch_api, torch_program/composed}。custom 侧走算子自己的 adapter（direct=torch.ops、generated=ModelNew、registry/aclnn=ctypes 单算子）。**msprof 抓的是谁跑就谁的 device kernel，故两侧同为 pure-kernel 口径**。

【达标阈值 & speedup 口径】`speedup = baseline_us / custom_us`（>1 表 custom 更快，performance.py:24-27）。达标线 `PERF_SPEEDUP_THRESHOLD = 0.6`——「custom 至少达 torch 基线 0.6× 才算性能合格」（performance.py:12-14）；`count_speedup_above` 用**严格 `ratio > 0.6`**统计（performance.py:80-95），产 `cases_above_threshold / cases_scored`。**不是硬 fail 门**——只报「加速比>阈值的用例数」+ `overall_speedup`（= Σ(baseline median×count) / Σ(custom median×count) 加权，performance.py:98-112）。阈值可经 build_performance_report 形参覆盖，但**非任务书驱动**、是写死常数 0.6。

【精度先筛】性能测量前先在隔离 worker 做精度筛选，失败/不匹配用例不进计时（performance_eval.py:353-416）。

【报告格式】operator_report.{json,md,html}（accuracy+performance 共享一个 run_id/时间戳目录）。perf 段（render_report.py:242-260）：`总体加速比: overall_speedup` + `加速比 > {threshold} 的用例数: cases_above_threshold / cases_scored` + `口径: comparability`（fair=两侧都 device kernel / partial / not_applicable，只有 fair 可直接作同口径结论）；`by_dtype` 表**每 dtype 一行**（summarize_latency 对同 dtype 的 custom/baseline 各取 median，performance.py:34-59）：dtype|coverage|baseline_us|custom_us|speedup|coverage_status；另有 custom_only_by_dtype（CPU-only 基线时只报 custom 绝对时延、不硬算 speedup）。perf 数据集固定 50 例、按 dtype/size_class 确定性配额选（performance_selection.py:9 TARGET=50）。

【小 shape】`classify_shape`/`SMALL_SHAPE_THRESHOLD_US=20.0` **仅定义、生产链未接线**（performance.py:10/30，只在 test_performance.py 用）——cannbot 实际不做小 shape 例外/免测。
- **我们现状**：我们拆同样两层：aclnn_runtime/perf_msprof.py（采集：产 us+scope+行为分类，不下达标结论）+ perf_compare.py（判定：ratio/target/status）。核心口径与 cannbot 高度对齐：warmup=5/repeat=20（perf_msprof.py:218-219）；CSV 计算 kernel 白名单六型与 cannbot 逐字相同（130-131）；kernel accounting = median×launches、setup 剔除、多 kernel 求和（口径常量 KERNEL_ACCOUNTING="median_x_launches" 227，稳态三规矩 docstring 64-77）；行为五分类同名（234-243）；CPU-fallback markers 逐字相同（275）；缺 MSTX 证据 fail-closed（ERR_WINDOW_REQUIRED 266，绝不靠 task 数反推窗）；TIMING_SCOPE="kernel_only"（216）；ratio 方向同为 baseline/npu（perf_compare.py:4-5）。但在采集入口、阈值语义、报告聚合上我们有意/被迫偏离：① 主路走 torch_npu.profiler 的 db（ROUTE_DB 优先 123-124；baseline 侧 COLLECTOR_TORCH_PROFILER 223），custom 侧才走 msprof CLI + ctypes mstx，且显式带 `--ai-core=off`（MSPROF_EXTRA_ARGS 229）——两侧采集器不同、还加了双边采集配置一致性闸（COMPARED_COLLECTION_KEYS 231-232、BLOCKED_INCOMPARABLE_COLLECTION_CONFIG 256）；② perf_compare 达标 = ratio >= target_ratio，target_ratio 来自 spec.perf.target_ratio（任务书驱动），缺基线声明时默认 0.95、比较用 `>=`（68-79、382）；③ perf_compare 只出逐例达标+status（summary 仅 perf_cases/达标/blocked/status 420-423），无 by_dtype median 聚合、无 overall_speedup 加权、无「cases_above_threshold/cases_scored」口径；④ 小 shape 例外真接线了（small_shape_exception 任务书驱动 129-149 + trivial-met numel<4096 217/358-361），且 hybrid 检测只作用于 baseline 侧（perf_msprof.py:85-88 docstring）。
- **gaps**：
  - [MEDIUM] 报告缺 cannbot 的三件套聚合口径：by_dtype median 汇总 + overall_speedup 加权 + 「cases_above_threshold / cases_scored」达标计数
    - 偏离：perf_compare.py:420-423 的 summary 只有逐例达标数/blocked/status，没有 cannbot render_report.py:242-260 展示的整体加速比与逐 dtype 中位数表
    - 对齐改法：在 perf_compare 产报告时补：(a) summarize_latency 式的每 dtype 取 median 一行（performance.py:34-59）；(b) overall_speedup=Σ(baseline median×count)/Σ(custom median×count)（performance.py:98-112）；(c) 用 cannbot 的严格 `ratio > threshold` 统计 cases_above_threshold/cases_scored，作为报告显性字段并入 summary，与既有逐例 status 并存（不替换硬门）
  - [LOW] 达标比较用 `>=`，cannbot 用严格 `>`
    - 偏离：perf_compare.py:382 `met = raw >= tgt`；cannbot performance.py:93 是 `ratio > threshold`
    - 对齐改法：若要与 cannbot 逐字一致，perf_compare.py:382 及 cases_above_threshold 统计改用严格 `>`；边界（恰等于阈值）语义差异极小，可作为一并对齐项，或在报告注记口径差异
  - [MEDIUM] 默认阈值 0.95 vs cannbot 0.6，且阈值来源不同（我们任务书驱动 / cannbot 写死 0.6）
    - 偏离：perf_compare.py:68-79 缺基线声明时兜底 0.95；cannbot 是全局常数 PERF_SPEEDUP_THRESHOLD=0.6
    - 对齐改法：这是有意分歧且更贴合 CLAUDE.md #0『任务书权威』——建议保留任务书驱动的 target_ratio，但把『无任务书目标时的兜底默认』从 0.95 改为 cannbot 的 0.6，并在报告里显式标注『阈值来源=任务书/兜底默认』，让缺目标时的口径与 cannbot 一致
  - [MEDIUM] 采集入口分裂：baseline 走 torch_npu.profiler(db)、custom 走 msprof CLI+ctypes mstx；cannbot 两侧统一 ctypes-MSTX + msprof-CLI(CSV)
    - 偏离：perf_msprof.py:8-12/123-124/222-226 —— 我们因 §9.7 A 实测『msprof CLI 下 Python 侧打不出 MSTX』把 baseline 侧改走 torch_npu.profiler db，引入了 db 路线 + taskType 数值 id 字典解析一整套 cannbot 没有的机制
    - 对齐改法：这是被真机 finding 逼出的偏离、非疏漏：faithful 基线是 cannbot 的『两侧同走 ctypes mstxRangeStartA + msprof CLI + CSV 解析』。建议不回退（回退会重蹈 §9.7 A 的静默失败），但须在设计 doc/报告明确记『本项目 baseline 采集通路与 cannbot 分歧及其实测依据』，并保留双边采集配置一致性闸（我们已有、cannbot 无）以防两侧口径漂移
  - [LOW] msprof 命令多带 `--ai-core=off`；cannbot 只『不请求 --aic-metrics』、不显式关 ai-core
    - 偏离：perf_msprof.py:229 MSPROF_EXTRA_ARGS 含 `--ai-core=off`（据 §9.7 C：默认 --ai-core=on 使数字虚高 2.0~3.75×）
    - 对齐改法：cannbot 的 faithful 做法是命令里只有 `--task-time=on --ascendcl=on --msproftx=on`、不带 aic-metrics 也不带 ai-core。因我们有真机实测的虚高证据，建议保留 `--ai-core=off` 但在注释/报告标注『此为对 cannbot 命令的一处有据偏离（§9.7 C），非口径遗漏』
- **cannbot 证据**：skills/operator-evaluation/scripts/performance.py:12-14 (PERF_SPEEDUP_THRESHOLD = 0.6，custom≥torch 0.6×); skills/operator-evaluation/scripts/performance.py:24-27 (speedup = baseline_us / custom_us); skills/operator-evaluation/scripts/performance.py:80-95 (count_speedup_above：严格 ratio > threshold); skills/operator-evaluation/scripts/performance.py:98-112 (build_performance_report：overall_speedup=Σmedian 加权 + by_dtype + cases_above_threshold/scored); skills/operator-evaluation/scripts/performance.py:34-59 (summarize_latency：每 dtype 取 median 一行) 及 10/30 (classify_shape/20us 仅定义未接线); skills/operator-evaluation/scripts/performance_eval.py:326-327,330 (warmup=5, repeat=20, speedup_threshold 默认=0.6); skills/operator-evaluation/scripts/perf_msprof.py:28-31 (_DEVICE_KERNEL_TYPES 六型 + MEMCPY_ASYNC); skills/operator-evaluation/scripts/perf_msprof.py:112-176 (kernel accounting：median×launches、setup 剔除、多 kernel 求和、非整除报错); skills/operator-evaluation/scripts/perf_msprof.py:41-82 (msprof_tx CSV 定唯一 MSTX 测量窗); skills/operator-evaluation/scripts/perf_msprof.py:399-430 (warmup 在窗外同进程 + 重新物化输入 + ctypes mstxRangeStartA + synchronize); skills/operator-evaluation/scripts/perf_msprof.py:573,624-627,739-746 (CPU-fallback markers；msprof 命令仅 --task-time/--ascendcl/--msproftx；先查 fallback 再信 kernel); ops-bench/docs/superpowers/specs/2026-07-23-operator-evaluation-quick-runtime-design.md:24-27,70 (PipeUtilization/Memory/L2Cache 等 out-of-scope；延迟路径不得带 --aic-metrics); skills/operator-evaluation/scripts/render_report.py:242-260 (报告：overall_speedup + cases_above_threshold/scored + comparability + by_dtype 表) 及 performance_selection.py:9 (perf 固定 50 例)
- **我方证据**：plugin/acc-common/aclnn_runtime/perf_msprof.py:218-219 (DEFAULT_WARMUP=5, DEFAULT_REPEAT=20，与 cannbot 同); plugin/acc-common/aclnn_runtime/perf_msprof.py:130-131 (CSV_DEVICE_KERNEL_TYPES 六型逐字同 cannbot) 及 216/227 (kernel_only / median_x_launches); plugin/acc-common/aclnn_runtime/perf_msprof.py:229 (MSPROF_EXTRA_ARGS 多带 --ai-core=off — cannbot 无此 flag); plugin/acc-common/aclnn_runtime/perf_msprof.py:64-77 (稳态三规矩 docstring：warmup 后重物化 + MSTX 圈窗，同 cannbot) 及 234-243/275 (行为五分类 + CPU-fallback markers 逐字同); plugin/acc-common/aclnn_runtime/perf_msprof.py:8-12,123-124,222-226 (采集入口分裂：baseline=torch_npu.profiler db 优先，custom=msprof CLI+ctypes mstx —— 与 cannbot 两侧统一 ctypes-MSTX+msprof-CLI 不同); plugin/acc-common/aclnn_runtime/perf_msprof.py:231-232,254-256 (COMPARED_COLLECTION_KEYS + BLOCKED_INCOMPARABLE_COLLECTION_CONFIG 双边采集配置一致性闸 —— cannbot 无); plugin/acc-common/perf_compare.py:4-5,68-79 (ratio=baseline/npu；target_ratio 来自 spec，缺声明默认 0.95 —— cannbot 写死 0.6); plugin/acc-common/perf_compare.py:382 (met = raw >= tgt，用 >= —— cannbot 用严格 >); plugin/acc-common/perf_compare.py:420-423 (summary 仅 perf_cases/达标/blocked/status —— 无 by_dtype/overall_speedup/cases_above_threshold); plugin/acc-common/perf_compare.py:129-149,217,358-361 (小 shape 例外+trivial-met 真接线 —— cannbot 的 classify_shape/20us 仅定义未接线)

### torch 封装算子的方式方法：reference/baseline/golden 的 torch 调用封装（op→一次 torch 调用的形参映射、attr 传参、多输出、dtype、device；torch 参考用于精度真值 vs 性能基线；语义差异如 median 全局/按维/index）
- **已忠实对齐**：False
- **cannbot 规则**：cannbot 的核心是「一份声明式 frozen reference，golden 与 perf 基线共用同一份、由单一通用解释器执行，全程零 op-name 分支」。

(1) op→一次 torch 调用怎么映射：靠两样声明式数据，无任何 per-op 代码。
  · reference.kind 二选一：`torch_api`=一个点路径 callable（15/16 个算子这样，如 torch.median/torch.pdist/torch.isclose/torch.sgn/torch.index_select/torch._foreach_add）；`torch_program`=一段被校验的迷你 AST（statements+outputs，1/16，aclnnSyncBatchNormGatherStats）。
  · reference.binding=把该 torch 签名**逐参冻结**（每参 name/index/parameter_kind∈{positional_only,positional_or_keyword,keyword_only}/annotation/has_default/default），再 `json.dumps(sort_keys) → sha256` 封印；eval 期 `validate_torch_binding` 会重算指纹核对，torch 版本变了 API 就报错。
  · 每条 case 带**完整有序调用计划** `arguments`：list of `{name, passing∈{positional,keyword,omitted}, value:{kind:...}}`；value.kind 词表 = tensor/literal/torch_symbol/list/tuple/dict/construct。`materialize_call` 是**唯一**通用解释器，明文声明「无 op-name 分支、无形参名推断」。
  · 形参映射：positional/keyword 由每个 argument 的 passing 决定；名字来自冻结签名。attr 传参=`{kind:literal,value:X}` 原样落位（如 Pdist 的 p=`{kind:literal,value:0}` 位置传）。

(2) dtype/device 处理：
  · tensor 节点带三段 dtype——`dtype`(语义,如 bfloat16)/`storage_dtype`(盘上,如 float32)/`torch_dtype`("torch.bfloat16")；按 storage_dtype 读 numpy → `tensor.to(want)` 转语义 dtype；bf16 落盘为 float32 字节、构 tensor 时 cast。
  · device 参数**按冻结 annotation 里含 "torch.device" 识别、不看名字**；perf 基线在 NPU 计时用 `override_reference_device=True` 把 device-typed 字面量替换成目标 device。

(3) 用于精度真值还是性能基线——**都用，同一份 reference**：
  · 精度 golden=**生成期在 CPU** 跑 reference（generate_engine device="cpu"，freeze_reference 写进 manifest，provenance baseline_device:"cpu"），冻结成 golden.bin；eval 期只读字节、绝不重算。封装函数=`invoke_frozen_torch(design,args,kwargs)->output`。
  · perf 基线=**同一 reference 在 NPU** 跑、msprof 计 kernel-only Task Duration。封装函数=`make_schema_v3_baseline_call(reference,case_root,case,*,device)->call()`，call() 一次同步调用返回 output。speedup=baseline_kernel/custom_kernel。
  · 计时口径硬数字：warmup=5、repeat=20（evaluate.py 与 performance_eval.py 双处默认一致）；每 kernel 取 repeat 次**中位数** launch × launches_per_invocation=单次调用耗时，多 kernel 求和；MSTX 只圈被测迭代；torch 回退 CPU 有 marker 检测（假计时判 CpuFallbackDetected）。

(4) 语义差异（median 全局/按维、index）：**只靠选哪个冻结 overload + arguments 里 dim 是否在场，零 op 分支**。aclnnMedian 冻结的是 `torch.median(input,dim,keepdim)->(Tensor,Tensor)` 这个 DIM overload；index 输出由 torch 二元组返回天然产生 → 冻结成 2 个 golden 文件；多输出由 `_output_leaves`/torch_program outputs 拍平。
  · ⚠ 但 cannbot 精度比对**只比 golden_files[0]**（accuracy.py case_matches_golden 只读第 0 个输出）——即 median 只比 values、**根本不比 indices**，没有 gather 一致性判据。
  · 容差：dtype 驱动的 allclose `(atol,rtol)`：fp16=(9e-2,2^-10)、bf16=(1e-1,2^-7)、fp32=(1e-3,2^-13)、fp64=(1e-6,2^-30)、complex64=(1e-3,2^-13)、complex128=(1e-6,2^-30)，int/bool 走 exact(0,0)，allclose(equal_nan=True)；一手出自 tilelang2ascend verification_ascendc.py。design 里的 comparison.mode(exact/mixed_tolerance) 实际**不被 accuracy.py 消费**，容差纯由 dtype 表决定。
  · cannbot 自己的**唯一破例**：perf 路径对 `torch.segment_reduce` 按 api 名硬分支（string-reduce + offsets kwarg 形态），这是 cannbot 自身对「零分支」的违背，别照抄。
- **我们现状**：我们有**两套彼此独立**的 torch 封装机制，而非 cannbot 的一套：

A) perf 基线（cannbot-spirit、声明式）：spec `perf.torch_baseline={"api":"torch.median","positional":["self"],"keyword":{"dim":"dim","keepdim":"keepdim"}}`。`resolve_torch_baseline_plan(torch_baseline,call)` 把该 case 已解析的 `aclnn_call.slots` 按 **slot name**（= aclnn 头形参名）翻成 torch 调用：positional=slot 名列表按序作位置参数（缺任一 slot→fail-closed），keyword=slot名→torch形参名（该 case 没这个 slot 就自然缺席，全局 median 无 dim 自动跟随）。op-中立、零 op 分支。基线 wrapper materialize()：tensor 从 case.inputs[slot.input_idx] 读、scalar 取 slot.value；bf16 用 torch.frombuffer 按位重解释（不做数值转换）；device 由 plan 显式给（torch.npu.set_device+.to），绝不假定 0 卡。用 torch_npu.profiler+MSTX 计时，warmup=5/repeat=20。**只用于 perf 基线，不产 golden。**返回 {api,positional:[slot],keyword:{kwarg:slot}}。

B) 精度 golden（per-op 命令式 Python）：每个算子手写 `golden_fn(inputs,attrs)->ndarray|tuple`，op→torch 的形参/attr 映射**焊死在 Python 代码里**（Median: t.median(x,dim=d,keepdim=...)；IsClose: t.isclose(a,b,rtol,atol,equal_nan)）。语义分派（全局/按维）靠 golden_fn 内 `attrs.get("dim") is None`（据字段、无 op 名分支，但仍是 per-op 代码）；多输出返回 tuple；另需 per-op `out_shape(in_shapes,attrs)`。golden 生成期 CPU 冻结成 golden_{k}.npy，torch 缺失 fail-closed。

C) 容差/口径（precision_policy）：`TORCH_ALLCLOSE` 标准 + `_TA_DTYPE_TOLS`（存 (rtol,atol)，逐字 adapt 自 cannbot accuracy.py:47-54，注释明标 provenance）；index 输出走 `index_value_consistency`（gather(self,idx) 值一致），**比 cannbot 多做一层**（cannbot 只比 values）。
- **gaps**：
  - [HIGH] cannbot 一份 reference 同源产 golden+perf 基线；我们 golden(per-op Python) 与 perf 基线(spec.torch_baseline) 是两套独立映射，会各自漂移
    - 偏离：同一算子的 op→torch 调用被写了两遍且用不同寻址：perf 基线按 slot NAME（perf_msprof.py:1553-1564），golden 按位置 inputs[0]+attrs 字典（Median/golden.py:110-116）；两处对 overload/默认值/参数顺序的理解可能不一致，且无任何机制强制它们等价——cannbot 结构上不可能漂移（单一 reference）
    - 对齐改法：让 golden 与 perf 基线共用**同一份声明式 torch reference**：把 golden 的 op→torch 调用也改为消费 `perf.torch_baseline`（或提升成统一的 `reference` 字段），由单一通用解释器执行；golden_fn 退化为『拿 reference+case → 跑 torch』的通用函数，per-op golden.py 只留 authorization/tier 文本。改点：gen_cases.load_golden + 新增通用 torch_reference 解释器，替掉 samples/golden/*/golden.py 里手写的 t.median/t.isclose 调用
  - [HIGH] golden 的 op→torch 映射是 per-op 命令式 Python 代码，不是声明式数据+通用解释器（cannbot 是纯 JSON reference+arguments + 单一 materialize_call）
    - 偏离：新增一个域内算子要手写一份 golden_fn（含正确的 attr→kwarg 映射、全局/按维分派、out_shape），而 cannbot 只需一份被 review 的 reference + 生成的 arguments JSON；per-op 可执行代码承载了本该是数据的调用映射（Median/golden.py:105-116、IsClose/golden.py:72-79）
    - 对齐改法：把『op→一次 torch 调用』下沉为声明式数据（api + 每参 positional/keyword + value.kind 词表），配一个通用解释器（对标 case_call.materialize_call）；out_shape 尽量由 torch 实跑推出（cannbot 直接落盘实测形状，无需 per-op out_shape）
  - [MEDIUM] 无冻结签名/指纹：torch 版本改了 API 我们不会发现
    - 偏离：perf.torch_baseline 映射与 golden_fn 都没有对 torch 签名的冻结+sha256 复核；cannbot 用 freeze_torch_binding 封印签名、eval 期 validate_torch_binding 重算指纹（reference_resolver.py:145-204）
    - 对齐改法：给 torch reference 增加一份冻结 binding（参数 name/kind/default）+ sha256，运行/采集前重算核对，指纹不符 fail-closed；至少覆盖 perf.torch_baseline.api 的签名
  - [MEDIUM] perf.torch_baseline 只是扁平 positional/keyword 映射，表达力弱于 cannbot 的 per-case arguments(value.kind 词表)
    - 偏离：我们基线 wrapper 只认 role=in(tensor) 或 slot.value(scalar)（perf_msprof.py:1783-1789）；cannbot 支持 tensor/literal/torch_symbol/list/tuple/dict/construct（case_call.py:82-122）——需要构造参数(torch.tensor(...).cuda())、torch 符号枚举、list/dict 参数的算子，我们的基线无法表达
    - 对齐改法：把 perf.torch_baseline 的值词表扩到 cannbot 的 value.kind 集合（或直接复用一套共享 arguments schema），基线 wrapper 的 materialize() 相应支持这些 kind
  - [MEDIUM] perf 基线缺 torch_program（多步声明式参考）；多步语义算子无对齐的 perf 基线
    - 偏离：perf.torch_baseline.api 必须是单个 torch.* callable（perf_msprof.py:1540-1541）；golden_fn 虽能任意多步 Python，但那是命令式逃生口、非受校验的声明式 program，且正因此 perf 基线无法与之匹配→又回到漂移。cannbot 有 torch_program（16 个算子里 1 个实用，SyncBatchNormGatherStats）
    - 对齐改法：给 perf 基线支持声明式 program 形态（对标 reference_resolver 的 torch_program 校验 + perf_runtime._run_torch_program），使多步语义算子的 golden 与 perf 基线仍同源
  - [LOW] （反向）我们 index 输出做 gather 一致性判据，cannbot 只比 golden_files[0]——这是我们更强、非缺陷，但『faithful to cannbot』时须知 cannbot 根本不比 index
    - 偏离：precision_policy.index_value_consistency（:81-82,372-374）对 median 的 indices 做 gather(self,idx) 一致性；cannbot accuracy.py:92 只读第 0 输出、完全不比 indices。方向相反：保留我们的更好，别为对齐 cannbot 而砍掉
    - 对齐改法：不改（保留 index_value_consistency）；仅在文档标注：此处刻意优于 cannbot，非未对齐
- **cannbot 证据**：skills/operator-case-generation/common/reference_resolver.py:145-163 freeze_torch_binding——torch_api/torch_program 冻结 + _seal sha256 封印; skills/operator-case-generation/common/reference_resolver.py:217-255 invoke_frozen_torch——执行冻结 reference 产 golden（含 torch_program AST 求值）; skills/operator-evaluation/scripts/case_call.py:5-7,57-139 materialize_call——唯一通用解释器，明示『无 op-name 分支/无形参名推断』，per-case arguments→(args,kwargs); skills/operator-evaluation/scripts/case_call.py:84-103 tensor 节点 dtype/storage_dtype/torch_dtype 三段 + .to(device)；:48-54,74 device 参数按 annotation 识别; skills/operator-evaluation/scripts/perf_runtime.py:107-135 make_schema_v3_baseline_call——同一 reference 作 perf 基线，返回零参同步 call(); skills/operator-case-generation/scripts/gen/generate_engine.py:97,116(device=cpu),152(freeze_reference),158(baseline_device:cpu)——golden 生成期 CPU 冻结; skills/operator-evaluation/scripts/accuracy.py:47-64 _ALLCLOSE_TOLS 逐 dtype (atol,rtol)+_tols；:85-93 case_matches_golden 只比 golden_files[0]（单输出）; ops-bench/ops-eval-dataset/designs/aclnnMedian/case_design.json reference.kind=torch_api torch_api=torch.median overload=(input,dim,keepdim)->(Tensor,Tensor)——全局/按维靠选 overload; skills/operator-evaluation/scripts/evaluate.py:191-192 warmup=5 repeat=20；performance_eval.py:326-327 同; skills/operator-evaluation/scripts/performance_eval.py:80-83,125-130 torch.segment_reduce 按 api 名硬分支（cannbot 自身破例）; skills/operator-evaluation/scripts/adapters/model_new.py:16-21 _output_leaves 多输出拍平；ops-bench/ops-eval-dataset/designs/aclnnSyncBatchNormGatherStats reference.kind=torch_program（多步 AST 实证）
- **我方证据**：plugin/acc-common/aclnn_runtime/perf_msprof.py:1518-1565 resolve_torch_baseline_plan——slot-name→torch 形参映射（positional slot 名、keyword slot→kwarg、缺 slot 自然缺席）; plugin/acc-common/aclnn_runtime/perf_msprof.py:1732-1844 _BASELINE_WRAPPER materialize()/invoke()；:1771-1781 to_tensor（bf16 frombuffer 位重解释、.to(device)）；:1755 set_device; plugin/acc-common/aclnn_runtime/perf_msprof.py:218-219 DEFAULT_WARMUP=5 / DEFAULT_REPEAT=20（与 cannbot 一致）; plugin/samples/golden/Median/golden.py:105-116 golden_fn 命令式 per-op torch.median 分派（attrs.get('dim') is None 全局/按维）；:88-102 out_shape; plugin/samples/golden/IsClose/golden.py:72-79 golden_fn 命令式 torch.isclose(a,b,rtol,atol,equal_nan); plugin/acc-common/precision_policy.py:125-142 _TA_DTYPE_TOLS adapt 自 cannbot accuracy.py:47-54（注释标 provenance），存 (rtol,atol); plugin/acc-common/precision_policy.py:76,81-82,260-380 TORCH_ALLCLOSE 标准 + index_value_consistency（gather 值一致，据 out_role 字段派生）——超出 cannbot; plugin/samples/specs/median.spec.json perf.torch_baseline 真实声明（api/positional/keyword，_note 强调 op-中立）


---

## 综合对齐改动清单（可执行）

验证完毕。以下是综合后的可执行对齐改动清单。所有路径均为绝对定位到文件/函数/行。

---

# torch 对标验收体系 · 对齐 cannbot-ops-input 可执行改动清单

## 0 · 贯穿所有改动的一条护栏（先立，否则每条都会误伤已验收通路）

**问题**：D1/D4 的多数改动落在 `gen_cases.py` 与 golden 加载路径上,这两条路径被 4 个已验收 elementwise 算子(IsClose/Sign/Equal/Neg)**逐字节钉死**(per-case 稳定种子 + `_emits_nonfinite` 向后兼容硬约束,`test_gen_cases_multi_output.py:1279` 等实测钉住)。任何改 gen_cases 默认行为的动作都会改这 4 份 caseset/golden 的字节 → 破坏 pin。

**统一护栏(= 泛化机制 + 字节安全机制,一箭双雕)**:新增一个**字段驱动**的开关
`spec.precision.case_profile`(受控词表:缺省/`"legacy"` → 现行为;`"torch_parity"` → cannbot 忠实网格),在 `gen_cases._plan`(约 `plugin/acc-common/gen_cases.py:1710` 附近入口)读一次,H2/H3/M1/M2/M5/L2 全部**只在 `torch_parity` 分支生效**。
- 律令#0 合规:这是按「spec 声明的能力档位」分支,**不是按算子名**,换任意声明 `torch_parity` 的域内算子零改即用。
- 字节安全:未声明的算子(含 4 个 pin 算子 + 当前 Median)走 legacy → 输出字节不变。
- 备选:也可直接以 `precision_policy.select_standard(spec)==TORCH_ALLCLOSE`(即 `oracle==torch`)作触发信号,省一个字段;但 gen_cases 要多依赖一层 precision_policy 路由,显式字段更清晰、可独立于精度标准控制造例。**推荐显式字段**。

**Median 的处理**:Median 现在 PASS,但它**尚未**声明 `torch_parity`。落地顺序应为——先实现护栏+parity 行为(此时 Median 仍走 legacy、PASS 不变),再单独把 `median.spec.json` 翻成 `case_profile:"torch_parity"`、**重生成 golden + 真机重验**。重验绿之前 Median 不翻档。这样任何一步都不破已通过通路。

---

## 高优先级(high)

### H1 —— 【D4·gap1+gap2】golden 与 perf 基线是两套独立 op→torch 映射,会各自漂移;且 golden 是 per-op 命令式代码而非声明式数据+通用解释器

**偏离的 cannbot 规则**:cannbot 一份声明式 `reference`(torch_api + 冻结 binding + per-case `arguments`)被**单一** `materialize_call`(`case_call.py:57-139`,明示「无 op-name 分支」)执行,golden(CPU)与 perf 基线(NPU)**同源**、结构上不可能漂移。我们:perf 基线走声明式 `perf.torch_baseline`(slot-name 驱动,已 op-中立),但 golden 走 per-op 手写 `golden_fn`(`plugin/samples/golden/Median/golden.py:105-116` 的 `t.median(x,dim=d,keepdim=...)`),同一算子的 op→torch 调用被写了两遍、用两种寻址(perf 按 slot name / golden 按 `inputs[0]`+attrs 字典),无机制强制等价。

**具体改哪里、改成什么**:
1. 新增共享模块 `plugin/acc-common/torch_reference.py`,函数 `invoke_reference(reference, resolved_slots, inputs, attrs, *, device="cpu") -> ndarray|tuple`——**唯一**通用 torch 解释器,消费与 perf 基线**同一份**声明(`perf.torch_baseline`,或将其提升为顶层 `reference` 字段),据 slot role(in→tensor from `inputs[slot.input_idx]`、attr→`slot.value`)物化,`_resolve_torch_fn` 点路径通用解析 `api`,CPU 上调用返回 numpy。零 op 分支。
2. `plugin/acc-common/gen_cases.py:214 load_golden`:当 spec 走 `torch_parity` 且声明了 reference、且算子目录**无** `golden_fn` 时,golden 改由 `torch_reference.invoke_reference` 产;`Median/golden.py` 退化为只留 `GOLDEN_SOURCE`/`GOLDEN_PROVENANCE`/`GOLDEN_CONTRACT`/authorization 文本(provenance 仍逐字属实),**删掉手写 `golden_fn`/`out_shape`**——out_shape 由 torch 实跑输出形状推得(对标 cannbot 直接落盘实测形状)。
3. perf 基线侧(`perf_msprof.py:1518 resolve_torch_baseline_plan` + baseline wrapper)复用同一 reference 解释器,保证「golden 的 torch 调用」与「perf 基线的 torch 调用」是同一份数据的两次执行(一在 CPU 产真值、一在 NPU 计时)。

**会不会破已通过通路 / 要不要保护**:
- 4 个 pin 算子:它们**无** reference 字段、不走 torch_parity → `load_golden` 仍加载其 `golden_fn`,**零影响**。护栏是「有 reference 且 torch_parity 才走新路」。
- Median:会从「手写 golden_fn」切到「reference 解释器产 golden」——**必须**加一个等价性测试:对同一批 case,新解释器产出的 golden 与旧 `golden_fn` 逐字节一致(容 tie/index 语义),通过后才删旧 `golden_fn`。这是本清单里回归风险最高的一项,务必先加等价 pin 再切。

**律令#0**:reference + 通用解释器是纯字段驱动、无算子名分支;全局/按维 median 靠「该 case 的 slots 里有没有 `dim`」自动跟随(与现 perf 基线同机制),不写任何 median 特判。仅 torch_parity 生效,不碰 catlass/new_example。

---

### H2 —— 【D1·gap1】数量口径根本不同:cannbot accuracy=完整笛卡尔无上限 + perf 恰选 50(两套);我们单套 `case_target=50` 封顶 + 1-wise 边际采样,精度覆盖被压成 50 条采样

**偏离的规则**:cannbot accuracy = dtype×format×rank×shape×value×attr 完整笛卡尔减排除(IsClose 1728/Sign 192/Scatter 3384),`design_contract.py` 明写「不设固定精度用例下限」;perf 另起、`performance_selection.py:10 TARGET=50` 恰选 50。我们 `gen_cases.py:1852 budget=max(case_target,len(forced))` + `_one_wise_pick` 采样,`:1872` 自认「50 封顶下 100% 正交不可达」。

**具体改哪里、改成什么**:`gen_cases.py:_plan`(1826-1854):
- `torch_parity` 分支下,**精度网格全量展开**——第 ③ 段常规正交网格(1826-1837)照产,但**不再**过 `_one_wise_pick` 封顶,`entries = forced + 全部 grid`(去掉 `budget - len(forced)` 的采样裁剪)。
- `case_target` 语义从「总用例封顶」窄化为「perf 选例目标数」——即它不再限制 accuracy 条数,只喂给 M5 的 perf 选例。
- `meta.coverage_strength` 相应改成「torch_parity:accuracy=full Cartesian、perf=50 quota」。
- legacy 分支一字不动。

**会不会破 / 保护**:
- 4 个 pin 算子(legacy)不受影响。
- 真机成本:torch_parity 算子 accuracy 可能上千条 → golden 生成 + 真机跑测时间成倍涨。`_GOLDEN_COST_BUDGET`(985)只挡**单条 shape 规模**、不挡**条数**。**必须**向用户明示这笔成本(faithful 的代价);建议保留一个 `precision.accuracy_cap`(可选)给「愿意 faithful 但要限量」的算子,缺省=无限(faithful)。
- Median 翻档后条数会从 ~50 跳到完整笛卡尔,需接受更长真机时间。

**律令#0**:全量展开是通用网格逻辑,无算子分支;仅 torch_parity。

---

### H3 —— 【D1·gap2】shape 阶梯缺 medium 档,且非 cannbot 的「base×size×rank padding」结构;size 门槛 2^16 ≠ cannbot 2^18

**偏离的规则**:cannbot `small=31(2^5-1)/medium=2047(2^11-1)/large=262144(2^18)`,每档 `shape=[base]+[1]*(r-1)`,numel 恒等于 base、与 rank 解耦,ranks 覆盖 1..8;size_class 边界 `small≤2^10 / medium 2^10..2^18 / large≥2^18`(`design_contract.py:32-34`)。我们 `gen_cases.py:952-961` `_REG_SHAPES` 全是 numel 3-255(全 small)、`_LARGE_SHAPES` 直跳 2^20、**无 medium**,`:897 _LARGE_NUMEL=2^16`。

**具体改哪里、改成什么**:
- 新增 `torch_parity` 专用阶梯构造:在 `_shape_ladder`(1317)/`_dtype_shapes`(1337) 里,torch_parity 时改用「按 size_class 取单基准维、其余补 1 到目标 rank」生成器——`small_base=31/medium_base=2047/large_base=262144`,对每个 rank r∈ranks 产 `[base]+[1]*(r-1)`,三档 × ranks 全覆盖。
- `_shape_class`(900-917)/`_LARGE_NUMEL`(897):torch_parity 下 size 边界改 `small≤2^10 / medium 2^10<n<2^18 / large≥2^18`,对齐 `design_contract.py:32-34`。
- legacy 保留现 `_REG_SHAPES`/`_LARGE_SHAPES`/`2^16` 不动。

**会不会破 / 保护**:legacy 阶梯(4 pin 算子 + 现 Median)完全不变。torch_parity 算子 shape 集换新 → golden 重生成、真机重验(与 H2 同批)。`_MAX_NUMEL=2^31`(962)上限仍守;注意 262144×(1)^(r-1) 仍 ≤2^31、安全。

**律令#0**:base×rank 生成器是纯结构逻辑,无算子分支;仅 torch_parity。

---

## 中优先级(medium)

### M1 —— 【D1·gap3】normal 值域偏离:cannbot 每 case 从 μ∈[-5,5]、σ∈[0.1,2] 采样、不 clip、不锚定;我们固定 μ=0/σ=1、clip[-5,5]、锚定前 3 元素(-2,0,3)

**改哪里**:`gen_cases.py:_make_varied`(354-360)。torch_parity 分支:`mu=rng.uniform(-5,5); sigma=rng.uniform(0.1,2); x=rng.normal(mu,sigma,shape)`,**去掉** `np.clip(...,-5,5)`(355)与 `f[0:3]=-2,0,3` 锚定(359-360)。对齐 `case_generator.py:48-53`。`_NORMAL_MU/_NORMAL_SIGMA`(966)仅 legacy 用。
**保护**:锚定(-2,0,3)是我们为 Sign 分支覆盖发明的、cannbot 无;legacy 保留(4 pin 算子的 Sign 覆盖靠它)。仅 torch_parity 去掉。int dtype 的锚点(343-352)属另一档(L2),此项不动整数路径。
**律令#0**:纯值域逻辑,字段档位驱动。

### M2 —— 【D1·gap4】非有限特殊值形态偏离:cannbot 4 kind(nan/pos_inf/neg_inf/mixed_inf)、同 dtype 各用不同 shape(3/7/15/31)、整张量循环填充;我们 3 kind、都用 (16,)、前 1/4 填充+其余 uniform、无 mixed_inf

**改哪里**:`gen_cases.py:_special_entries`(1488-1493)与 `_build_value_special`(419-433)。torch_parity 分支:
- 补 `mixed_inf`(值 `[+inf,-inf,0]`);4 kind 各分配不同 shape `[3]/[7]/[15]/[31]`(2^n-1),对齐 `design_contract.py:391-393`。
- special 数据改**整张量循环填充**(`np.resize(values,size)`,对齐 `case_generator.py:37-42`),取代「前 1/4 特殊值 + 其余 uniform」(430)。
- shape_upper_boundary 我们已是 2^20,与 cannbot `2^20` 一致,不动。
**保护**:legacy 保 3 kind/(16,)/前 1/4 填充(4 pin 算子的 inf/nan 用例字节靠它)。二元算子(IsClose)的对齐放置语义在 torch_parity 下要复核 mixed_inf 的二元对齐仍成立。
**律令#0**:kind 表 + shape 分配是通用数据,字段驱动。

### M3 —— 【D3·gap1】perf 报告缺 cannbot 三件套聚合:by_dtype median 汇总 + overall_speedup 加权 + cases_above_threshold/cases_scored

**改哪里**:`perf_compare.py` 出报告处(`_compare` 尾部,420-427 summary 组装)。**纯增字段、不动裁决**:
- `by_dtype`:对每 dtype 取 npu/baseline 的 median 各一行(对标 `performance.py:34-59 summarize_latency`);
- `overall_speedup = Σ(baseline median×count)/Σ(npu median×count)`(对标 `performance.py:98-112`);
- `cases_above_threshold/cases_scored`:用严格 `ratio > tgt` 统计(对标 `performance.py:80-95`),作**显性报告字段**并入 summary,与既有逐例 `达标`/`status` 硬门**并存不替换**。
**保护**:全部新增只读字段,既有 per-case status/simulation/门逻辑不变 → 4 算子 + Median perf 通路零影响。`test_perf_compare.py` 补新字段断言。
**律令#0**:聚合按 case 的 dtype 分组,无算子名分支;这是 torch 对标(Mode B)报告增强,不影响 catlass 的 catlass_compare 口径。

### M4 —— 【D3·gap3】无任务书目标时的兜底默认 0.95 vs cannbot 0.6

**改哪里**:`perf_compare.py:_resolve_target_ratio`(68-79),**只**改「缺 target_ratio 且未声明基线」那一支(75)的兜底常数 `0.95 → 0.6`;并在报告里显式标 `target_ratio_source ∈ {taskdoc, fallback_default}`。
**保护**:任务书驱动的 target_ratio(承 CLAUDE.md#0「任务书权威」)保留不动——**只**动无目标时的兜底。「声明了基线却缺 target_ratio→invalid」(73-74)那道更严的门不能动。现有测试若断言 0.95 需同步。
**律令#0**:合规。这是缺目标时向 cannbot 口径靠拢,非算子特判。

### M5 —— 【D1·gap5】无 cannbot 式 perf 50 选例(dtype 配额):cannbot 硬选 50、core fp32/16/bf16 各 10、complex 各 5、float64 5、其他 ≥3、留 small+large;我们 `select_perf_cases` 只按「性能」tag 全选、无配额无 50

**改哪里**:`perf_msprof.py:select_perf_cases`(1377-1396)。torch_parity 下,在「dims 含性能 + 过精度筛」的候选池上,移植 `performance_selection.py` 的确定性 50 选例:CORE(fp32/16/bf16)各 10、complex 各 5、float64 5、其他 dtype 各 ≥3、配额档保留 small+large、禁 empty/非有限、确定性填满 50,不足即失败。
**保护**:这是被 H2 直接触发的必要项——H2 让 accuracy 变完整笛卡尔后,perf 必须另起 50 子集(否则「性能」tag 会选中上千条)。非 torch_parity 算子保留现「全选性能 tag」。⚠ 我们 perf 走真机 msprof、判定模型本就与 cannbot 不同,**是否严格对齐 50 配额需用户裁**(可作为 H2 的配套或独立评估)。
**律令#0**:配额按 dtype/size_class,无算子分支。

### M6 —— 【D4·gap3】无冻结签名/指纹:torch 版本改了 API 我们不会发现

**改哪里**:配合 H1,在 `torch_reference.py` 里给 reference 增一份冻结 binding(每参 name/kind/default)+ sha256(对标 `reference_resolver.py:145-204 freeze_torch_binding`/`validate_torch_binding`);gen_cases 产 golden 前、perf 采集前各重算指纹核对,不符 fail-closed。至少覆盖 `perf.torch_baseline.api` 的签名。
**保护**:纯新增校验,指纹匹配时行为不变。首次落盘指纹需人工确认当前 torch 版本的签名基线。
**律令#0**:签名冻结是通用机制,无算子分支。

### M7 —— 【D2·gap2】complex64/128 容差被移除,复数输出 torch 对标算子 fail-closed;cannbot 支持 complex64(1e-3,2^-13)/complex128(1e-6,2^-30)

**改哪里**:两条路二选一,**须用户裁**:
- (a) faithful 补齐:`precision_policy.py:_TA_DTYPE_TOLS`(136-141)加回 `complex64=(2^-13,1e-3)/complex128=(2^-30,1e-6)`;`compute_metrics` 的 TORCH_ALLCLOSE 分支(1131-1154)实现按复数模长的 allclose(`|o-g|` 用复数绝对值);`SUPPORTED_COMPUTE_DTYPES`(146-149)加 complex64/128。
- (b) 保持收窄:不改代码,只把 `precision_policy.py:131-135` 的注释从「等于移除」改成明确「有意收窄、非 cannbot 全集」,别让 provenance 暗示已 faithful。
**保护**:现无复数算子,(a) 是纯扩能力、不破现通路;(b) 零改。当前是 finding #9 有意移除(声明与实现不一致更坏),补齐时必须**同时**改容差表 + compute_metrics + 支持集三处,否则重蹈 finding #9。
**律令#0**:两条都合规。

### M8 —— 【D4·gap4+gap5】perf.torch_baseline 表达力弱于 cannbot arguments;且缺 torch_program 多步声明式参考

**改哪里**:`perf_msprof.py` baseline wrapper `materialize()`(~1771-1789,现只认 role=in tensor / slot.value scalar)。把值词表扩到 cannbot `case_call.py:82-122` 的 `value.kind` 集合(tensor/literal/torch_symbol/list/tuple/dict/construct);并给 reference 支持 `torch_program`(多步 AST,对标 `reference_resolver` 的 program 校验 + `perf_runtime._run_torch_program`),使多步语义算子的 golden 与 perf 基线仍同源(与 H1 合并做最省)。
**保护**:纯扩表达力,现有扁平 positional/keyword 映射是新词表的子集 → 现算子零影响。属「按需扩」:当前 median 类单 api 用不到,遇到需 construct/list 参数或多步语义的算子再补;可标为 H1 的后续增量。
**律令#0**:词表 + program 校验是通用 schema,无算子分支。

### M9 —— 【D1·gap6】结构/整型类特殊值覆盖不足 parity:cannbot 要求从 torch 语义系统造 极值/0·1·-1/重复/越界索引/广播/规约轴/饱和;我们结构类只有 empty/scalar/bndlo/bndhi + 可选 tie

**改哪里**:扩 `gen_cases.py` 的 `value_profile` 受控词表(`_VALUE_PROFILE_KINDS`,478),补 `extrema/zero_one_neg/boundary_index/broadcast/saturation` 等 **op-中立生成器**(tie 已覆盖「重复」一档)。
**保护 / 裁量**:⚠ cannbot 实际数据集这些**多为 0 条**、由 agent 按算子自由裁量。故**更宜作为 spec 可声明的 profile**(声明才产)、而非 torch_parity 强制铺满——否则会给不需要的算子凭空造非法输入(越界索引对无索引算子无意义)。建议:作为 `spec.precision.value_profiles` 的可选声明项,缺省不产。现有算子不声明 → 零影响。
**律令#0**:profile 是声明式数据、通用生成器消费,无算子分支。

---

## 低优先级(low)

### L1 —— 【D3·gap2】达标比较用 `>=`,cannbot 用严格 `>`
**改哪里**:`perf_compare.py:382 met = raw >= tgt`。若要逐字一致改严格 `>`;边界(恰等阈值)语义差异极小。**建议**:硬门保留 `>=`(更宽松、对边界友好),仅 M3 的 `cases_above_threshold` 显示字段用严格 `>`(与 cannbot 报告口径一致),并在报告注记两处口径差。二者可并存不冲突。

### L2 —— 【D1·gap7】int 值域自定义:cannbot integers 用 spec 显式 min/max;我们写死 `[max(-100,min+1),min(100,max)]` 并锚点
**改哪里**:`gen_cases.py:_make_varied`(341-353)。torch_parity 下允许 spec 传显式 int min/max(对齐 `case_generator.py:55-57`),但**保留**排除 dtype-min 的溢出保护(343-352 的注释解释了为何不能取 dtype-min)。legacy 不动。

### L3 —— 【D2·gap4】机读报告缺 cannbot 的逐 dtype atol/rtol 回显 + failed/errored 分桶
**改哪里**:`validator.py` 报告组装处补一个 `by_dtype:[{dtype,passed,failed,errored,rtol,atol,pass_rate}]` 聚合块(rtol/atol 从 `precision_policy.threshold_for` 已有;failed=跑了但数值错、errored=kernel 崩/golden 读不了,errored 计入分母不计 executed;`overall_pass_rate=passed/total`)。纯增字段、不动裁决。**保护**:现三层 per-case 口径保留,新增块只读。

---

## ✅ 已忠实对齐 —— 别做无用功

| 维 | 已对齐的项(逐条已核) |
|---|---|
| **D2 精度数值比对(核心)** | 容差值 `_TA_DTYPE_TOLS`(`precision_policy.py:136-141`)与 cannbot `accuracy.py:47-54` 逐条对上(仅存储顺序 (rtol,atol) vs (atol,rtol) 相反、值一一对应);判据公式 `|o-g|≤atol+rtol·|g|`;inf 四象限 + equal_nan(`_allclose_close_mask`)与 np.allclose 语义等价;int/bool 逐位 exact;`mismatch==0`(容错率=0,= cannbot 全或无)。**这一维的数值口径无需改**。 |
| **D3 性能核心口径** | warmup=5/repeat=20(`perf_msprof.py:218-219`);设备 kernel 白名单六型逐字同;median×launches 计费 + setup 剔除 + 多 kernel 求和;行为五分类同名;CPU-fallback markers 逐字同;缺 MSTX fail-closed;`kernel_only` scope;ratio=baseline/npu。**核心采集口径无需改**。 |
| **D1 operator_class 门** | `_OPERATOR_CLASSES` 词表 + `_NONFINITE_CLASSES` + `_emits_nonfinite`(`gen_cases.py:439-468`)已忠实对齐 cannbot `design_contract.py:427/512`,注释已标 provenance。**无需改**。 |
| **D4 perf 基线映射机制** | `resolve_torch_baseline_plan`(`perf_msprof.py:1518-1565`)slot-name 驱动、op-中立、零算子分支——**机制本身是 faithful 的**;需改的是 golden 侧向它靠拢(H1),不是它本身。 |

---

## ⚠ 有意/有据偏离 —— 标注即可,**切勿为对齐而回退**(回退=制造回归或抹掉正确修正)

| 项 | 为何不回退 | 只需做 |
|---|---|---|
| **D2·gap3 容差 dtype 键**(我们按输出 dtype `derive_output_dtype`,cannbot 按输入 `case['dtype']`) | 我们**改对了 cannbot 的潜在瑕**——非同型算子(IsClose float→bool)cannbot 会键错。 | 在 `precision_policy.py:202` 注释标「有意偏离、按输出 dtype 更正确」,别当 bug 对齐。 |
| **D2·gap1 / D4·gap6 index_value_consistency**(gather 值一致;cannbot 只比 `golden_files[0]`、根本不判 index) | 我们**更强**——cannbot 对 median 的 indices 完全不比。保留。 | **修 provenance 注释**:`precision_policy.py:126-135` 及 index 判据处明标 `index_value_consistency` 系 OpRunway 原创、非 cannbot 口径,别让注释暗示 cannbot 出处。 |
| **D3·gap4 采集入口分裂**(baseline 走 torch_npu.profiler db、custom 走 msprof CLI+ctypes mstx) | 被真机 finding §9.7 A 逼出(msprof CLI 下 Python 侧打不出 MSTX);回退会重蹈静默失败。 | 在设计 doc/报告标「本项目 baseline 采集通路与 cannbot 分歧及实测依据」,保留双边采集配置一致性闸(我们有、cannbot 无)。 |
| **D3·gap5 `--ai-core=off`**(cannbot 只「不请求 --aic-metrics」) | 有真机实测(§9.7 C:默认 on 使数字虚高 2.0~3.75×)。 | `perf_msprof.py:229` 注释标「对 cannbot 命令的一处有据偏离(§9.7 C),非口径遗漏」。 |

---

## 落地批次建议(控制回归面)

1. **批 A(护栏先行,零行为变更)**:第 0 节 `case_profile` 开关 + 只读报告增强 M3/L3 + 注释修正类(D2gap1/D2gap3/D3gap4/D3gap5)。全部不改现有 caseset/golden 字节,可独立入库。
2. **批 B(torch_parity 造例对齐)**:H2/H3/M1/M2/M5/L2,全部 gate 在 `torch_parity`。落地后**先不翻 Median**,legacy 算子字节 pin 测试须全绿。
3. **批 C(同源 reference,风险最高)**:H1 + M6 + M8。**必须先加「新解释器 golden ≡ 旧 golden_fn」等价 pin**,再删 Median 手写 golden_fn。
4. **批 D(翻档 + 真机重验)**:把 `median.spec.json` 置 `case_profile:"torch_parity"`,重生成 golden、真机重跑,绿了才算 Median 完成对齐。M7(complex)/M9(结构 profile)/M4(0.6 兜底)/L1 视用户裁量插入。

关键文件锚点:`plugin/acc-common/gen_cases.py`(H2/H3/M1/M2/L2,`_plan`/`_shape_ladder`/`_dtype_shapes`/`_make_varied`/`_build_value_special`/`_special_entries`)、`plugin/acc-common/precision_policy.py`(M7/注释,`_TA_DTYPE_TOLS`/`compute_metrics`/`SUPPORTED_COMPUTE_DTYPES`)、`plugin/acc-common/perf_compare.py`(M3/M4/L1,`_resolve_target_ratio`/`_compare` summary)、`plugin/acc-common/aclnn_runtime/perf_msprof.py`(M5/M8,`select_perf_cases`/baseline wrapper)、新增 `plugin/acc-common/torch_reference.py`(H1/M6/M8)、`plugin/samples/golden/Median/golden.py`(H1 退化为薄壳)。