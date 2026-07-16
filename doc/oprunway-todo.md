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

### 🟠 精度用例按 opbase §1 生成 + 阈值走 ascendoptest + 精度门前置 fail-fast + 性能同输入（**已落地；真机验收阻塞 op-build、有 follow-up** · 2026-07-15）
> **权威源**：`cann/opbase` `docs/zh/ops_precision_standard/experimental_standard.md`（pin `f69d4e4e3f2626ddd37855a8d05063a1764ac4c9`，gitcode 公开读）。用户 2026-07-15 定：
> - **§1 用例生成规则 → 采纳为权威**（§0：仅浮点计算类算子；整型/搬运类另定，遇到先停确认）。
> - **§2 误差指标/通过标准 → 不采纳**；阈值继续走 **ascendoptest**（= 现有 `precision_policy` 快照，**零改动**）。§0 也印证 IsClose/Equal(bool)/Sign(符号)=逐位精确、不适用 §2 混合容差。
> - **数量以用户为准**：`case_target` 默认 50、运行时问用户（**覆盖 §1.1「不设固定下限」——用户明示以其为准**）。
> - **性能与精度同一套输入**（用户明示）：不再单独造大 shape 性能用例；**性能在全部相同输入上判**（§1 覆盖里维度可到 2²⁰、总元素 2³¹，大 shape 本在集里）。
> - **精度门前置 + fail-fast**：跑完整套 → 任一精度挂 → `FAILED_PRECISION` + 跳过性能 + 提前结束（fail-fast 粒度=**跑完再判**、非首个短路）。
- [ ] **① 数量可配**：spec 加 `precision.case_target`（默认 50）；`acc-spec` agent `AskUserQuestion` 问用户写入；`gen_cases`/门读同一字段。
- [ ] **② gen_cases 重写按 §1**：轴 = `dtype(可跑集) × 数据格式 × 维度 × attr` 正交覆盖——维度 1~8、每维取 **2ᵏ / 2ᵏ−1**、总元素 ≤ 2³¹；值域 **50% 均匀[-5,5] + 50% 正态(μ∈[-5,5],σ∈[0.1,2])**；attr 走等价类/布尔 T,F/枚举全值。§1.4 特殊场景（空 Tensor、标量[1]、边界全1维/某维最大、INF/-INF/NAN 遍历）**优先纳入、不与常规正交**，余量用正交组合确定性填到 `case_target`（SEED 稳定序）。每条 torch golden。**边界值域正好落实 Q9 剩余 NaN/±0/Inf 边界（codex #6）**。defer 的 dtype（runner 跑不了）隔 blocked-pending、不计必过集。
- [ ] **③ 单集单跑双出**：`run_workflow` Task2 一套用例在 NPU 跑一次 → 每条出精度(vs torch golden, ascendoptest 阈值)+性能(kernel-only us)。精度门前置 fail-fast：任一挂 → `FAILED_PRECISION`+跳过 Task3+非零退出+`acceptance.json` 记「精度未全过、性能未跑」。全过 → Task3 性能对比在**全部相同输入**上做。
- [ ] **④ 门加两道**：精度全过门（任一 case≠pass → FAILED）+ 精度数量门（precision case 数 ≥ `case_target`，否则 BLOCKED——补「跑子集报全」的**数量维**）。
- 落地方式（拟）：ultracode fan-out + codex 门 + **a3 真 torch 全量测**（边界语义最吃 torch 确定性、真机不可省）。**待用户点头开工。**

**✅ 已落地（2026-07-15）**：①②③④ 全实现 + Layer A（gen_cases §1）/B（validator na·nan/perf_compare trivial-met/run_workflow fail-fast/门 na·trivial 豁免+防伪造）/C（bf16 runner+repo_adapter）/D（acc-spec case_target）。**a3 真 torch mock e2e 全绿 + fail-fast 验 + bf16 生成验 + 274 单测全绿**。设计+验证详见 `doc/oprunway-cases50-design.md`。
- [x] **真机 blocker 已解（2026-07-16）**：根因非环境坏、是 **run_on_npu.sh 每次 fresh 都重建 op**（对 isclose 的 experimental/op 名路径不适用 + `rm -rf $OPP` 毁 opp）→ 修「用户态 opp 已建则复用、只建 runner_exe」；另修 isclose runner 第二道解析处 dtype 关卡漏补 bf16。真机彻底解封（完整 3-dtype 50 用例 Task2 全 pass、三门 PASSED）。
- [x] **✅ 真机 opp provenance 绑定已落地（2026-07-16 续，bf16 已转 tested）**：`run_on_npu.sh` 重写 provenance 机制——OPHASH 绑**真实 op 源** `$OPS/$OPRUNWAY_OP_SRC`（必填、相对仓、安全路径校验）；opp 落独立 stamp `.oprunway_opp_provenance`（`op_src|ophash|soc|vendor|build`）；顶层门：缺 opp→建、stamp 全字段符→复用、不符/缺失→**fail-closed 拒复用**（exit 4）除非 `OPRUNWAY_OPP_REBUILD=1` 授权从源重建；源不存在→exit 3；build 失败/无 .run→exit 5。`repo_adapter._ne_cfg` 加 `op_src`(必填+安全校验)/`opp_rebuild` 透传。**查修一个致命 bug**：脚本漏 `OP_SRC="$OPRUNWAY_OP_SRC"` 短名桥接→`$OP_SRC` 恒空→绑整仓 hash 且没走 `--experimental`（异源）；补一行后真机坐实。**a3 CANN 9.0.1 容器 provenance-clean 从 `experimental/math/is_close`(A2/A3 正源) 重建**：stamp ophash 与真源逐字节 sha256 一致、Task2 pass(27 用例含 9 bf16、0 fail)、三门 PASSED、fail-closed 三情形(exit3/4/复用不重建)实测过 → **isclose spec bf16 转 tested**。487 单测全绿。（int32 仍 Track C；msprof 跳 trivial 见下条。）
- [x] **GPU 标杆 trivial 豁免**（fork finding #4/reviewer #2/codex #4，**已做**）：`gpu_baseline.parse_gpu_baseline` 改为只要求覆盖**非 trivial**（numel≥4096）性能 case、trivial 宽容忽略（不当 extra）；GPU 标杆逐 trivial case 给数不现实的问题消除。
- [ ] **follow-up · 真机 msprof 跳 trivial**（真机跑观察）：`run_on_npu.sh` / `perfcases_list.txt` 现对**全部** perf 用例逐个 msprof（custom+TBE 各一），含 trivial 退化 case；50 用例真机跑 ~15-20min。perf_compare 已 trivial-met 免测，故 msprof 也应只测非 trivial 大 shape（perfcases_list 排除 trivial）→ 大幅提速。非阻塞。
- **codex 源码门（一轮）修的 4 项**：广播 numel 蒙混 trivial（`_case_numel`/gate/gpu 改按全输入 broadcast 输出算）· inf/nan 补「性能」维（v2 非空皆带性能）· `perf_min_numel` 覆盖删→固定 4096（防 compare↔gate 阈值不一致+类型崩）· 「真空」严格判定（拒 `shape:[false]/[0.0]` 伪造，validator+gate_task1+gate_task2 三处共用、Task2 独立复核）。
- [ ] **follow-up · equal_nan 有效性**（deviation #4）：§1 不产 nanpair、`_assert_equal_nan_effective` 不再触发；equal_nan T/F 结构覆盖 + NaN §1.4 覆盖但未**交集**证明（aligned-NaN 翻转）。minor。

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
