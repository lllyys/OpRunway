# 任务书输入校验判法（CP-B 第 0 步 · `dispatch_mode=validate_taskdoc`）

本页是 `dev-doc/oprunway-task-document-validation-standard.md` 那张 18 项表的**判法**：
逐项怎么才算「任务书已明确」、怎么才算「模糊」。表定义**要什么**，本页定义**怎么判**，
`acc-common/validate_taskdoc_input.py` 只做结构复核与机械路由，**判断本身在这里**。

**只看任务书自己**。这一步不读 PR、不读 op_def、不读 header——「PR 里写了」不能补任务书的缺，
那是后面 `task_pr_gaps` 的事。任务书是验收权威，这一步就是在问「这份权威够不够格当权威」。

---

## 0 · 三条通用判据（每项都先过这三关）

**判据一：能不能机械落到下游。** 判 `satisfied` 的门槛不是「提到了」，而是
「照这句话能不能直接填出 spec 字段、或直接决定一个验收动作，而且换个人填结果一样」。
「支持常用 dtype」提到了 dtype，但两个人会填出不同的 `params.dtype`——那是 `ambiguous`。

**判据二：引用取不出来就不是 satisfied。** 每个 `satisfied` 必须附任务书**逐字原文**
（`quotes[].text`）。脚本会去任务书里逐字找（比对前两边都删净空白），找不到当场 BLOCKED。
凑不出一句能支撑该项的原文，说明这项本来就没明确——填 `ambiguous` 或 `missing`，
**不要摘一句沾边的凑数**。引用可跨行、可跨句拼多条，但每条都得是原文里真有的连续片段。

**判据三：不确定就往严里判。** 判宽（模糊的判成明确）会静默生效、一路带进 spec 和真机跑测；
判窄只是多问用户一句。二者代价不对称。

---

## 1 · 状态怎么填

| status | 什么时候填 | 附什么 |
|---|---|---|
| `satisfied` | 任务书明确到能机械落地 | `quotes[]`（逐字原文，必填） |
| `ambiguous` | 提了，但不足以机械判定；或前后矛盾 | `rationale`：**模糊在哪、两种读法各是什么** |
| `missing` | 通读全文没有相关表述 | `rationale`：查了哪些章节、确认没有 |
| `not_applicable` | 仅条件项可用，且该场景确实不存在 | `applicable=false` + `rationale` + **`quotes[]`**（`conditional` 项必填） |

⚠ **一条引用只能支撑一个项**。脚本按归一后的文本跨项去重，同一条原文出现在两个项里当场
BLOCKED——否则从任务书里随便挑一句真原文就能把 18 项全标成 `satisfied`。不同项要引不同句；
一句话里的不同片段可以分别引用。

⚠ **`conditional` 项判 `not_applicable` 也要附原文**。「该场景不存在」是从任务书语义推出来的
结论，必须锚在原文上（如「本算子为逐元素计算，不涉及并列值选择或索引输出」）；只写一句
自由文本 rationale 说「我判断不会有」，脚本会 BLOCKED。两个性能项的不适用由顶层
`perf_required` 的证据统一背书，不必逐项再引。

`ambiguous` 的 `rationale` 要写成能直接拿去问用户的形式——它会原样进 `blocking_items`
摆给用户。写「不够清楚」等于没写；写「只说『dtype 与输入一致』，但没说多输出时
index 输出是不是也跟着变，两种读法会产出不同 golden」才有用。

---

## 2 · 条件项的适用性

三个 `conditional` 项要显式声明 `applicable`，**这个判断本身就是实质工作**，不能图省事一律填 false：

- **特殊语义**：算子语义里**是否真的会出现** tie/重复值、索引选择、NaN/Inf、正负零、空 tensor、
  标量输入、负轴。判 `applicable=false` 要能说清为什么不可能出现——
  「逐元素 abs，不涉及并列选择」是理由，「任务书没提」不是理由（那恰恰是 `missing`）。
  ⚠ 归约取元素类算子（median/topk/sort/argmax…）几乎必然存在 tie，判 false 前想清楚。
- **依赖与前置条件**：本轮交付**是否依赖**任务书之外的 toolkit、外部库、参考数据、额外仓或授权环境。
- **失败与例外规则**：任务书里**是否存在**不支持项、允许挂起项、baseline limitation 或已知限制。
  注意区分：任务书**声明了**限制但没说怎么处置 → `applicable=true` + `ambiguous`（阻断）；
  任务书通篇没有任何限制声明 → `applicable=false`。

两个 `conditional_perf` 项（性能 baseline / 性能口径）的适用性**不由本项自己定**，
由顶层 `perf_required` 统一决定，且 `perf_required=true` 必须附任务书里那句性能要求的原文。

---

## 3 · 18 项逐项判法

### 算子身份（must）
明确 = 算子名 + 所属仓/模块 + 任务或需求编号三者齐全，且能唯一定位。
典型不满足：只有算子名没有仓；编号写「见看板」；一份任务书里多个算子但没说各自编号。

### 交付范围（must · 唯一带机器可读清单的项）
明确 = 说清本次**新增/优化/修复什么**、**哪些明确不在范围内**，
**并把必选/可选交付件逐件落成 `deliverables` 清单**。
「不在范围内」这半句常缺——只写了要做什么、没划边界，判 `ambiguous`：
下游无法判断"没实现反向"是缺陷还是本来就不做。

⚠ **「验收基准」和「交付件」是两回事，任务书常在同一句话里同时出现。**
「以上游 X 的功能/精度/性能为唯一验收基准」说的是拿什么当**参照物比对**；
「X 层为必选交付项」说的是 **PR 必须包含什么**。同一个名字可以既是基准又是交付件，
也可以只是其中之一。判这项时两个问题分开答，别把基准当成交付件、也别因为它是基准
就以为不用交付。

#### `deliverables`：交付范围的机器可读半边

`quotes[]` 回答「任务书怎么说」，`deliverables[]` 回答「于是本次必须交付哪几件、哪几件可选」。
每条至少四个字段：

```json
{"id": "opencv_cpp_layer", "name": "OpenCV C++ 适配层 cv::GaussianBlur",
 "requirement": "required",
 "quotes": [{"text": "### 1. OpenCV C++ 层接口（**必选，逐字对齐**）"}]}
```

- `id`：`[A-Za-z0-9_.-]` 短标识，下游对账工件按它逐条指认归宿；
- `name`：交付件名称或层级，写给人看；
- `requirement`：受控词表 `required` / `optional`；
- `quotes[]`：支撑该判定的任务书**逐字原文**，规则同 18 项的引文（须真出现、须够长）。
  一条交付件可以引多句（分层图一句、章节标题一句、注意事项一句都算数）。

**脚本会按契约的受控标记词表（`必选` / `必须交付` / `须交付` / `必交付` / `可选` / `选做`）
扫一遍任务书原文，每一处出现都必须落在某条 `deliverables[].quotes` 或
`deliverable_scan_exemptions[].quote` 的引文范围内。** `delivery_scope` 判 `satisfied`
却还有没落进去的标记 → 当场 BLOCKED。这条挡的是实测过的一条路：摘一句
「aclnn 为必选交付项」就把交付范围判过，而任务书另外三处「必选」（适配层、kernel、
接口分层）一句没抽——于是下游拿不到任何可对账的必选交付件清单，
「PR 少交付了一整层」全流程无人发现，最后只能靠人手写进 `task_pr_gaps`，而人手写就会写错。

**清单与标记的一致性也是机械校的**：某条交付件的引文里只出现「必选」类标记，
却把 `requirement` 填成 `optional`（或反过来），当场 BLOCKED。一句话里必选可选都有
（「A 必选、B 可选」）时不设约束，那种句子本来就得靠判断。

#### 不是交付件的标记：显式豁免，不许静默跳过

有些「可选」修饰的不是交付件，是流程里的一步（如「GetWorkspaceSize →（可选 preprocess）
→ 执行入口」）。这类写进 `deliverable_scan_exemptions`，**必须附 rationale 说清
为什么它不是交付件**：

```json
{"quote": {"text": "GetWorkspaceSize →（可选 preprocess）→ aclnnGaussianBlur"},
 "rationale": "这处「可选」修饰的是调用流程里的一个中间步骤，不是交付件"}
```

豁免会原样进收据摆给编排层与人复核。**它是留痕的判断，不是免检通道**——
把每一处标记都豁免掉脚本挡不住，但会在收据里一条不落地摆出来。

⚠ 词表按「交付件定性」收窄，刻意**不**收 `必须` / `须` 这类泛义务词（任务书里它们大量
修饰行为要求，收进来只会把每份任务书逼成人工豁免堆）。任务书若用词表外的写法写
「必须提供 X」，本门扫不到它——那不表示已覆盖，而是这道门对该写法没生效，
清单仍须靠判断补全，确有普遍新写法时改契约词表（数据，不是代码）。

### API 与 overload（must）
明确 = 逐个列出要交付的接口、调用形态、全部 overload（有 `dim`/无 `dim`、in-place 变体等）。
典型不满足：只写算子名不写接口签名；写「以及相关接口」；overload 用「等」收尾。
⚠ 这项直接决定 `call_variants`，漏一个 overload 就漏一整条通路。

### 输入契约（must）
明确 = 输入数量、每个输入的含义、dtype 集合、rank/shape 范围、layout/format 约束**全部**可机械落地。
dtype 允许两种明确形态：**逐字枚举**，或**给出可绑定的集合定义**（如「所有进入 AICore 的数据类型」
——由 op_def 枚举成员）。既不枚举、也给不出可绑定定义 → `ambiguous`。
典型不满足：「支持常见 dtype」；「shape 不做限制」但后文又提到某维必须为 1。

### 属性契约（must）
明确 = 类型、合法范围、默认值、nullable/optional 语义、属性组合限制。
**默认值缺失是高频硬伤**——标准明写「不得自行选择默认值」，缺了就是 `ambiguous`，不许自己填 0。

### 输出契约（must）
明确 = 输出数量、dtype、shape 规则、多输出各自含义、optional output 的产出条件。
⚠ 多输出时每个输出是 value 还是 index 必须能判出来（决定 `out_role`/`index_of`/`gather_from`）。
「不能只依据当前实现猜测」——PR 里输出两个 tensor，不代表任务书要求两个。

### 功能语义（must）
明确 = 与哪个标准接口对齐（精确到 API 名与版本/来源），以及边界、异常、特殊值行为。
「与 torch 一致」不够——torch 哪个函数、哪个 overload、哪个版本。

### 特殊语义（conditional → 待确认项，不阻断）
适用时明确 = tie/重复值怎么选、索引取哪个、NaN/Inf 怎么传播、正负零、空 tensor、标量、负轴逐项有说法。
这项**不阻断流程**，未说明只列为待确认项进报告——但它直接关联 `operator_class` 和
`value_profiles` 判错的历史教训，`rationale` 要写清缺的是哪一档。

### Golden 标杆（must）
明确 = 真值来源 + **准确的 API/overload** + 运行环境 + **组合实现是否允许**。
四要素缺一即 `ambiguous`。「用 torch 对拍」缺 API 和环境；「参考实现见 PR」是**无效来源**
（canon: PR 不能当 golden 源），判 `ambiguous` 而不是 satisfied。

### 精度额外要求（optional）
未声明 → `missing`，走 workflow 标准精度口径，不阻断。
声明了但不清（如「精度要求更高」「误差尽量小」）→ `ambiguous`，**阻断**。
标准明写「不得自行解释或放宽」——宁可停下问，不许自己换算成一个数。

### 性能 baseline（conditional_perf）
`perf_required=true` 时明确 = 对照实现 + 准确 API + 是否要求同机同卡 + 语义等价依据。
⚠ **runner form 不能代替 baseline 定义**——「走 aclnn_py 通路」推不出 baseline 是 torch_npu。
「不劣化」只回答了阈值、没回答对照物，那是下一项与本项的分工，别混。

### 性能口径（conditional_perf）
明确 = kernel-only 还是端到端 + 统计量 + warmup/repeat + **达标公式和阈值**。
「性能不劣化」→ 阈值可机械换算为 `target_ratio=1.0`，但口径、统计量、warmup/repeat 仍需有说法；
全缺则 `ambiguous`，`rationale` 写明缺的是哪几项。⚠ 绝不套用参考仓默认 0.6。

### 目标硬件（must）
明确 = SoC/产品型号 + 数量 + **功能、精度、性能分别在哪些硬件验收**。
最后半句常缺：写了「A3」但没说性能是不是也在 A3 测。
「不得从本机环境反推」——手上有什么机器和任务书要求什么，是两回事。

### 交付物与 DUT（must）
明确 = 交付工程形态 + **实际被测构建物是什么** + 测试桥/框架封装算不算交付。
这项防的是「测错对象」和「把测试桥当 DUT」。只写「提交 PR」不算明确。

### 接入形态（must）
明确 = 必须交付的调用层级（kernel 直调 / ACLNN / Torch 封装…）。
「不能仅凭可运行路径代替交付要求」——能用 ctypes 调通，不代表任务书只要求这一层。

### 依赖与前置条件（conditional）
适用时明确 = 必需 toolkit、外部库、参考数据、额外仓、授权环境逐项列清（含版本）。
不明确 → 阻断，且**不进入跑测**。

### 失败与例外规则（conditional）
适用时明确 = 不支持项怎么记、允许挂起项的条件、baseline limitation 怎么处置、已知限制的边界。
不明确 → fail-closed 阻断。这项直接决定「某 case 失败」能不能挂起而不算 FAIL。

### 验收完成条件（must）
明确 = 什么情况下可 PASS + 是否允许部分覆盖 / 风险通过 / 延期项。
最常见的缺失项之一，也是最危险的——缺了它，「局部跑通」和「验收通过」就没有分界线。

---

## 4 · 产出与交还

落盘 `<workdir>/taskdoc_validation.json`：

```json
{
  "schema": "oprunway.taskdoc_validation",
  "schema_version": 1,
  "op": "<算子 snake 名>",
  "source_facts_digest": "<CP-A source_facts.json envelope 的 64 位 digest>",
  "perf_required": true,
  "perf_evidence": [{"text": "<任务书里那句性能要求的逐字原文>"}],
  "perf_required_rationale": "<perf_required=false 时必填>",
  "deliverables": [
    {"id": "<短标识>", "name": "<交付件名称/层级>",
     "requirement": "required",
     "quotes": [{"text": "<支撑该判定的逐字原文>"}]}
  ],
  "deliverable_scan_exemptions": [
    {"quote": {"text": "<那处标记的逐字原文>"},
     "rationale": "<为什么这处标记不是交付件>"}
  ],
  "items": [
    {"id": "operator_identity", "status": "satisfied",
     "quotes": [{"text": "<逐字原文>"}]},
    {"id": "special_semantics", "status": "missing", "applicable": true,
     "rationale": "<缺什么>"}
  ],
  "decisions": []
}
```

`decisions` 由 primary 在问过用户后追加，每条形如：

```json
{"id": "target_hardware", "action": "supplied", "resolved_status": "missing",
 "value": "<用户补充的事实>", "source": "user"}
```

- `resolved_status` 必须等于该项**当轮**的实际 status——否则上一轮的决策就能被搬到这一轮用。
- **`stop` 路由的项只接受 `action="supplied"`**（补齐事实）。`waived` 只对
  `list_pending` / `use_workflow_default` 的项开放：豁免掉 Golden 标杆、目标硬件或验收完成条件
  不会让这些事实凭空出现，只会让下游缺着必需输入继续跑；用户若不打算补，正确出口是停止验收
  去找任务书负责人。允许的动作由契约 `resolution_actions_by_route` 定，脚本据此校验。

- `items` 必须**恰好 18 项**、id 与 `acc-common/taskdoc_validation_contract.json` 逐项对齐，
  不多不少不重复——脚本按契约核对，缺一个就是 BLOCKED。
- `deliverables` 必须**显式给出**（任务书通篇没有任何交付定性标记时才允许是空数组）；
  `deliverable_scan_exemptions` 可缺省为空。
- `source_facts_digest` 取 CP-A 已落盘的 `source_facts.json` envelope 的 `digest` 字段。
- `decisions` **一律留空数组**：那是 primary 拿到阻断清单、问过用户之后才追加的，
  subagent 不得自行写入，也不得替用户判"这项其实不重要"。

回给 orchestrator 的结构化摘要固定含：18 项各自 status、判 `ambiguous`/`missing` 的项及
`rationale`、条件项的适用性判断与依据、`perf_required` 及依据、`deliverables` 里的
必选/可选件各几条。**不含任何自行宣告的通过与否**——阻断/待确认清单由
`validate_taskdoc_input.py` 机械派生，本 agent 不预判、也不替用户决策。

---

## 5 · 交付件 ↔ PR 对账（本步之后、`task_pr_gaps` 之前）

CP-B0 只读任务书、`fetch_source` 只读 PR，两者在此之前从不碰面——「PR 少交付了一个必选层」
于是全流程无人发现。`acc-common/reconcile_deliverables.py` 补的就是这次碰面：

```bash
python3 ${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}/acc-common/reconcile_deliverables.py \
  --root <workdir> --out deliverable_reconciliation.json
```

它消费 CP-B0 的 `deliverables` 清单 + CP-A 的 `pr_facts.json`，产出机器可读的
covered / gaps。**这是确定性对账，不是判定**（`acceptance_verdict` 恒 null）。

**它不做模糊名字匹配。** 「OpenCV C++ 适配层」这类描述没法可靠地自动映射到文件，猜错的
代价是「静默判成已覆盖」，比不判更坏。所以归宿要由编排层或人在 `<workdir>/deliverable_mapping.json`
里**逐条指认**，脚本只负责**验证**：

```json
{"schema": "oprunway.deliverable_mapping", "schema_version": 1,
 "taskdoc_bytes_sha256": "<收据 bindings 同名字段>",
 "taskdoc_validation_digest": "<收据 bindings.validation_digest>",
 "pr_facts_sha256": "<pr_facts.json 原始字节 sha256>",
 "mappings": [
   {"id": "aclnn_layer", "disposition": "present",
    "paths": ["<PR 改动文件或目录>"], "symbols": ["<key_files 里逐字出现的符号>"]},
   {"id": "opencv_cpp_layer", "disposition": "absent",
    "rationale": "通读全部改动文件，没有任何该层的源文件或接入点"}]}
```

- `present` 必须给至少一条 `paths` 或 `symbols`；路径要逐字命中 `changed_files`
  或作为目录前缀命中其下某个改动文件，符号要逐字出现在某个 `key_files` 正文里。
  **验不上就是缺口，不是通过**（`key_files` 只是 PR 的部分文件，查不到只说明「没查到」，
  不等于「PR 里没有」——所以交人复核）。
- `absent` / `uncertain` 必须给 `rationale`，各自落成 `missing_in_pr` / `undetermined` 缺口。
- 没写映射的必选件落 `unmapped` 缺口；三个绑定字段任一漂移即 BLOCKED（旧指认不得搬到新一轮）。
- CP-B0 清单本身没覆盖全（`deliverable_inventory.complete=false`）时**绝不 RECONCILED**：
  清单可能还漏着必选件，此时说「必选件全部有归宿」是假话。

退出码：`0` RECONCILED / `2` GAPS（交编排层或用户）/ `1` BLOCKED（结构性错误，重做）。
gaps 是 `spec.task_pr_gaps` 的**事实来源**——别再靠人凭印象手写那一段。
