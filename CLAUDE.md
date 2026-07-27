# CLAUDE.md

本文件指导 Claude Code 在 **OpRunway** 工作区中工作。**全程中文交互。**

## ⚠ 最高优先级规则

0. **🔴 泛化优先 · 绝不针对具体算子做优化 / 特判**（用户 2026-07-24 明定为**最高原则**，位列 #1 之前）。
   本项目是**通用算子验收工具**——**一切设计与改进必须泛化**：接口 / 目标目录 / 形状 / dtype 一律从
   「任务书 × op_def × example」**按字段分源通用探测**（承「零硬编码」）。**禁的是「按算子身份分派」**：
   代码里绝不出现按算子名的分支（`if op == "<名>"`）、绝不为**某个算子**裁专属逻辑 / 复制一份验收语义。
   - **允许**按「稳定的接口能力 / 仓 / 框架」扩通用机制——canonical 的 per-repo `repo_adapter` / `generated_harness`
     是按**能力 / 仓**扩（合法），**不是**按算子身份。把「各按一类形状裁的机制」并起来冒充「通用」才是违规（见 U7 设计 §3.1）。
   - **具体算子只作「见证 / 测试输入」验证通用通路，绝不是优化目标**。**建通用能力**时见证挑「最压满结构轴」的、
     别只挑最简单的类去裁机制；最小见证作冒烟 / 隔离故障 / baseline 不在此限。
   - **判据（可操作）**：一个制品**是否特判**——看它**是否表达可复用能力、是否由通用 schema / 生成器处理**，
     不只看是「数据」还是「代码」。per-op 的 spec / IR / gap / 目标机 是通用工具消费的**数据**（核实这些不违规）；
     手写 per-op runner / 为某算子改工具代码 = 违规。
   - **换任意「域内」算子（域内定义见 `plugin/acc-common/contract_ir/`：无状态 / 标准 aclnn 两段式 / 无 opaque descriptor），
     工具零改即可跑**；跑不了 = 通用性缺口 → 补**通用机制**、绝不开特例。**域外**（稀疏库 API / 有状态容器 / 无张量时序 …，
     **列表非穷尽**）命中即 **fail-closed 标「不支持的接口能力」**、绝不硬塞、绝不自动归某类 adapter；**未列的新形态默认按域外 fail-closed 待人裁**。
   - **三源缺失 / 冲突时**：ABI 走 header/example、语义 / dtype / 硬件走 op_def × 任务书交叉；**任务书权威**
     （PR / op_def ≠ 任务书 → 可能选错 PR，承硬约束 #1）；仍定不出 → **fail-closed 或 `AskUserQuestion` 问用户**，绝不静默猜。
   ⚠ 本条**已 capture 进 bureau logbook、待 `bureau:review` promote**；**promote 前以本条（CLAUDE.md）为现行权威**、bureau 副本按 trust tier 读。

1. **先抛方案、经用户同意才落地实施**。当前处于 init 阶段，骨架已搭、组件未建。
   构建 skill / agent / workflow 前，先写「设计 / 取舍 / 候选改法」，向用户列出、点头后才动手。
2. **不 push 任何远端**（含自己的 fork），除非用户明示。GitCode/GitHub 的提 issue / PR / comment 等对外动作，
   作用于**非本用户**的仓时必须先经用户同意，并署名 lys / lllyys；本用户自己的仓可直接做。
   ⚠ **人类署名按上句（lys / lllyys）；此外不带任何 AI 署名**（2026-07-10 用户明示「**never**」）：
   所有新 commit 及其它对外产出**绝不**追加 `Co-Authored-By: Claude …` / `Claude-Session: …`，
   PR body 不加 `🤖 Generated with Claude Code` 之类——**不因工具、harness 或提交模板的默认值而追加**。
   （遗留：本条确立前已有 10 个 commit 带了 trailer，其中 3 个已推公开远端。经用户 2026-07-10 决定
   **历史一律不动**，只管以后。）
3. **副作用先确认**：对外发布、删除/覆盖、改远端环境一律先问；本地探测、清缓存、重跑可直接做。
4. **本项目所有 doc 产出（md / 图 / svg 等）放项目根的 `doc/`**
   （`/Users/ll/Desktop/workspace-ascend/OpRunway/doc/`），**不**放上层 `markdown/`。
   每次改动落地后，同步在简表 `doc/oprunway-changes-brief.md` 追加一两句（倒序、大白话）。
5. **Codex audit-fix 门（push 前）**（⚠ **2026-07-24 用户改定：门时机从「commit 前」移到「push 前」，且不用每次 commit 都审**）：
   在 #1 / #3 的用户确认规则之外，再加一层独立审修门；它不替代方案确认、副作用确认，也不替代 #4 的 `doc/` 落点与改动简表。触发点：
   - **push 之前**：对**自上次 push 以来**本次要推的全部改动（代码 / md / bureau 文本）统一审+修一轮，通过后才 push。
     **commit 可自由进行、不逐个审**；开发迭代中的中间产物也不审，攒到 push 前一次过。
   ⚠ **本条 2026-07-24 再次由用户改定，领先于 canon**（此前是「commit 前」）：**ADR 0010 现为 `contested`**——原并列
   Claim A（双触发点，2026-07-06 canonical）/ Claim B（单触发点·commit 之前）都已被本次「push 之前」**再次覆盖**；
   须补 capture→compile、走 `bureau:review` 人门裁决。裁决前**以本条（push 前、不逐 commit 审）为现行执行规则**。
   按制品类型分工：
   - **代码 / 脚本**（假 exe、run_derisk.sh、skill 脚本等）→ **`cc-suite:audit-fix`**（9 维代码审→修→验循环）；
   - **散文**（CLAUDE.md 规则 / bureau 决策文本 / 设计 md）→ 底层 `codex exec`（Codex CLI）定制审（cc-suite 的代码维度套不上散文、且过重）。
     默认模型 **`gpt-5.6-sol`、reasoning `low`**（`codex exec -m gpt-5.6-sol -c model_reasoning_effort=low`）。
     （注：早先的 `mcp__plugin_nlpm_codex-cli__codex` MCP 由 nlpm 1.1.0 提供，nlpm 1.1.1+ 已移除该 MCP → 一律走 `codex exec` CLI。）
   **只跑一轮**：audit → fix → verify 各一次即收工（`cc-suite:audit-fix` 默认最多迭代 3 轮，**别迭代、太久**）；
   verify 剩下的 finding 如实列进结论、交用户定夺，不自动再修下一轮。
   audit-fix 结论需复述「发现了什么、改了什么、还有什么风险」；粒度可按「一个逻辑制品 / 一次变更」批量。
   ⚠ `nlpm`（`nlpm:check/score/fix`）**不是本门**——它是 NL 制品（skill/agent/command）的确定性质量 lint，
   仅在打磨已发布 skills 时另行使用。理由与 provenance 见 bureau ADR 0010。
6. **开工前必须通读 canon**。动手做任何 durable 工作（改设计 / 建组件 / 写 bureau / FAIL 归因）前，先通读
   `canon/`——全部 `architecture/` dossier + `decisions/` ADR + `lint/findings.md`，并按 BUREAU 的 trust tier 读
   （只有 `canonical` 当事实；`proposed / verified / stale / contested` 存疑，载重前先核）。目的是让自己被已定
   决策 grounding、不重推 canon 已 settle 的东西（血教训：漏掉上游前提会一路错到底）。**通读（grounding）与
   `bureau:query`（按需查证）并用、不互斥**；canon 大到通读不划算时，再退回「`00-overview` + query 优先」。

## 这个项目是干什么的

**OpRunway = NPU 算子「验收（acceptance）」工作区。** 把下面这条三段式验收流水线，
做成可复用的 **workflow / agents / skills**：

```
任务书 + PR ──①用例生成(ST)──▶ 测试用例集 ──②NPU跑测──▶ NPU 精度对比 + 性能数据
                                  │                                   │
                                  └──③ 同一份用例喂 GPU 标杆 ──────────┘
                                          + 外部给的 GPU 标杆数据
                                          ───▶ NPU↔GPU 性能对比报告
```

- **Task 1 · 用例生成（ST）**：输入 = PR + 算子任务书（算子公式、功能、规格、数据类型、shape、属性、性能目标、精度目标、验收标准）。输出 = 一套覆盖**功能 / 精度 / 性能**的测试用例集（机读 + 人读），属系统测试（ST）。**这套用例是整条流水线的「脊柱」，Task 2/3 都消费它。**
- **Task 2 · NPU 跑测**：用 Task 1 的用例，在既定算子工程（先 **catlass**）上跑出 **NPU 精度对比结果** 与 **NPU 性能数据**。
- **Task 3 · 性能对比报告**：把 Task 1 的同一份用例作为 **GPU 标杆测试的输入**，外部会给出 GPU 标杆数据；与 Task 2 的 NPU 性能数据对比，产出 **NPU↔GPU 性能对比测试报告**。

> 详细设计、数据契约、组件拆分、开放问题见 **`doc/oprunway-design.md`**。

**阶段**：现以 **catlass**（`cann/catlass`，gitcode）打底构建，跑通后再泛化到其它算子仓。

## 涉及的代码仓（均在 gitcode.com 下）

`cann/catlass`（**当前重点**）、`cann/asc-devkit`、`cann/ops-sparse`、`cann/ops-blas`、`cann/ops-cv`、
`cann/catccos`、`cann/shmem`、`cann/oam-tools`、`cann/amct`、`cann/hixl`、
`cann/cann-recipes-infer`（`ops/tilelang`）。具体可能变化，大方向如此。

## 目录结构

```
OpRunway/                              ← 项目根（git 仓：GitHub lllyys/OpRunway · GitCode brian66237/OpRunway）
├── CLAUDE.md · BUREAU.md · README.md
├── doc/                               ← 所有 md / 图 / 设计文档
│   ├── oprunway-design.md             ← 设计与流水线总览（先读这个）
│   ├── oprunway-changes-brief.md      ← 改动简表（倒序，持续维护）
│   └── oprunway-todo.md               ← 剩余施工 TODO + 用教训钉住的硬约束
├── plugin/                            ← OpRunway 实现（自维护插件仓骨架：skills/commands；agents/workflows/manifest 待补）
│   ├── acc-common/                    ← Layer 0 契约 + Layer 1 确定性脚本（工具中立）
│   │   ├── specs/                     ← spec.json（isclose/sign/equal）
│   │   ├── new_example/               ← per-op runner `oprunway_<op>_runner.cpp` + `run_on_npu.sh`
│   │   └── gen_cases · repo_adapter · validator · perf_compare · fetch_source · run_workflow .py
│   ├── skills/ · commands/            ← Layer 2 薄壳（入口/编排；agents 在建）
│   └── bridge/                        ← 路线 B 桥制品（catlass 去风险）
├── canon/                             ← bureau 决策/ADR（durable 知识）
├── reports/                           ← 验收产物 reports/<repo>/<op>/<pr>/…（gitignore 不入库）
└── repos/                             ← clone 的算子仓（12+，~688M，gitignore 不入库）
```

> 本机**直连 gitcode 可 clone**（无需代理）。12 个仓均已 clone（被测仓 + 参考仓）。
> 注：`cann-recipes-infer` 的算子重点在子目录 `ops/tilelang`。这些是 `--depth 1` 浅克隆，无法按 tag 校验版本；需要特定分支（如 `9.0.0-beta.1`）时再单独取。

## 参考项目与复用边界（重要）

**精度/性能验收以 catlass 自身机制 + 算子任务书目标为准，不基于任何「跑没跑崩」式的 skill。**

- **`repos/catlass`（被测重点 + Task 2 事实依据）**：build/run/golden/perf 已调研，详见 `doc/oprunway-design.md` §4。
  关键：`scripts/build.sh <example> [-DCATLASS_ARCH=3510]`（950 必带 arch）；golden = CPU float32（`examples/common/golden/` 可源码复用）；性能用 **msTuner**（`tools/tuner`）出 `task_duration(us)`（**kernel-only，不含 H2D/D2H**）。
- **`repos/cannbot-skills`（方法论参考，gitcode `cann/cannbot-skills`）**：借鉴其**精度标准分类**（`ops/ops-precision-standard`，MERE/MARE 按 dtype）、**性能指标体系**（`ops/ops-profiling`，HBM 带宽利用率/矢量化比例/稳态 warmup）、**验收纪律**（检查点门禁 + JSON 机读证据 + 开发≠评测）。只借方法论，不引依赖。
- **`../cann-ops-test/`（姊妹项目）**：做的是另一条闭环（扫 950 算子→跑示例→提 issue→跟进），使命不同。其 `setup-env`（搭 CANN 环境）**仅环境前置可借鉴**；其 `ops-test` 的 4 层「跑没跑崩」判定**不用于**精度/性能验收。整套工程约定（见下）继承。

> catlass 是 CUTLASS 风格 C++ 模板库（matmul/GEMM 系），与 ops-nn 风格「示例算子仓」不同，Task 2 一切以 §4 调研为准。

## 工程约定（继承 cann-ops-test，构建组件时必须守住）

- **零硬编码**：仓名 / 路径 / SOC / 任务书位置 / 目标算子 / 精度阈值，统统不写死，运行时**探测或 `AskUserQuestion` 询问**。脚本里的默认值只作「常见值」呈现给用户确认。**私有主机名 / 真实远端路径经 `OPRUNWAY_*` 环境变量传入，不得进入 Git tracked 文件**；实际机器值只允许写入被 `.gitignore` 忽略的 `.oprunway/real-machine.env`。token / 密码 / 私钥连该本地文件也不得写。
- **零用户全局配置**：不碰 `~/.config`、不改 shell rc；所有产物落用户 CWD 下的 `reports/`。机器连接元数据仅使用仓内忽略文件 `.oprunway/real-machine.env`，不另造全局状态。
- **全程中文**：发现、确认、报错都用中文。
- **副作用先确认**：clone / checkout / 对外提交 / 删除覆盖，先列计划、点头再做；支持 `*_DRY_RUN=1` 干跑。
- **跑测多层判定**：退出码 → 强失败信号 → 强成功信号 → 待复核（UNCERTAIN 不阻塞，最后统一复核）。
- **不凭空捏造**：报告里的数字/错误必须来自真实日志/采集，推断项显式标 `(推断)`。

## 远程 NPU 环境

真机环境的唯一当前入口是 **[`doc/oprunway-real-machine-environment.md`](doc/oprunway-real-machine-environment.md)**。

- 跟踪文档只记录脱敏的环境能力、最近验证版本、探测方法和安全边界。
- 实际 SSH alias、容器名、runtime env、setenv 与用户态 vendor 路径放在仓根 `.oprunway/real-machine.env`；该文件已被 `.gitignore` 忽略。
- 脱敏模板为 `.oprunway/real-machine.env.example`。
- 目标机仍须按“任务书适配硬件 × op_def AddConfig”逐算子核定；环境 profile 不构成目标机选择依据。
- build、pytest、用例生成、验收和 profiler compute 全部在远程 NPU 环境执行，本地不跑。

## 发布形态（倾向，待最终确认）

**自维护一个 OpRunway 插件仓（skills + agents + workflows 都放），再把其中 skills 部分 external-sync 进 `awesome-ascend-skills`** —— 即 cannbot 同款模式（`awesome-ascend-skills` 只收 skills，agents/workflows 留自有仓靠 `/plugin install` 分发）。详见 design §7。

## 现状与下一步

- **现状**：主干 workflow 已建成 + 真机端到端验证 + 入库推公开远端。
  - **三层可移植架构**（`plugin/acc-common/`）：Layer 0 六份 JSON 契约 · Layer 1 确定性脚本（`gen_cases`/`repo_adapter`/`validator`/`perf_compare`/`fetch_source` + `run_workflow` 驱动，工具中立、无 Claude-Code 依赖）· Layer 2 薄壳（`commands/` + `skills/`；agent 入口在建）。
  - **真机三算子验证**（IsClose/Sign/Equal，真 A3 NPU）：裁决全对（精度=真 NPU vs numpy golden，性能=msprof 真 kernel-only vs 真内置 TBE 基线，门同时卡精度+性能）。加算子 = spec + golden + runner 三文件。
  - **入库 + 推公开远端**：初始版已推 GitHub `lllyys/OpRunway` + GitCode `brian66237/OpRunway`（署名 lys；`repos/` 等大件已 gitignore）；后续改动经 PR 入库。
- **在建**（产品形态入口）：真实输入 = **任务书（md 或链接）+ PR 链接** → **agent 自动**产 spec、决定跑哪些步、处理失败、出报告（**不再人肉搓 spec.json 喂 `run_workflow.py`**——那只是 demo 期临时入口）。标准 skill/agent 形式 + 跨运行时可移植。
  - **①② 本地建成（经 PR 入库）**：`fetch_source.py`（取材：任务书/PR→中立 JSON）+ `acc-spec` skill（任务书→spec，23 份真实任务书语料 grounding + codex 审）。
  - **下一步**：③ per-op runner 锚定 + 构建路径选择（**FAIL 先解耦 root-cause 再归因**）→ ④⑤⑥ 编排串联。清单见 `doc/oprunway-todo.md`。

<!-- bureau:start -->
@BUREAU.md
<!-- bureau:end -->
