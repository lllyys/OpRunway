# 架构演进记录

每次架构级修改在这里留一条：**改了什么、为什么、留下的约束在哪读**。

现行规则不在这里——在各 skill 的 `CLAUDE.md` 与 `SKILL.md`；这里只答「为什么变成
现在这样」。被后续重建下线的机制只留结论，实施细节不留：那些文件已经不在仓里，
照着读不能用。最新在上。

| 日期 | 一句话 | 约束落在哪 |
| --- | --- | --- |
| 2026-09-10 | 长轮的等待补上崩溃判据与心跳：进程没了而产物不在当场退 3，`pgrep` 排掉自己的祖先（ssh 那层 shell 的命令行里就有 `--pattern` 的值，判据恒真、崩溃永远等满上限），隔离复验逐条打 `已完成 N/M`，`--timeout` 缺省 3600→120 | atk-accept 的 [SKILL.md](../../skill/repo-task-atk-accept/SKILL.md)「长轮起跑与等待」、`scripts/wait_for.py` |
| 2026-09-10 | 性能读数改回任务书口径：中位数与 90%分位并列，`kernel_total_us` 那个平均值降级成兜底。此前 p90 在收编那一步丢掉、自带件泛化集以平均值与判据表的中位数混在同一批算倍率，两处退出码全程 0 | [external-perf](../../skill/repo-task-atk-accept/references/external-perf.md)「报告哪两个读数」，约束在 atk-accept 的 [CLAUDE.md](../../skill/repo-task-atk-accept/CLAUDE.md)「性能」 |
| 2026-09-09 | 造数嫌疑改成在造数那一刻排除（骨架件 `check_sparse`），算子侧失败的处置判据改三分：两边都合法但形态不同时仍是算子侧 | [external-perf](../../skill/repo-task-atk-accept/references/external-perf.md)「算子侧失败的处置」 |
| 2026-09-09 | 性能回落的除数改成调用次数的两个来源，性能采集加现场级排他锁：SpMM 那轮 50 条里 25 条 fp32 被判「没跑出数」、25 分钟串行采样与判据表重叠在同一张卡上 | `scripts/perf_lock.py`、[external-perf](../../skill/repo-task-atk-accept/references/external-perf.md)「现场级排他锁」 |
| 2026-09-09 | 自带件体检回到冒烟之前（A2.4）：两份自带件各报 7 处、误报 0，载入改成照 ATK 的 `-p` 载整个目录 | [kit-acceptance](../../skill/repo-task-atk-accept/references/kit-acceptance.md)「A2.4 先过体检」 |
| 2026-09-09 | 每份 stage JSON 记 `started_at` 与阶段墙钟：SpGeMM 一轮 22 分 29 秒里有 400 秒此前归不到任何阶段头上 | `scripts/stage_clock.py` |
| 2026-09-08 | 两条线都在 sparse 算子上跑通：自带件路（生成侧不参与，标杆当场算）与自产路（原版 S1 到 S4 加 A1 到 A5，性能用自带件脚本） | [kit-acceptance](../../skill/repo-task-atk-accept/references/kit-acceptance.md) |
| 2026-09-08 | 纠正上一条的前提：生成侧不为 sparse 改流程，默认自产用例；必需增量收敛到 `backend` 与 `plugin` 标杆两个字段 | [case-strategy](../../skill/repo-task-case-gen/references/case-strategy.md)「张量声明不能跳过」 |
| 2026-09-07 | ATK 链路拆出两条正交的轴（执行剖面 / 工程形态），sparse 算子按 `backend=npu` 分流；自带件归一进用例包，两条路在 S4 合流 | [用例包契约](case-package-contract.md)、[ops-sparse 事实](ops-sparse-facts.md) |
| 2026-09-07 | MR !32 收到评审，回收判定从任务名改成 device_run 队列；CPU worker 误杀在真机上复现并修掉 | [dev-environment](dev-environment.md) |
| 2026-09-04 | ATK 三条问题提上游（[#28](https://gitcode.com/Ascend/ATK/issues/28) / [#29](https://gitcode.com/Ascend/ATK/issues/29) / [#30](https://gitcode.com/Ascend/ATK/issues/30)，含 [MR !32](https://gitcode.com/Ascend/ATK/merge_requests/32)）；submodule 指向 PR 分支、跑测机换装该构建，端到端耗时 633s→364s | [dev-environment](dev-environment.md) |
| 2026-09-03 | 独立执行器补 uint 存盘兼容与布局两轮；跳过与覆盖缺口进结论，能翻掉「通过」 | [跑测侧 CLAUDE.md](../../skill/repo-task-atk-accept/CLAUDE.md) |
| 2026-09-01 | 两个执行器的判定分歧查到机制（绑的不是同一份 tiling）；开发态文档按「改动时用不用得上」分流 | [跑测侧 CLAUDE.md](../../skill/repo-task-atk-accept/CLAUDE.md)、[executor-divergence](executor-divergence.md) |
| 2026-08-31 | 四个算子冷启动端到端；独立 C++ 执行器并入并补齐四类失真防线 | 同上、[排障事实](atk-accept-troubleshooting.md) |
| 2026-08-29 | 第四条链路七个 skill 并入并统一改名到 `cann-` 域 | [ops-report 契约](ops-report-contract.md) |
| 2026-08-28 | 四件事：冷启动翻出四条静默缺陷、ops-blas 并入、SKILL.md 门禁钩子、生成侧文风重排 | 仓根 `CLAUDE.md`、`.claude/rules/skill-style.md` |
| 2026-08-25 | 按最小闭环重建两个 skill，旧件不迁移 | 两侧 `CLAUDE.md` |
| 2026-08-15 – 08-24 | 被上一条重建下线的那一版（骨架 + 门禁 + 950 单测） | 只留教训，见末节 |

## 2026-09-09 造数嫌疑前移到造数那一刻

上一条给「算子侧失败的处置」写的判据是二分的：换自带件的构造函数复跑，
**仍失败才认定算子侧，通过则是量测件的造数问题**。这个二分漏了第三种情况——
两边造的数都合法，只是形态不同，算子只在其中一种形态上失败。

SpMM 那轮就是这一种：自带件的 `nnz = rows × degree` 必然整除、行长恒定，
判据表的 nnz 除不尽必然两种行长混着，而混着正是任务书要求的。按二分判据，
执行者被导向「那就是我造错了」，从 09:58 查到 10:04 又把行长方差那条路重走
一遍，花掉 8 分钟——**上一条刚写进「范围之外」的那类扫描，被上一条自己的
判据召回来了**。

两处改：

- **造数嫌疑在造数那一刻排除。** 骨架件 `check_sparse` 造完当场核 nnz、
  crow 单调、行内列严格递增无重复、列域，不合法退 3 整轮作废。事后拿别的
  构造函数去对照，结果还要再解释一轮；造完就核，「算子侧失败」当场就是个结论
- **判据改三分**：自带件复跑也失败且本侧自检过 → 算子侧；自带件复跑通过而本侧
  自检也过 → **仍是算子侧**，记「算子在任务书要求的形态上失败」，不追触发条件；
  本侧自检不过 → 量测件的缺陷，这一轮结论作废

同轮补上两个连着两轮踩的坑（`build-deploy.md`）：自足工程的 torch 适配层不走
`setup.py` 时 A2 的自动扩展步会跳过它，交付件里常有编好的产物可直接用；
`acl_base_rt.h` 找不到是 include 顺序把 torch_npu 自带的 ACL 头排在了 CANN 前面，
不是缺文件。以及跑测命令里的路径一律绝对——ATK 子进程的 CWD 在它自己的工作目录。

## 2026-09-09 性能采集的两处静默失真

SpMM 那轮验收 47 分钟，一半以上落在性能采集上。查下来是两处各自都退 0 的失真。

**第一处：回落的除数只认得出一个来源。** `profiler_fallback` 从 Profiler 产物
求和后除以步号列的去重个数。`00583d9` 已经把列名从写死改成按内容探测，但这一轮
撞到的是**列整个不存在**：一次调用只有一个 kernel 的路径，torch_npu 不写这一列。
同一轮 50 条产物里 25 条 fp32 没有 `Step Id`、25 条 complex64 有，前 25 条全部
进了「没跑出数」。

除数本来就是调用次数，步号只是它的一种表达。补第二个来源：数每个 kernel 名出现
了几次——一次调用里每个 kernel 各跑一次，出现几次就是调了几次。两个来源在有步号
列的产物上**逐条等价**（真机 25 条差异 0），各 kernel 名次数不齐时仍报缺。
50 条真机产物复算：旧的 25 条值不变，旧报缺的 25 条全部出数。

代价是执行者当场绕开了这个量具：它自带现查空闲卡分四片并行，绕开之后改成写死
单卡串行，50 条从七八分钟变成 25 分钟。

**第二处：一个现场可以自己和自己抢卡。** `probe_env.device_busy` 查的是设备上有没有
**别人**的进程，靠 `own_pids()` 把自己的进程树排除掉。一个验收现场里前后两条命令
是两棵进程树，谁也不是谁的后代——于是自带件 50 条与判据表 8 个场景重叠在卡 0 上
跑了 8 分钟，中间还并发了十几次最小复现，两边的退出码都是 0。

补 `perf_lock.py`：性能采集期间独占本现场。`run_kit_perf.py` 自己持锁，另起的量测件
用 `perf_lock.py --who … -- <命令>` 包住，不必改代码。拿不到锁退 3 并打印持有者，
**不排队等**——性能轮一跑几十分钟，排队等于把命令挂死在终端里。持锁进程已经不在
时陈锁自动接手。

**EPERM 算「进程在」。** 机器公用，持锁的可能是别人跑的一轮，那时 `os.kill(pid, 0)`
报的是没权限而不是查无此进程；把它当成不在，锁会被抢走，闸等于没有。这条是写测试
时在 macOS 上撞出来的（`os.kill(1, 0)`），真机上同样成立。

锁只管同一个现场内部，跨现场共卡仍归 `device_busy` 与 `free_npu.py` 现查，两者不重叠。

## 2026-09-08 自带件路独立成条

上一条把自带件绕道生成侧：`adopt_kit.py` 归一进用例包，再交给跑测侧 A1 到 A5，
理由是复用既有流程。**代价是验收一个自带件齐全的算子要先跑一遍生成侧，
而生成侧对这条路唯一的贡献是搬文件加冻 golden。** 冻 golden 在这条路上还是负的：
自带件的设计就是 CPU 标杆当场算，冻成静态文件反而与任务方口径隔了一层。

拆开之后是两条并行的线，证的东西不同：

| 线 | 用例从哪来 | 精度证明什么 | 生成侧 |
| --- | --- | --- | --- |
| 自带件路 | 任务方交付的那批 | 开发者达到了任务书写明的交付门槛 | 不参与 |
| 自产路 | 生成侧从接口原型与任务书设计 | 独立验收，覆盖任务方漏掉的场景 | 参与 |

卡住的从来不是生成侧，是跑测侧 A1 只认一种输入形态。A3 本来就有两种节点模式
（当场算 / 读冻结 golden），只是没有第二个入口。`run_atk.py` 加 `--nodes`
之后，拓扑交给自带件自己描述，`golden/` 那三道校验整体跳过，
待验收实现探针、隔离复验、报告与复现包全部照旧复用。

性能这半此前停在运行态。按「算子名遮住句子还成立」这条判据重分了一次：
分片并行、空闲卡现查、Profiler 产物回落、交换格式转换全都泛化，收进
`run_kit_perf.py` 与 `free_npu.py`；只有 adapt 脚本、派生插件与任务书 P 表的
数值留在运行态。**同一条判据也把 `kit_lint.py` 从生成侧挪到了跑测侧**——
它体检的是「自带件能不能在这台机上跑」。

两处判据在真机上被校准，都不是构造的用例：

- torch 扩展的 RPATH 指向工程的 `build/`，优先级高于 `LD_LIBRARY_PATH`，
  进程里加载的是工程那份。待验收实现的产物根因此认三处：`install_root`、
  `repo`、`parent_repo`
- 同一张卡上两个 Profiler 进程会互相踩，parse 报「数据不存在」

冷启动整链 15 分钟，结论与热跑逐条一致，分段耗时见
[排障事实](atk-accept-troubleshooting.md)。


自产路此前没在任何 sparse 算子上跑过（更早那次是**用桩顶替算子**验管道，
而且是另一个算子）。这次在 SpGemm A2A3 上整链 995 秒跑通，翻出三处判据问题：
`inputs/` 用不用按剖面一刀切、隔离复验重判后总结论与正文打架、
待验收实现的产物根少认一个字段。三处都是**热跑时被现场残留掩盖、
冷启动才暴露**的那一类。

## 2026-09-08 生成侧不为 sparse 改流程

上一条的前提错了，而且错法与前两次同源：**拿任务方交付的测试脚手架当算子的性质**。

任务方把一条用例编码成九个整数（格式、dtype、nnz、seed…），矩阵由他们的执行器
按 seed 现场造。据此推出「sparse 用例里没有张量 → 现有流程不适配」，
于是改了 `case_shape` 的四处、加了分档轴、加了归一脚本。

真实接口是 `aclsparseDenseToSparseGetBufferSize(handle, matA, matB, alg, bufferSize)`，
`matA` 是 `aclsparseConstDnMatDescr_t`，裹着 device 指针——**入参里有真张量**。
按 ATK 正常范式声明它，`case_shape` 那套改动一条都用不上。

### 生成侧真正的必需增量：两个字段

| 卡点 | 改法 | 为什么以前没有 |
| --- | --- | --- |
| `check_facts.py` 硬拒 `backend != "aclnn"` | 放开成 `aclnn`／`npu` | 之前的算子都是两段式 aclnn 接口 |
| `check_facts.py` 硬要 `baseline` 是 `torch.xxx` | `accuracy.kind` 加 `plugin` 档，`baseline` 改填执行器注册名 | 之前的算子在 torch 里都找得到语义等价接口；Blocked-ELL 根本没有 |

第二项是 ATK 的正规形态，不是绕路：`DesignConfig` 的 `name` 是 `Optional`，
`api_type` 单独就能定执行器（`design_config.py:449, 456`）。

同时删掉一条基于错误前提的校验（曾限制 npu 剖面下 `non_contiguous` 必须为假）。
`--slice_input` 切的是用例里声明的张量，与后端无关。

### 顺带查清 ATK 的三层分工

用例声明张量 →`DATATYPE_REGISTRY` 定数值怎么造 →`api_type` 定怎么调起来。
中间那层是正式扩展点，自带 `data_coo.py`（造稀疏矩阵）与 `matrix_profiles.py`
（造结构化矩阵）。**输入有强结构约束时不需要把张量从用例里拿掉**，那正是
这一层存在的理由。跳过它要付五项静默代价，判据写进了
`case-strategy.md`「张量声明不能跳过」。

### 性能主次也是反的

原先写「自测走 ATK，对基线表走自带脚本」。逐条比对任务书 3.3 与 ATK 的能力，
六条里五条 ATK 表达不了（分阶段耗时、中位数与 p90、复用描述符再采样、
两个独立计时范围、workspace 峰值与各 Kernel 耗时）。所以主线是任务方的脚本，
ATK 那轮只作绝对耗时的旁证，不参与算倍率。

### 保留但降级的

`case_shape` 的四处、分档轴、`adopt_kit.py` 全部保留，定位改成
「采纳自带件那条支路才用得上」。跑测侧那 434 行不动——那边的差异是真的
（没有 aclnn 接口、没有 opp vendor 层、C++ 执行器拼不出来），与用例长什么样无关。

## 2026-09-07 sparse 算子按执行剖面分流

评估 `aclsparseDenseToSparse` 社区任务时发现，ops-sparse 这类仓与现有 aclnn 链路
差在**两件独立的事**上。此前把它们当成一件，结论错了两次：先判成「工程模式不同
所以跑测脚本要重写」，再判成「要另建一个 skill」。两次都错在同一处——
把「怎么编」与「怎么调」揉在一起看。

拆开之后：

| 轴 | 问什么 | 判据 | 谁定 | 载体 |
| --- | --- | --- | --- | --- |
| 执行剖面 | ATK 怎么把算子调起来 | 有没有 `aclnn<Op>GetWorkspaceSize` 两段式声明 | 生成侧 S1 | `facts.json` 的 `backend` |
| 工程形态 | 怎么编、怎么装、怎么核符号 | 仓根有没有 `cmake/func.cmake` | 跑测侧 A2 探测 | `install.json` 的 `project_kind` |

两轴独立。ops-sparse 恰好是「自足工程 + torch 算子」，合成一个字段也能对上——
**正因为对得上，合并之后就再也发现不了它们是两件事。**

### 用例从哪来：分叉在 S2，合流在 S4

第二个发现是任务方**自带跑测件**。自带件与本仓用例包差的是契约那几项，
不是内容：自带件当场算 CPU 标杆，用例包要冻好的 golden；插件散在任务目录，
用例包按 `function_<op>.py` 找；`facts.json` 与 `perf/` 没有。

所以增量收敛成一个归一步骤 `adopt_kit.py`，替代 S2/S3，冻结仍走 S4。
三个来源（自带件齐全 / 自带件不全补生成 / 全自产）落到同一份用例包契约，
跑测侧因此**不长第二条代码路径**。

### 净增量

| 改动 | 性质 |
| --- | --- |
| `run_atk.PROFILES` 七字段表 + `build_install.KIND_*` | 参数化，aclnn 那条路一个字节没变 |
| `_under_test_lib_ok` 的 `/proc/self/maps` 探针 | 净增。它顶的是 aclnn 剖面里 `_check_loaded_so` 的位置——npu 剖面下 ATK 一个字都不打，不核就是本链路最贵的静默错误 |
| `adopt_kit.py` | 新增，唯一的新脚本 |
| `executors.applicable` 与 `accuracy.group_by` | 净增。前者分开「不适用」与「没做」，后者让纯 attr 用例的精度表不塌成一行 |

### 真机验到哪一步

用自带件的 200 条精度件走完 S2′ → S4 → A3，桩顶替未实现的算子：
归一 200 条、冻标杆 200/200（35s）、跑测 200/200（72s）。
**验的是管道不是算子**，量级与命令见 [ops-sparse 事实](ops-sparse-facts.md)。

地基那一条落实了：**冻结 golden 走 `accuracy_load` 对 npu 节点成立**。
自带件那套是当场算标杆的，两种形态能不能互换此前只有推断。

## 2026-09-07 MR !32 的回收判定改按队列

上一版按任务名判定"设备任务"，评审指出 `celery_run_opp_task` 不是设备 worker 专用：
`base_task._create_execute_task` 按 backend 选队列而任务名不变，CPU 后端落在 `cpu_run`
队列，而 `celery_config` 的 `include` 让每个 worker 进程都注册了那两个信号处理器。
后果是任一条 CPU 用例执行失败都会把 CPU worker 的 prefork 子进程打掉。

改成读 `task_failure` 的 `sender.request.delivery_info["routing_key"]`，只有落在
device_run 队列上才回收；判定函数 `is_device_run_queue` 放在 `worker_config` 里与队列
命名同处一地。下发到 device_run 队列的恰好是 opp、aclnn、dist 三个任务，队列判定是原
任务名单的精确超集，评审指出的 dist 覆盖缺失一并解决。拿不到 routing_key 时不回收。

评审第三条"回收条件过宽"没接受：精度比对是独立的 compare 任务、独立队列，落在
device_run 队列上的 `task_failure` 就是执行本身炸了；`SoftTimeLimitExceeded` 恰恰是最该
回收的情形。

真机复测两组。CPU 侧 8 条强制失败，两份实现各一轮：

| 实现 | `cpu_run` 上的 `task_failure` | worker 回收 |
| --- | --- | --- |
| 按任务名判定 | 8 | 8 |
| 按队列判定 | 8 | 0 |

device 侧同一批 252 条、同一张卡、同一条命令：

| 实现 | 执行失败 | 通过率 | 耗时 | 回收次数 |
| --- | --- | --- | --- | --- |
| `solo`（改动前） | 101 | 57.94% | 85.1s | — |
| 按任务名判定 | 7 | 95.63% | 110.9s | 7 |
| 按队列判定 | 7 | 95.63% | 105.3s | 7 |

## 2026-09-04 行文规范落成可机检的门禁

**问题：** 文档风格靠人记。`skill-style.md` 早就写明禁止「适当处理」这类模糊词，
但没有检查器；行文层面（人称、标题句式）连规则都没有，于是同一个槽位在四份
使用指导里有四种叫法，reference 里混着拟人主语与第一人称。

**做法：** 对照 Kubernetes 与 Google 的文档风格指南抽判据，**每条先在存量上标定
命中量与误报**，误报高的砍掉（禁「请」命中 9 处多在引述的上游原文里，禁「它会」
命中 10 处大半是正常的第三人称）。留下的 10 条写进 `doc_style_lint.py`，
钩子在改文件那一刻实跑并把行号注回上下文。

**Diátaxis 那条「reference 只描述不解释」没有收**：它假设读者是人，看不懂会自己
去翻 explanation；本仓 references 的读者是 agent，不写清「为什么这么定」，
它会自行推翻决定重来一遍。理由记在 `doc-style.md`。

**顺带抓到 4 处存量真实断链**——`SKILL.md` 让 agent「见 xx.md『某节』」而那节不存在，
此前无人发现，因为只有跑到那一步才暴露。引用完整性现在是常驻判据。

**踩到的坑：** 量具第一版把 `golden-task-doc.md` 与 `task-doc-template.md` 也扫了，
改一个标点让 doc-write 的 9 个测试当场红——那两份的字面内容是契约，被解析器按表头
取列、被门禁当「什么叫合格」的定义。现在按文件名豁免，并有测试守着。
第二个坑是半角括号单侧转换造出 `新机（CANN 9.0)` 这种混搭，改成成对转换。

**范围：** 61 份文档（57 份 skill + 4 份 guide）全部通过；20 个疑问句标题改成名词
短语，目录、`「章节名」`引用、锚点同步；机器可执行内容（命令、参数、退出码、路径）
逐字未变，416 个测试全过。

## 2026-09-03：独立执行器的 uint 兼容链与布局两轮

Roll 冷启动验收暴露出一条**七个缺陷串成的链**，任何一处单独修都不够，而且全程退出码 0。

**前三个让 uint32 整类跑不了。** ATK 存盘时把 `uint16/32/64` 包成 dict（见跑测侧
`CLAUDE.md` 的契约性事实），我们三处没接上：读输入不还原、`TORCH_TO_NAME` 里根本
没有这三种 dtype、读 golden 也不还原。修了第一处会卡在第二处，修了第二处会卡在第三处。
`runner.cpp` 的 `toAclType` 一直支持 `ACL_UINT32`——**能力从来都在，缺的是接线**。

**中间三个让报告替它圆场。** 跳过的用例不进 `verdict.json`，`scope` 按条数反推被标反
（222 < 239 → 全量误标成「仅 ATK 判失败的」），非连续轮丢了失败 id。合起来的效果是：
权威路径上 uint32 覆盖为 0，报告里那一行照打 `100.0% ✓`。

**最后一个是布局。** A3.5 的 `--slice-input` 默认 `auto`，跟着 `facts.non_contiguous`
走，于是 239 条全切成非连续，**连续布局一条没跑**。而 A2.6 判了 `shadowed`，ATK 那两轮
测的是被抢走 tiling 的内置实现——连续布局下待验收实现算得对不对，那一轮验收没有答案。
SKILL.md 的 A3.5 命令块里 `--slice-input` 一个字都没提，A3 却反复强调它，执行者照抄就落坑。

修法落在三条可泛化的判据上，没把算子名写进任何 skill 文件：

| 判据 | 落在哪 |
| --- | --- |
| 读 ATK 存的盘一律过 `restore_uint`（内部调 ATK 自己的 `restore_data`） | `run_cxx.py`，输入与 golden 两处 |
| 跑了多大范围由**产出方**落账，下游不按条数反推 | `run_cxx.py` 写 `scope`，`verdict.py` 读它 |
| **跳过 = 没测过**。整类 dtype 跳光或整个布局没跑，都能把「通过」翻成「结论不可用」 | `verdict.py`，报告里标 `—未覆盖` 而不是 ✓ 或 ✗ |

两条防线在真实数据上都走不到（缺口已被修没了），所以各造了一个合成场景逼它触发，
验完即清；A3.5 两轮驱动另在真机上跑了 3 条用例的冒烟。**只验正向不验反向，等于交了
一道没跑过的防线。**

## 2026-09-01：执行器分歧查到机制，文档分流

**分歧不是抖动，是两个执行器绑了不同的 tiling。** ATK 进程里有 torch_npu 把 GE
拉起来，IndexFill 的 tiling 解析到 CANN 内置那份；裸 aclnn 的 runner 解析到待验收包
那份。kernel `.o` 两侧相同，tiling 不同的那份会算出不同的 tilingKey，于是一个通过
一个 aicore 崩。用 `LD_PRELOAD` 把 torch_npu 塞进 runner 可以让它当场翻面，反向验证过。

由此得出的两条要当结论用：**ATK 测的是「待验收 kernel + 内置 tiling」这个混搭对**；
**C++ runner 测的才是待验收包自己的那对**。两者答的不是同一个问题。剩下 178/189
那一类（tiling 逐字段相同仍相反）未定位，见 [executor-divergence.md](executor-divergence.md)。

**顺带发现的量测缺陷：** 不 source `evidence/env.sh` 就跑 ATK，它静默去测 CANN 内置
实现，而且不报错。对比量测的脚本开头必须断言两个 `*_CUSTOM_OPP_PATH` 非空。

**文档分流。** 跑测侧 `CLAUDE.md` 涨到 50 KB，其中一半是「某算子某轮跑出来的现象」，
改脚本时用不上。按一把尺子分三处：改动时不知道就会写错的留在 `CLAUDE.md`
（红线、契约、不要改回去、契约性事实）；现象与量级进
[排障事实](atk-accept-troubleshooting.md)；演进叙事与 `最后更新` 堆栈进本文件。
50 → 26 KB，生成侧同法处理。

## 2026-08-31：四个算子冷启动端到端

按 `SKILL.md` 从零走 Roll / Median / ForeachMulList / IndexFillTensor 的 S0→S4 与
A1→A5，两侧共十处缺陷。**算子是量具不是内容**：四个各压中一条不同的路径（intarray
参数、多输出、张量列表输入、cross_dtype 性能），修法一律落在可泛化的判据上，没有把
算子名写进任何一份 skill 文件。

| 侧 | 缺陷 | 判据落在哪 |
| --- | --- | --- |
| 生成 | `check_facts.py` 漏判「出参是 `aclTensorList*` 时必写 CPU 执行器」 | `facts.json` 的出参 `atk_type`。**eval 探针查不出这条**——调用本身成功，错的是返回值嵌套层数 |
| 生成 | `Roll_constraint.py` 对秩 0 提前 `return`，等于放行 ATK 随机生成的参数 | 秩 0 在 ATK 里根本表达不了，改成如实说明并让 YAML 不放 0 |
| 生成 | `IndexFillTensor_constraint.py` 漏了「value 的 dtype 跟 self」 | 与「列表内 dtype 归一」同类：ATK 逐参数独立抽，文档说「与 X 一致」的都要在约束器里对齐 |
| 生成 | ATK 的 `attrs` 列表不能为空这件事没记 | 进 `yaml-authoring.md`「ATK 表达不了的两种取值」 |
| 跑测 | `_check_output_info` 对张量列表算子误报 | 判据改从 `facts.json` 的出参 `atk_type` 取 |
| 跑测 | `_case_dtype` / `verdict._dtype_of` 只认 dict，张量列表整批判成 unknown | 两处一起改 |
| 跑测 | `build_install.py` 只打日志尾部 | 改成先报**首个**错误，并说清错在算子目录还是母仓公共代码——这两种失败去向完全不同 |
| 跑测 | `run_cxx.py` 缺头只在算子目录所在仓里找 | 母仓路径取自 `install.json`，不猜 |
| 跑测 | `run_cxx.py` 碰上 `aclTensorList*` 直接崩 | 分出 `UNSUPPORTED_KINDS`，退 4 并说明「不是未复现，是根本没跑」 |
| 跑测 | `verdict.py` 抬头报 ATK 那轮的原始数，与同屏另两处不一致（79/252 vs 213/252） | 抬头改用折算连带之后的数，原始数在括号里 |

同批并入 **独立 C++ 执行器**（`--executor cxx`）：读生成侧冻结的输入、复用 ATK 精度
比较器、aicore 崩了重启进程接着跑。补齐的四类失真防线（算子包分四块接进来、tiling 库
由 runner 自己 dlopen、kernel 落点断言取自 Available bin 日志、`storage_shape` 照抄
ATK 口径）已进跑测侧 `CLAUDE.md` 的契约性事实，两条旧结论同批撤回。

顺带确认成立的三件事：`--devices auto` 与 `--isolate-devices auto` 真机上按预期现查
空闲卡；用例包两层结构四个算子全部落地；两个执行器在能对照的算子上结论一致
（Roll 的 126 与 Median 的 7 条）——**后半句在 2026-09-01 被推翻，见上一节**。

没解决的两件：

- **隔离复验的结果逐轮有出入。** 同一批 169 条失败跑两轮得到 28/1/140 与 33/2/134。
  成因是 ATK worker 崩掉的块**整块**按失败报，而哪一块崩不确定。量级一致、逐条不一致；
  已在那种块的打印里写明「没被判定过」，判据本身没改
- **`performance.kind=builtin` 的基线轮很贵。** Median 的基线轮 50 条跑了 1338s 且全部
  执行失败。`run-performance.md` 记着「基线轮卡住是常态」，但没有比 1800s 更早的收口手段

## 2026-08-29：第四条链路并入并统一改名

七个 skill 从 `cann-ops-test` 仓引入（`cann-env-setup` / `cann-950-feature-scan` /
`cann-ops-run` / `cann-issue-report` / `cann-issue-track` / `cann-doc-quickstart-check` /
`cann-doc-tutorial-review`），并从裸动宾名统一改到 `cann-` 域。

改名的三条理由：skill 装进扁平的 `~/.claude/skills/`，`setup-env` 这类名字**撞名即
静默覆盖**；Claude 按 name + description 选 skill，`report-issues` 会被任何「帮我提个
issue」勾住；`ops-test` 不跑测试、`tech-docs-guard` 不是常驻门禁，名不副实。
`repo-task-*` 五个不动——已真机跑通，且它本身就是有效命名空间。

**并进来时五项阻塞缺陷**，第一项最值得记：七个 skill 一个都没登记 `plugin.json`，
表现是**静默不加载**、`/` 列表里看不到、无任何报错。其余四项是 `cd skills/<name>`
路径全错（本仓是 `skill/`，无 s）、产物落进 skill 安装目录、仓根 `pytest` 因
`from scripts import X` 中断（根 `conftest.py` 的 `_prefer()` 要放 `<side>` 本身）、
`scan_repo.py` 缺 sys.path 引导。

**改名中三处会静默出错的地方**：`track.py` 与 `retest_orchestrator.py` 硬编码兄弟
skill 目录（跨 skill 导入运行时才炸）、`_state.py` 把 skill 名烤进产物路径常量
（机器上已有产物变孤儿）。

两条不能一把 `sed` 的：**`ops-test` 在 ATK 链路里是真实的算子工程目录名**，
五份文件必须排除；**被持久化的下划线标识符
一律不动**（`closed_by_track_issues`、`ops_test_args` 等），改了机器上已有的 state 读不回来。

同批做了一轮「照抄不踩坑」的体检。查出的问题有共同形状：**把相对 CWD 的路径写进
「在项目根跑」的命令里**。新增命令时的判据——命令里出现的每个路径，要么是
`<skill>/` 开头，要么是 `<CWD>/cann-ops-report/` 开头，没有第三种。

顺带修掉一个先于改名存在的缺陷：仓根 `pytest` 以
`Plugin already registered under a different name` 中断而分侧单跑全绿，原因是两侧
`tests/` 下的空 `__init__.py` 让 importlib 给两份 `conftest.py` 算出同名插件。
三份空 `__init__.py` 已删，改后仓根 **379 passed + 69 subtests**。

**解耦规则同批改了措辞。** 原规则是「`SKILL.md` 里不许写『先去跑另一个 skill』」，
这条链路七个天然串联，硬套会让 agent 自己瞎猜下一步。改成：业务上有串联就可以指名
道姓地链，但**串联必须落在文件契约上**——被依赖方读一份具体产物文件，找不到就打印
缺什么、去哪生成，而不是假定用户按顺序跑过。契约见 [ops-report-contract.md](ops-report-contract.md)。

**与仓规冲突、按现状保留的一项**：仓规写「不做 TDD，唯一有 `tests/` 的是
`repo-task-doc-write`」，而并进来的六个 skill 带 288 个测试。本轮没删——它们已经存在
且全绿，删掉是净损失；但它们同样证明不了真机行为。

## 2026-08-28：四件事

### 冷启动翻出四条静默缺陷

给跑测侧加报告分层与最小复现包时，按 `SKILL.md` 从零跑了一遍
UpsampleNearestExact1d。四条缺陷（`env.sh` 的 set_env.sh 路径靠推、卡占用按关键词判、
逐用例判定取错了列、复现包只装执行失败）的共同点是**脚本退出码 0 而结论是错的**，
且**前三条都不是这次改动引入的**。判据现在都在跑测侧 `CLAUDE.md` 的「不要改回去」。

单跑脚本、跑单测都发现不了：第一条要有真实的 CANN 装机布局，第二条要经 shell 拉起，
第三条要有真实的 ATK 报表。**验证方式比验证覆盖率重要**——本仓不做 TDD 的理由在这一轮
又验了一次。

### ops-blas 双 skill 并入，开发态文档对齐

本仓从三条链路变成四条。两处是真坏不是文档过时：仓根裸跑 `pytest` 因
`third_party/ATK` 自带 ut 产生 25 个收集错误（`pytest.ini` 加 `testpaths = skill`）；
`test_shared_facts_sync` 永久红——它锁的耦合在 2026-08-25 重建时就删了，锁却留着。

顺带纠了本记录自己的一条错：S11 那条写「推广为仓级 `test_shared_sync.py`」，仓里
没有这个文件，也没有任何提交建过它。

**留了一个待核对项**：blas 两侧的真机事实标着「CANN 9.0.1 实测」共 11 处，
与当时跑测机上记的版本对不上。以真机 `set_env.sh` 所在安装目录的版本信息为准；
核完要回头确认那 11 处标注还成不成立——msprof 的采集导出行为跨版本变过。
（机器档案已于 2026-09-04 移出本仓，见 `dev-environment.md` 开头。）

### SKILL.md 门禁钩子

**起因**：改 case-gen 那一轮跳过了「改 `SKILL.md` 前调 skill-creator」。值得记的不是
漏了，是漏的方式——`skill-style.md` 由仓根 `CLAUDE.md` `@` 导入，三问原文全程在上下文里。
**规则不是没读到，是读到了没执行**，所以「把规则写得更醒目」这条路已经被证伪。

同一轮里绑在「马上要发起的动作」上的三条门禁都执行了，只有绑在「正在编辑的文件类型」
上的这条漏了。措施是 `.claude/hooks/skill-md-gate.py`：`Edit`/`Write` 的 `file_path`
匹配 `skill/<name>/SKILL.md` 时注入 skill-creator、三问、载体法则与体积预算，
**只提醒不拦截**。选钩子不选改措辞，是因为它在动手那一刻触发，不经过模型的任务
自我分类，而分类正是失效的那一环。

两个实现细节：本机没装 `jq`，第一版按官方示例用它取 `file_path`，退出码 127 被吞、
钩子静默什么都不做，改用 `python3`；settings 监听器只看会话启动时已存在的目录，
新建 `.claude/settings.json` 后别的会话没生效就开一次 `/hooks` 或重启。

**验证开发态工具时不要拿交付物当试验台。** 第一次验证图省事拿
`repo-task-doc-write/SKILL.md` 当靶子，改了标题又 `git checkout` 还原——没进仓，
但那个窗口期里只要有一次 `git add -A` 它就跟着进仓了。靶子要用仓外的一次性路径。

### 生成侧文风重排与四算子冷启动

`case-gen/SKILL.md` 58% 是散文、段落中位 103 字、「它」7.7/千汉字（同类中文 skill 是
0.0）。根因不是措辞：把脚本行为和因果解释写成跟在命令后面的段落，主语是刚出现过的
脚本，必然用代词回指。**先改规范再改文档**——`skill-style.md` 加了载体法则表与
Anthropic 官方的必要性三问。重排结果 21.6 → 14.7 KB，散文 58% → 31%。

四个算子零上下文冷启动（UpsampleNearestExact1d / huber_loss / Pdist / remainder），
**质量没下降的硬证据**：UpsampleNearestExact1d 出的用例包与重排前那次逐个数字一致。

翻出 13 项缺陷，11 项存量。两项是重排引入的回归，**同属一类错误：下沉知识时只搬运，
没检查被搬走的那句是不是原地某个词的唯一解释**。

同轮把上一轮延后的五项全做掉，其中「约束器多个轮转周期必须互质」被低估了——
**两份发出去的 `assets/*_constraint.py` 模板自己就带这个 bug**
（`seq % 10 == 0` 必然满足 `seq % 2 == 0`），照抄模板就继承缺陷。另外三项：
`performance.kind` 加 `threshold` 形态（否则「roofline 80%」只能填 `none`，
而 `none` 的语义是「任务书没要求」——要求就此消失）、执行器注册名改**静态**比对
（不 import：import 要 atk 在位，还会把插件副作用带进 dry-run）、`freeze_golden.py`
失败时按 dtype / 秩 / 规模档报分布。

## 2026-08-25：按最小闭环重建两个 skill

**起因。** 三份真实社区任务书（roll、indexfilltensor、median）拿到手后，生成侧一份都
解析不了：`_taskdoc.py` 硬要 `§2.3` 的代码块与 `§2.4` 的双列表，而三份都没有编号章节。
这不是调参能修的——**社区任务书没有固定结构**，解析层的架构假设错了。

**做了什么。** 两个 skill 整体重写，旧件不迁移。

| 项 | 旧 | 新 |
| --- | --- | --- |
| 脚本 | 69 个 / 15875 行 | 9 个 / 1686 行 |
| reference | 35 份 / 8016 行 | 9 份 |
| 测试 | 950+ | 0（验证标准改为真机跑通） |
| 门禁 | 生成侧 20 道 + 验收侧若干 | 各阶段的出口判据，无独立门禁体系 |

下线的机制：`artifact-contracts.json` 骨架、门禁清单查询、作战卡、进度反推、交接包封印、
受控改写、分面拆分、签名对齐、L0-L3 知识分层、行文散文门禁、双份共用件同步纪律。
它们都是为「零上下文 agent 不可信」设计的补偿机制，但**消耗的上下文比防住的返工更多**，
且全部建立在「任务书结构固定」这个已被证伪的前提上。

**新的分工**：agent 解析任务书，脚本校验结果。事实来源按三跳降级并逐项记来源
（`taskdoc` → `opdoc` → `inferred`），任务书换个写法不影响脚本。

**底本**是 ATK submodule 自带的 `skill/atk-operator-onboarding`。我们的真正增量只有
四块：任务书 → 覆盖策略、母仓编译安装部署、golden 冻结与 accuracy_load 比对、精度性能裁决。

**一处与 ATK 自带 skill 相反的裁决。** `atk-quality-guard` 的 NEVER #4 要求「从 C++
源码提取 assert 写约束器」。那个 skill 服务已验收算子的质量加固，目标是不产生假失败；
本仓服务社区算子验收，目标是能发现它错。**用被测算子自己的断言生成用例，写错的断言
永远测不出来**，所以约束只从公开接口面取。

**真机结果**（Atlas A3 / CANN 9.0.0-beta.1 / ATK 26.8.8）：Roll 180 条全过、Median
200 条全过、IndexFillTensor 200 条报出 47 条执行失败而隔离复验后只有 1 条是真的。
这个发现促成了 `--mode isolate` 与连带失败识别——**不隔离就会把 1 条算子缺陷报成 47 条**。

> **「Median 200 条全过」已作废,不要拿它当验收结论。** 那 200 条受本节下面第一个
> 问题影响（`case_config.id` 恒为 0）**全部退化成同一种场景**，压根没测到正常形态。
> 用例包修好后是 160 条，Median 稳定挂 7 条（`97/111/134/135/136/150/155`）——
> 2026-08-31 与 2026-09-03 两套现场、ATK 两轮、独立执行器全量、isolate 单独跑，
> 五条路径结论逐字相同。

开发过程中真机改掉十条 skill 缺陷，判据都已进两侧 `CLAUDE.md`。其中取报告那条值得
单独说：它把 IndexFillTensor 的真实失败判成连带，报告写出「真实失败 0 条」——**一个只
影响错误路径的取值 bug，直接把结论从「不通过」翻成「几乎全过」**。这类 bug 单元测试很难
覆盖，它只在「任务挂到没写出报告」这个真实故障态下触发。

**另外两处只有真机能发现的问题：**

- `case_config.id` 在 `after_case_config` 里恒为 0（id 是钩子跑完才赋的）。照 ATK 自带
  skill 的写法用 `id % N` 分批构造场景，结果 Median 的 200 条**全部**变成退化维场景。
  条数、dtype 分布、门禁全绿，只有把秩分布打出来才看得见——**条数对不代表形态分布对**
- 冒烟门禁原本是「执行失败一条都不许有」。IndexFillTensor 冒烟 29/30 成功、1 条失败就
  被拦死，而那 1 条正是要测出来的算子缺陷

**规则太严和太松一样有害**——这两条都是把本该报出来的缺陷挡在了报告之外。

## 2026-08-15 – 08-24：被重建下线的那一版

那一版的形态是「骨架 + 20 道门禁 + 950 个单测 + L0-L3 知识分层 + 交接包封印」，
2026-08-25 整体下线，实施清单不再保留（脚本都已不在仓里，照着读不能用）。
留下来仍然成立的几条，都已落在现行文档里：

| 教训 | 现在在哪 |
| --- | --- |
| ATK 的签名自检按算子名 `grep` 磁盘上的同名头文件，社区算子与官方基本都重名，必踩 | 跑测侧 `CLAUDE.md`「为什么要 `ATK_CUSTOM_OPP_PATH`」 |
| 社区算子与 CANN 内置同名，按算子名逐级搜 `.so` 搜错就是自己跟自己比，报告 100% 通过而什么都没验 | 生成侧 `builtin-baseline.md`；跑测侧的链接顺序与四块绑定 |
| 「前面全绿」说明不了任何事，内置真值这条路必须有反证实验（故意改坏一条真值，那条必须变 Fail） | 生成侧 `CLAUDE.md` |
| 拿内置当真值时结论只能写「与内置逐位一致」，不能写「精度达标」——内置本身没被这轮检验过 | `verdict.py` 的 `conclusion_kind` |
| 覆盖率的分母不能由 agent 手写：median 七次验收 rank 写过三种，用例数 76~500，覆盖率次次 100% | 生成侧的钉死轴取值 |
| 验收范围只由精度性能目标划定，语义形态默认不构造 | 生成侧 `SKILL.md` 工作边界 |
| inf 判定：基线含 inf 直接通过；待测含而基线没有算失败；两侧都含算通过（不检查符号与位置） | 生成侧 `experimental_standard.md` |
| 平级双目录（S11，2026-08-24）：两个 skill 各自自足，安装态与开发态同构 | 现行目录结构 |

## 各 skill 的变更记录

### repo-task-atk-accept

| 日期 | 改了什么 |
| --- | --- |
| 2026-09-09 | 自带件路补 A1.5 用例扩展，条数与 dtype 两个轴一次问完：条数走自带件自己的生成脚本、dtype 走 `expand_kit_cases.py`，只改用例清单不碰自带件；`facts.kit.dtype_map` 记 dtype 名到 attr 编码的对应。**扩展排在 A2.4 体检之前**——体检的修复脚本会把 `outputs` 删掉，而 dtype 轴要读它 |
| 2026-09-01 | 查实两个执行器的分歧机制；文档按「改动时用不用得上」分流 |
| 2026-09-09 | 自带件路从「用例包路的分支」提成独立主干：`verdict.py` 认 `facts.kit.cases` 不再要伪造用例包，`kit-acceptance.md` 补齐 A1–A5 命令；**流程改成先跑再修**（冒烟提到体检之前，体检降级为跑通后查静默类）；冒烟失败由脚本摘真因给去向（六条实测特征串）；A2 顺带编 torch 扩展并写进 `PYTHONPATH`；`-c` 相对路径在子目录轮失效修掉 |
| 2026-09-09 | `SKILL.md` 26.4 KB → 9.1 KB：A1–A4 展开下沉、删末尾全量清单；新增 `facts_schema.py` 当 `facts.json` 唯一真相并配防漂移测试；`kit_lint.py` 打出装机 CaseConfig 收什么、每条标「冒烟看不看得见」 |
| 2026-09-01 | 精度分连续与非连续两轮各自报通过率；隔离复验加 `--slice-input` 必须同口径；空卡判据补上进程名为空的外来占用；判定行先落地再问 CANN 原因；归属判据补「单独复跑」 |
| 2026-09-01 | 两执行器判定不一致**结案**：根因是搬输入的方式（ATK 走 `TensorMove` kernel、独立执行器走 DMA）。A3.5 判据改为「仅 ATK 失败 → 仍判缺陷」，独立执行器从仲裁者降为最小复现器 |
| 2026-08-31 | `--devices` / `--isolate-devices` 改默认 `auto` 现查空闲卡；隔离单批超时统一到 1800s；构建失败先报首个错误并区分错在算子目录还是母仓；张量列表算子三处适配；四算子冷启动 A1–A5 全程跑通 |
| 2026-08-31 | 独立执行器补齐四类失真防线；`storage_shape` 改回 ATK 口径；opp 路径探针连同候选表删掉（误诊留下的）；撤回两条旧结论 |
| 2026-08-31 | 新增 `--executor cxx` 独立 C++ 执行器 |
| 2026-08-31 | 卡占用检查改问设备本身，判据从 `probe_env` 出、两处同源；A1 改报空闲卡号 / 被占卡号 |
| 2026-08-27 | 验收现场改四分区、CWD 挪进 `work/`；新增 `render.py` 与 `make_repro.py`；`--builtin-out` 跟着 `-o` 走 |
| 2026-08-27 | 性能对比按规模档出加速比；cross_dtype 改逐对比值，一对缺一条整对剔除 |
| 2026-08-27 | 删掉冒烟阶段收成 A1–A5；so 绑定改成每轮自动断言；超时改停滞检测、总时长 7200→1800；`run_atk.py` 加 `--op` 与卡占用检查 |

### repo-task-case-gen

| 日期 | 改了什么 |
| --- | --- |
| 2026-09-09 | 载入预算清账：`SKILL.md` 24.3 KB → 8.4 KB，S1–S4 展开下沉到各自 reference（S3/S4 与用例包布局新建 `generate-and-freeze.md`）；`case-strategy.md` 里 9.9 KB 的规模档独立成 `size-bands.md`；S2 的四份 reference 在主流程表里拆成四行，一行一个去处；删掉末尾的 reference 全量清单 |
| 2026-08-31 | 用例包收敛成两层：根目录只留跑测侧读的六样 |
| 2026-08-31 | S4 顺带冻结输入张量到 `inputs/<id>/input.bin` |
| 2026-08-28 | `SKILL.md` 按载体法则重排，三块知识下沉 references |
| 2026-08-27 | `perf/manifest.json` 落盘逐条规模档与 cross_dtype 配对表 |

## 待验证（下次上机时做）

| 项 | 怎么验 |
| --- | --- |
| 精度非连续轮 | 2026-09-01 新增的第二轮与 `--slice-input` 隔离口径，代码已合、**真机没跑过** |
| `performance.kind=threshold` | 两侧契约变更，未在真机复跑，要跑一个 threshold 算子确认报告那一节 |
| `cann-issue-report` 的 `submit` 子命令 | 只做了参数与接线的冷启动验证，**没真发过 issue**，要在真实上游发一篇 |
| `cann-ops-run` 退出码 3 的闸门链路 | 跑一轮真实算子产出非空队列，确认 `faq_lookup.py` 与 `failure-followup.md` 衔接得上 |
| 两个文档体检的产物落点 | 去掉 `cd` 后台账与报告是否都落在项目根同一处 |
| 七项契约中标「静默」的四项 | 空 CWD 冷启动按链路顺序跑一遍——退出码 0 不构成证据 |
| 2026-09-09 这批的四处 | A2 编扩展后 `env.sh` 有没有 `PYTHONPATH`；冒烟失败屏幕上有没有「真因/去向」；体检 A 类有没有打出装机 CaseConfig 收什么；`verdict.py` 还要不要补输入侧元数据 |
