# parallel-plan：repo-task-doc-write 交互改造的多 agent 并行执行方案 v2

状态：并行化执行方案（2026-09-16）。v1 冷读评审 10 条（4 High）已逐条吸收。
上游：`doc-write-intake-plan.md` v3（READY TO BUILD）。本文只管分工、依赖、冲突
与合流；改什么、判什么以 plan v3 为准，冲突时以 plan 为准。

## 0. 并行边界

- **SKILL.md 单一所有者**（3,323 字节硬预算）；**next_questions.py 不拆**
  （四个设计决策的汇点）；两者全归 P2，且 P2 内部再分 A/B 两段（见 §5）。
- **语义走查不并行**：单一参与者（Mr.0）与单一记录者，串行合理；但各场景
  **状态独立**，每场景独立目录、独立起点（见 §6），不共享状态。
- 其余产物两两零共享源文件，可并行。收益以实测为准，不预估倍数；若要压
  关键路径，P1.A/B 交付即可启动 P2-A，P1.C 只阻塞 P2-B。

## 1. 执行图与合流状态

```
P0 契约冻结（主 agent）
 ├─→ P1.A 骨架批次 + ratchet 测试
 ├─→ P1.B 严格解析器 + 反例单测          （并行；P1.C 仅为 P2-B 前置）
 └─→ P1.C intake 模板 + key 一致性测试
        ↓ 交付（编辑完成）→ 主 agent 包级验证（已授权地点）→ 验证通过
P2-A 集成·阶段 A（主 agent）：next_questions.py + SKILL T3 接入 + A 组测试
     + 走查产物核验测试 → 验证 + 检查点存档
P2-B 集成·阶段 B（主 agent）：SKILL T1/命令区/总审段（B4 归 P2-A，不在此段）
        ↓
P3 语义走查（主 agent + Mr.0 输入）：B1–B3/B5/B6 场景 + 跑 P2-A 产物核验测试
        ↓
P4 收尾（主 agent）：defect-map、brief、Codex checkpoint（全新 thread 冷读七维）
```

**合流状态机**：每包两态——「已交付（编辑完成，只跑过自己的测试或未跑）」→
「已验证（主 agent 在已授权地点跑过该包判据）」。测试执行地点未获 Mr.0 裁定时，
一切包停在「已交付」，**可以继续编辑，不得宣布全绿**；裁定后统一补验证。

**已知阶段性失败（明列，不许临时绕门）**：存量 `test_next_questions.py` 钉旧批次
布局（实现约束在第 6 批等），P1.A 落新批次后该断言必红，**属预期**；它归 P2-A
按 plan v3 更新。P1 合流门 = 各包**自己的**测试文件绿 + lint 绿，不含全量；
全量绿是 P2-A 的出口条件。

## 2. 文件所有权（源文件两两不相交；运行期共享写入另列 §4）

| 包 | 独占文件 | 交付判据（对应 plan v3） |
| --- | --- | --- |
| P0 | `references/decisions-format.md` | D-1/D-4 全部载体契约 + `_review_signature` 接口签名成文；**冻结时记 sha256 作为契约版本** |
| P1.A | `references/taskdoc-elements.json`、`tests/test_batches_ratchet.py`（新） | 12 批 41 条；A1 绿；checklist 重渲以 **stdout 比对**核不变（不加 `--write`） |
| P1.B | `scripts/_review_signature.py`、`tests/test_review_signature.py`（新） | 白名单 + fail-closed；评审反例全集绿 |
| P1.C | `assets/intake-template.md`、`tests/test_intake_template.py`（新） | key 集合 diff 测试绿；判据行引模板原文 |
| P2-A | `scripts/next_questions.py`、`tests/test_next_questions.py`、`tests/test_walkthrough_artifacts.py`（新，走查产物核验）、SKILL.md 的 T3 行与失效句 | 三态/三模式/`--all`/D-6；旧布局断言更新；A2–A6 全绿（A2 的旧骨架用**测试临时目录构造**，不落 fixtures）；I1–I4；检查点存档。**走查核验测试的执行约定**：以 `WALKTHROUGH_DIR` 环境变量指定产物目录；P2-A 全量跑时该目录不存在→按 pytest marker 显式反选（出口条件记「除走查核验外全绿」）；P3 必须带真实产物目录显式执行它，产物缺失或不一致一律 FAIL，**不得以 skip 得到通过** |
| P2-B | SKILL.md 其余改动 | T1/命令区/总审段；SKILL' ≤ 3,323 实测 |
| P3 | `reports/<run>/<scenario>/evidence/`（ignored） | 每场景独立目录；产物齐后跑 `test_walkthrough_artifacts.py` 核验 |
| P4 | defect-map、changes brief | D1/D2/D3 + 摘要 |

**B4（`--all` 机械断言）归 P2-A**，不在 P3。走查产物核验的**代码所有者是 P2-A**，
P3 只生成产物并执行该测试。

## 3. skill 改动门前置（每个写 skill 文件的 session 各自满足）

`skill-edit-gate.py` 按 hook 收到的 **session transcript** 判前置。**P0 也在门内**
（decisions-format.md 在 `plugin/skill/**` 下，路径命中 `SKILL_PATH`）。写 skill
文件的每个 session（主 agent 的 P0/P2、子 agent 的 P1.A/B/C）任务书均含开工两步：

1. Read `.claude/hooks/skill-best-practices.md`；
2. 用 Skill 工具**调用** `skill-creator`（读其 SKILL.md 不算）。

**代码判据 vs 运行时行为**：以上依据是门的代码逻辑；「子 agent 的 hook 确实收到
其独立 transcript」是未实证的运行时行为——**首个子 agent 派发时实测**：若照做仍
被拦，暂停该包、上报主 agent 改为主 agent 代写该包（回退到串行），不擅自
`SKILL_GATE_OFF`。P3/P4 只写 reports/dev-doc，不触发门。

## 4. 运行期共享写入（源文件不相交 ≠ 运行无共享）

- 存量测试会写删固定路径 fixture（如 `test_parser` 的 `_tmp_*.md`）：**P1 各包
  只准跑自己的测试文件**；全量 pytest 由主 agent 独占执行（合流验证时）。
- 新增测试一律用 pytest tmp_path 独立目录，不落共享路径。
- 门的收据 `.claude/.skill-gate-receipt.json` 会被各 session 覆盖——列为**允许的
  运行时副作用**，不影响本轮判定，不纳入越界检查。
- P3 产物统一 `reports/<run>/<scenario>/evidence/`，同时满足仓规产物位置。

## 5. P2 内部检查点（保住 plan v3 的阶段回滚语义）

P2-A 完成并验证后**存检查点**：记录 next_questions.py、SKILL.md、
test_next_questions.py 的完整内容快照（scratchpad 副本 + sha256 清单）。
P2-B 失败回退时恢复到检查点——阶段 A 的 T3 接入与失效句保留，不陪葬。

## 6. P3 输入与场景隔离

- 「恰 2 次输入」是**每个快速路径场景**（B1、B2 各自）的指标，不是全 P3 总量；
  B3/B5 按步骤如实记录输入次数。每场景独立目录、从空 decisions 或声明的
  初始 fixture 起步，场景间零共享状态。
- 模拟输入须 Mr.0 事先授权并在走查报告标注；默认等真实输入。

## 7. 越界写检查（不拿 `git status` 终态当证明）

- **开工基线**：P1 派发前，主 agent 记录当前 `git diff` 摘要、未跟踪清单与
  全部待改文件的 sha256（scratchpad 基线文件）。当前树已有的改动（hook 修复、
  dev-doc 三份）属基线，不混入本轮增量。
- **合流核对**：逐包对照「基线 → 交付后」的增量文件集 = 所有权表；各子 agent
  的最终报告须**逐调用列出全部写入**（Write/Edit/一切写文件的 Bash），主 agent
  据清单核对目标路径不越界。**残余风险如实声明**：共享 worktree 下「越界写后
  又还原」无法被终态检查机械证明，只靠任务书禁令与报告义务约束；若本期实践
  发现自报不可信，下轮升级为独立 worktree 硬隔离，本期不预设。

## 8. 契约版本与变更传播

- P0 冻结时记 sha256；三包任务书写明所依据版本；各包交付报告须回报该版本。
- 契约缺陷上报后：主 agent **暂停全部已启动的消费者**——P1 三包，以及已提前
  启动的 P2-A（§0 允许其在 P1.A/B 交付后先行）——修订 P0、版本翻新，逐个评估
  受影响面：受影响的废弃交付**连同其已有验证结果**重发/重验，不受影响的确认
  版本后继续。合流要求全部消费者回报版本一致。

## 9. 调度与收尾

- P1 三包同一消息并行派发（general-purpose，同 worktree）；任务书 = plan v3
  对应节 + P0 契约（含版本号）+ 所有权表 + 门前置两步 + 只跑自包测试的约束 +
  写入自报要求。
- P2/P3/P4 主 agent 亲自做。P4 checkpoint 评审：全新 thread 冷读、七维、
  审 P1–P3 全部 diff。
