# OpRunway 会话交接 · 2026-08-07（切换到纯 Codex 驱动）

> **本文是当前交接入口。** 用户已决定从这里起完全切换到 Codex CLI 直接驱动，不再经过 Claude Code
> 编排。本文只写「接下来做什么、从哪开始、有什么坑」，历史讨论过程不重复。

---

## 1 · 一句话现状

两个算子（`aclnnBernoulli` / `aclnnRemainderTensorTensor`）的接入 gap 清单与实施方案已定案，
经三轮 `codex audit-fix` 收敛。**P0 只读前置核验已全部完成（3/3）**，结论均已落账。
**尚未开始任何 `plugin/` 代码实施**——下一步直接进入 `C2 → B10`。方案已获用户授权可以开工（见 §6）。

---

## 2 · 权威文档（严格按这三份，唯一依据）

| 文档 | 作用 |
|---|---|
| `dev-doc/oprunway-bernoulli-remainder-gap-todo.md`（116 行） | **唯一 gap 依据**。19 个在册 gap（G2–G8、G10–G21），已按「谁能解决」分组，0 项待人工确认（三项已裁定，见 §5） |
| `dev-doc/oprunway-bernoulli-remainder-plan.md`（207 行） | **唯一实施依据**。P0 + Phase A/B/C，逐批「做什么/落点/完成判据」，完成判据均可机械验证 |
| `AGENTS.md` §5.12 | 新增：核心验收维度永远是精度 + 性能，资源类不是验收轴（与 §5.10 分层，见区分表） |

任务书与被测代码路径（不变）：

```
任务书 A：repos/cann-ops-competitions/04_tasks/01_community-task-2026/docs/202607/aclnnBernoulli_task_doc.md
代码仓 A：repos/ops-math-feat-aclnn-bernoulli-memory-optimization
任务书 B：repos/cann-ops-competitions/04_tasks/01_community-task-2026/docs/202607/aclnnRemainderTensorTensor_task_doc.md
代码仓 B：repos/ops-math-feat-aclnn-remainder-tensor-tensor-memory-optimization
```

⚠ `docs/202607/self_test_case/` 目录**不算输入**，不参与判据（gap 清单 §0 已述）。

---

## 3 · P0 结果（三路只读前置核验，已全部完成，并已转成正式 gap + 批次）

完整证据链已归档：`.cc-suite/audits/audit-p0-readonly-findings-20260807.md`（比下表详细得多，
实施前先读这份，不要重新分析）。

| 项 | 结果 | 转成的 gap / 批次 |
|---|---|---|
| **P0-a · format ND** | ✅ RESULT=属实 | **G22**（gap-todo.md 组 2）+ **B16**（plan.md）。⚠ B16 的完成判据带一个关键约束：`extended` stage-2 已有显式 ND 转换器，但 `standard` 遇非默认 format 目前是**生成期直接拒**——若两个算子的实际接口形态是 `standard`，光是「拒绝」不构成修复，必须补 `standard` 的等价 ND 支持，否则要如实报告「G22 未关闭」 |
| **P0-b · CANN 版本门** | ✅ RESULT=属实 | **G23**（组 2）+ **B17**。⚠ B17 已改成通用化设计——最低版本要求从任务书/spec 读，门比较「要求值 vs 实测值」，**不是硬编码 8.5.0**（避免违反 §5.1 泛化优先） |
| **P0-c · dtype 四类归因契约** | ✅ RESULT=未区分 | 并入 **B4** 的完成判据。归档文件给出四类成因（API 拒绝/harness 限制/证据不完整/已确认 DUT 缺失）各自的具体断链位置，B4 直接按那四行改，不用重新分析 |

**下一步顺序（plan.md §6，已更新）**：`B16 → B3-a → B2`（三者同改 `cpp_extension_codegen.py`，
B16 先做避免冲突）；`B17` 与主链无文件重叠可随时插入；随后 `C2 → B10 → A0 → A1 → A2 → …`（完整顺序见 §4）。

---

## 4 · 严格按这个顺序（plan.md §6 原文，已含 B16/B17）

```text
P0-a + P0-b + P0-c（已全部完成）
  → B16（G22 format ND）                             （落地前必做，其后 B3-a/B2 才在同一文件基础上改）
  → B17（G23 版本门）                                  与 B16 平行，与主链无文件重叠，可随时插入
  → C2 → B10
  → A0 → A1 → A2
  → B11 → B12
  → B3-a → B2；B3-b                                    （B16 之后再动 cpp_extension_codegen.py，避免同文件冲突）
  → B4（先按 P0-c 归档的四行断链证据补契约）；B5 ↔ B14；B6；B7 → B9 → B13（RNG 前提验证收据）→ 正式 Bernoulli 精度取证
  → B8
  → 真机见证
```

⚠ 三条硬约束，容易被漏掉：

1. `C2 → B10` 必须早于**任何**真机精度或性能取证——它同时是「不让 baseline 悄悄调到 DUT 自己」
   的性能卫生门，也是「Bernoulli 精度 oracle 完整性」的前提（标杆走 `torch_npu` 派发，若仍加载 DUT
   vendor `.so`，会形成自己与自己逐位相同的假通过）；
2. `B13` 只产**不可用于裁决**的「RNG 前提验证收据」（`evidence_grade=precondition`、
   `usable_for_verdict=false`），必须与正式 Bernoulli 精度证据物理隔离，通过后才能开始正式取证；
3. `G18`（Remainder 硬件冲突）、`G20`（Remainder 目标目录矛盾）已由裁定 G/H 给出**具体处理方式**
   （见 §5），不再是「未确认前阻断」，而是「按裁定方式执行 + 如实记账」。

见证依赖（plan.md §6 原文）：

| 见证 | 硬依赖 |
|---|---|
| Remainder | P0；裁定 G（A2/A3 真实构建）；裁定 H（`--target-dir math/floor_mod`）；C2 → B10；A0 → A1 → A2；B11 → B12；B3-a → B2，B3-b；B4；B5；B8（`aclnn_builtin`，裁定 F 已固定，无需再等确认） |
| Bernoulli | P0；C2 → B10；A0 → A1 → A2；B11 → B12；B3-a；B4；B5 ↔ B14；B6；B7 → B9 → B13；B8 |

---

## 5 · 三项裁定（本次会话新定，已落进两份文档，不要再问）

技术决策者按「最遵守原计划 + 最短实施时间」裁定，均 fail-closed + 如实记账：

| 裁定 | 结论 | 记账要求 |
|---|---|---|
| **F（G11a）** | baseline = `aclnn_builtin` | 报告须注明这是**解释**（「原算子」= 优化前的当前实现）；身份/指纹/双符号定义者不符即 fail-closed，**不得退换其他 baseline** |
| **G（G18）** | Remainder 仍以任务书 A2/A3 为验收目标，**不改到 `ascend950` 验收** | spec 保留 A2/A3 要求，记录 PR 只声明 `ascend950` 的冲突；在 A2/A3 上真实构建，失败或无注册 → 受控阻断，禁止 PASS；`ascend950` 最多作 development 诊断 |
| **H（G20）** | 取材 `--target-dir math/floor_mod` | source_facts/spec 同时记录任务书 `:68` 的 `experimental/math` 矛盾；目录矛盾 + 「实际交付未位于 experimental/math」列入 `task_pr_gaps` |

---

## 6 · 授权状态

用户已用 `/goal` 显式授权：读方案 → 实施 → 每轮 codex audit-fix → 遇到需要人裁的问题交给
「codex 按最遵守原计划 + 最短实施时间」自行决定，**不要停下来问用户**（用户会不在场）。
按仓规 §5.2「先出方案再实施」这一门槛**已经跨过**——plan.md 就是那份方案，已经在跑。

⚠ 但 §5.2 其余条款仍然有效：clone/build/真机跑测前仍要走 §5.3 的执行边界（本地只读，
compute 在 NPU 目标环境）；删除/覆盖仍需谨慎。

---

## 7 · 怎么用纯 Codex 驱动（不再有 Claude Code 编排层）

### 只读核验 / 审计类任务

```bash
cd /Users/ll/Desktop/workspace-ascend/OpRunway/.claude/worktrees/oprunway21
codex exec -m gpt-5.6-sol -c model_reasoning_effort=medium --sandbox read-only --skip-git-repo-check - <<'EOF'
你是只读代码审计员，不要修改任何文件，只读代码给出证据链。
<把要核验的问题写在这里，要求最后一行输出 RESULT=... 结论>
EOF
```

### 实施类任务（真正改代码）

```bash
codex exec -m gpt-5.6-sol -c model_reasoning_effort=medium --sandbox workspace-write --skip-git-repo-check - <<'EOF'
<把 plan.md 里对应批次的「做什么/落点/完成判据」原文贴进来，要求逐条落实，
并要求 codex 自己写 fixture/测试验证完成判据，不接受自评式表述>
EOF
```

### audit-fix 循环（每轮实施后必做，用户目标第 2 条）

1. **audit**：只读方式审这轮 diff，重点核完成判据是否真的可机械验证、有没有新的 fail-open；
2. **fix**：`--sandbox workspace-write` 按审计结果改；
3. **verify**：再来一轮只读审计确认修好了，不要自己复述「已修好」。

三轮做法参考本仓 `.cc-suite/audits/audit-fix-20260806-gap-revalidation-findings.md`
（记录了完整的 audit→fix→verify 三段式，可作模板）。

### 遇到需要裁定的问题（用户目标第 9 条）

不要停下问用户。让 codex 自己权衡「最遵守原计划」和「最短实施时间」给出裁定，
参考本次会话对 G11a/G18/G20 的裁定格式：

```
【裁定】<一句话结论>
【依据】<为什么这样最符合原计划 + 最省时间>
【落地动作】<具体改动>
【风险与记账】<可能错在哪；报告里必须如实记什么>
```

裁定落地后，**同步更新 gap-todo.md 与 plan.md**，不要只改代码不改文档——两份文档必须与
实际执行状态保持一致，下一轮/下一个人才能凭文档知道现在在哪。

---

## 8 · 已知缺口（交接时发现，均已处理完毕）

- ~~P0-b 属实之后缺一个对应批次~~ **已处理**：P0-a/P0-b 均已转成 G22/G23 + B16/B17，
  已过一轮 codex audit-fix（发现 4 条真问题：B16 完成判据未覆盖 standard 派发形态、
  B17 曾把 8.5.0 硬编码进通用门、§6 顺序自相矛盾、`:99-105` 引用来源标错，均已修正）。
- ~~P0-c 未完成~~ **已处理**：P0-c 已跑完，结论「未区分，需 B4 先补契约」，
  四类成因的具体断链证据已并入 B4 完成判据。
- **§6「支持在线和本地传入」（用户目标第 6 条）现状**：`fetch_source.py` 的
  `--pr`（在线）与 `--pr-snapshot`（本地）已是平级一等输入形态，`declared_source_form` 入口即定、
  未声明按最严档处理、不停下问用户——**基础设施层面已经满足**，`plugin/skills/acceptance-workflow/SKILL.md`
  也已写明「本地代码是一等输入形态，不是降级路由」。**这条不需要重新实现**，只需要在后续新增能力
  （dtype 表、轴声明等）时不要破坏这个对称性即可。

---

## 9 · 当前 git 状态

```
HEAD: 1351e22 docs: 裁定 F/G/H 落地——G11a/G18/G20 从待确认转为已裁定+记账
未提交: canon/logbook/2026/08/ 下两个 bureau logbook 分钟文件（跨会话共享目录，
        67d526fa-... 是本会话产出，b256a095-... 来自并发的其它会话，不要动）
```

`plugin/` 目录**没有任何未提交或已提交的代码改动**——全部工作到目前为止都停留在文档层。
