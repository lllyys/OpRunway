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

- [ ] **U1 · `op-acceptance` 派不出 subagent、也问不了用户（最贵；改动只一行）**：实测该 agent 130 条消息里工具只有 `Bash`×30 + `Write`×1 —— 设计里的三个 subagent（`acc-spec-extractor` / `acc-runner-dev` / `acc-verify-rootcause`）**一个没派**，`AskUserQuestion` 是**主循环替它问的**。根因在 `plugin/agents/op-acceptance.md:5` 的 `tools: Bash, Read, Write, Edit, Skill` —— **既无派 subagent 的工具、也无 `AskUserQuestion`**；frontmatter 的 `agents:` 声明**不授予工具**。→ CP-B/C/D 的分工、subagent 单轮约束、循环控制权**全是空的**，它只能自己读源码手搓。改法：`tools` 补齐，或直接删掉 `tools:` 让它继承全部。（对照：`skills:` 那条**有效**——`acceptance-workflow` 两次都自动注入了。）
- [ ] **U2 · `fetch_source.py` 的 PR URL 正则太窄 + 失败太安静**：用户给 `/pull/2663`（GitHub 风格），脚本只认 `pulls|merge_requests`，**不报错**、产出空 `pr_facts` + 一条 note 就继续往下走。agent 是 `grep` 脚本源码才诊断出来、自己把 URL 规范化成 `/merge_requests/2663` 重跑的。→ 换个不肯读源码的 agent，对应校验就会带着空 `target_dir` 糊过去。改法：接受 `/pull/N` 形态；解析失败 **fail-loud**，不产空壳往下传。
- [ ] **U3 · `samples/` 不随插件分发（预测命中）**：实测 agent 在 `plugin/` 下 `find samples/golden` 得 `No such file or directory`——`samples/` 在**仓根**，而 marketplace `source: "./plugin"`。→ `acc-runner/SKILL.md` 让 agent「照抄 `samples/runners/*.cpp`」指向的是不存在的文件，runner 生成会静默退化成凭空写（正撞 Equal 血教训）。改法：搬进 `plugin/`，或把骨架内联进 skill 参考文档。
- [ ] **U4 · 「干净 session」隔离实为无效**：skill 注入时报的 base directory 是**活仓** `/…/OpRunway/plugin/skills/acceptance-workflow`（**不是** `~/.claude/plugins/cache/`）——marketplace 是 `directory` 源、`installLocation` 直指仓根。于是 agent 那 30 条 Bash 几乎全在读活仓源码。**连带**（⚠ 下句是**按本次实测推的**、未穷举验证）：`/plugin install` 的快照看来只决定**组件注册**（有哪些 agent/skill、frontmatter 长什么样）；至少对「按 base directory 解析的文件」和「agent 直接读的源码」这两类，读的是**活仓工作树** → 仓一旦切分支，这部分行为就可能与安装时的快照对不上。要真隔离得换非 `directory` 源。
- [ ] **U5 · head 兜底照旧（预测命中）**：`pr_facts.json` 得 `"base": "master", "head": "master", "merged": true` —— head 兜底真的触发了，全程无 sha。本次因 PR 已合入而无害，但「被测 = PR 版」仍无机器保证（承 canon `pr-head-commit-is-the-tested-object`，`proposed`）。

#### Pdist 暴露的「非 elementwise 通路」空白（G1–G4；能力边界扩展，须单独立项）

> 均由验收 agent 实跑取证，逐条落在 `work/reports/pdist/scope_conclusion.json` 的 `hard_evidence`。
> Pdist = 成对归约：输入 `(N,M)` 二维点集 + float 属性 `p` → 输出 `(N*(N-1)/2,)` 一维。

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

- [ ] **批 2 · golden.py 来源契约字段 + `load_golden` 接线**：`GOLDEN_SOURCE` 之外补声明式来源块（source_kind / method_kind / authorization / cite / quote），加载时派生 tier 写进每条 case。
- [ ] **批 3 · 任务书全文快照入库（R12）**：`task_doc.snapshot.md` 的落点 / 命名 / sha256 契约与生成路径——`verify_authorization` 没它就核不了。
- [ ] **批 4 · spec 侧承载**：`acc-spec` 产出 golden 来源声明 + 两档链判定 + 人核标记，写进 spec（判定权威只在 spec，硬约束 #5）。
- [ ] **批 5 · 门侧接线**：`validator` / `validate_acceptance_state` / `run_workflow` 消费 tier——blocked → BLOCKED、`requires_human_review` → 人核 CP，不静默放行。
- [ ] **批 6 · agent 产出侧**：`acc-runner-dev` 补产 `golden.py`（**R6 生成期选 torch/numpy 并写死进文件**）+ 放宽 runner 的 scope gate 覆盖面（从任务书推，守最高律令）。⚠ 连带：该 agent 的 scope gate 仍写 dtype 仅 {fp32,fp16}、`bf16→BLOCKED`，**已 stale**（bf16 真机已验收过），须同步。
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
