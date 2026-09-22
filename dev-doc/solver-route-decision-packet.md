# 决策包：0923 solver 批次的验收线路与 harness 档位

> **时效说明（2026-09-21 追加）。** 本文是发给 Codex 评审的原始派单内容，
> **正文保持发出时的原样**，以便与评审结论对照。评审与后续复核推翻了其中若干判断
> （性能口径、标杆性质、档② 的「塞进别人的仓」说法、路线 1 的权限定位等），
> 现行结论以 [solver-0923-context.md](solver-0923-context.md) 与
> [accept-line-adoption-method.md](accept-line-adoption-method.md) 为准。

## 0. 请评什么

两个互相耦合的决定，**请一起评，不要各自取最优**：

- **轴 A**：0923 solver 批次走哪条验收线（三选一，见第四节）。
- **轴 B**：harness 的调用器从哪来（三档，见第五节）。

评审要求见第九节。本文自足，冷读不需要其它上下文；引用到的文件路径都相对本仓根。

## 1. 术语（首次使用处就地定义）

- **任务书**：社区算子任务的验收契约，规定接口、精度/性能/内存判据与交付件。
- **任务包**：本仓 case-gen 渲出的六件交付目录，契约见
  `plugin/docs/development/blas-case-package-contract.md`。
- **harness**：驱动一次算子跑测的整套 C++——参数解析、golden 参考、device 调用适配、
  测试驱动与构建注册。本文把它切成**调用器**（喂一条用例、结果落盘）与**判据器**
  （算 golden、比容差、出 PASS/FAIL）两半。
- **A1–A5**：accept 验收链的五道门（环境、有无、精度、性能、结论）。
- **msprof**：CANN 的性能采集工具，报的是 kernel task duration。
- **档①②③**：harness 调用器的三种来源，定义见第五节。

## 2. 背景事实（均已在本仓复核）

**0923 批次的形状。** 7 个任务包目录、6 份任务书（第七包「一般矩阵特征值」只有竞品基线，
无任务书）、29 个核心接口 + 17 个 `_bufferSize`。六份任务书同模板，判据逐字一致：

- 交付形态：ops-solver Host C API + AscendC/CATLASS **Kernel 直调**；§2.2 原文排除 aclnn 两段式与
  PyTorch 接口；接口是 handle 式 C API，返回 `aclsolverStatus_t`。
- 验收粒度：**族级**。五个接口「作为同一社区任务一并交付，不允许拆分验收」，
  同族走同一 PR 或一组关联 PR；每份任务书一张 12 条 `P-xx` 性能表横跨全族，全达标才收。
- 精度判据：rtol `2^-10`、atol `2^-16`、matched_ratio `0.99`、max_abs_error
  `1e-2 或 32·ULP`；golden 必须是 **float64 CPU 参考**；复数按实部虚部分别作 FLOAT32 判定。
- 性能判据：`T_NPU ≤ T_A100 / 0.8`，**预热 ≥10 次、采样 30 次、取中位数**。
- 内存门：workspace 不超 `bufferSize`；批量接口额外内存不超 A100 对应接口的 2 倍；
  100 次 create/destroy 后回到基线。

**包里给了什么。** 27 个算子目录各一套竞品 cuSolver/cuBLAS 测速件（`*_bench.cu`、
`build_run.sh`、`cases.json`、`bench_result.json`）。用例共 5638 条（已核），
**全部是性能用例，精度用例为零**——字段只有形状与属性，没有期望值、容差列、golden 路径，
也没有用例名。任务书点名的六个 `*_family_950_testCase/` 自测用例目录**全部不存在**。

**竞品基线的五处硬伤**（决定它能不能当 `T_A100` 用）：

1. 门禁写 A100，但随包唯一的出处证据指向 H100（四份改动报告里三份写 `sm_90 / CUDA 12.6`），
   且 `bench_result.json` 与 `run.log` 不记 GPU 型号、驱动、CUDA 版本、时间戳。
2. 采集口径是预热 5 次、`iters: 25`、输出 min/max/avg，**全套代码没有任何地方算中位数**。
3. `P-xx` 规格多数不在用例集：S LU 包 12 条只命中 4 条，四条批量行零命中，
   `smatinvbatched` 用例 n 最大 31 而 P-12 边界是 n=32。
4. `gels` 有 137/243 条被竞品自己的残差口径判失败（已核，S/C 各 137 条 `perf` 为空）——
   用的是 `‖A·X−B‖/(‖A‖‖X‖+‖B‖) > 1e-2`，而随机超定系统残差本来就是 O(1)，
   正确判据在法方程上。被误判的正是高瘦规格，P-11（m=4096, n=1024）在其中。
5. `sgetribatched`（153 条）与 `cgetribatched`（145 条）是半途快照，`perf` 全空；
   `Xlarft` 无 bench 目录，单矩阵 `getri` 无 bench。

**目标仓 ops-solver 的现状**（已核）：

- `src/` 下 7 个算子：`cgetrf`、`cgetri`、`cgetri_batched`、`cheevj`、`cmatinv_batched`、
  `sgetrf`、`sgetri`。0923 要交的 `Sgetrs`、Cholesky 全族、QR 全族、`Xgeev` 一个都没有。
- **没有 GTest、没有 `test/frame/csv_loader.h`、全仓没有任何 `.csv`；`build.sh` 只支持
  `--ops/--soc/--run/--pkg`，不支持 `--device` 或 `TEST_DEVICE_ID`。**
- 执行契约是 `bash build.sh --ops=<op> --run` 严格三步（`gen_data.py` → `<op>_test` →
  `verify_result.py`），三个入口**都不传参**，只跑得到默认 shape。它是冒烟测试，不是验收套。
- 三种测试骨架：因子重构式 2/7、乘回单位矩阵式 4/7、`cheevj` 的多门禁式 1/7
  （三个可执行，其中 `_benchmark` 已是中位数口径但预热 2 / 采样 7）。
- 29 个接口里只有 8 个有同类先例可抄，**13 个零先例**：getrs、potrs、potrsBatched、gels、
  orgqr/ungqr、ormqr/unmqr、Xlarft。
- 仓里的容差（`eps=1e-3`、`RELATIVE_TOL/ABSOLUTE_TOL=5e-3`、`ERROR_TOL=1e-4`）全不合任务书，
  且 `sgetri/gen_data.py` 的 golden 是 float32 的 `np.linalg.inv`。

**角色实况。** 任务书由上游写（ops-solver `origin/task` 分支），用例由任务方造，
**发布侧没有使用本仓任何 skill**。我们只在验收这一环出场，前三件是既成事实。

## 3. 现有三条线的能力面

- **`repo-task-blas-accept`**：消费六件任务包（只直读 CSV 与 GPU 基线两件），要求被测工程
  有 CSV 驱动的 GTest harness（`<op>_param.h`、`<op>_test.cpp`、`<op>_npu_wrapper.h`
  + `test/frame/csv_loader.h`），`build.sh --device` 编译期定卡；性能走 **msprof kernel
  task duration**，默认 `repeats=1`，基线载体是一张 `gpu_baseline.csv`。
- **`repo-task-atk-accept`**：ATK 节点驱动；`references/external-perf.md` 处理「标杆是任务书
  给死的一张表、只能引用产不出来」的情形，其能力对照表里**「中位数、p90」明确标着 ATK 不能**，
  该情形下 A4 落两步；`scripts/run_cxx.py` + `assets/standalone_runner/runner.cpp` 是一条
  不依赖 ATK 的独立 C++ 执行器，**只覆盖精度**（性能是相对基线的比值，另一套方法学），
  且「签名类型不在词表里就跑不了」；报告字段已有 `matched_ratio` 与 `max_abs_error`。
- **`HARNESS_REGISTRY`**：case-gen 模板内的域 profile 数据表，十二字段
  （`seed_columns`、`description_column`、`expect_column`、`expect_default_token`、
  `status_vocab_bound`、`threshold_columns`、`first_param_ctype`、`entry_headers`、
  `footprint_policy`、`build_device_flag`、`visible_devices_env`、`runtime_library_dirs`），
  已从 `blas` 泛化到 `sparse_frame`；`harness_overrides` 可覆盖键恰四个、整键替换。
- **`repo-task-blas-harness-gen`**（已建成 8239 行，在 `feature/harness-gen` 上未合入 main）：
  开发者拿不出 harness 时从任务包的 FACTS 确定性生成一套 overlay 装进开发者工程。
  它绑定 ops-blas 具体 revision 的 frame ABI，明写「产物 overlay 只声明对该 revision 兼容，
  不声称任意 revision 通用」；安装 preflight 全 fail-closed。**它要求手上先有一个符合六件
  契约的任务包**（消费包内 `_column_specs(facts)`，有 `UNSUPPORTED_PACKAGE_VERSION` 门）。

## 4. 轴 A：三条候选线路

| | 路线 1 · 补 harness 走 BLAS 线 | 路线 2 · ATK 线 + external-perf | 路线 3 · 新起 solver-accept |
| --- | --- | --- | --- |
| 要做什么 | 补 CSV+GTest harness 与 frame，扩列模型 | 复用外部基线机制与 `run_cxx.py` | 复用证据面，run 链自写 |
| 动上游工程 | **要** | 不要 | 不要 |
| 已知未解 | L1 四处缺口仍在；性能还要另加 API 级墙钟采集 | 依赖链准备段、中位数采集、性能方法学 | 两轴都要自己填 |
| 维护面 | 不增线 | 不增线 | **多一条线** |

**BLAS 线的硬前提 ops-solver 一条都不满足**（见第二节），A2 有无门第一关就过不去。

## 5. 轴 B：harness 调用器的三档

| 档 | 我们出什么 | 需要仓里有什么 | 本仓现成件 |
| --- | --- | --- | --- |
| ① 消费自带 | 只出判据器 | 整套测试约定，认得出读得懂 | blas-accept 的 A2′ |
| ② 注入 overlay | harness 源码，装进开发者工程 | **稳定的 frame ABI** + 构建挂钩 | `repo-task-blas-harness-gen` |
| ③ 独立执行器 | 整个执行器，不碰仓的测试树 | 只要公开头 + 可链接的库 | `run_cxx.py` + `standalone_runner/` |

选档的两条独立轴：

- **仓的 frame 强度**决定调用器能不能直接用。对照组 ops-blas：`test/frame/` 九个共享头，
  70 个算子目录下 69 个 `_param.h`、74 张 CSV、99 个 `_test.cpp`，开发者只填 per-op 三件
  加一个 golden。**ops-solver 无 frame。**
- **golden 的可审性**决定判据器能不能直接用。ops-blas 的 `sasum_golden.h` 共 36 行、
  核心一句 `cblas_sasum(n, x, incx)`，人一眼审完。**solver 的分解类 golden 判法在任务书里
  可二选一（geqrf 明写「或」），且八个批量接口没有 CPU 标杆命名。**

## 6. 两轴的耦合（这是为什么要一起评）

- 档② 要求先有符合六件契约的任务包 → 选档② 就隐含**先做一套 solver 的 case-gen**。
- 档③ 的现成件只覆盖精度 → 选档③ 就隐含**自建性能采集方法学**。
- 路线 1 隐含档②/①，路线 2 天然靠近档③，路线 3 两轴自由但都要自己填。
- 三条路线共同的未解项：**精度用例为零**，29 个接口三种判法（与 golden 逐元素比 / 自洽重构 /
  恒等式）的 float64 golden 必须自产，这部分落在 case-gen，不落在 harness。

## 7. 可用于校准的先例（不是用于类比）

2026-09-01 本仓为 ops-sparse 支持做过同构裁决（Codex `review-plan`，加权分复杂度 ×2，
满分 35）：A 案（原 skill 内参数化）26、C 案（拆共享核心 + 按域 skill）22、
B 案（新建平行 skill）18，裁 A。两条失分理由：

- **B 的真实成本是「重复买隔离」**：引擎与各项机制一式两份，双修复与漂移是永久成本。
- **C 的前提是两个域撑得起独立发布与版本耦合**，两个高度同构的域撑不起。

A 案成立依赖七条硬边界，其中一条对任何新域都适用：**回归门先升级再动手**——现有 check
只证明「新版自洽」，不证明「与旧行为一致」。

**请注意 sparse 与 solver 的差异**：sparse 的落差在用例模型层，solver 多一处物理量落差
（msprof kernel time vs 任务书要的 device 同步 API 级墙钟；前者不含 launch 与 host 开销，
比值系统性偏乐观）。先例可用于校准打分尺度，不可直接套结论。

## 8. 我方当前倾向（请当作待反驳的假设）

路线未定；harness 倾向**档③**，三条理由：ops-solver 无可绑 frame，档② 硬做等于把 frame
也塞进别人的仓（代码我们担、位置他们担）；任务书要的 API 级墙钟加 30 采样中位数，
仓里三步不传参的冒烟契约怎么改都表达不了；依赖链（getrs 先跑 getrf 且那次不计时）在独立
执行器里是几行代码，在 CSV 行模型里无法表达。

反面成本已知：`run_cxx.py` 只覆盖精度；「签名类型不在词表里就跑不了」；真正的大头是 golden。

## 9. 评审要求

1. **按下列八维逐维打分并给理由**（复杂度为最高优先级）：通用性、泛化性、简单性、清晰性、
   复杂度、冷读可理解性、爆炸半径、信任成本。八维的完整定义见 `.claude/rules/codex-review.md`，
   请以该文件为准。
2. **显式处理维度之间的冲突**（如简单 vs 覆盖完整），给权衡结论，不要单维最优。
3. **两轴一起给结论**：轴 A 与轴 B 的组合推荐，而不是各自最优。
4. **指出我们漏掉的候选**：如果存在第四条路线或第四档，请点名并同样打分。
5. **指出哪些「事实」需要再核**：第二、三节里若有你认为证据不足以支撑的断言，请点名。
6. **给出这次决定的可回退性判断**：选错之后止损的代价，按路线分别说。
