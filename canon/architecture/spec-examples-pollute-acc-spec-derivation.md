---
title: Spec examples pollute acc-spec derivation
updated: 2026-07-14
status: proposed
---

# Spec examples pollute acc-spec derivation

**问题（2026-07-13 干净用户测发现）**：`acc-spec` 产 spec 前会读到**同名算子的、填满答案的真 spec**，构成「先看答案再推导」的软污染。根因是 `plugin/skills/acc-spec/references/taskdoc-to-spec.md` 曾把三份真 spec 指作「目标 schema」，而 `plugin/acc-common/specs/` 又躺着真算子答案（`isclose/sign/equal/neg.spec.json`）——运行时 reference 翻得到、文档又指过去。产 IsClose 的 spec 时读 `specs/isclose.spec.json` = 读**同一道题的标准答案**（threshold=0、target 0.95、硬件 Atlas A2/A3、语义改造 note）。

**病根**：把两种「参考」塞进同一处——参考 **schema 结构**（合理、agent 需要知道产出长啥样）与参考**具体数值答案**（有害、是先看答案再推导）混在一起。

**已修复（2026-07-14，Q1；4 路对抗核验 SOUND、全量测试绿）**：
- 真样例迁出运行时路径 `plugin/acc-common/specs/` → 顶层 `samples/specs/`；
- 新建**零真值空模板** `plugin/acc-common/spec_schema_template.jsonc`，taskdoc-to-spec.md 的「目标 schema」改指它、§0 内联示例中性化；
- acc-spec 三入口（SKILL / taskdoc / agent）写死「产 spec 阶段禁读任何 `.spec.json`（含 `samples/`）」硬纪律；
- 新增 `plugin/acc-common/test_spec_isolation.py` 把「真样例不得回流运行时路径」固化为回归测试。

同 [[A gate must validate the object that actually takes effect]] 与 [[What decides how to judge must derive from spec not caseset self-declaration]] 家族：让判定/推导锚在该锚的东西（任务书 / pr_facts + 空模板）上、别锚在答案上。

**残留边界（故本页 `proposed`）**：① 隔离是「改指向 + 迁位置 + 位置回归测试 + prompt 纪律」四件，**非文件系统沙箱**——NL agent 仍物理可 `Read` `samples/`，防污染靠纪律；② 「agent 确实被锚定」这一**因果**由 transcript 复盘推断、未机械坐实（自产 spec 与样例逐项一致，但 derivation 真做了、挂了出处，无法排除也无法坐实必然锚定）。旧 `verified` 认定（基于修复前的 taskdoc:5 指向 + `specs/` 含真答案）已随本次修复过时，降回 `proposed`、待 `bureau:review` 复核。

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]（2026-07-13：Q1 发现；2026-07-14：Q1 修复落地）
