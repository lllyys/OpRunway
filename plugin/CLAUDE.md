# CLAUDE.md — 社区算子 Skill 仓

每次会话自动全量加载。**这里只放所有 skill 共用的开发约束和最简背景**，
任何只服务一个 skill 的东西都下沉到那个 skill 的 `CLAUDE.md`，
任何只在特定场景用到的东西都下沉到 `docs/development/`。

写本仓任何 skill 文档前，形式规范已随下面这行导入常驻上下文，不需要主动去读：

@.claude/rules/skill-style.md
@.claude/rules/doc-style.md

两份分工：**形式**归 skill-style（`SKILL.md` 是路由器不是知识库，知识放
`references/`，指令具体到能直接执行），**句子**归 doc-style（标题用名词短语、
不出现拟人主语与第一人称、模糊词要换成具体判据）。

形式规范取自 `cannAgent/cannbot-skills`，文风不可机检，自检清单在它末尾；
行文规范取自 Kubernetes 与 Google 的文档风格指南，**可机检**：

```bash
python3 .claude/hooks/doc_style_lint.py          # 61 份文档，退 0 才算完
python3 .claude/hooks/doc_style_lint.py --fix    # 只自动修半角标点
```

改 `SKILL.md` 或 `references/` 时钩子会自动跑它并把命中行号注回上下文。

## 本仓是什么

**能力清单、分类与使用指导在 [README](README.md) 与 [docs/README.md](docs/README.md)，
这里不重复。** 下面只列有专属开发规则的 skill——没列到的，规则全在它自己的 `SKILL.md`。

| skill | 开发规则 |
| --- | --- |
| `repo-task-doc-write` | [CLAUDE.md](skill/repo-task-doc-write/CLAUDE.md) |
| `repo-task-case-gen` | [CLAUDE.md](skill/repo-task-case-gen/CLAUDE.md) |
| `repo-task-atk-accept` | [CLAUDE.md](skill/repo-task-atk-accept/CLAUDE.md) |
| `repo-task-blas-case-gen` | [CLAUDE.md](skill/repo-task-blas-case-gen/CLAUDE.md) |
| `repo-task-blas-accept` | [CLAUDE.md](skill/repo-task-blas-accept/CLAUDE.md) |
| `cann-page-inspect` | [CLAUDE.md](skill/cann-page-inspect/CLAUDE.md) |

**开发时按契约分，不按 README 的使用分类分。** README 的四个分类是给使用者看的
（他想干什么），下面的契约表是给开发者看的（改动会波及谁）。两套切法成员不同，
不要混用——例如 `cann-issue-report` 在 README 里独立成类，在契约上却和跑测共用同一处产物根。

**解耦靠契约，不靠禁令。** 一条链路上前后两个 skill 有真实业务串联时，`SKILL.md` 里可以
指名道姓地链到下一个 skill——藏着它只会让 agent 自己瞎猜下一步。但**串联必须落在文件契约上，
不能落在「你得先跑过谁」这句话上**：被依赖方读的是一份具体的产物文件，找不到它就打印
缺什么、去哪生成，而不是假定用户按顺序跑过。

| 链路 | 契约载体 | 契约文档 |
| --- | --- | --- |
| ATK | 用例包 | [case-package-contract.md](docs/development/case-package-contract.md) |
| ops-blas | 六件包 | [blas-case-package-contract.md](docs/development/blas-case-package-contract.md) |
| 算子仓闭环 | `CWD/cann-ops-report/` 下的 `run_state.json` / `issues/state.json` / `scan/_intermediate.json` | [ops-report-contract.md](docs/development/ops-report-contract.md) |

**开发态不解耦。** 同一条链路的两侧改动要同时动、两侧都在真机上复跑。

**目录级 `CLAUDE.md` 由 Claude Code 按需加载**，写进本文件的每一行则是每次会话
都要付的常驻成本。所以这里只留所有 skill 共用的东西，**具体到某个 skill 的
一律下沉**——脚本名、行号、字段名都不该出现在本文件里。

## 文件结构

```
repo-task-atk-test/
├── CLAUDE.md                    # 本文件：仓级共用约束
├── .claude/rules/skill-style.md # skill 形式规范，被本文件 @ 导入
├── README.md                    # 全部 skill 的门面与安装
├── skill/<name>/                # 一个 skill 一个目录，结构见 skill-style.md
├── docs/
│   ├── README.md                # 文档地图：三层按读者分
│   ├── install.md               # 装 skill 与依赖
│   ├── guide/                   # 使用指导，一个分类一份，不含命令
│   ├── development/             # 契约、真机排错事实、架构演进
│   └── superpowers/specs/       # 当时的设计方案，历史快照，不随代码更新
└── third_party/ATK/             # 只读 submodule
```

## 开发流程

**不做 TDD。** 改动的验证标准是**在真机上跑通**，不是单元测试通过——上一版 950 个
单测没拦住任何一个真机上暴露的缺陷，那些缺陷全在环境、路径耦合和 ATK 行为上。

**但判据是「这段代码碰不碰真机」，不是「哪个 skill」。** 七个 skill 有 `tests/`
（`repo-task-doc-write` 与六个 `cann-*`），测的全是纯逻辑：文档结构解析、平台推断、
状态机去重、报告渲染。碰真机那部分一条都没测，也不该补。新写脚本按这条分——
能用假目录加 `monkeypatch` 跑完的补测试，要连卡才知道对不对的去真机跑。

仓根 `python3 -m pytest` 收 `skill/` 下全部测试（`third_party/ATK` 的 ut 不在范围内），
改任何 skill 前后各跑一次。

1. 写或改 `SKILL.md` 前调用户级 skill `skill-creator`。改 `SKILL.md` 时
   `.claude/hooks/skill-md-gate.py` 会把这条连同三问注回上下文——**它只提醒不拦截，
   看见了就照做**。**分工：通用怎么写归它**
   （渐进式披露、写作模式、description 触发优化、带 skill / 不带 skill 的评测闭环），
   **本仓特有的归 `.claude/rules/skill-style.md`**（五个信息来源、阶段最小载入、
   载体法则）。没装 skill-creator 就跳过这条，不要为它停下
2. 改 `SKILL.md` 或 `references/` 前先回答：**没有它，零上下文 agent 会在哪一步卡住？**
   答不上来就不加
3. 改脚本后推到远程实跑，用真实算子验证，不靠本地 mock
4. 真机跑出来的事实一律带出处（源码路径行号，或标「实测」），按**改动时用不用得上**分流：
   不知道就会把脚本写错的进 skill `CLAUDE.md` 的事实表，某算子某轮的现象与量级进
   `docs/development/<skill>-troubleshooting.md`。ATK 两侧已经拆开，其余 skill 仍是一张表

**改完一份 `SKILL.md` 想知道是变好还是变坏，用 skill-creator 的评测**——同一批
prompt 各起一个带 skill 与不带 skill 的 subagent，比通过率与 token。字节数和散文
占比是代理指标，证明不了执行效果。纯文本判定的（如 `repo-task-doc-write`）本地就能评；
要 atk、NPU 或访问外部站点的，只能在对应环境里评。

ATK submodule 的钉法与同一性判据、上真机前要落实的三件事、跑测现场为什么不进仓，见
[docs/development/dev-environment.md](docs/development/dev-environment.md)，
**跑第一条远程命令前必读**。

**本仓不记任何一台机器的地址、账号、目录布局或 CANN 版本**——各 skill 的边界条款
本来就要求运行时探测或询问，开发文档不该反过来写死。要给自己的机器留备忘，
放 `docs/development/scratch/`（已 gitignore）。

## 新增一个 skill

四步，缺第 3 步 skill 装不上而且不报错：

1. 建 `skill/<kebab-case-name>/`。目录名必须与 `SKILL.md` frontmatter 的 `name`
   逐字相同，不同名 Claude Code 不认
2. 按 `.claude/rules/skill-style.md` 的骨架写 `SKILL.md`，写完过一遍它末尾的自检清单
3. 在 `.claude-plugin/plugin.json` 的 `skills` 数组加一行 `"./skill/<name>"`，
   并在 `README.md` 首表加一行。**漏了 plugin.json 这行，skill 静默不加载**，
   表现是 `/` 列表里看不到它，没有任何报错
4. 有专属红线、设计取舍或真机事实时才建 `skill/<name>/CLAUDE.md`；
   只有一两条规则就写进 `SKILL.md`，不建空壳

登记完对一下数，两个数不等就是漏了：

```bash
grep -c '"\./skill/' .claude-plugin/plugin.json   # plugin.json 登记数
ls -d skill/*/ | wc -l                            # 实际目录数
```

新 skill 的 `CLAUDE.md` **不要复制仓根这份的任何内容**。同一条规则在上下文里
出现两次，agent 要花预算判断两处是否冲突。它只写这个 skill 独有的部分。

## 当前状态

十三个 skill。真机跑通结果记在
[docs/development/architecture-log.md](docs/development/architecture-log.md)：
ATK 链路三个算子**流水线**跑通（全阶段有产物，**不是算子验收通过**——Median 与
IndexFillTensor 的结论都是不通过，缺陷已提上游 #28/#29/#30）；
ATK 链路的 sparse 剖面（`backend=npu`）归一、冻结、精度三步在真机上跑通，
**用桩顶替未实现的算子，验的是管道不是算子**，A2 待 ops-sparse 补 arch22 实现；
ops-blas 链路 sger 的精度与性能流水线通、完整验收待回填；
`cann-page-inspect` 在真实站点跑通；七个 `cann-*` 规范已对齐，真机复跑待做。

**最后更新：** 2026-09-09（自带件路提成独立主干：跑测侧不再为它伪造用例包，
`facts.kit.cases` 指哪份读哪份。**体检回到冒烟之前**（A2.4）——同日先按「先跑再修」
排过一轮，两份自带件各报 7 处、逐条落在真正需要的改动上、误报 0，据此改回；
体检替代不了冒烟，宿主环境假设那一类仍只有冒烟暴露。每份 stage JSON 记开始时刻
与阶段墙钟。载入预算有量具了
（`.claude/hooks/skill_budget_lint.py`，接进编辑门禁），上限按实测定在
12 KB / 32 KB；`repo-task-atk-accept` 的 `SKILL.md` 26.4 KB → 9.1 KB，
`repo-task-case-gen` 24 KB 仍超。这批已推远程机，**真机验证待做**。
同日补 A1.5 用例扩展，**两个轴一次问完**：条数走自带件自己的生成脚本，
dtype 走 `expand_kit_cases.py`（自带件的生成器把 dtype 写死在模块常量里，
加条数补不上），`facts.kit.dtype_map` 记 dtype 名到 attr 编码的对应。
**精度用例一条命令扩到 1000 条**：条数不够时量具自己调自带件的生成脚本，
倒推值与裁剪都是内部量。这一条返工了三轮，每轮错法相同——**把内部机制暴露成了
用户要读的流程**。据此把跑测侧的提问点全盘过了一遍，判据是**答案由既定策略定不定
得了**：用例扩展、性能批次、哪几条不跑满三处收成既定动作，只在 A1 那张范围表里
告知一次；A2 前那次范围确认也降级成播报——表里每一项都是既定推导，回答只能是「好」。
**整条链路只剩 A0 一个确认点**（两个路径）；其余停下来的地方都不是提问，
是 skill 定不了的输入（注册模块名、采样口径）或致命错误（装错算子目录）。
已推远程机并真机验过：扩到 400 与 1000 条后经现场修复脚本处理都过装机 CaseConfig，
体检报的项与原件 200 条逐条相同；装机 ATK 26.8.8 的容差词表认 fp16 与 bf16。
**ATK 真跑 fp16／bf16 用例待做**）

**上一次：** 2026-09-08（自带件路独立成条：任务书自带跑测件时整条生成侧
不参与，`run_atk.py --nodes` 把拓扑交给自带件，标杆当场算。性能量具按
「算子名遮住句子还成立」重分一次，分片并行与空闲卡现查收进跑测侧，
`kit_lint.py` 也从生成侧挪过来。冷启动整链 15 分钟。**自产路也在同一个算子上跑通**，整链 995 秒，
生成侧原版一行未改。SpGeMM A2A3 结论：自带件口径精度 200/200 通过、
自产口径 120 条逐条复跑都通过但有 1 条批内污染、性能两套口径都不达标）

**再上一次：** 2026-09-08（纠正上一条的前提：sparse 算子入参里有真张量，
此前拿任务方 testcase 的编码当算子性质，推错两轮。生成侧不为它改流程，
必需增量收敛到 `facts.json` 的 `backend` 与 `accuracy.kind=plugin` 两个字段；
`case_shape` 那几处与 `adopt_kit.py` 降级为「采纳自带件支路才用得上」。
性能主次纠正：任务书口径 ATK 表达不了时主线是任务方脚本。契约升到十四项）

**更早：** 2026-09-07（ATK 链路拆出两条正交的轴：执行剖面 `facts.json.backend`
定 ATK 怎么调算子，工程形态由 A2 探测定怎么编。sparse 算子走 `backend=npu`，
跳过 A2.6／A3.5 并在报告里标不适用；任务自带跑测件由 `adopt_kit.py` 归一进用例包，
与自产路在 S4 合流。契约升到十三项。自带件 200 条真机跑通管道，
桩顶替未实现的算子）

**再更早：** 2026-09-07（ATK MR !32 按评审改：回收判定从任务名换成 device_run
队列，修掉误杀 CPU worker 的回归并补上 dist 覆盖；submodule 钉点挪到 `5ada6a8`）

**更早些：** 2026-09-04（行文规范落成可机检门禁：`.claude/rules/doc-style.md`
加 `doc_style_lint.py`，判据取自 Kubernetes 与 Google 风格指南并在存量上标定过误报；
顺带修掉 4 处存量断引用与 20 个疑问句标题）
