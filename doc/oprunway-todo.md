# OpRunway 施工 TODO（离「通用算子验收工具」还差的）

> **现状（2026-07-22 更新）**：Wave 1–3 经 **PR #3**、**PR #6**（V1/Q1 + Q9 + Q7 + cases50 + 真机 opp provenance 绑源 + IsClose bf16 转 tested）、**PR #7**（**runner 去引擎化**：runner 移出引擎作输出、`find_runner` fallback 退役 fail-closed）先后合入 main（PR #7 合入时 main = `b727d6f`，GitHub + GitCode 双镜像同 OID；**PR #8 合入后当前 main = `1d2bb3a`**，GitCode 镜像见下）。
> **PR #8 已合入 main**（2026-07-22，merge commit `1d2bb3a`；分支 `feat/golden-out-of-engine`，8 commit / 25 文件）：**golden 去引擎化**——`gen_cases` 的 `GOLDEN` 硬表改 `load_golden(op)` 按算子加载器 + golden 来源契约扩六枚举 + ADR 0011（proposed）+ **来源契约批 1**（`0192e49`，见下「🔴 下一刀」）。⏳ **GitCode 镜像尚未同步**。
> 编排升级 / 精度双标准 / 性能小 shape + GPU consumer / dtype 扩面 / catlass adapter / P2 原子化分发 **均已落地**，`acc-common` 的 unittest 覆盖：**合入前 main 486 个** · **现 main 523 个**（486 → 490 是 +4 golden 加载器的 fail-closed 真测、490 → 516 是 +26 批 1 的派生表穷举与授权核验测、516 → 523 是 +7 目录段软链洞的回归；三者均在 a3 容器全绿。含判定链、三级门、适配器与脚本、provenance 回归、对抗负例）。
> **引擎去具体算子化两刀均已入 main**（runner = PR #7、golden = PR #8）——
> **但成立的只是「elementwise 那条通路的 golden 值已去引擎化」**。
> ⚠ **别说成「引擎零内置算子」**（2026-07-22 更正，此前这么写是错的）——去引擎化**只覆盖 elementwise 通路**，仓里有**两处已知例外、如实记账**：
> ① `plugin/acc-common/catlass_adapter.py:152` 的 `golden_catlass_matmul` + `:162` 的模块级 `GOLDEN_SOURCE`——catlass matmul 的 golden **仍内置在引擎里**，且 `:186-190` 的注释明写「**有意**不进 `gen_cases` 的 golden 加载器路径」（matmul 的 (m,n,k) plan + A/B 专属输入构造与 elementwise 引擎结构不兼容）；**catlass 通路本轮 out-of-scope**；
> ② `plugin/acc-common/gen_cases.py:34` 的 `_BF16_EXACT_OPS = frozenset({"Sign", "Neg"})`——一张**按算子名硬编码**的表（决定 bf16 走 exact_equal 还是须 lossy 阈值），仍是引擎里的算子知识。
> 产出侧另有洞，见下「🔴 下一刀」。
> **但仍不是「能对任意算子一键验收」的成品**——剩余的洞见下。
> ⚠ **别误读**（2026-07-22 更正）：**「剩下的主要靠人门裁决与外部资源」这个旧判断已不成立**——`agent 产出侧`（下一刀）
> 是**依据充分、当下就能写**的代码工作：引擎接口（`find_runner`/`load_golden`）已在代码里定死，来源政策有 **两档链**
> 可依（ADR 0011 决策 3，已按用户 2026-07-22 裁定重写；⚠ **该 ADR 仍是 `proposed`、未经 `bureau:review` promote**，
> 且本轮重写尚未进 canon，载重前按 BUREAU trust tier 复核），且档位判定的机器实现（批 1）已落地，不等真机、
> 不等外部数据。其余代码型后续（sign/equal/neg 的 bf16 真机验收、Track C 的 int32 runner +
> `neg_runner`、ATK 双标杆 fallback、其余 11 仓 adapter）才是**各自卡在真机未开 / 标准未定 / 目标任务未明**、
> 动手前得先有依据的那批。人门裁决与外部资源见文末 A/B。
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

> **最高律令（用户 2026-07-22 重述；⚠ 更正此前写窄了的版本）**：golden 的来源是**两档链**，按序——
> **① 任务书指定的测试方法**（须有任务书原文授权，引文可核：全文快照入库、按 `task_doc.snapshot.md:<行区间>` 逐字对照）；
> **② 第①档没有时，退 CPU 上的 torch / numpy 现成 API**。细则：
> - **R2 · PR 里的参考实现一律禁止作 golden 源**——落地方式**不是写条禁令、而是可产值域里没有那个格子**（`PRODUCIBLE_ORACLE_SOURCES` 排除含「仓/PR 的 CPU 参考」语义的 `cpu_ref` 与 `catlass_existing_ref`）；禁令会被绕过，缺值只能 fail-closed。（「绝不信 PR」在精度维的延伸：PR 是被测物，拿被测物的参考实现当「正确答案」等于自证。）
> - **R4 · 任务书指定了、但本环境跑不起来 → fail-closed 问用户，不自动回落第②档。**
> - **R5 · 人核门按「怎么算出来的」判**：第②档**现成 API 单调**（一个 API 直出）→ 不必人核；**按公式自拼多步** → 必须人核。
> - **R6 · torch 优先 numpy 兜底是生成期选型、写死进 `golden.py`**，运行时不按「谁装了」偷换（承 ADR 0011 决策 4）。
>
> ⚠ **旧记「golden 只能来自任务书指定的测试方法、否则 fail-closed」漏了第二档、是错的**（2026-07-22 更正），一律以本段为准。
> 详见 `doc/oprunway-golden-decoupling-adr.md` 决策 3（⚠ 该 doc 是**设计稿**；canon 侧 **ADR 0011 仍 `proposed`**、未经 `bureau:review` promote）。

### ✅ 已合入 main（PR #6 · 2026-07-13~20）
- [x] **V1 dtype 来源红线**：acc-spec 三入口改「dtype 全集 = 任务书 > 原 TBE 信息库 > 问用户」，PR op_def 仅对照。核验 SOUND。
- [x] **Q1 样例隔离**：真样例迁 `samples/`、零真值模板、三入口禁读 `.spec.json`、测试重定、archive_ops 内联、守门测试。4 路核验 SOUND。**stale 页 `spec-examples-pollute` 已刷「已修复」（`proposed` 待 review）。**
- [x] **compile ×2**：首轮 4 页（V1/Q1/Q9/Q7）+ 次轮 5 页（cases50/provenance：`opp-provenance-bound`·`opbase§1 生成`·`精度门 fail-fast`·`perf trivial-met`·`runner-dtype` 更新）。gazette health 全 0。
- [x] **provenance 批 4-finding 收口**：`_deploy.tgz` token+清理 · na out.bin 解耦 · 裸 open→with · 去 `OPRUNWAY_OP` 死字段。独立审一轮、a3 容器 487 测全绿。

### ✅ P0 · 收尾（已完成 2026-07-20）
- [x] **codex 门**：各批 commit 前均过一轮（代码走独立 Claude 新眼审=cc-suite 委托结构 / 散文 codex 或独立审；**codex 无人值守空转已坐实、退回独立 Claude**）。
- [x] **commit + 入库**：**PR #6 已 push + merge 进 main**（`f91ccda`）、GitCode 镜像已同步（双镜像同 OID）。
- [x] **bureau 刷新**：golden 律令已入 canon（`golden-source-from-taskdoc-method` + `golden-fixed-to-torch-cpu`）；Q1 stale 页 `spec-examples-pollute` 已刷「已修复」+ `_verify.json` 指纹更新；cases50/provenance 决策已 compile。

### 🟢 Q9 golden（**已建 + a3 真 torch 验证 14 测全绿 · 2026-07-14**）
> ⚠ **本段留的是 2026-07-14 的 canon 口径，已不再描述 main 上的实现**（别把「规范状态」和「代码状态」混着读）。
> ADR 0011 提出下述调整，其**代码已随 PR #8 合入 main**（`1d2bb3a`），但该 ADR 本身
> **`status: proposed`、未经 `bureau:review` 人门 promote** → **代码现状与 canon 口径暂时并存**；
> canon 侧一律以本段原文为准，直到人门裁决：
> ① 拟把「固定 torch 单后端、绝不回退 numpy」放宽为「**按算子 torch > numpy 定档并记录**」——确定性红线仍在（按算子**定档**、非运行时「谁装了用谁」的兜底）；
> ② 拟把 golden 值移出引擎，改由 `<ops_root>/<op>/golden.py` 按算子加载；
> ③ 拟把 `oracle_source` 的**推导**从「只认 torch/numpy 两前缀」放宽为「六枚举可直接声明」（六枚举本身 main 上已有，放宽的是推导侧）。
>
> 原文（2026-07-14 决策终稿，**现行**）：golden = CPU 标杆、**固定用 torch(CPU) 单后端**（确定性，**不回退 numpy**——torch/numpy 边界不一致如 `sign(NaN)` 会产非确定 golden）；torch 缺失 → fail-closed 报错要求安装。精度验证在装了 torch 的机器上（NPU 机）。
- [x] **golden torch-required**：`gen_cases` `_require_torch()` + 四 golden_fn 恒 torch；`golden_source`/`oracle_source` 恒 torch_ref。（⚠ **本条已被 PR #8 覆盖、现为历史记录**：`1d2bb3a` 起 main 上 `_require_torch` 与四个内置 golden_fn 已移除、改按算子 `golden.py` 定档，`_require_torch` 迁进各份 `samples/golden/<op>/golden.py`。）
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
- [x] **① 数量可配**：spec 加 `precision.case_target`（默认 50）；`acc-spec` agent `AskUserQuestion` 问用户写入；`gen_cases`/门读同一字段。
- [x] **② gen_cases 重写按 §1**：轴 = `dtype(可跑集) × 数据格式 × 维度 × attr` 正交覆盖——维度 1~8、每维取 **2ᵏ / 2ᵏ−1**、总元素 ≤ 2³¹；值域 **50% 均匀[-5,5] + 50% 正态(μ∈[-5,5],σ∈[0.1,2])**；attr 走等价类/布尔 T,F/枚举全值。§1.4 特殊场景（空 Tensor、标量[1]、边界全1维/某维最大、INF/-INF/NAN 遍历）**优先纳入、不与常规正交**，余量用正交组合确定性填到 `case_target`（SEED 稳定序）。每条 torch golden。**边界值域正好落实 Q9 剩余 NaN/±0/Inf 边界（codex #6）**。defer 的 dtype（runner 跑不了）隔 blocked-pending、不计必过集。
- [x] **③ 单集单跑双出**：`run_workflow` Task2 一套用例在 NPU 跑一次 → 每条出精度(vs torch golden, ascendoptest 阈值)+性能(kernel-only us)。精度门前置 fail-fast：任一挂 → `FAILED_PRECISION`+跳过 Task3+非零退出+`acceptance.json` 记「精度未全过、性能未跑」。全过 → Task3 性能对比在**全部相同输入**上做。
- [x] **④ 门加两道**：精度全过门（任一 case≠pass → FAILED）+ 精度数量门（precision case 数 ≥ `case_target`，否则 BLOCKED——补「跑子集报全」的**数量维**）。
- 落地方式：ultracode fan-out + codex 门 + a3 真 torch 全量测。**①②③④ 已全部落地（2026-07-15），见下条 ✅。**

**✅ 已落地（2026-07-15）**：①②③④ 全实现 + Layer A（gen_cases §1）/B（validator na·nan/perf_compare trivial-met/run_workflow fail-fast/门 na·trivial 豁免+防伪造）/C（bf16 runner+repo_adapter）/D（acc-spec case_target）。**a3 真 torch mock e2e 全绿 + fail-fast 验 + bf16 生成验 + 274 单测全绿**。设计+验证详见 `doc/oprunway-cases50-design.md`。
- [x] **真机 blocker 已解（2026-07-16）**：根因非环境坏、是 **run_on_npu.sh 每次 fresh 都重建 op**（对 isclose 的 experimental/op 名路径不适用 + `rm -rf $OPP` 毁 opp）→ 修「用户态 opp 已建则复用、只建 runner_exe」；另修 isclose runner 第二道解析处 dtype 关卡漏补 bf16。真机彻底解封（完整 3-dtype 50 用例 Task2 全 pass、三门 PASSED）。
- [x] **✅ 真机 opp provenance 绑定已落地（2026-07-16 续，bf16 已转 tested）**：`run_on_npu.sh` 重写 provenance 机制——OPHASH 绑**真实 op 源** `$OPS/$OPRUNWAY_OP_SRC`（必填、相对仓、安全路径校验）；opp 落独立 stamp `.oprunway_opp_provenance`（`op_src|ophash|soc|vendor|build`）；顶层门：缺 opp→建、stamp 全字段符→复用、不符/缺失→**fail-closed 拒复用**（exit 4）除非 `OPRUNWAY_OPP_REBUILD=1` 授权从源重建；源不存在→exit 3；build 失败/无 .run→exit 5。`repo_adapter._ne_cfg` 加 `op_src`(必填+安全校验)/`opp_rebuild` 透传。**查修一个致命 bug**：脚本漏 `OP_SRC="$OPRUNWAY_OP_SRC"` 短名桥接→`$OP_SRC` 恒空→绑整仓 hash 且没走 `--experimental`（异源）；补一行后真机坐实。**a3 CANN 9.0.1 容器 provenance-clean 从 `experimental/math/is_close`(A2/A3 正源) 重建**：stamp ophash 与真源逐字节 sha256 一致、Task2 pass(27 用例含 9 bf16、0 fail)、三门 PASSED、fail-closed 三情形(exit3/4/复用不重建)实测过 → **isclose spec bf16 转 tested**。487 单测全绿。（int32 仍 Track C；msprof 跳 trivial 见下条。）
- [x] **GPU 标杆 trivial 豁免**（fork finding #4/reviewer #2/codex #4，**已做**）：`gpu_baseline.parse_gpu_baseline` 改为只要求覆盖**非 trivial**（numel≥4096）性能 case、trivial 宽容忽略（不当 extra）；GPU 标杆逐 trivial case 给数不现实的问题消除。
- [ ] **follow-up · 真机 msprof 跳 trivial**（真机跑观察）：`run_on_npu.sh` / `perfcases_list.txt` 现对**全部** perf 用例逐个 msprof（custom+TBE 各一），含 trivial 退化 case；50 用例真机跑 ~15-20min。perf_compare 已 trivial-met 免测，故 msprof 也应只测非 trivial 大 shape（perfcases_list 排除 trivial）→ 大幅提速。非阻塞。
- **codex 源码门（一轮）修的 4 项**：广播 numel 蒙混 trivial（`_case_numel`/gate/gpu 改按全输入 broadcast 输出算）· inf/nan 补「性能」维（v2 非空皆带性能）· `perf_min_numel` 覆盖删→固定 4096（防 compare↔gate 阈值不一致+类型崩）· 「真空」严格判定（拒 `shape:[false]/[0.0]` 伪造，validator+gate_task1+gate_task2 三处共用、Task2 独立复核）。
- [ ] **follow-up · equal_nan 有效性**（deviation #4）：§1 不产 nanpair、`_assert_equal_nan_effective` 不再触发；equal_nan T/F 结构覆盖 + NaN §1.4 覆盖但未**交集**证明（aligned-NaN 翻转）。minor。

### 🔴🔴 首跑实测暴露的编排层洞（Pdist 用户测试 · 2026-07-22）

> **provenance**：在 `OpRunway-usertest/work` 起干净 session 真跑 Pdist（任务书 `Pdist_task_doc.md` + `cann/ops-math` MR 2663，已合入），
> 插件快照 `b2a1b6f`，耗时 13 分钟（16:04:23→16:17:27）。durable 产物 `work/reports/pdist/{correspondence,scope_conclusion}.json`。
> transcript：`~/.claude/projects/-…-OpRunway-usertest-work/5ed5cb8d-….jsonl`（主）+ `subagents/agent-adad37aacf7324284.jsonl`（验收 agent，130 条）。
> ⚠ **结论本身是合格的**——判 `OUT_OF_SCOPE_P3`、明标 `verdict_type: NOT_pass_fail`、拒绝在无证据基础上判 pass/fail、探针落 scratchpad 跑完清理、**插件仓零改动**。
> **洞全在编排层**，且 U1 是主脊。

- [x] **U1 · 已修（2026-07-22，用户拍板取最小授权）**：`op-acceptance.md:5` 现为 `tools: Bash, Read, Write, Edit, Skill, AskUserQuestion, Agent(acc-spec-extractor), Agent(acc-runner-dev), Agent(acc-verify-rootcause)`——**显式补两个能力、不删 `tools:` 继承全部**（用户选的口径）。⚠ **仍欠真 session 复验**：改的是 frontmatter，「它真的会派 subagent 了」只有再跑一次干净 session 才算数，本地无从自证。原始诊断——实测该 agent 130 条消息里工具只有 `Bash`×30 + `Write`×1 —— 设计里的三个 subagent（`acc-spec-extractor` / `acc-runner-dev` / `acc-verify-rootcause`）**一个没派**，`AskUserQuestion` 是**主循环替它问的**。根因在 `plugin/agents/op-acceptance.md:5` 的 `tools: Bash, Read, Write, Edit, Skill` —— **既无派 subagent 的工具、也无 `AskUserQuestion`**；frontmatter 的 `agents:` 声明**不授予工具**。→ CP-B/C/D 的分工、subagent 单轮约束、循环控制权**全是空的**，它只能自己读源码手搓。改法：`tools` 补齐，或直接删掉 `tools:` 让它继承全部。（对照：`skills:` 那条**有效**——`acceptance-workflow` 两次都自动注入了。）
- [x] **U2 · 已修（2026-07-22）**：`_PR_RE` 收成锚定正则（`^https?://gitcode\.com/<owner>/<repo>/(merge_requests|pulls?)/<num>` + 右界 `(?=[/?#]|$)`，`/pull/N` 形态已认），`_parse_pr_url` 在**任何网络调用与 makedirs 之前**调用，形态不认识 → **抛 ValueError fail-loud**、不落空壳 `pr_facts.json`。原始诊断——用户给 `/pull/2663`（GitHub 风格），脚本只认 `pulls|merge_requests`，**不报错**、产出空 `pr_facts` + 一条 note 就继续往下走。agent 是 `grep` 脚本源码才诊断出来、自己把 URL 规范化成 `/merge_requests/2663` 重跑的。→ 换个不肯读源码的 agent，对应校验就会带着空 `target_dir` 糊过去。改法：接受 `/pull/N` 形态；解析失败 **fail-loud**，不产空壳往下传。
- [x] **U3 · 已修（2026-07-22）**：`samples/` 已由仓根迁入 `plugin/samples/`（仓根 `samples/` 已不存在），marketplace `source: "./plugin"` 下随插件分发。原始诊断——实测 agent 在 `plugin/` 下 `find samples/golden` 得 `No such file or directory`——`samples/` 在**仓根**，而 marketplace `source: "./plugin"`。→ `acc-runner/SKILL.md` 让 agent「照抄 `samples/runners/*.cpp`」指向的是不存在的文件，runner 生成会静默退化成凭空写（正撞 Equal 血教训）。改法：搬进 `plugin/`，或把骨架内联进 skill 参考文档。
- [ ] **U4 · 「干净 session」隔离实为无效**：skill 注入时报的 base directory 是**活仓** `/…/OpRunway/plugin/skills/acceptance-workflow`（**不是** `~/.claude/plugins/cache/`）——marketplace 是 `directory` 源、`installLocation` 直指仓根。于是 agent 那 30 条 Bash 几乎全在读活仓源码。**连带**（⚠ 下句是**按本次实测推的**、未穷举验证）：`/plugin install` 的快照看来只决定**组件注册**（有哪些 agent/skill、frontmatter 长什么样）；至少对「按 base directory 解析的文件」和「agent 直接读的源码」这两类，读的是**活仓工作树** → 仓一旦切分支，这部分行为就可能与安装时的快照对不上。要真隔离得换非 `directory` 源。
- [x] **U5 · 已修（2026-07-22），且 canon 那个「尚未实测」的开放问题有答案了**：
  原状是 `pr_facts.json` 得 `"base": "master", "head": "master"`、**全程无 sha**，兜底 `head→base→master→main`。
  **真打 gitcode API 实测（cann/ops-math）**：
  - **MR 3400（open）**：`head.repo` 是**贡献者 fork**、`head.ref` **字面就叫 `"master"`** →
    按分支名去 base 仓取会拿到 base 的 master（**实测 sha `e16a230c` ≠ head `9b494b2d`**）——
    **静默取到完全不相干的代码，却仍记成「取自 PR head」**。这不是理论风险，是活的。
  - **MR 2663（merged，正是 Pdist 首跑那个）**：head 同样在 fork（`xiaoy2459/ops-math`）上。
    **实测的这 2 个 PR head 都在 fork 上。**⚠ n=2，只能说「fork 情形真实存在且不罕见」，
    **不足以断言「是常态」**——要下这个结论得抽样统计（未做）。
  - **canon 页问的「open+fork 的 `contents?ref=<sha>` 可解析性」→ 在实测的这 2 个 PR 上可以**：
    `ref=<head_sha>` 对 **base 仓** HTTP 200。⚠ **这不是平台保证**——两次 200 不能证明
    「所有仓、所有 fork commit 都可达」。故实现仍留了一层退路：base 仓拿不到时，
    **用同一个 sha** 退到 `head_repo`（不引入分支名风险）。
  **改法**：`pr_facts` 记 `head_sha`/`head_repo`/`is_fork`；关键文件**只按 head_sha 取、退役分支名兜底**；
  拿不到 sha → **一个文件都不取**并说清为什么（宁可没有，不要来源不明的）。
  实测复跑：MR 3400 的 7 份、MR 2663 的 6 份关键文件**全部取自各自 head commit**。
  ⚠ 连带：canon `pr-head-commit-is-the-tested-object`（`proposed`）的前置疑问已被实测解答，
  **具备 promote 材料**——但**须走 bureau**，不可手改。

#### U6 · 默认走 mock 是错的；mock 本身就不该存在（用户 2026-07-22 定）

> **用户原话**：「为什么默认走 mock？默认应该走 NPU，且不应该有 mock 的存在。」

**实况（已核）**：mock **确实是全局默认**——`run_workflow.py:230` `ap.add_argument("--mode", default="mock", …)`，
连函数签名 `run_workflow.py:55` `def run(spec_path, mode="mock", …)` 也默认它。编排层更把它定成**强制一步**：
`acceptance-workflow/SKILL.md:84` 要求 CP-B 必跑 `--mode mock`，`:86` 还写「mock 裁决异常先修 spec，别上真机」；
`plugin/agents/op-acceptance.md:63`（注意仓里还有个同名的 `plugin/commands/op-acceptance.md`）规定缺 NPU/VPN 就「到 CP-B（mock）为止」。

⚠ **比「默认值选错」更严重的是它产什么**：`repo_adapter.py:182` 的 mock「NPU 输出」literally 是
`out = golden.copy()  # mock：完美 NPU = golden` —— **精度维按构造必过**（除非人为 `--defect` 注入坏点）；
性能是 `_mock_us(numel)`（`:159`，按元素数算的假数）+ `perf_compare.mock_baseline`（`:73`，假基线）。
而它**产出的是与真验收同名同形的 `acceptance.json` 裁决**。这与本项目「不基于跑没跑崩式判定」的立场直接冲突。
（canon `catlass-synthetic-demo-cannot-forge-pass`【`proposed`】只证了 **catlass 通路**的 mock 造不出 PASS，
**没覆盖 elementwise 通路**——那条路上 mock 是实实在在出裁决的。）

**mock 现在混了两个职责，拆开看才好办**：
- (a) **契约自检**（spec / gen_cases / validator 链是否自洽、id 一一对应、防跑子集）—— 这个有价值。
  **但 `gen_cases --dry-run` 已经能做**（plan-only、不算 golden、不 import torch），Pdist 首跑里验收 agent 取证用的正是它。
- (b) **伪造 `acceptance.json` 裁决**（拿 golden 冒充 NPU 输出）—— 这个是有害的，就是要删的那部分。

- [ ] **U6a · 默认值翻过来**：`--mode` 默认改 `new_example`（真机）；没 NPU **fail-closed 说清楚跑不了**，不再静默退到 mock。
- [ ] **U6b · CP-B 的自检改用 `--dry-run`**，不再产 mock 裁决；`acceptance-workflow` / `op-acceptance` 两处散文同步改写。
- [ ] **U6c · 删 mock 通路**。⚠ **连带面已估（别低估）**：`repo_adapter.MODES`（`:646`）· `run_mock`/`_inject_defect`/`_mock_us`（`:146-198`）·
  `perf_compare.mock_baseline`（`:73`、`:398` 有调用点）· `catlass_adapter.run_catlass_mock`（`:452`）+ `CATLASS_MODES` ·
  **8 个测试文件共 89 处 mock 引用**（`test_runner_lookup` 25 · `test_gen_cases_dtype_attr` 16 · `test_ne_transport` 15 ·
  `test_catlass_adapter` 13 · `test_perf_compare` 10 · 余 3 个合计 10）。**删之前先想清楚这些测试改测什么**——
  它们现在靠 mock 当「可确定性复现的假 NPU」，删干净会连带失去一批不依赖真机的回归能力。
- [ ] **U6d · `--defect` 注入机制何去何从**：现在它靠 mock 造坏点来证明 validator 真会 fail（防「门是假的」）。
  删 mock 后这条自证路径没了，需要替代（候选：留一条**明确非验收、不产 `acceptance.json`** 的测试专用夹具，
  与验收通路彻底隔离）。**这条须用户拍板**，别默默删掉。

#### 🔑 用户 2026-07-22 拍板的四条（本轮据此施工，**别再重开讨论**）

拿分类学数据（44 个算子行、elementwise 仅 34%）给用户看后定的：

1. **下一刀 = shape_transform 三个**（`UpsampleNearestExact1d&2d` · `UpsampleNearest3d` · `im2col`）。
   理由：卡点只有两个（输出 shape 由属性公式推、attr 得能是 `list[int]`），改完顺带解锁 reduction 的一部分；
   且它们是「给已有成熟算子加 dtype」这类最典型的社区任务。**不是**先啃数量最多的 Foreach 族（8 个）——
   那是对 case 结构的根本改造（一个 case 现在 = 一组同形单张量，表达不了列表），spec/runner/validator 三处都得动。
2. **`out_shape` 交给 per-op `golden.py`**，**不搞 spec 表达式语言**。
   签名 `out_shape(in_shapes, attrs) -> tuple`，**可选**导出；不导出 = 输出同输入形状（elementwise，现有 4 份样例零变更）。
   ⚠ **代价用户明确接受**：这是**代码不是数据**，门没法「不执行就校验」它。
   否掉表达式语言的理由：`im2col` 的实际公式带 floor / 连乘 / 多维归约，小表达式语言表达不下。
3. **`--defect` 改成测试专用夹具**：保住「证明 validator 真会 fail、门不是假门」的回归能力，
   但**移出 CLI 入口**，不给任何人拿它冒充验收的机会。
4. **dtype 冲突以任务书为准**：任务书 dtype 全集当需求，`op_def` 支持不了的差额入 `task_pr_gaps`、
   裁决落 `PASSED_WITH_GAPS`。「没实现」是**发现**不是借口。承 canon `task-spec-authoritative-over-pr`。
   ⚠ 但**不能变成「宣称有 gap 就免检」的后门**——gap 须有据可查（指向任务书原文 + op_def 出处），
   且不得覆盖「算子实现了但跑挂了」那种真失败。

连带定的第 5 条（C5）：**mock 通路本体保留**（测试与本地演示要用），但**物理上不再产 `acceptance.json`/`verdict.json`**，
只产标明 non-acceptance 的产物 —— 消除「伪造裁决」这个真正的危害，而不必动那 89 处合理的测试引用。

#### U8 · `_build_inputs` 在 arity≥3 时静默丢输入（2026-07-22 施工中发现，**已修**）

`gen_cases._build_inputs` 的常规 `varied` / `pair*` 路径末尾写死 `return [x0, x1]`（二元构造），
而 `empty` 与特殊值路径却按 `arity` 产满 —— **arity≥3 时多出来的输入被无声丢掉，两边行为还不一致**。
当前 4 个内置算子都是 1/2 元所以踩不到，但 `bincount(self, weights)`、SPMV 这类一来就中招：
**会悄悄少造一个输入且不报错**，直接违反本仓「fail-closed 优于静默降级」。

- [x] **已修**：`arity > 2` 直接 `raise ValueError`（报清楚是几元、指向 U7b），**不假装支持**。
  加 `test_arity_ge3_rejected_not_silently_truncated` 钉住（并对照验二元仍正常产 2 个，证不是把整条路堵死）。
  ⚠ 这只是**止血**——真要支持多输入算子得一般化 pair 构造，属 U7b。

#### U7 · 用例生成只支持 elementwise，得覆盖任务书里出现的全部算子类型（用户 2026-07-22 定）

> **用户原话**：「要能支持所有在任务书里出现过的算子类型，不要只支持 elementwise 类型。」
> Pdist 的 G1–G4（见下节）是这条的**一个实例**，不是全部。

**实底（`doc/oprunway-task-pr-map.md` 41 份任务书清点，算子形态按名称+仓内 README 抽查）**：
**elementwise 是少数派**。至少还有这些结构上过不去的类，每类都不是「加个 `golden.py` 」能解决的：

| 类 | 例 | 卡在哪（结构性，非参数问题） |
|---|---|---|
| **张量列表（Foreach 族，8 个）** | `ForeachAddListV2` / `AddScalarV2` / `MulList` / `SubListV2` / `RoundOffNumberV2` / `Exp` / `Expm1` / `Neg` | 输入输出都是 **Tensor List**、不是单张量（**已核**：`ForeachAddListV2` 设计文档「对两个张量列表（Tensor List）执行逐元素…」）。`gen_cases` 的 case 结构是「一组同形单张量」，表达不了列表 |
| **归约类** | Pdist · `median` · `MinDim&MaxDim` | 输出 shape ≠ 输入 shape；`MinDim&MaxDim` 还是**双输出**（values + indices） |
| **输出 shape 依赖输入内容** | `bincount` | **已核**（README）：「out 大小为 (self 的最大值+1) 与 minlength 中的最大值」→ golden 与 runner 的输出 buffer 都得**运行期定**。当前最狠的一类 |
| **无输入张量的生成类** | `Arange` · `logspace` | **已核**（README）：`Arange` 从标量 start/end/step 算出 1 维张量 → **根本没有输入张量可造** |
| **形状由属性推导** | `im2col` · `MaxUnpool2d/3d` · `UpsampleNearestExact1d&2d` / `Nearest3d` | **已核**（`im2col` README）：输出 shape 由 kernel_size/padding/dilation/stride 公式推导 |
| **复数 dtype** | `Polar`（实→复）· `AngleV2`（复→实） | 当前 dtype 集只有 {fp32, fp16, bf16, int16, int32}。⚠ `im2col` README 更列了 COMPLEX32/64 · DOUBLE · BOOL · INT64/UINT64 等一大票 |
| **稀疏 / 线代** | `SPMV`（ops-sparse）· `aclblasTrsmBatched`（ops-blas）· `aclsolverCheevj`（ops-solver） | 输入不是稠密张量；且这些仓的 adapter 都还没有 |
| **跨卡 / 融合 / 注意力** | `SyncBatchNormGatherStats` · `SlidingTileAttention` · `dynamicMap` | 多设备或复合语义 |

- [ ] **U7a · 先做形态分类学**：把 41 份任务书逐份归类到上表（或修订上表），产出一份**机读**的算子形态清单
  （每份任务书 → I/O 形态 / dtype 集 / 属性轴 / 输出 shape 来源）。**没有这个清单，后面的扩展就是拍脑袋。**
  ⚠ 上表的分类**部分是按算子名推的**，只有标「已核」的四个（`ForeachAddListV2` / `bincount` / `Arange` / `im2col`）
  真读过仓内 README 或任务书；其余须逐份核实后再当事实用。
- [ ] **U7b · spec schema 要能表达这些形态**：至少需要 I/O arity（含列表）、rank/shape 约束、输出 shape 来源
  （同形 / 公式推导 / 依赖输入内容）、属性作语义轴、多输出、复数与更宽 dtype 集。**这是 G1/G2/G4 的一般化。**
- [ ] **U7c · runner 骨架同步一般化**：现在是「固定四槽 + 输出 numel = 输入 numel」，装不下列表输入、多输出、动态输出 buffer。**这是 G3 的一般化。**
- [ ] **U7d · 分期**：全覆盖是大工程，须**排优先级**（建议按任务书份数排：Foreach 族 8 份是单类最多的，
  归约类次之）。**优先级须用户拍板**，别自作主张从最简单的开始。

#### 修复批次（**U1/U2/U3/U6a/U6b/U7a 已落地，2026-07-22**；分支 `fix/pdist-usertest-gaps`）

#### 🟢 shape_transform 用真算子跑通了（2026-07-23）——**三个全通，且经 a3 真 torch 复跑**

> **a3 `oprunway_prov`（真 torch 2.10.0+cpu）实跑**：Im2col 50 用例 `(2,2,2,2)→(2,8,9)` ·
> UpsampleNearestExact2d 18 · UpsampleNearest3d 20 `(2,3,2,4,4)→(2,3,4,6,8)`（**rank 5**）。
> 三者 `out_shape_source` 均为 `golden.out_shape` —— **声明值驱动整条链、且与真 torch 实际产出
> 的形状逐 case 对过账**（对不上引擎 fail-closed）。全量 686 测在 a3 亦全绿。
> ⚠ **仍未证**：golden 的**数值本身**只证了「真 torch 跑得出来、形状对得上」，**没跟另一个独立实现
> 逐位比对过数值**；且**真机 NPU 一次没跑**（`--mode new_example` 卡在 `verify_mode=exact⇒bool`
> 那条已记账的引擎缺口）。**精度/性能验收结论不得由这些推出。**

此前 C1–C5 的引擎侧全是用**假算子**单测的。这轮换真算子，结论是**有条件的通**：

- **✅ Im2col 通了**（ops-math `conversion/im2col`）：50 用例 · PASS · `contract_problems=0` ·
  `out_shape_source` 50/50 = `golden.out_shape`。关键实测 `(2,2,2,2) → (2,8,9)`：
  **4 维入 3 维出、输出 rank 随输入 rank 跳变**。
  ⭐ **这是 C1「out_shape 放 golden.py、不搞 spec 表达式语言」那个决定的正面证据**——
  这种形状小表达式语言表达不下，`out_shape()` 十来行普通 Python 就写完了。
- **✅ 两个 Upsample 也通了**：`UpsampleNearestExact2d` 18 用例 · `UpsampleNearest3d` 20 用例（**rank 5**）。
  它们原本卡在两个引擎缺口，本轮一并补掉：
  - [x] **`allow_empty_tensor`（spec 新开关，缺省 `true` = 现行为不变）**：opbase §1.4 把「空 Tensor」
    当普适特殊场景**无条件强塞**，但很多算子任务书白纸黑字写「不支持空Tensor」。强塞只有两个出口——
    golden **为非法输入编造输出**（= 替算子发明它不支持的语义），或整条链卡死。
    ⚠ 开关**只收真布尔**：写成 `"false"` / `0` 直接拒（真值性判断会把它们悄悄读成「允许」，
    本仓在批 1 的 `authorization_verified` 上栽过同款 fail-open）。
    ⚠ `doc/oprunway-op-shape-taxonomy.md` §3.5 早就点名要这个字段——**这次才补上**。
  - [x] **`_EXT_RANK_SHAPES` 补 5 维，且只在 rank 约束点名时并入**：`_MAX_RANK` 本是 8 而阶梯只到 4 维。
    ⚠ **第一版直接并进 `_REG_SHAPES`，当场误伤 elementwise**（`sign` 用例集多出两个 5 维 shape、
    4 个测试变红）——**改变既有算子的用例集 = 悄悄改变已验收过的东西**。改成按需并入，
    并加回归 `test_ext_rank_ladder_does_not_leak_into_unconstrained_ops` 钉住。
    ⚠ 只补到 5 维：**没有实际算子要求 6~8 维**，凭空铺满只让笛卡尔积与 golden 开销白涨。
  - [x] **已修（2026-07-23）** `_fit_rank` 造不出 `(0,C,H,W)`：新增 `spec.empty_axis` 声明空维在哪一轴。
    ⚠ **轴号定不了 rank**——im2col 的 rank 是 `[3,4]`，而合法空形态只有 4 维那个。
    所以按合法 rank 从小到大逐个试，**判据交给算子自己的 `out_shape()`**：
    「哪个 rank 的空形态合法」本就是算子知识，而 `out_shape` 正是它的所在地（C1 的前提），**引擎不猜**。
    全部候选都被拒 → fail-closed 并给两条出路（改轴 / 关掉 `allow_empty_tensor`），
    **绝不挑一个算子不认的形状硬塞**。
    **a3 真 torch 实测**：im2col 产 3 条空用例 `(0,1,1,1) → (0,4,4)`，空 Tensor 覆盖 **0 → 3 条**。
    ⚠ 本机替身跑不出来（朴素 unfold 不支持 N=0），这条**只有 a3 能证**。
- ⭐ **过程本身值得记：这个结论中途被推翻过一次。** 施工阶段报的是「Im2col 通了 50 用例 PASS」，
  而 codex 审出那次 PASS 有一部分建立在 golden **为非法空输入编造输出**上（见下条诚实性缺口）。
  **补完 0 维闸，Im2col 也 fail-closed 了** —— 反而暴露出更干净的事实：**三个算子撞的是同一堵墙**。
  ⭐ 另：三个施工 agent **都没有为了跑通而降 rank 或编假 golden**，当场停下并记 gap。这比「跑通了」更值钱。
- [x] **G4 归约类规模预算已落地**：从 `out_shape()` 推 cost（零新契约、4 份 elementwise 样例一字不动），
  超预算 → **显式降规模 + 三处留痕**，**不是静默跳过大 shape**（那会让覆盖悄悄缩水而报告显示「已覆盖」）。
  改前实测 Pdist 类算子直接 `MemoryError`（5.5e11 对 / 2.2 TB）。
  - [x] **连带闸已补**（codex 抓的静默错过路径）：被降过规模的 case **不得走 trivial-met**。
    trivial-met 的正当性是「这 case 本来就小、perf 没意义」；降规模 case 是「它本来很大、
    我们没按目标规模跑」——**没测却算过**。现改判 blocked 并带上原规模。
  - [x] **已补（2026-07-23）** `validator` 侧消费 `golden_cost`：降规模的 case 进裁决的
    `overall.scaled_cases` + `counts.scaled`。不带出来的话，caseset 账本里明明记着「这条的目标规模没跑」，
    裁决与报告却只字不提 → 下游据裁决写「已覆盖大 shape」就成了没根据的话。
    ⚠ 带出来 **≠ 判失败**：降规模是显式、有账的取舍，但**必须在结论里可见**，由人/门定它够不够。
    账本原样透传、不重算（它是 gen_cases 生成期写的事实）；账本形状不对 → 视为无降规模、**不报错**
    （它是记账不是判定依据，为一份坏账本把整个裁决判死是过度反应）。
- [x] **两处对称性收口**：`repo_adapter.main()` 复用 `catlass_adapter` **同一套**守卫
  （`assertIs` 钉住是同一对象、不是各抄一份）· `run_catlass_mock` 补自报 `defect_injected`。
- [x] **诚实性缺口已修 + 补上守门**：`Im2col/golden.py` 的 `GOLDEN_PROVENANCE` 声称「不为 numel=0 编造输出」，
  实测却返回 `(4,2)`——**声明写了、代码没做**，fail-closed 被委托给了 torch（换个替身结论就变，
  且 dry-run 不 import torch、走不到那层）。同批 `UpsampleNearestExact2d` 有这道闸：
  **三份 golden 两份防了一份没防**。根因是**三个新算子零测试覆盖**。
  已补 0 维闸 + 新建 `test_samples_golden_contract.py`（含「provenance 声称 ↔ 实际行为」对账）。

**这轮的引擎侧新账（下一刀的主线，3 个 agent 独立点名）**
- [x] **已修（2026-07-23）** `repo_adapter` 的 `verify_mode=exact ⇒ bool`：exact 只是**判据**（逐位比），
  跟输出是不是 bool 毫无关系。改成据 caseset 的 `compare_dtype`（**validator 会据 spec IO 矩阵独立派生
  并强制相等**，谎报过不了裁决层，故采集层用声明值是安全的）。空 Tensor（compare=na）无 compare_dtype →
  只校形状、不断言 dtype，且要求 compare 必须是 na（否则 fail-closed）。
  配两条回归：exact+浮点算子的 compare_dtype 必须是浮点；源码级钉住不得再从 verify_mode 推 bool。
- [x] **已修（2026-07-23）** `_BF16_EXACT_OPS` 那张写死的算子名白名单 →
  改由 spec 声明 `precision.bf16_bitexact`（旧表退役成**历史默认**，Sign/Neg 行为零变更）。
  ⚠ 声明的语义是「输出恒等于某个输入元素、不做算术」——**不是放松阈值的旋钮**：
  声明错了会让本该用 lossy 阈值的算子被按逐位相等判，直接产假 fail 或假 pass。报错文案给两条出路
  （真是搬运类就声明 / 真做算术就挂 deferred）。只收真布尔。
  **连带解锁**：三个 shape_transform 算子的 bf16 defer 全部解除（它们的 gap 早就写着
  「任务书与 op_def 都支持、纯粹被引擎白名单挡住、最近邻是纯 gather 够格进白名单」）——
  实测 Im2col 50 用例含 16 条 bf16、两个 Upsample 各 21 用例含 7 条 bf16。
- [ ] `gen_cases._NATIVE` 没有 `bool`，任务书要求的 BOOL 增量造不出用例。
- [x] **已修（2026-07-23）** `run_on_npu.sh` 的 vendor 后缀硬编码：改成按序解析——
  ① 显式 `OPRUNWAY_VENDOR_SUFFIX`；② 从 OPS 仓目录名推（`ops-math`→math、`ops-cv`→cv、`cann-ops-blas`→blas）；
  ③ **推不出来 fail-closed，不猜**。配 3 条回归（含「推不出必须是空串，脚本据此 fail-closed」+ 源码级钉住无 `_math`）。
- [~] **已记进规则、但 kind 本身待你裁**：`taskdoc-to-spec.md §1.2` 缺第三类 dtype gap ——
  「**`op_def` 声明了、但目标硬件那一支的 aclnn 实现没有**」。im2col 的 `bool` 就是这格：
  `im2col_def.cpp` 的 `VALUE_DATA_TYPE_LIST` 含 `DT_BOOL`，而 `aclnn_im2col.cpp:222-225` 的
  `IsRegBase` 分流下、非 regbase（A2/A3）那支的 `DTYPE_SUPPORT_LIST` 只有 {FLOAT, FLOAT16, BF16}。
  - 用不了 `dtype_unsupported_by_op_def`（它有「op_def 确实没声明」的自洽硬校，会当场判不符）
  - 只能退 `dtype_deferred`，可那个 kind 的语义是「**我们的**能力缺口」，
    而这明明是**被测物的**缺口 → **语义被迫说反了**
  已在规则文里写清这一情形 + 要求 reason 逐字写明真实成因，别让措辞盖掉验收发现。
  **要不要补第三类 kind（如 `dtype_unsupported_on_target_hw`）须你拍板。**
- [ ] **im2col 硬件双源冲突未裁**：任务书 `适配硬件` = A2/A3（→ a3），仓内 `im2col_def.cpp:41` 只
  `AddConfig("ascend950")`、kernel 只有 arch35（→ a5）。**开跑前必须先定目标机**。
- [ ] **Upsample 算子名二义（正踩 Equal 那个老坑）**：op_def 注册的是 `UpsampleNearest`
  （1d/2d/exact 共用一个 kernel、靠 attr 区分），而 spec/golden 用的是 aclnn 接口名 `UpsampleNearestExact2d`。
  **runner 文件名与 opp 部署按哪个名字定位，须在 acc-runner 环节实读确认。**


> 施工方式：7 路并行 agent（按**文件所有权互斥**切分）+ 逐项独立复核 + 集成对账。
> ⚠ **本机验证有天花板**：Mac 无 torch → 523 测里 59 条红全因此而起，golden 相关通路本机根本验不了。
> 本机只能证「与基线一致」，**真结论以 a3 容器（真 torch）为准**。

按「挡路程度 × 改动成本」排。**用户 2026-07-22 定：先记账、暂不动手。**

- [x] **0 · GitCode 镜像同步 PR #8** —— 2026-07-22 已推，双镜像同 OID `1d2bb3a`。
- [x] **1 · U1 已改（2026-07-22）**——`tools:` **显式补最小权限**，用户明确批准：
  ```
  tools: Bash, Read, Write, Edit, Skill, AskUserQuestion,
         Agent(acc-spec-extractor), Agent(acc-runner-dev), Agent(acc-verify-rootcause)
  ```
  `Agent(<type>)` 是 Claude Code 在 `tools:` 里声明「可派哪个 subagent」的写法（依据：anthropic-docs
  CHANGELOG v2.1.147 · 2026-05-21「plugin agents that declare multiple `Agent(...)` types in `tools:` frontmatter」）。
  比泛用的派活工具更窄——**只放行这三个 subagent**，不是任意派活。三个 subagent 的 `tools` 行未动。
  - ⚠ **更正一条我先前写错的记录**：此处原写「**倾向直接删掉 `tools:`**，理由是这仓在『声明式白名单被静默忽略』
    上栽过（`plugin.json` 的 `agents` 数组）」——**那条先例套不上**。`plugin.json` 的 `agents` 数组是**被忽略**，
    而 `tools:` 这次**恰恰生效了**（agent 就只有那 5 个工具，一个不多）。机制不同，不能拿来论证裸删。
    且原记录把「删掉 tools:」写成用户倾向，**用户当时并未表态**，是记录者自己的判断被误记成了用户决定。
  - ⚠ **仍待真验**：`Agent(...)` 这个写法本机找不到现成用例佐证（只有 CHANGELOG 一条文字依据）。
    **必须真起一次 session 跑验收，确认 primary 真能派 subagent、真能 `AskUserQuestion`**——
    「frontmatter 里写了」≠「工具真给到了」，这正是 U1 本身的教训。光看 `check_agent_frontmatter.py` 绿**不算数**。
- [x] **2 · U2 已改**：`fetch_source.py` 抽出纯函数 `_parse_pr_url()`，容错 `/pull/N`·`/pulls/N`·`/merge_requests/N`；**形态不认识在任何网络调用之前抛 `ValueError`**、绝不落空壳 `pr_facts.json`；网络失败仍记 notes 照常写（**区分「用户输错 URL」与「环境取不到」两类失败**）。新增 `test_fetch_source.py` 12 条测（含「网络之前就抛」的断言、不打真网络）。
  - 遗留：报错以裸 traceback 冒泡（语义对、UX 糙）；`www.` 前缀与大写 host 现被拒（旧行为是产空壳，更糟）。
- [x] **3 · U3 已改**：`git mv samples → plugin/samples`（13 文件 rename 保历史），随插件分发。连带改：`_golden_fixture.py` 的 `_SAMPLES_GOLDEN`、`archive_ops/` 两条软链（验证无断）、`acc-runner` 两份散文改用 `${OPRUNWAY_PLUGIN_ROOT}/samples/...`（工具中立变量，`init.sh:55` 既有约定、Claude 下等价 `${CLAUDE_PLUGIN_ROOT}`）、`gen_cases.py` 与 `repo_adapter.py` 两条**用户可见报错**里的样例路径。
  - ⚠ **施工 agent 漏报了 8 处测试引用**（`test_ne_transport.py:21`、`test_gen_cases_dtype_attr.py:316`、`test_validate_acceptance_state.py` ×5、`test_spec_isolation.py` 的 `_ROOT`），由主控补齐。**本机只暴露 2 条新红，另约 9 条被 torch 红掩盖**——这正是「本机对账不能当验收依据」的实例。
  - ⚠ 二阶影响：samples 进插件后 agent 物理上够得到真 spec 样例，**防污染只剩纪律、没有文件系统屏障**（canon 本就写明「隔离是纪律非沙箱」，但姿态实质变弱）。
- [x] **4 · U6a/U6b 已改**：
  - **U6a**：`--mode` 默认 `mock` → `new_example`（argparse + `run()` 签名两处），并在 `makedirs`/`json.load` **之前**加 `_ne_cfg()` fail-closed 预检——缺 `OPRUNWAY_*` 直接 `SystemExit`、**不落半个产物**。加 2 条测试钉死。全仓 5 个 `run_workflow.py` 子进程调用点逐行核过，均已显式带 `--mode mock`（测试用 mock 合理，**测试不是验收**）。
  - **U6b**：CP-B 自检从 `run_workflow --mode mock`（产伪造裁决）改成 `gen_cases.py --dry-run`（plan-only 契约自检）。改了 `acceptance-workflow/SKILL.md` 9 处 + `agents/op-acceptance.md` 5 处 + `commands/op-acceptance.md`（那句「默认 mock」在 U6a 落地后已成假话）。
  - ⚠ **散文里逐字写明了 dry-run 的能力边界**：它**不算 golden、不 import torch、不落 `.npy`** → 验不了 golden.py 在不在 / 来源契约 / `oracle_source` 映射 / validator 链 / 三级门。**CP-B 过了不代表用例链整体可用**，缺 golden 这类问题会漏到 CP-D 才炸，且 `refine_spec` 变不出 `golden.py`（真撞上要停下告知用户，别在 refine 循环里空转）。
  - **U6c/U6d 仍未做**（删 mock 通路）：连带 89 处测试引用 + `--defect`「证明门真会 fail」的自证路径要先定替代方案，**须用户拍板**。
- [x] **4.2 · C1–C5 已落地（2026-07-22）**：shape_transform 通路打通（`out_shape` 契约——**当时是 4 元组，2026-07-23 已扩为 5 字段具名元组** `Golden(fn, source, provenance, out_shape, contract)`，别读作现行契约 —— 加 attr `list[int]` +
  spec `rank` 约束）· dtype 挂账 `passed_with_gaps` 全链接线（validator → 门 → `run_workflow`，**exit 2 挂人工、绝不回 0**）·
  mock 物理上产不出 `acceptance.json`（改产标 NON-ACCEPTANCE 的 `dev_run_summary.json`）· `--defect` 出 CLI。
  验证：**a3 `oprunway_prov` 容器真 torch 2.10.0+cpu 跑 639 测全绿**（`OK (skipped=2)`，54.6s；
  传输逐文件 sha256 双侧一致 `6f0ac4a9…`）。本机：torch 替身 639 全绿 + 裸跑与基线零 diff + 两道门 PASS/SYNCED。
  ⚠ **本机「零新增红」不能当放行依据**——59 条恒红会掩盖结构性断裂（本轮就掩盖了 5 条：
  「文件不存在」「argparse 不认参数」这类，与数值无关）。**造 torch 替身重跑**才是本机唯一有效的自证手段。
- [ ] **4.3 · C1–C5 的遗留项**（复核报上来、本批没做完的）：
  - [ ] **`--mode catlass` 真机通路被顺手降成非验收**（`_acceptance_capable` 单元素白名单的副作用，**零测试覆盖**）。
    本项目 Task 2 明确以 catlass 打底，「真机 catlass 永远出不了验收裁决」应当是**显式决定**而非白名单副作用。**须用户拍板。**
  - [x] **`repo_adapter` 的静默 reshape 已收紧**（2026-07-22）：reshape 靶子从顺手用的 `golden.shape`
    改成 caseset **声明**的输出形状，并断言两者一致（`_readback_shape`）。挡的是「adapter 自己把靶子弄错」
    这类真实 bug（声明 `[N,1]` 却按 golden 的 `[N]` 收）。
  - [ ] **逐维验 NPU 实际输出形状：做不到，且有意不假装做**。`out.bin` 是**扁平 dump**、只带元素数不带形状 →
    「NPU 实际产出几维、每维多少」在采集层**根本观测不到**，能验的只有 numel。
    ⚠ 往 evidence 里塞一个「实际输出形状」字段等于**拿声明跟自己比 = 假验证，比不验更坏**，故本仓有意不做。
    真要逐维验，须**让 runner 把自己实际的输出形状一并写出来**（runner 契约变更）。
    在那之前，`validator._EV_SHAPE_KEYS` 恒不触发是**如实反映现状**，不是缺陷。**别把现在说成「已逐维验形」。**
  - [x] **两条 CLI 出口已对称（2026-07-23 复核确认已在位）**：`repo_adapter.main()` 落盘前过 `refuse_reserved_out(out_path)`（`repo_adapter.py:960`）+ `assert_non_acceptance(evidence, mode)`（`:965`），与 `catlass_adapter.main()` **共用同一份实现**而非各抄一份口径相近的；`run_catlass_mock` 自报 `envelope["defect_injected"]`（`catlass_adapter.py:566`）。
  - [x] **`dtypes: []` 产 0 条 case 已补 fail-closed**（2026-07-22）：连同「dtype 集重复 / 白名单」三道校验一起
    提进共享预检 `check_spec_capability`，**`gen_cases` 与 `_dry_run` 共用、且先于 `load_golden`**。
    ⚠ 原来 `_dry_run` **压根没这道闸** —— 而它现在正是 CP-B 的契约自检，空 dtype 集会安静地
    `emitted=0` 通过 CP-B、跑 0 条也显示「无失败」。这是活的「0 用例冒充验收」。
  - [x] **rank≥5 已通（2026-07-23）**：`_EXT_RANK_SHAPES = [(2,3,2,4,4), (1,2,3,3,3)]`，**仅当 spec 的 rank 约束点名了 `_REG_SHAPES` 覆盖不到的 rank 时才并入池**——不是无条件加进去，否则会漏进无 rank 约束的 elementwise 算子、静默改变它们的用例集（当场红过 4 个测试，有回归钉住）。实测：`upsample_nearest_3d.spec.json`（rank 5）`--dry-run` 出 21 条，用的正是 `1x2x3x3x3` / `2x3x2x4x4`。
  - [x] **`--perf-slow` 已下架**（2026-07-23）——与 `--defect` 同批理由：同类注入旋钮、只对非验收通路有意义；
    进程内 `run_workflow.run(..., perf_slow=[...])` 的回归能力**完整保留**，拿掉的只是 CLI 旋钮。
    ⚠ **这是施工 agent 自行拍的板，而本条原记为「未决」**——如实记账，**可推翻**：否决的话要连
    `PerfSlowFlagRetiredTest` 一起回退，且 `doc/oprunway-todo-plans.md` §922 的本地演示配方
    已因下架失效、需改写成进程内调用。
  - [ ] canon 页 `catlass-synthetic-demo-cannot-forge-pass`：① 「全文不含 acceptance.json 字样」**已失真**（字样在、语义相反——
    现在是「拒绝以裁决产物名落盘」的白名单，比原表述更强）；② 它自己点名要的**负向测试已补**（7 条），
    proposed→verified 的前置条件已满足、**请人门裁**；③ 记的报错原因已变成 ADR 0011 的「缺 golden.py」。
    ⚠ **走 bureau `capture → compile → review`，不可手改。**
  - [ ] 新增大写状态 `PASSED_WITH_GAPS` 扩了 canonical 状态词表 → `canon/architecture/task3-state-machine.md`
    **必须走 bureau，绝不手改**。
- [ ] **4.5 · `canon/_verify.json` 的 samples 路径已失效（须走 bureau，不许手改）**：`:106` / `:111` 两条 artifact 仍记
  `samples/runners/oprunway_isclose_runner.cpp` 与 `samples/specs/isclose.spec.json`——**仓根这两个路径 U3 之后已不存在**。
  这不是历史文字，是**机器可读的指纹条目**：canon 复核会把已迁移的工件判成缺失。
  ⚠ 按 BUREAU 写门，canon 页**不得手编**、`canonical` 不得手设 → 须走一次 `capture → compile → review`
  把路径重录为 `plugin/samples/...` **并重算 hash**；不许只改路径却留着未经确认的 verified 状态。
- [ ] **5 · U4 / U5 本批不动**：U4 要换 marketplace 源形态 → 影响分发方式，得先定发布形态（T9 `proposed`）；U5 属 canon `pr-head-commit-is-the-tested-object`（`proposed`）的落地，该页自带前置「未合并 PR 的 head 常在贡献者 fork，open+fork 的 API 可解析性**尚未实测**」。
- [~] **6 · U7a 形态分类学进行中**：产出 `doc/oprunway-op-shape-taxonomy.md`。首轮 ops-math 18 份完成，ops-nn 16 份 + 其余 6 仓 7 份**首轮 agent 交了桩**（已重跑补齐）。U7b/c/d 与 G1–G4 仍未开工，**优先级须用户拍板**。

#### Pdist 暴露的「非 elementwise 通路」空白（G1–G4；能力边界扩展，须单独立项）

> 均由验收 agent 实跑取证，逐条落在 `work/reports/pdist/scope_conclusion.json` 的 `hard_evidence`。
> Pdist = 成对归约：输入 `(N,M)` 二维点集 + float 属性 `p` → 输出 `(N*(N-1)/2,)` 一维。
> ⚠ **G1–G4 是 U7「归约类」那一格的实例，不是 U7 的全部**——按 U7a 的清单做一般化时，别只照 Pdist 修。

- [ ] **G1 · rank 约束的归约类用例生成**：spec schema **无任何** rank/ndim/shape 约束字段，`gen_cases` 只按内部 `_REG_SHAPES`/`_LARGE_SHAPES` 造 1~8 维（`(3,)`、`(2,3,4)`、`(2,2,2,2)` 等对 Pdist 全非法），且假设输出同形。需支持「输入固定 rank + 输出 shape 由输入结构派生」。
- [ ] **G2 · 度量属性 `p` 没进覆盖轴（这条最刺眼）**：任务书**核心要求就是修 `p=inf` 的精度问题**，而 `--dry-run` 实测 `id_kinds` 里的 `inf`/`ninf`/`nan` 全是**输入数据**的特殊值、**不是属性 `p` 的取值**；无 `attr_matrix` → `p` 恒等默认 2.0 → **核心验收场景 0 覆盖**。现有 attr 机制是照 IsClose 的 `rtol`/`atol`/`equal_nan` 设计的，需扩到「决定算子语义的度量参数」且能表达 `inf`。
- [ ] **G3 · reshape 输出的 runner 骨架**：`acc-runner-dev` 的固定四槽骨架假设输出 numel = 输入 numel；Pdist 需按行数 N 算出输出 buffer `N*(N-1)/2`，且 `GetWorkspaceSize` 里 `p` 是夹在 input 与 output **之间**的 float 标量参数（照 `test_aclnn_pdist.cpp` 锚定）。
- [ ] **G4 · 归约类的规模/复杂度感知预算**：实测 mock 探针 **2 分钟超时**（Exit 143）——大 shape 下 O(N²) 成对计算爆炸（N=65535 → ~2.1e9 对）。需给归约类设规模上限，或改向量化 golden。

> **顺带一条正面发现（对批 6 有用）**：`golden.py`「没人产」是**流程空缺、不是能力空缺**——本次 agent 为做探针，**自己手写了一份 Pdist 的 `golden.py` + `spec.json`** 丢进 scratchpad，`--dry-run` 跑通了。批 6 要做的是把这件事写进流程，而不是从零教它怎么写。

### 🔴 下一刀 · agent 产出侧（通往「兼容多仓的很多算子」的**真使能件**）

> 用户 2026-07-22 强调：目标是**兼容多仓的很多算子**，别再用「几个算子够用」的框法。
> 引擎侧去具体算子化两刀已写完（**PR #7 / PR #8 均已合入 main**，`1d2bb3a`）——**elementwise 通路**的引擎 fail-closed 要 `runner.cpp` + `golden.py`
> （两道现均已在 main 生效；catlass 通路与 `_BF16_EXACT_OPS` 是已知例外，见头部）。产出侧的实况是
> **一半有、一半没有**（别说成「完全没有」）：
> - **`runner.cpp` 有人产**：`acc-runner-dev` 的 `gen_runner` mode 据 spec + 算子自带 example 锚定生成，
>   但 **scope gate 限死** `experimental/math/<op>` + dtype ∈ {fp32, fp16}，其余一律 BLOCKED → **覆盖面才是洞**。
> - **`golden.py` 在正式流程里没有固定产出者**：现有 agent / skill 都没把它列为交付物（PR #8 后引擎也不再内置）→ **流程空缺**。
>   ⚠ 别写成「能力空缺」：Pdist 首跑里验收 agent 为做探针**临时手写并跑通了一份**（见上「🔴🔴 首跑实测」节），只是这条路没进可重复执行的流程。

#### ✅ 批 1 已落地（`0192e49`，2026-07-22）——「档位怎么算」的唯一实现

按用户 2026-07-22 的裁定，在 `plugin/acc-common/precision_policy.py` 新增三件（**纯新增、零行为变更、不接任何调用者**，接线在后续批次）：
- **受控词表**：`PRODUCIBLE_ORACLE_SOURCES` 四枚举——**`cpu_ref`（语义含「仓/PR 的 CPU 参考」）与 `catlass_existing_ref` 进禁产集**，即 **R2「禁 PR 作 golden 源」的落地方式是值域里没那个格子**（canonical 的六枚举定义**未动**，改它须走 bureau review）；另有 `GOLDEN_SOURCE_KIND` / `GOLDEN_METHOD_KIND` / `RUNNABLE_METHOD_KINDS` / `AUTHORIZATION_KIND` / `GOLDEN_BLOCKED_REASON`（未定哨兵沿用仓里已有的 `needs_user`）。
- **`derive_golden_tier(g, authorization_verified) -> (tier 1..4, requires_human_review, blocked_reason)`**：九条规则按序首命中即返 + 穷举兜底，无未定义态；**声称有任务书授权却核不实 → 直接 tier 4，不降级照跑**；方法族跑不起来 → blocked（R4）；无授权 + 现成 API 单调 → tier 2 免核、自拼多步 → blocked（R5）。
- **`verify_authorization(g, snapshot_path)`**：R12（任务书全文快照入库）的机器落点——校快照 sha256 → cite 严格匹配 `task_doc.snapshot.md:<行区间>`（**只认这一个文件名，PR/仓内文件连被引用资格都没有**）→ 行号不越界 → quote 是该区间逐字子串。
- **诚实边界**（已逐字写进 docstring）：只证「引文出自快照那几行」，**不证**「这句话该算 oracle_method 还是 impl_reference」——那一刀仍是 agent 自报，Sign 那类误判机器拦不住。
- 验证：a3 CANN 9.0.1 容器 **523 测全绿**（基线 490：批 1 +26、软链洞 +7）；审修门 audit→fix→verify 各一轮，修 1 高（`authorization_verified` 用真值性判断 → `"false"`/`1` 把未核授权抬进 tier 1 的 fail-open）+ 2 中（假测试、类型防御不对称）。

**两条本轮保守默认（⚠ 可推翻，先按此走）**：
1. **仓自带（非 PR）的参考实现 = `impl_reference`、不构成 golden 授权** → 走第②档、落 **tier 2/3**（它只说明「别人怎么实现」，不等于任务书授权了这个测试方法）。
2. **档位本轮 per-op**；同算子多 dtype 档位不一时**取最保守档**；per-dtype 扩展留待需要。

#### ⏳ 批 2–7（标题级；**全部未做**。⚠ 批次切分是按批 1 的接线顺序**推的**，编号/边界待主线确认）

- [x] **批 2 已落地（2026-07-23）· 声明式来源块 + tier 派生写进每条 case**
  - `golden.py` **可选**导出 `GOLDEN_CONTRACT`（`source` / `method_kind` / `method` /
    `authorization{kind,cite,quote}` / `taskdoc_snapshot{sha256}`）。**不导出 → `golden_tier` 为 None、
    行为与批 2 前完全一致**——不强制既有 golden 立刻改写。
  - `load_golden` 返回改**具名元组** `Golden(fn, source, provenance, out_shape, contract)`。
    ⚠ 刻意用具名元组而非再加位置项：字段增删时位置解包会**静默错位**，具名取则当场报错。
    实证——改完 6 处旧的 4 元解包**当场炸**（`too many values to unpack`），不是悄悄错位。
  - **`precision_policy.validate_golden_contract`**：只校**词表 + 结构**，不核授权真伪、不判档（三者分开，
    避免「自己核自己」）。⚠ 词表拼错必须早拦：`derive_golden_tier` 的兜底会把不认识的组合判成 tier 4，
    于是**一个本该 tier 2 的正当 golden 被判 blocked**，而报错是含糊的 `unverifiable_authorization`——查半天查不到是拼错了。
  - **记录不阻断**（批 2 的边界）：tier 4 也照常产用例，只把 `blocked_reason` 如实写进每条 case。
    阻断归批 5 的门。理由：档位是**结论的一部分**，得先可见可审；先阻断会让「快照还没入库」这种真问题
    以「算子跑不了」的面目出现，反而更难查。
  - ⭐ **IsClose 做成了完整自证的参考实现**：真任务书快照（`IsClose_task_doc.md` 逐字节）与 golden.py 同处，
    引文锚 `task_doc.snapshot.md:13` + 逐字 quote → **实测派生 tier 1**（authorization_verified=True、
    不需人核、未 blocked）。**改一个字（`cpu`→`CPU`）就掉回 tier 4** —— 引文锚真在起作用。
  - 新增 6 条测试（无契约保持旧行为 / impl_reference+single_api → tier 2 / 声称授权无快照 → tier 4 不降级 /
    真快照逐字引文 → tier 1 且改一字即掉档 / 词表拼错早拦 / 声称授权缺 cite 早拦并提示该用 impl_reference）。
  - ⚠ **未做的两件（原计划在批 2 里、实际拆出去）**：`GOLDEN_SOURCE` 收紧到可产四枚举 ·
    `catlass_adapter` 那处 `"numpy f32 …"`。**它们会改变现有 oracle_source 映射行为**（`"torch …"`/
    `"numpy …"` 的 backend 简写现仍受支持），属破坏性变更、且 catlass 那处 TODO 早就点名
    「测试不会红、但真跑时炸」——**单独一批做，别混在这里**。
- [x] **批 3 已落地（2026-07-23）· 任务书全文快照入库（R12）**——**它是整条 golden 来源契约链的前提，不是可选装饰**：
  没有快照，`verify_authorization` **恒返 False** → 任何声称「任务书指定了真值口径」的 golden 都被
  `derive_golden_tier` 规则② 判 tier 4（unverifiable_authorization）、直接 blocked。**所以批 2 必须等它。**
  - **落点**：`<ops_root>/<op>/task_doc.snapshot.md`（`repo_adapter.taskdoc_snapshot_path`）——
    落在**算子目录内**、与 spec/runner/golden 同处，不是取材工作区：引文锚要能随算子一起被复核、被搬运。
    文件名**只认这一个**（R2 的落地方式：cite 指向 PR / 仓内文件一律不接受，值域里没那个格子）。
    软链守卫复用 `op_dir`（从 ops_root 起逐段拒）。
  - **生成**：`fetch_source.py --snapshot-into <dir>`，**逐字节原样复制**（二进制读写、不经文本层）。
    ⚠ 不许任何规范化：改一个字节行号就可能移位，报出来的却是「引文与出处对不上」这种
    **看起来像 agent 编造引文**的错，真病因反而查不出来。
    ⚠ **已存在就不覆盖**——快照是引文锚，既有 golden 的 sha256 绑着它；静默覆盖 = 让所有既有锚一起失效却不报错。
    ⚠ **但「不覆盖」不等于「不吭声」**（自查补的）：上游任务书改版后安静留着旧快照、还打印旧 sha256，
    调用方会以为刷新过了 → **比覆盖更坏的静默**（验收基于一份自己都不知道过期的引文锚）。
    故内容不一致 **fail-loud**，报错同时给两个指纹 + 处置方式（删了重来**并复核 cite 行号**，改版后行号极可能移位）。
  - **端到端实证**：有快照 + 真引文 → 核过、**tier 1**；掉包快照 → tier 4 blocked；编造引文 → 拒。
  - 新增 7 条测试（含 CRLF + 尾行无换行 + 中文的字节保真用例）。
- [x] **批 4 · 判据锚拉回 spec（2026-07-23 · 口径 C，用户拍板）**：判据只从 spec 派生，caseset 自声明只作待核对断言（硬约束 #5）。
  - ⭐ **动因（真管路实证）**：批 5 那道 BLOCKED 门吃的是 **caseset 的 `expected.golden_tier.blocked_reason`**——改 `caseset.json` 一行（blocked→null）即绕过。逐步复现：真 tier1 `pass` → caseset 改 blocked → `blocked_golden_unauthorized` → 再从 blocked 改回冒充 tier2 → `pass`。门吃生成物的自声明，这正是 #5 存在的理由。
  - **⚠ 发现口径 C 的 preview（纯对账 authorization_kind + snapshot_sha）有洞**：门实际吃 `blocked_reason`/`requires_human_review`，攻击者只改 `blocked_reason=null`、不动对账字段，纯对账照样过、门照样失效。故做的是**更强版**：validator 对账后**用 spec 锚重新 `derive_golden_tier`**，判门用重新算的、不信 caseset 的 blocked_reason。
  - **落地**：`spec.golden` 判据锚（`source`/`method_kind`/`authorization.kind`/`taskdoc_snapshot.sha256`）· `gen_cases._derive_tier` 加 `snapshot_sha` 随 case 走 · `validator._reconcile_golden`（对账不符 fail-closed + 重新派生 + `golden_judged_from` 显式进裁决）。与 `golden.py` 的 `GOLDEN_CONTRACT` 是**双源交叉核验**（同硬约束 #4 模式），不一致 fail-closed。
  - **行为矩阵**（真管路 IsClose）：spec.golden 在场原始 `pass` · 改 blocked_reason **无效**（重新派生 → 仍 blocked）· 改 snapshot_sha `fail` · 改 authorization_kind `fail` · av=False 真实态 `blocked` · 无 spec.golden `pass`+`caseset_self_declared`（向后兼容但判据来源显式标出）。
  - **⚠ 残余边界（documented，非 bug，同 check_golden 的 os._exit）**：`authorization_verified`（读快照逐字核引文的结果）validator 纯函数复现不了，仍取 caseset。对账 `snapshot_sha` 把它钉到 spec，残余篡改面收窄到「真快照在场 + sha 对 + 引文不逐字」那一窄缝（授权本不实的常见情形无真快照 → 撞 snapshot_sha 对账 fail）。要消除须让 validator 读快照（口径 B）或引签名——不做，因跑测机 ≠ 裁决机时拿不到快照。钉成 `test_residual_boundary_documented`。
  - 🔴 **审修门（ultracode 红队 8 角度 + codex 代码审）各自独立逮到同一个 Critical**：第一版 `_reconcile_golden` 遍历 caseset 收集的 tiers，**spec.golden 在场时删掉 / 置空 caseset 的 golden_tier → 收集空集合 → 门整个不触发 → 静默 pass，还谎标 `judged_from="spec"`**。两条独立对抗审查指向同一洞 = 高置信。加上一圈 fail-open：畸形 spec.golden 当 legacy 放行 · oracle 缺 sha 的 None==None 假通过 · 去重丢 av · 非 dict authorization/不可哈希 tier 抛异常逃出 `validate()`。
  - **重写（codex 的架构建议对）**：**spec.golden 是判据权威，caseset 只用于「逐项核对 + 供 av」，从不决定 blocked**。三路径：无 golden 键=legacy · 有键但畸形=fail-closed blocked · 合法=从 spec 派生权威档（每 case 必带 dict golden_tier 否则删改信号 blocked、anchor 四字段对账、av 严格布尔且全体一致、有任何 problem 派生非 blocked 则强制 blocked）。对账不符归 **blocked**（判据链不可信，盖过 fail，符合批 5「真值来路不明盖过一切」）。全程 isinstance 守护、`validate()` 保持 total（异常不逃逸）。
  - **重写后攻击矩阵 11 场景全 fail-closed**（删 golden_tier/None/非dict/spec.golden畸形/缺sha/非dict authorization/不可哈希 tier 全 blocked 且不崩；改 blocked_reason 无效；legacy 兼容；残余边界 documented）。
  - **acc-spec 侧**（agent 行为，本地测不了、需真 session 复验，同 U1）：`acc-spec:extract_spec` 加产 `spec.golden`（两档链独立判，与 golden.py 双源）；`spec.golden.taskdoc_snapshot.sha256` 有**顺序依赖**（需快照先入库，否则留空记 gap 待 gen_golden 回填，**别编 sha**）。
  - 验证：本地 shim 751 绿 · 新增 8 条 `GoldenSpecAuthorityTest`（含批 5 洞的回归 + 残余边界钉子）。<a3 待补>
- [x] **批 5 已落地（2026-07-23）· 门侧接线：tier 真正参与裁决路由**
  ⭐ **核心判断（这条比实现更重要）**：golden 授权核不实 **≠ 精度 fail，也 ≠ needs_review**。
  它意味着**这份真值本身来路不明** → 基于它的每一条精度判定都不成立。
  - 报成 `fail` 会让人去查算子 —— **查错方向**（算子可能好好的）。
  - 报成 `needs_review` 也不对 —— 指标算得好好的，不确定的是**真值本身**。
  故单列 `blocked_golden_unauthorized` → 编排层 `BLOCKED_GOLDEN_UNAUTHORIZED`（exit 1）。
  - **排在所有别的判定之前**：来路不明的真值下，「精度 fail」「性能未达」这些结论本身就不成立，
    不该被它们盖住。配了「精度真的挂了 + tier 4」的负例钉住这条优先级。
  - **不进精度放行集** → 不跑 Task3：拿一份不知对不对的 golden 判过的「精度通过」去支撑
    「性能达标」，是把无效结论往下传。
  - `requires_human_review`（tier 3 / multistep，R5 末位档）→ 与 `passed_with_risk` 同档挂人工 CP，
    但原因分开如实记（`golden_needs_human_review`）。
  - **向后兼容**：`golden_tier=None`（未声明契约块）不参与门，裁决与批 5 前一致。
  - 裁决产物新增 `overall.golden_blocked` / `golden_needs_human_review` / `counts.golden_blocked`。
  - 新增 6 条测试（含优先级负例 + 编排层不落 FAIL(精度) 的源码级钉子）。
- [x] **批 6 · agent 产出侧（2026-07-23 落地）**：`acc-runner-dev` 加 `gen_golden` 模式，`golden.py` 有产出者了。
  - 三处事实源原子同步（漏一处 `check_agent_frontmatter.py` 就 exit 1）：契约表 SUBAGENTS · `agents/acc-runner-dev.md` · `plugin/AGENTS.md`；连带接进 CP-B 两处编排（`skills/acceptance-workflow/SKILL.md` + `agents/op-acceptance.md`），**排在 `--dry-run` 之前**——理由是让 CP-B 在 dry-run 前就完成来源契约检查（`check_golden.py`）。⚠ **不能说成「dry-run 会因缺 golden fail-closed」**：真 `gen_cases()` 缺 golden 才 fail-closed，`_dry_run` 专门捕获「缺 golden」降级成「未核」照常出计划（文件在但坏了才抛）。
  - 新增手册 `skills/acc-runner/references/golden-authoring.md`（两档链决策树 · 文件骨架 · `GOLDEN_CONTRACT` 逐字段 · 何时 BLOCKED）+ 确定性自检 `acc-common/check_golden.py`（退出码 0/2/1 三态，8 条单测钉死）。
  - **runner 的 scope gate 明确不套到 `gen_golden`**（已在 agent 文本里写死）——golden 是纯 CPU Python、与算子仓布局无关；套上去正是「只支持 elementwise」那类窄化的来源。
  - ⚠ **原条目里「scope gate 仍写 bf16→BLOCKED 已 stale」那句本身是错记**：实读该行，它早已改成「runner 侧有 bf16 分支，但真机 kernel 支持须逐算子确认」——**不是 stale，是有意的逐算子纪律**，本批未动它。
  - ⚠ **「放宽 runner scope gate 覆盖面」未做**，仍是 P1 的独立一刀（见下条）。本批只解耦了 golden 与该 gate 的绑定。
  - **审修门逮到 4 个 fail-open**（详见简表 07-23 条）：golden 里 `SystemExit(0)` 假绿（**引擎主路 `load_golden` 同洞、一并修**）· argparse 参数错误退 2 与「需人核」撞车 · 退出码按 tier 路由漏掉 `(tier 1, 需人核)` · 必需导出只查 `hasattr`。已全修 + 8 条回归。
  - ⚠ **留下的诚实边界（已写进 `check_golden.py` docstring，不是遗漏）**：golden.py 与检查器**同进程**执行，`os._exit(0)` / C 层退出挡不住，要挡须换子进程隔离。**有意不做**——runner.cpp 本身就要编译并在 NPU 上跑，只给 golden 加沙箱是不对称的。
  - 验证：本地 shim **743 绿** · **a3 真 torch 2.13.0 743/743 绿** · a3 上 `check_golden.py IsClose --load` exit 0、`SystemExit(0)` exit 1。
- [~] **批 6b · 放宽 runner 的 scope gate 覆盖面**（方案 `doc/oprunway-batch6b-design.md`；期1-A 已落地）：**期1-A ✅**(接回 VENDOR_SUFFIX + 8 处 stale gate 全仓对齐 + 零引擎改动放行 ops-<族> 非 experimental aclnn;期0 债经实证确认已还、scout 误报)。**B-core ✅**(commit e1c2e6b:接口探测器 5 类 + 从 test_aclnn 抽真实入口名 + 18 算子据实核放行 6 个)。**期2 C ✅ gen_cases 层**(3 shape_transform 样例 im2col/upsample2d/3d 真 torch 各 50/21/21 case、out_shape 对账过;真机 NPU 验收另需 a3 build runner)。**剩** 期3 D(dtype/多输入/双实现/catlass,逐项立项+真机预算,开放大工程)。原条目——现仍只认 `experimental/math/<op>` + aclnn 两段式，非此一律 BLOCKED/转 P3。要从任务书推目标目录与接口形态（守「零硬编码 / 探测或问」的最高律令），配 `OPRUNWAY_TARGET_DIR` 等。⚠ 这是「支持所有任务书里出现过的算子类型」那条用户指令的**剩余大头**——批 6 只让 golden 侧不受它拖累，runner 侧仍窄。
- [ ] **批 7 · 报告 + canon 收口**：报告展示 tier / provenance / 人核项；ADR 0011 与本轮裁定走 `capture → compile → review`（**现 ADR 0011 仍 `proposed`**）。
- [ ] 贯穿项：产出物落点 `<ops_root>/<op>/`，与 `find_runner`/`load_golden` 的安全边界（**逐段拒软链**、`_check_id`、缺则 fail-closed）对齐。
- [ ] **R8 记账**：catlass 通路（`catlass_adapter.py` 的内置 matmul golden）本轮 **out-of-scope**，两档链暂不覆盖它。

### 🔵 P2 · 扩展 / 接通
- [~] **插件-算子解耦**（`doc/oprunway-plugin-op-decoupling-design.md`）**引擎侧两刀均已入 main** ✅：① **runner 去引擎化**（**PR #7**：3 份样例 runner 移出引擎 → `samples/runners/`、runner 只作输出、`find_runner` fallback 退役改 fail-closed、门 runner_source 仅 user）；② **golden 去引擎化**（**PR #8**，`1d2bb3a`：`GOLDEN` 硬表 → `load_golden(op)` 加载器、4 内置 golden 迁 `samples/golden/<op>/`、来源契约扩六枚举、ADR 0011 `proposed`、来源契约批 1 `0192e49`）。⚠ **口径（2026-07-22 收窄）**：成立的只是「**elementwise 通路**的 golden 值已去引擎化」，**不是「引擎零内置算子」**——`catlass_adapter.py:152/:162`（matmul golden 内置、注释明写有意不进加载器路径）与 `gen_cases.py:34` 的 `_BF16_EXACT_OPS` 按算子名硬表是**两处已知例外**（catlass 通路本轮 out-of-scope）。剩下的是产出侧，见上「🔴 下一刀」。
- [ ] **(a) TBE 信息库接通**（dtype 独立源）：每份任务书自带路径 `.../tbe/config/ascend910b`；读法随运行环境探测、**不写死 ssh**。
- [ ] int32 扩展（Track C，锁已解）。

### ⏸ 外部阻塞（等资源，非我们能推）
- [ ] 真实 GPU 基线数据（Task3 真对比）｜其余 11 仓 adapter｜catlass 真机验收（需 950 + generated_harness）。

> **golden resume 要点（别丢）**：AscendOpTest 自己没 golden 源、只有 `expect_func`/`golden_path` 槽位（开发者填）→ 真问题是 numpy 忠不忠实任务书语义 + 有无交叉核/诚实记录。四算子 reference 已核（`repos/cann-ops-competitions/.../docs/202604/*_task_doc.md`）：IsClose/Equal=语义改造→CPU 逻辑（np.isclose/np.equal 忠实）；Sign=纯重写（np.sign 忠实）；Neg=uint8 点名 torch.neg 回绕（待核）。连带：现 IsClose/Sign「PASSED」精度维需重核 golden 忠实性才算合规。
> ⚠ **2026-07-22 更正（E3）**：上句「Sign=纯重写」是**从任务书读出来的授权、并不存在**——Sign 任务书**一字未提** torch/numpy/公式，只说「参考昇腾内置 Sign 的 TBE 实现 + 增加 int16」，那是 **`impl_reference`、不构成 golden 授权** → Sign 实为**第二档回落**（CPU 现成 API `torch.sign`），**不是第一档「任务书指定」**。golden 值本身没错、**措辞错**（`samples/golden/Sign/golden.py` 的 `GOLDEN_PROVENANCE` 同错，**已在代码侧更正**）。对照：IsClose/Equal 任务书**有**原文（「二进制比较改为逻辑值比较」）→ 写「任务书指定」是准确的。

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
   - **本会话新/改 6 页**（`opp-provenance-bound` / `case-generation-follows-opbase-section-1` / `precision-gate-precedes-performance-fail-fast` / `performance-reuses-precision-inputs-with-trivial-met` / `real-npu-runner` 更新 / `spec-examples-pollute`「已修复」）：待复核 promote。⚠ **`real-npu-runner` 页「标题 vs body」改名**在此步收口——body 已 fp32/fp16/bf16、标题仍「only fp32/fp16」+ supersede 横幅，人审时决定改名并修其它页对它的 `[[…]]` 入链。
   - **ADR 0011 golden 去引擎化**（`canon/decisions/0011-golden-decoupling.md`，`proposed`；**代码已随 PR #8 入 main，ADR 本身仍待 promote**）+ 连带被标 supersede 的 `golden-fixed-to-torch-cpu` 页（决策 4 把「恒 torch 单后端」放宽为「按算子 torch>numpy 定档」）。
   - ⚠ **`golden-source-from-taskdoc-method` 页记的是写窄了的旧律令**（「只能来自任务书指定方法」，**漏了第二档**）——用户 2026-07-22 重定为**两档链 + R2/R4/R5/R6**（见上「最高律令」段）。canon 页**不得手改**，须走一次 `capture → compile → review`；在此之前该页视为**待更正**，载重前以本 doc 为准。
   - **ADR 0010 现为 `contested`，只欠人裁**（2026-07-22 实读 frontmatter 更正——此前记「stale / 待走 capture→compile→review」**是错的**，capture 与 compile 都已完成）：页上已并列两个 claim——**Claim A** 双触发点（2026-07-06 canonical：bureau 写入前审拟写文本 + md/代码生成后审产物）、**Claim B** 单触发点收敛到 commit 之前（2026-07-10 用户下令，未经 review），页首明写须经 `bureau:review` 由人裁决后才恢复单一 canonical 表述。**现行执行规则仍以 CLAUDE.md #5（= Claim B）为准。**⚠ 连带：CLAUDE.md #5 那句「ADR 0010 仍记旧触发点…待走 capture→compile→review」的注脚同样过时，但改 CLAUDE.md 须先经用户点头。
   - T9 发布形态决定（当前 `proposed`）、门职责扩展（「门内重算比对」属证据可信、非重判 verdict）。
   - **2 条 lint survivor**：ADR0002 `msTuner`→`msprof op`；5 页 `1.2×`→`target_ratio`。⚠ 护栏：ADR0006/0008 未同步 rename 前**不宜单独 promote**（否则固化 drift）。survivors **单靠 `bureau:note` 不进 review 视图**，需 `bureau:lint --apply`（改 canonical status、消耗性）或 `bureau:compile` 才可见——用户已选「先不跑、留 review 一次处置」。
2. **T4 catlass 偏离 canonical 需人裁**：未走 canon `catlass-to-aclnn-bridge`【canonical】的路线 A/B，自选「注入其自带 example 树的 repo-native harness」第三路径。要么人门追认（改 canon），要么改回 A/B。
3. **「完工」标准未定**（够 demo / 够内部用 / 够对外发布）——定了才能倒推「到可用 v2 还差哪几步」。

### B. 外部资源阻塞
4. **真机验证**：待 `ascend-a5`（真 950 / arch 3510）+ VPN。catlass 真机 build/run、Track C 的 int/bf16 runner、AscendOpTest bool cross-check 全挂在这。
5. **真 GPU 基线数据**：consumer 侧与最小字段契约已就绪，缺数据即走 `BLOCKED_WAIT_GPU_BENCHMARK`。

### C. 已收口（不再是待办）
- ✅ 公开台账 push + **PR merge**：**PR #6**（`f91ccda`）、**PR #7**（`b727d6f`）、**PR #8**（`1d2bb3a`，2026-07-22）均已 merge 进 main。⏳ **GitCode 镜像 `brian66237` 尚未同步 PR #8**（GitHub `lllyys` 已同步）。
- ✅ **引擎去 runner 化 + 去 golden 化两刀均已入 main**（PR #7 / PR #8）——「**elementwise 通路**第 5 个算子不再撞死在硬表上」现已成立。⚠ catlass 通路的内置 matmul golden 与 `_BF16_EXACT_OPS` 硬表**仍在**（见头部两处例外）。
- ✅ 清掉 6 个 SessionEnd 机械空 stub（`canon/logbook/2026/07/`，无内容、非 `bureau:file-session` 产物）。
- ✅ 真机 opp provenance 绑源 + IsClose bf16 转 tested（2026-07-16）；provenance 批 4-finding 收口（2026-07-16）。
- ✅ PR#2 body Equal 作废更正：2026-07-10 在线复核确认 body **早已含更正**（裁决表 Equal 行 = 「无结论·结论作废」），denylist 词仅出现在作废叙述内，评论/review 无旧结论 → **无需编辑**。

## 备注

- 详细设计/契约见 `doc/oprunway-design.md`；各 todo 的实施 plan（均经 codex 审）见 `doc/oprunway-todo-plans.md`；改动流水见 `doc/oprunway-changes-brief.md`。
