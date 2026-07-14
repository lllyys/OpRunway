# OpRunway 施工 TODO（离「通用算子验收工具」还差的）

> **现状（2026-07-10 更新）**：Wave 1–3 已全部经 **GitHub PR #3 合入 main**（merge commit `055a85d` 已进入 main 历史；当前 GitHub + GitCode 双镜像 main = `6390f74`，包含该 merge）。
> 编排升级 / 精度双标准 / 性能小 shape + GPU consumer / dtype 扩面 / catlass adapter / P2 原子化分发 **均已落地**，`acc-common` 由 **368 个 unittest 用例**覆盖（含判定链、三级门、适配器与脚本、对抗负例）。
> **但仍不是「能对任意算子一键验收」的成品**——剩余的洞见下。
> ⚠ **别误读**：剩余项里**确有代码型后续**（ATK 双标杆 fallback、Track C 的 int/bf16 runner + `neg_runner`、runner 自检 sidecar 硬门、其余 11 仓 adapter）；只是它们**各自卡在标准未定 / 真机未开 / 目标任务未明**，动手前得先有依据，不能凭空写。**能立刻把「验收结论」往前推的那部分，确实只剩人门裁决与外部资源**（见文末）。
> 〔~~Equal~~ 那条 **2026-07-09 作废**：任务书↔PR 配错、Equal 社区任务实为「未验收空任务」，见硬约束 #1。真机有效裁决仅 **IsClose / Sign**〕

## 🔒 已用教训钉住的硬约束（别再违反，先写这，因为最值钱）

1. **验收前先验证「任务书 ↔ PR」对应关系本身**（2026-07-09 血的教训，最上游）：确认「这个 PR 确实是这份任务书的交付 PR」、且该任务**确有已验收交付**，才能开跑。配错对应、或对应的其实是「任务书要了但未落地/未验收」的空任务 → 下游一切裁决（哪怕 root-cause 解耦做得再干净）**全部作废**。查证靠 **issue 追踪号 + 改动落点目录**，别靠算子名字面匹配。
   - **动因案例（Equal，结论已翻案两次到底）**：Equal 真机 fail 曾被归因 op-bug→harness→op「真阳性」refine 三遍，却**始终没质疑最上游**——「#2890 到底是不是这个任务的 PR」。**2026-07-09 正式确认**：① **#2890 系误配**（非本社区 Equal 任务的交付 PR）；② **Equal 社区任务至今未验收通过、无已验收对应 PR**。故先前「Equal A3 未达标·真阳性」**整体作废**。原缺陷报告 `doc/equal-a3-defect-report.md` **已删除**；上报**取消**（无缺陷可报）。
2. **FAIL 必先解耦 root-cause 再归因**：用「被测物自己的 build + 声明支持的 dtype + 手算 golden」独立复现，确认是「被测物 vs 我的 harness」，才能下结论。⚠ **排序**：本条是**确认对应关系为真之后**才谈的归因——对应本身错了（见 #1），解耦再干净也无意义。
3. **平台 / spec / 构建路径从任务书推，不猜**：平台/dtype/阈值一律从**正确的**任务书推——前提是先确认哪份任务书才对应被测 PR（见 #1）。
4. **合入状态用 gitcode 查证，别沿用假设**：「7月前=已合入」是设定、不是事实；`api.gitcode.com/api/v5/.../commits?path=` 一查即知。
5. **判定权威只在 spec**（对抗式代码门坐实的结构性教训）：凡决定「怎么判」的东西一律从 `spec` 派生；`caseset` / `evidence` 的自声明字段只作**待核对断言**，不得用来决定判据——否则 caseset+evidence 联手放宽阈值即可绕过验收。

---

## 🗂 当前 TODO（2026-07-14 整理）

> **新最高律令（用户明示）**：**精度标准与 golden 只能来自任务书指定的测试方法**，不是自撰 numpy（除非任务书要求）；不在支持范围 → **fail-closed 抛用户**、不静默降级。（「绝不信 PR」在精度维的延伸。）

### ✅ 本会话已做（都未 commit）
- [x] **V1 dtype 来源红线**：acc-spec 三入口改「dtype 全集 = 任务书 > 原 TBE 信息库 > 问用户」，PR op_def 仅对照。核验 SOUND。
- [x] **Q1 样例隔离**：真样例迁 `samples/`、零真值模板、三入口禁读 `.spec.json`、测试重定、archive_ops 内联、守门测试。4 路核验 SOUND。
- [x] **compile**：4 页 canon（其中 1 页因 Q1 已变 stale，见下）。

### 🔴 P0 · 收尾（把已做的落袋）
- [ ] **codex 门**：V1/Q1/compile 改动 commit 前统一过一轮（代码 `cc-suite:audit-fix` / 散文 `codex exec` gpt-5.6-sol low）。
- [ ] **commit + 入库**：走 PR 进 main；**PR #6（marketplace+Q2/Q3）待用户 merge**，合后同步 GitCode 镜像。
- [ ] **bureau 刷新**：capture golden 律令；Q1 修复 →刷 stale 页 `spec-examples-pollute-acc-spec-derivation`(verified) + `_verify.json` 指纹（capture→compile→review，不手改）。

### 🟢 Q9 golden（**已建 + a3 真 torch 验证 14 测全绿 · 2026-07-14**）
> 决策终稿：golden = CPU 标杆、**固定用 torch(CPU) 单后端**（确定性，**不回退 numpy**——torch/numpy 边界不一致如 `sign(NaN)` 会产非确定 golden）；torch 缺失 → fail-closed 报错要求安装。精度验证在装了 torch 的机器上（NPU 机）。
- [x] **golden torch-required**：`gen_cases` `_require_torch()` + 四 golden_fn 恒 torch；`golden_source`/`oracle_source` 恒 torch_ref。
- [x] **select_standard 白名单 fail-closed**（=Q7 落点1）：未知 oracle raise、堵 class C 静默降级。
- [x] **oracle_source 止血**：删写死 `cpu_ref`、据 `golden_source` 据实映射（严格首 token 前缀）、缺失 fail-closed。
- [x] **catlass spec 补 standard** + codex 9 维门一轮（#1/#2/#4/#5 修）+ a3 真 torch 全量绿。
- [x] **门校 oracle_source 一致性**（防伪造）已建：`evidence.oracle_source` ∈ 六枚举 且 == 映射(caseset `golden_source`)，fixture 已补字段、a3 真 torch 绿。
- [ ] **剩余 · 深覆盖**（codex #6）：torch golden 的 NaN/±0/Inf 边界向量测试（现测无边界随机输入 torch==numpy + 负容差 fail-closed）。
- 附：本轮顺带修传输 GNU-tar 可移植性 bug（`_deploy.tgz` 写打包目录外），server 上 transport 才通。

### 🟢 Q7 dtype 覆盖门（**已建 + a3 真 torch 验 · 2026-07-14**）
- [x] spec `dtype_required`(任务书全集) / `dtype_tested` 字段 + 门校 dtype 覆盖（`required ⊄ 真实用例 dtype` 且无 `dtype_deferred` → BLOCKED）。**用真实 cases 判、不信自报**（防跑子集报全）。
- [x] IsClose 权威全集回填 {fp32,fp16,bf16,int32}+deferred gap；Sign/Equal/Neg=`needs_user`（全集待信息库/用户，门不硬 BLOCK）。
- [x] 白名单 catlass spec 补 standard（Q9 已做）；抗坏输入不崩、删 required 仍对账（codex 修）。
- [ ] **剩余**：run_workflow 级「Q7/Q9 失败→BLOCKED」端到端断言（codex #3）；legacy 无 dtype_required 的宽容是 migration tradeoff（可加 schema version 收紧）。

### 🔵 P2 · 扩展 / 接通
- [ ] **(a) TBE 信息库接通**（dtype 独立源）：每份任务书自带路径 `.../tbe/config/ascend910b`；读法随运行环境探测、**不写死 ssh**。
- [ ] int32 扩展（Track C，锁已解）。

### ⏸ 外部阻塞（等资源，非我们能推）
- [ ] 真实 GPU 基线数据（Task3 真对比）｜其余 11 仓 adapter｜catlass 真机验收（需 950 + generated_harness）。

> **golden resume 要点（别丢）**：AscendOpTest 自己没 golden 源、只有 `expect_func`/`golden_path` 槽位（开发者填）→ 真问题是 numpy 忠不忠实任务书语义 + 有无交叉核/诚实记录。四算子 reference 已核（`repos/cann-ops-competitions/.../docs/202604/*_task_doc.md`）：IsClose/Equal=语义改造→CPU 逻辑（np.isclose/np.equal 忠实）；Sign=纯重写（np.sign 忠实）；Neg=uint8 点名 torch.neg 回绕（待核）。连带：现 IsClose/Sign「PASSED」精度维需重核 golden 忠实性才算合规。

## ✅ 已落地（Wave 1–3，均在 main）

### 体系结构轴（落地设计 P0–P3）
- **P0 机器可校验门** ✅：`validate_acceptance_state.py` 三级完整性门 + `run_workflow` 硬 blocker + `AGENTS.md` 跨CLI单一源 + `check_manifest_sync.py`。
- **P1 编排升级** ✅：`op-acceptance` 改薄为 `mode:primary` 编排器（只调度 + CP-A..E 状态机 + 工件门 + 任务书↔PR 对应校验前置）+ 3 个单轮 subagent（`acc-spec-extractor` / `acc-runner-dev` / `acc-verify-rootcause`）+ `acceptance-workflow` skill + `check_agent_frontmatter.py` meta-lint。
- **P2 原子化 + 分发** ✅：原子 skill（`acc-casegen` / `acc-precision` / `acc-perf` / `acc-rootcause`）+ `workflows/` 材料仓（`development-guide.md` / `task-prompts.md`）+ `init.sh` 跨 CLI 扇出（19 测）。
- **P3 catlass adapter** ✅（代码侧）：`catlass_adapter.py` + 真机脚本加固（堵 17 条对抗门：绕 opt-in / `rm -rf` 用户目录 / 假 BIN 报 DONE 等）。⚠ 见下「人门」。

### 验收口径轴
1. ✅ **任务书 → spec 自动化**：`acc-spec` skill（抽取规则基于 **23 份真实任务书语料**归纳；语料原文**未随仓入库**，provenance 见 acc-spec skill 文档与改动简表）。
2. ✅ **per-op runner 锚定 + root-cause 入 harness**：`acc-runner` skill + 编排纪律。
3. ✅ **AscendOpTest 默认阈值 + MERE·MARE 双标准**：`precision_policy.py` 作三标准 SSOT（AscendOpTest 逐 dtype `{tolerance,error_rate}` 完整 15 dtype 快照 + `compare.py` 掩码语义复刻 + 内容 hash；生态 MERE·MARE 打 `NOT_SETTLED`；`exact`）。三层 pass 同出 `catlass_compare_pass` / `standard_profile_pass` / `acceptance_precision_pass`，**放行只看 acceptance**；任务书宽于平台底线 → `PASSED_WITH_RISK` + `requires_human_cp` + 退出码 2。
   - ⏳ **诚实边界**：生态 MERE·MARE 端到端 **out-of-scope**（无真 SPMV/Trsm 任务书 → 无真 spec，仅单测覆盖判定逻辑；单标杆不过记 `needs_review` 不自动 fail）；**ATK 双标杆 fallback 未实现**；接真 `compare.py` bool 做 cross-check 仍是**桩位**（`ascendoptest_bool=None`），「我方复算 == 工具 bool」待真机对拍。
4. ✅ **性能小 shape 例外**：`perf_sim_plot.py`（<10us 且差 ≤3us → 出仿真图 + 走 `PASSED_WITH_RISK` 人核）；门内据 `simulation` 确定性重算 SVG 并比对字节（防「有图强制」空心）。`timing_scope` 必填、双边同口径否则 `BLOCKED_INCOMPARABLE_TIMING_SCOPE`。
5. **dtype / attr 覆盖扩面**：
   - ✅ **Track A（本地能力）**：`gen_cases`/`repo_adapter`/`validator`/`precision_policy` 扩 int16/int32/**bfloat16**（位级双表示 uint16，零依赖）+ **attr_matrix** + `storage_dtype`/per-case compare/`case_origin`/`rule_ref` 契约 + 语义化稳定 `case_id`。fixture mock 端到端 + 机器门全绿（`test_gen_cases_dtype_attr.py` 50 测）。
   - ⏳ **Track B（挂任务书原文 + 用户批）**：把新增 dtype 提进**权威** spec 的 `params[].dtype`——触碰硬约束「dtype 从任务书推不猜」，须 gate。**权威 4 spec 未动。**
   - ⏳ **Track C（挂真机 NPU + pr_facts）**：`runner.cpp` 的 int/bf16 分支 + `neg_runner`（当前 new_example 仅 sign/equal/isclose）。`repo_adapter.run_new_example` 遇 int/bf16 现 **fail-fast 标 Track C**（不静默跑）。**Neg 的 int-min 取负 / uint8 回绕 / int64 溢出语义 = out-of-scope**。
6. ✅ **GPU 标杆接入（Task 3）—— consumer 侧**：`gpu_baseline.py` + 最小字段契约（`gpu_baseline_contract.json`）+ 按 `case_id` 对齐 + NPU↔GPU 报告；缺标杆走 `BLOCKED_WAIT_GPU_BENCHMARK`（正规挂起，**非 fail**）。⏳ **真 GPU 基线数据待外部方提供**。
7. **泛化到 catlass + 其余仓**：✅ catlass adapter 代码落地；⏳ 真机待 950；**其余 11 仓未做**。
8. ✅ **发布形态定稿**：用户逐条拍板——插件继续作为主仓 `plugin/` 子目录（不拆独立 repo）、插件名维持 `oprunway`、skills 向 `awesome-ascend-skills` 的 external-sync **「很久以后」**、`init.sh` 跨 CLI 扇出保留。⏳ 待 `bureau:review` promote 成 `canonical`（当前 `proposed`）。

### 裁决可信性（对抗式代码门的产物）
- ✅ **假通过路径逐条堵死并钉负例**：validator 以 **spec 为权威**复算 canonical policy 做三处一致（`caseset`+`evidence` 同步放宽会被揪出）；judge 校验 metric（非负整数 / `numel>0` / 有限）；`gate_task3` 与 `caseset`/`evidence` 按 case 对齐（防「跑性能子集 + 伪造 summary」）；`repo_adapter` ssh/scp 注入防护；catlass 脚本 17 条对抗门。
- ✅ **evidence↔产物 provenance 绑定（方案 A）**：产物落盘 + evidence 记 `sha256` + 门内用 `precision_policy.compute_metrics` **重算比对**；numpy 缺失或产物缺失一律 `FAILED`，mock 不放宽。
  - ⚠ **诚实边界（须写进 canon 与 docstring，别假装已防）**：方案 A 只证「metrics 由这两个文件算出」，**不证「文件来自真 NPU 跑测」**——后者要绑 `OPRUNWAY_DONE` 哨兵 / raw log hash / msprof 输出。

---

## 🚧 当前真正的阻塞（挡住「验收结论往前推」的那些）

> 下面 A/B 两类是**当下无法靠写代码绕过**的。至于代码型后续（ATK fallback / Track C runner / sidecar 硬门 / 其余 11 仓 adapter），见上文各项的 ⏳ 标注——它们等的是**标准、真机或目标任务**，不是等人写。

### A. 人门裁决（agent 不可越门）
1. **`bureau:review` promote**（BUREAU.md：只有人能升 `canonical`，agent 不得手编 cabinet prose、不得手升）：
   - Equal 翻案 4 页：`verify-spec-pr-correspondence` / `root-cause-decoupling` / `task-spec-authoritative` 可按 checklist 直接 promote；**`perf-baseline-by-reference-source` 须先裁 `perf_baseline_source` 张力**（GPU 对比层是否为可选/非必需）再升，否则 hold。
   - T9 发布形态决定（当前 `proposed`）、门职责扩展（「门内重算比对」属证据可信、非重判 verdict）。
   - **2 条 lint survivor**：ADR0002 `msTuner`→`msprof op`；5 页 `1.2×`→`target_ratio`。⚠ 护栏：ADR0006/0008 未同步 rename 前**不宜单独 promote**（否则固化 drift）。survivors **单靠 `bureau:note` 不进 review 视图**，需 `bureau:lint --apply`（改 canonical status、消耗性）或 `bureau:compile` 才可见——用户已选「先不跑、留 review 一次处置」。
2. **T4 catlass 偏离 canonical 需人裁**：未走 canon `catlass-to-aclnn-bridge`【canonical】的路线 A/B，自选「注入其自带 example 树的 repo-native harness」第三路径。要么人门追认（改 canon），要么改回 A/B。
3. **「完工」标准未定**（够 demo / 够内部用 / 够对外发布）——定了才能倒推「到可用 v2 还差哪几步」。

### B. 外部资源阻塞
4. **真机验证**：待 `ascend-a5`（真 950 / arch 3510）+ VPN。catlass 真机 build/run、Track C 的 int/bf16 runner、AscendOpTest bool cross-check 全挂在这。
5. **真 GPU 基线数据**：consumer 侧与最小字段契约已就绪，缺数据即走 `BLOCKED_WAIT_GPU_BENCHMARK`。

### C. 已收口（不再是待办）
- ✅ 公开台账 push：双镜像同步至同一 OID。
- ✅ PR#2 body Equal 作废更正：2026-07-10 在线复核确认 body **早已含更正**（裁决表 Equal 行 = 「无结论·结论作废」），denylist 词仅出现在作废叙述内，评论/review 无旧结论 → **无需编辑**。

## 备注

- 详细设计/契约见 `doc/oprunway-design.md`；各 todo 的实施 plan（均经 codex 审）见 `doc/oprunway-todo-plans.md`；改动流水见 `doc/oprunway-changes-brief.md`。
