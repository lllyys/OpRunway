# OpRunway 当前 TODO

本页只保留尚未完成且有当前证据的工作。仓规以根 `AGENTS.md` 为准，已完成历史查 changes brief 与 Git。

## P0 · 已证明的 DUT 修复后复验

- [ ] RemainderTensorTensor：补齐 `ascend910_93` 的 kernel source、op_def config 与安装交付后，
  用镜像 skill 重跑 A3。完成条件：fresh build 产出非空 A3 ops-info/binary/kernel delivery，并形成
  完整 accuracy/performance 证据；不能只增加 host ACLNN 符号。此前一轮已产生
  `DUT_FAIL / TARGET_DELIVERY_MISSING` 终态（本地 reports/ 已清空，结论以 changes brief 与 Git 历史为准）。

## P1 · 迁移收尾

- [ ] c_api 真机端到端：用 isolated-acceptance 无头通路跑 aclblasTrsmBatched 完整
  S1–S5。完成条件：accuracy/performance 证据齐全、verdict 产出，并把现场摩擦回填
  reference。
- [ ] 把验收 skill 拆成两个独立 skill：用例生成 S1–S2 与测试验收 S3–S5，零共享文件。
  拆分时两侧各自复制 `_soc_binding`、`_stage_card`、`artifact-contracts.json` 骨架
  （跨五阶段，需一分为二）、`mark_step`、`probe_progress`、`timeline.jsonl`、
  `probe_env`、`env.sh`、`glossary.md`、`experimental_standard.md`。现行防漂移纪律要求
  共享模块禁止局部副本，拆分后改为各自持有并允许各自演化。完成条件：两个 skill
  各自独立通过回归与一轮真机验收。
- [ ] 演练一次上游同步（fetch → `git diff --binary | git apply --3way --directory=plugin` → 更新
  `plugin/.claude-plugin/upstream.json` 基线）。

## P1 · 矩阵乘系列验收（新方向）

- [ ] 真机跑通 ops-blas 已有算子测试作为事实基线（锚点候选 sasum 与 cherk），记录结论后
  再决定 skill 如何承接三职责。心智模型与公私边界见
  `dev-doc/matmul-series-acceptance-mental-model.md`。完成条件：至少一个已有算子在目标机上
  build + GTest 全流程有据可查，结论回填该文档。

## 维护规则

- 只新增有真实失败证据与完成判据的条目；不为假想未来预建架构。
- 完成项从本页删除，在 `dev-doc/oprunway-changes-brief.md` 顶部记录结果。
