# 验收 Skill 拆分实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `skill/repo-task-atk-test/` 拆成嵌套的两个子 skill——`case-gen` 只吃任务书、
产出封过印的用例交接包；`acceptance` 吃交接包加 PR 源码加任务书、做构建跑测与裁决——
父 `SKILL.md` 做路由，脚本与 reference 一个都不挪。

**Architecture:** 骨架 `artifact-contracts.json` 仍是一份，`stages` 加 `skill` 归属并新增 S0。
交接包是两侧唯一接口：`seal_bundle.py` 在生成侧出口写 `evidence/bundle.json`（全文件 sha256、
任务书 sha256、ATK 版本），`check_bundle.py` 在验收侧入口重算核对并做 PR 头文件与任务书签名
的一致性门。生成侧签名从任务书 §2.3 解析、dtype 从 §2.4 取，不读任何工程目录。

**Tech Stack:** Python 3 标准库；`align_signatures.py` 与 `freeze_inputs.py` 沿用既有的
`torch` / ATK 运行时导入。测试用 `unittest` 写、`pytest` 跑。

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-08-20-atk-skill-split-design.md`。本计划的「本文 §x」
  指该 spec 的章节；形如 `§2.3`、`§2.4` 且出现在任务书语境时指**任务书模板的章节**。
- skill 根目录：`skill/repo-task-atk-test/`。所有相对路径以此为基准，除非写明 `docs/` 或
  `skill/repo-task-doc-write/`。
- 行文遵守 `.claude/rules/prose-style.md`。新写的两个子页与路由页基线为 0；现有 `SKILL.md`
  17 处存量按归属分到三个文件后重定基线，总和不得超过 17。
- 四条红线（本文 §2.6）：生成侧连工程目录都不读；ATK 不改；封印后 S2 产物只有
  `rewire_adapter.py` 能动且必须更新清单；`check_bundle.py` 重算 sha256 不信清单自述。
- 脚本退出码：`0` 通过，`2` 判据不满足，`3` 结构或输入不对。
- 开发流程按仓根 `CLAUDE.md`：骨架先行 → reference 补规范 → 失败测试 → 实现 → `render_views.py --write`。
- 测试命令（上游仓根）：`PYTHONPATH=third_party/ATK python3 -m pytest skill/ tests/ -q`。
  当前基线 `22 failed, 890 passed, 13 skipped`，22 条既存失败见仓根 `CLAUDE.md`。
  任何任务完成后 failed 数不得上升。
- 下游镜像仓（OpRunway）跑同一条命令时把 `third_party/ATK` 换成
  `repos/repo-task-atk-test/third_party/ATK`，`skill/` 换成 `plugin/skill/`。
- 每个任务独立可验，先不拆后拆：Task 1–8 全部在现有单 skill 内落地，跑完一轮端到端仍是
  五阶段；Task 9 才出现三份 SKILL.md。

---

## 文件结构

**改动的既有文件**

| 文件 | 改什么 |
| --- | --- |
| `references/artifact-contracts.json` | `stages` 加 `skill` 与 S0；`gate_inventory` 加 5 道；`artifacts` 加 2 件 |
| `scripts/_contracts.py` | 校验 `skill` 字段；`S0_BLOCKING_SCRIPTS`；按 skill 过滤的渲染入口 |
| `scripts/align_signatures.py` | `--signature-source taskdoc --task-doc` 模式 |
| `scripts/make_must_cover.py` | `--dtype-source` 接受任务书 |
| `scripts/derive_interface.py` | `interface.json` 记 `task_doc.sha256` |
| `scripts/rewire_adapter.py` | 改写后更新 `bundle.json` |
| `scripts/mark_step.py`、`scripts/probe_progress.py` | 按 skill 过滤卡与进度 |
| `SKILL.md` | 改为路由页 |
| `CLAUDE.md`、`README.md`、`docs/skills/repo-task-atk-test/*.md` | 两种入口 |
| `tests/test_gate_premises.py`、`tests/test_contracts.py`、`tests/test_document_style.py`、`tests/test_progress_probe.py` | 按本文 §7 适配 |
| `skill/repo-task-doc-write/tests/test_shared_facts_sync.py` | 同步名单加 `_taskdoc.py` |

**新增文件**

| 文件 | 职责 |
| --- | --- |
| `case-gen/SKILL.md` | 生成侧流程页：S1 → S2 → 封印 |
| `acceptance/SKILL.md` | 验收侧流程页：S0 → S3 → S4 → S5 |
| `scripts/_taskdoc.py` | 任务书 §2.3 / §2.4 解析，拷自 doc-write 的 `_signature.py` 与 `_taskdoc_parser.py` 所需部分 |
| `scripts/seal_bundle.py` | 生成侧出口，写 `evidence/bundle.json` |
| `scripts/check_bundle.py` | 验收侧入口，写 `evidence/bundle_intake.json` |
| `references/handoff.md` | 交接包规范：目录、清单 schema、排除名单、两道门的判据 |
| `tests/test_taskdoc_source.py`、`tests/test_seal_bundle.py`、`tests/test_check_bundle.py`、`tests/test_skill_routing.py` | 见各任务 |

**交接包（跑 skill 时生成，不进仓）**

```
<工作区>/atk-case-<op>/               生成侧产物，验收侧复制为 atk-verify-<op>/ 后工作
├── evidence/bundle.json             封印清单
├── evidence/bundle_intake.json      验收侧接收结论（只在副本里出现）
└── …                                今天 S2 结束时的全部产物，路径不变
```

---

## Task 1: 骨架加 S0 与阶段归属

**Files:**
- Modify: `references/artifact-contracts.json`
- Modify: `scripts/_contracts.py`
- Modify: `tests/test_contracts.py`、`tests/test_gate_premises.py`
- Modify（派生）: `references/gate-inventory.md`、`references/decision-points.md`

**Interfaces:**
- Produces: `stages[*].skill ∈ {"case-gen", "acceptance"}`；`stages.S0`；
  `_contracts.S0_BLOCKING_SCRIPTS = ("check_bundle.py",)`；`_contracts.stages_of(skill)`。
- Consumes: 无。

- [ ] **Step 1: 写失败测试**

在 `tests/test_contracts.py` 的 `SpineStructureTest` 加：每个 stage 必须有 `skill` 且取值合法；
S0 存在且归 `acceptance`；S1、S2 归 `case-gen`；S3–S5 归 `acceptance`。在
`tests/test_gate_premises.py` 把 `test_skill_entry_states_the_right_count_and_stage` 拆成按
skill 分组的两条断言，暂时仍指向根 `SKILL.md`（Task 9 再改指子页）。

Run: `PYTHONPATH=third_party/ATK python3 -m pytest skill/repo-task-atk-test/tests/test_contracts.py skill/repo-task-atk-test/tests/test_gate_premises.py -q`
Expected: 新增断言红，其余绿。

- [ ] **Step 2: 改骨架**

`stages` 每项加 `skill`；按本文 §6 新增 `S0`。`gate_inventory` 先只加
`seal_bundle.completeness`（S2）与四道 `check_bundle.*`（S0），`script` 指向尚未存在的脚本——
`test_gate_premises` 要求脚本存在，所以本步同时放两个只含 `--help` 与 `sys.exit(3)` 的占位脚本，
Task 5、6 再填实现。`artifacts` 加 `evidence/bundle.json` 与 `evidence/bundle_intake.json`。

- [ ] **Step 3: 改 `_contracts.py`**

`load()` 后校验 `skill`；加 `S0_BLOCKING_SCRIPTS`；`render_card(data, stage)` 不变，
新增 `stages_of(data, skill)` 返回该 skill 的阶段序列（`case-gen → [S1, S2]`，
`acceptance → [S0, S3, S4, S5]`）。

- [ ] **Step 4: 派生视图**

Run: `python3 skill/repo-task-atk-test/scripts/render_views.py --write && python3 skill/repo-task-atk-test/scripts/render_views.py --check`
Expected: 退出码 0；`gate-inventory.md` 多出 5 道门。

- [ ] **Step 5: 跑测试**

Run: 同 Step 1
Expected: 全绿。`test_help_matches_gate.test_every_inventoried_gauge_prints_help` 因占位脚本
有 `--help` 而绿。

---

## Task 2: 任务书解析模块与任务书指纹

**Files:**
- Create: `scripts/_taskdoc.py`
- Create: `tests/test_taskdoc_source.py`
- Modify: `scripts/derive_interface.py`
- Modify: `skill/repo-task-doc-write/tests/test_shared_facts_sync.py`

**Interfaces:**
- Produces: `_taskdoc.signature_block(md_text) -> str`（§2.3 代码块原文）；
  `_taskdoc.param_dtypes(md_text) -> dict[str, list[str]]`（§2.4 参数名 → dtype 列表）；
  `_taskdoc.sha256(path) -> str`；`interface.json["task_doc"] = {"name", "sha256"}`。
- Consumes: `skill/repo-task-doc-write/scripts/_signature.py`、`_taskdoc_parser.py`。

- [ ] **Step 1: 写失败测试**

用 `skill/repo-task-doc-write/references/golden-task-doc.md` 当正向 fixture：§2.3 解析出
`aclnnSlidingTileAttentionGetWorkspaceSize` 的 9 个业务参数（摘掉 workspace 四件套）；
§2.4 的 `q` 对应 `["FLOAT16", "BFLOAT16"]`，`windowSizeLen` 对应 `["int"]`。反向 fixture 用
doc-write 的 `tests/fixtures/defects/no_headings.md`：两个函数都返回空并给出「缺 §2.3」
「缺 §2.4」的报错文案，文案里要出现 `repo-task-doc-write`。

- [ ] **Step 2: 拷并裁剪**

只拷 `_signature.py` 的 C 声明解析与 `_taskdoc_parser.py` 的章节、表格反解两部分到
`_taskdoc.py`，文件头注明来源与同步方式。`test_shared_facts_sync.py` 的 `SHARED` 改为
成对列表，加 `("scripts/_taskdoc.py", 对应来源片段的 sha256 由一个小脚本从两份源文件拼出)`；
若拼接不可靠，退而只比对拷过来的函数体。

- [ ] **Step 3: `derive_interface.py` 记指纹**

`--task-doc` 已是输入，派生时把文件名与 sha256 写进 `interface.json["task_doc"]`。
`tests/test_derive_interface.py` 加一条断言。

- [ ] **Step 4: 跑测试**

Run: `PYTHONPATH=third_party/ATK python3 -m pytest skill/ -q -k "taskdoc or derive_interface or shared_facts"`
Expected: 全绿。

---

## Task 3: `align_signatures.py` 任务书模式

**Files:**
- Modify: `scripts/align_signatures.py`
- Modify: `tests/test_align_signatures.py`
- Modify: `references/plugin-authoring.md`（「签名只能从工程目录里读」一节）

**Interfaces:**
- Produces: `--signature-source taskdoc --task-doc <md>`；输出的 `signature_alignment.json`
  加 `source: {"kind": "taskdoc", "sha256": ...}`；头文件模式输出 `source.kind = "header"`。
- Consumes: `_taskdoc.signature_block`。

- [ ] **Step 1: 写失败测试**

用 golden 任务书跑 taskdoc 模式，不给 `--env`，期望退出码 0 且 `source.kind == "taskdoc"`；
头文件模式不给 `--env` 仍退出码 2（保留既有白名单行为）；两种模式对同一份声明产出的
`parameters` 逐项相等。

- [ ] **Step 2: 实现**

`--env` 改为仅头文件模式必填；taskdoc 模式从 `_taskdoc.signature_block` 取声明文本后走
既有的 C 声明解析，`require_project_source` 与 `reject_installed_header` 不介入。
reference 里补一段：生成侧的签名来源是任务书 §2.3，工程目录留给验收侧的一致性门。

- [ ] **Step 3: 跑测试**

Run: `PYTHONPATH=third_party/ATK python3 -m pytest skill/repo-task-atk-test/tests/test_align_signatures.py -q`
Expected: 全绿（本机缺 torch 时该文件按既有方式 skip，需在有 torch 的机器上补跑一次）。

---

## Task 4: `make_must_cover.py` 的 dtype 来源接受任务书

**Files:**
- Modify: `scripts/make_must_cover.py`
- Modify: `tests/test_make_must_cover.py`
- Modify: `references/case-design.md`（dtype 轴来源一节）

**Interfaces:**
- Produces: `--dtype-source <任务书>` 合法；核对方式为「文件 sha256 == `interface.json.task_doc.sha256`」。
- Consumes: `_taskdoc.param_dtypes`、Task 2 的 `interface.json["task_doc"]`。

- [ ] **Step 1: 写失败测试**

decl 含 dtype 轴，`--dtype-source` 指向 golden 任务书、`--interface` 指向记了同一 sha256 的
`interface.json`：退出码 0，dtype 面等于 §2.4 各 tensor 参数 dtype 列的并集。把任务书换成
另一份（sha256 不同）：退出码 2，报错点名「与 interface.json 记录的任务书不是同一份」。

- [ ] **Step 2: 实现**

`require_project_source(args.dtype_source, args.env, "--dtype-source")` 改为先判来源类型：
`.md` 且 sha256 与 `interface.json.task_doc.sha256` 相等 → 任务书模式；否则走既有工程树
白名单（验收侧或旧流程仍可用）。

- [ ] **Step 3: 跑测试**

Run: `PYTHONPATH=third_party/ATK python3 -m pytest skill/repo-task-atk-test/tests/test_make_must_cover.py skill/repo-task-atk-test/tests/test_assets_example.py -q`
Expected: 全绿。

---

## Task 5: `seal_bundle.py`

**Files:**
- Create: `scripts/seal_bundle.py`（替换 Task 1 的占位）
- Create: `tests/test_seal_bundle.py`
- Create: `references/handoff.md`

**Interfaces:**
- Produces: `evidence/bundle.json`，schema 见本文 §4.2；退出码 0/2/3。
- Consumes: `probe_progress.survey()` 的存在性推断；`interface.json`、`env.json`、
  `signature_alignment.json`、各分面 `coverage_*.json` / `validate_*.json` / `frozen_inputs_*.json`。

- [ ] **Step 1: 先写 reference**

`references/handoff.md` 写清交接包目录、`bundle.json` 每个字段的来源、排除名单与理由、
两道门各自的判据与退出码。≥100 行要带 `## 目录`。

- [ ] **Step 2: 写失败测试**

用 `tests/test_progress_probe.py` 同款的临时工作目录 fixture：S2 产物齐全 → 退出码 0，
`files` 覆盖全部文件且不含排除名单；缺 `must_cover.json` → 退出码 2 且报错列出它；
`coverage_*.json` 结论为未通过 → 退出码 2；没有 `evidence/` → 退出码 3；
封两次内容相同（`sealed_at` 除外）。

- [ ] **Step 3: 实现**

复用 `probe_progress.survey()` 判齐全；sha256 用 `_case_utils` 既有函数；
`phase_supported`、`atk`、`python` 从 `env.json` 取；`facets` 从各分面的 `coverage_*.json`
反推。脚本自带 `--help`，调用 `_stage_card.announce(__file__)`。

- [ ] **Step 4: 跑测试并派生视图**

Run: `PYTHONPATH=third_party/ATK python3 -m pytest skill/repo-task-atk-test/tests/test_seal_bundle.py skill/repo-task-atk-test/tests/test_contracts.py -q && python3 skill/repo-task-atk-test/scripts/render_views.py --check`
Expected: 全绿，退出码 0。

---

## Task 6: `check_bundle.py` 与接口一致性门

**Files:**
- Create: `scripts/check_bundle.py`（替换 Task 1 的占位）
- Create: `tests/test_check_bundle.py`
- Modify: `references/handoff.md`

**Interfaces:**
- Produces: `evidence/bundle_intake.json`：`integrity / task_doc / atk_version / interface` 四项
  各带 `passed` 与 `evidence`；退出码 0/2/3。
- Consumes: `bundle.json`；验收侧 `env.json`；`--task-doc`；可选 `--header` + `--aclnn-name`
  经 `align_signatures.py` 头文件模式产出的 `signature_alignment_pr.json`。

- [ ] **Step 1: 写失败测试**

以 Task 5 封好的目录为 fixture：原样 → 0；任一文件改一个字节 → 2 且 `integrity.evidence`
点名该文件；多放一个文件 → 2；换一份任务书 → 2；`env.json` 的 ATK 版本不同 → 2；
没有 `bundle.json` → 3；给 `--header` 且参数多一个 → 2 且 `interface.evidence` 列出差异；
给 `--header` 且一致 → 0；合法 rewire（Task 7）后 → 0。

- [ ] **Step 2: 实现**

逐项判据独立，全部跑完再汇总退出码，`bundle_intake.json` 里每项都有结论，不因第一项失败
就跳过后面——验收侧需要一次看到全部问题。接口比对取参数名集合、相对顺序、C 类型三件，
与 `check_signature_contract.py` 的三判据同一口径。

- [ ] **Step 3: 跑测试**

Run: `PYTHONPATH=third_party/ATK python3 -m pytest skill/repo-task-atk-test/tests/test_check_bundle.py skill/repo-task-atk-test/tests/test_help_matches_gate.py -q`
Expected: 全绿。

---

## Task 7: `rewire_adapter.py` 同步清单

**Files:**
- Modify: `scripts/rewire_adapter.py`
- Modify: `tests/test_rewire_adapter.py`

**Interfaces:**
- Produces: 改写成功后 `bundle.json.files[<case_json>]` 更新；`bundle.json.rewires[]` 追加
  `{"file", "before", "after", "fields", "at"}`。
- Consumes: `bundle.json`（不存在时保持今天的行为，不报错）。

- [ ] **Step 1: 写失败测试**

有 `bundle.json` 时合法改写后 `check_bundle.py` 仍 0 且 `rewires` 长度为 1；
语义变化的改写仍退出码 2 且清单不动。

- [ ] **Step 2: 实现并跑测试**

Run: `PYTHONPATH=third_party/ATK python3 -m pytest skill/repo-task-atk-test/tests/test_rewire_adapter.py skill/repo-task-atk-test/tests/test_check_bundle.py -q`
Expected: 全绿。

---

## Task 8: 卡与进度按 skill 过滤

**Files:**
- Modify: `scripts/mark_step.py`、`scripts/probe_progress.py`
- Modify: `tests/test_mark_step.py`、`tests/test_progress_probe.py`、`tests/test_stage_card.py`

**Interfaces:**
- Produces: `mark_step.py 0 接收与环境` 打 S0 卡；`probe_progress.py` 有 `bundle.json` 无
  `bundle_intake.json` 时报当前阶段 S0，并只列该 skill 的阶段。
- Consumes: `_contracts.stages_of`。

- [ ] **Step 1: 写失败测试**

S0 卡含五道门名与三条 forbidden；`probe_progress` 对「生成侧封完印的目录」报
「case-gen 已完成，验收侧当前 S0」；对「验收副本里有 `bundle_intake.json` 无 S3 产物」
报 S3。

- [ ] **Step 2: 实现并跑测试**

Run: `PYTHONPATH=third_party/ATK python3 -m pytest skill/repo-task-atk-test/tests/test_mark_step.py skill/repo-task-atk-test/tests/test_progress_probe.py skill/repo-task-atk-test/tests/test_stage_card.py -q`
Expected: 全绿。

---

## Task 9: 三份 SKILL.md

**Files:**
- Modify: `SKILL.md`（路由页）
- Create: `case-gen/SKILL.md`、`acceptance/SKILL.md`
- Create: `tests/test_skill_routing.py`
- Modify: `tests/test_document_style.py`、`tests/test_gate_premises.py`

**Interfaces:**
- Produces: 三份 frontmatter：父 `name: repo-task-atk-test`，子
  `name: repo-task-case-gen` / `repo-task-atk-accept`；description 按本文 §3.3 草案。
- Consumes: 现有 `SKILL.md` 正文按阶段归属分配。

- [ ] **Step 1: 写失败测试**

`test_skill_routing.py`：三份 frontmatter 合法（`name` 小写连字符 ≤64、description 非空
≤1024、第三人称）；路由页不含任何 `mark_step.py` 调用与阶段表；两个子页各含自己的阶段表
且不含对方的阶段号；子页 description 互含对方的名字用于引流。
`test_document_style.py`：`SKILL_FILES = [根, case-gen, acceptance]`；`PROSE_BASELINE` 键改
相对路径；`test_main_skill_stays_compact` 改为路由页 ≤80、子页各 ≤300；扫描脚本文档入口的
两条测试改扫三份。`test_gate_premises.test_skill_entry_states_the_right_count_and_stage`
改指子页。

- [ ] **Step 2: 写路由页**

保留「工作边界」里两侧通用的部分、「上下文被压缩后」、「参考和脚本规则」的通用条目；
新增「按输入选子流程」一节：只有任务书 → 读 `case-gen/SKILL.md`；有 `atk-case-<op>` 目录
加工程目录 → 读 `acceptance/SKILL.md`；两者都没有 → 停下问。

- [ ] **Step 3: 写两个子页**

现有正文按阶段归属切分，不改措辞；生成侧加「封印」一节、环境前提一节（Phase A 即可）；
验收侧加「S0 接收与环境」一节，其余 S3–S5 原文。两页各自的进度呈现只列自己的阶段。

- [ ] **Step 4: 重定 prose 基线并跑测试**

先跑一次看每个文件的实际违规数，按「只减不增、总和 ≤17」写进 `PROSE_BASELINE`。

Run: `PYTHONPATH=third_party/ATK python3 -m pytest skill/repo-task-atk-test/tests/ -q`
Expected: failed 数不高于基线。

---

## Task 10: 开发文档、使用者文档与下游 manifest

**Files:**
- Modify: `CLAUDE.md`、`README.md`
- Modify: `docs/skills/repo-task-atk-test/design.md`、`quickstart.md`
- Modify: `docs/development/architecture-log.md`（加一条演进记录）
- 下游 OpRunway：`plugin/.claude-plugin/plugin.json`、`plugin/.claude-plugin/upstream.json` 旁注

- [ ] **Step 1: skill 侧 `CLAUDE.md`**

目录结构画进两个子目录；「验收流程」一节改为「阶段归属见骨架 `stages[*].skill`」；
红线表指向 spec 本文 §2.6。

- [ ] **Step 2: 使用者文档**

`design.md` 加「两种入口」；`quickstart.md` 给两段第一条消息：只有任务书时写什么、
有交接包时写什么，并说明生成侧不需要 NPU。`README.md` 的 skill 表加一行。

- [ ] **Step 3: 下游 manifest**

`plugin.json` 的 `skills` 加 `./skill/repo-task-atk-test/case-gen` 与
`./skill/repo-task-atk-test/acceptance`；`claude plugin validate` 通过；无头会话列出三个名字。

- [ ] **Step 4: 跑全量回归**

Run: `PYTHONPATH=third_party/ATK python3 -m pytest skill/ tests/ -q`
Expected: failed ≤22，且 22 条集合与基线一致。

---

## Task 11: 触发评测与真机验证（需用户授权真机）

**Files:**
- Create（不进仓）: skill-creator 工作区的触发查询集与结果

- [ ] **Step 1: 触发评测**

按 skill-creator 流程为三条 description 各写 20 条查询（应触发 8–10、不应触发 8–10，
近似误触发为主：doc-write 的任务书撰写、只跑一次功能测试、拿到交接包却想改用例），
跑触发率，取最优回填 frontmatter。

- [ ] **Step 2: 无 NPU 机器跑生成侧**

给 golden 任务书，跑到 `seal_bundle.py` 退出码 0。这一步对应本文 §10 第 1 条。

- [ ] **Step 3: 真机跑验收侧**

把交接包拷到真机，给 PR 源码与同一任务书，跑到 S5。再换一个 PR 跑第二次，核 S2 产物
sha256 不变。对应本文 §10 第 2、3 条。真机操作须用户授权，走 `isolated-acceptance`
的无头通路；该 skill 按两个子 skill 重排是独立的后续工作。
