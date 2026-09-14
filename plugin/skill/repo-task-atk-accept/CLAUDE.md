# CLAUDE.md — 跑测验收 skill

改这个 skill 时读。运行时规则在 `SKILL.md`，行文规范与开发流程沿用仓根 `CLAUDE.md`。

唯一目标：**让零上下文 agent 拿一个用例包和一份算子工程，独立跑出可信的验收结论。**

三份配套的开发态文档，按需要读：

| 要什么 | 去哪 |
| --- | --- |
| 某个算子某轮跑出来的现象与量级 | [排障事实](../../docs/development/atk-accept-troubleshooting.md) |
| 某个决定是怎么演进来的、撞过哪些坑 | [架构演进记录](../../docs/development/architecture-log.md) |
| 两个执行器判定不一致的根因（已结案） | [executor-divergence.md](../../docs/development/executor-divergence.md) |
| 已提给 ATK 上游的三个问题 | [#28](https://gitcode.com/Ascend/ATK/issues/28) 连带失败 · [#29](https://gitcode.com/Ascend/ATK/issues/29) deepcopy · [#30](https://gitcode.com/Ascend/ATK/issues/30) tiling |

## 上游是生成侧

吃的用例包由 `repo-task-case-gen` 产出。使用态上不要求它必须来自生成侧——任何符合
结构的目录都收；**开发态上有十四项硬契约**，`run_atk.py` 与 `verdict.py` 都在读它们。
动之前读[用例包契约](../../docs/development/case-package-contract.md)，那里也有归因表。

**用例包里的错回生成侧改，不在验收工作区就地 patch。** 工作区里的是副本
（`<现场>/input/`），改它不传回生成侧，只修好眼前这一次，而且这一轮的结论和用例包
对不上、复现不了。`input/` 是只读语义，本轮生成的东西全在 `work/` 下。

## 三条红线

| 红线 | 具体是什么 |
| --- | --- |
| **不拿算子实现当验收依据** | 工程的公开接口面可读，实现不可读。精度失败时不要去 kernel 里找原因然后判「这是预期行为」——那是让待验收算子给自己出考卷。归因写到「哪一组用例失败」为止，成因交给算子作者 |
| **ATK 是黑盒** | 不改 `third_party/ATK/`，也不改装机的 `site-packages/atk/`。精度标准、比较器、报告格式都按原样用 |
| **结论从数据推导** | `verdict.py` 从 `install.json`、`accuracy.json`、`performance.json` 推结论，报告作者只写证据链。`run_atk.py` 退出码 0 只表示任务跑完，`accuracy.json` 的 `passed` 才是结论 |

## 不要改回去

每条都是真机上换过代价的。改到对应位置时先看这里。

### 部署与构建

| 约束 | 改回去会怎样 |
| --- | --- |
| **`ATK_CUSTOM_OPP_PATH` 不能省，别只设 `ASCEND_CUSTOM_OPP_PATH`** | ATK 在后者下只认十种固定 vendor 名（`{customize,custom} × {_math,_nn,_cv,_transformer,""}`，`acl_wrapper.py:545`）。我们给每个算子起专用 vendor 名 `<snake>_atk` 避免多算子互相覆盖，代价是不在白名单里。前者第一优先级、不做名字匹配、值直接是 `.so` 路径。顺带让 pyaclnn 的签名自检只搜到本算子的头文件 |
| **构建落点靠观察，不靠推断** | 早先按 `experimental`/`index`/`math`/`conversion` 四个锚推路径，等于假设「社区任务 = 往 `experimental/` 加新算子」。bernoulli 那类「改母仓已有算子」四条假设一条不中，连撞四次且三次报错方向是错的 |
| **不要再加「已有算子 ⇒ 非 experimental」这类规则** | 还是假设。`experimental/math/roll` 就是反例：算子既已存在、又在 experimental 下。只需要知道它**在哪** |
| **冒烟的拦截判据不许按剖面开关** | 原来是「`param_reject` 为假就整条闸打开、无条件进全量」，理由是 npu 剖面认不出接口层拒绝的特征串。sparse 走的正是 npu 剖面，于是这道闸在那条路上等于不存在——两次整批同因的口径问题都是这么漏到全量轮的（ATK 归一化缺 fp32↔fp64、标杆自己注入 NaN/Inf），每次多付一轮全量。判据换成条数：冒烟每种取值只有一条，2 条以上一起挂就不是个别用例的事。同形的坑见「三条正交的轴」——**判据要落在产物上，不落在剖面声明上** |
| **不要把砍掉的冒烟加回来** | 它不 fail fast：部署真坏时 ATK 卡在第一条不返回，冒烟和全量一样挂着（实测两次，11 分钟与 10 分钟没跑完 2 条）。省下的也就一分钟（冒烟 5 条 29.5 秒 vs 全量 187 条一两分钟）。它想验的三件事现在各有归属：符号→A2 退 3；环境变量→`run_atk.py` 开跑前查退 3；**so 落点→每轮自动断言退 2**（原先无任何自动检查，这是净增的保护） |
| A2.5 冒烟是**另一件事**，别与上一条混为一谈 | 它验「装进去的实现声不声明支持这些 dtype」，**变不成断言**——不同算子目录能实现同一套 aclnn 接口，装错目录时符号照样可见。口径是每种 dtype 各 1 条加 `facts.smoke.axes` 每个判据轴取值各 1 条，**整批同因（2 条以上一起挂）或接口层拒绝时退 3**，孤例不拦（那是隔离复验的活）。收益：装错目录时早两分钟报错，且直指 `op_dir` |

分界线是「能从现场看出来的交给脚本，看不出来的停下来交出去」：

| 事情 | 谁做 |
| --- | --- |
| 算子在母仓的位置（`rglob` 同名目录） | 脚本 |
| 要不要 `--experimental`（落点在不在 `experimental/` 下） | 脚本推导 |
| `aclnn<Op>GetWorkspaceSize` 由谁定义（`grep -rl`） | 脚本，符号核不到时报出来 |
| 母仓里没有该算子时放哪 | **停，退 4**。答案在任务书「PR 申请合入」，由 agent 或用户给 `--target` |
| 有多个同名目录时用哪个 | **停，退 4**，列出候选 |

### 卡与环境

| 约束 | 改回去会怎样 |
| --- | --- |
| **卡占用问设备，不问进程表** | 判据错过两次，都错在「从进程的名字推它占不占卡」：按「命令行含 `atk` 且含 `--devices`」判会把拉起自己的那层 shell 当成别人的任务，退 3 拦住自己；按「可执行名是 `atk`」判则**别人的进程不叫 atk 就看不见**，代价可量化——IndexFillTensor 报的 16 条缺陷里 3 条是共卡误判，误判率 19%，方向是把通过报成失败 |
| 卡号映射查 `npu-smi info -m` 的 **Chip Logic ID** | 不能按 `npu*2+chip` 算，每卡芯片数随机型变（A2 是 1，A3 是 2） |
| 排除**自己的进程树**（`own_pids`），不按进程名排除 | 隔离复验跑在全量轮之后，按名字排除就是上面第一个坑 |
| npu-smi 不可用时返回空**不拦** | 把跑测卡死在一个诊断步骤上比漏判更糟 |
| 判据只写一份（`probe_env.py` 的 `chip_map` / `own_pids` / `device_busy`），A1 与跑测两处引用 | 同一个判据分两份写迟早漂 |
| **一个算子占一张卡**，目标卡上有别人就退 3 | 共卡的后果不对称：精度结论仍对，**性能结论静默作废**（Device 耗时混进别人的负载），隔离复验会把别人的 aicore 异常算成本算子的连带 |
| **`env.sh` 里 `set_env.sh` 的路径要「找」，不能「推」** | 按 `ASCEND_TOOLKIT_HOME` 的父目录拼是假设装机目录是 `<根>/latest` 形态，而 set_env.sh 会把它解成 `<根>/cann-<版本>`，父目录下没有 set_env.sh。后果静默：那句 source 带 `2>/dev/null`，下游每条命令都在没有 CANN 环境下跑，`build.sh` 回落到另一处安装，cmake 报「找不到 ASC 包」——报错方向差三层。现在 `find_set_env` 只认文件真在的路径，都不在时退 2 |

### 精度、隔离复验与并发

| 约束 | 改回去会怎样 |
| --- | --- |
| **逐用例结论以 statistic 表为准**，不以 `failed cases` / `accuracy false cases` 两张表为准 | 真机撞过静默失真：208/211 不达标而两张表都是 0 行，dtype 表显示每档 100%、复现包一条用例都没有，总结论却写「不通过」 |
| **汇总数仍以 summary 为准**，不在 `_case_verdicts` 里重算 | 两套算法出两个通过率是更糟的失真。对账不等时只在报告里写出来，**只报不改**——改数就是掩盖上游 |
| **隔离复验的判据是「有没有跑到执行」**：日志里有 `[case N][RUN OPP TASK] Start Execute OPP` 就是真实失败，没有就是连带 | 恒等式九份真机日志逐份验过：`执行到 OPP 的条数 == 通过数 + 自有失败数`。曾经错过两次：① 认「批内最小 id 是真凶」——执行顺序不按 id，round5 认成 163 而那一轮 163 根本没执行过；② 认「报错含 507015 的是连带」——真凶自己也报 507015，只是措辞不同 |
| **「跑通」必须同时排除精度不符** | `failed_ids` 只是执行失败，跑起来了但算错的不在里面；只看它会把「算错了」判成连带，再被 `verdict.py` 计入通过。实测一轮抓到 4 条（id 109、125、138、214） |
| `exec_failures` / `accuracy_false` / `cascade` / `not_checked` 对 `checked` 是**精确划分** | `verdict.py` 依赖这一点，改循环时要保住 |
| **隔离复验与全量必须同口径**（含非连续比例） | 口径不同的重跑说明不了任何事。口径对齐后连带才计入通过——它在与全量相同的输入布局下真的跑通了。只有轮数用尽的 `not_checked` 才是未判定。**现在这条靠机制保证**：比例两侧都从 `facts.json` 读，ATK 按用例 id 定切不切，同一条用例的布局自动一致，不再由执行者在命令行上挑（挑错了测的是另一件事，而且不报错） |
| **提速只能靠并发，不能靠算法** | 一次批量跑的信息有硬上限：到自己那块的第一个污染者为止，之后的用例根本没执行。单卡轮数 = 污染型真实失败数 P + 1，换任何分组、二分、编码都绕不过去。按卡切块后是 ⌈P/C⌉ + 1——同一批 210 条实测：单卡 15 轮 12.6 min，8 卡 4 轮 **1 min 36 s** |
| **并发有占用上限，不要当成性能参数删掉** | 机器公用。`auto` 现查到几张就用几张会把空闲卡吃光，别人只能等或被迫共卡，而共卡让性能结论静默作废。上限 4 定在 `probe_env.MAX_AUTO_DEVICES` 一处，隔离复验与性能分片都读它，`--max-devices 0` 给独占机器解开。**这条与「并发度不要写死」不冲突**：那条说的是哪个并发最快，这条管的是能占多少 |
| 别走的两条路 | ① 每条用例单独跑一次 atk：把批内 0.3 秒/条变成 25 秒/条，1000 条失败时 8 卡要 52 分钟；② 让 ATK 崩后恢复 device：方向对，但 `third_party/ATK` 是只读 submodule，要走是给上游提 issue（`pool="prefork"` + `max_tasks_per_child=1`，2 行） |
| 并发时**每块必须有自己的 cwd** | ATK 把 `atk_output/` 写在当前目录，共用一个目录会互相覆盖，`_newest_report` 还会取到别人的报告 |
| **`_newest_report(stem, since)` 的 `since` 不能去掉** | 任务失败到连报告都没写出来时，「取最新 xlsx」会拿到上一轮的报告——上一轮多半通过，于是这一轮被判成通过。隔离复验里一轮接一轮跑，这个坑最致命：id=152 单独跑 5/5 必挂，却被判成连带，报告写出「真实失败 0 条」 |
| **aicore 成因要有证据才认领** | 触发判据和成因是两回事：任何执行失败都触发复验，成因不一定是 aicore。`_aicore_evidence` 命中 `aic-error`、`EZ9999` 才写进 `isolate.json`，空的时候只能写「批内连带，成因未定」 |
| **超时按停滞判，不按总时长判**；总时长兜底**跟着条数走，不是一个常数** | ATK 没有超时兜底，worker 段错误后主进程一直等、进度停在 `0/N`。`_run_logged` 盯日志 `st_size`，`STALL_SECONDS = 180` 不增长就杀，这是主要拦截手段。总时长只当漏网兜底，但**不能写死**：原先 `TIMEOUT = 1800` 标定依据是「187 条实测一两分钟」，用例扩到 1000 条后没人回来调，跑得完的轮次被兜底杀掉——实测 2.81 s/条 × 1000 ≈ 47 min 撞上限，整轮白跑，而报告上看不出是被兜底杀的还是算子挂的。现在 `_round_timeout()` 按 `ROUND_OVERHEAD_SECONDS + 条数 × SECONDS_PER_CASE` 推，下限 `TIMEOUT_FLOOR = 1800`。**仍然不许为了治卡死去调大它** |
| 独立执行器的失败分支**先 `fflush(stdout)` 再问 `aclGetRecentErrMsg()`** | 顺序反了实测丢过整条判定行——那次 abort 就发生在 `aclGetRecentErrMsg()` 里面，调用方只看到「整批一条都没判出来」，去查二进制起没起来，查错方向 |
| **每条跑测命令都要带 `--op`** | 别的算子的验收现场里 `cases.json` 与 `golden/` 一样齐全，在里面跑完全合法，脚本无从发现跑错了现场。真机上出过在 1d 的现场里重复跑 1d、以为在跑 2d，白烧十分钟。`--op` 与 `./facts.json` 的 `aclnn_name` 逐字比对，不等退 3。纪律写在散文里拦不住，写进参数才拦得住 |

### 性能

| 约束 | 改回去会怎样 |
| --- | --- |
| **按规模档出加速比，不出一个总比值** | 真机上 52 条用例的耗时区间 [62.91, 1736.03] us，**跨 27 倍**。揉成一个中位数，「大张量上劣化 30%」这种形态完全看不见，而那正是切分路径（UB 循环切分、多核切分、尾核）出问题的典型表现。逐档出表，某档少于 `MIN_PAIRED` 写 unknown |
| **逐样本先算比值再取中位数**，不是两组中位数相除 | 后者只在两组分布完全一致时才等价，任何一条掉队都会悄悄失衡 |
| **只出加速比，不并排出耗时比** | 两个数互为倒数，并排放着容易读反。判据内部仍跑在耗时比上，那是实现细节 |
| **档位不在本侧算**，读生成侧的 `perf/manifest.json` | `size_band` 的字节门槛是生成侧的知识，重算就是第二套阈值。缺 manifest 时退回旧口径并在报告里写明做不了分档，不猜 |
| **抽样留在生成侧**，不要搬回来 | 「某一档抽不够」的修法是回生成侧调 `max_length` 重新生成，只有那边做得到。早先 `sample_smoke.py` 在本侧，导致规模档阈值两个 skill 各存一份，真机上跑出过两套不一致的阈值（元素数 vs 字节数） |
| **性能轮以精度先过为硬前提** | 失败用例的耗时不可信，一个算错的算子跑得快没有意义。`verdict.py` 里这条不由人判断 |
| **任务书口径 ATK 表达不了时，倍率只能来自外部工具**，不许拿 ATK 那轮的数去算 | 两个口径的数相除没有意义：ATK 采的是一次调用的 Device 耗时，任务书要的常常是分阶段、复用描述符、按特定调用范围采的数。相除出来的倍率看着像结论，其实两边量的不是同一件事。判据表在 `references/run-performance.md`「外部性能结果」，实测一例六条里五条 ATK 不能 |
| 外部结果的收编与裁决**分在两个脚本**：`collect_perf.py` 只校验与算倍率，`verdict.py` 只裁决 | 收编处一旦开始判达标，同一个结论就有两个出处。`_perf_status` 与报告正文都调 `_threshold_with_external`，分两处算迟早出现「总结论说达标、正文说不达标」 |
| 门槛认倍数写法（`倍`/`×`/`x`/`*`）与耗时口径折算，**没有倍数写法就不判** | 只认「倍」一个字时，用 `×` 写的任务书一律抠不到，性能结论全部降级成待人工判定（spgemm 实测撞过）。反过来做模糊匹配同样糟：**抠错一个数比抠不到糟得多**，前者给出的是看着像结论的错结论。判据在 `verdict._criterion_threshold`，量测件 `assets/perf_harness.py` 是同一套，`tests/test_external_perf.py` 拿同一份语料把两处钉在一起 |
| **读数口径以任务书为准，中位数不许换成平均值** | 任务书写的是「报告耗时中位数及90%分位耗时（即约90%的正式采样耗时不高于该值）」（sparse 三份逐字相同），倍率判据落在中位数上。自带件汇总的 `kernel_total_us` 是 `总耗时 / 步数`——一个平均值，两个统计量一个都不是，早先 `run_kit_perf` 直接取它，于是泛化集那批以平均值、判据表那批以中位数进了同一批倍率，还一起算算术平均门槛。现在一律从 Profiler 逐次读数按 `kernel_trace.summarize` 出数，认不出才退回那一列并打 `stat_kind=mean` 逐条标进报告。**语料要选得出区分度**：等长耗时的语料下平均与中位数相等，测试放行「悄悄改回取平均」（实测注入一次没被抓到，补了 100/100/100/500 那条才响） |
| **p90 要跟着数走到报告** | 任务书要求中位数与 90%分位**并列报告**。量测件两个都算了，早先 `collect_perf._summarize` 只透传中位数，p90 在收编那一步丢掉，报告里一个字都没有——**退出码全程 0**。分位数的判据取任务书括号里那句：不高于该值的采样占比达到九成的最小观测值，不做线性插值 |
| ATK 那轮采不到数时，**外部结果不能跟着一起丢** | 算子没发 kernel、报表列名对不上，与「性能没测过」是两回事。真机上撞过：`_performance` 提前 return，外部裁决算对了却没进报告正文 |
| `kind=builtin` 的两轮由 `run_atk.py` 连着跑，**裁决仍归 `verdict.py`** | 早先轮 2 靠执行者记得敲，没敲不报错——`verdict.py` 只写「未评级(基线轮无数据)」，看上去像内置实现跑不了。裁决不能搬进 `run_atk.py`：轮 2 全挂与轮 2 没跑要分开说，那需要两份 JSON 都在手 |

### 报告与复现包

| 约束 | 改回去会怎样 |
| --- | --- |
| **`render.py` 不重算任何数**，只读 `verdict.json` | 一旦它自己算一个百分比，早晚出现「HTML 说通过、md 说不通过」，而两份报告署着同一次验收 |
| 精度表的分母是**用例包里的用例数**，不是 ATK 报告的 `total` | 报告的 `total` 只说明这一轮跑了多少条，用例包才是任务书要求覆盖的范围。两者不等时点出来 |
| 「未通过」拆成精度不符 / 执行失败 / 未判定三列，且只在有非通过用例时展开 | 三者归因完全不同（算错了 / 跑不起来 / 没测到），揉成一列读者当成一件事；全过时展开是一排 0，噪声 |
| **最小复现包只回答「改完 kernel 之后那几条过了没」**，不重现裁决逻辑 | 拿到它的人是算子作者，不装本 skill，也不该被要求读 Python。没有 `verdict.json`、没有隔离复验、没有停滞检测，结果直接看 ATK 自己产的 xlsx |
| 复现包**不拷 golden**，用 `PKG=$HERE/../input` 引过去 | golden 几百 MB。整包拷走时 README 让改这一个变量 |
| 复现包的 atk 命令由 `run_atk.py` 的 `_node_command` 现拼，**不抄一份** | 抄了的话跑测侧改了拓扑或 `--slice_input`，复现包会悄悄跑成另一件事。为此 `_node_command` 不自己 `resolve()` golden，也接受传入的 `load_node`——复现包拼的是带 `$PKG` 变量的命令，resolve 会把变量名当相对路径 |
| 复现包装**执行失败 + 精度不符**两类 | 只装执行失败的话，「全部跑起来了但有几条算错」这种最常见的形态，复现包会是空的 |
| **用例子集换目录不换文件名**，一律叫 `cases.json` | ATK 用用例文件基名当 golden 子目录名，子集叫 `round1.json` 就会去找 `golden/cpu_0/round1/`。所以隔离复验写 `isolate/round<n>/cases.json`、性能子集写 `subset/cases.json` |

## 三条正交的轴

**曾经把它们当成一件事，结论错了三次。** 判据、载体、消费者都不同：

| 轴 | 问什么 | 谁定 | 载体 | 谁读 |
| --- | --- | --- | --- | --- |
| 执行剖面 | ATK 怎么把算子调起来 | 生成侧 S1 | `facts.json` 的 `backend` | `run_atk.PROFILES`、`verdict.py` |
| 工程形态 | 怎么编、怎么装、怎么核符号 | 跑测侧 A2 结构探测 | `install.json` 的 `project_kind` | 只有 `build_install.py` |
| 用例形态 | 输入是 ATK 造的真张量，还是执行器按 seed 现造 | 生成侧 S2／S2′ | **产物本身**：`inputs/` 在不在、`params[]` 里有没有张量入参 | `run_atk.main` 的 inputs 分支、`check_facts` 的入参形态校验 |

三轴独立，各自都有反例：自足工程也可以暴露 aclnn 接口，母仓算子也可以只注册
torch 算子；npu 剖面下自产用例是真张量、自带件用例是 attr 编码。
**不要合并成一个字段**——ops-sparse 那一轮三条轴的取值恰好对上（自足工程 +
torch 算子 + attr 编码），合并之后就再也发现不了它们是三件事。

第三条轴是 2026-09-09 补上的。此前它被当成 `backend` 的附属：`--register-module`
绑在工程形态上、`inputs/` 读不读与 `non_contiguous` 能不能填 `true` 绑在 `backend`
上，而那三条规则的依据全来自自带件恰好是 attr 编码这一个样本。**判据一律落在
产物上，不落在剖面上**——产物在不在是当场看得见的，剖面只是个声明。

分流只判一次，判在生成侧。跑测侧重判一次的话，两处判据迟早漂，而漂了不报错。

| 约束 | 改回去会怎样 |
| --- | --- |
| `npu` 剖面下「测的是不是待验收实现」用 `/proc/self/maps` 探针，**不能省** | 它顶的是 aclnn 剖面里 `_check_loaded_so` 的位置。aclnn 靠日志里 `import aclnnXxx from <路径> success!`，npu 剖面没有那行——实现由 `torch.ops.load_library` 或工程自己的 import 装进进程，ATK 一个字都不打。核不住时报告一切正常、通过率甚至好看，但测的是别处的同名实现。**注册进 ATen 已有算子 NPU 键的那一档尤其要探**：so 没装进来时调用不抛异常，dispatcher 静默落到别的实现 |
| 「不适用」与「没做」分开记（`executors.applicable`） | 两者在版面上都是「这一节没有内容」，处置却相反：前者补不了，后者要回去补 |
| npu 剖面跳过 A3.5 时报告要写明代价 | 少了一路交叉验证，失败判定全靠隔离复验。不写的话读者会按 aclnn 剖面的证据强度理解这份报告 |

### 耦合面：14 个既有函数，11 个分派点

**剖面不是靠「到处 if」接进来的，实质逻辑都在只有那条路才够得着的新函数里。**
下面这张表是全部耦合面，改共享代码前对一遍：

| 位置 | 加了多少 | 加的是什么 |
| --- | --- | --- |
| `_node_command` `_device_times` `_run_round` | 18 行 | 后端名与列名前缀由剖面给，不写死 |
| `_case_dtype` `_smoke` | 25 行 | 取不到 dtype 时退到分档轴 |
| `_repo_root` `_find_op_dir` | 15 行 | 认第二种仓形态与算子目录形态 |
| `render` 三处、`make_repro` 两处 | 44 行 | 表头与守卫按剖面取 |
| `case_shape` 四处 | 20 行 | 纯 attr 用例早返回 |

只有那条路够得着的新函数：`_under_test_lib_ok`（74）、`_finish_standalone`（61）、
`_attr_group`（19）、`case_shape` 的三个（47）。**aclnn 路上一行都执行不到**，
因为调用点全在 `custom_opp` / `standalone` / `_GROUP["attr"]` 的另一分支里。

两处已知的味道，改到附近时留意：

| 味道 | 为什么这么写 | 风险 |
| --- | --- | --- |
| `_ACTIVE_PROFILE` 与 `_GROUP_ATTR` 是模块级可变全局，`main()` 设一次 | `_node_command` 也被 `make_repro.py` 调用，那边没有 args；改签名要动更多调用点 | 状态藏在模块里，读代码看不出它何时变。**新写的消费者要么接受显式参数，要么在函数头注明读的是这两个全局** |
| `_repo_root` 从返回 `None` 改成返回 `(None, None)` | 形态要和仓根一起返回，分两次探要走两遍祖先链 | 唯一一处破坏性签名改动。漏一个调用点就是运行时崩，`test_repo_root_callers_all_unpack_the_pair` 盯着它 |

**aclnn 路不变靠的是默认值，而默认值是约定不是机制。**
`tests/test_profiles.py` 末节把加剖面之前的行为逐字钉住：完整的 aclnn 命令串、
七个剖面字段的取值、冒烟的分档轴、`_repo_root` 调用点的解包。
三次故障注入验过它真会响（改 `node_prefix`、改缺字段兜底、改调用点解包）。
**改这几条期望值之前，先确认 aclnn 链路真的要改。**

### 自足工程（ops-sparse 形态）的实测事实

不知道就会把 `build_install.py` 写错。全部实测 2026-09-07，量级与现场见
[ops-sparse-facts.md](../../docs/development/ops-sparse-facts.md)。

| 事实 | 写错的表现 |
| --- | --- |
| `build.sh` 只认 `--ops` / `--run` / `--soc` / `--pkg` 四个，其余落 `*)` 打 `Unknown option` 退 1 | 带上 `--experimental` 或 `--vendor_name` 就构建失败，报错指向参数不指向形态 |
| 装包 **`--install` 必须给**，只给 `--install-path=` 时解压完就退、一个文件不装、**退出码仍是 0** | 后面每一步都在空目录上找 so |
| 装到 `<install-path>/cann/{lib64,include,share}`，**比给的路径多一层 `cann`** | 按给的路径找 `lib64` 找不到 |
| 生效靠 `LD_LIBRARY_PATH`，**没有 opp vendor 层**，`ASCEND_CUSTOM_OPP_PATH` 在这条链路上没有落点 | 设了那两个变量以为生效了 |
| 全仓 `GetWorkspaceSize` 命中 0，导出的是 `aclsparse<Op>` 一段式符号 | 符号核查固定核 `aclnn<Op>GetWorkspaceSize` 时永远退 3 |
| 算子目录是 `sparse/<op>/`，kernel 与 host 在 `arch<NN>/` 那一层下 | 按 `op_kernel/` / `op_host/` 找时一个候选都找不到 |
| npu 节点在报表里的 Device 耗时列叫 `npu_0_Device性能（us）` | 前缀写死成 `pyaclnn` 时这一列被当成标杆，性能数悄悄错位 |
| SOC 到 arch：`ascend910b*` 与 `ascend910_93*` → `arch22`，`ascend950*` → `arch35`，`ascend310p*` → `arch20` | 算子只在某个 arch 下有实现时换机型编不出东西，而构建照样退 0 |

## 必须成对改的地方

| 动这个 | 就要同时看 |
| --- | --- |
| 验收现场的 CWD（现在在 `work/`。**放这儿是因为 `atk_output/` 由 ATK 自己在 CWD 下建、落点改不了**——圈进 `work/` 后现场顶层永远只有 `report/` `repro/` `input/` `work/` 四个目录零个文件，代价是命令里的用例包要写成 `../input/`；现场根叫 `<op>-verify/` 是为了不与用例包 `<op>/` 撞名） | ① `run_atk.py` 找 `function_*.py` 是从 **golden 的父目录**（用例包根）找，不是从 CWD——CWD 里没有用例包，按 CWD 找会让 CPU 标杆执行器静默挂不上；② `--builtin-out` 跟着 `-o` 走，各写各的默认值时 `-o` 挪进 `stage/` 而它没挪，`verdict.py` 会报「基线轮无数据」 |
| `run_cxx.py` 的 `TORCH_TO_NAME` | `runner.cpp` 的 `toAclType`——**两张表必须对称**。runner 认得而这边造不出来的 dtype 永远到不了 runner，表现是**整类用例被跳过**，退出码仍是 0（实测 uint16/32/64 三种一直不在这张表里，Roll 的 uint32 因此整类没跑）；反过来则让 runner `exit(4)` 打死进程 |
| 卡占用判据 | `probe_env.py` 一处实现，A1 的报告与跑测的闸门两处引用 |
| 用例包契约的十四项 | 生成侧同时改，两侧都在真机上复跑 |
| `SKILL.md` 里的跑测命令 | 每条都要带 `--op` |

## 契约性事实

真机上验出来的 ATK / CANN 行为，**不知道就会把脚本写错**。环境是 Atlas A3 +
CANN 9.0.0-beta.1 + ATK 26.8.8。现象与量级类的事实在
[排障事实](../../docs/development/atk-accept-troubleshooting.md)。

### ATK 的行为

| 事实 | 出处 |
| --- | --- |
| **`accuracy_load` 按 `-c` 那个 json 的文件名去 golden 下找目录**：`cases.json` → `<baseline_dir>/cases/`。名字对不上时不报错，每条都记「标杆输出为空」→ 执行失败 | 实测：`-c /tmp/sub012.json` 让 3 条全报执行失败，改名 `cases.json` 后才真正跑到算子 |
| `-c` 指到一个筛完为 0 条的 json 时，ATK 退出码 0、报告 0 条、不报任何错 | 实测 |
| golden 子目录名是**加载节点自己的** `f"{backend}_{name}"`；`--backend aclnn` 的待验收节点在 ATK 里叫 `pyaclnn_0` 而不是 `aclnn_0`，所以内置基线冻成 `pyaclnn_builtin`——重名时加载节点会被改名成 `pyaclnn_0_1`，一条都匹配不上 | `nodes_config.py:90` → `opp_tasks.py:465`、`nodes_config.py:149`，Median 与 Bernoulli 两次验收日志实测 |
| **标杆节点的 backend 不能写死成 cpu**：内置基线的用例包里 `cpu_builtin/` 躺着的是生成侧只给形状用的 `torch.zeros_like` 废数据，比对全错而报告不提示异常。`run_atk.py` 的 `_load_node` 读 `golden/manifest.json` 的 `baseline_dir` 定这两个值 | 实测 |
| `accuracy_load` 是正式任务类型，aclnn 任务显式支持 | `atk/configs/base_config.py:60`、`atk/tasks/task_creator/aclnn_task.py:51` |
| 报告 xlsx 有 summary / failed cases / accuracy false cases / statistic 四张表 | 实测 |
| **逐用例精度判定在加载节点的列上**（`<backend>_<name>_精度通过`），被测节点 `pyaclnn_0_精度通过` **整列是 None** | UpsampleNearestExact1d 实测 2026-08-28 |
| **`failed cases` 与 `accuracy false cases` 可能都是 0 行**，即使 summary 的通过数小于总用例数 | 同上，208/211 时两张表皆空 |
| 精度判定是逐输出张量真比对，int32 走 `torch.equal`；`精度详情` 形如 `{'cpu_0': [{'filename': 'output_0.pt', 'result': False, 'error_info': 'torch.equal failed'}]}` | 实测 |
| 接口层 dtype 拒绝的报错形如 `AclNN_Parameter_Error(EZ1001): Tensor self not implemented for DT_INT8, should be in dtype support list [...]`，**落在 `evidence/accuracy.log`，不在 xlsx 里** | 实测 2026-08-31 |
| Device 耗时在 `statistic` 表的 `<node>_Device性能（us）` 列 | 实测 |
| 绑对了 so 时日志有 `import aclnnXxxGetWorkspaceSize from <路径> success!` | `atk/tasks/backends/pyaclnn_backend.py:246` |
| 任务挂到没写出报告时 `atk_output/` 里没有本轮的 xlsx | 实测 |
| **执行顺序不按 id**：`create_dataset` 4 并发，做完就往 device_run 队列塞 | 实测 41 → 43 → 42 |
| 真凶与连带**都报 507015**，只有措辞不同，报错文本不能当判据 | 实测 case 67（真凶）与 case 72（连带） |
| **连带用例走不到 `[case N][RUN OPP TASK] Start Execute OPP` 这行日志** | 九份真机日志逐份验过 2026-08-31 |
| `--slice_input` 切不切由 `random.Random(case_id)` 定，与批次无关 | `atk/tasks/backends/backend.py:147-159` |
| **切中哪几条可以离线复算**：`rng = random.Random(case_id); should_slice = rng.random() < ratio`。同一个 ratio 下划分完全确定，所以不必为了「知道切了谁」把连续与非连续拆成两轮 | `atk/tasks/backends/backend.py:151-157` |
| **`slice_contiguous` 不落盘**，只活在内存的 `InputDataset` 上，只被 `get_storage_shape` 读。想在报告里分组只能从用例侧复算，回读产物这条路走不通 | `atk/configs/dataset_config.py:62`、`atk/tasks/api_execute/aclnn_base_api.py:140` |
| **`normalize_outputs_for_compare` 只覆盖两对 dtype**：fp8→fp32、(fp16\|bf16) 且 remote 是 fp32。**没有 fp32↔fp64、也没有 c64↔c128**，其余原样返回。任务书要求「fp32 的 golden 用 float64 算」时必踩：`torch.isclose` 两侧 dtype 不同直接抛 `Float did not match Double`，整批比对阶段失败。自带件路要在派生比对器里补这两对 | `atk/tasks/post_process/single_benchmark_compare.py:52-72`，实测 2026-09-10 一轮 324 条全废 |
| **`-p` 收目录与收单文件不是一回事**：收目录时 `register_all_from_dir` 导入目录下所有 `.py`（执行器与比对器都注册），收单文件时只载那一个。冒烟给目录、精度给单文件就会「冒烟过了、全量每条 KeyError」 | 实测 2026-09-10 |
| `device_run` worker 是常驻单进程（`pool="solo"`、`concurrency=1`、无 `max_tasks_per_child`），`on_failure` 既不 reset device 也不重启 worker；`reset_device` 全仓只有 `npu_backend.py` 调，pyaclnn 这条路径没有 | `worker_config.py`。**这是必须做隔离复验的根因。**2026-09-04 起本仓跑测机装的是打过补丁的构建（`pool=prefork` + 异常后回收子进程，上游 [MR !32](https://gitcode.com/Ascend/ATK/merge_requests/32)），连带在这台机上已消失；**上游默认仍是 solo**，所以隔离复验不能删——换一台机器就又需要它 |
| **ATK 存盘时把 `uint16/32/64` 张量包成 dict**：`torch_save_safe` → `sanitize_data` 存成 `{"__type__": "uint32_tensor", "data": ndarray, "shape": ...}`，逆函数是同文件的 `restore_data`（`atk/common/utils.py`，`torch >= 2.3` 才有这段）。**冻结输入 `inputs/<id>/input.bin` 与 golden 的 `output_*.pt` 两处都经过它**，读盘不还原就整类 dtype 静默跳过。**还原要调 `restore_data`，不要手写解这个 dict**——字段名是 ATK 的私有约定，手写一份等于复制进本仓，上游改了这边不报错、只是又整类跳过 | 实测 2026-09-03：Roll 的 uint32 17 条全被 `write_descriptor` 判成「按签名是 tensor，实际是 dict」跳掉，而报告仍按 ATK 轮给 uint32 打 100% ✓。顺带实测 torch 2.10 上裸 `torch.save/load` 这三种 dtype 是通的——**所以这是 ATK 的约定，不是 torch 的限制** |
| **用例包里没有输入张量**，只有 golden 输出；ATK 每次跑测按 `case_id` 重生成 | `dataset_executor.py:135`、`base_dataset.py:91` 的 `torch.manual_seed`，实测 2026-08-31 |
| ATK 运行时的输入 dump **不可靠**：同一算子四次跑测，三次 `input/` 为空，一次只落了 5 条 | 实测 2026-08-31 |
| `atk task --input_data <dir>` 是官方参数，格式 `<dir>/<case_id>/input.bin` | `atk task --help`、`dataset_executor.py:72-84` |
| 精度比较器**可以脱离 celery 与 `RemoteManager` 单独用**：`MixedToleranceBenchmarkAccuracyCompare(CaseConfig(**case)).compute_accuracy_result(...)`，7/7 与 ATK 报表口径一致 | 实测 2026-08-31 |
| **8 个 atk 实例可以同时跑**，各占一张卡、各自独立工作目录，墙钟 31s（单实例 25s）。唯一冲突是 `sqlite_web` 抢 8881 端口，不影响跑测 | 实测 2026-08-31 |
| 一次 atk 启动约 30 秒，**几乎与用例条数无关**；部署坏或用例卡死时停在 `0/N` 不返回 | 实测 |
| **ATK 上游 master 已走在 submodule 的 `bac1aa7` 之前**（2026-09-03 实测多 6 个提交），但两者 `PACKAGE_VERSION` 都是 `26.8.8`——**版本号分辨不出构建，同一性判据是 commit**。装机那份的来源记在 `env.json` 的 `atk.origin` | `git log bac1aa7..origin/master`，2026-09-03 复核。上一版记的「没有更新版本可拉」已过期 |
| `--soc` 由 `torch_npu.npu.get_device_name(0)` 推，A3 是 `ascend910_93`；`--ops` 收蛇形目录名不是 aclnn 接口名 | 实测、`build.sh --help` |

### 算子包的四块怎么接进来

| 事实 | 出处 |
| --- | --- |
| **自定义算子包分四块接进来**：接口 so 靠链接顺序、kernel 靠 `ASCEND_CUSTOM_OPP_PATH`、tiling 靠谁 dlopen、opp 路径靠 `install.json`。**每块都有内置对应件，谁没接谁就是空的，而空的地方用内置，不报错** | 实测 2026-08-31，upsample 与 index_fill |
| **`BOUND`（dladdr）断言只管 aclnn 接口 so 的落点**，不证明 kernel 与 tiling 来自同一个包 | 同上 |
| **`ASCEND_CUSTOM_OPP_PATH` 的正确值就是 vendor 目录本身**：算子包自带的 `bin/set_env.bash` 写的是它、ATK 用的是它，两个算子实测都是它 | 实测 2026-08-31 |
| **待验收包与 CANN 内置会导出同名 aclnn 符号，链接顺序决定调到谁**：`-lopapi` 排在 `-lcust_opapi` 前面时连到内置，5 条 uint8 报 `EZ1001 ... dtype support list [DT_FLOAT,DT_FLOAT16,DT_BFLOAT16,]`，看着像待验收实现不支持 uint8 | 实测 2026-08-31 |
| `dladdr` 直接对导入函数名取址拿到的是**本可执行文件里的 PLT 桩**，必须先 `dlsym(RTLD_DEFAULT, "<符号名>")` 取真地址 | 实测 2026-08-31 |
| **ATK 与独立执行器绑的不是同一份 tiling**：ATK 绑 **CANN 内置**的 `index/index_fill/op_host/`，裸 aclnn 的 runner 绑**待验收包**的 `experimental/.../arch22/`。kernel `.o` 两侧都来自待验收包 | 实测 2026-09-01：plog 里 tiling 打印的行号与两份源码逐行对上（待验收 72/84/115 行 `[OPS_NN]`，内置 +3 行 `[OP_PROTO]`） |
| **机制是 GE 的 `MergeFunctions` 先到先得，没有任何环境变量能控制**：日志措辞可判别，`tiling func registered.` 是真注册、`has been registered.` 是被跳过。C++ 里待验收第 27 个注册抢到槽位，ATK 里内置第 561 个先到。torch_npu 链着整套 GE（`libge_runner`/`libgert`/`libregister`），把内置 op host 更早拉进来 | 实测 2026-09-01。**`ASCEND_CUSTOM_OPP_PATH` 与 `config.ini` 只管扫描顺序**——实测本包已排搜索序第一、内置排最后，仍是内置赢；装进 CANN `opp/vendors` 走 `load_priority` 排第一也一样；`LD_PRELOAD` 反而让待验收错过注册窗口 |
| **两侧判定不一致的根因是搬输入的方式**：ATK 用 torch 搬（`aclnnInplaceCopy` → `TensorMove` kernel），独立执行器用 `aclrtMemcpy`（DMA 不发 kernel）。某类 kernel 缺陷要**同时满足两个条件**才触发——① 紧邻的前一个 AI Core kernel 访问过输入张量的 GM，② 输入数据量级非极小；满足时 100% 复现。ATK 每次都满足条件①，独立执行器从不满足，于是 ATK 必现、独立执行器漏报 | 实测 2026-09-01：给 runner 加一次前置 `aclnnInplaceCopy` 只读输入，178 从 5/5 过变 5/5 挂，硬件错误寄存器与 ATK 逐位相同（`fixp_error0 0x800db`）；换 DMA 读同一块内存不触发，换读无关缓冲不触发。**所以独立执行器不是 ATK 判定的仲裁者** |
| **`ASCEND_CUSTOM_OPP_PATH` 已经带得动 tiling so**，index_fill 上不做显式 dlopen 也绑到待验收那份（`OPS_NN`，注册序与带 dlopen 时逐项相同） | 实测 2026-09-01。与 `standalone-executor.md` 里那条「不 dlopen 就用内置 tiling」（upsample 实测）矛盾，**upsample 那条未复核，dlopen 先留着** |
| 两份 tiling 会算出**不同的 tilingKey**：case 128（N=511 P=1 Q=511）待验收那份算 1、内置那份算 0，发射的 kernel 分别是 `..._1` 与 `..._0`，一个 `sync 507015` 一个通过；其余 12 个 tiling 字段逐字段相同 | 同上，两侧 DEBUG plog 对拍。反向验证：给 runner 加 `LD_PRELOAD=…libtorch_npu.so`，tiling 当场翻成内置那份、key 变 0、用例通过 |
| CANN 9.0.0-beta.1 **内置已有 IndexFill 的 op_host（tiling + infershape），但没有 ascend910_93 的 kernel 二进制** | `strings libophost_nn.so`、`opp/built-in/.../kernel/ascend910_93/` 下无该目录 |
| **不 source `evidence/env.sh` 就跑 ATK，它静默去测 CANN 内置实现**：fp64 用例报 `EZ1001 Tensor self not implemented for DT_DOUBLE`，而待验收接口接受 fp64 | 实测 2026-09-01。对比量测开跑前必须断言两个 `*_CUSTOM_OPP_PATH` 非空 |
| **`storage_shape` 必须照抄 ATK**：没切片时是 view 形状，切了是 `[shape[0]*2] + shape[1:]`。自己另定一套时 aclnn 不报错、内置 kernel 也照跑对，**但自定义 kernel 拿它算 tiling 会算出退化结果** | `aclnn_base_api.py:136`、`acl_wrapper.py:312`，实测 2026-08-31 |
| 显存要按 **storage 形状**给够，不是按 view 的 span——声明 68 个元素只申请 67 个，kernel 照 storage 访问就越界 | 同上 |
| 摘掉 `ASCEND_CUSTOM_OPP_PATH` **不会**静默回落到内置同名实现，仍按自定义包失败 | 实测：只留 `ATK_CUSTOM_OPP_PATH` 时结果与两个都设时一致（0/3） |

### 环境与卡

| 事实 | 出处 |
| --- | --- |
| 社区算子目录不能独立构建，必须回母仓跑 `build.sh` | 实测，三个算子皆是 |
| **母仓与算子工程的版本要配套**，不配套时构建挂在算子目录里而不是母仓里 | 实测 |
| **构建日志的尾部是误导的**：并行编译时尾部多半是别的目标刷出来的告警，真正的首个错误在几千行之前。已改成先报首个错误 | 实测 |
| `ASCEND_TOOLKIT_HOME` 被 set_env.sh 解成带版本号的真实目录，**它的父目录下没有 set_env.sh** | 实测 2026-08-28 |
| `env.json` 的 `npu.device_ids` 是**物理卡号**且不滤占用，`--devices` 要的是 `logic_devices` | 实测：device_ids `[0..7]`，logic `[0..15]` |
| logic device id 只能从 `npu-smi info -m` 的 **Chip Logic ID** 列查，Mcu 行那列是 `-` | 实测 2026-08-31 |
| `npu-smi info -t proc-mem` 必须带 `-i <npu> -c <chip>`；空闲时打 `No process in device.`，被占时打 `Process id:<pid> …`，拿不到进程名时名字整列为空 | 实测 2026-08-31 与 2026-09-01 |
| 进程表里拉起跑测的那层 shell（`bash -c`、ssh 包装、nohup）命令行含 `atk` 与 `--devices` | 实测 2026-08-28 |
| vendor 目录是 `<install-path>/vendors/<vendor_name>_<仓后缀>` | 实测 |
| 母仓 `tests/ut/op_api/` 的 gtest 只调第一段接口、不上 device；`examples/test_aclnn_<op>.cpp` 上 device 但 shape 与数据全写死、结果只 `LOG_PRINT`。**两者都不是可投喂的执行器** | 实测 2026-08-31，index_fill |
| 生成侧没显式构造规模档时，性能子集的 `large` 全量常常是 0 条，调 `-n` 没用 | 实测，三个算子首轮皆是 |

## 目录结构

```
skill/repo-task-atk-accept/
├── SKILL.md              路由器：阶段表、剖面表、A0 那道闸、停止条件
├── references/           11 份按需加载的知识，每个阶段的展开各归一份
└── scripts/              17 个量具
```

脚本间的 import 共三处，都是单向的：`build_install.py` → `probe_env.write_env_sh`；
`verdict.py` → `render`、`make_repro`（A5 一条命令出齐四样产物）；`make_repro.py` →
`run_atk._node_command` 等（复现命令与跑测命令同源）。`render.py` 与 `make_repro.py`
都能单独跑：前者重出报告，后者重出复现包。

两侧的 `probe_env.py` 是**两份不同的脚本**，不是共用件：跑测侧要查 NPU、CANN 与 SoC
并生成 `env.sh`，生成侧不需要。不做双份同步纪律。

## 加东西之前

- 新增 reference 或脚本前先答：**没有它，零上下文 agent 会在哪一步卡住？** 答不上来就不加
- **验证靠冷启动跑 `SKILL.md` 里那几条命令本身**，不是脚本单测。四条能让脚本退出码 0
  而结论错的静默缺陷全是这么翻出来的
- 真机跑出来的事实按上面那把尺子分流：影响脚本怎么写的进「契约性事实」，
  现象与量级进[排障事实](../../docs/development/atk-accept-troubleshooting.md)
- 变更记录写进[架构演进记录](../../docs/development/architecture-log.md)，不在本文件堆栈
