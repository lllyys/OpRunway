# harness-gen 多 agent 执行编排（ultracode fan-out）

配套 `dev-doc/harness-gen-plan.md` §6。本文把各增量展开成多 agent 编排；产出方式：
12-agent workflow（run wf_041e7def-75b，2026-09-03）——每增量一份编排提案加一轮对抗核查，
核查修正已折入本文，原始提案与核查全文存 session scratchpad。

## 编排通则（约束所有增量）

- 单写者：仓内一切落盘只由主会话执行；fan-out 子 agent 只产 scratchpad 分析、草案与
  diff 建议。
- Codex checkpoint 由主会话串行发起，永不进 fan-out；冷读评审用全新 session。
- 冻结面机械核统一为 `git status --porcelain` 对两冻结目录输出为空。git diff 抓不到
  未跟踪新增——前身车道的 contract.py 正是以未跟踪文件落进 case-gen 的，实证过的洞。
  凡 import/exec 冻结树内 .py 一律 PYTHONDONTWRITEBYTECODE=1，防 __pycache__ 落入。
- 并行隔离要结构性：多镜头与双盲 agent 各配独立 scratchpad 目录或内联输入，不靠提示词
  自律；同模型对拍各附 choice-point 清单（schema 允许多种写法之处），防共模失效。
- 验收零上下文只约束验收与试跑：真机 A1–A5 一律走 isolated-acceptance 无头通路；开发期
  分析 agent 读 plugin/** 时子目录记忆注入不算违规；冷读审读 agent 只读 scratchpad 副本，
  物理隔离。
- 真机单机独占：远端动作全串行、只由主会话发起。请门时写明覆盖的重跑轮数，删除/覆盖
  单列；决策类问题提前呈报，让用户响应与本机工作并行。
- worktree 隔离只给要改文件对拍的 agent（变异红队类）；只读镜头不开。
- 触碰前身 worktree 的 git 命令一律加 --no-optional-locks，它全程只读。

## Step 0 · sasum 手写 overlay 真机 spike

1. 五路只读侦察 fan-out：frame ABI 面（test/frame 全件 + CMake helper 定义，含
   fingerprint 素材）、既有测试树结构（gemm/sasum 实样，产物布局实证）、任务包列清单、
   accept 链输入摆放、build 入口传导。frame 符号核对直接 grep 头文件原文，不拿报告互证。
2. 主会话冻结三草案：工程兼容配置 v0、安装 preflight 清单、golden 落位与产物布局
   （平铺 vs arch 子目录按侦察实证裁定）。两件实况随即呈报用户：上游干净树已有
   test/asum/sasum（含 sasum_golden.h），撞「已存在即拒绝」门，处置要用户裁定；
   .oprunway/real-machine.env 尚缺，请用户提供。呈报后本机工作继续，不空等。
3. 双盲手写对拍 fan-out：draft-A 与 draft-B 同规格独立手写（各自独立目录、互不知情），
   加一路 preflight 脚本草案。两稿一致不构成正确性证据，裁决者是 frame 头文件与编译器。
4. Codex checkpoint（主动发起的 spike 评审，不援引触发线——scratchpad 草案不属手写源）。
5. 用户门：真机授权（容器、副本制备、撞车处置执行、构建、A1–A5，逐项）。
6. 真机串行链：checkout 兼容配置绑定 revision → preflight → 安装 → 构建 → 无头 A1–A5。
   编译级修复走「改稿-重装-重建」小循环，同目标授权持续有效；改到判定语义才重过评审。
7. 收尾：兼容配置 v0→v1 回填，spike 产物与真机通过记录的仓内落点写死（供 Step 3 前置
   核验），commit 待用户明示。

## Step 1 · 新 skill 骨架与移植

1. 四路只读侦察 fan-out：移植清单核对（§7 七项逐项，含已完成项确认）、基座单源复核
   （改编 handoff §7 脚本在 scratchpad 实跑）、骨架与 manifest 草案、装载器三道门与
   测试设计。
2. 主会话汇聚裁决：测试 fixture 寻址策略此时定死（环境变量指根或随副本复制），
   唯一落盘计划成文，唯一可改既有文件是 plugin/.claude-plugin/plugin.json。
3. 主会话落盘：骨架 → scripts → tests → manifest 最后注册；落盘后即跑测试、
   claude plugin validate、porcelain 核。
4. 四路对抗验证 fan-out：测试独立性（scratchpad 副本从零跑）、冻结面与 manifest 门、
   冷读零上下文（读 skill 切片的 scratchpad 副本）、移植完整性对拍（对前身
   --no-optional-locks 存档）。修复回主会话，一轮即收。
5. Codex checkpoint（≥4 手写源触发）→ 收束。删前身 worktree 与 commit 署名张力
   （AGENTS 禁 trailer vs 平台要求 Claude-Session）都是用户门。

## Step 2 · IR schema 冻结

1. 三路盘点 fan-out：角色表矩阵（facts-schema 逐行）、sasum 事实卡（示例包 + 前身
   contract.py）、sgemm 事实卡（无示例包，从 repos/ops-blas 推导，逐条带 provenance，
   确证不了标 UNKNOWN）。sgemm 卡逐条核，其余抽查。
2. 双竞争草案 fan-out：最小机制稿 vs 覆盖完整稿，共用盘点矩阵的字段命名骨架，
   偏置只作用于结构取舍——否则合成点无法逐字段 diff。
3. 主会话合成 v0，分歧逐项按七维裁决并留档。
4. 六路 fan-out：C1/C2 正例实例、D1/D2 双盲复算（结构隔离，各附 choice-point 与猜点
   清单）、负例作者（cherk 双角色：loader 门正例、§4 复数拒绝负例）、对抗攻击。
5. 主会话对拍判读：分歧先归因（条文欠定 / 素材 UNKNOWN / 笔误），只有第一类改条文；
   定向复算至多一轮。v1 起即按 prose-style 行文，落盘保逐字节一致。
6. Codex 无条件一轮（核心契约）→ 用户冻结裁定门：schema 冻结是用户裁定，不是产物状态。

## Step 3 · sasum IR→C++ 纵切

1. 前置核验（主会话）：Step 0 产物与通过记录按其收尾写死的落点核验，缺件即停。
2. 四镜头侦察 fan-out：手写件解剖映射、IR 字段可导出性矩阵（独立产出，双向 join 归
   主会话）、列同源反空跑、确定性与 provenance 规格。
3. 对拍口径冻结（主会话）：逐文件定逐字节 vs 语义 diff；同时冻结 IR 访问 API、param.h
   公开形状、共享 stub；并按体量阈值定死「草案扇出还是主会话直写」——前身 contract.py
   36 行量级，降级直写很可能是正解，不为并行而并行。
4. 实现落盘 + 本地机械门：测试全绿、确定性双跑树哈希相等、validate、porcelain。
5. 四镜头对抗对拍 fan-out：逐字节、语义（专找编译能过判定变味）、确定性、负例；
   各镜头在自己目录独立生成，零共享状态。
6. 修复循环（主会话）：修的人不自签，对应镜头复核；改到核心逻辑重过 Codex。
7. Codex checkpoint → 真机门与 A1–A5（重跑授权轮数在请门时写明）。

## Step 4/5 · sgemm 建设样本与 sger 零改动证伪

sgemm：四镜头差距分析（空跑镜头从 facts-schema 与模板独立推导列集，保真并行）→
扩展落盘 + Codex 无条件一轮 → 四镜头对拍（独立预言只产期望清单、diff 归主会话；
变异门核走 worktree，判据是 fixture 对拍测试变红加基线 SHA 失配预演——确定性双跑只验
确定性，不承担抓变异）→ 真机 A1–A5（请门时交代任务包与 GPU 基线来源，不到 A4 才发现缺件）。

sgemm 的包 fixture 按 case-gen 模板逐字节派生：通用代码区原封取自冻结模板、只填 FACTS，
保证过 §3 版本门；自由草拟的包会被版本门拒收。

sger 留出纯度机械化：基线冻结（harness-gen skill 全目录 SHA 清单，tests 代码一并入内，
外加只增路径白名单）必须发生在任何 sger 专属材料进 agent 上下文之前。此前所有子 agent
prompt 禁含 sger 字样、禁读 repos/ops-blas/test/ger/**，prompt 存档作核验证据；公共头文件
里顺带出现的 sger 符号豁免。零改动机械核以脚本退出码为准：skill 目录 status 只许白名单内
新增，零修改零删除。若必须改生成器才能接入，即证伪结果，交用户裁决，不粉饰为通过。

## 横切 · 四层测试与三道回归门

四层测试随逐算子循环执行，不集中攒批：loader/IR 单测与生成树黄金测试在每次落盘后本机跑，
离线编译在远端容器，A1–A5 走无头通路。三道回归门：

- R1：porcelain 对两冻结目录为空，每次落盘后即跑，贯穿全程。
- R2：同一输入双跑 staging 树哈希相等，每次生成后跑。
- R3：sger 夹序核——基线在 sger 数据落盘前记录，机械核在落盘后立即跑；夹的是生成与
  落盘步骤，不是验收跑。

待用户裁定一项：纯 Python 单测与双跑生成拟在本机执行（AGENTS §3 把「测试」点名远端；
理由是零 CANN/ATK 依赖、纯文本比对），裁定结果回填本文。
