# plan：repo-task-doc-write 交互改造实施方案 v3

状态：实施方案（2026-09-16）。v1 冷读评审 15 条（8 P1）→ v2 闭环核对
10 关 4 半 1 开 + 3 新点 → 本版逐条吸收。对应 `dev-doc/doc-write-intake-spec.md`
v3；术语与 FR/INV/A/B/I 编号沿用 spec §1。本版对 spec 的唯一反馈修订：FR2 的
原话去向按 D-1 澄清（见下），已同步进 spec。

## 0. 范围、通路与执行地点

改动全部落在 `plugin/skill/repo-task-doc-write/`，外加 defect-map 与 dev-doc 簿记。
写入通路按仓规：Claude 写、Codex 只读审；skill 改动门两项前置本 session 已满足。

**执行地点（待 Mr.0 裁定的仓规张力）**：本 skill 的 `tests/` 全是纯文本逻辑，
`plugin/CLAUDE.md`（随镜像分发的上游开发规则）明文「能用假目录加 monkeypatch
跑完的补测试」并要求仓根 pytest 改前改后各跑一次；而根 `AGENTS.md` §3 写「测试
在远程目标环境执行」。两文冲突时以 AGENTS.md 为准并记录张力——**本 plan 默认
申请例外：纯文本 pytest/lint 在本机跑**，理由是它不碰 NPU、不碰 ATK、无环境
前置；若 Mr.0 不准，全部测试改在远程容器执行，plan 其余部分不变。
**Mr.0 裁定（2026-09-16）：准本机例外。**

## 1. 设计决策

**D-1 decisions.json 结构（增量扩展）**

现状 `{<骨架key>: {"human_reply": "..."}}`，旧调度器以「`human_reply` 非空」为已答。

- 记录字段增补：`source`（intake/review/batch）、`confirmed_content`（被确认的
  内容文本）、`note`（过程记录，不构成拍板）。
- **原话去向（消解 v1 评审 #1 的冲突，spec FR2 已同步澄清）**：一切答复原话
  逐字保存——构成有效拍板的存 `human_reply`，不构成的（「暂不决定」等）存
  `note`。`answered()` 代码不动。
- 保留 key `_premises`：`{"random_operator": {"value": "random"|"nonrandom",
  "human_reply": "<原话>"}}`——机器可读值 + 原话凭据分开存。
- 保留 key `_superseded`：失效记录去处，`{key, record, reason, ts}` 列表；
  **失效 = 整条记录移入并从原位删除**——对只认非空 `human_reply` 的任何版本
  调度器都表现为未答（v1 评审已实测确认此机制成立）。
- **失效触发与传播规则（统一，不逐例特判）**：依赖表落
  `references/decisions-format.md`（数据表，非代码）：
  `2.2.project_mode → {2.3.signature, 3.5.tooling}`；`2.1.baseline →
  {2.3.signature, 2.4.*, 3.5.param_mapping}`；`2.3.signature →
  {3.5.param_mapping}`；`2.4.dtype → {3.2.threshold_table}`；
  `2.4.value_range → {3.5.generation_rules}`——**条件项的输入也入表**：签名或
  基线变化使既有 `3.5.param_mapping` 确认失效，条件重新求值，不会被
  `answered()` 的旧确认跳过。执行者 = 修改记录的 agent，按表沿闭包传播
  （间接依赖自动覆盖：baseline 变 → 2.4.dtype 失效 → 阈值表随之失效）。
  触发判据 = 该记录的 `human_reply` **或** `confirmed_content` 任一发生内容
  变化（原话相同、内容变了同样触发）。传播后受影响 key 回到 pending，
  下一轮总审重新生成、重新确认。

**D-2 调度侧签名判定：新增独立严格解析器**

v1 拟复用 `_signature.parameter_names` 被评审否决（实测 `def f(x` 返回 `['x']`、
双定义只取第一份、坏参数被跳过）。改为新增 `scripts/_review_signature.py`
（调度侧专用，门禁共享件零改动）：

- 支持语法白名单：恰好一个 Python `def` 签名，或恰好一个 C 风格原型。
- 完整判定 fail-closed：括号配平、每个参数位解析为合法标识符、无省略号/占位符、
  全文恰一个签名。返回 `{status: complete|incomplete|multiple, params}`。
- 三态：两侧 status 均 complete → 参数名序列逐位相等 ⇒ 一致，否则 ⇒ 不一致；
  任一侧非 complete 或缺 `confirmed_content` ⇒ 无法判断。
- 单测覆盖评审给出的全部反例：截断、双定义、坏参数、`/` 与 `*` 分隔符、去重。

**D-3 随机条件：决策表 + 有效文本面**

有效文本面（三种模式，互斥覆盖全部调用形态）：`--doc` 与 `--candidates` 同时传
报错退出 2。①有 `--doc`（检查既有任务书）：只以 doc 为面，与门禁同径。
②有 `--candidates`（撰写流程总审前）：面 = 逐 key 最新内容集合——该 key 本轮
候选块（见 D-4）优先，其次该 key 的 `confirmed_content`。③两者都不传
（旧式批次循环调用）：面 = 全部 `confirmed_content` 集合；无任何 confirmed
内容时面为空、S 记未命中，其余走决策表（存量 golden decisions 因 R=是落
「已闭合」行，行为不变）。任何模式都不拼接旧稿全文、说明文字与 `_superseded`
归档。信号扫描用门禁同一份 `random-operator-signals.json`、同区分大小写子串判据。

决策表（S=面命中信号，P=前提值，R=`3.2.random_strategy` 已有效拍板；
**R 最高优先，全表闭合**）：

| R | S | P | 判定 |
| --- | --- | --- | --- |
| 是 | 任意 | 任意 | 已闭合，不追问、不重开——存量记录（如 golden decisions，无 `_premises` 无 `confirmed_content` 但有策略答复）落此行，兼容不破 |
| 否 | 命中 | 任意 | 适用 → 进待问（随机：四策略之一；非随机：「不涉及+理由」） |
| 否 | 未命中 | random | 适用 → 进待问（前提独立生效，不被文本覆盖） |
| 否 | 未命中 | nonrandom | 不适用 |
| 否 | 未命中 | 缺失 | 无法判断 → **经由 `3.2.random_strategy` 本项**进待问，标注「条件未定：缺随机性前提」；agent 呈现时先问前提、记 `_premises` 再复核。前提不是骨架 key，不新增 pending 契约 |

**D-4 总审文件：呈现、扫描、批准范围三合一**

T3 总审的载体是一份**逐 key 分节的总审文件**（`evidence/review-round-<N>.md`）。
节结构固定：`## <骨架key>` 标题、detail 行（名称/section/failure），然后**恰一个
` ```candidate ` 围栏块**装实际候选内容。**机器只认围栏块**：从总审文件提取的内容仅限候选块，扫描面再按 D-3 模式②合成
（候选块优先、未入本轮的 key 取 `confirmed_content`）；`confirmed_content` =
对应节围栏块内容逐字复制——detail 元数据永不进入解析与扫描。该文件同时是：①呈现给人的内容；②`--candidates` 的扫描
输入；③批准范围的记录——整体同意只覆盖该文件本轮围栏块内容，文件归档不复用。
CLI 不负责呈现候选，呈现由 agent 按此契约完成（decisions-format.md 明文）。

**D-5 批次表（12 批 41 条，评审已核对通过）**

1 前提(3)、2 概述(4)、3 接口(5)、4 类型排布(3)、5 形状值域(2)、6 异常(1)、
7 实现约束(6)、8 精度(4 含条件)、9 性能(3)、10 验收环境(3)、11 自验(3 含条件)、
12 交付(4)。尺寸 3,4,5,3,2,1,6,4,3,3,3,4 = 41。ratchet 锁唯一归属与 1≤批≤6。

**D-6 收尾指引闭合**

无可呈现批次而 pending 非空时：逐项输出**完整 detail**（同批内格式）+ 明确动作
指引「按所列 key 记录答复后重跑」；退出码 0。测试用旧骨架 fixture 实际走完
「记录 → 重跑 → 推进」一循环（A2 的「可推进」以状态前进断言）。

## 2. 改动清单（按文件）

| # | 文件 | 改动 | 切片 |
| --- | --- | --- | --- |
| 1 | `references/taskdoc-elements.json` | 仅 `question_batches` → 12 批 | W-A1 |
| 2 | `scripts/next_questions.py` | 三态（D-2/D-3）、`--all`、`--candidates`、D-6 收尾 | W-A2 |
| 3 | `scripts/_review_signature.py` | 新增：严格签名解析器 | W-A2 |
| 4 | `references/decisions-format.md` | 新增：记录字段、保留 key、有效拍板、依赖表与失效规则、总审文件契约 | W-A2 |
| 5 | `SKILL.md`（阶段 A 部分） | T3 行链 decisions-format.md、失效规则一句 | W-A2 |
| 6 | `assets/intake-template.md` | 新增：必填面 + 前提 + 可选面 | W-B |
| 7 | `SKILL.md`（阶段 B 部分） | T1 行改写、命令区 `--all`/`--candidates`、总审流程段 | W-B |
| 8 | `tests/*` | 见 §3 | 随片 |
| 9 | defect-map、changes brief | D1/D2/D3 三条；落地摘要 | 收尾 |

不改：`_signature.py`、`check_taskdoc.py`、`make_taskdoc.py`、
`render_views.py`、模板、golden。`_checks.py` 原列不改，后按 spec 既定例外
（D14，2026-09-17）做一处条件化缺陷修复，替代验收见 spec 非目标节。既有 `test_next_questions.py` 中钉旧批次布局的
断言（如实现约束在第 6 批）**按新布局更新**——spec §0 已把批次布局列为允许
变化的内部布局，保留的是「跳到首个缺口」「已答跳过」等行为断言。

## 3. 验证：机械可测与语义产物两层

**验证边界（消解 v1 评审 #8）**：调度器、解析器、门禁是机械层，pytest 直接测。
intake 映射、候选生成、批准处理、失效传播是 **agent 语义层**——生产入口就是
agent 按 decisions-format.md 执行，不另造脚本，测试不得内置第二套实现冒充。
语义层的验收 = **一次真实走查**（agent 实际执行 B 组场景）产出过程产物，
pytest 核验产物一致性。

**机械层断言（pytest）**

| AC | 断言 |
| --- | --- |
| A1 | ratchet：41 条恰各属一批、非 human 不入批、1 ≤ 批大小 ≤ 6 |
| A2 | 旧骨架 fixture：批内全答 → 退 0 + 逐项 detail；按指引记录后重跑 → pending 缩小、可达 done |
| A3 | 决策表五行逐行：命中未答→待问；已拍四策略任一或「不涉及+理由」→ 不追问且重复调用幂等；未命中+前提 random→待问；nonrandom→不出现；前提缺失→经 `3.2.random_strategy` 待问。补两用例：**仅 confirmed_content 命中信号**（候选块无信号）→ S 命中；三模式面（doc/candidates/都不传）各得正确 S |
| A4 | 解析器反例全集（截断/双定义/坏参数/分隔符/去重）→ incomplete 或 multiple → 无法判断；完整两侧：逐位相等/数量差/命名差/重排/部分重合 → 一致或不一致各归位；「不适用+理由」拍板后关闭且 done 可达 |
| A5 | note-only 在 pending；`_superseded` 后原 key 在 pending——**新逻辑与复刻旧版判定（非空 human_reply 即已答）的独立断言各跑一遍**；仅原话旧记录已答且条件判无法判断 |
| A6 | CLI 参数与 `--json` 四字段**语义**断言：结构不变、已答跳过、单批模式跳到首个缺口（不锁批号与成员）；**无新参数（模式③）时行为确定**：面取 confirmed_content 集合，对存量 fixture（含 golden decisions）输出与决策表一致、不重开已闭合项 |
| B4 | `--all`：全部 pending 无遗漏无重复、detail 字段齐、`--all --json` 结构 |
| I1–I4 | 骨架四字段 diff 零、`_checks.py`/`_signature.py` diff 零、golden+defects 门禁输出逐字节、预算实测（见 §5）、pytest+lint 全绿 |

**语义层走查（agent 执行一次，产物入 ignored `reports/` 或 evidence/，pytest 核验）**

| 场景 | 过程产物与核验 |
| --- | --- |
| B1 快速路径 | 填好的 intake、decisions.json、review-round-1.md、任务书 md、门禁完整输出与封条，外加**对话凭据**（走查报告记录每次人输入的时点与内容，据以断言恰 2 次）。核验：**按 source 分路追溯**——source=intake 的 confirmed_content 溯到 intake 文件对应格，source=review 的溯到对应轮次总审文件围栏块，均逐字一致；封条 sha256 匹配 md。**另走一次缺口路径变体**：intake 少填两批 → 验最终全部解决、无死循环 |
| B2 条件闭环 | 同上，变体：无 doc、前提 nonrandom、生成规则候选含「正态分布」→ 总审文件含 `3.2.random_strategy` 节 → 拍「不涉及+理由」→ T4 落稿 → T5 过。仍 2 次输入 |
| B3 映射与部分批准 | 走查含「除 X 外同意」与一次拒绝→重生成→再确认；核验 X 仍 pending、拒绝项有两版候选记录；**原话逐字保真（intake 原文与 human_reply/note 逐字节对照）与空格跳过**逐项核验 |
| B5 失效传播 | **四个代表场景逐一走**：project_mode 变、baseline 变（含经 dtype 间接失效阈值表）、dtype 变、value_range 变——每场景核验受影响组进 `_superseded`、重走确认到关闭；反例（背景变更）核验零失效；同原话异内容场景一并走 |
| B6 交付核对 | intake 模板 key 集合与 FR1 逐项 diff（pytest 可测）；判据行溯源与术语首现——人工检查清单，逐项记录到走查报告 |

## 4. 实施切片（顺序执行，各自可回滚）

- **W-A1 骨架与覆盖**：#1 + A1 + checklist 重渲逐字节核对。回滚 = 还原骨架。
- **W-A2 调度与记录契约（阶段 A 完整闭合）**：#2/#3/#4/#5 + A2–A6 + I 组。
  阶段 A 交付时记录契约已从 SKILL.md T3 行接入运行入口，不依赖 W-B。
  回滚 = 还原 next_questions.py 与 SKILL.md，删 `_review_signature.py` 与
  `decisions-format.md`。
- **W-B 交互层**：#6/#7 + B 组走查与产物核验。回滚 = 删 intake 模板、还原
  SKILL.md 的 T1/命令区/总审段（不动 W-A2 已接入的 T3 行与失效句）。
- 收尾：#9 + 一轮 Codex checkpoint 评审（改到核心调度，无条件触发）。

## 5. 预算（INV5/I3 的计量集合与口径）

**计量口径（Codex 裁定，2026-09-16）**：条件引用不豁免——golden 计入 T4 最小
载入（「按需」只决定何时读，不改载入合计）。据此：

**预算表（SKILL' = 修改后的 SKILL.md，硬上限 3,323 字节 = 现状 3,217 + 106；
I3 以实测把关）**：

| 阶段 | 最小载入集合 | 合计上界 | 上限 |
| --- | --- | --- | --- |
| T1–T2 | SKILL' | ≤ 3,323 | 32,768 ✓ |
| T3 拍板 | SKILL' + decisions-format（≤4,096） | ≤ 7,419 | 32,768 ✓ |
| T4 落稿 | SKILL' + golden 21,526 + template 7,919 | ≤ 32,768 | 32,768 ✓（贴线） |
| T5 质量门 | SKILL' + checklist 2,147 | ≤ 5,470 | 32,768 ✓ |

执行约束：SKILL.md 的新增说明**全部下沉** `decisions-format.md`，SKILL 内只留
链接与最短指令；净增超 106 字节时等量精简既有措辞补足。
`assets/intake-template.md` 按需读取不入常驻链，单独记录字节数。
本期不动 golden 与 template。

## 6. 七维自评

| 维 | 自评 |
| --- | --- |
| 通用性 | 条件判定与门禁同词表同判据；解析器语法白名单明确，超界一律无法判断 |
| 泛化性 | 依赖表、批次、判据全在数据（骨架/format 文档）；新增依赖 = 表里加一行 |
| 简单性 | 门禁链五文件零改动；新机制四件且各一职：保留 key、严格解析器、总审文件、决策表 |
| 清晰性 | 载体契约集中 decisions-format.md 一处；总审文件三合一消除呈现/扫描/批准的错位 |
| 复杂度 | 有效拍板塌进「写哪个字段」；失效塌进「移走记录」；无新状态机 |
| 冷读可理解性 | 新 reference 与模板术语首现定义；B6 含人工冷读检查项 |
| 爆炸半径 | 改写点仅 next_questions.py 与骨架批次表；语义层零脚本（agent 按文档执行）；三片独立回滚，阶段 A 自闭合 |

## 7. 风险与对策

- 严格解析器把合法写法误判 incomplete → 方向 fail-safe（进待问多问一次），
  白名单可按实例扩展（数据侧演化）。
- 总审文件由 agent 组装，可能漏 key → `--all` 的 pending 清单是机械对账单，
  B1 核验「总审文件节集合 = pending 集合」。
- 预算口径若按回退版执行，SKILL' 余量极窄（106 字节）→ 已备下沉与等量精简方案，
  不阻塞实施。
