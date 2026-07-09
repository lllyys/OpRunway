# acc-spec 抽取规则：任务书 md → spec.json

> `acc-spec` skill 的 reference。把算子任务书（md）确定性地抽成中立的 `<op>.spec.json`。
> 规则由 23 份真实社区任务书语料归纳、并对 IsClose/Sign/Equal 三个手工 spec 验证过。
> 目标 schema 见 `plugin/acc-common/specs/{equal,isclose,sign}.spec.json`；消费方是
> `acc-common/gen_cases.py`（Task1 造用例）与 `validator.py`（Task2 裁决）。
> **抽取只做『任务书里有什么/缺什么』，不做验收判定；缺口显式落 `task_pr_gaps`，不静默臆造。**

## 0. 目标 schema（权威，来自 3 个已建 spec + validator.py + gen_cases.py）

```jsonc
{
  "op": "IsClose",                    // PascalCase，去 aclnn 前缀
  "repo": "ops-math",                 // 顶层仓名
  "hardware": ["Atlas A2","Atlas A3"],
  "reference": {"type":"tbe","ref":"...","path":"opp/built-in/..."},
  "change": {"kind":"semantic","note":"...","dtypes_added":["int16"]},
  "params_source": "derived_from_reference",   // 或 "task_doc_table"
  "params": [
    {"name":"self","io":"in","dtype":["float32","float16"],"noncontiguous":true},
    {"name":"rtol","io":"attr","dtype":["double"],"default":1e-05},
    {"name":"out","io":"out","dtype":["bool"]}
  ],
  "generalize": true,
  "verify_mode": "exact",             // exact | numerical | behavioral（三值，与 validator 一致）
  "precision": {"oracle":"ascendoptest","threshold":0,"threshold_source":"..."},
  "perf": {"baseline":"tbe","target_ratio":0.95,
           "small_shape_exception":{"text":"<10us 差3us→仿真图","when_us_below":10,"abs_gap_us_within":3}},  // T6(待散文门)：对象
  "task_pr_gaps": []
}
```

**下游硬依赖**（抽错会崩/误判）：
- `gen_cases.py` 读 `params`(区分 in/attr、取 self 的 dtype、attr 的 default)、`verify_mode`、`precision.threshold`。
- `validator.py` 三处口径必须一致(spec/caseset/evidence)，且 `verify_mode` 只认 `exact|numerical|behavioral`；`numerical` 但 `threshold` 空 → 判 `uncertain`→`needs_review`（非 pass）。

## 1. 字段映射表

| 字段 | 定位（任务书里看哪儿） | 归一化/受控词表 |
|---|---|---|
| `op` | 标题/算子名称栏；去 aclnn 前缀 | PascalCase。标题名≠仓目录名≠原型名时以原型 REG_OP 名为准，歧义入 gap |
| `repo` | 『开源仓地址』或 PR 合入路径 `cann/<repo>` | ops-math / ops-nn / ops-transformer / catlass（experimental 子目录记 note 不入 repo） |
| `hardware` | 『适配硬件/支持产品』栏 | 'Atlas A2 训练系列产品'→'Atlas A2'；'Atlas A3 系列产品'→'Atlas A3'；'Ascend 950PR/950DT'、'Atlas 300V Pro' 原样。⚠『Atlas 800T A2』出现在『train loss 对比』语境=标杆对比机、非适配硬件，勿入 |
| `reference.type` | 『参考实现/功能对标』段动词 | tbe / torch / numpy / gpu / cpu / builtin（现有 aclnn 再开发）|
| `reference.ref` | 参考的具体定位 **+ 语义改造点** | 自由文本：TBE 文件路径 / gitcode URL / torch API / CUTLASS example 号。语义改造(如『二进制比较→逻辑值比较』)必记，供 casegen/golden |
| `reference.path` | TBE 内置三件套路径 | kernel=`opp/built-in/op_impl/ai_core/tbe/impl/dynamic/`、proto=`op_proto/inc/`、信息库=`config/ascend910b`（legacy 走 `ops_legacy/` + `*-legacy.json`）|
| `change.kind` | 『任务概述』定性词 | rewrite_tbe / add_dtype / align_dtype / semantic / new_op / gpu_port / bugfix（复合取主 kind，余入 note）|
| `change.dtypes_added` | add_dtype 新增类型 | 如 `["int16"]`、`["bf16"]` |
| `params_source` | 有无完整参数表 | 有表→`task_doc_table`；只写『原算子所有类型』→`derived_from_reference` |
| `params[]` | 参数说明表 | 每参 `{name,io:in\|out\|attr,dtype:[],default?,noncontiguous?}`；Tensor→in/out，标量/属性→attr |
| `generalize` | 测试标准是否要泛化数据 | 默认 true；无张量IO(Sleep)/融合无泛化要求→false |
| `verify_mode` | 见 §2 决策树 | exact / numerical / behavioral |
| `precision.oracle` | 精度校验工具/真值来源 | 受控词表 `ascendoptest / mere_mare / torch / scipy / std_exact / dual_benchmark / none`，**按任务书原文抽**（多数社区任务=ascendoptest；SPMV=生态标准 MERE·MARE + 双标杆；Sleep=none）——**勿一律填 ascendoptest** |
| `precision.threshold` | 见 §3 | 数字：exact→0；behavioral→省略；numerical→AscendOpTest 主 dtype 默认值 |
| `precision.threshold_source` | 必填，记数字依据+推断链 | 自由文本 |
| `perf.baseline` | 『性能要求-基线』 | tbe / self_fp16 / small_op_concat / gpu / theoretical / none |
| `perf.target_ratio` | 『性能目标』换算 | ≥95%→0.95；**无劣化/持平→1.0**（『无劣化』=不得更慢=ratio≥1.0，literal 读法；勿误宽成 0.95）；10X→10.0；0.5倍A100→0.5；0.8倍H100→0.8；90%→0.9 |
| `perf.small_shape_exception` | 小 shape 例外条款 | T6(待散文门)：产**对象** `{text(人读原文), when_us_below, abs_gap_us_within, requires}`——机读阈值供 perf_compare 判例外(<阈 且 差≤容差→出仿真图挂人核)；legacy 纯字符串 perf_compare 正则兜底解析。抽取脚本是否也产 object 见 follow-up |
| `task_pr_gaps[]` | 由格式变体/缺口收敛 | 结构化缺口/矛盾/推断项 |

## 2. verify_mode 决策树（⚠ 三值）

```
① 无数值张量输出 / 精度栏『不涉及』(Sleep 延时算子)      → behavioral（精度维度 na，靠功能 pass/fail）
② 输出 bool，或整型位运算/逐位对齐 CPU·torch(Equal,IsClose,RightShift) → exact，threshold=0
③ 其余：浮点输出 / 超越函数 / 距离·角度 / 含 cos·sin·exp·ln / 累加  → numerical
```
- **混合口径**（MinDim/MaxDim/Median：值 numerical + indices exact）→ 主口径取『值』= numerical，索引精确性由 golden 承担。
- **整型挂阈值 oracle 的歧义**（Sign∈{-1,0,1}、Gcd 整数、ForeachMul 整型乘）→ 任务口径挂 AscendOpTest 阈值仍归 numerical，`threshold_source` 注『整型实为精确』。
- 任务书**从不直写** exact/numerical → 一律推断，`threshold_source` 标 (推断)。

## 3. precision.threshold —— 最普遍的缺口（23/23 缺具体数值）

任务书恒为『满足 AscendOpTest 工具默认阈值』，无一给数字。落 spec 必须是数字（validator 做 `value<thr`，空→needs_review）：

| verify_mode | threshold | threshold_source 写法 |
|---|---|---|
| exact | `0` | 『bool/整型逐位、==无容差』 |
| behavioral | 省略 threshold | 『无数值输出，精度维度 na』 |
| numerical | 主 dtype 的 AscendOpTest 默认值（**必落数字**）| 『AscendOpTest 默认阈值(fp16 1e-3) (推断/待工具核实)』 |

> ⚠ **`precision` 对象任何 verify_mode 都要留**（至少 `{"oracle":"..."}`；behavioral 用 `"oracle":"none"`）——`validator.py`/`gen_cases.py` 无条件读 `spec["precision"]`，省略整个对象会 KeyError。只是 behavioral 的 `threshold` 可省。
> ⚠ **numerical 默认必落推断数字**（并标 gap），不留空——留空会走 `needs_review`（非 pass），仅在明确阻塞时才留空。

**主 dtype 默认阈值(推断，待 AscendOpTest 核实)**：fp32≈1e-4、fp16≈1e-3、bf16≈4e-3。主 dtype 选『最紧需求者』(含 fp16 取 1e-3)。
**per_dtype 例外**（SPMV：按 dtype 分档 + 双标杆比例阈值 最大相对≤2/平均≤1.2/均方根≤1.2）→ 单 threshold 不够，扩展 precision 为 per-dtype 映射并标 gap。

## 4. 兜底策略（任务书缺字段时）

优先级：**任务书原文 > PR 源码（`pr_facts.key_files`）> reference 反推(TBE 信息库/torch) > 惯例默认(标 (推断)) > 问用户**。

| 缺什么 | 兜底 |
|---|---|
| **dtype 列表** | **优先级：任务书显式 dtype 表 > PR op_def > 安全子集**。① 任务书有明确 dtype 表→用它。② 只写『支持所有类型』/缺→读 `pr_facts.key_files` 的 `*_def.cpp` `REG_OP … DataType({...})` 得**任务全 dtype 集**（例 equal_def {FLOAT16,BF16,FLOAT,INT8,UINT8,INT32,UINT32}）；与任务书显式集冲突→入 gap。③ **⚠ `params.dtype` 只填当前 pipeline 支持的子集**（gen_cases: float32/float16/int32；new_example runner: float32/float16）——**不支持的 dtype 不进 `params.dtype`（否则 gen_cases/runner 崩）**，任务全集与不支持项记 `task_pr_gaps`『任务需 {…}、pipeline 暂支持 {…}、余待扩』。④ add_dtype 的新 dtype：**支持才进 `params.dtype`**，否则只记 `change.dtypes_added` + gap（工具未支持前不宣称会真测）|
| threshold 数值 | 按 §3 主 dtype 惯例填 + 标 (推断)；或留空走 needs_review；per_dtype 复杂→问用户/查工具 |
| verify_mode | 按 §2 决策树推断 |
| **aclnn 入口/语义**（③ runner 锚定用）| **从 `pr_facts.key_files` 里算子自带 example(`test_aclnn_*.cpp`) 读真实调用的 aclnn 函数 + 输入 dtype**——runner 必须锚定它，别凭 header 猜（Equal 曾因猜错入口/dtype 翻车）|
| repo | reference URL 反推；数学类→ops-math、index/loss→ops-nn (推断) |
| hardware | 按 arch 需求推(950 算子→950PR) (推断)；缺省 A2/A3 |
| perf(性能栏『无』) | **省略整个 `perf` 字段**（run_workflow 无 perf 则不 gate 性能）；**勿写 `{baseline:"none"}`**——下游把非空 baseline 当有性能目标会误报 `BLOCKED(声明性能目标但无性能用例)` |
| shape/规格 | 泛化验收，交 casegen；参数表 '-' 不阻塞 |
| CANN 版本 | 『算子开源仓指定版本』→ 运行时按仓定，不入 spec |

## 5. 多算子一书 → 拆多个 spec

N 个算子 → N 个 `<op>.spec.json`。**共享字段抽一次复用**(hardware/repo/oracle/generalize)，**逐算子独立抽** op/reference/change/params/verify_mode/perf/threshold/gaps。三档：
1. **同族仅入参差异**(FmodScalar↔FmodTensor、MinDim↔MaxDim、Median↔MedianDim)：共享 dtype/precision/perf，只 params+op 名不同。
2. **异构双算子**(Cast↔EmbeddingDenseGrad)：reference/change/perf/合入仓全不同，**必须完全独立**，禁止合并。
3. **第二算子参数表留空**(MaxDim 列填 '-')：从兄弟算子继承 + 两个 spec 的 gaps 都记『继承自兄弟(推断)』。

## 6. task_pr_gaps 收敛

每条记『缺什么 / 影响字段 / 兜底』。常见类型：缺 dtype 列表、缺 threshold 数值、缺 verify_mode 明写、缺 per_dtype 声明、缺 shape 规格、缺 CANN 版本、缺性能绝对基线、**语义矛盾需澄清**(bincount 支持负数 vs 必须非负)、**模板残留**(MaxUnpool2d 仓名矛盾、Cast 合入路径矛盾、自验证报告 `xxx` 占位)。供 op-acceptance 报告步骤列『任务书↔PR 落差』，推断项标 (推断)。无缺口→`[]`。

## 7. 校验（写完 spec 自检）

- `verify_mode ∈ {exact,numerical,behavioral}`；`numerical` 则 `threshold` 有数或明确留空走 needs_review。
- `params` 至少一个 io=out；attr 有 default（gen_cases 读 default 造 golden）。
- `verify_mode=exact` ⇒ `threshold=0`；`precision.threshold_source` 非空。
- add_dtype ⇒ `change.dtypes_added` 非空；其中 **pipeline 支持的** dtype 已并入 `params.dtype`，**不支持的** 只在 `change.dtypes_added` + `task_pr_gaps`（不强求 ⊆ params.dtype，避免让 gen_cases/runner 崩）。
- `precision` 对象存在（任何 verify_mode 都不省略整个对象）；`perf` 无要求时整字段省略、不写 `{baseline:"none"}`。
- 多算子每份 spec 的 op 唯一、gaps 独立。

## 8. GPU 移植类特例（SPMV / DualMatmul 等）

无 TBE 基线：`reference.type=gpu/cpu`、`perf.baseline=gpu`（带 A100/H100 参考 us）、`target_ratio` 按倍数语义(0.5/0.8)、`hardware=950PR/DT`。精度 golden 来源记 reference（CPU 标杆），性能标杆入 perf。dtype 常以三列合法组合表给（非笛卡尔积），组合约束入 note/gap。
