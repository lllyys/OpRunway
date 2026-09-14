# OpRunway 当前 TODO

本页只保留尚未完成且有当前证据的工作。仓规以根 `AGENTS.md` 为准，已完成历史查 changes brief 与 Git。

## P0 · sparse R1（已立项，实施中 @ feature/sparse-r1）

- [ ] push 前 audit 遗留（thread `01a0615a`，裁 FIX_NEEDED 无 P0，用户裁定本轮不修直接提）：
  P1×4——rerun.sh 只给 check 传 `--device-pool`（A3/A4/A5 复跑静默用默认池）；
  性能复跑无条件 `--skip-build`（编译期定卡域遇 auto 必中断）；device_pool 未入
  evidence_id/A5 契约绑定（改池不改证据身份）；闭合校验不核 npu_gate 内部一致性、
  且 npu_gate/final 为非字典时抛 AttributeError 而非裁证据不足。
  P2×4——报告与 environment.json 缺 device_pool/device_resolved 贯穿；显式卡号也被
  标「auto 定卡」；`--device-pool ''` 被静默扩成默认池、`--device -1` 被接受；
  128 字符 run_id 加 `-rerun` 后超上限。修复时两模板与 accept 三处解析器须对称同改。
  七维预警（修复时按此调整，勿照单全收）：pool 只入 evidence_id 与 A5 契约绑定，
  不进 runtime manifest——manifest 是设备无关的包身份，塞运行参数破坏包跨卡复用
  （通用性/爆炸半径）；attempts 有序前缀校验会把遍历实现细节升成契约（泛化性），
  要么明写冻结要么不加；evidence_id 加键属对外契约变更，无条件过 checkpoint；
  三处解析器无对称性机械门，重演 perf 模板漏块事故的温床，宜先补对称门再改。


- [ ] accept 依赖 case-gen 私有名 `_harness_registry()`（跨 skill 隐式接口，M2·5·6′
  checkpoint 非阻断观察）：改公开名或在冻结文档固定调用面。

- [x] **msprof 性能通路修复(wave 0-4 全部完成;@ fix/msprof-perf-pipeline,待 Mr.0 审 diff
  与 commit)**:实施入口 [msprof-perf-fix-plan.md](msprof-perf-fix-plan.md)——A1 去 export、
  A2 采集开关、A3 重复键容忍、A7 单次免 warmup、进度反馈、产物目录参数,六件一体。
  V-3 实测 msprof 不透传 application 失败,CRASH 判据改「gtest JSON 执行成功证据」
  (Codex 两轮评审均已吸收);真机回归:V-4 ctpmv 200 例 195 PASS/5 FAIL(5 例=0911 已知
  小尺寸窄面,launches 全 [1],23.4min)、V-5 三项全过(全链/外指/无 summary),四条判定线
  全部端到端直证。记 plan 附录 A.1-A.11。**下面两条(双份计数、提速)随本条一并关闭。**
  口径注记:V-4 载具经 Mr.0 授权由 cgeru 改 ctpmv,isolated-acceptance 无头正式口径未走
  (A4 直链+V-5 sger 小包 A1→A5 覆盖判据),不据此宣称任何算子正式通过。
  wave 3 checkpoint 已过(FIX_NEEDED 3P1+3P2 全修,Codex verify 六项 FIXED,记 plan A.8)。
  新遗留(本轮发现,不阻塞):①两协议「阈值可否覆盖」既有分叉(accept 版 0.8 固定 vs
  case-gen 版 FACTS 可覆盖),本轮未动;②build.sh 默认 Debug 无 Release 开关的隐藏契约
  再次踩中(V-2 首建即中),上游修法已有 #363 先例,本仓侧待议;③Codex 澄清的两个 HEAD
  既有分叉:accept 对非数值基线记 None vs 模板拒绝非数值/非正数(严格度差异)、case-gen
  协议 NO_REF「只采集不评判」旧句与期望集过滤(无基线不跑)不一致。
- [x] **修 msprof op_summary 双份计数 bug**（详见
  [msprof-op-summary-double-count.md](msprof-op-summary-double-count.md)）：
  `--application` 自动导出 + 显式 `--export=on` 导两遍，`parse_op_summary` glob 后不去重全累加
  → launches/kernel_us 翻倍 → 好算子假 FAIL。Ctpmv/910B3 与 cgeru/950 均实证（跨平台）。
  **Mr.0 定案 = 采集层不跑显式 `--export`，只靠 `--application` 自动导出 → 单份 op_summary，
  第二份根本不产生（不留 md5 去重/launches 机械门等兜底，就这一条）。** 前提：保留
  `--ai-core=on --task-time=on`（否则无数可导，见下条 A2）；自动导出平台相关，换平台/CANN 需真机
  确认确实产 op_summary，否则解析取不到数、自然裁 NO_KERNEL。改采集命令 + 核心解析口径，
  无条件过 checkpoint 评审。
- [x] 修默认 msprof 采集参数不足：`msprof --application` + `--export=on` 在 a3/CANN 9.0.1
  上报 "no summary data to export" → 0 op_summary → NO_KERNEL；需加 `--ai-core=on
  --task-time=on`。与上一条同属 accept msprof 通路。
- [x] 提速（现状每 case = warmup + 5 采样各起一次 msprof，200×6≈1200 次，~3.5h）：
  **Mr.0 定案 = 每 case 单次采样、免 warmup、不批量——全 200 例跑 200 次 msprof。**
  改法：去掉 5 次取平均、去掉我方 warmup（msprof 内部自行预热），每 case 6 次→1 次。
  1200 次 → 200 次（~6×，~3.5h → ~30–35min，a3 单次 msprof ~10s/case 外推）。
  **不走批量（方案一）**：批量本可 ~12× 更快，但 demux 无 case 锚点（A5）且照样双份计数（A1），
  风险高于那点速度；每 case 单发时每次 msprof 只跑一个 case、输出天然归属，**不需要 demux**，
  A5 拆分前提随之作废。**A1 也从源头消除**：定案不跑显式 `--export`，只留 `--application` 自动导出
  → 单份 op_summary → 无双份计数（不必再 md5 去重，见上条）。落地前真机确认 msprof 单次内部预热
  行为、自动导出在各目标平台确实产 op_summary。依据见 msprof-perf-issues-summary.md A8/A7/A5/A1。

- [x] 按 [sparse-r1-implementation-plan.md](sparse-r1-implementation-plan.md) 走
  里程碑序；全部里程碑完成（M7 真机证据 2026-09-02，「正式通过」待 G4 基线回填复验；
  普查见 [sparse-r1-census.md](sparse-r1-census.md)；
  fixture 钉板与 registry 冻结进行中）。编号与状态唯一源是
  [sparse-gap-learning-map.md](sparse-gap-learning-map.md) §0。
- [ ] E1 探明 arch35 真机——不阻塞 M0.5–M6 本地实施，阻塞 M7 真机验证与「正式支持」
  声明。事实核对仅剩 V4（sparse kernel 的 Task Type，需真机 msprof）。
- [ ] 立项同场待决：G2 上游改名、G3 任务书完整性门槛、G4 基线回填归属。
- [ ] 存量陷阱（fixture 已钉现状，blas 现网带着跑，本期不修）：trap1 dtype enum
  无消费者崩物化、trap2 perf.key 派生键静默重算、trap3 复标量 edge 裸数字崩、
  trap5 生成器/校验器口径分叉、trap4/6 的 blas 存量面（重复轴值/命名空间碰撞与
  pairwise 不收敛）。修法与通则见 fixture README 处置表。

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

- [x] msprof 真机 spike——由 msprof 性能通路修复轮(2026-09-14)完成并超额覆盖:
  op_summary 目录/列名/task 类型/duration 单位已实测,两侧 `perf-protocol.md` 待实测项
  已同步;「五次中位数」口径已废(单次采样定案,见 plan 附录 A)。
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
