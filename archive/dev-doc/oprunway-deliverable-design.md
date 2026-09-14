# OpRunway 验收产物交付设计

本文件定义一次正式验收结束后交付给调用方的东西：交给谁、包里有什么、人怎么读、
收件人怎么自己核。全文以「执行 agent 只照中文文字手工产出、不依赖任何生成器脚本」
为前提设计；凡是要求人手计算、跨文件对照或凭记忆重构的动作，一律视为设计缺陷并已消除。

正文（第 1–7 节）是执行一次验收就要照做的操作规则。第 8 节「本设计的取舍」是维护者视角，
与正文分开，落地时应移出 `plugin/`（仓规 §2）。

---

## 1. 问题陈述

正式入口跑完一次验收后，会在一个全新 session 目录下产出 `inputs/`、`receipts/`、`reports/`
三个子目录，外加 build 日志、ATK 输出、profiler CSV 等。当前有四个已确认的问题。

**问题 1：人读报告太薄。** `reports/acceptance.md` 只有终态词、原因码、spec 哈希、
一串证据 sha256 和未验证条款。用例分母与通过数、精度判据与其来源、性能实测值、目标 SoC、
ATK 版本、build 来源、耗时，全都没有。读者拿到它得不到任何可判断的事实，也无法否证结论。

**问题 2：交付边界不清。** 产物全在远端 session 目录里。哪些交给调用方、哪些是现场痕迹、
怎么带出来、多大、能不能离线复现，没有任何定义。结果是要么什么都不给，要么把几十 GB 现场
原样交出去。

**问题 3：产物太散无从看起。** 没有入口文件。收件人拿到一堆目录，不知道从哪读起，
也不知道读完哪几个文件就算读完了。

**问题 4：证据链不可独立核验。** 报告列了一串哈希，但收件人没有可照着跑的核验步骤；
哈希对应包里哪个文件、用什么命令核、核不过意味着什么，都没说。列哈希而不给核法，
等于要求对方无条件相信。

---

## 2. 根因

四个问题不是四个独立缺陷，根因只有三条。

**根因 A：产物是「执行过程的副产品」，不是「为某个读者准备的东西」。**
session 目录的结构服务于执行流程（先做什么后做什么），不服务于阅读（先看什么后看什么）。
所以既没有入口，也没有边界——问题 2 和问题 3 同源。

**根因 B：结论与证据之间缺一层「可复算」。**
报告陈述结论，证据堆在旁边，中间没有「用这几个整数、照这张表、你自己走一遍」这一步。
只要这一步不存在，报告就只能被相信或被怀疑，不能被检验。问题 1 的「薄」和问题 4 的
「不可核验」是同一件事的两面：薄不是字数少，是可否证的事实少。

**根因 C：哈希被当成装饰而不是坐标。**
一个 64 位值只有同时给出「它是哪个文件的摘要」和「用什么命令能重新算出它」才有用。
现状里大量哈希是聚合内容锚、规范化字节摘要、现场绝对路径下的文件摘要——
收件人手上既没有那个文件，也没有那个算法，于是每一个哈希都是死值。

另有一条本轮特有的前提，它决定了本设计的形态：**确定性代码正在被删除，
四份大收据与终态产物今后由执行 agent 按中文文字手工产出。**
因此凡是「手写嵌套 JSON 数组」「跨四个数组做 join」「手抄几百行清单」的方案都不可用——
它们把最脆的东西架在最不可靠的地基上。本设计的一切结构选择都服从这一条。

---

## 3. 交付包定义

### 3.1 档位

只有三档，档位由固定白名单决定，执行 agent 不做逐文件判断。

| 档位 | 内容 | 何时产出 |
|---|---|---|
| 现场保留 | 整个 session 目录，不移动、不删改 | 每轮都有，留在 NPU 目标机 |
| **核验包（默认）** | 第 3.2 节白名单树，目标 50 MB 以内 | 正式终态（`PASS`/`DUT_FAIL`/`UNSUPPORTED`）时产出 |
| 复现包（按需） | 核验包 + `staging/` + `install/` + 全部输出 `*.bin` | 收件人明确索取时才产 |

未形成正式裁决的轮次（`PLUGIN_ERROR`、`NEEDS_INPUT`、`BLOCKED`），
以及 session 的 `reports/` 下出现 `acceptance.pending.*` 或 `attempt.json` 的轮次，
**一律不产交付包**，改产一份《未成局说明》（见 3.5 节）。

### 3.2 核验包目录树（固定白名单，照单复制，不做判断）

包根目录名三段：`<算子名>-<UTC 日期 YYYYMMDD>-<session 目录名>`，
整包打成同名 `.tar.gz`。**文件名一律 ASCII**：中文文件名经 tar 在不同平台间正规化后
会让 `sha256sum -c` 假失败，中文只出现在文件内容里。根级文件用两位数字前缀，
`ls` 的排序就是阅读顺序。

```
<算子名>-<UTC 日期>-<session 名>/
│
├── 00-REPORT.md              【唯一入口，必读】人读验收报告全文。十分钟读者只读 §0/§1/§2。
├── 01-VERIFY.md              【收件人核验步骤】11 步，全部离线，逐条给命令与期望输出。
├── 02-MANIFEST.sha256        包内除自身外全部文件的逐文件 sha256。由 cmd/manifest.cmd 产出。
│                             它取代收据两两互绑，是判断包是否完整的唯一手段。
├── 03-OFFSITE.md             未随包交付的现场大件登记：类别 / 目录级现场路径 / 体量 /
│                             不进包的理由 / 包内替代物是哪个文件。明细见 evidence/offsite-index.txt。
├── acceptance.json           唯一机读正式终态，固定字段模板填空；evidence 只绑 00-REPORT.md 的 sha256。
│
├── cmd/                      【命令自证清单】所有清单先把命令写成文件、再执行它产出清单。
│   │                         收件人的核验动作退化为「在对应目录重跑同一个文件并 diff」。
│   │                         这消灭了「agent 手抄清单抄错」和「两边排除规则写得不一样」两类事故。
│   ├── rec.sh                命令回执记录器（见 3.4 节）。所有机器事实都必须经它落盘。
│   ├── selftest.sh           包结构自检：一串固定 test -f / test -d，缺件即打印缺失的包内相对路径。
│   │                         打包倒数第二步跑一次，收件人第一步跑一次。
│   ├── manifest.cmd          产出 02-MANIFEST.sha256（在包根跑）
│   ├── source-files.cmd      产出 inputs/source-manifest.sha256（在被测算子子目录跑）
│   ├── build-inputs.cmd      产出 inputs/build-input-manifest.sha256（在源码根跑，排除规则更严）
│   ├── install-tree.cmd      产出 evidence/install-tree.sha256（在安装根跑）
│   ├── outputs-index.cmd     产出 evidence/outputs-index.sha256（在 session 根跑）
│   ├── output-case-ids.cmd   从 outputs-index 提取 case 目录并去重（分母的第二条独立路径）
│   ├── zero-side-cases.cmd   列出 DUT 侧或参考侧输出为 0 的 case 目录（正式 PASS 下须为空或
│   │                         恰好等于预期报错 case 集合）
│   ├── case-ids.cmd          从 caseset 提取全部 case id，升序去重一行一个
│   └── offsite.cmd           产出 evidence/offsite-index.txt（未随包大件的大小 + 哈希 + 相对路径）
│
├── inputs/                   调用方输入的逐字副本（调用方有权拿回自己给过什么）
│   ├── taskdoc.md                任务书原文逐字副本——全链语义权威
│   ├── op.spec.json              spec 人读缩进版（现状根本没进 session，本设计强制补上）
│   ├── op.spec.canonical.json    spec 规范化字节、无尾换行；对它跑 sha256sum 就等于报告里的 spec 摘要。
│   │                             这让全报告出现频率最高的那个哈希第一次可以用普通工具核。
│   ├── design.<原后缀>           ATK 用例设计，caseset 的唯一生成源、精度判据名的出处
│   ├── case_generator.py         仅当调用方提供
│   ├── execution_plugin.py       仅当调用方提供（CPU 真值或比较口径若由它定义，它就是精度判据本身）
│   ├── task-cases/**             仅当任务书自带 self-test bundle：官方分母来源，整棵逐字复制
│   ├── source-manifest.sha256    被测算子子树逐文件清单（路径 + 内容哈希，不含权限位）
│   └── build-input-manifest.sha256   构建输入范围逐文件清单
│
├── records/                  执行事实记录。全部是「命令 + 逐字输出」与小表格，不写大 JSON。
│   ├── 10-source.md              输入身份、源码清单锚、目标 SoC 是否在任务书硬件集合内
│   ├── 20-build.md               build/install 命令回执、fresh 包身份、CMakeCache 目标绑定键值、双符号命中行
│   ├── 30-cases.md               ATK 版本探测、casegen 命令回执与 seed、分母与 case id 集合
│   ├── 40-execution.md           物理卡与 child 环境、两条 ATK 命令回执、加载 ELF 证据、计数闭合表
│   └── 50-timeline.md            起止 UTC、逐阶段耗时、主动预算、SESSION_ROOT 逐字值
│
├── evidence/                 结构化证据原件，按角色裁，不按体积裁
│   ├── caseset.json              ATK 实际生成的正式 caseset——分母本体
│   ├── case-ids.txt              由 cmd/case-ids.cmd 产出
│   ├── accuracy-workbook.xlsx    精度实测结果的原始载体
│   ├── performance-workbook.xlsx 仅 performance=measure；device 时间的原始载体
│   ├── profiler/                 全部 op_statistic_*.csv 与 op_summary_*.csv 原件，扁平放置，
│   │                             文件名保持现场原名（名里带 case 归属）
│   ├── install-tree.sha256       整棵安装树逐文件清单（相对安装根）。一条命令同时覆盖 vendor ELF、
│   │                             ops-info、binary_info_config 与全部 kernel；收件人按 SoC 直接 grep
│   ├── outputs-index.sha256      DUT/参考侧全部输出 bin 的逐文件清单（相对 session 根）。
│   │                             bin 本体不进包，但逐 case 覆盖仍可机械核
│   ├── output-case-ids.txt       由 cmd/output-case-ids.cmd 产出——分母的第二条独立路径
│   ├── zero-side-cases.txt       由 cmd/zero-side-cases.cmd 产出——单侧缺输出的 case 目录
│   ├── site-values.txt           【现场记录值清单】报告里出现、但不对应包内任何文件的 64 位值，
│   │                             逐条列「值 + 两个空格 + 含义」。它是「一哈希一文件」这条规则的
│   │                             唯一豁免名单，收件人可用它做孤儿哈希闭合
│   └── offsite-index.txt         由 cmd/offsite.cmd 产出：未随包大件的大小、哈希、相对 session 根路径
│
├── logs/                     文本证据。默认整份进包，只有四类超长日志走摘录
│   ├── atk-version.casegen.log       ATK 版本探测全文（本来就很短）
│   ├── atk-version.execution.log     同上
│   ├── install.log                   安装日志（带 --quiet，通常即全文）
│   ├── nm-*.txt                      全部候选 .so 的 nm 输出原件，编号保持现场原样
│   ├── atk-loaded-library.log        命中「import … from … success!」的 ATK 内部日志【原件整份】。
│   │                                 它是「运行时真的加载了本轮 ELF」的唯一实证，绝不允许只交 grep 结果。
│   └── excerpts/                     唯一允许摘录的四类：build 与两条 ATK run 的 stdout/stderr。
│       ├── build.head-tail.txt           前 200 行 + 后 200 行
│       ├── build.grep-target.txt         命中目标 SoC 与算子 build token 的行
│       ├── atk-accuracy.head-tail.txt    前 200 行 + 后 200 行
│       └── atk-performance.head-tail.txt 同上（仅 measure）
│
└── site/                     现场自述，整节标注「不可离线核验」。原文落盘比让 agent 转述难编得多。
    ├── npu-smi.txt               取卡时刻的 npu-smi info 全文（仓规 §6 要求读完整输出）
    ├── cann-version.txt          CANN / 驱动版本命令输出
    ├── lease.txt                 外部 flock 锁文件路径、取锁与释放时刻
    └── handoff.txt               执行方具名声明（唯一性、未复用、session 路径）
```

### 3.3 明确不进包的东西，以及为什么

理由词固定，agent 照抄，不自行编写。

| 现场路径 | 体量量级 | 不进包的理由 | 包内替代物 |
|---|---|---|---|
| `staging/source/**` | 数十–数百 MB | 收件人手上本来就有源码原件，核身份用逐文件清单即可 | `inputs/source-manifest.sha256` |
| `staging/source/build_out/**` | 数百 MB–1 GB+ | 纯构建中间物，不被任何结论引用 | 无（连登记都不做） |
| `staging/**/*.run` | 数–数十 MB | 只有离线重装才用得上；本档不承诺离线重装 | `evidence/offsite-index.txt` |
| `staging/**/CMakeCache.txt` | 数十–数百 KB | 整份缓存对评审无信息量；只需三个目标绑定键值 | `records/20-build.md` 的回执 |
| `install/**` | 数–上百 MB | 已被安装树逐文件清单全覆盖，二进制本体不增加可核性 | `evidence/install-tree.sha256` |
| `atk-execution/**/*.bin` | 数十–数百 MB | 即使进包也无法离线重算精度（缺输入张量与元信息） | `evidence/outputs-index.sha256` |
| `atk-execution/**/PROF_*/**` | 数十–上百 MB/case | 原始 msprof 目录；本链只消费导出的两类 CSV | `evidence/offsite-index.txt` |
| `atk-casegen/result/**/csv|excel/**` | 与 JSON 同量级 | caseset 的冗余视图，无人引用 | 无（连登记都不做） |
| 主机侧 plog / device 日志 | 数十–数百 MB | 不在 session 内，也不被任何结论引用 | 无（03-OFFSITE.md 用一句话说明） |
| ATK 可执行文件本体 | — | 第三方工具，不随包分发 | 报告 §9 列为「只能相信现场」 |

**登记规则**：凡不进包却被报告引用的，必须在 `03-OFFSITE.md` 有类别行，
且其逐文件哈希必须出现在 `evidence/offsite-index.txt`（由 `cmd/offsite.cmd` 产出，不手抄）。
缺登记 = 不合格。

**根锚不进包**：`02-MANIFEST.sha256` 自身的 sha256 只在带外交接消息正文给出。
包内自证不算数。

**打包前脱敏自检**：`find . \( -name '*.env' -o -name 'real-machine.env' \) -print`
命中即停止交付（仓规 §6 秘密不得外泄）。

### 3.4 四条硬规矩（直接约束 agent 的动作，不只约束产物结构）

**规矩一：命令回执成对，且当场落盘。**
每个机器事实都以「逐字命令 + 逐字输出」两行进入某个 `records/*.md`。
执行形式固定为经 `cmd/rec.sh` 记录，**不允许先跑后补记**：

```sh
# cmd/rec.sh —— 用法: sh cmd/rec.sh <回执文件> <命令...>
f="$1"; shift
{ printf '\n$ %s\n' "$*"; "$@" 2>&1; printf '[rc=%s]\n' "$?"; } | tee -a "$f"
```

落盘时点绑到阶段上，不绑到心情上：
**每个阶段（source / build / cases / execution）的最后一个动作，
是确认该阶段的 `records/*.md` 已包含本阶段全部命令回执**。
build 与 ATK 的 stdout 是一次性的，7200 秒的轮询流程里漏存一条，事后无法补，
只能凭记忆重构——那是本设计里最直接的编造入口，必须靠时点纪律堵死。

**规矩二：一哈希一文件。**
报告里出现的每个 64 位值，必须是包内某个文件 `sha256sum` 的结果。
不满足的量（聚合内容锚、build 前后锚、spec 之外的规范化摘要、未随包文件的摘要）
一律改称「现场记录值（不可离线复算）」，**并逐条写进 `evidence/site-values.txt`**。
这条规则因此从自律变成可否证：收件人扫全包文档里的 64 位值，减掉 MANIFEST 的哈希列，
剩下的必须恰好等于 `site-values.txt`。

**规矩三：只抄不算，且禁止数数。**
数字只从唯一出处抄一次。唯一允许的算术是正式终态下最多一次减法，
且 `PASS` 时三个减数恒为 0。
凡「有多少个」一律不由 agent 数：对相应清单跑 `wc -l` 或 `grep -c`，
把命令与输出成对落盘，再抄那个输出。

**规矩四：先报告后清单，四步线性无环。**
`records/*` 各阶段当场落盘 → 写 `00-REPORT.md` → 写 `acceptance.json`（内含报告哈希）
→ 跑 `cmd/selftest.sh` → 跑 `cmd/manifest.cmd` 产出 `02-MANIFEST.sha256`
→ 把 MANIFEST 的 sha256 粘进带外交接消息。
这条顺序主动消掉了「A 记 B 的哈希、B 又记 A 的哈希」那个封印环，
代价是报告里不能出现任何「包内文件数 / 总字节」这类需要 MANIFEST 先存在的数字——
这两格已从封面卡删除，改为固定句「文件清单见 `02-MANIFEST.sha256`，其行数即文件数」。

### 3.5 未成局说明（不产交付包时的替代物）

文件名 `00-NO-VERDICT.md`，单文件交付，不打包、不带 MANIFEST。固定骨架：

```markdown
# <算子名> 本轮未形成正式裁决

- 词：`PLUGIN_ERROR` / `NEEDS_INPUT` / `BLOCKED`（三选一，逐字）
- 一句话原因：<...>
- 现场 session 绝对路径：<SESSION_ROOT>
- 已取得的证据：<逐条列出，指向现场路径>
- 缺失的证据：<逐条列出，说明缺哪一步>
- 下一步需要什么：<输入 / 硬件 / 授权，逐条>

本文件不是 DUT 结论。本轮没有产生 `acceptance.json`，也没有交付包。
```

---

## 4. 人读报告完整模板

以下是 `00-REPORT.md` 的可直接照抄骨架。**尖括号占位符全部由本轮实际值替换；
模板本身不含任何具体算子名、SoC、shape、dtype、阈值或 URL。**
每个占位符旁的括注是出处，抄完保留括注。

````markdown
# <算子名> 验收报告

> 通用规则（保留本段）：
> **零留空**——每个字段要么写实际值，要么写「不适用（理由）」或「未取证」，
> 后者必须同时在 §9 对应栏出现。
> **一哈希一文件**——本报告出现的每个 64 位值都是包内某个文件 sha256sum 的结果；
> 不满足的一律标「现场记录值」并已列入 `evidence/site-values.txt`。
> **只抄不算**——数字只从唯一出处抄一次；本报告全篇只有 §2 一处算术。

## §0 封面卡

| 字段 | 值 | 出处 |
|---|---|---|
| 终态词 | `<PASS / DUT_FAIL / UNSUPPORTED>` | `acceptance.json` verdict.status |
| 原因码 | `<REASON_CODE>` | `acceptance.json` verdict.reason_code |
| 一句话结论 | <...> | `acceptance.json` verdict.message |
| 算子名 / op_type / build_token | `<算子名>` / `<OP_TYPE>` / `<BUILD_TOKEN>` | `inputs/op.spec.json` |
| 目标 SoC | `<目标 SoC>` | `records/20-build.md` build 命令回执 |
| 任务书声明硬件集合 | `<硬件集合逐字>` | `inputs/op.spec.json` |
| 用例分母 N | `<N>` | `records/30-cases.md` 分母回执 |
| 通过 | `<P>` | `records/40-execution.md` 计数表 |
| 执行失败 / 精度不通过 / 精度缺失 | `<A>` / `<B>` / `<C>` | 同上 |
| 精度判据名 | `<判据名>` | `inputs/design.<后缀>` |
| 随机性策略 | `<固定种子值 或 统计口径 或 不适用（非随机算子）>` | `inputs/design.<后缀>` + `records/30-cases.md` |
| 性能模式 | `<none / measure>` | `inputs/op.spec.json` |
| 性能子集大小 | `<M 或 不适用>` | `records/30-cases.md` |
| ATK 版本 | `<版本串>` | `logs/atk-version.casegen.log` |
| 物理卡号 → 逻辑 device | `<K>` → `0` | `records/40-execution.md` child 环境回执 |
| 起止 UTC / 主动耗时 / 预算（秒） | `<起>` / `<止>` / `<耗时>` / `<预算>` | `records/50-timeline.md` |
| 现场 session 绝对路径 | `<SESSION_ROOT>` | `records/50-timeline.md`（现场位置，不可用于核验） |

包内文件清单见 `02-MANIFEST.sha256`，其行数即包内文件数。
本包根锚（`02-MANIFEST.sha256` 自身的 sha256）只在交接消息正文给出，不在包内。

## §1 怎么读这个包

- 十分钟读者：读完 §0、§1、§2 即可，不需要打开任何子目录。
- 想自己核：翻 `01-VERIFY.md`，11 步、全离线、约十分钟。
- 想看原件：查 §7 的证据表。
- 包根五个文件：`00-REPORT.md` 结论；`01-VERIFY.md` 核法；`02-MANIFEST.sha256` 完整性；
  `03-OFFSITE.md` 没进包的东西在哪；`acceptance.json` 机读终态。
- 边界声明：本包是「可核验的结论 + 小体量证据」，不是完整现场。
  完整现场留在 §8 登记的 session 目录里。

## §2 终态与复算

对外状态词表只有六个：`PASS`、`DUT_FAIL`、`PLUGIN_ERROR`、`UNSUPPORTED`、`NEEDS_INPUT`、`BLOCKED`。
正式 `acceptance.json` 只可能是其中的 `PASS`、`DUT_FAIL`、`UNSUPPORTED`。

判定表（固定文案，不随算子变化，请自己走一遍）：

```
① 目标 SoC ∉ 任务书硬件集合                                    → UNSUPPORTED，不执行 DUT
② build+install 成功、cache 绑定与双符号成立，
   但安装树无该 SoC 的算子交付                                  → DUT_FAIL / TARGET_DELIVERY_MISSING
③ 执行失败>0 或 精度缺失>0 或（measure 且性能不完整）           → 流程错误，不得产出正式报告
④ 精度不通过 ID ∩ 预期报错 ID ≠ ∅                              → 流程错误，不得产出正式报告
⑤ 精度不通过>0                                                 → DUT_FAIL / NUMERICAL_MISMATCH
⑥ 以上皆否                                                     → PASS
```

本轮取值（每行一个事实，出处均为 `records/40-execution.md`）：

- 目标 SoC 在任务书硬件集合内：`<是 / 否>`
- 安装树存在该 SoC 的算子交付：`<是 / 否>`
- 执行失败 `<A>`、精度缺失 `<C>`、性能完整 `<是 / 否 / 不适用>`
- 精度不通过 ID 与预期报错 ID 是否相交：`<否 / 是>`
- 精度不通过 `<B>`

本轮落到第 `<N>` 支。

通过数算式（本报告唯一一处算术）：
`通过 = N − 执行失败 − 精度不通过 − 精度缺失 = <N> − <A> − <B> − <C> = <P>`。
正式 `PASS` 时 A、B、C 恒为 0，通过 = N。

**自相矛盾条款**：若你照上表复算落到第 ③ 或第 ④ 支却拿到了这份正式报告，
无需相信任何现场记录，这份报告即不可采信。

## §3 被测对象与输入身份

| 项目 | 包内相对路径 | sha256 | 核法 |
|---|---|---|---|
| 任务书 | `inputs/taskdoc.md` | `<64位>` | 01-VERIFY 步骤 2 |
| spec 人读版 | `inputs/op.spec.json` | `<64位>` | 步骤 3 |
| spec 规范化版（其哈希即 spec 摘要） | `inputs/op.spec.canonical.json` | `<64位>` | 步骤 3 |
| ATK design | `inputs/design.<后缀>` | `<64位>` | 步骤 5 |
| case generator | `inputs/case_generator.py` 或「未提供」 | `<64位>` | 步骤 5 |
| execution plugin | `inputs/execution_plugin.py` 或「未提供」 | `<64位>` | 步骤 5 |
| self-test bundle | `inputs/task-cases/`（文件数 `<F>`，出处：步骤 5 的 wc 回执） | — | 步骤 5 |
| 源码子树清单 | `inputs/source-manifest.sha256` | `<64位>` | 步骤 4 |
| 构建输入清单 | `inputs/build-input-manifest.sha256` | `<64位>` | 步骤 4 |

被测算子子目录（相对你自己的源码根）：`<被测子目录>`。

清单生成命令**不在本报告里复述**：它们逐字存放在 `cmd/source-files.cmd` 与
`cmd/build-inputs.cmd`，收件人在自己的源码树里重跑同一个文件再 diff 即可。
排除规则就在那两个文件里，两边必然一致。

清单只带路径与内容哈希，**不含权限位**。聚合内容锚不可离线复算，
已按「一哈希一文件」降级为现场记录值，见 `evidence/site-values.txt`。

## §4 执行事实

| 项目 | 值 | 出处 |
|---|---|---|
| build 命令 argv（整段逐字） | `<...>` | `records/20-build.md` |
| install 命令 argv（整段逐字） | `<...>` | `records/20-build.md` |
| fresh 包路径 / 大小 / sha256 | `<...>`（现场记录值） | `records/20-build.md` |
| CMakeCache 目标绑定三键值 | `<键>=<实际>`（期望 `<期望>`） | `records/20-build.md` |
| vendor ELF 路径 / sha256 | `<...>`（现场记录值） | `records/20-build.md` |
| 双符号命中行（两行原文） | `<...>` | `logs/nm-<i>.txt` |
| ATK 三元组（生成阶段 / 执行阶段） | 逐字相同：`<是 / 否>` | `logs/atk-version.*.log` |
| casegen 命令 argv 与 seed | `<...>` | `records/30-cases.md` |
| accuracy / performance ATK 命令 argv | `<...>` | `records/40-execution.md` |
| `ASCEND_RT_VISIBLE_DEVICES` 实际值 | `<K>` | `records/40-execution.md` |
| 运行时加载 ELF 的命中行原文 | `<import … from … success!>` | `logs/atk-loaded-library.log` |
| 逐阶段耗时 | `<逐行>` | `records/50-timeline.md` |

## §5 精度结果

**分母五路对齐**（五个数必须相同；每个数都来自一条命令回执，不手数）：

| 路径 | 值 | 出处 |
|---|---|---|
| caseset 条数 | `<N>` | `evidence/case-ids.txt` 的 `wc -l` 回执 |
| 精度工作簿数据行数 | `<N>` | `records/40-execution.md` |
| 工作簿「总用例数」单元格 | `<N>` | `records/40-execution.md` |
| 输出目录去重数（独立于工作簿） | `<N>` | `evidence/output-case-ids.txt` 的 `wc -l` 回执 |
| §0 的分母 | `<N>` | §0 |

四个整数与三组 ID（整列粘贴，不手抄）：

- 执行失败 `<A>`，ID：`<...>`
- 精度不通过 `<B>`，ID：`<...>`
- 精度缺失 `<C>`，ID：`<...>`
- 预期报错 case ID：`<...>`

**单侧缺输出自检**（整段粘贴 `evidence/zero-side-cases.txt`）：

```
<文件内容整段粘贴；正式 PASS 下应为空，或每一行都能对应到上面的预期报错 case>
```

判据与来源：

- 比较器名：`<判据名>`（`inputs/design.<后缀>`，与 `inputs/op.spec.json` 逐字相同）
- 任务书对应条款位置：`<任务书小节名>`（`inputs/taskdoc.md`）
- 各 dtype 阈值是否在 design 里显式写出：`<是 / 否（依赖 ATK 隐式默认）>`
- 真值来源：参考侧输出，归属由 `evidence/outputs-index.sha256` 的路径前缀判定
- 随机性策略：`<固定种子 <值> / 统计口径 <口径名> / 不适用（非随机算子）>`

覆盖计数闭合（每行 = 一条命令 + 输出 + 期望等式，出处 `records/40-execution.md`）：

| 量 | 命令 | 值 | 期望 |
|---|---|---|---|
| DUT 侧输出条目数 | `grep -c '<DUT 前缀>' evidence/outputs-index.sha256` | `<...>` | = 应有 case 数 |
| 参考侧输出条目数 | `grep -c '<参考前缀>' evidence/outputs-index.sha256` | `<...>` | = 应有 case 数 |
| `op_statistic` 份数 | `ls evidence/profiler/op_statistic_* \| wc -l` | `<...>` | = 性能子集大小 |
| `op_summary` 份数 | `ls evidence/profiler/op_summary_* \| wc -l` | `<...>` | = 性能子集大小 |

措辞纪律（保留本句）：本章结论的完整含义是「ATK 用声明的比较器判定通过」，
不等于「数值正确」。

## §6 性能结果

性能模式：`<none / measure>`。
`none` 时本章只保留一句「本轮未测量性能」，并在 §10 落位，不留空表。

`measure` 时：

- 性能子集 ID：`<...>`
- 逐 case device 时间（微秒）：整列从性能工作簿粘贴，出处 `records/40-execution.md`
- profiler：`op_statistic` `<x>` 份、`op_summary` `<y>` 份，表头字段名 `<...>`
- timing scope：ATK `performance_device` 记录的 NPU device 时间，非端到端墙钟

纪律（保留三句）：性能维度不产生「达标 / 满足 / 通过」判定，只产生实测值。
任何以「相对某基线提升」形式给出的任务书条款一律进 §10 未取证栏。
本 workflow 不连接、不运行、不采集 GPU 数据。

## §7 包内证据表

每行三列：包内相对路径 / sha256 前 12 位 / 这份文件证明什么（一句）。

**现场绝对路径不在本表**：包内相对路径与现场路径的对应关系由一条规则给出——
现场路径 = `<SESSION_ROOT>` + 该文件在 `03-OFFSITE.md` 与 `records/50-timeline.md`
里登记的相对位置。手工维护两列对应关系错了没人发现，所以本表只留一列。

| 包内相对路径 | sha256 前 12 位 | 证明什么 |
|---|---|---|
| `evidence/caseset.json` | `<...>` | 分母本体 |
| `evidence/accuracy-workbook.xlsx` | `<...>` | 精度实测原始载体 |
| `evidence/install-tree.sha256` | `<...>` | 该 SoC 的算子交付是否在安装树里 |
| `evidence/outputs-index.sha256` | `<...>` | 逐 case 输出覆盖 |
| `logs/atk-loaded-library.log` | `<...>` | 运行时加载 ELF 的实证 |
| `<其余逐行>` | `<...>` | `<...>` |

## §8 现场留存件

摘要见下表，明细在 `03-OFFSITE.md`，逐文件哈希在 `evidence/offsite-index.txt`。

| 类别 | 体量量级 | 不进包的理由 | 包内替代物 | 索取时该报什么 |
|---|---|---|---|---|
| `<类别>` | `<量级>` | `<固定理由词>` | `<包内文件>` | `<case 编号 或 相对路径>` |

索取方式：见 §11 的联络方式。索取到原件后对它跑 `sha256sum`，
与 `evidence/offsite-index.txt` 里的值比对。

## §9 可核验边界

**可离线复算**（收件人自己能得出同样结论）：包完整性、任务书同一性、spec 摘要、
源码同一性（正反双向）、其余输入同一性、分母五路闭合、单侧缺输出自检、终态复算、
证据表哈希、孤儿哈希闭合、清单命令重跑。

**只能相信现场**（记录内部自洽可核，独立性不可得）：数值真的来自 NPU 执行；
运行时真的加载了那个 ELF；profiler 真的对应这次运行；fresh build 真的发生过；
机器、卡、CANN 与锁的自述；ATK 可执行文件身份；CPU 真值与阈值语义；
这是唯一一轮、没有挑选重跑。
每条的依据都是同一批现场执行记录。**本报告不使用「多份证据互相印证」这类措辞**——
记录互相引用不产生独立性。

**未取证**：GPU 或原算子的性能比值（`UNVALIDATED`）；
内存、显存、workspace、带宽等资源条款（未评估）；NPU 绝对时间不构成性能达标。

## §10 未验证条款与未评估维度

逐条列出 `inputs/op.spec.json` 的未验证条款原文，以及任务书里每一条资源类要求
（逐条对账，不得静默丢弃）。

- `<条款原文>` —— 未验证 / 未评估
- `<...>`

上述条款不包含在本次终态判定中。

## §11 执行方具名声明与联络

固定模板，由执行方填空并署名（副本同时存于 `site/handoff.txt`）：

> 本包为算子 `<算子名>` 本轮唯一一次正式执行的产物；session 路径为 `<SESSION_ROOT>`；
> 未复用任何既有 build、缓存或输出；未在受保护只读输入根内写入；
> 所选物理卡为 `<K>`，外部锁文件路径为 `<锁路径>`，取锁时刻 `<UTC>`，释放时刻 `<UTC>`。
> 声明人：`<姓名>`　时刻：`<UTC>`　联络：`<联络方式>`

## 附录 A 逐 case 明细

见 `evidence/accuracy-workbook.xlsx`，可用任意表格软件离线打开。
其数据行数必须等于 §0 的分母 `<N>`，这本身是又一次闭合检查。
本报告不手抄逐 case 表。

## 附录 B 命令回执原文

本轮全部「命令 + 逐字输出」按 `records/` 顺序汇总。
**是否可重跑不靠人工打标签**：在 `cmd/` 下有同名文件的就是可在包内重跑，
只记录在 `records/*.md` 里的就是现场一次性。

## 附录 C 四个必读陷阱

1. spec 摘要是规范化 JSON 字节的摘要。请核 `inputs/op.spec.canonical.json`，
   不要对人读版直接跑 `sha256sum`——那必然对不上，且不是造假。
2. 一切核验走包内相对路径。收据与报告里的现场绝对路径在你的机器上必然不存在。
3. 聚合内容锚不可手工复算，请用逐文件清单；权限位不在核验范围。
4. `sha256sum -c` 不会发现清单外多出来的文件。每处清单核验都配了一步反向 diff，别跳过。

## 附录 D 现场记录值清单

见 `evidence/site-values.txt`。该文件逐条列出「报告里出现、但不对应包内任何文件」
的 64 位值及其含义。`01-VERIFY.md` 步骤 9 会把它当成孤儿哈希的唯一豁免名单：
扫出来的孤儿必须恰好等于这份清单，多一条少一条都要问清楚。
````

---

## 5. 收件人核验步骤

以下是 `01-VERIFY.md` 的可直接照抄骨架。全部离线，约十分钟。
**任何一步失败即停止**，后面的步骤都以前面的结论为前提。

开头两句固定说明：本机是 Linux 用 `sha256sum`，macOS 用 `shasum -a 256`
（输出格式相同，可互换）；`$PKG` 指解包后的包根，`$SRC` 指你自己那份被测源码根，
`$TASKDOC` 指你自己那份任务书。

```bash
# 步骤 0　工具自检、结构自检、拒收检查
command -v sha256sum || echo '本机没有 sha256sum，下文一律换成 shasum -a 256'
cd "$PKG" && sh cmd/selftest.sh          # 期望：打印 OK；缺件会打印缺失的包内相对路径
find . \( -name 'acceptance.pending.*' -o -name 'attempt.json' \) -print
# 期望：无输出。命中即说明这不是一次成功验收却被打成了正式包，直接拒收。
find . \( -name '*.env' -o -name 'real-machine.env' \) -print
# 期望：无输出。命中说明产出端跳过了脱敏自检。
find . -type f -size 0
# 期望：无输出。包内不允许空文件——这一条直接抓占位空 CSV。
```

```bash
# 步骤 1　包完整性（正向 + 反向 + 根锚，三步缺一不可）
cd "$PKG" && sha256sum -c 02-MANIFEST.sha256 | grep -v ': OK$'   # 期望：无输出
sh cmd/manifest.cmd > /tmp/manifest.new
diff /tmp/manifest.new 02-MANIFEST.sha256                        # 期望：无输出
# 这一步跑的是产出端用过的同一个文件，抓「清单外多塞文件」，不需要你自己拼 find。
sha256sum 02-MANIFEST.sha256
# 把这一行与交接消息正文里的根锚逐字比。根锚不在包里，包内自证不算数。
# 不符：整包不可采信，停止。
```

```bash
# 步骤 2　任务书是不是你给的那一份
sha256sum "$TASKDOC" "$PKG/inputs/taskdoc.md"
# 期望：两行哈希相同，且等于报告 §3 表里任务书那一行。
# 不符：输入不是同一份，全部结论作废。
```

```bash
# 步骤 3　spec 摘要
sha256sum "$PKG/inputs/op.spec.canonical.json"
# 期望：等于报告 §3 里的 spec 摘要，也等于 acceptance.json 的 spec 摘要字段。
python3 -c "import json,sys
d=json.load(open('$PKG/inputs/op.spec.json'))
sys.stdout.write(json.dumps(d,sort_keys=True,separators=(',',':'),ensure_ascii=False))" | sha256sum
# 期望：与上一条相同——证明人读版与规范化版是同一个对象。
# 注意：不要对 inputs/op.spec.json 直接跑 sha256sum 去比 spec 摘要，那必然不等，不是造假。
```

```bash
# 步骤 4　被测源码同一性（正向 + 反向）
cd "$SRC/<报告 §3 给出的被测子目录>"
sha256sum -c "$PKG/inputs/source-manifest.sha256" | grep -v ': OK$'   # 期望：无输出
sh "$PKG/cmd/source-files.cmd" > /tmp/src.mine
diff /tmp/src.mine "$PKG/inputs/source-manifest.sha256"               # 期望：无输出
# 反向 diff 不能跳：只跑 -c 的话，源码树里多出来的文件不会被发现。
# 你跑的是产出端用过的同一份字节，排除规则两边必然一致。
cd "$SRC"
sh "$PKG/cmd/build-inputs.cmd" > /tmp/bi.mine
diff /tmp/bi.mine "$PKG/inputs/build-input-manifest.sha256"           # 期望：无输出
```

```bash
# 步骤 5　其余输入同一性
cd "$PKG"
sha256sum inputs/design.* inputs/case_generator.py inputs/execution_plugin.py 2>/dev/null
find inputs/task-cases -type f 2>/dev/null | wc -l
# 期望：逐条等于报告 §3 表里的值；bundle 文件个数等于 §3 标注的个数。
# 少一个就等于分母被偷偷缩小。
```

```bash
# 步骤 6　封面卡与出处对齐（复算前必做）
# 把报告 §0 的六个整数（N / 通过 / 执行失败 / 精度不通过 / 精度缺失 / 性能子集大小）
# 逐格与 records/30-cases.md、records/40-execution.md 里的同名命令回执比对。
grep -n -E 'wc -l|grep -c|总用例数' records/30-cases.md records/40-execution.md
# 期望：每个整数在 records 里都能找到产生它的那条命令与输出，且值相同。
# 报告里同一个数字只出现在 §0 与它的出处两处；第三处出现即为抄写错误。
```

```bash
# 步骤 7　终态复算（不需要任何工具）
# 用步骤 6 对齐过的整数，照报告 §2 逐字印出的六支判定表从上往下走一遍。
# 得到的终态必须与 §0 的终态词、acceptance.json 的 verdict 一致。
# PASS 的充要形态是执行失败=0、精度不通过=0、精度缺失=0 且性能完整。
# 若你落到第 ③ 或第 ④ 支却拿到了这份正式报告，无需相信任何现场记录，本报告即不可采信。
```

```bash
# 步骤 8　分母与覆盖闭合（两条独立路径 + 一次自反检查）
wc -l < evidence/case-ids.txt                 # 路径一：caseset 的 case 数
sh cmd/output-case-ids.cmd | wc -l            # 路径二：从文件系统清单独立统计的 case 数
# 期望：两数相同，且等于报告 §5 分母五路对齐表里的每一个数。
# 路径二来自 outputs-index，与工作簿不同源，才构成真正的第二条路径。
sh cmd/zero-side-cases.cmd | diff - evidence/zero-side-cases.txt   # 期望：无输出
cat evidence/zero-side-cases.txt
# 期望：空文件，或每一行都能对应到报告 §5 列出的预期报错 case。
# 有一行对应不上，就是「某个 case 单侧缺输出」，正式 PASS 下不允许存在。
ls evidence/profiler/op_statistic_* | wc -l
ls evidence/profiler/op_summary_*   | wc -l
# 期望：两数相等且等于报告 §0 的性能子集大小。
```

```bash
# 步骤 9　一哈希一文件闭合（孤儿哈希）
grep -rohE '[0-9a-f]{64}' 00-REPORT.md 03-OFFSITE.md acceptance.json records/ \
  | LC_ALL=C sort -u > /tmp/doc-hashes.txt
awk '{print $1}' 02-MANIFEST.sha256 | LC_ALL=C sort -u > /tmp/pkg-hashes.txt
awk '{print $1}' evidence/offsite-index.txt | LC_ALL=C sort -u >> /tmp/pkg-hashes.txt
LC_ALL=C sort -u -o /tmp/pkg-hashes.txt /tmp/pkg-hashes.txt
comm -23 /tmp/doc-hashes.txt /tmp/pkg-hashes.txt > /tmp/orphans.txt
awk '{print $1}' evidence/site-values.txt | LC_ALL=C sort -u | diff - /tmp/orphans.txt
# 期望：无输出。孤儿哈希必须恰好等于产出端预先申报的现场记录值清单。
# 多一条 = 报告里有个哈希落不了地；少一条 = 申报了却没在报告里用，两种都要问清楚。
```

```bash
# 步骤 10　命令回执重跑与形状检查
# 打开报告附录 B，凡在 cmd/ 下有同名文件的命令，原样重跑，输出应与回执逐字相同。典型四条：
grep '<目标 SoC>' evidence/install-tree.sha256 | head
# 期望：该 SoC 的 ops-info / binary_info_config / kernel 交付都在安装树清单里。
head -2 evidence/profiler/op_statistic_*.csv | head -20
# 期望：表头含算子类型与总时间列，且有正数行。
grep -o 'from .* success!' logs/atk-loaded-library.log | LC_ALL=C sort -u
# 期望：去重后**恰好一行**。多行 = 加载身份不唯一；零行 = 唯一实证缺失。
grep -c 'GetWorkspaceSize' logs/nm-*.txt
# 期望：被选中那份命中；双符号两行原文与报告 §4 相同。
```

```bash
# 步骤 11　ELF 身份、条款对账、措辞检查
grep -E '\.so' evidence/install-tree.sha256 | head
# 期望：vendor ELF 那一行的哈希，与报告 §4 的 build 侧 ELF 哈希、
#       以及执行侧「实际加载 ELF」哈希三者为同一字符串。
# 你核到的是「三处记录写的是同一个文件」，不是「进程真的加载了它」——后者在报告 §9 信任栏。
grep -nE '达标|满足|合格|优于|提升|倍' 00-REPORT.md
# 期望：仅命中否定句（例如「不产生达标判定」）。出现肯定式即违规，报告应退回。
grep -nE '相比 ?GPU|对比 ?GPU|GPU 基线' 00-REPORT.md
# 期望：同上，只允许出现在「本 workflow 不采集 GPU 数据」这类否定句里。
# 最后拿你自己的任务书逐条对报告 §10：每一条资源类要求、每一条相对基线的性能要求，
# 都必须在未取证栏出现过。缺一条即说明该条款在 spec 编制阶段被静默丢弃。
```

**结论口径**：步骤 0–11 全部通过，你得到的是
「这份包内部完整、输入与你给的一致、终态可由公开规则从公开整数复算出来、
每个哈希都能落地、逐 case 输出覆盖没有单侧缺失」。
你没有得到、也不可能从本包得到的是下一节列出的那些事实。

---

## 6. 无法离线核验、只能相信现场的部分

以下每一条都写进报告 §9 的信任栏，逐条注明「本报告对该事实的依据是现场执行记录」。
**禁止**用「多份证据互相印证」暗示它们被独立验证过——同源记录互相引用不产生独立性。

| # | 只能相信的事实 | 能核到什么 | 为什么核不动 |
|---|---|---|---|
| 1 | 数值真的产自 NPU 执行 | 工作簿存在、格式正确、分母闭合 | 包内无输入张量与元信息，无法离线重算任何数值 |
| 2 | 运行时真的加载了本轮 ELF | 三处记录写的是同一个哈希；加载日志里命中行去重恰好一行 | 日志是现场写的文本，不能证明进程 dlopen 过它 |
| 3 | profiler 真的对应这次运行 | CSV 存在、表头正确、份数与性能子集相等 | CSV 与本次运行之间没有任何密码学绑定，归属靠文件名编号 |
| 4 | fresh build 真的发生过、未复用旧产物 | 命令 argv、返回码、包身份、CMakeCache 绑定键值自洽 | 全部来自同一批现场记录，无独立时间戳源 |
| 5 | 机器、卡、CANN 版本、外部互斥锁 | `site/` 下三份原文自洽，与声明一致 | 现场自述；原文落盘只是提高伪造成本，不产生独立性 |
| 6 | ATK 可执行文件确实是官方 ATK | 两阶段三元组逐字相同 | 收件人手上没有那份可执行文件，无法验证哈希对应何物 |
| 7 | CPU 参考值的语义与阈值含义 | 判据名在 design 与 spec 里逐字一致 | 比较器内部语义由 ATK 与调用方 plugin 决定，不在包内 |
| 8 | 这是唯一一轮，没有挑选重跑 | §11 的具名声明与 `site/handoff.txt` 副本 | 纯声明。唯一的补强是它是可追责的一句话 |
| 9 | 记录本身由 agent 手工产出 | 每条事实都有「命令 + 逐字输出」的成对回执 | 确定性代码已删除；agent 可以让一切自洽而全都是编的 |
| 10 | 未随包大件的内容 | 路径、大小、哈希已登记在 `evidence/offsite-index.txt` | 文件本体不在包内；要核本体须索取复现包 |
| 11 | 聚合内容锚、build 前后锚 | 值已登记为现场记录值 | 算法含权限位、排序口径自定义，无现成工具可复现 |

第 9 条是本轮相对旧实现新增的信任面，必须在报告里显式写出，让收件人自己给它定价。

---

## 7. 手工产出最容易出错的地方与防呆做法

| # | 最容易出错的地方 | 为什么容易错 | 防呆做法 |
|---|---|---|---|
| 1 | 命令输出没当场落盘 | stdout 一次性；漏存一条事后无法补，只能凭记忆重构 | 一律经 `cmd/rec.sh` 记录；每阶段最后一个动作是确认该阶段回执齐全 |
| 2 | 手抄清单抄错、两边排除规则不一致 | 清单几百行；两边各敲一次 find，字节难以一致 | 五处清单改用 `cmd/*.cmd`：命令先落文件，两边跑同一份字节再 diff |
| 3 | 漏文件 | 白名单树十几项，手工复制必有遗漏 | `cmd/selftest.sh` 在打包倒数第二步与收件人第一步各跑一次，缺件即打印路径 |
| 4 | 封面卡与正文数字分叉 | 同一个整数印两遍，改一处忘另一处 | 同一数字只允许出现在 §0 与它的唯一出处；核验步骤 6 先做逐格对齐再复算 |
| 5 | 数数数错 | 「多少份 profiler」「多少条输出」靠眼睛数 | 一律 `wc -l` / `grep -c`，命令与输出成对落盘，报告只抄那个输出 |
| 6 | 摘录可被裁剪而不可核 | 手贴的 grep 结果「贴什么都过」 | 文本证据默认整份进包；只有固定四类超长 stdout 走摘录，加载库日志必须交原件 |
| 7 | 「一哈希一文件」的边界歧义 | 有些哈希只存在于清单行里，agent 可能误标 | 非包内文件摘要一律进 `evidence/site-values.txt`；步骤 9 做孤儿闭合 |
| 8 | 封印顺序做成环 | A 记 B 的哈希、B 又覆盖 A | 固定四步线性顺序（3.4 规矩四），并从封面卡删掉需要 MANIFEST 先存在的两个数字 |
| 9 | 秘密混进包 | `.env` 与私有配置就在附近 | 打包前一条 `find` 自检，命中即停止交付；收件人步骤 0 复查一次 |
| 10 | 未成局却出了正式包 | 三个非正式词是最常见的收尾 | 出现 `acceptance.pending.*` 或 `attempt.json` 即不产包，改产《未成局说明》 |
| 11 | 中文文件名过 tar 后校验假失败 | 跨平台 Unicode 正规化 | 文件名一律 ASCII，中文只出现在文件内容里 |
| 12 | 占位空文件被当成证据 | 空 CSV 与空日志看起来像有 | 收件人步骤 0 的 `find . -type f -size 0` 必须无输出 |
| 13 | 现场绝对路径与包内路径对应关系手抄 | 两列路径手工维护，错了没人发现 | 报告 §7 只留包内相对路径一列；现场路径由 `<SESSION_ROOT>` 前缀规则推出 |
| 14 | 某 case 单侧缺输出却被计数补平 | 计数等式会被互相抵消 | `cmd/zero-side-cases.cmd` 自反器：PASS 下输出须为空或恰好等于预期报错集合 |
| 15 | 分母四处同源、假装对齐 | 工作簿相关的几个数其实是一个来源 | 增加第五路：从 `outputs-index` 独立统计 case 目录去重数，来自文件系统而非工作簿 |
| 16 | 报告把 NPU 绝对时间写成「达标」 | 措辞习惯 | 收件人步骤 11 用两条 grep 机械检查；产出端在写完 §6 后自己先跑一遍 |

---

## 8. 本设计的取舍

> 本节是维护者视角的实现取舍讨论，不服务于「执行一次验收」。
> 按仓规 §2，落地时正文（第 1–7 节）进 `plugin/` 的 skill 文字，本节移到 `dev-doc/`。

### 8.1 为什么以「封面即结论」为骨架

三份候选提案在「报告写什么、包里放什么、收件人怎么核」三层上高度趋同。
真正拉开差距的是对「确定性代码正被删除」这一前提的响应。
只有 readable 把这个前提吃进了设计：它放弃手写大 JSON 收据，
把四份收据换成 Markdown 的「逐字命令 + 逐字输出」，机读面收缩到
`acceptance.json` 与一份 MANIFEST，并用四条硬规矩直接约束 agent 的动作而非只约束产物结构。
另两份都把 `receipts/*.json` 当「原件、逐字复制」，而那些原件今后必须由 agent 手敲；
其中一份还要求手写含数百条 rows/outputs/profiles 的执行收据，
再用一段禁止修改一个字符的 python 去解析它——脚本必然崩，
而崩的时候 agent 只剩改数据或伪造派生文件两条路。

### 8.2 嫁接进来的东西

- **命令自证清单（`cmd/*.cmd`）**：产出端与收件人跑同一份字节，
  同时消灭「手抄清单抄错」与「排除规则两边不一致」。这是三份提案里最强的单点防错机制。
- **`cmd/selftest.sh`**：手工白名单复制最常见的失败是漏件，这是唯一给产出侧的漏件提示。
- **禁止数数**：把「只抄不算」推到底，所有计数改成命令回执。
- **`site/` 目录**：仓规 §6 对「读完整 `npu-smi`」「非阻塞 flock 覆盖整个 CLI」有硬要求，
  原文落盘比让 agent 在声明里转述难编得多，成本只是三条重定向。
- **拒收规则与脱敏自检**：一条 `find` 的成本，杀伤力是整份 PASS；仓规 §4 与 §6 的合规缺口。
- **孤儿哈希闭合**：把「一哈希一文件」从自律变成可否证。
- **镜像路径思路**：报告 §7 去掉现场绝对路径列，改用前缀规则。
- **单侧缺输出自反器与分母第五路**：拿走逐 case 覆盖检查八成的收益，
  但不引入跨四个数组的 join。
- **复现包档位**：让「放弃离线重算精度」从能力缺失变成收件人的档位选择。

### 8.3 冲突处的裁决与理由

1. **收据形态：Markdown 回执 vs 五份 JSON 原件。裁决取 Markdown。**
   仓规 §3 要求「最终 receipts 的互相哈希绑定」，本设计用单份 MANIFEST 覆盖全包
   加一条带外根锚来承接，绑定强度不降（覆盖面反而更大，人读文档第一次进了哈希覆盖），
   但换掉了两两互绑那个封印环。理由：环在手工产出下必然出现顺序悖论，
   而 §3 要的是「不可事后拼接」，单链加带外锚同样成立。
   §3 的「显式 null / 坏类型 fail-closed」在 Markdown 里没有类型可判，
   改由「零留空」规则承接：每一格要么是命令回执，要么是「不适用（理由）」且在 §9 落位。
2. **是否保留派生 python 命令块。裁决：不保留。**
   题面硬约束是不依赖生成器脚本、不要求复杂计算或交叉索引；
   仓规 §2 也把 SKILL.md 定为唯一编排层，往里塞一段含 fail-closed 判据的 python
   等于在编排层重建一小套裁决器。它想解决的逐 case 覆盖问题，
   用「单侧缺输出自反器 + 分母第五路」拿走大部分收益。
   `cmd/*.cmd` 与 `rec.sh`、`selftest.sh` 不同性质：它们只做 `find | sort | xargs sha256sum`
   与固定 `awk` 分组，不生成用例、不做判断、不解析裁决字段。
3. **进出包判据 vs 固定白名单。裁决：包里只留白名单，判据移出。**
   原判据之一「十分钟读者会读到」需要判断力，正是要扣分的东西；
   而排除式规则漏一条 pattern 会静默放进几百 MB 或一个 `.env`，
   白名单漏一项只会缺文件并被 `selftest.sh` 当场抓到——失败方向不同。
4. **封面卡重复印数字。裁决：保留封面卡，但同一数字只允许出现两处，
   并在核验步骤里先做逐格对齐再复算。**
   十分钟读者不翻子目录的价值高于「每个数字只出现一次」的洁癖，
   代价由「§0 与出处两处、第三处即错」这条规则和核验步骤 6 兜住。
   同时删掉封面卡里需要 MANIFEST 先存在的两个数字，避免与封印顺序冲突。
5. **日志摘录 vs 原件。裁决：默认整份进包，只有固定四类超长 stdout 走摘录。**
   摘录是手工筛选的，收件人拿不到原件就无法 diff，等于「贴什么都过」。
   加载库日志是「运行时加载了本轮 ELF」的唯一实证，必须交原件。
6. **目录形态：语义目录 vs session 子集镜像。裁决：语义目录。**
   镜像解决的是核验时的定位问题，不是阅读时的入口问题；
   定位问题已由 `cmd/*.cmd` 与前缀规则解决，而入口问题是本轮四个问题之一。

### 8.4 明确放弃的东西

1. **离线重算精度。** 输出 `.bin` 不进核验包，且包内没有输入张量与元信息。
   收件人只能核到「每个 case 两侧输出都存在且哈希已登记」。
   要把精度升为可核项，需要 ATK 侧另存输入并单列一档「重算包」，是明确的能力升级。
2. **离线重跑终结器。** 聚合内容锚、必测覆盖的匹配解、安装树遍历这三类人手不可能可靠复现，
   全部降级为「执行时一次记录、事后只做文件级比对与整数复算」。
   其中「必测覆盖契约确实被满足」在本设计下是纯信任项，没有替代物，只做披露。
3. **权限位。** 内容锚重定义为逐文件清单文件自身的 sha256，权限差异不再被发现。
4. **收据的机读性。** 机读面只剩 `acceptance.json` 与 MANIFEST。
   换来的是手工产出不会因为逗号、引号、嵌套写错。
5. **跨四个数组的逐 case join。** 用两个单表检查替代，
   极端情况（某 case 同时缺 DUT 与参考侧输出、且不在预期报错集合）由自反器抓，
   但「某 case 有输出却对应错了 profiler」这类错配仍抓不到。

### 8.5 与仓规的对齐检查

- §2：正文不含开发期取舍；本节移出 `plugin/`。`cmd/*.cmd` 不构成第二套
  generator / runner / 裁决器。
- §3：绑定项逐条落位；build 前后源码锚与聚合锚以现场记录值形式保留并申报。
  fail-closed 由「零留空 + 单侧缺输出自反器 + 证据不完整不出正式包」承接。
- §4：六词词表逐字印出；正式 `acceptance.json` 只可能三词；
  三个非正式词的交付形态定义为《未成局说明》。
- §5：随机算子的种子或统计策略在封面卡与 §5 各有一格，不得留空。
  性能只出实测值，资源维度明确写未评估。
- §7：报告模板与 `cmd/*.cmd` 模板均为占位符，不含算子名、SoC、shape、dtype、阈值或 URL。
