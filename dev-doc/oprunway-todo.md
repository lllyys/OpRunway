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

## P0 · harness-gen 独立生成 skill（本线 @ feature/harness-gen，方案 v4.1 已定稿）

方案 `dev-doc/harness-gen-plan.md`，多 agent 编排 `dev-doc/harness-gen-orchestration.md`；
硬约束：case-gen/accept 零改动，porcelain 全程为空。

- [x] Step 0 · sasum 手写 overlay spike：2026-09-10 三轮真机 A1–A5 走通（r1 不通过/
  r2 证据不足/r3 演练通过），产物 ABI 冻结实证；报告 reports/harness-spike-20260909/。
- [ ] Step 2 吸收 spike 发现：IR schema 加 golden 累加精度策略字段；32ULP 钳位 ×
  用例阶梯的耦合成为 FACTS 显式输入（frame float golden n≥2^24 停摆，证据在报告）。
- [ ] preflight 遗留（Codex F5/F6/F8）：CSV 期望 SHA 入检查、manifest 三态恢复分支、
  wrapper 收敛单调用点消 A2′ calls_per_case 歧义。
- [ ] 测试债（V4 记账）：T3 全分支投影期望表未随迁，现仅 sasum 7 列被独立判据钉死、
  cherk 只断列数；允许表收第二条目前须补回（package-abi.md 加行规则已含此门槛）。
- [ ] frame float golden 大 n 缺陷是否回报上游 ops-blas：候选 issue/PR，须用户明示。
- [x] Step 1 · 2026-09-10 落地：骨架+manifest（六项）、装载器三道门（三码停机模型）、
  contract.py 迁入、29 测全绿；Codex 判修后可提交且 F1–F8 已关（thread `01a08a2e`）。
- [ ] Step 1 遗留（Codex R1）：ast.parse/literal_eval 无输入大小上限；任务包边界扩到
  不可信来源时再加文件与结构预算。
- [x] Step 2 · IR schema v1 冻结（2026-09-10）：schema+compile_contract v2+ir_validator+
  签名表+模板契约+正负实例，50 测绿，Codex 四轮判可冻结，/goal 授权。遗留随 Step 4：
  sgemm 端到端 fixture、R-01 大 k 精度、R-02 padding、R-04 OP_C。
- [x] Step 3 · sasum IR→C++ 纵切（2026-09-10）：renderer/installer/harness H1-H5，逐字节
  复现 rev1、跨机确定性、真机 A3 精度 77/77，rev1 已验基准；installer 经 Codex 四轮加固
  （M-01–M-08+符号链接目标全关，三破坏性风险消除），79 测绿。
- [ ] Step 3 在案遗留（威胁模型外，不阻塞）：L-01 安装进程锁、L-02 两端树哈希清单归档、
  L-03 注释宽度按 East Asian Width、真实 IO 故障下 rmtree/rename 深层原子性。
- [x] Step 4/5（2026-09-10 起，2026-09-14 A′ 重写）：方案乙 L3 通用后端；Codex 二审打回后
  按 A/B 审议采纳 A′（设备权威+哨兵降级搬运），三审再修 int ABI 域门/fail-closed/文档；
  sger 零改动接入成立；本地桩编译（frame ABI 桩，-Wall -Wextra）sger/sgemm 零错零警；
  100 测绿。Codex 五轮复核收敛,终审判「离线可证部分代码关闭条件已达到」(2026-09-14)。
- [ ] Step 4/5 真机 A1–A5 运行（待 GPU 基线 + 设备窗口，Mr.0 裁定 2026-09-15 停在离线强状态）：
  离线已到顶——Codex 判离线可证部分闭合；**真 frame ABI 离线编译 sger/sgemm 双 RC=0**（A3
  容器 oprunway_prov、CANN 9.0.1、g++ 11.4，对真 test/frame+utils+include+CANN 头）。实测更正
  前记：libops_blas.so 在 A3(9.0.1) 可重建、盘有余量——原「asc-devkit≥9.1/盘满」框定被直接观察
  取代。真机 A1–A5 **运行**未做,两道真实闸:①共享机设备租约(/work/run/oprunway-device-leases,
  勿扰他人);②sger GPU/cuBLAS 基线——无基线则 accuracy NO_REF→证据不足(同 sasum spike r2)。
  待基线就绪 + 设备窗口再跑;链接/GTest 发现/null 返回码/数值等编译证不了的项随此腿闭合。
- [ ] 移植完成后删前身 worktree oprunway-blas-harness-gen（未提交工作，删前逐项核对）。

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
