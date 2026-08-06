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
  // §1.3（torch 对标 / 多输出 / aclnn 两段式被测物）用到的字段。
  // ⚠ `runner_form` **别当可选字段省略**：当前只有 `cpp_extension` 能产验收裁决，正式验收一律显式写它（§1.3.1）。
  //   省略确实有缺省兜底（键缺席 → `cpp_extension`），但**省略再也不表达 `cpp`**——要 `cpp` / `aclnn_py` 只能显式写。
  "scenario": "<可选：torch_ref_aclnn>",      // 场景标识（编排层路由标签）
  "runner_form": "cpp_extension",            // 受控词表 {cpp_extension（缺省，唯一验收准入）| aclnn_py | cpp}
  "call_variants": "<cpp_extension / aclnn_py 两种形态**均必填**：变体对象数组，见 §1.3.3>",
  // §1.6 仅 cpp_extension：张量 ACL 存储格式（受控两值）。**整字段省略 = torch_npu_rank_default = 现行为**
  "aclnn_tensor_format": "<可选：torch_npu_rank_default（缺省）| nd>",
  "allow_empty_tensor": "<仅 legacy 档可选 bool，缺省 true；torch_parity 决定②不产特殊场景、声明即拒>",
  // §1.4 可选：任务书点名「某 attr 所指的轴长度 = L」这类边界 → **定向生成**，别指望正交网格撞上
  "attr_axis_lengths": [{"attr":"<已声明的 attr 名>","lengths":[1]}],   // 不需要就整字段省略
  "reference": {"type":"<tbe|torch|numpy|gpu|cpu|builtin>","ref":"...","path":"opp/built-in/..."},
  "change": {"kind":"<rewrite_tbe|add_dtype|align_dtype|semantic|new_op|gpu_port|bugfix>","note":"...","dtypes_added":["<add_dtype 才有>"]},
  "params_source": "<task_doc_table | derived_from_reference>",
  // §1.5：算子类别（受控词表，决定「该不该给它喂 NaN·Inf」）。**每份新 spec 都要判**——
  //   判错会「该测的没测」或「不该判挂的判挂」（median PR6429 血教训）。省略 = legacy 兼容出口。
  "operator_class": "<floating_compute | integer_compute | structural>",
  "params": [
    // C3（2026-07-22 定）：in 参数可选 "rank"，限制 gen_cases 的 shape 阶梯只在合法维度内取值。
    //   取值 = int（如 2）或 int 列表（如 [3,4]）。**不写 = 不限制**（现行为）。别自造 `input_rank_constraint` 等别名。
    {"name":"<in 参数>","io":"in","dtype":["<支持子集>"],"noncontiguous":true,"rank":"<可选：int 或 int 列表>"},
    // C2（2026-07-22 定）：attr 值类型放开到 int | float | bool | str | list[int]（原先只允许标量）。
    //   `output_size` / `kernel_size` 这类**既是数组、又决定输出形状**的属性靠它。
    {"name":"<attr 参数>","io":"attr","dtype":["double"],"default":"<默认值：标量或 list[int]>"},
    // 多输出契约（§1.3.2）：out >1 个、或任一 out 声明了 out_role → 走多输出通路。
    //   单输出且不写 out_role = legacy，行为零变更。out dtype 只能是**单值**或哨兵 "<from_input>"。
    //   ⚠ index 输出的 dtype **按算子 header / op_def 实际声明填**（int32 或 int64），**不是**恒 int64——见 §1.3.2。
    {"name":"<value 输出>","io":"out","dtype":["<单值>  或  \"<from_input>\""],"out_role":"value"},
    {"name":"<index 输出>","io":"out","dtype":["<int32 | int64：取自 header/op_def 实际声明>"],"out_role":"index","index_of":"<value 输出名>","gather_from":"<in 参数名>"}
  ],
  // ⚠ C1：**输出形状不写进 spec**（不搞表达式语言）——非 elementwise 算子的输出形状由 per-op
  //   `<ops_root>/<op>/golden.py` 可选导出的 `out_shape(in_shapes, attrs)` 定（详见 acc-runner 的
  //   `references/runner-skeleton.md` §6）。**别在 spec 里发明 out_shape / output_shape / shape_formula 字段。**
  "generalize": true,
  // Q7 dtype 覆盖门（gate 消费）：dtype_required=任务书**权威全集**（来源见 §1 dtype 行）；全集未知/信息库未接通→"needs_user"；
  //   legacy 未迁→省略。dtype_tested=实测子集（gen_cases 据**真实生成的 cases** 归并写入 caseset、门据此对账）。
  //   缺项由 task_pr_gaps 的 dtype_deferred 记录 —— ⚠ 记下来 ≠ 免检，且该条目须声明 `capability_source`（§1.2a）。
  "dtype_required": ["<权威全集>  或  \"needs_user\"  或  省略"],
  "dtype_tested": ["<实测子集，如 float32/float16>"],
  "verify_mode": "<exact|numerical|behavioral>",   // 三值，与 validator 一致
  // T5 精度口径升级（待散文门）：precision 显式声明 standard + tolerance_policy_id；
  //   保留 oracle + threshold(digest) 向后兼容；per-case 结构化 policy 由 gen_cases 按 golden dtype 派生。
  "precision": {"oracle":"<按任务书原文抽>","standard":"<据 oracle+verify_mode 映射>","tolerance_policy_id":"<spec 级摘要>",
                // §1.6 用例来源（受控两值）。**整字段省略 = generated = 现行为**；写 taskdoc ⇒ 必须喂 --taskdoc-caseset
                "case_source":"<可选：generated（缺省）| taskdoc>",
                "taskdoc_caseset":"<可选，仅 taskdoc 档：{\"sha256\":\"<64 位小写 hex>\" 或 null}——逻辑身份声明>",
                "threshold":"<exact→0；numerical→主 dtype 默认>","threshold_source":"...",
                // §1.3.4：仅 standard=="torch_allclose" 用
                "tolerance_source":"<dtype_table | taskdoc | torch_default；省略=dtype_table>",
                "taskdoc_tol":"<仅 tolerance_source==taskdoc 必填：[rtol, atol]>",
                "value_profiles":"<可选：[\"nan\"] / [\"tie\"] / 两者；省略=不产此类用例>",
                "case_target":"<int 精度用例目标数，**必填、无缺省**；见下『case_target 怎么定』>",
                "case_target_source":"<可选但强烈建议：这个数是怎么来的（矩阵怎么乘 / 沿用了什么既有事实）>"},
  // T6/T8（待散文门）：perf.small_shape_exception 升为对象——机读阈值供 perf_compare 判小shape例外
  //   (<when_us_below 且 |差|≤abs_gap_us_within → 出仿真图挂人核)；legacy 纯字符串 perf_compare 正则兜底。
  // §4.1（AGENTS.md §5.10）：只测不比档。**写了 mode=measure_only 就不得再写 baseline/target_ratio
  //   /small_shape_exception/torch_baseline/aclnn_baseline**（五项必须缺席），且必须给全授权四件套。
  "perf": {"mode":"<可选：ratio_gated（缺省，= 比值裁决）| measure_only（只测不比，须授权）>",
           "measure_only_authorization":{"taskdoc_requirement":"<no_perf_requirement|gpu_comparison|change_class_no_perf_comparison>",
             "cite":"<task_doc.snapshot.md:<起>[-<止>]>","quote":"<任务书原文逐字>","taskdoc_snapshot_sha256":"<64 位小写 hex>"},
           "baseline":"<tbe|gpu_external|torch_npu|aclnn_builtin>","target_ratio":"<任务书性能目标换算：无劣化→1.0，≥95%→0.95>",
           // §1.3.5：仅 baseline=="torch_npu" 用；缺 torch_baseline → 采集端 fail-closed
           "torch_baseline":{"api":"torch.<...>","positional":["<slot name>"],"keyword":{"<slot name>":"<torch 形参名>"}},
           // §1.3.5：任务书点名可直接调用的 ACLNN / 小算子拼接基线时用
           "aclnn_baseline":{"library":"cann_builtin_libopapi","variants":[
             {"when":{"attr":"<attr>","is_null":true},"symbol":"<base name>","slots":["<slot name>"]}
           ]},
           "warmup":"<可选 int，缺省 5>","repeat":"<可选 int，缺省 20>",
           "small_shape_exception":{"text":"<人读说明>","when_us_below":"<number>","abs_gap_us_within":"<number>"}},
  "task_pr_gaps": []
}
```

**`case_target` 怎么定（精度用例目标数）**：`precision.case_target` **必填、没有缺省值**——不写这个键，
`gen_cases`（真跑与 `--dry-run` 两条路都是）当场 fail-fast。

⚠ **这里原先写的是「缺省 50 / `AskUserQuestion` 问用户（建议 50）」，2026-08-06 整条删掉，理由有两条、都要记住：**

1. **那个默认值实测就是个 fail-open**：extractor 照「建议 50」自己填了 50、全程 0 次被审视，
   792 个候选组合就这么留了 50 条。「这个算子该造多少条、依据是什么」被一个缺省值永久免答了。
2. **那条规矩物理上执行不了**：它要求用 `AskUserQuestion`，而 `agents/acc-spec-extractor.md` 的
   `tools:` 里**根本没有这个工具**。写了一条做不到的动作，等于把第 2 步默认跳过、直接落「建议值」。

**现在怎么定这个数**：**按覆盖矩阵算，不是拍一个数**。

- `case_profile == "torch_parity"`：`case_target` **必须精确等于常规完整笛卡尔矩阵大小**
  `dtype × rank × shape_profile × attribute_profile`（再减去有 `reason + evidence` 的 `excluded`；
  见 §1.3『Torch overload 覆盖与 `torch_parity_matrix`』）。特殊场景按 §1.3 的结构约束本来就不进
  笛卡尔、也不计入 `case_target`；决定②进一步明确本档当前三类 `operator_class` 均产 0 条，故
  `total_emitted == regular_emitted == case_target`。账本分别写 `regular_emitted / special_emitted /
  total_emitted`，不得把独立叠加的概念偷塞进矩阵乘法。
  不相等 `gen_cases` 直接报错，这条已经是硬校。
  ⚠ 这四条是**当前矩阵已有的自由轴**，不表示看到任何字段都要再加一维：特殊场景已决定不产、
  输出个数不是自由轴、值域 regime 暂不引入；新增轴是否必须交叉统一按 §1.3「轴集契约」判断。
- `precision.case_source == "taskdoc"`：`case_target` **照样必填**，且**必须精确等于**规范化后的
  任务书用例条数——这一档你不推算这个数，它被用例集锁死（`_taskdoc_plan` 逐字核对，对不上当场报
  「任务书用例集有 N 条、`precision.case_target=…`——两者必须相等」）。⚠ **别读成「这一档不用写」**：
  不写照样 fail-fast，`_require_case_target` 排在分档之前。⚠ 也**别为了跑通把它改成报错里那个 N**：
  规范化会按语义内容派生 `case_id`，原始条数与规范化条数不一致本身就是要查的事（样例见
  `plugin/samples/specs/gaussian_blur.spec.json` 的 `_case_target_note`）。
- 其它档：也按该算子的覆盖轴推算，并把算法写进 `precision.case_target_source`。
  ⚠ **统一的笛卡尔算法（含非 torch_parity 档）尚未落地**（roll19 方案步骤 11，需人评审轴集后实现）。
  在它落地之前，这个数**必须给得出依据**——沿用某份既有事实要写清沿用的是什么；
  给不出依据就停下问用户，**别随手填一个数**（填了就退回本条要删的那个毛病）。

**`gen_cases` 侧仍然成立的行为**（与上面怎么定这个数无关）：**须 ≥1**（0/负/非整 → fail-fast，
堵零用例空跑冒充验收）；`< S`（`forced_total` = 特殊场景 + 白名单的强制下限）时用 `max(case_target, S)`、
emit 略超并 note；`> pool_max` 时实际 emit = `pool_max`，数量门软化（PASS+note，不硬 BLOCK）。
`gen_cases.py <spec> --dry-run` 会打印 `forced_total`、`pool_max` 与区间行——那是**产出 spec 之后的自检**，
用来看「定的这个数落在哪」，**不再是**定这个数的手段（它自己也要求 spec 已有 `case_target`）。
铺法仍按 §1 覆盖-预算（dtype 分层 fp16/fp32/bf16 重点 + 其他 1-2、shape 阶梯、值域 uniform/normal、
attr 笛卡尔、§1.4 特殊场景、白名单必覆盖 + 1-wise 采样）铺到 `case_target`。

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
| `change.kind` | 『任务概述』定性词 | 受控**八值**：rewrite_tbe / add_dtype / **extend_shape** / align_dtype / semantic / new_op / gpu_port / bugfix（复合取主 kind，余入 note；唯一真源 `perf_mode.CHANGE_KINDS`，词表外 fail-closed）。⚠ `extend_shape`（扩展 shape·rank）**别再硬塞进 `semantic`**——`add_dtype` / `extend_shape` / `new_op` 三类是 §4.1 `change_class_no_perf_comparison` 授权的机器判据，塞错就派生不出来。⚠ 整字段省略 = **未声明**，不兜任何默认 |
| `change.dtypes_added` | add_dtype 新增类型 | 如 `["int16"]`、`["bf16"]` |
| `params_source` | 有无完整参数表 | 有表→`task_doc_table`；只写『原算子所有类型』→`derived_from_reference` |
| `operator_class`（§1.5）| 任务书的**算子功能/公式**段 + 参考 API 语义（这算子到底在算什么） | 受控词表 `floating_compute / structural / integer_compute`，**三选一、无第四种**。legacy 档据它决定产不产 `inf/-inf/nan` 特殊场景、`value_profiles` 能不能含 `"nan"`；`torch_parity` 决定②则对三类统一保持特殊场景 0 条，字段仍须照实填写并落账，**不得**据类别自行加例。**每份新 spec 都判**；整字段省略 = legacy 向后兼容出口（照产 NaN·Inf），别当缺省答案用。词表外取值 → fail-closed |
| `params[]` | 参数说明表 | 每参 `{name,io:in\|out\|attr,dtype:[],default?,noncontiguous?,rank?}`；Tensor→in/out，标量/属性→attr。**attr 值类型（C2）**：`int \| float \| bool \| str \| list[int]`——数组属性（`output_size`/`kernel_size`/`stride`/`padding`/`ksize`）照原样写成 `[a,b]`，别拍平成字符串、别只取首元素。**in 的 `rank`（C3）**见下一行 |
| `params[].rank`（C3，可选）| 任务书参数表『维度(shape)』栏 / 算子 README / `*_infershape.cpp` 的 rank 校验 | int（`2`）或 int 列表（`[3,4]`）。**只声明确凿的 rank**，任务书没写死就**别填**（不写=不限制=现行为，不臆造）。例（依据 `dev-doc/oprunway-op-shape-taxonomy.md`，相关行标 `verified`）：Pdist=2、im2col=[3,4]、UpsampleNearestExact1d=3、UpsampleNearest3d=5、bincount=1。⚠ legacy 档中它只收窄「造哪些 shape」，收不掉特殊场景：`gen_cases._special_entries` 的空 Tensor / 标量 / 边界 / inf-nan 是强制项，rank 约束下走 `_fit_rank` 保 numel 调维。`torch_parity` 决定②则一条特殊场景都不产，不能拿 rank 声明反推已经覆盖。任务书明写「不支持空 Tensor」时照记 `task_pr_gaps`；在 `torch_parity` 下不得声明 `allow_empty_tensor` 冒充消费方 |
| `generalize` | 测试标准是否要泛化数据 | 默认 true；无张量IO(Sleep)/融合无泛化要求→false |
| `dtype_required`（Q7 dtype 覆盖门）| 任务书**权威 dtype 全集**（来源优先级同下 dtype 行：任务书显式表 > 原 TBE 信息库 > 问用户）| list of dtype。任务书只写『支持所有类型』且信息库未接通/全集未知 → **填 `"needs_user"`**（不谎报覆盖、也不臆造全集）；legacy 未迁 → **整字段省略**（门判『未声明→覆盖门未行使』、不阻塞）。**IsClose 已核**：op_def 正源={float32,float16,bfloat16,int32} |
| `dtype_tested`（Q7 dtype 覆盖门）| 当前 pipeline **实测子集**（通常 float32/float16）| list。**gen_cases 据实际生成的 cases 归并并写入 caseset**（门也用真实 cases 对账，口径一致、消除「并集过报」）；spec 侧此字段作声明/文档，**须与真实一致否则门抓「自报不符」→ BLOCKED** |
| dtype 覆盖缺口 → `task_pr_gaps` | required 有、tested 无的 dtype | **两类挂账，按成因选**（§1.2 有对照表）：① **我们测不了** → `{"kind":"dtype_deferred","dtypes":["int32"],"capability_source":"runner","runner_form":"cpp","reason":"…runner 无 int 分支/Track C…"}`（**必须声明能力来源**，四道硬校见 §1.2a）；② **算子 op_def 压根不支持**（C4）→ `{"kind":"dtype_unsupported_by_op_def","dtypes":[…],"task_doc_ref":…,"op_def_ref":…,"op_def_dtypes":[…]}`（四道硬校见 §1.2，缺一即 `overall=fail`）。**挂账合规 → 门不判「静默收窄」**；⚠ 那**只**表示缺口被如实记下来了，**不表示该 dtype 免检**：`dtype_deferred` 的终态不会是干净 `pass`（§1.2a），C4/target_hw 落 `passed_with_gaps`。挂账不合规 = 不算挂账；两类记录都无 → 门 BLOCKED |
| `verify_mode` | 见 §2 决策树 | exact / numerical / behavioral |
| `precision.oracle` | 精度校验工具/真值来源 | 受控词表 `ascendoptest / mere_mare / atk_double / torch / scipy / std_exact / none`，**按任务书原文抽**（多数社区任务=ascendoptest；SPMV=生态标准 MERE·MARE + ATK 双标杆=`atk_double`；Sleep=none）——**勿一律填 ascendoptest**。⚠ 旧文写的 `dual_benchmark` 已统一为 `atk_double`（与 `precision_policy.select_standard` 识别的词一致）；`mere_mare` 与 `atk_double` **都**映射到 standard `ecosystem_mere_mare`（ATK 双标杆 fallback 本轮 out-of-scope、未实现）|
| `precision.standard`（T5，待散文门）| **先读任务书显式精度工具/标准；仅缺失时**才从 oracle+verify_mode 兜底（见 §1.1）| 受控词表 `ascendoptest_default / ecosystem_mere_mare / exact / behavioral / torch_allclose`。`oracle` 是真值来源，不得覆盖任务书点名的验收尺；缺省不填时 `precision_policy.select_standard` 才按 §1.1 兜底 |
| `scenario`（§1.3）| 任务书『参考实现/功能对标』段是否把 **torch 指定为真值口径** × PR 是否**标准 aclnn 两段式**工程 | 受控值 `torch_ref_aclnn`；不属该场景 → **整字段省略**，别编新值 |
| `runner_form`（§1.3）| **执行形态 = 用哪座调用桥去调被测物**（见 §1.3.1）。⚠ 它**不是**「被测物工程结构」的同义词——被测物是不是 aclnn 两段式，判的是**域内/域外**，不是这个字段 | 受控词表 `cpp_extension` / `aclnn_py` / `cpp`（派生 mode 见 `AGENTS.md` §4）。**正式验收一律 `cpp_extension`**：它是当前**唯一**能产验收裁决的形态（准入白名单 `run_workflow._ACCEPTANCE_RUNNER_FORMS`），另两个已于 2026-08-06 **停止准入**：不但产不出 `acceptance.json` / `verdict.json`，**连真机入口都没有了**（逃生阀已删），抽成它们等于抽出一份跑不了的 spec。⚠ **缺省 = `cpp_extension`**（键缺席即此，唯一真源 `repo_adapter.DEFAULT_RUNNER_FORM`，`run_workflow` / `gen_cases` / `cpp_extension_codegen` / `cpp_extension_adapter.prepare` 全部同源）——所以**省略再也不表达 `cpp`**，`cpp` / `aclnn_py` 只能显式声明。⚠ **缺省兜得住 ≠ 可以省着不写**：正式验收的 spec 一律显式写 `"runner_form": "cpp_extension"`，执行身份要在 spec 里一眼可读、可审。⚠ 只有**键缺席**吃缺省：显式写 `null` / `""` 是一份写坏的 spec，照旧在受控词表处 fail-closed。`cpp_extension` / `aclnn_py` ⇒ **都必须**同时给 `call_variants`，否则 gen_cases fail-closed。⚠ runner form **只决定执行形态，不能反推任务书指定的性能标杆**——baseline 仍逐字按任务书核 |
| `aclnn_tensor_format`（§1.6，可选，**仅 `runner_form=cpp_extension`**）| **ABI 事实源**（接口 header / docs / example）对张量存储格式的要求；任务书与 op_def 只作交叉 | 受控两值。整字段省略 = `torch_npu_rank_default` = op-plugin 按 rank 猜格式（3→NCL、4→NCHW、5→NCDHW、其余→ND），产物逐字节不变、manifest 记 `default_unverified`。接口按 `GetStorageFormat()==FORMAT_ND` 校格式（症状：rank-3 张量被 L2 拒成 `ACLNN_ERR_PARAM_INVALID` 161002）时才写 `nd`。⚠ `nd` 当前只在手写 `extended` stage2 下实现，落在走官方宏的 `standard` 形态上 → fail-closed。**没核过就别写，沿用缺省并挂账** |
| `call_variants`（§1.3.3）| **递归发现的接口头**的函数签名（`<op_subdir>` 下有界递归找到的 `aclnn_*.h`，剔 `*_impl.h`；**层级不预设**）+ 任务书的 attr 语义 | 变体对象数组；`when`/`symbol`/`active_outputs` 必填，`active_attrs`/`attrs` 选填。按 **attr 取值**分派，**绝不按算子名**。`runner_form ∈ {cpp_extension, aclnn_py}` **一律必填**（两种形态共用同一份逐 case 调用契约 `aclnn_call`）|
| `params[].out_role` / `index_of` / `gather_from`（§1.3.2）| aclnn 签名的输出形参 + 任务书对各输出的语义描述 | `out_role ∈ {value, index}`（多输出时**每个** out 必填）；`index_of` 指本 spec 某 `value` 输出名；`gather_from` 指本 spec 某 `in` 参数名。二者仅 `index` 有、且**必填** |
| `allow_empty_tensor`（§1.3.6）| 任务书『不支持空 Tensor』类明写约束 | **仅 legacy 档可写**：真 bool，缺省 `true`，`"false"`/`0` fail-closed。`torch_parity` 决定②不产特殊场景，本键无消费方，声明即 fail-closed；算子事实照记 `task_pr_gaps` 或 `_` 前缀注释，不能用一个无效开关冒充已覆盖 |
| `attr_axis_lengths`（§1.4，可选）| 任务书**点名**的轴长度边界（典型句式「归约维/dim 所指轴上维度为 1 时…」）| `[{"attr":"<已声明 attr 名>","lengths":[<正整数>…]}]`。**声明了却一条都产不出 → fail-closed**（假覆盖）。不需要就整字段省略 |
| `precision.tolerance_policy_id`（T5，待散文门）| **口径 id（分两层，别混）**：`spec.precision.tolerance_policy_id`=**spec 级摘要/向后兼容**（exact→`exact`、ascendoptest→`ascendoptest_default`、mere_mare/atk_double→`ecosystem_mere_mare`，**无 dtype 后缀**）；`caseset.expected.tolerance_policy_id`=**门控用、格式 `standard:dtype`**（如 `ascendoptest_default:float32`，per-case 由 `gen_cases` 按 golden dtype 生成，exact/behavioral 无 dtype 后缀）。validator/gate 的三处一致比的是**caseset 级**那份 | 
| `precision.acceptance_policy?`（T5，待散文门）| 任务书验收目标宽于平台底线时 | 可选 `{"standard":"...","error_rate":...}` 等覆盖；acceptance 过而 standard 不过 → PASSED_WITH_RISK 走人工 CP。**仅任务书明确放宽时才填**，勿臆造 |
| `precision.case_source`（§1.6，可选）| 任务书**给没给成套自测用例**（典型：`精度自测用例参考[自测用例目录](./self_test_case/<op>/)` 这类链接，含相对链接）| 受控两值。整字段省略 = `generated` = 本引擎按覆盖-预算规则造例（现行为、逐字节不变）。任务书**给了**用例 → 写 `taskdoc`，并由编排层把 `taskdoc_caseset.py` 规范化后的 caseset 显式喂给 `gen_cases --taskdoc-caseset`。⚠ 声明 `taskdoc` 却拿不到那份文件 → **fail-closed，绝不回退自生成**；词表外取值同样 fail-closed（这个字段猜错的代价特别贵——判成 `generated` 就等于把任务书点名的测试点整套换掉）|
| `precision.taskdoc_caseset`（可选，仅 taskdoc 档）| — | `{"sha256": "<64 位小写 hex>"}` 或显式 `null`（表示本轮未绑定）。spec 侧对那份 caseset 的**逻辑身份声明**，供跨轮对账 |
| `precision.threshold` | 见 §3 | 数字：exact→0；behavioral→省略；numerical→AscendOpTest 主 dtype 默认值 |
| `precision.threshold_source` | 必填，记数字依据+推断链 | 自由文本 |
| `perf.mode`（§4.1，可选）| 本轮性能维**要不要做比值裁决**（AGENTS.md §5.10 三种情形）| 受控两值。整字段省略 = `ratio_gated` = 现行为（要 baseline + target_ratio）。属 §5.10 三种情形之一 → 写 `measure_only`，并**同时**给 `measure_only_authorization`；此时 `baseline` / `target_ratio` / `small_shape_exception` / `torch_baseline` / `aclnn_baseline` **五项必须缺席**，`perf` 块字段走白名单（`mode` / `measure_only_authorization` / `case_source` / `case_selection` / `shape_classification` / `warmup` / `repeat` / `side_timeout_s`），词表外一律 fail-closed |
| `perf.measure_only_authorization`（§4.1，`mode=measure_only` 时**必填**）| 任务书原文（或本轮改动类别）+ CP-A 任务书快照 | `{taskdoc_requirement ∈ {no_perf_requirement, gpu_comparison, change_class_no_perf_comparison}, cite, quote, taskdoc_snapshot_sha256}` **四项缺一即 fail-closed**（与 `golden.authorization` 同一套锚）。⚠ **宽档必须由可核事实授权，不由 spec 自报或省略取得**；走 `change_class_no_perf_comparison` 还要与 `spec.change.kind ∈ {add_dtype, extend_shape, new_op}` 机器对账 |
| `perf.baseline` | 『性能要求-基线』（**仅 `ratio_gated` 档**）| tbe / self_fp16 / small_op_concat / gpu / theoretical / none / **torch_npu** / **aclnn_builtin**。框架级 Torch 或已确认“小算子拼接等价于 Torch 接口”用 `torch_npu`；实际要求直接 ACLNN 才用 `aclnn_builtin` |
| `perf.torch_baseline`（§1.3.5）| aclnn 签名的形参名（= slot name）↔ torch API 形参名 | `{api: "torch.*", positional: [slot…], keyword: {slot: torch形参}}`。`positional` 缺任一 slot → fail-closed；`keyword` 里某 slot 在该 case 不存在 → 该 kwarg 自然缺席（变体自动跟随）|
| `perf.aclnn_baseline`（§1.3.5）| 任务书点名的 ACLNN API + case 调用形态 | `{library:"cann_builtin_libopapi", variants:[{when,symbol,slots}]}`；`symbol` 不带 `aclnn` 前缀，`slots` 从逐 case `aclnn_call.slots` 选择/重排；每个 case 须恰好匹配一条 |
| `perf.case_source` / `perf.case_selection` / `perf.shape_classification` | 性能 case 来源、选取与目标硬件大小分界；`perf` 存在时来源与分类必填 | `case_source:"precision_cases"`；实际采集只消费本轮精度 verdict 的 pass case，fail/needs_review 不进入性能比较。需要补接口/属性 × 大小 shape 覆盖时，用 `case_selection.include_precision_tags` 选入同一 caseset 中已有的精度 case，不另造输入。精度 pass 不等于 ratio 必然达标或 baseline 证据必然存在。分类为 `{metric:"sum_input_bytes",small_max_bytes,hardware}`，边界计入小 shape；A3 为 262144 bytes。只打分组标签，不免测、不改裁决 |
| `perf.target_ratio` | 『性能目标』换算 | ≥95%→0.95；**无劣化/持平→1.0**（『无劣化』=不得更慢=ratio≥1.0，literal 读法；勿误宽成 0.95）；10X→10.0；0.5倍A100→0.5；0.8倍H100→0.8；90%→0.9 |
| `perf.small_shape_exception` | 小 shape 例外条款 | T6(待散文门)：产**对象** `{text(人读原文), when_us_below, abs_gap_us_within, requires}`——机读阈值供 perf_compare 判例外(<阈 且 差≤容差→出仿真图挂人核)；legacy 纯字符串 perf_compare 正则兜底解析。抽取脚本是否也产 object 见 follow-up |
| `task_pr_gaps[]` | 由格式变体/缺口收敛 | 结构化缺口/矛盾/推断项 |

## 1.1 precision.standard 选择决策树（T5，与 `precision_policy.select_standard` 对齐）

先定 `verify_mode`（§2），再定 `standard`。**第一优先级是任务书显式精度条款**：

- 点名 AscendOpTest 默认阈值 → `ascendoptest_default`；
- 点名生态 MERE/MARE/ATK → `ecosystem_mere_mare`；
- 点名 torch allclose/rtol+atol → `torch_allclose`；
- 明确逐位/零容差 → `exact`。

只有任务书没有指定精度工具/标准时，才用以下 oracle 兜底：

```
① verify_mode=behavioral（无数值输出，Sleep 类）           → standard = behavioral（精度维度 na）
② verify_mode=exact（输出 bool / 逐位对齐，Equal/IsClose） → standard = exact（threshold=0）
③ verify_mode=numerical：
   ├─ 任务书只把 **torch 指定为真值口径**且未另写精度标准（oracle=torch）    → standard = torch_allclose（§1.3.4）
   ├─ 任务书引用「生态《算子开源精度标准》」/ oracle∈{mere_mare, atk_double}
   │  / 落在 experimental 目录（cann/opbase experimental_standard）        → standard = ecosystem_mere_mare
   └─ 否则（oracle=ascendoptest / 缺省）                                  → standard = ascendoptest_default
```

⚠ `select_standard` 对 numerical 的 oracle 走**显式白名单**：只有 `{ascendoptest, none, 缺省}` 才映射
`ascendoptest_default`，`torch`→`torch_allclose`，`{mere_mare, atk_double}`→`ecosystem_mere_mare`；
**其余 oracle（如 `scipy` / `std_exact`）一律 raise**、拒绝静默降级 → 抽到它们必须显式写 `precision.standard`
或停下问用户。

⚠ `ecosystem_mere_mare` 是 **proposed / NOT_SETTLED**（来自 `canon/architecture/ecosystem-precision-standard.md`
status=proposed，一手出自 cann/opbase `experimental_standard.md`，**非事实、未 settle**）：其常量与判据都打 `NOT_SETTLED`，
**单标杆不过不自动 fail、记 `needs_review`**（ATK 双标杆 fallback 本轮不实现、out-of-scope）。抽到它时在 `task_pr_gaps`
显式标注「生态标准 proposed / 单标杆 needs_review」。缺省不确定就退回 `ascendoptest_default`（平台底线）。

## 1.2 dtype 冲突以**任务书**为准（C4 · 用户 2026-07-22 拍板）

**规则**：任务书声明的 dtype 全集 = **需求**（写进 `dtype_required`）；算子 `op_def` 支持不了的差额
**入 `task_pr_gaps`**、裁决落 `passed_with_gaps`。**「没实现」是发现、不是借口**
（承 canon `task-spec-authoritative-over-pr`）。

⚠ 这是既有红线的延伸：任务书明确枚举时不得由 PR 改写；任务书若以“所有进入 AICore 的类型”
定义实现域集合，则同一 PR head 的 op_def 是集合成员的本轮枚举事实，不属于用 PR 覆盖任务书语义。
**保持不变**（§1 dtype 行 + §4 例外段）。C4 只是规定了「任务书要、算子没做」这个差额**怎么落账**——
仍然不允许拿 op_def 当全集权威、不允许因为 PR 没做就把需求缩掉。

**怎么写这条 gap**（结构化条目，字段名与硬校**实读自 `validator._check_dtype_gap`**，别自造别名）：

```jsonc
{"kind": "dtype_unsupported_by_op_def",
 "dtypes": ["<任务书要、op_def 没有的 dtype，非空>"],
 "task_doc_ref": "<任务书原文定位：章节/行/原句摘要>",
 "op_def_ref":   "<op_def 出处：文件路径 + 行号/字段>",
 "op_def_dtypes": ["<op_def 实际声明的支持集，供交叉核验>"]}
```

⚠ **它绝不是「宣称有 gap 就免检」的后门**——`validator` 四道硬校缺一即**拒**（拒 = contract problem → `overall=fail`，
不是静默忽略这条 gap）：

1. **有据**：`task_doc_ref` + `op_def_ref` + `op_def_dtypes` 三者必填、类型正确。**没有出处的 gap 一律不认。**
2. **自洽**：声称「op_def 不支持」的 dtype，不得同时出现在自报的 `op_def_dtypes` 里。
3. **不得覆盖真失败**：该 dtype 若**有真实用例在跑**（实测集含之）→ 拒。
   **这就是「没实现」与「实现了但跑挂了」的判别式**：前者压根造不出用例，后者一定有用例 + 证据，必须走精度/功能裁决。
4. **在需求内**：spec 声明了 `dtype_required` 时，gap 的 dtype 须确在任务书要求内（给任务书没要求的 dtype 挂账 = 无据）。

**与 `dtype_deferred` 别混**（三类挂账，`validate_acceptance_state` 的 dtype 覆盖门都认，且**三类都逐条硬校、不合规即不算挂账**）：

| kind | 什么情形 | 谁的问题 | 挂上以后 |
|---|---|---|---|
| `dtype_deferred` | 任务书要、算子也做了，**是我们这条 pipeline 暂时测不了**（某张能力表没有该 dtype）| **我们的**能力缺口 | **不是免检**：覆盖门只放行「不算静默收窄」这一点；终态**不会**是干净 `pass`，且须过能力来源硬校（见 §1.2a）|
| `dtype_unsupported_by_op_def` | 任务书要、**算子 `op_def` 压根没声明支持** | **被测物的**缺口 = 验收**发现** | 裁决落 `passed_with_gaps`（不是 pass）|
| `dtype_unsupported_on_target_hw` | 任务书要、**`op_def` 声明了**，但**目标硬件那一支的 aclnn 实现没有**（分支 `DTYPE_SUPPORT_LIST` 不含）| **被测物的**缺口 = 验收**发现** | 裁决落 `passed_with_gaps`（不是 pass）|

⚠ **「挂账」= 「这个缺口被如实记下来了」，不等于「这个 dtype 免于验收」。** 三类都一样。
`dtype_deferred` 尤其容易被读成免检牌，实测也确实被这么用过（aclnnRoll 试跑：任务书要的两个 dtype
一条用例没跑、终态却是干净 pass）。现在两道门各拦一半，见 §1.2a。

### §1.2a `dtype_deferred` 的两道门（2026-08-06 收严，写 spec 前必读）

**① 终态映射**（`gate_task2`）：任务书要求的 dtype 挂了 `dtype_deferred` 且**一条用例都没跑**时，
终态**不得**是最低档的干净 `pass`。合法终态：`needs_review`（首选，交人核）/ `fail` / `passed_with_risk`；
`passed_with_gaps` 只在**另有**结构合法的被测物侧 finding gap 撑着时才合法——**deferred 撑不起它**。

**② 能力来源硬校**（`gate_task1` 的覆盖门，`validate_acceptance_state._check_deferred_gap`）：
挂 deferred 必须**指名是哪张能力表不支持**，门拿**活表**逐条交叉核验。四道校缺一即拒；
**拒 = 这条挂账不算数** → 该 dtype 仍按「静默收窄」判 → 门 BLOCKED。

```jsonc
{"kind": "dtype_deferred",
 "dtypes": ["<非空 dtype 字符串列表>"],
 "capability_source": "generation | runner | compute",   // 必填：哪张能力表不支持
 "runner_form": "cpp | aclnn_py | cpp_extension",        // 仅 capability_source=runner 时必填；其余来源**不得**写
 "reason": "<人读说明>"}
```

| `capability_source` | 对应能力表 | 含义 |
|---|---|---|
| `generation` | `gen_cases._NATIVE`（+ `bfloat16`）| 造不出输入 / 算不出 golden / 落盘读不回 |
| `runner` | `repo_adapter.SUPPORTED_NP_BY_FORM[<runner_form>]` | 该 runner form 的真机侧收发不了（含 Track-C：`DEFERRED_NP_BY_FORM` 里的 dtype 本就不在支持表里，合法）|
| `compute` | `precision_policy.SUPPORTED_COMPUTE_DTYPES` | 误差 metrics 复算不出来（如 bf16、`complex128`）|

⚠ **`complex64` / `uint32` 别再照旧例挂 deferred**（2026-08-06 起两者四层齐备：生成 / 收发（仅
`cpp_extension`）/ 阈值 / 复算）。给它们挂 `dtype_deferred` 会撞上第 4 条硬校「与表不矛盾」——
门读活表发现其实支持 → **拒该 gap**，该 dtype 随即按「静默收窄」判 BLOCKED。仍然不支持的复数是
`complex128`（缺真机实证）。`cpp` / `aclnn_py` 两条通路本轮**没有**跟着放开，走那两条时
`capability_source=runner` 依然成立。

四道硬校：

1. **读得出**：`dtypes` 须为非空的 dtype 字符串列表。写成 `"dtypes": "complex64"`（漏内层方括号）
   或把整个 `task_pr_gaps` 写成对象（漏外层方括号）→ 门读不出被 defer 掉的是什么 → 拒。
2. **有来源**：`capability_source` 必填且属受控词表。**不指名 = 门没有对照物 = 「宣称有缺口就免检」。**
3. **来源可定位**：`runner` 来源须带 `runner_form`（真机表逐形态各一份）；其余来源带了 `runner_form` 即拒。
   本轮实跑的 evidence 记了 `runner_form` 时还要**逐字相符**——不许挑一支更弱的 runner 表来给缺口撑腰。
4. 🔴 **与表不矛盾**：自报不支持的 dtype 若在那张表的**当前**支持集里 → 伪造 deferred，拒。
   （门读的是**活表**，不是文档里的快照：别处给表补了 dtype，门当天就跟着变严。）

⚠ **不扣 `dtype_required`、也不扣实测集**：删掉 / 清空 / 改写 caseset 的 `dtype_required` 绕不过这两道门；
反过来，Track-C 那种「用例造得出、真机跑不了」的形态下 caseset 里**有**该 dtype 的真实用例，
挂账**仍然成立**，不会被误伤。

### §1.2b 第三类 `dtype_unsupported_on_target_hw`（已裁定补入，2026-07-23）

⚠ **已裁定：补第三类 kind `dtype_unsupported_on_target_hw`**（2026-07-23 由 im2col 的 `bool` 撞出、用户拍板）。
此前「按 `dtype_deferred` 落」是**接线前的过渡**、现已过时——`dtype_deferred` 语义是「**我们的**能力缺口」，
会把「算子在目标硬件上没实现」这个**被测物侧验收发现**说反，**别再用它罩这种情形**。

**语义**：任务书要 dtype X；算子 `op_def` **声明支持** X；但**目标硬件那一支的 aclnn 实现**（如 im2col 的
`aclnn_im2col.cpp:222-225` 的 `IsRegBase` 分流下，非 regbase = A2/A3 那一支的 `DTYPE_SUPPORT_LIST`）**不含** X。
它是**被测物侧的验收发现**（不是「我们 pipeline 测不了」）→ 覆盖门放行、裁决落 `passed_with_gaps`，与
`dtype_unsupported_by_op_def`（C4）**同档、同桶**。

**怎么写这条 gap**（结构化条目，字段名与硬校**实读自 `validate_acceptance_state._check_target_hw_gap`**，别自造别名）：

```jsonc
{"kind": "dtype_unsupported_on_target_hw",
 "dtypes": ["<任务书要、目标硬件那支实现没有的 dtype，非空>"],
 "task_doc_ref": "<任务书原文定位：章节/行/原句摘要>",
 "op_def_ref":   "<op_def 出处：文件路径 + 行号/字段（证 op_def 确实声明了）>",
 "impl_ref":     "<目标硬件实现出处：aclnn_xxx.cpp:行 + 分支名（DTYPE_SUPPORT_LIST）>",
 "target_hw":    "<哪支硬件：如 Atlas A2/A3（非 regbase 分支）>",
 "op_def_dtypes": ["<op_def 实际声明的支持集，供交叉核验——须含上面的 dtypes>"],
 "impl_dtypes":   ["<目标硬件实现实际支持集，供交叉核验——须不含上面的 dtypes>"]}
```

⚠ **它绝不是「宣称有 gap 就免检」的后门**——与 C4 同为反后门硬校、**方向相反**（C4 证「op_def 没声明」，
本 kind 证「op_def 声明了、目标硬件那支没实现」）。`validate_acceptance_state` 五道硬校缺一即**拒**（拒 = 该 gap
不计入已挂账集 → 对应 dtype 仍按「静默收窄」判 → BLOCKED）：

1. **有据**：`dtypes`（非空）+ `task_doc_ref` + `op_def_ref` + `impl_ref` + `target_hw` 五者必填、类型正确。
2. **op_def 确实声明**：gap 的 dtype **须在**自报 `op_def_dtypes` 里（与 C4「不得在」相反）；不在 → 说明 op_def 其实
   没声明该 dtype → **该走 C4**，本 kind 拒。
3. **目标硬件那支确实没实现**：gap 的 dtype **不得在**自报 `impl_dtypes` 里；在 → 说明目标硬件其实实现了 →
   不是「没实现」的发现，本 kind 拒（自相矛盾/伪造）。
4. **不得覆盖真失败**：该 dtype 若**有真实用例在跑**（实测集含之）→ 拒。**这就是「没实现」与「实现了但跑挂了」
   的判别式**：前者压根造不出用例，后者一定有用例 + 证据，必须走精度/功能裁决。
5. **在需求内**：spec 声明了 `dtype_required` 时，gap 的 dtype 须确在任务书要求内。

**示例（im2col `bool`）**：`im2col_def.cpp` 的 `VALUE_DATA_TYPE_LIST` 含 `DT_BOOL`（→ `op_def_dtypes` 含 `bool`），
而 `aclnn_im2col.cpp:222-225` 非 regbase 分支的 `DTYPE_SUPPORT_LIST` 只有 `{float32, float16, bfloat16}`
（→ `impl_dtypes` 不含 `bool`）。任务书要 `bool` → 挂 `dtype_unsupported_on_target_hw`。

⚠ **两类 finding gap 的 verdict 侧接线状态不同——别把 target_hw 也当成能落 `passed_with_gaps`**：
- **C4 `dtype_unsupported_by_op_def`**：`validator` **已识别**该 kind → 裁决落 `passed_with_gaps`；
  `validate_acceptance_state` 把它纳入合法枚举并交叉核验，`run_workflow.run`（2026-07-22 当日稍晚接线）把
  `passed_with_gaps` 归入**精度放行集合**（与 `pass`/`passed_with_risk` 同列）→ **继续跑 Task3**，顶层
  `overall=PASSED_WITH_GAPS`、canonical state 同名、**退出码 2（挂人工 CP）**、`requires_human_cp=true`。
  端到端**已通**。
- **`dtype_unsupported_on_target_hw`**：**`validator` 侧尚未识别该 kind**（只识别 C4）→ 对这条 gap 仍产
  **干净 `pass`**，**当前不会**落 `passed_with_gaps`。门 `validate_acceptance_state` 已把它接进**覆盖门认账**
  与**双向交叉核验**：gate_task2 方向② 逮住「caseset 有结构合法的 target_hw gap，但裁决是干净 pass」→ 记 error
  → **gate FAILED → BLOCKED**（fail-closed，绝不让「算子未实现任务书要求的 dtype」机读成干净 `PASSED`/exit 0）。
  **故现阶段挂 `dtype_unsupported_on_target_hw` 的算子会走 BLOCKED、而非 `passed_with_gaps`**；要它端到端落
  `passed_with_gaps`，须先补 `validator` 侧对该 kind 的识别（本批未做）。落 spec 前知悉这一状态差。

⚠ 早前一版记「`precision_ok` 不认 `passed_with_gaps`、会跳过 Task3」——那是 C4 接线前的实况、现已过时。
一律**逐字引用确定性产物的实际字段并标来源**（ADR 0007），不自行宣告裁决。

## 1.3 torch 对标 / 多输出 / aclnn 两段式被测物怎么填

> 这一节回答的是「**任务书说对标 torch、被测物是个标准 aclnn 两段式工程、算子还有多个输出**」这条通路上，
> 那些**只有这条通路才用得到**的字段该怎么从任务书 × 被测来源抽出来。
> 权威依据（反推自实现，别凭记忆改）：`precision_policy.call_variants` / `active_output_names` /
> `derive_output_contracts` / `_torch_allclose_tol`、`gen_cases._build_aclnn_call` / `_value_profiles` /
> `_allow_empty_tensor`、`cpp_extension_codegen` / `cpp_extension_adapter`、
> `aclnn_runtime/perf_msprof.resolve_torch_baseline_plan`、准入白名单 `run_workflow._ACCEPTANCE_RUNNER_FORMS`。
> **不属这条通路的算子：以下字段一个都不要写**（`scenario` / `out_role` / `tolerance_source` 等全部可省略）。

### 1.3.0 四件事**各自独立**触发，别打包

很容易误以为「torch 对标 ⇒ 多输出 ⇒ 某个 runner_form」是一套。**不是**，是四个正交的判断，各自看各自的依据：

| 判断 | 看哪儿 | 落到哪个字段 |
|---|---|---|
| 真值口径是不是 torch | **任务书**『参考实现 / 功能对标』段 | `precision.oracle=torch`；`precision.standard` 仍先读独立精度条款，条款缺失才兜底 `torch_allclose` |
| 被测物是不是 aclnn 两段式工程 | **被测来源**（PR 或本地源码快照）的工程结构（§1.3.1 ②） | **只判「域内/域外」**：域内才继续往下抽，域外 fail-closed 记 gap。⚠ **它不决定 `runner_form`** |
| 用哪座**调用桥**去调它 | 验收准入（§1.3.1 ③），不看工程结构 | `runner_form`：**正式验收恒为 `cpp_extension`** |
| 算子是不是多输出 | **aclnn header 签名** + 任务书输出描述 | `params[].out_role` 等（§1.3.2） |
| 调用变体表（`runner_form ∈ {cpp_extension, aclnn_py}` **一律必填**）| **接口头里有几个入口 + attr 怎么分派** | `call_variants`（§1.3.3） |

🔴 **「被测物是 aclnn 两段式」不蕴含 `runner_form=aclnn_py`。** 这条旧文写反过，是 Roll 被判成
`aclnn_py`、卡在准入门前的直接原因，**下一个人别再照原样判**：

- `aclnn_py` 与 `cpp_extension` **调的是同一个东西** —— 被测来源构建出的那个 vendor `.so` 里的
  标准 aclnn 两段式符号。两者的差别只在**调用桥**：
  `aclnn_py` 用通用 ctypes 直接调；`cpp_extension` 按官方 `NpuExtension` / `EXEC_NPU_CMD_EXT`
  生成独立 `torch.ops` 调用桥再调（**不重编 op-plugin、不把 op-plugin 当 DUT**）。
- 所以「工程结构是 aclnn 两段式」证明的是**这个算子在域内、能被两条桥中任意一条调到**，
  它**不指定**用哪座桥。用哪座桥由**验收准入**定，而准入当前只有一个答案。
- `runner_form` 也**不反推性能标杆**：baseline 仍逐字按任务书核（§1.3.5）。

⚠ **`call_variants` 的触发条件是 `runner_form ∈ {cpp_extension, aclnn_py}`，不是「header 里有多个入口」**——
这两种形态**一律必填**（缺了 `gen_cases` 当场 fail-closed，判据是 `needs_aclnn_call`、两形态共用同一份解析；
`cpp_extension_codegen` 另有一道「非空列表」硬校）。入口个数只决定**产几条 variant**：
多入口产多条；**单入口也必须有一条**（`when` 写 `{"always": true}`，`active_attrs` / `active_outputs` 照签名填）。

`scenario=torch_ref_aclnn` 是**前两条同时成立**时才写的**合并标签**（编排层路由用）；单独成立时只写各自的字段、
**不要**写 `scenario`。单输出算子完全合法（照样要 `call_variants`，只是 `active_outputs` 只有一个名）。

### 1.3.1 怎么判 `scenario == torch_ref_aclnn` / `runner_form` 填什么

**① 真值口径侧（任务书，权威）**：任务书『参考实现』/『功能对标』段把 **torch 的某个 API 指定为功能与真值的对标物**
（典型句式：「参考 `torch.<api>` 功能，在昇腾 NPU 上基于 Ascend C 实现功能一致的算子」）。
→ `reference.type=torch`、`precision.oracle=torch`、`golden.method_kind=torch_cpu`，且这句话本身就是
`golden.authorization.kind=oracle_method` 的授权引文（任务书指定的是**真值口径**，不是「照着谁重写」的
`impl_reference`——两者判别见 SKILL 的 golden 判据锚要点）。
⚠ 任务书只说「对齐/参考某实现」而没把 torch 立为真值口径 → **不是**本场景，别硬套。

**② 被测物侧（被测来源的工程结构，`pr_facts.json` 的改动文件 + `key_files`；`local_source` 快照同形）**
——这一步判的是**域内/域外**，**不是** `runner_form`：
- **仓根**有 `build.sh`，算子目录（`<op_subdir>`）下有 `op_host/`，且**在 `<op_subdir>` 下有界递归**（深度 ≤3、不跟随软链）能找到 `aclnn_*.h`（剔 `*_impl.h`）；
  ⚠ **接口头落在哪一层不得预设**——2026-07-24 dogfood 实测：PR6429 的头在 `<op_subdir>/op_host/op_api/aclnn_median.h`，
  `<op_subdir>/` 下**并没有** `op_api/`。旧文写的「算子目录下直接有 `op_api/aclnn_*.h`」是错的，**钉死一层会把真 PR 判成「非域内」**；
  `op_api/`、`op_host/op_api/`、`op_api/include/` 各种落点都算数（gate 侧实现见 `aclnn_adapter.find_aclnn_project`）。
- header 里是**标准两段式**：`aclnnXxxGetWorkspaceSize(...)` + `aclnnXxx(...)`；
- **无 opaque descriptor**（不是「先 create 一个不透明句柄再多次调用」那种有状态 API）。
- ⚠ **不要求** per-op `build.sh`、**不要求** `op_graph/`（2026-07-24 实测：ops-nn 实验算子二者皆无）。

两者都成立 → `scenario: "torch_ref_aclnn"`；只有 ② 成立（真值口径不是 torch）→ `scenario` 省略。
**②不成立**（缺件 / 非标准两段式 / 有 opaque descriptor）→ 这属于「不支持的接口能力」，
按域外 fail-closed 记 `task_pr_gaps` 并停下问用户，绝不硬塞（律令 #0）。

**③ `runner_form` 侧（验收准入，与 ①② 都无关）**：**② 成立（域内）就写 `runner_form: "cpp_extension"`。**

理由不是「哪种形态更好」，而是**真机成熟度**：`cpp_extension` 是唯一跑通过完整 torch_parity 矩阵的通路
（Median PR6429 1152 例、`gate.passed=true`），所以准入白名单
`run_workflow._ACCEPTANCE_RUNNER_FORMS` 里当前**只有它一个**。落 spec 前须知：

| 写成 | 后果 |
|---|---|
| `cpp_extension` | ✅ 正常出验收裁决（`acceptance.json` / `verdict.json`）|
| `aclnn_py` / `cpp` | ⛔ **停止准入（2026-08-06）**：入口门直接拦下，且**没有任何办法跑起来**——逃生阀 `--allow-experimental-form` 已删除，显式 `--mode new_example` / `aclnn_py` 同样被拒。抽 spec 时抽出这两个值 = 这份 spec 直接作废，**正确处置是抽成 `cpp_extension`**（要补 `call_variants`）|
| 整字段省略（键缺席）| 全仓一致解析为 **`cpp_extension`**（缺省唯一真源 `repo_adapter.DEFAULT_RUNNER_FORM`；`run_workflow` 派 mode、`gen_cases` 校 dtype 与 `aclnn_call`、`cpp_extension_codegen` / `cpp_extension_adapter.prepare` 全走同一个读侧入口 `repo_adapter.spec_runner_form`，不会两处打架）。⚠ **但别这么写**：缺省兜住的是「漏写」这类事故，不是可以不声明执行身份的许可——正式验收一律显式写。⚠ 也**别再用省略表达 `cpp`**：那个语义已经没有了 |
| 显式 `null` / `""` | ⛔ 不是「没写」，是一份写坏的 spec。读侧一律 `.get(k, DEFAULT)`（**不用 `or` 兜**），所以这些值原样送进受控词表 → fail-closed 报「不受支持的 runner_form」 |

`runner_form=cpp_extension` 的三个连带后果：
- **必须**同时声明 `call_variants`（`gen_cases` 与 `cpp_extension_codegen` 各有一道 fail-closed）；
- 真机可收发 dtype 白名单据 form 放开（`repo_adapter.supported_np("cpp_extension")` 含 int / bf16，
  与 `aclnn_py` 同集）——即 §4 那条「fp32/fp16 才进 `params.dtype`」的 cpp-runner 限制**不适用**本形态；
  但**能不能进 `params.dtype` 仍要逐 dtype 核「算子在目标硬件那支真支持」**，不是形态放开就随便填；
- 它需要**独立构建收据**把 PR head / 本地快照的整树与子树 merkle、构建命令与实际加载的 vendor ELF 绑起来
  （三级门 `validate_acceptance_state` 会对账）——接入成本比 `aclnn_py` 高，这是已知账单，不是可以绕的理由。

⚠ 已有 spec 写着 `aclnn_py`（如 Roll）→ 要做正式验收就得迁到 `cpp_extension`，
**不是**把准入门放宽。旧 `aclnn_py` 跑出来的 PASS 属于旧 caseset，不得沿用。

### 1.3.2 从 aclnn 签名派生 `out_role` / `index_of` / `gather_from`

看**递归发现的接口头**（`<op_subdir>` 下有界递归找到的 `aclnn_*.h`，剔 `*_impl.h`；落点不预设层级，见 §1.3.1）里
`aclnnXxxGetWorkspaceSize` 的**输出形参**（一般是末尾那几个 `aclTensor*`，
在 `uint64_t* workspaceSize, aclOpExecutor** executor` 之前），逐个对到 `params` 的 `io:"out"` 条目，
**名字沿用 header 形参名**（去掉 `Out` 之类后缀时要保证与 `torch_baseline` / `call_variants` 里用的名一致）。

- **每个 out 都必须写 `out_role`**（多输出时），受控词表只有 `value` / `index`。
  - `value` —— 承载**数值结果**的输出。
  - `index` —— 承载**下标**的输出（配合归约/选择类算子给出「取自哪个位置」）。
    ⚠ **它的 dtype 必须是算子 `header` / `op_def` 实际声明的那个整数 dtype（`int32` 或 `int64`），照抄，不许改写**：
    golden 侧的 index 也**按 spec 声明的 dtype 存**（与真机 buffer 同源一处声明），写错就是拿另一份契约去验被测物。
    **不得因为 torch 返回 `int64` 就把 DUT 的输出契约改成 `int64`**——DUT 声明 `int32` 而 torch 给 `int64`，
    那是**一条要挂账的落差**（写 `task_pr_gaps`，如 `indices_dtype_mismatch`），不是可以顺手抹平的差异。
    （PR6429 实证：`op_def` 固定 `DT_INT32`，首跑却按 int64 抽了 spec，把这条落差掩掉了。）
  - ⚠ 空串 / 其它值一律 fail-closed。**别按「非 index 即 value」自己猜**——伪造的角色能骗过判据派生。
- **`index` 输出必须再写两项**：
  - `index_of: "<某个 out_role==value 的输出名>"` —— index 判据**不逐位比下标**（并列时 NPU 与 golden
    可以合法给出不同下标），改判 `index_value_consistency`：`gather(源, idx_npu)` 与 `gather(源, idx_golden)`
    的**值**是否在容差内一致；容差直接**取自它所引的那个 value 输出**。所以 `index_of` 必须指向一个真的
    `value` 输出，指向另一个 index / 空 / 不存在的名 → fail-closed。
  - `gather_from: "<某个 io==in 的参数名>"` —— gather 的**数据源**。必须由 spec 锚定：早期实现取「case 的
    第一个输入」，那样调一下 caseset 里 inputs 的顺序就能换掉 canonical 判据的 gather 源（判据只从 spec 派生，
    硬约束 #5）。多输入算子尤其要看清 header：**下标是相对哪个输入张量的**，就填哪个。
- **输出 dtype 只有两种合法写法**：**单值**（如 index 输出写 `["int32"]` 或 `["int64"]`，**按 header/op_def 实际声明**），
  或哨兵 `["<from_input>"]`（= 随输入 dtype，
  用于「值输出与输入同类型」的归约/选择类）。写成多候选 → 歧义 → fail-closed。
- out 参数必须**具名且不重名**（`outputs[]` / manifest / evidence 三处按 name 交叉核验身份）。

单输出算子：**不写** `out_role`（写了就触发多输出通路），保持 legacy，行为零变更。

### 1.3.3 从 aclnn header 派生 `call_variants`

**什么时候写**：**`runner_form ∈ {cpp_extension, aclnn_py}` 一律必填**（不填 `gen_cases` 当场 fail-closed；
`cpp_extension` 另有 `cpp_extension_codegen` 的「非空列表」硬校）。两种形态共用同一份逐 case 的
`aclnn_call` 契约（`gen_cases` 里那句 `needs_aclnn_call = runner_form in ("aclnn_py", "cpp_extension")`），
抽法完全一样。
「header 里有多个入口」**不是触发条件**、只影响变体条数：单入口写一条 `{"when": {"always": true}, …}` 即可。
⚠ `cpp_extension` 还可在 `call_variants[i]` 上写 `stage2_form`（`standard` / `extended`）——但那是**兜底**：
形态的首选来源是 CP-C0 预检（直接读 PR head header），spec 显式声明只在拿不到预检时用，且须与 header 一致。
两处都没有 → codegen 退回历史缺省并挂 `stage2_form_unverified`，**验收门据此拒**。

**为什么需要**：同一个算子的不同 attr 取值可能对应**不同的 aclnn 符号**与**不同的输出 arity**——header 里
往往能看到两个入口（如 `aclnnXxx(self, out, ...)` 与 `aclnnXxxDim(self, dim, keepdim, valuesOut, indicesOut, ...)`）。
没有变体表，driver 只能自己把 `null` 兜成某个默认值：那既不是该 case 的语义，还可能与签名对不上（越界写 / ABI 崩）。

**怎么抽**（按字段，**绝不按算子名**）：

1. 在**递归发现的接口头**（`<op_subdir>` 下有界递归找到的 `aclnn_*.h`，剔 `*_impl.h`；**不得预设目录层级**，见 §1.3.1）里
   列出该算子**全部**两段式入口，记下每个入口的**形参顺序**。
   > 🔴 **每个 `symbol` 必须是 PR 的被测 `.so` 真正导出的符号**（在**本 PR 自带的 header**里、且 build 后 `nm -D <libcust_opapi.so>` 里有）。
   > **绝不能**从「任务书点名的 API」或「参考实现/master 的既有 API 表面」抄符号——**任务书说支持两个入口 ≠ PR 就实现了两个入口**。
   > PR 常把多个语义**合并进一个符号**（靠某 attr / 某输出是否为空区分），或改名/加 `V2`/`V3` 后缀。
   > **判错的代价是「验的不是 PR、是 CANN 内置同名实现」的假 PASS**——2026-07-25 median 血教训：任务书写「支持 aclnnMedian、aclnnMedianDim」、
   > 参考实现（gather_v2）确有两个独立 API，acc-spec 便据此写了 `Median`(全局)+`MedianDim`(按维) 两个符号；
   > 但 **PR6429 把两者统一进一个 5 参 `aclnnMedian`**（全局=`indicesOut` 空、按维=`indicesOut` 非空），`.so` **不导出 `aclnnMedianDim`**。
   > 结果：`MedianDim` 变体在旧（宽松）档下**静默命中了 CANN 内置 `aclnnMedianDim`**、by-dim 用例其实没验 PR（严格档 `is_dut` 门已封死此路，build 时 `NOSYM` fail-closed）。
   > **正解**：两个变体都路由到 PR 真有的那个 `Median`，用 `active_outputs`（index 落不落地=`out_null`）区分全局/按维；「PR 未单独提供 aclnnMedianDim」记进 `task_pr_gaps.api_surface_mismatch`（是 API 表面差异、非功能缺失）。
   > 拿不到 PR 自带 header 或 build 产物来核符号 → **fail-closed 问用户**，别按任务书猜。
2. 对每个入口写一条变体：
   - `symbol` = **CamelCase 基名**（去 `aclnn` 前缀、去 `GetWorkspaceSize` 后缀）。真符号由运行时拼成
     `aclnn<symbol>GetWorkspaceSize` / `aclnn<symbol>`。
   - `active_attrs` = 该**入口签名里真正出现**的 attr 槽，须为 spec attr 顺序的**子序列**；签名里没有的
     形参必须排除（否则 ctypes argtypes 错位）。签名一个 attr 都没有 → 写 `[]`。
   - `active_outputs` = 该入口**真正落地**的 out 参数名，须为 spec out 顺序的**子序列**、不重名。
     未列出的 out 在 slots 里成为 `out_null`（传 NULL、不回读）。
     ⚠ 落地了 index 输出、却没落地它 `index_of` 所引的 value 输出 → fail-closed（判据悬空）。
   - `when` = 该变体的**分派谓词**，三选一，必填（**不允许隐式全匹配**）：
     `{"always": true}` / `{"attr":"<attr名>","is_null":true|false}` / `{"attr":"<attr名>","equals":<值>}`。
     逐 case 按**声明顺序取首个匹配者**；无匹配 → fail-closed，绝不退默认 → 所以**变体表必须覆盖
     `attr_matrix` 里的每一种组合**（写完自己拿 `attr_matrix` 逐行对一遍）。
   - `attrs`（选填）= 该变体**显式写死**的 attr 取值覆盖。⚠ 某 attr 在 `active_attrs` 里、而该 case 取值为
     `null` 且这里也没覆盖 → **fail-closed**（绝不静默转标量默认值）。
3. attr 参数的 `dtype` 在本形态下必须**恰有一个候选**且 ∈ `{int64, bool, float32/float}`——标量 ABI 宽度
   必须确定，多候选 / 空 / 未知一律 fail-closed（拼错宽度 = 段错误）。

#### Torch overload 覆盖与 `torch_parity_matrix`

`case_design.json` 只提供可复用的覆盖轴，**不是任务书要求的 Torch API 表面上限**。抽 spec 时须先从任务书和
Torch 签名列出必须对标的 overload，再逐个建立：

`Torch overload → attribute_profile → call_variants 条目 → active_outputs`

- 无可选 attr 的 overload 用对应 attr=`null` 表示“省略”，不得偷换成某个标量默认值；
- 该 profile 必须由 `when.is_null=true` 的变体承接；签名中不存在的 attr 从 `active_attrs` 排除；
- 仅返回 value 的 overload 只列 value `active_outputs`，其余输出走 `out_null`；
- 带 attr 的 overload 由 `when.is_null=false`（或更窄的 `equals`）承接；
- 同一 PR 符号承载多个 overload 是合法的，但每个语义仍须有独立 profile/variant 契约；
- `case_target` 按补齐后的 `dtype × rank × shape_profile × attribute_profile` 重算。

例如参考设计只有带 attr 的 6 个组合，而任务书还点名无 attr overload，则须新增 **1 个** null profile，
不能继续沿用原矩阵大小并宣称“完整 Torch 对标”。这条规则按签名/字段生效，不得按算子名特判。

#### 轴集契约：哪些必须交叉，哪些不进笛卡尔

**通用判据**：只有当两条轴**共同决定被测实现的分支或切分决策**时才必须交叉；只改变数值量级、
不改变控制流的轴，边际覆盖就够。应用这条判据时只看 spec 已有的**接口能力 / 算子类别**字段，例如
`params[].rank`、轴选择器 attr、`call_variants` / `active_outputs`、`operator_class`；**不得按算子身份分派**。

这条判据已有一正一反两个实测见证，二者必须一起读：
以下数字逐字来自 `dev-doc/oprunway-case-axis-design.md` §12.2 / §12.6 / §12.8：

- **正例（带轴选择器、实现按归约长度切分支的接口能力）**：`dtype` 类 × 归约长度共同圈定 90 个 cell，
  58 条 fail 全在区内、区外 0；任何一条轴单独都圈不出。更强的反事实是：可构造合法的 1-wise
  边际覆盖集避开整个失败区，对这个真实缺陷 **100% 漏报**。所以这一对必须交叉。
- **反例**：`keepDim` 在失败区内为 72.7% vs 81.8%，**无可测效应**；它只改变输出 shape，
  不改变切分决策。这个结果支持边际覆盖，防止把正例误读成「所有轴都要全交叉」。

当前轴集另有三条边界：

1. **特殊场景不进笛卡尔；决定②已定本档当前产 0 条。** 参考仓 `design_contract.py` 明文规定
   特殊场景若存在只能独立叠加，理由逐字是**避免组合爆炸**；这条结构约束不因数量为 0 而失效。
   取舍按用户既定的「最遵守原计划 + 实施时间最短」执行：参考仓明确给 structural `special=0`，
   而本仓 §12 对空 / 标量 / 上下边界没有任何实测输入；这与值域 regime 同属「收益零实测支撑」，
   因此不把 legacy 四类 forced 项接入 `torch_parity`，也不把 structural 的明文结论外推成其它类别
   应新增场景。三类受控 `operator_class` 均为 0；`gen_cases` 在 `special_scenario_policy` 中写
   `reason + evidence + emitted=0`。抽 spec 时不得声明 `allow_empty_tensor / empty_axis`，它们在本档
   没有消费方、会 fail-closed；算子事实改用 `task_pr_gaps` 或 `_` 前缀说明。
2. **多输出不是自由轴。** 输出集是 attr 轴的确定性函数：`_select_call_variant` 据 attrs 选变体，
   `active_outputs` 随之确定。再加一条“输出个数”轴只会重复计数，并造出 attr 与输出 arity 不可能同时成立的组合。
3. 🔴 **值域 regime 轴暂不引入。** 现 `torch_parity` 只有 `uniform` 一档，结构上没有第二档可比，
   因而测不了 regime 与 dtype 或其它轴的交互；「加了有没有用」至今**零实测支撑**。
   引入前必须先有能对照至少两档 regime 的可测矩阵；否则直接增加第二档只会让相应用例数翻倍，
   覆盖收益仍未知。这里明确留痕是为了防止后续把“有意不加”误当成遗漏。

结构示例（**纯占位、非任何真实算子**）：

```jsonc
"call_variants": [
  {"when": {"attr": "<attrA>", "is_null": true},
   "symbol": "<SymbolBase>",  "active_attrs": [],                     "active_outputs": ["<value输出名>"]},
  {"when": {"attr": "<attrA>", "is_null": false},
   "symbol": "<SymbolBaseX>", "active_attrs": ["<attrA>", "<attrB>"], "active_outputs": ["<value输出名>", "<index输出名>"]}
]
```

### 1.3.4 `tolerance_source` 怎么选（仅 `standard == torch_allclose`）

`torch_allclose` 的判据是 `|actual-golden| <= atol + rtol*|golden|`，**容错率 = 0**（不是坏点占比门），
`equal_nan=true`。rtol/atol 从哪来由 `tolerance_source` 决定，受控词表三选一：

| 取值 | 什么时候选 | 注意 |
|---|---|---|
| `dtype_table`（**缺省**，整字段省略即此）| 任务书**没写**具体 rtol/atol —— 绝大多数情况 | 逐 dtype 容差表 `precision_policy._TA_DTYPE_TOLS`（adapt 自参考仓，一手 tilelang2ascend）。**表里只有 float16/bfloat16/float32/float64**；整型 / bool 输出**不进此表**、由 `effective_standard` 自动转 `exact` |
| `taskdoc` | 任务书**白纸黑字给了** rtol/atol 数值 | **必须**同时写 `precision.taskdoc_tol: [rtol, atol]`，缺 → fail-closed。两值须为**非 bool、有限、非负**实数（`inf` 会让判据恒真 = 门被拆掉，当场拒）|
| `torch_default` | 任务书明确要求「按 `torch.allclose` 默认判」 | 取 (rtol=1e-5, atol=1e-8)。**很紧**，别拿它当「不确定时的默认」 |

⚠ **只有整字段省略（None）才落缺省 `dtype_table`**。写成 `""` / `false` / `0` 一律 fail-closed 拒——
字段一旦出现就必须是词表内的字符串（防一份写坏的 spec 悄悄拿到 dtype_table 的容差）。
⚠ `precision.threshold` 在本 standard 下仍只是向后兼容 digest（§3），真门控走结构化 policy。

**`precision.value_profiles`（可选）**：在常规覆盖之外**强制补**的受控数值形态用例，op-中立：
- `"nan"` —— 均匀底 + 前 1/4 位 NaN，压 **NaN 传播语义** 与 `equal_nan` 判据。**整型 dtype 不适用**。
- `"tie"` —— 小值集循环填充造大量并列，压 **`index_value_consistency`**（并列时下标可合法分歧）。
只在**任务书 / 算子语义确有这类边界要求**时声明（有 index 输出 → 基本该声明 `tie`；任务书提到 NaN 语义
→ 声明 `nan`）。⚠ 声明了却产不出（dtype 集里一个浮点都没有、tie 的形状造不出并列）→ **fail-closed**：
「声明覆盖却产零条」= 假覆盖。

### 1.3.5 性能 baseline 调用契约与 `target_ratio`

先逐字读任务书，并把用户对含混术语的明确确认作为事实写入 spec。实际要求直接 `aclnnXxx`
才写 `baseline:"aclnn_builtin"`；若已确认“小算子拼接等价于 Torch 对应接口”，写
`baseline:"torch_npu"`。不能只凭 API 名自行推断，也不重复证明用户已经确认的事实。

**`baseline: "torch_npu"`** = 基线取「**同一台真机**上 `torch_npu` 跑对应 torch API 的 kernel-only 耗时」
（真机内基线，**不是** GPU 外部数据）。采集走 `aclnn_runtime/perf_msprof.py`（msprof `--task-time` + MSTX
测量窗，只累加 device 计算 kernel、`MEMCPY_ASYNC` 不计入）。

声明它就**必须**给 `perf.torch_baseline` 调用映射，否则采集端 fail-closed（那边不猜 torch 形参）：

```jsonc
"torch_baseline": {
  "api": "torch.<...>",                       // 必须 torch. 开头的点路径；非 torch.* → fail-closed
  "positional": ["<slot name>"],              // 按序作 torch 位置实参；本 case 缺任一 → fail-closed
  "keyword": {"<slot name>": "<torch 形参名>"} // slot 在该 case 不存在 → 该 kwarg 自然缺席
}
```
- **键是 slot name**，即该 case `aclnn_call.slots` 里的名字 = **aclnn 头签名的形参名** = `params[].name`；
  值是 **torch 侧的形参名**。两边名字不同是常态，别想当然写成一样。
- `keyword` 的「自然缺席」机制正是**变体自动跟随 case** 的地方：某变体没有某个 attr（如无该形参的入口），
  对应 kwarg 就不传，调到的就是 torch 侧对应 arity。**采集端无需也不得为此写任何算子分支**（律令 #0）。
- out / `out_null` slot 一律忽略（torch 侧输出是返回值）。
- `warmup` / `repeat` 可选（缺省 5 / 20），只在任务书或实测有特别要求时才写。

**`target_ratio` 只从任务书原文换算，绝不抄参考仓默认值。**
参考仓 cannbot-ops-input `performance.py` 的 `PERF_SPEEDUP_THRESHOLD = 0.6` 是**它自家**的「达到 torch 0.6×
即合格」口径；抄过来会把「比基线慢 40%」判成达标。任务书是验收权威（硬约束 #1）：
『无劣化 / 持平』→ **1.0**（literal 读法，勿误宽成 0.95）；『≥95%』→ 0.95；『10X』→ 10.0。
任务书没写性能目标 → 按 **§4.1** 走 `perf.mode=measure_only` + 授权四件套并记 gap，**不要**填一个自己觉得合理的数、
**也不要**再靠「整块省略 `perf`」表达「没要求」（见 §4.1 ⚠）。

⚠ **对照物要与任务书条款是同一个东西，并走最短证据链**：任务书语义明确就直接配置；语义含混就
询问用户并记录答案。已确认等价于 Torch 接口时直接走 `torch_npu`，不再证明；未确认时才记
`task_pr_gaps` 并挂起。

### 1.3.6 `allow_empty_tensor`

**本字段仅在 legacy 档有消费方。** 顶层可选 bool，**缺省 `true` = legacy 现行为**
（opbase §1.4 把空 Tensor 当普适特殊场景强制铺）。
任务书**明写**「不支持空 Tensor」（Upsample 系、im2col 的部分形态、以及很多归约类）→ 写 `false`。
理由：强塞一条算子语义上不存在的用例，只有两个出口——要么 golden **替算子编造**它并不支持的语义，
要么整链 fail-closed 卡死。写 `false` 是**算子的显式声明**，不是默认放松，仍要在 `task_pr_gaps` 记依据原句。
⚠ **只接受真布尔**：`"false"` / `0` 会被真值性判断误读成「允许」，引擎 fail-closed 拒收。
（相关可选字段 `empty_axis`：允许空 Tensor、但 0 只能落在某一特定轴时声明轴号；两者都不写 = 老行为。）

`case_profile="torch_parity"` 下决定②已定特殊场景为 0 条，`allow_empty_tensor / empty_axis`
没有任何代码消费，声明即 fail-closed。任务书里的空 Tensor 支持事实仍须落 `task_pr_gaps` 或 `_` 前缀注释；
这只是如实记录，不得写成“本轮已覆盖空 Tensor”。

### 1.3.7 本节自检（并入 §7）

- **`runner_form` 已显式写出**，且正式验收写的是 `cpp_extension`（唯一准入形态，§1.3.1 ③）；
  写 `aclnn_py` / `cpp` 只在「明知这轮只做开发级验证」时才允许，且必须在 `task_pr_gaps` 记明「非验收通路」。
- `runner_form ∈ {cpp_extension, aclnn_py}` ⇒ `call_variants` 非空；每条 `when` 是三种谓词之一；`attr_matrix` 的**每一行**
  都能匹配到至少一条变体（无匹配 → 运行时 fail-closed）。
- `call_variants[].active_attrs` / `active_outputs` 分别是 spec attr / out 顺序的**子序列**且不重名；
  引用的名字都在 `params` 里存在。
- 多输出（out >1 或写了 `out_role`）⇒ **每个** out 都有合法 `out_role`；每个 `index` 输出都有
  `index_of`（指某 `value` 输出）+ `gather_from`（指某 `in` 参数）；out 名唯一。
- out 的 `dtype` 是**单值**或 `["<from_input>"]`，不出现多候选；**`index` 输出的单值 = header/op_def
  实际声明的整数 dtype（int32 或 int64），不是「torch 返回 int64 所以写 int64」**（差额挂 `task_pr_gaps`）。
- `standard=="torch_allclose"` ⇒ `oracle=="torch"`；`tolerance_source` 省略或 ∈ 词表；
  选 `taskdoc` 则 `taskdoc_tol` 是两个非负有限实数。
- `perf.baseline=="torch_npu"` ⇒ `perf.torch_baseline` 齐全（`api` 以 `torch.` 开头、`positional` 里的
  slot 名都在 `params` 里）；`target_ratio` 有**任务书原文依据**（在 `_target_ratio_note` / `threshold_source`
  或 gap 里写清出处），**不是** 0.6 这类抄来的默认值。
- `perf.baseline=="aclnn_builtin"` ⇒ `perf.aclnn_baseline.library=="cann_builtin_libopapi"`；
  `variants` 对每个性能 case 恰好命中一条，`symbol/slots` 完整；真机产物须带实际库 sha256 与符号定义方。
- `allow_empty_tensor` / `scenario` 等**不属本场景就整字段省略**，别写空串或占位值；
  `torch_parity` 下前者必须省略（决定②为 0 条，声明会被无消费方门拒绝）。
  ⚠ **`runner_form` 不在这条里**：它是执行形态声明、不是场景标签，正式验收必须显式写 `cpp_extension`。

## 1.4 `attr_axis_lengths` —— 任务书**点名的轴长度边界**怎么定向生成（可选，顶层）

> 权威依据（反推自实现）：`gen_cases._attr_axis_lengths` / `_axis_indices` / `_axis_length_shape`。

**为什么需要**：任务书常点名「**归约维 / `dim` 所指的那根轴上维度为 1**」这类边界。但 gen_cases 里
**shape 阶梯与 attr 取值是正交网格里各自独立取的**——含长度-1 轴的 shape 只由特殊场景产、且只配第一组 attr，
点名的那个组合可能**一条都撞不上**。2026-07-24 dogfood 实测坐实：median 的「dim 所指轴长度=1」实跑 **0 条**
（当时连告警都没有，`gaps=0` 的裁决照样出）。所以这类边界必须**声明式定向生成**，不能指望撞运气。

**怎么写**：

```jsonc
"attr_axis_lengths": [ {"attr": "<attr 名>", "lengths": [<正整数>, …]} ]
```

- `attr` —— **必须引用本 spec `params` 里已声明的 attr 参数名**（不在 attr 名集里 → fail-closed，防伪造覆盖）。
- `lengths` —— **非空正整数列表**（每个 ≥1；bool / 非 int / ≤0 一律拒）。
  ⚠ 长度 **0**（空张量）**不走这里**——用 `allow_empty_tensor` + `empty_axis` 表达（空张量有它自己一整套语义）。
- 语义：把该 attr 的值当作**轴下标**（int 或 int 列表，允许负数），对每个 `L` 生成一条「这些轴的长度 = L」的用例。
- 某组 attr 取值里该 attr 是 `null`（如「全局归约、压根没有那根轴」）→ **该变体自然跳过，不是错误**。
- **声明了却一条都产不出 → 当场 fail-closed**（「声明覆盖却产零条」= 假覆盖，本仓最忌）。撞上一般是：
  attr 的取值根本不是轴下标 / 轴下标越界 / in 参数的 `rank` 约束把 shape 掐死了 → **回去核，别把字段删掉绕过**。
- 整字段省略 = 不生成此类用例（现有算子零变更）。

**什么时候写**：任务书**点名**了某个轴维度边界就写；没点名别自己加（每加一条都是强制用例、要真产得出来）。
`gen_cases --dry-run` 的账本会打印 `attr_axis_lengths: 声明 N → 定向生成 M 条`；同一账本的**零配对告警**
（某 attr 取值 × 某 shape 结构类从未同时出现）是发现「该声明而没声明」的线索。

## 1.5 怎么判 `operator_class` —— 算子类别决定「该不该给它喂 NaN·Inf」（受控词表，顶层）

> 权威依据（反推自实现）：`gen_cases._operator_class` / `_emits_nonfinite` / `_special_entries` /
> `_value_profiles`。方法学出处 = 本项目 case 生成规则**要求参照**的仓 `Justbin/cannbot-ops-input`。

**为什么要它（不是洁癖，是判错过的账）**：引擎原先**无条件**给每个浮点 dtype 铺 §1.4 的
`inf` / `-inf` / `nan` 特殊值用例。2026-07-24 实测：**median PR6429 本该通过验收，我们判了 `FAIL(精度)`，
6 条 fail 全部是 NaN 用例** —— 用**超出验收口径**的用例把一个合格 PR 判挂了。

**方法学依据**（一手出处，可复核）：

- `skills/operator-case-generation/common/design_contract.py:427` —— 受控词表就是这三个：
  `{floating_compute, integer_compute, structural}`；
- 同文件 `:512` —— `if design["operator_class"] == "floating_compute": _validate_floating_rules(design)`，
  而 `_validate_floating_rules`（`:360-393`）**只在这一类里**强制 `nan / pos_inf / neg_inf / mixed_inf`；
- 另两类的口径见该仓 `SKILL.md:252` 原话：「For integer and structural operators, define value profiles and
  specials from the Torch semantics: **extrema, zero/one/minus-one, duplicate/negative/boundary indices,
  broadcasting relationships, reduction axes, saturation**…」—— **极值 / 0·1·-1 / 重复 / 越界索引 / 广播 /
  规约轴 / 饱和**，里头没有 NaN·Inf；
- 实证对照：该仓 `ops-bench/ops-eval-dataset/designs/aclnnMedian/case_design.json` 的
  `operator_class = "structural"`、**全文零 nan 零 inf**；`aclnnPdist`（`floating_compute`）则有
  nan 2 处、inf 8 处、special 22 处。

**怎么判**（看算子**在算什么**，不看名字、不看 dtype）：

| 判成 | 什么样的算子 | 例 |
|---|---|---|
| `floating_compute` | **做真正的浮点算术**：逐元素算术 / 超越函数 / 规约求和 / 距离 / 归一化——输出是**算出来的新数** | add、mul、div、exp、log、sqrt、softmax、layernorm、sum/mean、norm、**pdist**、matmul |
| `structural` | **选值 / 排序 / 索引 / 规约取元素**——输出**恒等于某个输入元素**（或它的下标），不做算术 | **median**、min、max、topk、sort、argmax/argmin、gather、index_select、slice/concat/transpose、cast 类搬运 |
| `integer_compute` | **纯整型逻辑**：位运算 / 整数比较 / 整数算术，语义上不存在 NaN·Inf | bitwise_and/or/xor、shift、整数 gcd/mod、整数比较 |

拿不准时的两个判别问句（按序问）：
1. **「把一个 NaN 喂进去，任务书/参考 API 对它的行为有明确规定吗？」** 有（如 torch 明写 NaN 传播规则、
   `equal_nan` 语义）→ 偏 `floating_compute`；没有、只能靠我们自己发明一个"应该"的答案 → 不是 floating。
2. **「输出的每个数，是不是原封不动等于某个输入元素？」** 是 → `structural`。

**判错的代价（两头都很贵，所以别兜默认）**：

- **判宽了**（结构 / 整型类误判成 `floating_compute`）→ 引擎照产 NaN·Inf 用例 →
  **不该判挂的判挂**（就是本次 median 的教训：合格 PR 被判 `FAIL(精度)`，6 条 fail 全是 NaN 用例）。
- **判窄了**（浮点算术类误判成 `structural`）→ NaN·Inf 用例整批不产 → **该测的没测**，
  报告却照常显示"精度全过"—— 假验收，比误判更难被发现。

**各档对用例生成的实际影响**（`gen_cases` 据字段分档、op-中立，**绝无按算子名分支**）：

| | `inf` / `-inf` / `nan` 特殊场景 | `value_profiles: ["nan"]` | `value_profiles: ["tie"]` | 空 / 标量 / 上下边界 |
|---|---|---|---|---|
| `floating_compute` | 产 | 可用 | 可用 | 产 |
| `structural` | **不产** | **fail-closed** | 可用 | 产 |
| `integer_compute` | **不产** | **fail-closed** | 可用 | 产 |
| 整字段省略（legacy）| 产（= 引入本字段前的行为） | 可用 | 可用 | 产 |

- **`tie` 三档通吃**：并列 / **重复值**正是参考仓给结构 / 整型类列的那一档特殊值，别跟 `nan` 一起砍掉。
- **`nan` profile 与非浮点类别同时出现 → 引擎当场 fail-closed**（不静默丢 profile——静默会让"账面声明了
  NaN 覆盖、实际一条没产"长期打架）。撞上就二选一：确实要验 NaN 传播 → 改判 `floating_compute`；
  是结构 / 整型类 → 从 `value_profiles` 去掉 `"nan"`。
- **整字段省略只是 legacy 兼容出口**（现有 isclose/sign/equal/neg 未声明、caseset 逐字节不变），
  **不是"拿不准就不写"的正当理由**：每个算子都落得进这三类之一，没有"不适用"。

## 1.6 `precision.case_source` —— 任务书给了用例，就用它的（受控词表）

**用户口径（2026-08-04 定）：任务书给了 case → 用任务书的，不自行生成；不给才自行生成 + 用默认精度标准。**
这不是效率优化，是验收权威归属：任务书是验收权威（AGENTS.md 5.8），另起炉灶铺正交网格
等于把任务书点名的测试点换掉。

**怎么判**（判据是任务书文本结构，**不是算子身份**）：

1. 任务书正文里有没有指向**成套自测用例**的链接 —— 典型句式
   `精度自测用例参考[自测用例目录](./self_test_case/<op>/)`。**相对链接也算**（相对任务书自身所在目录解析）。
2. 那个目录里是不是真的构成「成套件」——由 `acc-common/taskdoc_caseset.py` 的
   `discover()` 按结构判（cases JSON + golden `.py` + prototype JSON），**抽 spec 的人不用自己判**：
   编排层跑 `taskdoc_links.py` → `taskdoc_caseset.py`，结局落受控词表 `DISCOVERY_OUTCOMES`。
3. 结局 `recognized` → spec 写 `precision.case_source: "taskdoc"`；其余六种结局 → **BLOCKED**，
   **不回退自生成**（「认不出任务书的 case，那就自己造一套」正是这一档要堵的洞）。
   任务书压根没给用例 → **整字段省略**（= `generated` = 现行为）。

**两档的连带后果，落 spec 前须知**：

| | `generated`（缺省 / 省略） | `taskdoc` |
|---|---|---|
| 用例身份、shape、dtype、attr、值域 | 本引擎按覆盖-预算规则铺 | **全部**来自规范化后的任务书用例集 |
| `case_target` 怎么定（上面那段） | 按覆盖轴推算（`torch_parity` 档 = 完整矩阵大小），依据写进 `case_target_source` | **两档都必填**，只是这一档的数不由你推算：须**精确等于**规范化后的用例条数，`_taskdoc_plan` 逐字核对。⚠ **不是「不适用」**——不写照样 fail-fast |
| 规模预算（G4 降规模） | 行使 | **不行使**（降规模会把任务书点名的 shape 改掉，那就不是那条用例了） |
| `coverage_strength` 表述 | `1-wise+whitelist：…` | `taskdoc_provided：用例集由任务书提供（N 条…），覆盖强度由任务书决定` |
| 特殊值（inf/-inf/nan） | 按 `operator_class` 强制铺（§1.5） | 由任务书决定，本引擎这一档**不强制铺** |

⚠ **`coverage_strength` 两档表述必须不同**：用了任务书的 case 就**不得**再声称「1-wise + 白名单」——
那是本引擎自己铺网格时的覆盖强度，拿来描述任务书给的用例就是冒领（5.8）。

⚠ **性能维要跟着走**：`taskdoc` 档的性能候选池 = **全部可判精度的任务书用例**。
任务书用例天然不带「性能」维，不这么定的话性能维恒零数据。spec 侧照常写
`perf.case_source: "precision_cases"`（`perf` 存在时必填，见 §1 映射表那一行）。

## 2. verify_mode 决策树（⚠ 三值）

```
① 无数值张量输出 / 精度栏『不涉及』(Sleep 延时算子)      → behavioral（精度维度 na，靠功能 pass/fail）
② 输出 bool，或整型位运算/逐位对齐 CPU·torch(Equal,IsClose,RightShift) → exact，threshold=0
③ 其余：浮点输出 / 超越函数 / 距离·角度 / 含 cos·sin·exp·ln / 累加  → numerical
```
- **混合口径**（值输出 numerical + 下标输出）→ 主口径取『值』= numerical。
  ⚠ 下标输出**不是** exact：走多输出契约后，index 输出的判据是 `index_value_consistency`
  （gather 出来的值一致，容差随所引 value 输出），由 `out_role`/`index_of`/`gather_from` 派生，见 §1.3.2。
  早前写的「索引精确性由 golden 承担」是单输出时代的说法，多输出通路已不适用。
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

> ⚠ **例外·验收标准类字段**（精度阈值·oracle / 性能目标 / 硬件目标 / golden 口径）来源恒为任务书
> 或任务书引用的独立权威源。dtype 另按下表逐句判：任务书显式枚举时 PR 只作对照；任务书定义
> “所有进入 AICore 的类型”等实现域集合时，本轮 op_def 负责枚举集合成员。

| 缺什么 | 兜底 |
|---|---|
| **dtype 列表** | ① 任务书显式 dtype 表 → 逐字采用。② 任务书写“所有进入 AICore 的数据类型”等实现域集合 → 从**本轮同一 PR head** 的 `op_def` 与目标硬件 `AddConfig` 枚举集合成员；任务书定义集合，op_def 提供成员，不读旧 spec/报告。③ 任务书仅写无绑定对象的“所有类型”或完全缺失 → 查任务书引用的独立 built-in/TBE 能力源；仍无事实才问用户。④ 任务书明确写窄时，PR 不得扩张；PR 声明更窄则记录结构化 dtype gap。⑤ `params.dtype` 取任务全集与当前 `runner_form` 确定性能力表的交集，其余逐项挂账。|
| threshold 数值 | 按 §3 主 dtype 惯例填 + 标 (推断)；或留空走 needs_review；per_dtype 复杂→问用户/查工具 |
| verify_mode | 按 §2 决策树推断 |
| **aclnn 入口/语义**（③ runner 锚定用）| **从 `pr_facts.key_files` 里算子自带 example(`test_aclnn_*.cpp`) 读真实调用的 aclnn 函数 + 输入 dtype**——runner 必须锚定它，别凭 header 猜（Equal 曾因猜错入口/dtype 翻车）|
| repo | reference URL 反推；数学类→ops-math、index/loss→ops-nn (推断) |
| hardware（验收标准类·不猜）| 从任务书『适配硬件』栏取；缺失/模糊 → **问用户**（硬件属验收标准，不按 arch 推断、不缺省 A2/A3）|
| perf(性能栏『无』) | 走 **§4.1**：`perf.mode="measure_only"` + `measure_only_authorization`（ground `no_perf_requirement` + cite + quote + 快照指纹）。⚠ **不再靠「整块省略 `perf`」表达「没要求」**；**勿写 `{baseline:"none"}`**——下游把非空 baseline 当有性能目标会误报 `BLOCKED(声明性能目标但无性能用例)` |
| shape/规格 | 泛化验收，交 casegen；参数表 '-' 不阻塞 |
| CANN 版本 | 『算子开源仓指定版本』→ 运行时按仓定，不入 spec |

### 4.1 「本轮不做比值裁决」的**唯一**合法写法：`perf.mode=measure_only` + 授权

AGENTS.md §5.10 列了三种情形，**授权强度完全相同**（都要 ground + cite + quote + `taskdoc_snapshot_sha256`）：

| ground（`taskdoc_requirement`）| 什么时候用 | 判据从哪来 |
|---|---|---|
| `no_perf_requirement` | 任务书对性能**没有要求** | 任务书原文（引「性能要求：无」那一段） |
| `gpu_comparison` | 任务书要求的是**与 GPU 比对**（如「以 OpenCV CUDA A100 为参考，ratio ≥ 0.45×」）| 任务书原文那条 GPU 条款 |
| `change_class_no_perf_comparison` | 本轮改动属**新增 dtype / 扩展 shape·rank / 开发新算子**三类之一 | `spec.change.kind ∈ {add_dtype, extend_shape, new_op}`，机器对账 |

⚠ **别再用「整块省略 `perf`」表达「任务书没有性能要求」。** 省略不是声明，它表达不了「谁授权的、依据是哪句话」，
且下游拿不到目标比值时会落 `invalid_config`（BLOCKED）。**方向是 fail-closed**：误判成 `invalid_config` 只多报一次错，
误判成「无目标」会把一条真实性能要求整条吞掉。

⚠ **`measure_only` 是「不做对比」，不是「不做测量」。** 这一档下**每条**性能 case 仍强制有真机实测的
`npu_us`（有限正数 + 合法 kernel-only scope），缺一条即 `blocked` → 三级门 FAILED。报告刻意**不出**
`达标` / `cases_above_threshold` / `cases_scored`，改出 `measured` 系列键，**一个「达标」字都不许打印**（律令 5.8）。

⚠ **走 `gpu_comparison` / `change_class_no_perf_comparison` 时，任务书原有的比值 / 绝对门限 / 吞吐条款
照旧强制进 `task_pr_gaps` 标「未验收」**——改的是取证方式，不是条款可以不算数。

## 5. 多算子一书 → 拆多个 spec

N 个算子 → N 个 `<op>.spec.json`。**共享字段抽一次复用**(hardware/repo/oracle/generalize)，**逐算子独立抽** op/reference/change/params/verify_mode/perf/threshold/gaps。三档：
1. **同族仅入参差异**(FmodScalar↔FmodTensor、MinDim↔MaxDim、Median↔MedianDim)：共享 dtype/precision/perf，只 params+op 名不同。
2. **异构双算子**(Cast↔EmbeddingDenseGrad)：reference/change/perf/合入仓全不同，**必须完全独立**，禁止合并。
3. **第二算子参数表留空**(MaxDim 列填 '-')：从兄弟算子继承 + 两个 spec 的 gaps 都记『继承自兄弟(推断)』。

## 6. task_pr_gaps 收敛

**两种形态并存**：`kind` 已定义的**结构化条目**（门/validator 会读并硬校——`dtype_deferred`（见 §1.2a，
**须带 `capability_source`**）、C4 的 `dtype_unsupported_by_op_def`、`dtype_unsupported_on_target_hw`，
见 §1.2/§1.2a/§1.2b）必须按字段写全；其余仍写自由文本条目（历史条目原样被忽略、不报错）。
**别给自由文本条目乱安 `kind`**——安上就要过对应硬校，过不了就是 `overall=fail`。
⚠ 结构化条目**写不全 = 不算挂账**：`dtype_deferred` 缺 `capability_source`（或自报的层其实支持该 dtype）
会被覆盖门拒，该 dtype 随即按「静默收窄」判 BLOCKED——不是「写少一个字段但还是放行」。

每条记『缺什么 / 影响字段 / 兜底』。常见类型：缺 dtype 列表、缺 threshold 数值、缺 verify_mode 明写、缺 per_dtype 声明、缺 shape 规格、缺 CANN 版本、缺性能绝对基线、**语义矛盾需澄清**(bincount 支持负数 vs 必须非负)、**模板残留**(MaxUnpool2d 仓名矛盾、Cast 合入路径矛盾、自验证报告 `xxx` 占位)。供 op-acceptance 报告步骤列『任务书↔PR 落差』，推断项标 (推断)。无缺口→`[]`。

## 7. 校验（写完 spec 自检）

- `verify_mode ∈ {exact,numerical,behavioral}`；`numerical` 则 `threshold` 有数或明确留空走 needs_review。
- `params` 至少一个 io=out；attr 有 default（gen_cases 读 default 造 golden）。
- `verify_mode=exact` ⇒ `threshold=0`；`precision.threshold_source` 非空。
- add_dtype ⇒ `change.dtypes_added` 非空；其中 **pipeline 支持的** dtype 已并入 `params.dtype`，**不支持的** 只在 `change.dtypes_added` + `task_pr_gaps`（不强求 ⊆ params.dtype，避免让 gen_cases/runner 崩）。
- `precision` 对象存在（任何 verify_mode 都不省略整个对象）；任务书**无性能要求**时按 §4.1 写
  `perf.mode="measure_only"` + 授权四件套（**不再整字段省略**），且不写 `{baseline:"none"}`。
- **§4.1 · `perf.mode`**：写了 `measure_only` ⇒ 授权四件套齐全、`baseline`/`target_ratio`/`small_shape_exception`/
  `torch_baseline`/`aclnn_baseline` **五项一个都不出现**、`perf` 块其余字段在白名单内；
  走 `change_class_no_perf_comparison` ⇒ `change.kind ∈ {add_dtype, extend_shape, new_op}`。任一条不满足即 fail-closed。
- **`precision.case_target` 存在且是 ≥1 的整数**（**无缺省**，省略 → `gen_cases` 真跑与 `--dry-run` 都 fail-fast），
  且这个数**给得出依据**（`torch_parity` 档 = 完整笛卡尔矩阵大小，精确相等；其它档把算法/沿用来源写进
  `precision.case_target_source`）。⚠ **拿不准就停下问用户，不许随手填一个数**——「缺省 50」正是因为
  没人回答过这个问题才被删掉的（见上文『`case_target` 怎么定』）。
- **§1.3 · 轴集契约**：新增交叉只由「共同决定实现分支 / 切分」的接口能力证据触发，绝不按算子名；
  特殊场景不进笛卡尔且决定②已定 `torch_parity` 当前为 0 条（与值域 regime 同按零实测不扩面）；
  输出个数不作自由轴；值域 regime 暂不引入，直到先有能实测其交互收益的多档矩阵。
- 多算子每份 spec 的 op 唯一、gaps 独立。
- **C2 · attr 值类型**：每个 attr 的 `default`（及 `attr_matrix` 里的取值）∈ `bool/int/float/str` 标量 **或 `list[int]`**。
  ⚠ **数组只支持 `list[int]`**：嵌套数组、浮点数组、`list` 里混 `bool`（`[True]` 与 `[1]` 在 JSON 里都长成 `[true]`/`[1]`、语义会串）**引擎一律 fail-closed 拒**；空数组也拒（manifest 会错位）。真需要别的形态 → 记 gap、停下问，别硬塞。
- **§1.5 · `operator_class`**：受控词表三选一（`floating_compute` / `structural` / `integer_compute`），
  **每份新 spec 都判**（判法见 §1.5 的两个判别问句）。自洽硬校：类别 ∈ {structural, integer_compute}
  ⇒ `precision.value_profiles` **不含 `"nan"`**（含则 `gen_cases` 当场 fail-closed）；`"tie"` 三档都可留。
  词表外取值 → fail-closed；整字段省略只对 legacy spec 合法（= 照产 NaN·Inf），别拿来当"拿不准"的出口。
- **C3 · `rank`**（可选）：填了就得是 int 或**非空** int 列表、每个值在 1..8 内（`gen_cases` 的 shape 阶梯上限）；
  多个 in 参数各自声明时引擎取**交集**（常规构造路径下所有输入同形），交集为空 → fail-closed；
  **只在任务书/README/`*_infershape.cpp` 确凿写死 rank 时才填**，否则整字段省略（= 不限制 = 现行为）。
  ⚠ 填了 `rank` 后若该算子**造不出任何合法常规 shape**，`gen_cases` 会 **fail-closed**（拒绝产 0 条常规用例冒充验收）——
  撞上说明 rank 填错或该算子超出当前 shape 阶梯能力，**回去核，别把 `rank` 删掉绕过**。
- **C4 · dtype 冲突 gap**：`kind=dtype_unsupported_by_op_def` 的条目**四个字段齐全且有据**（§1.2 四道硬校），
  且这些 dtype **不在** `params.dtype`（不能既说 op_def 不支持、又真造用例去跑它）。
- **§1.4 · `attr_axis_lengths`**（任务书点名的轴长度边界）：任务书点名了「某 attr 所指轴维度 = L」这类边界 ⇒
  **必须**声明该字段（否则该边界大概率零覆盖，而 `gaps=0` 的裁决会掩盖它）；写了则 `attr` 引用**已声明的 attr 名**、
  `lengths` 是**非空正整数列表**（0 走 `allow_empty_tensor`/`empty_axis`）；**声明了却产不出 → gen_cases fail-closed**，
  撞上要回去核 attr/rank，**不许删字段绕过**。任务书没点名就整字段省略。
- **§1.6 · `precision.case_source`**：受控两值，**整字段省略 = `generated` = 现行为**。写了 `taskdoc` ⇒
  编排层**必须**把规范化 caseset 显式喂给 `gen_cases --taskdoc-caseset` / `run_workflow --taskdoc-caseset`，
  否则 fail-closed（**不回退自生成**）；这一档 `case_target` **照样必填**，且须**精确等于**规范化后的
  用例条数（对不上 `gen_cases` 当场炸——见上文『`case_target` 怎么定』与『两档的连带后果』表）。
  **拿不准就整字段省略**——判成 `generated` 等于把任务书点名的测试点整套换掉，代价特别贵。
- **§1.6 · `aclnn_tensor_format`**：只在 `runner_form == "cpp_extension"` 下有意义；**整字段省略 = 现行为**。
  写 `nd` 前须有 ABI 事实源（header/docs/example）支持，且该算子的 stage2 形态是 `extended`——
  落在 `standard` 上会 fail-closed。没核过就别写。
- **§1.3 · torch 对标 / 多输出 / aclnn 两段式被测物**：若写了 `scenario` / `runner_form` / `call_variants` /
  `out_role` / `tolerance_source` / `value_profiles` / `perf.torch_baseline` / `allow_empty_tensor` 中任一项，
  逐条过 **§1.3.7 自检清单**；不属该场景则除 `runner_form` 外这些字段**一个都不该出现**。
  ⚠ **`runner_form` 是例外**：被测物在域内就必须显式写，正式验收恒为 `cpp_extension`（§1.3.1 ③）——
  它是当前唯一能产验收裁决的形态，写成 `aclnn_py` / `cpp` 或省着不写都会让编排在准入门前停摆。
- **C1 · 输出形状**：spec 里**没有**输出形状字段（别自造 `out_shape`/`output_shape`/`shape_formula`）；
  非 elementwise 算子的输出形状由 per-op `golden.py` 的可选 `out_shape(in_shapes, attrs)` 定
  （详见 `skills/acc-runner/references/runner-skeleton.md` §6）。抽 spec 时只需在 `task_pr_gaps` 记「该算子非 elementwise、
  输出形状规则出自任务书/`*_infershape.cpp` 的哪一句」，供 ③ 产 `golden.py` 时锚定。

## 8. GPU 移植类特例（SPMV / DualMatmul 等）

无 TBE 基线：`reference.type=gpu/cpu`、`perf.baseline=gpu`（带 A100/H100 参考 us）、`target_ratio` 按倍数语义(0.5/0.8)、`hardware=950PR/DT`。精度 golden 来源记 reference（CPU 标杆），性能标杆入 perf。dtype 常以三列合法组合表给（非笛卡尔积），组合约束入 note/gap。
