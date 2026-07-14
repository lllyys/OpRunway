# acc-spec 抽取规则：任务书 md → spec.json

> `acc-spec` skill 的 reference。把算子任务书（md）确定性地抽成中立的 `<op>.spec.json`。
> 规则由 23 份真实社区任务书语料归纳、并对 IsClose/Sign/Equal 三个手工 spec 验证过。
> 目标 schema 看**空模板** `plugin/acc-common/spec_schema_template.jsonc`（零真实数值，只看结构/字段/类型）——
> **产 spec 时只看空模板、不看任何真样例（`samples/specs/*.spec.json`）的数值**（读同名算子样例=先看答案，软污染）。
> 消费方是 `acc-common/gen_cases.py`（Task1 造用例）与 `validator.py`（Task2 裁决）。
> **抽取只做『任务书里有什么/缺什么』，不做验收判定；缺口显式落 `task_pr_gaps`，不静默臆造。**

## 0. 目标 schema（权威，字段集来自 validator.py + gen_cases.py 的消费口径）

> ⚠ 下面的值**仅示形（占位符/取值口径），不是任何真实算子的答案**——产 spec 时按字段口径从
> `task_doc.md` + `pr_facts.json` 抽，别把这里的示例值当成某个算子该填的数。零真值空模板见
> `plugin/acc-common/spec_schema_template.jsonc`。

```jsonc
{
  "op": "<PascalCase 算子名>",         // 去 aclnn 前缀
  "repo": "<顶层仓名>",                // ops-math / ops-nn / catlass …
  "hardware": ["<从任务书『适配硬件』抽>"],
  "reference": {"type":"<tbe|torch|numpy|gpu|cpu|builtin>","ref":"...","path":"opp/built-in/..."},
  "change": {"kind":"<rewrite_tbe|add_dtype|align_dtype|semantic|new_op|gpu_port|bugfix>","note":"...","dtypes_added":["<add_dtype 才有>"]},
  "params_source": "<task_doc_table | derived_from_reference>",
  "params": [
    {"name":"<in 参数>","io":"in","dtype":["<支持子集>"],"noncontiguous":true},
    {"name":"<attr 参数>","io":"attr","dtype":["double"],"default":"<默认值>"},
    {"name":"out","io":"out","dtype":["<输出 dtype>"]}
  ],
  "generalize": true,
  // Q7 dtype 覆盖门（gate 消费）：dtype_required=任务书**权威全集**（来源见 §1 dtype 行）；全集未知/信息库未接通→"needs_user"；
  //   legacy 未迁→省略。dtype_tested=实测子集（gen_cases 据**真实生成的 cases** 归并写入 caseset、门据此对账）。缺项由 task_pr_gaps 的 dtype_deferred 记录。
  "dtype_required": ["<权威全集>  或  \"needs_user\"  或  省略"],
  "dtype_tested": ["<实测子集，如 float32/float16>"],
  "verify_mode": "<exact|numerical|behavioral>",   // 三值，与 validator 一致
  // T5 精度口径升级（待散文门）：precision 显式声明 standard + tolerance_policy_id；
  //   保留 oracle + threshold(digest) 向后兼容；per-case 结构化 policy 由 gen_cases 按 golden dtype 派生。
  "precision": {"oracle":"<按任务书原文抽>","standard":"<据 oracle+verify_mode 映射>","tolerance_policy_id":"<spec 级摘要>",
                "threshold":"<exact→0；numerical→主 dtype 默认>","threshold_source":"..."},
  // T6/T8（待散文门）：perf.small_shape_exception 升为对象——机读阈值供 perf_compare 判小shape例外
  //   (<when_us_below 且 |差|≤abs_gap_us_within → 出仿真图挂人核)；legacy 纯字符串 perf_compare 正则兜底。
  "perf": {"baseline":"<tbe|gpu_external>","target_ratio":"<任务书性能目标换算：无劣化→1.0，≥95%→0.95>",
           "small_shape_exception":{"text":"<人读说明>","when_us_below":"<number>","abs_gap_us_within":"<number>"}},
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
| `reference.path` | TBE 内置三件套路径 | kernel=`opp/built-in/op_impl/ai_core/tbe/impl/dynamic/`、proto=`op_proto/inc/`、信息库=`config/ascend910b`（legacy 走 `ops_legacy/` + `*-legacy.json`）。**信息库 config（`config/<soc>` 下 ops-info）= dtype 全集的独立对照/兜底源（独立于被测 PR）**，任务书对 dtype 模糊时作全集来源；⚠ **当前 `fetch_source.py` 未抓此文件、读法随运行环境变（本机直读/ssh/ssh+docker）→ 该独立源尚未接通（TODO），模糊时回退问用户** |
| `change.kind` | 『任务概述』定性词 | rewrite_tbe / add_dtype / align_dtype / semantic / new_op / gpu_port / bugfix（复合取主 kind，余入 note）|
| `change.dtypes_added` | add_dtype 新增类型 | 如 `["int16"]`、`["bf16"]` |
| `params_source` | 有无完整参数表 | 有表→`task_doc_table`；只写『原算子所有类型』→`derived_from_reference` |
| `params[]` | 参数说明表 | 每参 `{name,io:in\|out\|attr,dtype:[],default?,noncontiguous?}`；Tensor→in/out，标量/属性→attr |
| `generalize` | 测试标准是否要泛化数据 | 默认 true；无张量IO(Sleep)/融合无泛化要求→false |
| `dtype_required`（Q7 dtype 覆盖门）| 任务书**权威 dtype 全集**（来源优先级同下 dtype 行：任务书显式表 > 原 TBE 信息库 > 问用户）| list of dtype。任务书只写『支持所有类型』且信息库未接通/全集未知 → **填 `"needs_user"`**（不谎报覆盖、也不臆造全集）；legacy 未迁 → **整字段省略**（门判『未声明→覆盖门未行使』、不阻塞）。**IsClose 已核**：op_def 正源={float32,float16,bfloat16,int32} |
| `dtype_tested`（Q7 dtype 覆盖门）| 当前 pipeline **实测子集**（通常 float32/float16）| list。**gen_cases 据实际生成的 cases 归并并写入 caseset**（门也用真实 cases 对账，口径一致、消除「并集过报」）；spec 侧此字段作声明/文档，**须与真实一致否则门抓「自报不符」→ BLOCKED** |
| dtype 覆盖缺口 → `task_pr_gaps` | required 有、tested 无的 dtype | 每缺口一条 `{"kind":"dtype_deferred","dtypes":["bfloat16","int32"],"reason":"…runner 未支持/Track C…"}`——**门据此放行**（有 deferred 记录=显式挂账、非静默收窄）；无 deferred 记录 → 门 BLOCKED |
| `verify_mode` | 见 §2 决策树 | exact / numerical / behavioral |
| `precision.oracle` | 精度校验工具/真值来源 | 受控词表 `ascendoptest / mere_mare / atk_double / torch / scipy / std_exact / none`，**按任务书原文抽**（多数社区任务=ascendoptest；SPMV=生态标准 MERE·MARE + ATK 双标杆=`atk_double`；Sleep=none）——**勿一律填 ascendoptest**。⚠ 旧文写的 `dual_benchmark` 已统一为 `atk_double`（与 `precision_policy.select_standard` 识别的词一致）；`mere_mare` 与 `atk_double` **都**映射到 standard `ecosystem_mere_mare`（ATK 双标杆 fallback 本轮 out-of-scope、未实现）|
| `precision.standard`（T5，待散文门）| 平台层标准，从 oracle+verify_mode 映射（见 §1.1 决策树）| 受控词表 `ascendoptest_default / ecosystem_mere_mare / exact / behavioral`。缺省不填时 `precision_policy.select_standard` 会按 §1.1 兜底 |
| `precision.tolerance_policy_id`（T5，待散文门）| **口径 id（分两层，别混）**：`spec.precision.tolerance_policy_id`=**spec 级摘要/向后兼容**（exact→`exact`、ascendoptest→`ascendoptest_default`、mere_mare/atk_double→`ecosystem_mere_mare`，**无 dtype 后缀**）；`caseset.expected.tolerance_policy_id`=**门控用、格式 `standard:dtype`**（如 `ascendoptest_default:float32`，per-case 由 `gen_cases` 按 golden dtype 生成，exact/behavioral 无 dtype 后缀）。validator/gate 的三处一致比的是**caseset 级**那份 | 
| `precision.acceptance_policy?`（T5，待散文门）| 任务书验收目标宽于平台底线时 | 可选 `{"standard":"...","error_rate":...}` 等覆盖；acceptance 过而 standard 不过 → PASSED_WITH_RISK 走人工 CP。**仅任务书明确放宽时才填**，勿臆造 |
| `precision.threshold` | 见 §3 | 数字：exact→0；behavioral→省略；numerical→AscendOpTest 主 dtype 默认值 |
| `precision.threshold_source` | 必填，记数字依据+推断链 | 自由文本 |
| `perf.baseline` | 『性能要求-基线』 | tbe / self_fp16 / small_op_concat / gpu / theoretical / none |
| `perf.target_ratio` | 『性能目标』换算 | ≥95%→0.95；**无劣化/持平→1.0**（『无劣化』=不得更慢=ratio≥1.0，literal 读法；勿误宽成 0.95）；10X→10.0；0.5倍A100→0.5；0.8倍H100→0.8；90%→0.9 |
| `perf.small_shape_exception` | 小 shape 例外条款 | T6(待散文门)：产**对象** `{text(人读原文), when_us_below, abs_gap_us_within, requires}`——机读阈值供 perf_compare 判例外(<阈 且 差≤容差→出仿真图挂人核)；legacy 纯字符串 perf_compare 正则兜底解析。抽取脚本是否也产 object 见 follow-up |
| `task_pr_gaps[]` | 由格式变体/缺口收敛 | 结构化缺口/矛盾/推断项 |

## 1.1 precision.standard 选择决策树（T5，与 `precision_policy.select_standard` 对齐）

先定 `verify_mode`（§2），再定 `standard`：

```
① verify_mode=behavioral（无数值输出，Sleep 类）           → standard = behavioral（精度维度 na）
② verify_mode=exact（输出 bool / 逐位对齐，Equal/IsClose） → standard = exact（threshold=0）
③ verify_mode=numerical：
   ├─ 任务书引用「生态《算子开源精度标准》」/ oracle∈{mere_mare, atk_double}
   │  / 落在 experimental 目录（cann/opbase experimental_standard）        → standard = ecosystem_mere_mare
   └─ 否则（oracle=ascendoptest / 缺省）                                  → standard = ascendoptest_default
```

⚠ `ecosystem_mere_mare` 是 **proposed / NOT_SETTLED**（来自 `canon/architecture/ecosystem-precision-standard.md`
status=proposed，一手出自 cann/opbase `experimental_standard.md`，**非事实、未 settle**）：其常量与判据都打 `NOT_SETTLED`，
**单标杆不过不自动 fail、记 `needs_review`**（ATK 双标杆 fallback 本轮不实现、out-of-scope）。抽到它时在 `task_pr_gaps`
显式标注「生态标准 proposed / 单标杆 needs_review」。缺省不确定就退回 `ascendoptest_default`（平台底线）。

## 2. verify_mode 决策树（⚠ 三值）

```
① 无数值张量输出 / 精度栏『不涉及』(Sleep 延时算子)      → behavioral（精度维度 na，靠功能 pass/fail）
② 输出 bool，或整型位运算/逐位对齐 CPU·torch(Equal,IsClose,RightShift) → exact，threshold=0
③ 其余：浮点输出 / 超越函数 / 距离·角度 / 含 cos·sin·exp·ln / 累加  → numerical
```
- **混合口径**（MinDim/MaxDim/Median：值 numerical + indices exact）→ 主口径取『值』= numerical，索引精确性由 golden 承担。
- **整型挂阈值 oracle 的歧义**（Sign∈{-1,0,1}、Gcd 整数、ForeachMul 整型乘）→ 任务口径挂 AscendOpTest 阈值仍归 numerical，`threshold_source` 注『整型实为精确』。
- 任务书**从不直写** exact/numerical → 一律推断，`threshold_source` 标 (推断)。

## 3. precision.threshold —— 向后兼容 digest（不再是唯一门控口径）

⚠ **T5 后语义变了**：`precision.threshold` 现在只是**向后兼容的标量 digest**（旧 gate/spec 的
`value<thr` 语义），**真正的门控走结构化 policy**——validator/gate 按 `standard` 分支用
`precision_policy.threshold_for()` 派生 canonical policy（ascendoptest 走坏点占比门、mere_mare 走 MERE/MARE、
exact 走 mismatch），再要求 spec/caseset/evidence 三处一致。所以 threshold 只需**与所选 standard 的 digest 对齐**
（`threshold_digest(policy)`：exact→0、ascendoptest→tolerance、mere_mare→Th、behavioral→0）。任务书 23/23 缺具体
数值时，spec 级仍落一个「主 dtype 代表值」作 digest + 标 (推断)，per-case 精确 policy 由 `gen_cases` 按 golden dtype 派生：

| standard | threshold（digest，按 standard 分支）| threshold_source 写法 |
|---|---|---|
| exact | `0` | 『bool/整型逐位、==无容差』 |
| behavioral | 省略 threshold（`{"oracle":"none"}` 即可）| 『无数值输出，精度维度 na』 |
| ascendoptest_default | 主 dtype 的 AscendOpTest 默认 tolerance（**必落数字**，含 fp16 取 1e-3）| 『AscendOpTest 默认阈值(fp16 1e-3) (推断/待工具核实)』 |
| ecosystem_mere_mare | 主 dtype 的 Th=2^-k（digest；判据是 MERE<Th 且 MARE<10Th）| 『生态标准 Th=2^-10(fp16) proposed/NOT_SETTLED；单标杆不过→needs_review』 |

> ⚠ **`precision` 对象任何 verify_mode 都要留**（至少 `{"oracle":"..."}`；behavioral 用 `"oracle":"none"`）——`validator.py`/`gen_cases.py` 无条件读 `spec["precision"]`，省略整个对象会 KeyError。只是 behavioral 的 `threshold` 可省。
> ⚠ **numerical 默认必落推断数字**（并标 gap），不留空——留空会走 `needs_review`（非 pass），仅在明确阻塞时才留空。

**主 dtype 默认阈值(推断，待 AscendOpTest 核实)**：fp32≈1e-4、fp16≈1e-3、bf16≈4e-3。主 dtype 选『最紧需求者』(含 fp16 取 1e-3)。
**per_dtype 例外**（SPMV：按 dtype 分档 + 双标杆比例阈值 最大相对≤2/平均≤1.2/均方根≤1.2）→ 单 threshold 不够，扩展 precision 为 per-dtype 映射并标 gap。

## 4. 兜底策略（任务书缺字段时）

优先级：**任务书原文 > PR 源码（`pr_facts.key_files`）> reference 反推(TBE 信息库/torch) > 惯例默认(标 (推断)) > 问用户**。

> ⚠ **例外·验收标准类字段**（dtype 全集 / 精度阈值·oracle / 性能目标 / 硬件目标 / golden 口径）**不走此通用序**——它们的来源**恒为任务书**（或任务书引用的、独立于 PR 的权威源），**PR 只作对照查 gap、绝不当权威**；dtype 全集专门次序见下表 dtype 行。此通用序**仅用于被测物类字段**（aclnn 入口 / example / target_dir——PR 是被测物、取自 PR 合法）。**任务书指定的标准/方法若不在当前支持范围 → fail-closed 问用户，不静默降级。**

| 缺什么 | 兜底 |
|---|---|
| **dtype 列表** | **⚠ 绝不来自被测 PR。来源优先级：任务书显式 dtype 表/规格 > 原 TBE 算子信息库（`opp/built-in/.../tbe/config/<soc>` ops-info，独立于被测 PR）> 问用户**。① 任务书有明确 dtype 表→用它（权威）。② 只写『支持所有类型』/缺→取原 TBE 信息库历史支持集作全集（独立源）。**⚠ 该独立源当前未接通**（`fetch_source` 不取该文件；且读法随运行环境变——skill 可能跑在服务器本地可直读 / 跑 Mac 需 ssh / 需 ssh 再进 docker，接通时须**探测环境、不写死 ssh**，列为 TODO）→ **接通前一律回退问用户、绝不回退读 PR**。③ **PR 的 `*_def.cpp` op_def 仅作对照**：读它只为与任务书全集比对（例 equal_def {FLOAT16,BF16,FLOAT,INT8,UINT8,INT32,UINT32}），PR 声明 < 任务书全集 → 记 `task_pr_gaps`（Fmod 式『PR 缩 dtype』缺口）；**绝不把 PR op_def 当全集权威**。④ 全新算子（`change.kind=new_op`，built-in 无条目）→ 直接问用户。⑤ **⚠ `params.dtype` 只填端到端 pipeline 支持子集 = float32/float16**（gen_cases 另可造 int32，但 new_example runner 跑不了 int32 → int32 属 Track C、**不进 `params.dtype`**、连全集一起记 gap）——**不支持的 dtype 不进 `params.dtype`（否则 gen_cases/runner 崩）**，任务全集与不支持项记 `task_pr_gaps`『任务需 {…}、pipeline 暂支持 {…}、余待扩』。⑥ add_dtype 的新 dtype：**支持才进 `params.dtype`**，否则只记 `change.dtypes_added` + gap（工具未支持前不宣称会真测）|
| threshold 数值 | 按 §3 主 dtype 惯例填 + 标 (推断)；或留空走 needs_review；per_dtype 复杂→问用户/查工具 |
| verify_mode | 按 §2 决策树推断 |
| **aclnn 入口/语义**（③ runner 锚定用）| **从 `pr_facts.key_files` 里算子自带 example(`test_aclnn_*.cpp`) 读真实调用的 aclnn 函数 + 输入 dtype**——runner 必须锚定它，别凭 header 猜（Equal 曾因猜错入口/dtype 翻车）|
| repo | reference URL 反推；数学类→ops-math、index/loss→ops-nn (推断) |
| hardware（验收标准类·不猜）| 从任务书『适配硬件』栏取；缺失/模糊 → **问用户**（硬件属验收标准，不按 arch 推断、不缺省 A2/A3）|
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
