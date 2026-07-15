---
title: Task spec is authoritative over PR
updated: 2026-07-15
status: proposed
---

# Task spec is authoritative over PR

验收以**算子任务书**为准，不以交付 PR 为准——18 个真实 PR 深读证明二者**不逐项对齐**：Fmod 任务书要 INT16、PR 实际交 INT32；im2col 任务书写 A2/A3、PR 主攻 950；RightShift 定 10× 性能目标、PR 内零性能证据。**⚠ 前置**：「任务书权威」成立的前提是**先确认这个 PR 确是这份任务书的交付**（见 [[Verify spec-PR correspondence before acceptance]]）——拿错 PR 去对任务书判毫无意义。（原列的「Equal 只注册 A2 未做 A3 → 按任务书判未达标」一例**已作废**：2026-07-09 确认 #2890 配错、Equal 任务未验收。）

含义（落到 [[OpRunway workflow three-layer architecture]] 的 Task 1）：

- `spec.json` 从**任务书**生成；validator 按 spec 判、不按 PR。
- 任务书↔PR 的落差进 `spec.task_pr_gaps`、显式标**待确认**，不当错、也不默默采信 PR。
- **PR 自带的 UT ≠ 精度验收**（多是 `TestGetWorkspaceSize` / `SUCCEED()` 只跑不比）；证据（精度 + 性能）由 **OpRunway 自产**，不指望 PR 里有。仓内**性能证据基本缺席**、精度证据强弱不一——这正是 OpRunway 的价值所在。

依据 `doc/oprunway-spec-pr-analysis.md`（41 任务书 + 18 PR 规律）。

## 标准来源路由（2026-07-13 用户明示强化：绝不信 PR）

用户 2026-07-13 定为**最高律令**：**验收标准始终基于任务书，PR 是被验收的，永远不要相信 PR。** 关键是把两件事分开：

- **被测物**（受审对象）= PR 的代码。用 PR **识别 / 构建 / 运行**被测算子**合法**（被测代码版本另有硬门 [[PR head commit is the tested object]]）。
- **验收标准**（判通过/不通过的尺子：dtype 集 / 精度阈值 / 性能目标 / 硬件 / shape·属性 / 算子语义→golden）**必须来自任务书**，或任务书引用的、**独立于 PR** 的权威源（如原 TBE 算子的算子信息库 `opp/built-in/.../tbe/config/`）。**从 PR 数据推出任何一条验收标准 = 违反 = bug。**
- **灰区厘清**：PR 的 `op_def` 声明可作**对照**（PR 声明 < 任务书要求 → 记 `task_pr_gaps`），但**绝不能当标准来源**。与 [[Target hardware and dtype set are determined per operator from taskdoc and op_def]] 的「任务书 + op_def 双源交叉核验」**一致**——交叉核验 = 对照查 gap，不等于把 op_def 当全集权威。

审计（session 0513d745 只读 workflow）定位当前**唯一硬违反 V1**：dtype 全集来源把被测 PR 的 `op_def` 当权威（`acc-spec` 的 `SKILL.md` + `taskdoc-to-spec.md` + `agents/acc-spec-extractor.md` 三入口把 PR 排在独立源之上）。纠正方向：dtype 全集 = **任务书显式表/规格 > 原 TBE 算子信息库（独立源）> 问用户**，PR `op_def` 仅对照（PR 声明 < 全集 → 记 gap，遮住 Fmod 式「PR 缩 dtype」缺口即危害所在）。其余 5 类（精度/性能/shape/golden/硬件）审计判**合法**（PR 建被测物 or 仅对照）。

**V1 已落地（2026-07-15，`45084c0`）**：acc-spec 三入口散文改定——dtype 全集来源路由为「任务书显式表 > 原 TBE 信息库（独立源，非 PR）> 问用户」，PR `op_def` 降为**仅对照记 gap**；独立源未接通/新算子 → 问用户、**绝不回退读 PR**。⚠「原 TBE 信息库」这条中间档当前端到端**仍未接通**（`fetch_source` 不取、样例 `reference.path` 多空/占位），接通前实际回退到问用户（= TODO(a)）。散文纪律非文件系统强制、留 proposed，待 `bureau:review`。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-06 检查点），[[session 7f7b1411-e1d0-47aa-93d5-19ccd6fcd130 · 2026-07-08]]（原 Equal 平台错配例，2026-07-09 撤），[[session 37223d6d-c20e-48a9-84f5-99aeaddb7f51 · 2026-07-09]]（Equal 例作废+前置「先验证对应」），[[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]（2026-07-13：绝不信 PR 律令 + 标准来源路由 + V1 硬违反），[[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：V1 落地 `45084c0`）
