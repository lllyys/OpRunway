# OpRunway 当前 TODO

本页只保留尚未完成且有当前证据的工作。仓规以根 `AGENTS.md` 为准，已完成历史查 changes brief 与 Git。

## P1 · blas-accept 复测（已落地 @ worktree blas-accept-retest，见 brief 2026-09-18）

- [ ] Mr.0 审 diff 与 commit（三笔拆分建议：①量具证据保护+锚 ②量具 warmup
  ③复测全量——量具复测模式+accept+折叠核+测试+文档；回滚合法序仅逆序）。
- [ ] **换卡映射串的顺序依赖**（2026-09-23 实测，与 MR #13 通用性有关，本轮范围外）：
  `ASCEND_RT_VISIBLE_DEVICES` 取逗号列表时，首项非 0 就 `SetUpTestSuite` 失败。
  实测 `5` 与 `0,5` 通过，`5,0` 与 `2,1` 失败；**裸跑（不带 profiler）结果相同**，
  是被测二进制或 CANN 的既有行为，不是采集后端引入的。现行换卡映射串都以 0 打头，
  所以现状没被打穿，但「任意物理卡互换」这个通用说法目前没有证据支撑，需补验或收窄措辞。
- [ ] P7 换卡门控版：差「显式非零编译卡完整链」真机形态 + Mr.0 裁定启用；
  实施时 profile 绑卡推导走数据不走分支，附录 A 契约已在 spec §11。
- [ ] 上游 PR 另立项（两 blas skill 的复测面）。
- [x] 采集后端换 `msprof op` 并撤掉 warmup——**代码与文档端到端完工**（2026-09-24）。
  契约 `blas-msopprof-spec.md` v2、计划 `blas-msopprof-plan.md` v2。真机验过 V1
  （ctpmv 三例读数与独立测量逐例吻合）、截断 fail-closed、复测轮折叠与替代告警。
  预算 lint 退 0，blas 两 skill 行文零拦截，测试 131 全绿，两示例逐字节一致。
- [x] **验收者可用性三道真机门全过**（2026-09-24）：
  - **M1 多 launch 真机正例**：cherk 在 a3 建不出（只有 arch35，本机是 arch22 档），
    由 `ssymm/arch22` 顶替。TC_L1_04（m=512 n=512）实测 **194 个 launch、5 种 kernel、
    194 份 CSV、92866.70 us**；解析器返回的行数与总和逐一对上。同一份产物上
    **扁平 glob 命中 0 份、递归 glob 命中 194 份**——递归是必需项。
    截断在这份真实数据上 `--launch-count=194` 触发、`=512` 放行。
  - **sparse**：a3 上无任何 sparse 材料，改用机械判据关闭——两个 `harness_profile`
    渲染出的量具**只在 `HARNESS_PROFILE` 与 `BUILD_CONVENTION` 两个常量上不同**，
    采集通路逐字节相同（`test_profile_parse.py::ProfileSharedCollectionTest` 固化）。
    所以 blas 的真机证据覆盖 sparse 的采集面；sparse 独有的绑卡面本轮未动。
  - **零上下文 isolated-acceptance**：无头会话只凭 `SKILL.md` + `references/` + `scripts/`
    跑完 A1(0) → A2(0) → A4(1)，产出完整结果 JSON，**自报「没有缺口，没有卡在任何一步」**，
    并正确引用 perf-protocol 的口径变更做保守解读。第一轮因环境未备齐而正确停下并
    指出缺口（见下条），备齐后一次跑通。
    注：该轮工程是 **Debug 构建**（`CMAKE_BUILD_TYPE=Debug`），读数比 Release 高约四倍，
    机制有效但数值不可与 Release 对照，不能当算子结论读。
- [ ] **零上下文实跑抓到的两个既有文档缺口**（2026-09-24，isolated-acceptance 无头会话，
  非本轮引入，但确实卡住验收者）：
  - **skill 怎么到目标机没写。** 文档里的 `<skill>` 指的是本机上放 `SKILL.md` 的目录，
    而命令要在容器里跑；且 `accept.py:185` 要求 `repo-task-blas-case-gen/scripts/package.py`
    与它并排存在，文档只说「还原完整 plugin」，没说并排这个硬约束。零上下文会话据此
    停在 A1 之前，判断正确——目标机上那份旧 case-gen 的 `package.py` 哈希不同，
    用它会悄悄换掉量具。
  - **首轮跑在非编译卡上没有通路。** 编译期 `TEST_DEVICE_ID` 固定，`--map-device` 与
    `--compiled-device` 的重映射只在复测轮有文档，首轮没有。会话按文档拒绝即兴发挥。
    这与 P7 换卡门控是同一件事的两面。
- [ ] V7 余下两条跨契约路径未在真机上验：旧历史组合（旧首轮加已有旧复测轮仍折叠）、
  换卡轮。两者都有单测覆盖，缺的是真机证据。
- [ ] 测试瘦身（低优先，下次动 schema 时顺手做）：`test_fold.py` 的形状违规表驱动
  约 12 条测的是加载层契约保证到不了折叠层的输入（非 dict 轮、布尔轮号、空 case 名、
  错误容器类型等），属信任成本维的过度防护。现在删的收益小于工时，改 schema 时一并清。
  非有限数 ratio 那组保留——JSON 解码器确实会放进 NaN，是真实场景。

## P2 · 行文机检（上游 rules/hooks 已移植 @ worktree blas-accept-retest 与 solver-accept-0923）

- [ ] 补段落层级四条的机检：行长 ≤100、连续 ≥3 单句段、同段连续 ≥3 行句号结句、
  单段 >5 句。上游 `doc_style_lint.py` 只覆盖 ZH-xx 用词句子层与 SA-xx 结构层，
  这四条不在其中；`prose-style.md` 原先声称由 `tests/test_document_style.py` 实现，
  该文件在本仓从未存在（2026-09-22 核出并已改正文档）。棘轮走暂存行不走计数基线。
- [ ] 配 `scripts/check_skills.py --changed` 与 `.githooks/pre-commit`（上游已有，未移植）。
- [ ] 词表分歧待裁：上游 ZH-W1/W2 禁用 `复测` `基线` `判据` `量具` `裁决` `契约`，
  blas 两个 skill 合计命中 406 处。现 `MIGRATED` 只含 `repo-task-case-gen`，对我们只计数
  不拦截；上游把 blas 纳入迁移名单时会一次性变硬拦截。Mr.0 已裁「只挪机制，词表自定」。
- [ ] **SA-05 交叉引用检查有盲区**（2026-09-23 Codex 内存探针证实）：跨 skill 的普通
  Markdown 链接不报错，含 `..` 的路径不做规范化，链接目标不在标题表就直接跳过。
  当前文档里「SA-05 放行」不能当作链接正确的证据。修的话要动镜像面的
  `plugin/.claude/hooks/doc_style_lint.py`，属上游件，应提 PR 而不是本仓改。
- [ ] `jargon_scan.py` 跑出的自造词候选逐个定去留：`自带件`(139/0)、`跑测`(184/1)、
  `量具`(58/0)、`剖面`(55/0)、`规模档`(45/0)、`复测轮`(41/0)、`隔离复验`(32/0) 等。

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
