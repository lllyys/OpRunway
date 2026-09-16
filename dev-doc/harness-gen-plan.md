# C\++ harness 生成 · 方案 v4.1（定稿，2026-09-03）

新建独立 skill `repo-task-blas-harness-gen`：开发者拿不出 C\++ 测试 harness 时，由我们从
任务包的 FACTS 确定性生成一套，落成开发者算子工程的 overlay，交现有 accept 按 developer
链路验收。本方案取代前身车道的方案 v3（那份按「改 case-gen + 改 accept + 新 skill」三路
展开）；翻案原因与评审记录见 §10。

## 0. 硬约束（用户裁定，2026-09-03）

- case-gen（repo-task-blas-case-gen）与 accept（repo-task-blas-accept）是主流件，
  一行不改，代码与文档都算；机械判据：全程 `git status --porcelain` 对两目录输出为空
  （含未跟踪新增；git diff 抓不到掉进冻结目录的新文件）。
- 新 skill 独立自足：不 import case-gen，不要求 case-gen 安装在场。
- 定位是边缘案例工具：主流场景（开发者自带 harness）不用它。

## 1. 术语（首次使用处就地定义）

- **FACTS**：case-gen 从任务书提取的结构化事实字典（签名、params、golden、verify、
  cases 等），存在于任务包 gen_csv.py 顶部；规范见 case-gen `references/facts-schema.md`。
- **任务包**：case-gen 渲出的六件套交付目录（gen_csv.py、`<op>_test.csv`、README、
  verify 脚本等）。accept 只读其中 CSV 与 GPU 基线两件（accept.py:24 注释）。
- **Contract IR**（下称 IR）：从 FACTS 编译出的机器可读「可执行契约」，本 skill 内部
  产物，不进交付件。
- **harness**：驱动一次算子验收的整套 C\++——CSV 参数解析（`<op>_param.h`）、golden
  参考、device 调用适配（`<op>_npu_wrapper.h`）、GTest 驱动（`<op>_test.cpp`）与
  CMake 注册。
- **overlay**：本 skill 的最终产物形态——一棵以开发者算子工程根为基准的相对路径文件树
  （`test/<op>/…`），安装即拷入工程。
- **空跑**：CSV 列名与 harness 读列对不上，参数没真正喂进算子而测试「通过」的假通过；
  accept 侧对应告警计数 COLUMN_NOT_READ。
- **A1–A5 / A2′**：accept 验收链的五道门与人工源码审阅环节，定义见 accept
  `references/run-chain.md`。
- **包生成器 ABI**：任务包内 gen_csv.py 暴露给本 skill 消费的接口面——FACTS 字面量、
  `_column_specs(facts)`（每列 name/kind/source/index）与 `_header_columns(facts)`。
- **工程兼容配置**：随本 skill 分发、绑定目标工程具体 revision 的 frame ABI 与 CMake
  helper 描述（带版本号与 fingerprint）；生成只对它负责，实况核验交给安装 preflight。
- **UNSUPPORTED_CONTRACT / UNSUPPORTED_PACKAGE_VERSION**：两个 fail-closed 停机码。
  前者指 FACTS 语义超出已实现的生成能力，后者指包生成器 ABI 版本或摘要不被认识。

## 2. 输入与输出契约（两阶段）

Codex 对 ops-blas@621aafd 实树裁定（§10）：测试注册由 `-DTEST_NAMES` 驱动，
test/CMakeLists.txt 按名解析目录并 add_subdirectory，无家族白名单，无需改工程任何既有
文件。据此契约分两阶段，输入各不相同：

**生成阶段——任务包是唯一外部输入。** 生成器内置一份工程兼容配置（§1）：绑定 ops-blas
具体 revision，覆盖完整 frame ABI 接口面（csv_loader、BlasTest fixture、fill、
DeviceBuffer、verify、test_main、cblas_compat）与 CMake helper 名。产物 overlay 只声明
对该 revision 兼容，不声称任意 revision 通用。frame 面 2026-07/08 间有六次提交在改，
不得凭「看起来薄」当它稳定——兼容靠版本化，不靠假设。

**安装/构建阶段——必须有目标工程与环境。** 输入是 overlay + 目标工程 + SoC/设备/CANN
环境。安装 preflight 全部 fail-closed：

- revision/fingerprint 校验。语义漂移不必然编译失败：frame 的缺列默认、容差、枚举映射
  改了可能编译照过、判定变味，所以这是硬门，不能赌编译错兜底。
- 入口签名与工程公开头（include/cann_ops_blas.h）交叉校验；FACTS 里的签名只够渲染，
  不能替代对实况的核对。
- 目录冲突检查：test/CMakeLists.txt 的 file(GLOB) 家族搜索取第一匹配，同名歧义要拒绝
  而非静默选择。
- 当前 SoC 下算子实现存在、CMake helper 存在。

产物形状：

- 按包声明的目录形态落 `test/<家族>/<op>/`：`<op>_param.h`、`<op>_test.cpp`、
  `<op>_npu_wrapper.h`、`CMakeLists.txt` 与该包 CSV 的逐字节副本；CSV 与测试源同
  basename（frame 按源文件名找 CSV）。golden 参考落位（独立头或并入 test.cpp）由
  Step 0 对拍冻结。
- per-op CMakeLists 用一行式 `ops_blas_add_gtest_tests(${OPS_BLAS})`（sger 形自动发现）。
  翻案记录：本节原定 gemm 显式目标形；spike 实证整目录替换下一行式可用且更简，
  Step 2 冻结时改从实证（contract-ir.md §13）。
- 不生成、不替换工程根 build.sh；构建走既有 `bash build.sh --soc=… --ops=…`。
- 不自带 gtest main：CMake helper 自动挂 frame 的 test_main.cpp。
- 安装是显式独立步骤：目标文件已存在且不逐字节相同即拒绝；支持 dry-run；安装清单入
  manifest，可整体回滚。
- provenance 只记稳定字段（生成器版本、IR 版本、兼容配置版本、输入内容哈希），
  保「同一输入 → 源码层逐字节相同」。

## 3. 同源与包 ABI 门

列语义不在本 skill 重建。生成器直接消费包内 `_column_specs(facts)`——它与产出 CSV 的是
同一份代码，kind/source 一并给出，`_header_columns` 只是它的投影（模板与示例包内同在）。
装载前后三道门，全 fail-closed：

1. **ABI 版本门**：只接受已知的（facts schema 版本，生成器版本，通用代码区 SHA-256）
   组合，函数缺失或版本未知报 UNSUPPORTED_PACKAGE_VERSION；允许列表随本 skill 分发。
2. **装载纪律**：单次读取 gen_csv.py 字节；AST 校验后对同一份字节 exec；FACTS 用
   ast.literal_eval 提取，非字面量即停。
3. **同源断言**：`_column_specs(FACTS)` 的列名序列 \== `_header_columns(FACTS)` \==
   实际 CSV 表头（含列序，重复列名拒绝）。三者不等即停，不生成。

harness 对参数执行语义（怎么造数、怎么调、怎么比）仍是本 skill 对 FACTS 的第二次解释。
这是新后端的固有成本，与可避免的列投影复制不同类，如实区分（评审结论，§10）。

## 4. MVP 范围（支持矩阵）

| 轴               | 支持                        | 拒绝（停机码）                                       |
| --------------- | ------------------------- | --------------------------------------------- |
| FACTS schema    | v1                        | v2 → UNSUPPORTED_PACKAGE_VERSION            |
| golden.kind     | cblas 且实参可由 params 角色机械映射 | lapacke/loop/composed → UNSUPPORTED_CONTRACT |
| 参数角色组合          | 封闭角色表内、建设样本覆盖的组合          | 其余 → UNSUPPORTED_CONTRACT                    |
| 复数标量 / nullable | 本期不支持                     | → UNSUPPORTED_CONTRACT                       |
| 输入形态            | A（任务包 + 目标工程）             | B（无 FACTS 工程）另立方案                             |

两条拒绝的理由要记住：v2 开放 `golden.kind=harness`，golden 与校验本来就由开发者
harness 自带，对这种包反向生成 harness 是悖论；复数与 null 的上游规范分叉（三种复数
列名、两种 null 表达）等 cherk 类样本进入时再显式选择，本期不预选默认。

## 5. IR 与生成器纪律（继承 v3）

- IR schema 在编码前冻结，含 sasum、sgemm 完整实例与至少两个负例；生成器照 IR 产
  C\++ 不需要任何隐式推断。
- 封闭 primitive 语法对齐 facts-schema 现存角色表；扩语法要改生成器并过评审，
  不往配置表塞行。
- 禁止 `if op == "xxx"` 式特判；算子名只作数据（符号名、文件名）。

## 6. 落地顺序与多 agent 执行模型

执行采用 ultracode fan-out：每个增量按「只读侦察 fan-out → 主会话裁决与落盘 →
多镜头对抗验证 → Codex checkpoint → 用户门」骨架编排，通则与逐步编排见
`dev-doc/harness-gen-orchestration.md`（12-agent workflow 设计并对抗核查后定稿）。
三条不变量：仓内落盘与 Codex 评审只在主会话；真机单机独占全串行且逐门授权；
冻结面机械核用 porcelain 口径（§0）。

1. **Step 0 · 产物 ABI spike（最高风险先行）**：手写最小 sasum overlay，装进干净的
   目标工程副本，用既有 build.sh 构建、走通 accept A1–A5。输入契约已由静态证据裁定
   （§2），spike 不再决定输入数量；它验证的是冻结的 frame ABI 模板可编译链接、golden
   落位、安装 preflight 清单完备、真机全链可跑。真机操作按仓规先取用户授权。
   两件实况先呈报用户：上游干净树已有 test/asum/sasum（撞「已存在即拒绝」门，处置
   要裁定）；.oprunway/real-machine.env 尚缺，需用户提供。
2. **Step 1 · 移植与装载器**：新建 skill 骨架并注册 manifest（§7）；前身 contract.py
   与 7 个测试迁入并按 §3 改造；改编版复验脚本固化「基座列投影已单源」结论。
3. **Step 2 · IR schema 冻结**〔完成 2026-09-10〕：schema v1 冻结（contract-ir.md，
   CONTRACT_VERSION=2），compile_contract v2、ir_validator、签名表、模板契约、正负实例
   与 50 测就位；Codex 四轮核验判「可以冻结」，/goal 授权冻结。
4. **Step 3 · sasum 纵切**〔完成 2026-09-10〕：renderer.py（IR→C\++，逐字节复现 rev1、
   跨机确定性）+ installer.py（H4/H5 七门 + 回滚）+ harness H1–H5 接线；真机 A3 精度
   77/77、rev1 提升为已验基准；58 测绿。
5. **Step 4 · sgemm 建设样本**〔完成 2026-09-10〕：方案乙 L3 通用后端（v3 schema +
   行为源侧车 + cblas 派生）；离线语法编译通过；真机被 asc-devkit 9.0 阻塞（记录在案）。
   **Step 5 · sger 零改动证伪**〔完成 2026-09-10〕：sger 只加新文件、renderer/签名表/
   contract/validator 零改动（SHA 与断言双证）、离线语法编译通过——泛化证伪成立；
   全库真机构建撞共享盘满，以离线编译代偿。

## 7. 移植清单（前身车道 oprunway-blas-harness-gen 的未提交资产）

- contract.py（36 行）→ 新 skill scripts/，配包装载器；不再依赖 case-gen facade。
- tests/test_contract_projection.py（7 测试）→ 新 skill tests/，砍 package.py facade
  与 accept 路径断言，case-gen 示例包只作只读 fixture。
- package.py 单源化补丁 → 弃：基座已交付同等效果（package.py 三处取表头均委托包内
  生成器，无本地副本）。
- case-gen 两处文档一句话修订 → 弃（§0 硬约束所禁）。
- codex-review「半径与去重常冲突」原则段 → 本线 `.claude/rules/codex-review.md`。
- plan v3 与 handoff → 由本文件取代，不照搬；v3 文本随前身 worktree 保留至移植完成。
- 新 skill 注册进 `plugin/.claude-plugin/plugin.json` 的 skills 数组（overlay 目录，
  不属冻结面）；`claude plugin validate` 通过作为门。

全部落完且复验通过后，前身 worktree 方可删；它的工作全在未提交状态，删前逐项核对。

## 8. 成功判据

- sasum、sgemm 两条纵切在真机走通现有 accept A1–A5（developer 链路，accept 零感知）。
- sger 零改动接入成立（§6 Step 5 的机械核）。
- 同一输入两次生成，staging 树逐字节相同（树级哈希）。
- case-gen 与 accept 两目录全程 porcelain 为空（§0 口径）；plugin manifest 校验通过。
- §3 三道门与 §4 各拒绝路径均有测试覆盖。
- 成功声明限定在已覆盖的 primitive 组合，不据样本宣告全 BLAS 通用。

## 9. 明确不做

- 不改 case-gen 与 accept 的任何文件；verdict 无 harness_source 字段；A2′ 不缩小，
  生成件照常受审。
- 不做来源证明哈希、证据闭包、隐藏 case、差分/变形测试；不防恶意实现与环境篡改。
- 形态 B（无 FACTS 工程）不在本方案；FACTS schema v2 不在本期。
- IR 不是交付件；不动工程根 build.sh。

## 10. 决策与评审记录

- 2026-09-03 用户裁定：冻结 case-gen/accept，新 skill 独立承载、边缘定位（§0）。
  该裁定翻案了方案 v3 的方案 C 归属结论与 accept generated adapter。
- v4 草案经 Codex review-plan（gpt-5.6-sol @ xhigh，thread
  `01a06656-2288-78c2-985e-ee9312538098`）判 MAJOR GAPS；四项门槛全部折入本 v4.1：
  产物改 overlay 契约（§2）、消费 `_column_specs` 加版本门（§3）、MVP 收窄 schema v1
  可执行子集（§4）、sasum overlay spike 先行（§6）。
- 其中两条最重发现由 Claude 在仓内独立复核成立：accept 只读包内 CSV 与基线两件
  （生成物落包内必空跑）；包内早有 `_column_specs`，列 kind 重建是伪命题。
- 2026-09-03 输入契约裁决（同 thread 续问，对 ops-blas@621aafd 实树核查）：注册由
  TEST_NAMES 驱动、无需改工程既有文件；生成阶段任务包唯一输入成立，前提是 revision
  绑定的工程兼容配置加安装 preflight（§2 两阶段即此结论）。Claude 原判四点被修正：
  工程知识不止四样（fixture/fill/DeviceBuffer/verify/test_main/cblas_compat 全算）、
  frame 面近期六次提交在改不得称稳、语义漂移不必然编译失败、输入契约不必等 spike。
- 2026-09-03 执行模型改多 agent（用户指令，ultracode fan-out）：12-agent workflow
  （run wf_041e7def-75b）出六份编排提案并逐份对抗核查，定稿于
  `dev-doc/harness-gen-orchestration.md`；核查同时抓出并修入本方案：冻结面判据升级
  porcelain（§0/§8）、sger 基线扩面与纯度纪律（§6）、Step 0 两件呈报实况（撞车与
  real-machine.env 缺失）。
- 2026-09-10 Step 2 冻结材料就绪：IR schema v1（contract-ir.md，CONTRACT_VERSION=2）
  经三路盘点、双竞争草案、29 分歧裁决、双盲实例对拍（sasum 1 分歧/sgemm 36 分歧四簇
  归因）与 18 项对抗攻击（8×P0 全修入）合成；对拍基准修订 overlay-rev1 随之产出；
  §2 CMake 形制翻案从 spike 实证。冻结待用户裁定。
- 写入通路：按本线 codex-review 规则「Claude 写、Codex 审」，本文件由 Claude 直接
  落盘。

