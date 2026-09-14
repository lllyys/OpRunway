# 验收 Skill 拆分设计：用例生成与测试验收解耦

**日期：** 2026-08-20
**状态：** 已评审，待实施
**产物：** `skill/repo-task-atk-test/` 内新增 `case-gen/SKILL.md` 与 `acceptance/SKILL.md`，
父 `SKILL.md` 改为路由页；新增 `seal_bundle.py`、`check_bundle.py`；骨架加 S0 与阶段归属

---

## 0. 一句话

把五阶段验收切成两个可独立运行的子 skill——生成侧只吃任务书、产出封过印的用例交接包，
验收侧吃交接包加 PR 源码加任务书、做构建跑测与裁决——两者之间唯一的接口是交接包目录
和它的 `bundle.json` 清单。

---

**记号约定：** 形如 `§2.3`、`§2.4` 的编号指**任务书模板的章节**；指本文自身章节时写
「本文 §x」。「生成侧」指 `case-gen`，「验收侧」指 `acceptance`。

---

## 1. 问题与目标

### 1.1 现状：用例与 PR 绑死

当前 S2 的签名对齐从待验收算子工程的头文件读（`align_signatures.py` 的 `--env` 必填，
`require_project_source` 强制出处在 `env.json` 的 `operator_project.path` 之下），dtype 轴
也要从工程里的 README 或头文件取。结果是用例生成必须等 PR 到手，而且一份用例只对应
那一个 PR——同一任务书来第二个 PR 就得从 S1 重走一遍。

这与「任务书是验收约束的唯一来源」这条既有原则不一致：任务书 §2.3 已经写了完整签名，
§2.4 已经逐参数写了 dtype，doc-write skill 的门禁还强制两者一致。从 PR 抄签名不是在验收
PR，是在让 PR 自己定考卷的题面。

### 1.2 事实：生成侧不需要真机

逐脚本核过依赖：

| 脚本 | 需要什么 | 不需要什么 |
| --- | --- | --- |
| `derive_interface.py` | 纯 Python | — |
| `align_signatures.py` | `torch`、装机 ATK 的两张类型表 | NPU、已构建的 `.so`（`torch_npu` 为可选导入） |
| `make_must_cover.py`、`make_yaml.py` | `torch`（后者缺失时降级为「不可判定」） | NPU |
| `atk case`、`validate_cases.py`、`freeze_inputs.py`（默认模式） | 装机 ATK、`torch`，CPU 单节点 | NPU、CANN |
| `probe_env.py` | `npu-smi`、CANN | — |

ATK 的非测试代码没有一处 `import torch_npu`；`probe_env.py` 自己就把能力分成
「仅 Phase A」与「Phase A + B」，`freeze_inputs.py` 的 docstring 写明默认模式在 Phase A 用
CPU 单节点即可。真正碰 NPU 的是 `probe_env.py` 的设备探测、S3 以后的全部量具、以及
`capture_reference.py` 和 `freeze_inputs.py --golden` 这两条 `cann_builtin` 专属路径。

所以生成侧的环境前提可以降到 Python + ATK + torch（CPU），这是拆分能成立的物理基础。

### 1.3 目标

- 一份任务书生成一次交接包，验收任意多个 PR
- 生成侧在没有 NPU 的机器上跑完
- 冻结纪律从「规则禁止回 S2」变成「验收侧拿到的是封过印的输入」
- 新增一道现在测不出的判据：PR 实现的接口是不是任务书定的接口

### 1.4 不做什么

- 不拆成两个平行目录、不复制任何脚本或 reference（理由见本文 §2.1）
- 不改 ATK、不改上游 `third_party/`
- 不重编阶段号（理由见本文 §2.5）
- 不在本轮改 `isolated-acceptance` 的主机侧编排，它按两个子 skill 重排是后续工作

---

## 2. 架构决策

### 2.1 嵌套布局，父 SKILL.md 做路由

```
skill/repo-task-atk-test/
├── SKILL.md                 # 路由页：按手上的输入判断进哪个子流程
├── case-gen/SKILL.md        # 生成侧：S1 任务书解读 → S2 用例生成 → 封印
├── acceptance/SKILL.md      # 验收侧：S0 接收与环境 → S3 → S4 → S5
├── CLAUDE.md                # 开发规则，仍只有父目录这一份
├── scripts/ references/ assets/ tests/   # 各一份，一个文件都不挪
```

选嵌套而不是平行两目录，是因为两侧共用的不是两个事实文件，而是整层骨架：
`_contracts.py`、`_stage_card.py`、`_case_utils.py`、`_policy.py`、`mark_step.py`、
`probe_progress.py`、`gate_lookup.py`、`run_atk_task.py`、`probe_env.py`、
`derive_interface.py`、`freeze_inputs.py`，外加 `artifact-contracts.json`、
`acceptance-policy.json`、`glossary.md`、`atk-cli.md`、`execution.md`、`builtin-baseline.md`。
平行目录意味着十几份副本和一张随之膨胀的 sha256 同步表；嵌套布局零副本，上游的
patch 照常落在原路径上。

**放弃的替代方案：** 平行两目录加「各存副本 + `test_shared_facts_sync`」。它是 doc-write
skill 的先例，对两个文件合适，对十几个文件是长期税。换来的「验收侧物理上没有生成量具」
在交接包封印机制之下边际价值很低——冻结靠的是 sha256，不是靠拿不到工具。

### 2.2 两条安装通路，行为不同

实测（2026-08-20，本机 Claude Code）：

| 通路 | 子 skill 能否独立注册 |
| --- | --- |
| plugin（`--plugin-dir` / marketplace，manifest `skills` 数组列出父与两个子目录） | 能，三个各自注册 |
| 目录扫描（`cp -r` / 软链到 `~/.claude/skills/`） | 不能，只扫一层，只见父 |

官方文档对第一种没有任何记载，它是当前版本的行为，不是承诺。所以设计上把**父路由当保底**：
父 `SKILL.md` 的 description 覆盖两种触发场景，正文只做一件事——判断输入属于哪种，
指到对应子页。最坏情况退化成「一个 skill、两份流程页、按输入读一份」，零副本、单份骨架、
上游路径不动三项收益一个不丢。

子页 frontmatter 的 `name` 按 plugin 规则决定命令名（文档明写：plugin skill 里 `name`
覆盖目录名），定为 `repo-task-case-gen` 与 `repo-task-atk-accept`；目录名用短的。

### 2.3 生成侧只读任务书

签名从 §2.3 接口定义的代码块解析，dtype 轴从 §2.4 参数表的「dtype类型」列取。
两者都有现成的解析基础：doc-write skill 的 `_signature.py` 已经在做「从接口定义代码块抽
参数名」，`golden-task-doc.md` 给出了 §2.3 与 §2.4 的标准形态。

这意味着生成侧的输入只有任务书一份，交接包因此与 PR 无关。代价是任务书写糙了
（§2.3 解析不出参数、§2.4 没有 dtype 列）生成侧就停在待确认——这是把质量压力推回
doc-write，方向正确，但存量任务书可能过不了，要在报错里说清缺哪一节。

**放弃的替代方案：** 任务书为主、工程路径可选。兼容存量任务书，但交接包是否与 PR 无关
取决于当次怎么调用，契约就不稳了。

### 2.4 接口一致性是验收侧的第一道门

验收侧拿到 PR 后，用 `align_signatures.py` 的头文件模式读 PR 头文件，与交接包里任务书派生的
`signature_alignment.json` 逐参数比对名称、顺序、C 类型。不一致时停在 S0，报告写明
「PR 接口与任务书 §2.3 不一致」，并列出差异。

这道门现在的流程测不出来——签名本来就是从 PR 抄的，抄什么过什么。任务书是契约，
PR 偏离契约是 PR 的问题，不是用例的问题，所以这是 DUT 侧的阻塞，不回生成侧。

### 2.5 骨架一份，阶段号保留

`artifact-contracts.json` 仍是一份。`stages` 每项加 `skill` 字段（`case-gen` 或 `acceptance`），
新增 `S0`。阶段号 S1–S5 不重编：文档里「阻塞·未验收 @S3」这类引用全部保留，上游 diff
最小。验收侧的阶段序列是 S0 → S3 → S4 → S5，编号不连续但语义诚实——用例是别处的
S1–S2 生成的。

`_stage_card.stage_of()` 从骨架反查量具归属，跨阶段返回 `None`。`probe_env.py` 在 S1 与
S3 各跑一次，本来就跨阶段；S0 进来后它跨 S0/S1/S3，行为不变。`freeze_inputs.py` 默认模式归
S2、`--golden` 归 S3，骨架里 `producer` 只登记默认模式，不变。

### 2.6 四条红线在两侧的形态

| 红线 | 生成侧 | 验收侧 |
| --- | --- | --- |
| 不拿实现当验收依据 | 更强：连工程目录都不读，只读任务书 | 不变：公开接口面可读，实现不可读 |
| ATK 是黑盒 | 不变 | 不变 |
| 冻结纪律 | 封印即出口，封印后不再动任何产物 | 交接包只读；`rewire_adapter.py` 是唯一例外，改完必须更新 `bundle.json` |
| Agent 声明不可信 | `seal_bundle.py` 核产物齐全再封 | `check_bundle.py` 重算 sha256，不信清单自述 |

---

## 3. 两个子 skill 的契约

### 3.1 生成侧 `repo-task-case-gen`

| 项 | 内容 |
| --- | --- |
| 输入 | 任务书（路径）；用户已知的接口模式、基线、随机策略确认 |
| 环境 | Python + 装机 ATK + torch（CPU）；`probe_env.py` 报「仅 Phase A」即可 |
| 阶段 | S1 任务书解读 → S2 用例生成 → 封印 |
| 出口 | `seal_bundle.py` 退出码 0，`evidence/bundle.json` 落盘 |
| 产物 | `<工作区>/atk-case-<op>/`，即交接包（本文 §4） |
| 不做 | 读任何工程目录；构建；跑 NPU；写验收结论 |

S1 的四件产物里 `env.json`、`env.sh` 仍由 `probe_env.py` 产出，记录的是生成机的指纹，
随交接包走，供验收侧核 ATK 版本。`--op-repo` 不给，`operator_project` 为空。

### 3.2 验收侧 `repo-task-atk-accept`

| 项 | 内容 |
| --- | --- |
| 输入 | 交接包目录；待验收算子工程（PR 归约后的本地目录）；任务书；device / CANN / SoC |
| 环境 | NPU 真机，CANN 已装，`probe_env.py` 报「Phase A + B」 |
| 阶段 | S0 接收与环境 → S3 编译安装部署 → S4 精度性能测试 → S5 输出测试结果 |
| 入口 | 把交接包复制为 `<工作区>/atk-verify-<op>/`，`check_bundle.py` 退出码 0 |
| 产物 | 与现在的 S3–S5 相同：绑定报告、冒烟日志、results、verdict、报告、复现包 |
| 不做 | 重新生成用例；改交接包里任何 S2 产物（接线改写除外） |

复制而不是原地工作，是为了让一个交接包服务多个 PR：交接包目录永远干净，每次验收在
自己的副本里追加 `evidence/` 与 `conclusion/`。

### 3.3 description 草案

三条都要第三人称、同时写「做什么」和「何时用」，并且彼此排斥。

- **父：** 「ATK 社区算子验收的入口：只有任务书时生成用例交接包，有交接包加 PR 源码时
  做构建跑测与精度性能裁决。凡提到用 ATK 验收算子、为算子任务书生成测试用例、核对提交
  能否放行、或拿到 `atk-case-<op>` 交接包要跑测，都先进本 skill，再按手上的输入进入对应
  子流程。」
- **case-gen：** 「根据社区算子任务书生成 ATK 用例交接包（YAML、约束器、用例 JSON、
  冻结输入与封印清单），不需要 NPU 与 CANN。凡是只有任务书、要为算子准备测试用例、
  要在验收前把用例冻结下来、或要为多个 PR 复用同一套用例时，用本 skill。」
- **acceptance：** 「拿封好的 ATK 用例交接包、待验收算子 PR 或源码与任务书，在 NPU 真机上
  构建安装、冒烟、跑精度性能并裁决能否放行。凡是手里有 `atk-case-<op>` 交接包要跑测，
  或要判断一次提交是否满足任务书的精度性能要求，用本 skill；没有交接包先用
  repo-task-case-gen。」

最终措辞由 skill-creator 的触发评测定（本文 §8）。

---

## 4. 交接包

### 4.1 目录

就是今天 S2 结束时的工作目录，一个字段不改，只多一份清单：

```
atk-case-<op>/
├── evidence/
│   ├── constraints.md interface.json env.json env.sh
│   ├── signature_alignment.json signature_contract.json
│   ├── <各量具报告>.json              ← 文件名由 -o 决定，封印按内容配对
│   ├── timeline.jsonl
│   └── bundle.json                      ← 封印清单（新）
├── <op>_decl.json <op>_materialize.py must_cover.json *_materialized.json
├── <op>.yaml <op>_constraint.py [function_<op>.py]
├── result/<yaml>/json/all_<yaml>.json    ← atk case 产出的用例 JSON
└── frozen_<分面>/
```

路径不改，所以 `verdict.py -c`、`make_repro.py -y/-p/-j/--must-cover`、
`select_perf_cases.py`、`save_failed_cases.py` 这些跨边界读 S2 产物的脚本默认路径全部不动。
覆盖、校验、冻结与适配器报告没有固定文件名，封印按用例集摘要配对：
`check_coverage.py` 写 `case_file_sha256`，`freeze_inputs.py` 写 `case_json_sha256`。

### 4.2 `bundle.json`

```json
{
  "schema_version": 1,
  "operator": "<op>",
  "sealed_at": "<ISO 8601>",
  "task_doc": {"name": "<文件名>", "sha256": "<任务书全文 sha256>"},
  "generator": {"skill": "repo-task-case-gen", "phase_supported": "仅 Phase A"},
  "atk": {"version": "<env.json fingerprint.atk>", "python": "<selected_python>"},
  "interface": {"interface_mode": "aclnn", "candidate_symbol": "aclnnXxx",
                "baseline_api": "torch.xxx",
                "baseline_kind": "torch"},
  "facets": [
    {"name": "<接口名>", "yaml": "<op>.yaml", "case_json": "result/.../all_<op>.json",
     "must_cover": "must_cover.json", "frozen_dir": "frozen_<接口名>",
     "coverage_report": "evidence/<覆盖报告>.json",
     "freeze_report": "evidence/<冻结报告>.json",
     "validate_report": "evidence/<校验报告>.json",
     "adapter_report": "evidence/<适配器报告>.json"}
  ],
  "files": {"<相对路径>": "<sha256>"},
  "excluded": ["evidence/timeline.jsonl", "evidence/repro.sh", "evidence/bundle.json",
               "evidence/env.json", "evidence/env.sh"],
  "ignored_dirs": ["__pycache__"]
}
```

`files` 覆盖交接包内除五个文件与 `__pycache__` 目录外的全部普通文件。
`timeline.jsonl`、`repro.sh` 会继续追加，`bundle.json` 不能包含自己的摘要；
`env.json`、`env.sh` 是验收侧 S0 会重新探测并覆盖的机器指纹。
ATK 导入约束器时会在 `__pycache__` 生成含时间戳的 `.pyc`，因此按目录名忽略。
排除项与忽略目录写进清单，验收侧不另写一份可漂移名单。

### 4.3 `seal_bundle.py`（生成侧出口）

1. 用 `probe_progress.py` 同一套存在性推断核 S1、S2 产物齐全，条件产物按
   `interface.json` 与 `signature_alignment.json` 判定
2. 核 S2 四道出口门的证据文件都在且结论为通过
3. 逐文件算 sha256，写 `bundle.json`
4. 退出码：0 封印成功；2 产物不齐或门禁未过（列出缺什么）；3 工作目录结构不对

封印之后再跑任何 S2 量具都应视为违规，`seal_bundle.py` 本身不做这层拦截——
拦截在验收侧的 `check_bundle.py`，它会发现 sha256 对不上。

### 4.4 `check_bundle.py`（验收侧入口）

1. `bundle.json` 存在且 `schema_version` 认识
2. 重算全部文件 sha256，与 `files` 逐项比对；清单内文件缺少或摘要不同判红
3. 交接包的 `task_doc.sha256` 必须等于验收侧拿到的任务书的 sha256
4. 验收机 `env.json` 的 ATK 版本必须等于 `atk.version`——能力矩阵绑版本，版本不同则
   用例的可表达性判定不再成立
5. `--header` 给出 PR 头文件时，调用 `align_signatures.py` 头文件模式，与交接包里的
   `signature_alignment.json` 比参数名集合、相对顺序、C 类型
6. 产出 `evidence/bundle_intake.json`：每一项的结论与证据
7. 退出码：0 通过；2 任一判据不满足；3 清单缺失或头文件解析不出

第 5 项的失败在报告里单列为「PR 接口与任务书不一致」，不与交接包损坏混写。

新增文件只在三类位置判为篡改：`frozen_*/` 之下、`result/` 之下，以及
交接包根目录的 `*.yaml`、`*_decl.json`、`*_constraint.py`、`*_materialize.py`、
`must_cover*.json`、`*_materialized.json`、`function_*.py`。验收侧合法追加的
`evidence/*.json`、`conclusion/`、ATK `output/` 与日志不属于篡改。

### 4.5 `rewire_adapter.py` 与清单

接线改写是冻结后唯一允许动 S2 产物的通道，它已经记录改写前后的 `case_json_sha256`。
改动只有一处：改写成功后同步更新 `bundle.json` 里该文件的摘要，并在清单里追加
`rewires` 一项记录前后值。没有这一步，S5 前的复核会把合法改写当成篡改。

---

## 5. 量具改动

| 脚本 | 改什么 | 为什么 |
| --- | --- | --- |
| `align_signatures.py` | 新增 `--signature-source taskdoc --task-doc <md>` 模式：从 §2.3 代码块解析 `GetWorkspaceSize` 声明；该模式下 `--env` 不再必填，`require_project_source` 不介入 | 生成侧没有工程目录 |
| `make_must_cover.py` | `--dtype-source` 接受任务书：从 §2.4「dtype类型」列取 dtype 面；核对方式从「在工程树内」改为「sha256 等于 `bundle.json`/`interface.json` 记录的任务书」 | 同上 |
| `derive_interface.py` | `interface.json` 加 `task_doc.sha256` | 生成侧后续量具核对 dtype 来源要用，`bundle.json` 也从这里取 |
| `seal_bundle.py` | 新增 | 本文 §4.3 |
| `check_bundle.py` | 新增 | 本文 §4.4 |
| `rewire_adapter.py` | 改写后更新 `bundle.json` | 本文 §4.5 |
| `_contracts.py` | `stages` 加 `skill` 字段校验；新增 `S0_BLOCKING_SCRIPTS` | 骨架是唯一真相 |
| `mark_step.py` / `probe_progress.py` | 按 `skill` 过滤：生成侧只渲染 S1、S2 的卡，验收侧只渲染 S0、S3–S5 | 两边各看各的地图 |

任务书解析不另写一套：把 doc-write 的 `_signature.py` 与 `_taskdoc_parser.py` 中用到的部分
拷为本 skill 的 `_taskdoc.py`，纳入 `test_shared_facts_sync` 的同步名单。这是两个事实文件
之外新增的第三对副本，仍是 doc-write 先例的用法，不是本文 §2.1 放弃的那种规模。

---

## 6. 骨架与门禁清单

`stages` 新增：

```json
"S0": {
  "name": "接收与环境",
  "skill": "acceptance",
  "core": "核对交接包没被动过、对得上这份任务书，并拿到真机环境指纹",
  "lookup_topics": ["backends"],
  "gates": ["交接包完整", "任务书一致", "ATK 版本一致", "接口一致", "环境指纹可用"],
  "forbidden": ["重新生成用例", "改交接包里任何 S2 产物", "拿不到卡号就自己选卡"]
}
```

S1、S2 标 `"skill": "case-gen"`，S3–S5 标 `"skill": "acceptance"`。

`gate_inventory` 新增四道 S0 门，全部归 `check_bundle.py`：`check_bundle.integrity`、
`check_bundle.task_doc`、`check_bundle.atk_version`、`check_bundle.interface_conformance`
（最后一道有前提：`--header` 已给；`when_broken: not_applicable`，`evidence_key` 指向
`bundle_intake.json` 的对应项）。生成侧 19 道 S2 门不动，另加 `seal_bundle.completeness`。

`artifacts` 新增 `evidence/bundle.json`（S2，`producer: seal_bundle.py`，
`consumed_by: check_bundle.py, rewire_adapter.py`）与 `evidence/bundle_intake.json`
（S0，`producer: check_bundle.py`，`consumed_by: S5 证据链`）。

---

## 7. 测试影响

结构不变量测试里会红的条目与处置，全部已定位：

| 测试 | 现状 | 处置 |
| --- | --- | --- |
| `test_gate_premises.test_skill_entry_states_the_right_count_and_stage` | 硬编码阶段集合 `{"S2"}` 与字面量「有 19 道」 | 改为按 `skill` 分组推导，分别核对 `case-gen/SKILL.md` 与 `acceptance/SKILL.md` 里的计数 |
| `test_gate_premises.test_every_s2_blocking_script_is_inventoried` | 只核 S2 | 同样核 `S0_BLOCKING_SCRIPTS` |
| `test_document_style.PROSE_BASELINE` | 按文件名计数，只认根 `SKILL.md` | 键改为相对路径；三份 SKILL.md 各自重定基线，总量只减不增 |
| `test_document_style.test_main_skill_stays_compact` | 只测根 `SKILL.md` ≤375 行 | 路由页 ≤80 行；两个子页各 ≤300 行 |
| `test_document_style.test_every_script_has_a_documentation_route` | 扫根 `SKILL.md` + references | 扫三份 SKILL.md |
| `test_document_style.test_documented_script_names_exist` | 同上 | 同上 |
| `test_contracts.CardCoverageTest` | 卡产物 == 骨架产物 | 按 `skill` 过滤后分别核 |
| `test_contracts.SpineStructureTest` | 每阶段五个卡槽 | S0 同样五槽；`skill` 字段取值受限 |
| `test_progress_probe` | 从盘上产物反推阶段 | 加 S0 用例：有 `bundle.json` 无 `bundle_intake.json` → 当前 S0 |
| `test_env_sh.test_vendor_and_custom_opp_are_absent_until_s3` | 锁 `probe_env` 两段式 | 不变；生成侧只跑第一段 |
| doc-write `test_shared_facts_sync` | 两个文件 | 加 `_taskdoc.py` |

新增测试：`test_seal_bundle.py`、`test_check_bundle.py`（含篡改一个字节判红、多一个文件判红、
任务书换一份判红、ATK 版本不同判红、头文件多一个参数判红、合法 rewire 后仍通过）、
`test_taskdoc_source.py`（§2.3 解析、§2.4 dtype 列、缺节时的报错文案）、
`test_skill_routing.py`（三份 SKILL.md 的 frontmatter 合法、description 互斥关键词、
路由页不含任何阶段正文）。

---

## 8. 文档与分发

- 父 `SKILL.md`：只剩路由逻辑、工作边界里与两侧都相关的部分、以及「上下文被压缩后」那节
  （它指到 `probe_progress.py`，两侧通用）。现有正文按阶段归属分到两个子页。
- `CLAUDE.md`：目录结构画进两个子目录；「验收流程」一节说明阶段归属；红线表引用本文 §2.6。
- `docs/skills/repo-task-atk-test/design.md` 与 `quickstart.md`：加「两种入口」一节，
  quickstart 给两段第一条消息的写法（只有任务书 / 有交接包）。
- `README.md`：skill 表加一行说明两个子流程与各自的 NPU 需求。
- 下游（本仓 OpRunway，不在上游 PR 内）：`plugin/.claude-plugin/plugin.json` 的 `skills`
  数组加两个子目录；`upstream.json` 旁记一句子 skill 独立注册依赖未文档化行为，父路由保底。
- 触发评测：按 skill-creator 的流程为三条 description 各写 20 条应触发/不应触发的查询，
  跑触发率，取最优措辞回填。

---

## 实施勘误（2026-08-20 Task 5）

- 分面报告改为由 `-o` 决定文件名并按用例集摘要配对，因为四个量具都不会生成分面后缀。
- 排除名单加入机器指纹文件并忽略 `__pycache__`，因为验收侧会重建这些可变内容。
- 新增文件判红改为限定路径和根目录类型，因为验收侧必须追加证据、输出与日志。

## 9. 风险

- **子 skill 独立注册是未文档化行为。** 父路由保底已覆盖；上游若不接受 manifest 写法，
  只需去掉两条数组项，功能不受影响。
- **存量任务书质量。** §2.3 解析失败或 §2.4 缺 dtype 列时生成侧停在待确认。报错必须指出
  缺哪一节、该用 doc-write 补什么，不能只说「解析失败」。
- **`isolated-acceptance` 需要重排。** 它现在一条提示词跑完五段；拆分后要么一个会话内
  先后触发两个子 skill，要么两台机器两个会话。这是后续工作，不阻塞本设计。
- **上游同步。** 骨架、`align_signatures.py`、`make_must_cover.py`、三份 SKILL.md 是上游也会
  改的热点，冲突概率高于平均；缓解办法只有尽快提 PR 回上游。

---

## 10. 验收标准

本设计算落地，需同时满足：

1. 在没有 NPU、没有 CANN 的机器上，只给任务书，生成侧跑到 `seal_bundle.py` 退出码 0
2. 把交接包拷到真机，给 PR 源码与同一份任务书，验收侧从 `check_bundle.py` 跑到 S5 出报告
3. 对同一交接包换一个 PR 跑第二次，S2 产物 sha256 全程不变
4. 交接包任一文件改一个字节，`check_bundle.py` 退出码 2
5. PR 头文件比任务书 §2.3 多或少一个参数，S0 停下并在报告里点名
6. 全量回归：`PYTHONPATH=third_party/ATK python3 -m pytest skill/ tests/ -q` 的 failed 数
   不高于当前基线（22 条，全部可归因于本机缺 torch 或既有行文红）
