# C++ harness 生成 · 立项笔记（2026-09-03）

> **2026-09-03 更新**：方案已定稿为 v4.1（`dev-doc/harness-gen-plan.md`）——用户裁定
> case-gen/accept 零改动，新 skill 独立承载、边缘定位。本页「按方案 C 分层重放」的
> 首任务表述作废，移植清单以 v4.1 §7 为准。

本线（feature/harness-gen，基于 feature/sparse-r1 @ b0cb3ed）的任务：为算子生成 C++ 测试
harness——`test/<op>/<op>_param.h`、`<op>_test.cpp` 与 CMake 注册，对标 ops-blas
`test/frame/` 的 CSV 驱动 gtest 惯例。输入形态两种：

- **A：六件任务包 + 任务书**——包内 CSV 列语义已由 FACTS 定义，生成最顺；
- **B：开发者提供的算子工程**——无 FACTS，先从工程（头文件/aclnn 签名/param 先例）提取
  列语义再生成。

## 已核实的上游 harness 结构（2026-09-02 核查 repos/ops-blas，file:line 为当时坐标）

三层分工：frame 公共件 `test/frame/csv_loader.h` 只提供 ReadMap「按列名取字符串」原语，
零标量语义；各算子 `*_param.h` 决定列名、实虚部拆法、null 表达——分叉全在这层；
半例外 cgeam 借 frame 的 BlasFillMode 解码 fill token（语义一半在 param.h 一半在 frame）。

同语义并存的规范（生成器必须显式选择，不能各写各的）：

- 复数标量三种：`alpha_real/alpha_imag`（chemm 40745ad `chemm_param.h:45-46`、gemm arch35、
  ctrsm 样本）；`alpha_re/alpha_im`（PR!347–350 的 csymm/csyrk/cher2k）；`alpha_fill`
  token（cgeam `cgeam_param.h:26-27,45`）。
- null 指针两种：数值列魔法串 `"null"/"nullptr"`（chemm `chemm_param.h:47`）vs 0/1 标志列
  `nullAlpha/nullBeta`（四个新 PR）。

根因是 frame 没有标量列公共契约，param.h 逐算子复制改。上游收敛方向是 frame 加
`ReadComplexScalar` 类公共解析器并规定列名；本线生成器可先在生成侧统一（生成的 param.h
用哪种规范要成为显式输入或显式默认，并记录进产物）。

## 与本仓资产的关系（生成器的单一事实源）

case-gen 的 FACTS（`plugin/skill/repo-task-blas-case-gen/references/facts-schema.md`）已装齐
列语义：params 投影、case_controls、harness_profile registry 12 字段（status_vocab、expect
列、阈值列、种子列、entry_headers 等）。harness 生成器若以同一 FACTS 反向生成 param.h 的
读列代码，则 CSV 与 harness 两侧同源，COLUMN_NOT_READ 恒零、accept 的 A2 契约天然闭合——
这是形态 A 的核心设计判断，值得先过一轮 review-plan。

配套阅读：case-strategy/perf-protocol（生成侧约束）、accept 的 run-chain.md（A1–A5 链、
期望集口径）、`dev-doc/sparse-r1-registry-freeze.md`（registry 12 字段冻结面）。

## 样本与参考

- param.h 实样：`repos/ops-blas` 各 `test/<op>/`；PR 分支 feat/*-arch22 四算子
  （csymm/csyrk/cher2k/cherk）；plain ctrsm 的 18 列 CSV 样本（2026-09-02 微信两版本，
  表头同、数据行重掷，见当日核对结论）。
## 前身车道（重要：先读它，不要重新发明）

worktree `oprunway-blas-harness-gen`（分支 feature/ops-blas-harness-gen，HEAD 1486fc2）里有
一条**未 commit 的同主题车道**，2026-08-28/29 已走完 Increment 1+2：方案 v3 定稿
（`dev-doc/harness-gen-plan.md`，四轮 Codex 七维评审；终局 = case-gen 编译 FACTS 出
Contract IR → harness-gen 薄后端出 C++ → accept 拥有构建/运行；信任模型「如实、不过声」，
`harness_source ∈ {generated, developer}`）、冷启动交接（`dev-doc/harness-gen-handoff.md`，
含任何 session 可自跑的复验脚本）、`contract.py` + case-gen 首个 `tests/`（7 个行为测试）、
package.py 列投影单源化（方案 C，Mr.0 拍板，thread `01a04b84`）。**那个 worktree 在移植完成前
不许删**——工作全在未提交状态，强删即毁。

本线第一个任务不是从零设计，而是**移植**：把前身车道的 Inc1+2（contract.py、tests、package.py
单源化改造、plan/handoff 两文档、codex-review 的「半径与去重常冲突」原则段——本线缺此段）
重放到本线的新基座上。注意冲突面：sparse R1 已把 package.py/模板大改（registry 12 字段、
FACTS v2、v1/v2 分支），车道的 package.py 补丁是对旧基座写的，须按方案 C 的分层重做而非照抄；
落完跑 handoff §7 复验脚本 + 三道回归门，过 Codex checkpoint（package.py check 属核心逻辑，
无条件一轮），然后才可删前身 worktree。车道 plan 里「FACTS 升 v2」收尾项本线基座已交付，移植时
把该项划掉。

## 纪律

仓规见 `AGENTS.md`；评审七维与 audit-fix 同过七维见 `.claude/rules/codex-review.md`；
改 skill 先过 skill-edit-gate；真机操作读 `.oprunway/real-machine.env`（ignored，缺则先探）；
push、上游 PR 须用户明示。
