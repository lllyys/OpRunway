# solver 线交接说明（入口文档）

交接对象：0923/0924 昇腾社区 Cholesky 算子批次的验收供给线——两个 skill
（repo-task-solver-accept 判定与验收、repo-task-solver-case-gen 造数与装包）、
已交付的十个任务包、以及一套进行中的对标改造。

**你手里有两样东西**：上游 PR（https://gitcode.com/Justbin/repo-task-atk-test/pull/15，
两 skill 代码现状）+ 本交接包（其余一切）。不依赖原工作区与远程容器。

## 先读什么（顺序即优先级）

1. `solver-handover-todo.md` —— 看板：20 条待办的状态、来源、背景指引；
2. `solver-handover-plan.md` —— 操作手册：六阶段执行序，每步带完成判据；
   **每完成一步回看板标 ✅ 填落点**；
3. `solver-skill-supplement.md` —— 已裁定原话（24a-24i 是 0924 裁定群）；
4. `review-bundle-issues-0924.md` —— 验收方 issue 清单原文 + 我方追注。

## 产物地图（对着你手里的东西）

| 你有什么 | 内容 |
| --- | --- |
| PR #15 的 skill/ 下两目录 | 两 skill 本体：criteria s1-A5、tests 86/86 绿；**尚无批量判定与纯脚本装包通路** |
| 本包 trees/solver-s3-tree/ | 批量通路 + gen 并行修复（plan 阶段 2 归并源） |
| 本包 trees/solver-s4-tree/ | 非批量纯脚本装包通路（同上） |
| 本包 taskbooks/ | 两份 0924 任务书（契约真源，issue 的评审基准） |
| 本包 standards/ | opbase 混合容差标准 a5e8e71 快照 + cholesky_precision 残差链蓝本 README |
| 本包 frozen/canonical_batched.json | 批量用例冻结件（种子与规格，公开自测域） |
| 本包 delivered-packages/ | 已交付十包（两个 tar，按任务书分装）——v3 产包结构先例 + 旧包兼容分支测试对象 |
| 本包 solver-s3-batched-spec.md | 批量 spec（归并与 T8 标题改写的对象） |

## 当前状态一页

- **已交付**：十包 v2（判定口径为交付时点 s1-A2/旧阈值），副本即本包
  delivered-packages/；与改造后口径的差异由 todo HT-19（v3 重渲）收口。
- **代码**：PR #15 = 现状；批量与纯脚本两条通路等待从本包 trees/ 归并（plan 阶段 2）。
- **对外未决**：任务书反馈清单（ε、容差表、URL、batchSize、potrf 残差基）待
  原负责人 路由；HT-15 等任务侧补字。

## 环境

python3.10+ 与 numpy/scipy/pytest 即可开工：criteria 全部测试、造数、装包、自检、
sim_dut 冒烟都本地跑。只有真机 NPU 执行与性能实采需要昇腾环境——没有就按 plan
记「待真机」，不阻塞其余全部工作；真机信息届时向任务侧申请。

## 授权边界（红线）

- 悬置/裁定类问题与任务侧确认，不自行拍板；
- 不 push、不 merge、不发对外 PR/issue/评论，除非获得明确授权；
- 验收私集种子不入包、不入公开仓（看板「原负责人补充描述」第 3 条）；
- commit 不加 AI 署名；对外署名用交接者自己的身份，**不署 liangyuansheng / lllyys**。
