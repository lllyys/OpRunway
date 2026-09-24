# 0923 solver 批次验收上下文

本文件是 worktree `solver-accept-0923` 的起点材料，蒸馏自 2026-09-18 至 09-21 的分析
session（transcript：`~/.claude/projects/-Users-ll-Desktop-workspace-ascend-OpRunway/
826592e8-f7b9-479e-93ae-29230706e6f6.jsonl`）。文中标「已核」的条目在本 worktree 里
重新验过数，其余是该 session 的结论，未复核。

**2026-09-21 订正过一轮**：Codex 八维评审（thread `01a0c318`）指出多处与原文不符，
逐条复核后已改，清单见文末「订正记录」。

参考物本地位置（只读）：

- 任务批次：仓内 ignored `repos/community_task/9月/昇腾社区线上发放0923`
  （2026-09-22 自工作区根挪入；本地 HEAD `b8e6d22` @ 09-15，远端已到 `05a8c04d`，**本地落后**）
- 目标仓：`~/Desktop/workspace-ascend/ops-solver`（`gitcode.com/cann/ops-solver`）

## 1. 这批任务是什么

0923 是昇腾社区算子任务的一次周度发放，整批都是 ops-solver 稠密线性代数。七个目录里
只有六份任务书——第七包「一般矩阵特征值(950)」只有 `xgeev` 的竞品基线，任务书从来没写过。
（已核：27 个算子目录、6 份 `*_task_doc.md`。）

六份任务书是同一个模板批量生成的，八个一级章节逐字相同，只换算子和规格：

| 任务书 | 交付接口 | 接口数 |
| --- | --- | --- |
| `Atlas950_Sgetrf_Sgetrs_Sgetri_SgetriBatched_SmatinvBatched` | LU 族实数 | 5 |
| `Atlas950_Cgetrf_Cgetrs_Cgetri_CgetriBatched_CmatinvBatched` | LU 族复数 | 5 |
| `Atlas950_Spotrf_Spotrs_Spotri_SpotrfBatched_SpotrsBatched` | Cholesky 实数 | 5 |
| `Atlas950_Cpotrf_Cpotrs_Cpotri_CpotrfBatched_CpotrsBatched` | Cholesky 复数 | 5 |
| `Atlas950_Sgeqrf_Sorgqr_Sormqr_Xlarft_SSgels` | QR 实数（含 `Xlarft`） | 5 |
| `Atlas950_Cgeqrf_Cungqr_Cunmqr_CCgels` | QR 复数（无 larft） | 4 |

合计 29 个核心接口，另加 17 个 `_bufferSize` 伴生函数，共 46 个 API 入口。

三条粒度规矩决定了验收怎么组织：一份任务书对应社区任务系统里的一个任务，五个接口
「作为同一社区任务一并交付，不允许拆分验收」；§5 又要求同族接口走同一个 PR 或一组
关联 PR，不得只合入部分；每份任务书一张 12 条 `P-xx` 的性能表横跨全族，12 条全达标才收。

**验收粒度是族，不是接口。**

## 2. 交付形态与判据

交付形态是 **Kernel 直调**，六份任务书 §2.2 逐字排除了另外两种：「使用 ops-solver
Host C API + AscendC/CATLASS Kernel 直调工程模式（非 aclnn 两段式、非 PyTorch 接口）」。
接口是 handle 式 C API（`aclsolverCreate`/`SetStream` + 裸 Device 指针 + 同名
`_bufferSize` 查询），返回 `aclsolverStatus_t`。Python 只在测试侧造 golden 时出现，
不构成交付物。

判别方法：任务书对标 cuSolver/cuBLAS/cuSPARSE 这类句柄式 C 库的就是 Kernel 直调，
对标 `aclnnXxx` 的才是 aclnn 两段式。

六份判据逐字一致：

- 精度：rtol `2^-10`、atol `2^-16`、matched_ratio `0.99`、max_abs_error `1e-2 或 32·ULP`；
  复数按实部虚部分别作 FLOAT32 判定。golden 走更高精度 CPU 参考：实数用 float64，
  **复数用 complex128**（C-QR §3.2.1 原文点名 `complex128` 的 `qr`/`lstsq` 或
  `zgeqrf`/`zungqr`/`zunmqr`/`zgels`）。
- 性能：`T_NPU ≤ T_A100 / 0.8`，预热 ≥10 次、采样 30 次、**取中位数**。
- 内存：workspace 不超 `bufferSize`；批量接口额外内存不超 A100 对应接口的 2 倍；
  100 次 create/destroy 后回到基线。
- 语义：列主序、全 Device 指针、`lda` 支持合法 padding、`info` 真实写回三段语义、
  重复执行 bit-wise 一致、`matinvBatched` 在 n 超界时必须报错而非静默转调。

## 3. 包里给了什么、没给什么

给的只有竞品金标的采集手段，27 个算子目录各一套：`*_bench.cu`（cuSolver/cuBLAS 测速
程序）、`build_run.sh`（nvcc 编一次跑一次）、`cases.json`、`bench_result.json`、
`run.log`、`compile.log`，另有 4 份 `改动报告*.md`。用例共 5638 条（已核），
`compile.log` 27 个全是 0 字节（已核）。

没给的要逐项点名，因为这决定了验收的输入缺口：

- 零个 Python 脚本、零个 csv、零个 yaml。别的批次都有（aclblas 给 `gen_csv.py` +
  两个 verify + `gpu_baseline.csv`；0902 的 aclsparse 给二十多个 py 和 ATK yaml）。
- 任务书点名的六个 `*_family_950_testCase/` 自测用例目录**全部不存在**。
- `pics/` 缺失，每份任务书末尾四张环境申请截图都是死链。
- NPU 侧什么都没有：无 gen_data/verify_result、无 CMakeLists、无 kernel 骨架、无 ATK 用例。

**`cases.json` 是性能规格集，包里没有满足任务书要求的精度套件。** 字段只有形状和属性
（m/n/lda/nrhs/batch/uplo/trans…），没有期望值、没有容差列、没有 golden 路径，也没有
用例名——所以 BLAS 线那种 `TC_PF_` 前缀分流在这里无从谈起。

措辞要卡准：**缺这些字段只说明它不是现成的精度用例**，不等于这批 shape 不能用——
由 shape 造输入再算 CPU 参考是可行的，缺的是参考与判定，不是规格。

`check: true` 不是精度判据，它是测速守门员：27 个 bench 都查 `info` 非零即失败，
其中 21 个另算一个残差、阈值统一 `1e-2`，残差超标就把该条 `perf` 置 null。它比的是
自洽（结果代回原方程），任务书 3.2 要的是更高精度 CPU 单标杆（实数 float64、
复数 complex128）逐元素比。

## 4. 竞品基线的五处硬伤

这批数据当 `T_A100` 用之前，五个问题得先有说法：

1. **无法建立逐结果来源。** 门禁写 A100，而四份改动报告里**两份**（`cpotrsbatched`、
   `cunmqr`）写 `平台: H100 / sm_90 / CUDA 12.6`，`build_run.sh` 默认仍是 `ARCH=sm_80`。
   27 份 `bench_result.json` **无一**记录 GPU 型号、驱动或 CUDA 版本，只有 1 份
   （半途快照的 `sgetribatched`）带 `captured_at_server_time`。结论只能是「逐结果来源
   无法建立」，**不能断言全批都来自 H100**——编译命令用 `sm_90` 不等于每张结果表都在那跑。
2. **统计量对不上。** 采集是预热 5 次、`iters: 25`、输出 min/max/avg，全套代码里
   **没有任何地方算中位数**；任务书要的是预热 ≥10、采样 30、报中位数。
3. **门禁规格大多不在用例集里。** S LU 包 12 条 `P-xx` 按全参数逐条比对，**只有 3 条
   命中**（P-01、P-02、P-04）。P-05 的 shape 在（n=4096、nrhs=32），但该 shape 在
   `sgetrs/cases.json` 里只有 `trans=1`（即 T），而 P-05 要 `trans=N`，不算命中。
   P-07/P-08 是单矩阵 `getri`，连 bench 目录都没有；四条批量行全部零命中；
   `smatinvbatched` 用例 n 最大只到 31，P-12 的 n=32 边界没有覆盖。
4. **`gels` 有 137/243 条被 bench 自己的残差口径判失败**（已核：S/C 各 137 条 `perf`
   为空）。bench 用 `‖A·X−B‖/(‖A‖‖X‖+‖B‖) > 1e-2` 判失败，而超定最小二乘的残差不该按
   近零方程去判。**但「137 条全部是误判」尚未独立证实**，需要用高精度 `lstsq` 逐类复核
   失败原因，不能只凭「残差本来就是 O(1)」一句带过。
   S-QR 的 P-11（`SSgels m=4096, n=1024, nrhs=8`）**根本不在 `sgels/cases.json` 里**
   （已核：该表零条 `m=4096,n=1024`），所以它是「规格未覆盖」，不是「被误判丢了耗时」。
5. **两个算子零耗时。** `sgetribatched` 与 `cgetribatched` 各计划 **208 条**用例，
   快照里分别只有 153 / 145 条结果（`partial: true`、`running_case: 154`），且这些结果
   `perf` 全空（已核）——采集跑到一半被抓下来，而 perf 只在全部跑完时才写。
   计划条数与已有结果数是两个量，别混称。`Xlarft` 连 bench 目录都没有，单矩阵
   `getri` 也没有。

## 5. 目标仓 ops-solver 的现状

master 上只有 7 个算子（已核：`cgetrf`、`cgetri`、`cgetri_batched`、`cheevj`、
`cmatinv_batched`、`sgetrf`、`sgetri`），四个口径（src、头文件、docs/zh、test）
完全对齐。0923 要交的 `Sgetrs`、Cholesky 全族、QR 全族、`Xgeev` 一个都还没有。

六份任务书的上游在 ops-solver 的 `origin/task` 分支 `docs/` 下，与发放包逐字节相同；
同一提交里还有一份 Atlas 800I A2/A3 版本（交付 4 个接口，明写不交付单矩阵 `Sgetri`），
**没进任何发放批次**。

测试形态两代，0923 要照新的那代写：

- 执行契约是 `bash build.sh --ops=<op> --run` 严格三步（`gen_data.py` → `<op>_test`
  → `verify_result.py`），三个入口**都不传参**，所以 `--run` 只跑得到默认 shape。
  仓里的 `--run` 是冒烟测试，不是验收测试套。
- 二进制名硬拼 `<op>_test`，路径相对仓根硬编码，`data/{input,output,golden}` 跑完即删。
- 三种骨架：因子重构式 2/7、乘回单位矩阵式 4/7（最多）、`cheevj` 的多门禁式 1/7
  （三个可执行：`_test` / `_contract_test` / `_benchmark`）。`_benchmark` 已经是中位数
  口径，**且 warmup 与 repeat 是命令行入参**——`PrintUsage` 写着
  `<device> <n> <jobz> <uplo> <warmup> <repeat>`，先前记的「预热 2 / 采样 7」只是某次
  调用的取值，不是它的上限。
- 29 个接口按先例分三档：**8 个有现成同类可抄**（getrf S/C 走骨架 1；getri、
  getriBatched、matinvBatched S/C 走骨架 2）、**8 个思路可改**（potrf、potrfBatched、
  geqrf S/C 把重构残差换成 `F·Fᵀ−A` 或 `Q·R−A`；potri S/C 加 uplo 与对称补全）、
  **13 个零先例**（getrs、potrs、potrsBatched、gels、orgqr/ungqr、ormqr/unmqr、Xlarft）。
- 仓里的容差（`eps=1e-3`、`RELATIVE_TOL/ABSOLUTE_TOL=5e-3`、`ERROR_TOL=1e-4`）全不合
  任务书，且 `sgetri/gen_data.py` 的 golden 是 float32 的 `np.linalg.inv`。
  **骨架可以抄形，判据必须重写。**

## 6. 按方法论走一遍

[accept-line-adoption-method.md](../accept-line-adoption-method.md) 的摸底七问，本批的答案：

| 问 | 0923 的答案 |
| --- | --- |
| 谁填了槽 | 任务书、性能规格由上游与任务方填；harness 待开发者交；我们只在 accept 出场 |
| 交付形态 | kernel 直调 + handle C API；§2.2 原文「非 aclnn 两段式、非 PyTorch 接口」 |
| 验收粒度 | 族级，五接口不可拆，12 条 `P-xx` 全达标才收 |
| 判据 | rtol 2⁻¹⁰ / atol 2⁻¹⁶ / matched_ratio 0.99；golden 实数 float64、复数 complex128 |
| 用例 | 随包 5638 条全是性能规格；没有满足任务书要求的精度套件 |
| 工程形态 | 无 GTest、无 `csv_loader.h`、全仓无 `.csv`；`test/` 入口可传参但各算子各写各的 |
| 标杆 | **第三类：要求开发者在 A100 实测填入**，表内为待测占位；随包数据口径不合 |

落差按三层分：

- **L0 能填**：registry profile 的十二字段基本可按 ops-solver 普查实例化。
- **L1 是主战场**：依赖链准备段不计时、原地算子每轮要恢复输入、workspace 与三条内存门、
  `info`/`ipiv` 语义、族级不可拆。
- **L2 不在我们的量具上**：任务书要 NPU kernel 耗时，msprof 报的正是它。落差在随包竞品
  数据（cudaEvent 包 API、预热 5、采样 25、无中位数），**结论是那批数不能当 `T_A100`**。

harness 档位：

- **档① 消费开发者的 harness，不成立。** 它尚未交、形态未知，且 ops-solver 没有 frame
  强制形态，各人各写；golden 那一侧也不具备「一眼审完」的条件——分解类判法任务书里可
  二选一，八个批量接口没有 CPU 标杆命名。
- **档② 与档③ 都可行，而且工作量相当。** overlay 装得进去（`test/CMakeLists.txt` 按名
  `add_subdirectory`，追加目录不改既有文件，`TEST_NAMES` 由 `build.sh:179-180` 从 `--ops`
  传入），但仓里没有 frame 可绑，装进去的 per-op 文件要自带驱动、golden 与比较——那已经是
  一个完整执行器。**两档的差别是部署位置，不是工作量。**
- **已裁定走档③**（见第 8′ 节）。取舍轴是「借不借 ops-solver 的构建系统」：借能省掉自配 CANN
  编译链，代价是受其执行约定约束（`--run` 三步不传参只跑默认 shape，要传参就得绕过它），
  且工程结构一变 overlay 就要重新适配。不借则只依赖公开头与 `libops_solver.so`，边界最稳。

**注意「给 ops-solver 补一套 frame」不是候选方案**：那要仓的维护者合入，不是我们能单方面
执行的，只能作为上游建议提出来（权限模型见
[acceptance-roles.md](../acceptance-roles.md) 第二节）。

## 7. 六件任务包用在 0923 的差异

六件契约见 `plugin/docs/development/blas-case-package-contract.md`。甲必须走它，丙与乙
可以选择走或不走。逐件差异：

| 件 | blas 现在怎么产 | 0923 差在哪 |
| --- | --- | --- |
| `<op>_test.csv` | 四块 `TC_L0_`/`TC_PW_`/`TC_ED_`/`TC_PF_`；精度期望集是非 PF 行 | 四块可沿用，但包粒度冲突（见下） |
| `gen_csv.py` | FACTS 加冻结的公共代码区 | 要 schema v2（`golden.kind=harness`），三种判法怎么进 FACTS 是新问题 |
| 两个 verify 脚本 | 从模板渲身份常量与运行协议 | 采样协议不同（准备段排除、逐样本恢复输入、workspace 复用），模板要扩 |
| `README.md` | 投影接口、列、golden 与 verify 契约 | 随上面走 |
| `gpu_baseline.csv` | 投影性能键、GPU 值与口径元数据 | 见下 |

**最硬的一处是包粒度。** 六件契约定义的是**单算子包**（「恰好一个 `<op>_test.csv`」），
0923 的验收粒度是**族**（五接口不可拆，12 条 `P-xx` 横跨全族）。要么拆五个包再加跨包聚合，
要么改包定义。这跟采集协议无关，是契约层的结构冲突。

### `gpu_baseline.csv` 只能填门禁基线

**随包那 4944 条参考耗时不得填进去。** 三条全不合门禁要求：口径是预热 5 / 采样 25 /
min-max-avg（门禁要 10/30/中位数）、27 份结果无一记录卡型与 CUDA 版本、P 表严格命中
只有 3/12（S-LU 已核）。填进去就等于把摸底数据变成门禁。

正确填法：

- 只填开发者实测交付的 `T_A100`（12 条/族）。
- 开发者未交之前，按契约「没有性能基线也保留只有元数据和表头的 `gpu_baseline.csv`」——
  **空表头，不是填参考值**。
- 要拿那 4944 条做摸底对比，走另一个载体，明确标成非门禁。

### 随包的采集脚本，验收侧不跑

`build_run.sh` 与 `*_bench.cu` 是采集手段，跑在 NVIDIA 机器上。任务书把这活派给开发者：
「开发者须在 A100 上实测填入 `T_A100`，并在报告中给出 CUDA 版本与采集脚本」。

验收侧要做的是**核开发者交的 `T_A100` 怎么来的**（CUDA 版本、采集脚本、采样协议），
不是自己去采——我们也没有 A100。

**连带结论：** 开发者交件之前，性能侧只能出摸底结论。精度侧不受影响，golden 是自产的，
不依赖任何 GPU 数据。

## 8. 比选过程：0923 走哪条验收线（已裁决，见 8′）

BLAS 线（`repo-task-blas-accept`）的硬前提 ops-solver 不满足：没有 CSV 驱动的 GTest
harness、没有 `test/frame/csv_loader.h`、全仓无任何 `.csv`。**卡点在 A1 不在 A2**——
`csv_loader.h` 是 `accept.py:543-544` 的 `hard=True` 检查；三文件缺失在 A2′ 只记警告。
`build.sh` 不支持 `--device` 这一条不算硬障碍：`sparse_frame` profile 已支持不传
`--device`、运行时映射设备，属可配置差异。

即便补上 harness，用例模型还有四处装不下：getrs 要先跑 getrf 造因子且那次不计时
（CSV 一行表达不了「先跑 A 再计时 B」）、`_bufferSize` 与三条内存门 BLAS 线没有这一档、
`info` 三段语义与 `ipiv` 并列主元允许取不同下标、以及五接口不可拆与「一个任务包恰好
一个 `<op>_test.csv`」的定义冲突。还要加上一条先前漏掉的：**原地写回的算子每轮都要恢复
输入**，否则预热与采样吃的是上一轮的输出（0923 竞品 `sgetrf_bench.cu:218` 正是这个病）。

**性能口径不是落差——这一条 2026-09-21 经 Codex 评审订正。** 任务书原文是「NPU **kernel
耗时**满足 `T_NPU ≤ T_A100 / 0.8`」，「每轮须 Device 同步后计时」是计时方法不是统计口径；
msprof 报的正是 kernel task duration，**两边本来就是同一个物理量**。落差在随包竞品数据
（cudaEvent 包住 API 调用、预热 5、采样 25、无中位数），结论是那批数不能当 `T_A100`。

**标杆的性质也订正了：不是「只能引用的死表」。** 任务书写明「开发者须在 A100 上实测填入
`T_A100`，并在报告中给出 CUDA 版本与采集脚本。下表 `T_A100` 列为待测占位」。
所以这批不属于 `external-perf.md` 处理的那一类（标杆由任务书给死、只能引用），
责任在开发者实测；随包 `bench_result.json` 是参考而非答案。

能复用的是最贵的那部分，且与算子族无关：A1 环境门（工具链/CANN/设备探测、msprof
查找顺序、严格单卡门与 auto 选卡闭合校验）、runtime 包 + manifest + 哈希、A5 机械裁决
与三类产物布局、复跑归因、停止报告模板。`HARNESS_REGISTRY` 也是设计好的扩展点
（已从 `blas` 泛化到 `sparse_frame`，schema v2 有 `harness_profile` + `harness_overrides`），
但 profile 只解决「认得出这是哪个域」，解决不了上面的模型缺口。

ATK 侧可复用的件要说准：`assets/perf_harness.py:179` 本来就用 `statistics.median`
（算不了中位数的是 ATK 自己的 `performance_device`，两者别混）；但 `run_cxx.py` 是按
aclnn 两段式写死的——它搜 `aclnn…GetWorkspaceSize`、要求尾参 `uint64_t*` /
`aclOpExecutor**`，handle 式 C API 要新写后端。报告字段里 `matched_ratio` 只出现在说明
文字中、未在结果构造里输出，`max_abs_error` 先转 float64、承载不了复数分部判定——
**不能凭字段名认定现成可用**。

四条候选路线（第四条由 Codex 评审补入）：

1. 给 ops-solver 补 CSV+GTest harness 与 frame，走 BLAS 线——要动上游工程形态，
   模型缺口仍在。爆炸半径最大。
2. 走 ATK 线 + external-perf——要新增 handle C API 后端、改输入表示与比较器，
   远不止「补依赖链与中位数」。
3. 新起 `repo-task-solver-accept`——按需复用已独立的工具与证据约定，run 链自写。
4. 抽共享核心 + 按域 skill——长期扩展面最好，但抽取边界尚无实证，本期迁移足迹大。

2026-09-21 的 Codex 八维评审（thread `01a0c318`）第一轮推荐 **(路线 3, 自产 harness·
现场交付)** 34/45。第二轮按十一处订正重评，并补了两个遗漏候选：

| 候选 | Codex 第二轮 | 本仓重评（2026-09-22） |
| --- | ---: | ---: |
| **甲** 扩 BLAS + overlay 交付 | 30 | **29** |
| **丙** 新起 solver + overlay 交付 | 33 | 33 |
| **乙** 新起 solver + 现场交付 | **34** | **34** |
| 戊 扩 BLAS + 现场交付 | 27 | —— |
| 己 抽共享核心 + 薄层 + 现场交付 | 26 | —— |
| 丁 接 ATK + handle 后端 | 19 | —— |

**本仓重评只动了甲，且两次反向调整后又下调一分**（刻度沿用 Codex 的 1–5、复杂度双权重、
满分 45）：

- 爆炸半径 3 → 4：BLAS 线只服务 blas 一个真实消费者（sparse 走 ATK 自带件路），
  `feature/sparse-r1` 已废无 rebase 成本，残留也轻。
- 简单性 3 → 2：BLAS 采集层正在改（预热问题在修，可能换 msopprof），且**不与 solver
  捆绑**，甲要么等、要么绑一个移动目标。
- 爆炸半径 4 → **3**（2026-09-22 定）：甲要动的不只是接口，是 **blas 的三道防假通过的门**
  ——A1 的 `csv_loader.h` 硬检查（`accept.py:543-544`）、A2′ 的三文件（`accept.py:346`）、
  空跑检测 `_column_read_report`（`accept.py:335`，要求 CSV 列名在 harness 源码里以字符串
  字面量出现）。solver 无 frame、非 CSV 驱动，这三道门都得改成按域可开关。
  它们是 blas 正确性的组成部分，不是普通接口。

**这一调把结论的形状改了。** 甲的条件分支——若 BLAS 采集重做连协议一起改（准备段排除、
逐样本恢复输入、workspace 复用），简单性 2→4、复杂度 3→4——**最好也只到 33，追平丙，
仍落后乙一分**。因为那三道门要不要改，跟采集层怎么改完全无关。

所以「先去问采集重做的范围」这条建议降级：它只决定甲是 29 还是 33，**不再能翻转排序**。

Codex 同时判定**这是架构选择，不是开跑许可**。

**以上评分早于 2026-09-22 的端到端实测，基础是估算。** 实测见第 11.2 节，三处与评分假设
不符：甲 的 A1 直接退 3（不是"成本高"，是"不改代码跑不了"）；乙丙 产出等价且数值逐位
相同；overlay 污染被测库是写法问题而非形态问题，**乙 的爆炸半径优势因此收窄**。
排序依据可以从估算换成实测，重评时机由 Mr.0 定。

**「A100 基线」这个词指两样东西，别混**（2026-09-21 订正，先前笼统说成「基线不存在」是错的）：

| | 竞品套件的逐用例实测 | 门禁表的 `T_A100` |
| --- | --- | --- |
| 在不在 | **在**：4944/5520 条带耗时（89%），25/27 算子有 | 全是「待测」占位 |
| 谁产 | 任务方随包发 | **任务书要求开发者在 A100 实测填入** |
| 能不能直接当门禁基线 | **不能**，见下三条 | —— |

前者不能直接充当后者，三处对不上：**规格覆盖**（S-LU 的 12 条 P 严格全参数只命中 3 条）、
**统计量**（采集是预热 5 / 采样 25 / 报 min-max-avg，门禁要预热 ≥10 / 采样 30 / 中位数）、
**出处**（27 份结果无一记录卡型、驱动或 CUDA 版本）。

所以随包数据的角色是**采集手段加一份参考结果**——`build_run.sh` + `cases.json` 是可复用的
采集方式，`bench_result.json` 是参考值。它不阻塞建线，也不阻塞拿它做摸底对比；
**它只阻塞出正式的性能结论**，因为门禁那 12 条的 `T_A100` 仍待开发者实测交付。

## 8′. 裁决：走乙（Mr.0 裁定，2026-09-22）

**方案：新起 solver 自己的生成侧与验收侧两个 skill，自产 harness 并留在验收现场。**

- `repo-task-solver-case-gen`——从任务书产出精度 case、性能 case、调用脚本与 golden；
  精度 case 要合开源社区精度标准，覆盖任务书 §3.5 的九类必测场景。
- `repo-task-solver-accept`——执行验收、出具验收报告。

两侧的唯一交界是任务包（一个算子一个包，每包带自己那几条 `P-xx`）。
**调用点归哪一侧未定**，见本节末。

裁决依据是改动成本与维护成本两轴，不是八维总分：

| | 甲 | 乙 | 丙 |
| --- | ---: | ---: | ---: |
| 改动成本（估） | ~6800–9950 | **~4600–7100** | ~5100–7600 |
| 入场费 | **A1 退 3，必须先改两道硬门**（实测） | **0**（实测） | **0**（实测） |
| 对外契约 | 必动 + 存量包重钉 | 0 | 0 |
| 维护性质 | **永久耦合**，每次任一域改动要跨域回归 | 独立但重复 | 独立 + 跟目标仓测试树 |

甲 贵出的部分主要不在 accept，在 **case-gen 被六件契约拉高了成熟度门槛**（4000–6000 对
1400–2300）。丙 与乙 差约 500 行加一项「跟随目标仓结构」；它唯一的理由是产物形态贴近
开发者的交付件，但按角色模型**那是开发者的交付件不是我们的**，理由不成立。

**乙 是唯一不依赖别人先做什么的路**——甲 要等 blas 的采集重做，丙 要跟 ops-solver 的测试树。

### 两条翻案条件

- **blas 的采集重做若恰好覆盖 solver 所需**（准备段排除、逐样本恢复输入、workspace 采样期
  复用）→ 甲 的改动成本大降，重算。
- **ops-solver 若有了 frame** → 丙 的 per-op 只需薄文件，成本可能降到乙 以下。

### 硬边界：违反即重新评估方案

方法论要求裁决落地时同时钉边界。采用 `solver-plan-v3.md` 第 5 节那十二条（Codex 给出、
每条带机械发现方式），**它们要落进 skill 的 `scripts/` 与 `tests/`，不是人工清单**。其中
对本裁决最要紧的三条：

1. **裁决实现只在发布 skill 内，仓外只放运行产物**——在仅含发布切片的环境跑完整命令，
   缺 `dev-doc/`、`repos/` 即无法运行则不合格。
2. **不复制既有线的完整 A1/A5，不写逐接口的采样分支**——出现完整副本即触发重评。
3. **参考耗时与门禁基线分开**——仅有 min/max/avg 的输入只能停在摸底态，不得改变必测集合。

**这三条被违反，乙 的评分作废，要重新比选。**



## 9. 开跑前要定死的歧义

- **`matinvBatched` 的 n 边界四说不一**：任务书说 n≤32 合法、n=33 必须报错；
  ops-solver 头文件与 `api_list` 说 matinv `n<32`、getriBatched `n≥32`；竞品 bench
  README 说仅 `n<32`；用例集最大只到 n=31。而 P-12 的规格恰好是 n=32、batch=256，
  既落在争议点上又是零基线那一条。
- **分解类算子「自洽重构 vs 与 golden 比较」的优先级没定**，geqrf 甚至明写「或」，
  二选一由开发者定——验收时会成为争议点。
- **输出不唯一的豁免其实三族都有**（2026-09-21 订正，先前记成「只写了 LU」是错的）：
  LU 的 ipiv 并列主元可与 CUDA 取不同下标；QR 两份 §3.2.4 明写「Householder 的符号/相位
  约定允许与 CUDA 不同，但必须满足还原后的 Q R 通过混合容差」；Cholesky §3.2.2 指明
  「与原 A 的**指定三角**比较」。这条不再是设计成本。
- **八个批量接口没有 CPU 标杆命名**（LAPACK 无 batched 语义，只能逐个调单矩阵版，
  但文档没写这句），Cholesky 批量「比什么」只有「批量接口对应输出」五个字。
- `xgeev` 没有任何精度要求，因为那个包没有任务书。

## 10. 卫生问题

`cunmqr/改动报告_20260907.md` 在公开仓里引用了带员工账号的内部构建路径
（`/home/s00800266/solver_bench/...`）。要不要提 issue 回上游另议。

## 11. 实测与探针（2026-09-22）

本节严格区分两类证据：**实测**是真跑出来的，**估算**是读代码推的。先前有把两者混在同一张
表里呈现的毛病，这里分开记。私有主机、容器与路径按仓规不入仓，只记平台与结论。

### 11.1 真机实测（A5 目标机，Ascend950PR，CANN 9.0.0）

| 做了什么 | 结果 |
| --- | --- |
| `build.sh --soc=ascend950` 构建 ops-solver | ✅ 一次过，产出 `libops_solver.so` 与公开头 |
| 仓外程序链该 `.so` 编译 | ✅ **一条 `g++`，0.236 秒**（两个 `-I`、两个 `-L`、两个 `-l`，无 CMake） |
| 仓外程序加载、初始化、建 handle、调用 | ✅ 一路进到算子 host 代码 |
| 调用 `aclsolverSgetrf` | ❌ **207000**，挂在 `aclrtGetHardwareSyncAddr` |
| 仓自己的 `--ops=sgetrf --run` | ❌ **同一行、同一错误码**（对照组：不是我们的问题） |
| `--ops=cheevj` 构建 | ❌ **被测库自己链不上 `-lomp`** |
| `--ops=cmatinv_batched --run` | ✅ **PASS**，三步全过，精度验证通过 |

三条结论：

- **平台不匹配，不是环境坏。** 七个算子的 README 里六个只声明「Atlas A3 / Atlas A2」，
  没有一个声明 950；而这六个恰好都调 `aclrtGetHardwareSyncAddr`。唯一不调它的
  `cmatinv_batched` 在 950PR 上跑通了。
- **树内交付会改被测库的构建。** `test/cheevj/CMakeLists.txt` 用
  `set_property(... TARGET_DIRECTORY ${OPS_SOLVER} ... "-O3;-fopenmp")` 加
  `target_link_libraries(${OPS_SOLVER} PRIVATE omp)`，把 `-fopenmp` 加到**被测库源文件**上
  并给库链 `omp`。这台机器没有 `libomp`，于是启用该测试目录直接让
  `libops_solver.so` 链接失败。**「只新增文件」不等于「被测物构建语义不变」，这条从推断
  变成了实测。**
- **仓外编译的成本近乎为零。** 先前担心的「自配 CANN 编译链」不存在，
  `source set_env.sh` 之后一条 `g++` 就够。**乙相对丙那一分里「丙能省编译配置」的部分
  不成立。**

`msprof op`（即 `msopprof`）的选项面已确认：`--kernel-name`、
`--launch-skip-before-match`（0–1000）、`--warm-up`（0–500）、`--launch-count`（1–5000）。
**「每个样本只含目标调用的 kernel」在工具层直接支持**，不必走 `op_summary` 求和再除
`calls_per_case`。未实跑（缺可跑的目标算子）。

### 11.2 甲乙丙 端到端实测（A5 目标机，2026-09-22）

三条路各走一遍，输入按 0923 的 `cases.json` 格式（字段 `n/batch/lda/lda_inv/iters/check`
逐字段一致），被测是 ops-solver 现有的 `aclsolverCmatinvBatched`——七个算子里唯一在
950PR 上跑得起来的那个。**这是量我们自己的通路，不是验收 solver。**

通路形态（三件共 366 行，全部自写）：

```
cases.json（0923 格式）
  → gen_data.py  68 行   造 complex64 输入 + complex128 golden
  → harness.cpp 182 行   自带极简 JSON 解析读 cases.json（与 0923 的 *_bench.cu 同做法）
                         精度路：一次干净调用 → Ainv.bin
                         性能路：预热 10 → 逐轮恢复输入 → 同步 → 计时 30 次 → 中位数
  → verify.py   116 行   任务书判据（rtol 2⁻¹⁰ / atol 2⁻¹⁶ / ratio 0.99 / 复数实虚分判）
```

| 路 | 走通了吗 | 实测 |
| --- | --- | --- |
| **乙** 自产 · 现场交付 | ✅ | 精度 **5/5 PASS**，性能中位数齐；全链 ~16s，编译 0.462s |
| **丙** 自产 · overlay 交付 | ✅ | 同样 **5/5 PASS**，`[PASS] cmatinv_e2e_test`；全链 21.8s |
| **甲** 扩 BLAS 线 | ❌ | **A1 退出码 3**，硬失败两项：`csv_loader.h` 与 `harness_profile` 探测 |

**甲 的 A1 逐项**（`accept.py env --repo <ops-solver> --soc ascend950`）：

| 检查项 | 结果 |
| --- | --- |
| python3 / cmake / g++ / build.sh / CANN set_env / msprof | ✅ 六项全过 |
| `csv_loader.h` | ❌ **硬失败**——ops-solver 无 frame |
| `harness_profile` 探测 | ❌ **硬失败**——registry 不可达 |
| `cblas.h` / `lapacke.h` | 缺失（软；BLAS 线 golden 依赖，solver 走 complex128 用不上） |

十项里六项免费通过，卡住的正是探针预测的那两道，**而且确实是 `hard`，不是告警**。

**乙 vs 丙 对照**：

| | 乙 | 丙 |
| --- | --- | --- |
| 对目标仓的足迹 | **0** | 1 个新目录，**0 既有文件改动**（工作树差异为空） |
| 额外代码 | —— | `CMakeLists.txt` 17 行 + 无参契约适配 |
| 编译 | 一条 `g++`，0.462s | 走 `build.sh`，全库重建 |
| 全链耗时 | ~16s | 21.8s |
| 精度 | 5/5 PASS | 5/5 PASS，**数值逐位相同** |
| 中位数(ms) | .1297/.2473/.7381/1.5115/2.5905 | .1332/.2445/.7321/1.5072/2.5868 |

**两条路产出等价，差别只在部署。**

由此订正两条先前的判断：

- **「overlay 会污染被测库构建」是写法问题，不是形态问题。** 本次 overlay 的
  `CMakeLists.txt` 只建可执行文件，装入后工作树的既有文件差异为空；`test/cheevj` 那份的
  污染是因为它主动用 `set_property(... TARGET_DIRECTORY ${OPS_SOLVER} ...)` 与
  `target_link_libraries(${OPS_SOLVER} PRIVATE omp)` 改了库本身。**乙 相对丙 的构建隔离
  优势因此缩小**——丙 只要不写那种 CMakeLists 就没有这个暴露面。
- **甲 与乙丙 不是同一量级的选择。** 乙丙 是「写 366 行就跑通」，甲 是「先改 blas 线的
  两道硬门才谈得上开始」——而那两道正是探针估的 1850–2600 行里最贵的部分
  （A1 硬门要下沉成 registry 字段，还得把 profile 探测提到该门之前）。

另记一条与选型无关的事实：0923 的 `cmatinvbatched/cases.json` 里 batch 上到 **1000 万**，
而现有接口头文件写死 `batchSize ∈ (0,3000]`。本次按「格式相同、数据自选」造了可行规格。

### 11.3 本地探针三份

**A · golden 共用率——实验性的，真跑了。** `python3 -m unittest` 29 测全过，被测方是独立
写的 float32 实现（不是 golden 跟自己比）。结论：**模板不是风险点**——29 接口塌成
1 个判定器（79 行）+ 3 个 reduce 算子 + 4 个 reference 源，29 个配对函数合计 51 行。
**这推翻了本文先前「golden 是最大工作量、方差最大」的判断。**

三处实测出来的坑：

1. **任务书规定的精度输入让 ipiv 重构分支从不执行。** 主路径要求 A 对角占优，实测
   n≥31 起换行率 **0%**，ipiv 恒等于 `[1..n]`。后果：`P⁻¹` 重构写反了，整批用例照样全绿。
2. **同一次计算三种判法三种结论。** κ=1e5 时重构 PASS、恒等式 FAIL、直比 FAIL；
   直比类的条件数预算实测只到 **κ≈几百**（200 过、400 挂）。而 getri/potri 被同时挂两条，
   任务书那个「A⁻¹ **及** A·A⁻¹」是与门还是或门没写清。
3. **QR 族九个接口的 golden 全压在一个 `build_Q` 上。** 实测 m=2048 要 7.84 秒，
   4096 外推约 55 秒，乘用例数是小时级。解法是改用 WY 表示（`Q = I − V T Vᴴ`，
   正好是 Xlarft 算的那个 T），**但必须提前设计**。
   附带：因符号自由，`ormqr` 的 golden Q 只能用 NPU 自己的 V 与 tau 建，
   **所以 ormqr 单独测测不出 V/tau 本身的错**——任务书对 orgqr 写了「与 geqrf 联测」，
   对 ormqr 没写。

实测证伪一条：geqrf 的备选判法「或比较 R 与 golden 的上三角」与同文的符号自由条款
**直接冲突**——换一个合法 Householder 符号约定后 R 直比必挂而 QR 重构仍过，
**该备选不可用，必须走重构**。

**B · harness 骨架——估算为主。** 探针写了一个 `Sgetrs` 骨架（1698 行，只做过
`-fsyntax-only`，本机无 CANN 未验链接），据此**外推** 29 接口约 10000–14000 行，
共用率 66%（族内共用 81%）。**外推部分是估算，不是实测。**

它指出的两条可核事实值得单列：

- **A100 金标测的是退化输入。** `sgetrf_bench.cu:41` 原话「循环计时复用上次分解结果」，
  计时循环里没有 `d_A` 回填——第 2 轮起分解的是上一轮的 LU 因子；`sgetrs_bench.cu:35`
  同理。后果不是数字偏一点：**harness 老实恢复输入则 NPU 测干净输入、A100 测退化输入，
  比值没有共同基准。**
- **linkage 风险乘以 46。** `cann_ops_solver.h:77` 的 `}` 已关掉 `extern "C"`，其后计算接口
  是 C++ linkage。新接口若 linkage 一致但参数类型漂移（`int` vs `int64_t`），
  **编得过、链得上、运行期栈错乱，不报错只出错数**。

**C · 甲的改动面——估算。** 读代码估 ~1850–2600 手写行，动 blas 活路径占 25–30% 行数，
但**承重契约面全中**（A1 硬门、A2′、空跑门、绑卡推导、性能统计核、性能期望集）。
三处低估：性能统计核要拆掉 `calls_per_case` 单除数模型；动 `HARNESS_REGISTRY` 会让
**存量 blas 任务包的 `check` 立刻红**（registry 落在被哈希的通用代码区内）；
**SKILL 文档预算只剩 8 字节**（accept 侧最大单阶段 32760 B，上限 32768）。

### 11.4 这些实测改了什么

- **golden 从「最大风险」降级**，模板可塌，风险转到判据盲区（ipiv 不触发、条件数预算）。
- **丙相对乙的编译成本优势不成立**（一条 g++）。乙 相对丙 的构建隔离优势先由 cheevj 的
  omp 事故坐实，又被 11.2 的 overlay 实测收窄——**那是写法问题，不是形态问题**。
- **端到端验收现在不可能完成**：要验的 46 个接口还不存在，现存的六个不在目标平台上跑。
  我们的工具链最多量到「调用发出去」这一步。

### 11.5 仍待澄清，其中一条卡死判据

`experimental_standard.md` **不在任务包里**，六份任务书都只给 gitcode URL。而
`matched_ratio` 的确切定义、INF/NAN 规则、`max_abs_error` 的参照口径**全部指向它**——
**拿不到这份文档，判据锁不死**。

其余已量化的歧义：`1e-2 or 32·ULP` 取 max 还是 min 结论相反（交叉点 |golden|=4096）；
ULP 参照哪个值三种候选差四个数量级；复数 `matched_ratio` 按平面分算还是合并算
（实测 1.5% 虚部坏点分算 FAIL、合并被稀释成 PASS）；批量接口逐 batch 判还是全批合并；
`lda` padding 区域是否参与比较。

## 12. 订正记录（2026-09-21）

Codex 评审指出、本 worktree 逐条复核确认的错处：

| 原先写的 | 实际 | 复核方式 |
| --- | --- | --- |
| 任务书要 device 同步的 **API 级墙钟** | 原文是「NPU **kernel 耗时**」，同步只是计时方法 | S-LU §3.3 第 2–3 条 |
| 标杆**只能引用、产不出来** | 「开发者须在 A100 **实测填入**，下表为待测占位」 | 同上第 4 条 |
| S-LU 的 P 表命中 **4/12** | **3/12**：P-05 的 shape 只有 `trans=1`，而它要 `trans=N` | 逐条比对 `cases.json` |
| P-11（m=4096,n=1024）在 gels 误判里 | S-QR 的 P-11 **不在 `sgels/cases.json`**，零条该 shape | 全表扫描 |
| **三份**改动报告写 H100 | **两份**（`cpotrsbatched`、`cunmqr`） | `grep -l H100` |
| `bench_result.json` 都不记时间戳 | 27 份里 **1 份**有 `captured_at_server_time`；GPU/驱动/CUDA 确为 0 份 | 字段统计 |
| 输出不唯一的豁免**只写了 LU** | QR §3.2.4 与 Cholesky §3.2.2 都有 | 读原文 |
| golden 一律 **float64** | 实数 float64、**复数 complex128** | C-QR §3.2.1 |
| `cheevj_benchmark` 预热 2/采样 7「次数不对」 | warmup 与 repeat 是命令行入参 | `PrintUsage` |
| 29 接口「8 有先例 + 13 零先例」 | 漏了中间 8 个「思路可改」，8+8+13=29 | 重新分档 |
| 卡在 **A2** 有无门 | `csv_loader.h` 是 **A1** 硬检查；三文件缺失 A2′ 只告警 | `accept.py:543` |

**尚未独立证实、留待复核的一条**：`gels` 那 137 条是否全部是误判。需要用高精度 `lstsq`
逐类复核失败原因，现有依据只是「超定残差不该按近零方程判」，不足以断定每条都无辜。

- **2026-09-24 订正**：性能采集工具钉为 `msprof op`（Mr.0 裁定）；本档早期
  行文中「msprof op（即 msopprof）」的等同说法不再作数，msopprof 仅为 BLAS 线
  当时的候选，与 solver 线无关。
