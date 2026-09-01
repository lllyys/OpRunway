# OpRunway 当前 TODO

本页只保留尚未完成且有当前证据的工作。仓规以根 `AGENTS.md` 为准，已完成历史查 changes brief 与 Git。

## P0 · sparse R1（已立项，实施中 @ feature/sparse-r1）

- [ ] 按 [sparse-r1-implementation-plan.md](sparse-r1-implementation-plan.md) 走
  M0.5–M6；当前在 M0.5（普查已完成，见 [sparse-r1-census.md](sparse-r1-census.md)；
  fixture 钉板与 registry 冻结进行中）。编号与状态唯一源是
  [sparse-gap-learning-map.md](sparse-gap-learning-map.md) §0。
- [ ] E1 探明 arch35 真机——不阻塞 M0.5–M6 本地实施，阻塞 M7 真机验证与「正式支持」
  声明。事实核对仅剩 V4（sparse kernel 的 Task Type，需真机 msprof）。
- [ ] 立项同场待决：G2 上游改名、G3 任务书完整性门槛、G4 基线回填归属。

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

- [ ] msprof 真机 spike：用 sger 的精确 `--gtest_filter` 确认 op_summary 目录、列名、
  kernel task 类型、duration 单位与五次中位数；完成后同步两侧 `perf-protocol.md` 的
  “待实测”项。阻塞：目标机 sshd 在 kex 阶段拒连。
- [ ] A3 运行链：用 cdgmm、sger、srotg、srotm、ssymm、strsv 的现成 arch22 CSV 跑
  A3–A5，记录构建、GTest 映射、精度 JSON、性能 JSON 与 verdict；本轮只为现场事实基线，
  可跳过因任务包 CSV 不同而必然失败的 A2。
- [ ] 第一个真实矩阵乘系列 PR 走完 A1–A5 后，把现场摩擦回填两个 skill 的 CLAUDE.md；
  在此之前保留 prototype 标记，不把静态门与零上下文 eval 描述成正式验收通过。
- [ ] LAPACK producer 链机械门：验证跨调用参数的 dtype、长度、batch 对齐与调用顺序；当前
  FACTS 只保存自由文本调用描述，不能据此证明 batched 链闭合。

## 维护规则

- 只新增有真实失败证据与完成判据的条目；不为假想未来预建架构。
- 完成项从本页删除，在 `dev-doc/oprunway-changes-brief.md` 顶部记录结果。
