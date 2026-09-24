# solver 线交接说明（入口文档）

交接对象：0923/0924 昇腾社区 Cholesky 算子批次的验收供给线——两个 skill
（repo-task-solver-accept 判定与验收、repo-task-solver-case-gen 造数与装包）、
已交付的十个任务包、以及一套进行中的对标改造。

## 先读什么（顺序即优先级）

1. `solver-handover-todo.md` —— 看板：20 条待办的状态、来源、背景指引；
2. `solver-handover-plan.md` —— 操作手册：六阶段执行序，每步带完成判据；
   **每完成一步回看板标 ✅ 填落点**；
3. `solver-skill-supplement.md` —— Mr.0 裁定原话（24a-24i 是 0924 裁定群）；
4. `review-bundle-issues-0924.md` —— 验收方 issue 清单原文 + 我方追注；
5. `taskbook-0924-diff.md` —— 新旧任务书与 PR #44 的对照。

## 当前状态一页

- **已交付**：十包 v2（六非批量 + 四批量，纯脚本零数组）在
  `reports/solver-delivery-0923-v2/`，按任务书分装的两个 tar 在其 `by-task/`；
  指纹对账见 DELIVERY_LEDGER.md。判定口径为交付时点（s1-A2/旧阈值），
  与改造后口径的差异由 todo HT-19（v3 重渲）收口。
- **代码**：plugin 两 skill；accept criteria 在 s1-A5、tests 86/86 绿；
  **plugin 尚无批量判定通路与纯脚本装包通路**——在 `reports/solver-s3/tree`
  （批量 + gen 并行修复）与 `reports/solver-s4/tree`（非批量纯脚本）两棵树上，
  归并是 plan 阶段 2。
- **仓与分支**：本仓分支 `worktree-solver-accept-0923` 已推 origin；上游 PR 分支
  `solver-skills-0924` 已推 fork（brian66237），PR 待在 gitcode 网页创建。
- **环境**：本机只编辑与只读探测；一切执行在远程容器（机器信息在主检出根
  `.oprunway/real-machine.env`，保护根只读）；本地测试用带 scipy 的 venv。
- **对外未决**：任务书反馈清单（ε、容差表、URL、batchSize、potrf 残差基）待
  Mr.0 路由；HT-15 等任务侧补字。

## 授权边界（红线）

- 悬置/裁定类问题找 Mr.0，不自行拍板；
- 不 push、不 merge、不发对外 PR/issue/评论，除非 Mr.0 明示；
- 验收私集种子不入包、不入公开仓（看板「Mr.0 补充描述」第 3 条）；
- commit 不加 AI 署名；对外署名 liangyuansheng / lllyys。

## 产物地图

| 位置 | 内容 |
| --- | --- |
| plugin/skill/repo-task-solver-{accept,case-gen}/ | 两 skill 本体（发布物） |
| dev-doc/solver/ | 全部设计与裁定文档（本目录） |
| reports/solver-delivery-0923-v2/ | 已交付十包与对账 |
| reports/solver-s3/tree、solver-s4/tree | 待归并的批量/纯脚本实现 |
| repos/new_task_doc-0924/ | 0924 版任务书（issue 的评审基准） |
| repos/community_task（分支 pr-44） | 任务书 PR #44 增量 |
| repos/opbase | 生态精度标准（mixed_tolerance_standard.md） |
| repos/solver_tasks-main | solver 精度标准参考工程（残差链蓝本） |
