# OpRunway 改动简表

> 倒序：最新在上。每天一节，一条一句，大白话。`待决` 置顶。

## 待决（还没定的事）

1. 算子任务书的真实格式没拿到（已知是 md）→ M1 拿真实样例校准 §3 契约字段。
2. ~~精度口径之争~~ **已定**：三层（任务书 > 平台标准 > catlass 内置只作 smoke），阈值待任务书校准。
3. ~~性能口径~~ **已定**：timing_scope 必填、默认 kernel-only；待与 GPU 标杆对齐。
4. GPU 标杆 schema 外部未给，但「我们需要对方给什么」的最小字段已先定（design §7）。
5. 发布形态已定倾向（自维护仓 + skills sync）；补「接口稳定前不 external-sync」。仓位置/插件名未定。
6. 远程 NPU 环境（哪台机、catlass 在哪 build、是否进 Docker）待用户提供后补进 CLAUDE.md。
7. 优先级（Codex 排序）：Q3>Q4>Q5>Q6>Q1>Q2>Q8>Q9>Q7。完整见 `doc/oprunway-design.md` §13。

## 2026-07-08

- **补齐 agent+skill 体系 + 端到端跑通（初步可用）**：装上 keystone——`agents/op-acceptance`（编排 agent：(任务书,PR)→六步→裁决/报告，人不碰 spec.json）+ `skills/acc-runner`（③ 据算子自带 example 生成并验证 per-op runner）+ `.claude-plugin/plugin.json`（可 `/plugin install` 的 manifest）+ 更新 `commands/op-acceptance` 到 (任务书,PR) 入口。**端到端 demo（新算子 Neg）**：`Neg_task_doc` + `ops-math!2680` → fetch → acc-spec 产 `neg.spec`（应用 codex 修的规则：dtype 只填支持子集 fp32/16+余入 gap、『不劣化』→target_ratio 1.0、uint8 回绕特例入 gap）→ gen_cases 注册 `golden_neg` → run_workflow mock → **裁决 PASS**（5 用例）；`task_pr_gaps` 带 3 缺口；原三算子无回归。**mock 端到端可用；new_example 真机待 VPN + runner 验证。** ③ 经 codex 审出**过度声称**（构建路径选择/验证硬门其实代码没做、attr_order 非 spec 字段、双哨兵漏写等）→ **诚实收窄**：仅 `experimental/math` aclnn 闭环、验证-才-信是**纪律非代码硬门**（sidecar 待补）、legacy/catlass 标待扩。全套推 **PR #1**（github lllyys + gitcode brian66237），评论附任务书↔PR 测试案例 + 端到端 demo 证据。

## 2026-07-07

- **入口改产品形态：agent 收(任务书, PR)→自动 spec（①② 建成，标准 skill/agent 形式 + 可移植）**。经用户点头定方向：真实输入 = **任务书(md 或链接) + PR 链接** → **agent 自己**产 spec、决定跑哪些步、出报告，**不再人肉搓 spec.json 喂 run_workflow.py**。**①** `fetch_source.py`（取材：任务书本地/链接 + PR gitcode API → `task_doc.md` + `pr_facts.json`，含算子自带 example + `op_def.cpp`；token 走 env 不落盘、纯 stdlib 可移植）。**②** 标准 skill `plugin/skills/acc-spec/`（任务书→spec.json）——**ultracode fan-out 27 agent 读 23 份真实任务书**归纳「任务书→spec」抽取规则、对 isclose/sign/equal 三手工 spec 验证：**IsClose 一致；Sign/Equal 分歧反证我手写 spec 有漏**（Sign 漏测 int16、『无劣化』该 1.0 非 0.95；Equal 漏 small_shape 例外 + change.kind 误标）。关键洞：**23/23 任务书不给精度阈值数值** → 兜底填惯例值标 (推断)；『支持所有 dtype』模糊 → 靠 **PR 的 `op_def.cpp` 权威 dtype 集**补（Sign 真集 `{bf16,fp16,fp,int32,int16}`、Equal `{fp16,bf16,fp,int8,uint8,int32,uint32}`）；runner 入口靠 **example 的 aclnn 调用**锚定（治 Equal 猜错入口那病）。CLAUDE.md 现状/目录已更新。下一步 ③ runner 锚定+构建路径 → ④⑤⑥。
- **记 TODO**：`doc/oprunway-todo.md` 落地——主干完工+真机验证过，但离「通用算子验收工具」还差 9 个洞（**P0**：任务书→spec 自动化、per-op runner 锚定+构建路径选择+root-cause 入 harness）+ 3 条用血的教训钉住的硬约束（FAIL 先解耦 root-cause / 平台·spec 从任务书推别猜 / 合入用 gitcode 查证）。
- **三算子真机验证泛化——每个门都真在判、三种不同裁决**：把 workflow 从 IsClose 单例泛化到 op 驱动（`gen_cases` 按 spec 的 arity/attr/verify_mode 分发 + `GOLDEN[op]` 注册；`repo_adapter` 输入按序 `x{j}.bin`、manifest 按 `attr_order`、out 按 golden dtype、runner 按 `oprunway_{op}_runner.cpp` 选、snake 名 `_snake()` 派生；`run_on_npu.sh` 用 `OPRUNWAY_RUNNER/OPNAME` 参数化）。**加一个算子 = spec + golden + runner 三文件**。三算子真 A3 跑通，出**三种互不相同的诚实裁决**：**IsClose**(二元/bool/3attr) 精度 pass + perf custom 15.7<TBE 22.7us **快** → **PASS**；**Sign**(一元/数值/无attr) 精度 pass(max_rel_err 0) + perf custom 9.7>TBE 6.3us **慢** → **性能未达成**；**Equal**(二元/bool/无attr) 出 **精度 fail** → **FAIL(精度)**（根因已在内部定位并记录，细节暂不公开）。**门是真在判不是盖章**（性能门抓偏慢的 Sign、IsClose 两门皆过、Equal 检出输出≠golden）。<br>**教训（最该记）**：任何 FAIL 必须先用「被测物自己的 build + 声明支持的 dtype + 手算 golden」解耦 root-cause、确认是「被测物 vs 我的 harness」再归因，不能在质疑下来回改口；平台/spec/构建路径从任务书推别猜；合入状态用 gitcode 查证。**门的机制没问题**（precision 门确实检出输出≠golden，问题只在归因）。**IsClose/Sign 可信**。
- **workflow 真机端到端跑通（new_example）**：写 `repo_adapter.run_new_example`（npy→bin+manifest → tar-over-scp 部署 a3 → `build.sh --run_example` 真 NPU 跑 → 拉回 out.bin → 采集 evidence；广播用 materialize 规避改 runner；golden 不出本机）+ codex 写的参数化 aclnn runner（`oprunway_isclose_runner.cpp`，读 `OPRUNWAY_CASES`/manifest 循环 case）。`run_workflow --mode new_example` 在真 A3 NPU 跑 IsClose **6 用例、裁决 = pass**（功能/精度 mismatch=0，含广播/float16）。全程用户态、不碰共享 opp。**codex 审出 2 Critical**（远端失败/旧 out.bin 被判**假通过**）+ 多 High（注入/未校验），我修全：哨兵 `OPRUNWAY_DONE` 判成败、tar 排除 out.bin、shlex+白名单防注入、输入/golden/字节校验、拒空 Tensor——codex 复核全 FIXED。**perf 也做成真的了（msprof 闭环）**：`build.sh --pkg` 建持久 custom op 包 → 装用户态 opp → 编**双 exe**（custom 链 `libcust_opapi` + 内置 TBE 链系统 `libopapi`）→ **`msprof op` 取真 kernel-only `Task Duration(us)`**，**内置 TBE 同法 msprof 作真基线**。远端编排落 `new_example/run_on_npu.sh`。实测 **custom 16.5us vs TBE 23.1us、ratio 1.40 达标**（Ascend C 比 TBE 快 40%）。**Task 3 出真性能裁决（非 blocked/mock）。整个 workflow 真机端到端·精度+性能全真。** 曾试的 aclrt event 计时被 codex 判非 kernel-only（op-call 口径、50-80ms 不随规模变）已弃用。**codex 审出假通过风险、我修全**：stale exe（改 hash-stamp `md5(runner)+SOC+vendor` 判脏重建）、内置 TBE 基线被 custom 用户态库污染（改用干净 `SYS_LD`）、stale perf_result/_real_baseline（拉前删本地 + run_workflow 每轮清）、perf 解析用 `math.isfinite`、总体口径同时 gate 精度+性能（防只看退出码假通过）。⚠ 记：msprof 拒写组/他人可写目录（输出目录 chmod 700）；`set -u` 会被 vendor set_env.bash 的未绑定变量搞崩（用 set -e）；`rm -rf` .run 装的只读目录前先 `chmod -R u+w`。
- **真机跑通 is_close（A3 真 NPU）**：a3 起 autossh 隧道 → clone ops-math → `build.sh --experimental --ops=is_close --soc=ascend910_93` 编成 → `--run_example is_close eager` 在真 NPU 跑出 `[1,0,1,0]`、exit 0。全程 `/home/lys` 用户态；**共享 `opp/vendors` 未污染**（虽 777 可写，build.sh 自设本地 `ASCEND_CUSTOM_OPP_PATH`）。⚠ 记：a3 共享 opp 是 777、务必用户态。下一步：参数化 example 读/写 bin（route-B 套路）+ 接 `repo_adapter.new_example`。
- **workflow v0 建成 + 跑通 + 过代码门**：`plugin/acc-common/` 建三层——Layer 0 契约（`specs/isclose.spec.json`）+ Layer 1 确定性脚本（`gen_cases`/`repo_adapter`/`validator`/`perf_compare`）+ 驱动 `run_workflow`；Layer 2 入口 `README` + `commands/op-acceptance`。**端到端跑通 IsClose**（mock NPU + 真 numpy golden），能抓 defect（→ 裁决 fail）。`cc-suite:audit-fix` 代码门审出 **15 处**（含 **Critical 假通过漏洞**：validator 不校验 caseset↔evidence、阈值不以 spec 为准、perf 缺项静默跳过），**2 轮修全、codex 复核通过**。`new_example` 真机跑测留桩（待上 NPU + VPN）。

## 2026-07-06

- **compile 本会话结论入 canon**：把 07-06 的 durable 结论 distill 成 **5 个新 proposed 页**（`ecosystem-precision-standard` MERE/MARE 一手+更正、`workflow-three-layer-architecture`、`task-spec-authoritative-over-pr`、`engineering-paradigm-trichotomy`、`perf-baseline-by-reference-source`）+ 更新 `ADR 0006`（kernel-only 双边同口径坐实）。codex 散文门修 6 处（含与 canonical 的 `perf_baseline_source` 冲突→按 bureau 冲突策略标注待 review、不单方改）。结构全绿、canonical 19 / proposed 7。待 `bureau:review` 升 canonical。
- **Layer 0 坐实（一）· `spec.json`**（`doc/oprunway-spec-schema.md`）：schema + Sign/IsClose/SPMV 三真实例（TBE 重写 / 语义改造 / GPU 移植），验证兜住参考三类·改动·精度分层·性能基线的多样性；精化出 `params_source` / `dtype_combinations` / `precision.fallback` / `perf.reference_cases` / `verify_mode` 推导。codex 对本地三份任务书逐一核对、修 8 处（补 SPMV dtype 组合约束、IsClose `other`、收紧覆盖声明等）。
- **workflow 设计 v1**（`doc/oprunway-workflow-design.md`）：据地基定**三层**（数据契约 JSON ／ 确定性脚本核心 ／ per-tool 薄壳），遵约束 A 可移植——**6 个 JSON 契约**（spec/caseset/evidence/verdict/baseline/perf_report）+ **4 脚本**（gen_cases/repo_adapter/validator/perf_compare）+ CC 薄壳（编排 + parse agent + 3 skill + eval）。核心脑子沉到脚本、stage 间只传 JSON。codex 散文门**两轮**、确认 Layer 0/1 无 Claude-Code 依赖。
- **任务书规格 + PR 内容规律总结**：深读 18 个代表 PR（5 agent，跨新算子/加dtype/移植 + 6 仓）→ `doc/oprunway-spec-pr-analysis.md`。关键：**任务书是权威**（PR 不逐项对齐、落差标待确认）、**证据得自己产**（性能证据基本缺席、精度强弱不一）、**工程范式三分**（标准 GE / experimental 库式 / 头文件库）、契约要覆盖「验证模式×精度口径×性能基线×整型语义」的多样性。codex 散文门审过、收紧了 7 处过度概括。
- **社区任务书 ↔ PR 全量对应**：clone `cann-ops-competitions`，抽出 7 月前 **41 份任务书**（202604/202605），3 个 agent 逐仓 file-level 匹配 PR → `doc/oprunway-task-pr-map.md`。34 找到 / 7 未找到；发现「一任务对多 PR 是常态、多为 aclnn 原生 new_example、主 PR 多 open」。codex 审出计数/残留问题、已修、复审通过。
- **散文门改走 codex CLI**：查明 nlpm 1.1.1+ 移除了 codex MCP → CLAUDE.md #5 / ADR 0010 / memory 的引用从 `mcp__…codex` 改成 `codex exec`；实测 CLI 兜底可用。
- **review 收尾**：`repo-adapter`、`ADR 0010` → canonical（共 19 篇）；剩 ADR 0006/0008 待审。

## 2026-07-02

**流程规则 + bureau compile**：定 Codex audit-fix 双门规则、compile 本会话修订入 canon。**本轮改动文档及 purpose：**

- `CLAUDE.md` 最高优先级规则 **#5**（新增，Codex 审过）— purpose：**Codex audit-fix 双门**——bureau 变更前审拟写文本、md/代码生成后审+修产物；分工=代码/脚本走 `cc-suite:audit-fix`、散文走 `codex exec`（Codex CLI），`nlpm` 非本门。auto-memory `codex-audit-fix-gate.md` 存指针。（2026-07-06：散文门原写 MCP `mcp__plugin_nlpm_codex-cli__codex`，nlpm 1.1.1+ 移除该 MCP → 改走 `codex exec` CLI。）
- `canon/decisions/0010-codex-audit-fix-gate.md`（新建 ADR，proposed）+ `canon` 更新 4 页（`ADR 0008` e2e 对齐 / `acceptance-contract` 加 `perf_baseline_source`+GPU 非默认基线 / `acceptance-pipeline` GPU=Task3 对比 / `catlass-to-aclnn-bridge` Codex 桥修订）— purpose：把 d31ea446 的修订 compile 进 cabinet（全 proposed，待 review 提 canonical）。散文门 Codex 又修 3 处（移除 gpu_external 等）。结构校验 22 页全过。
- review 决策：**全 HOLD、先 compile**（避免把已知错声明固化成 canonical）；compile 后 4 页已修，待下一轮 review。

---

**AscendOpTest 桥真机验证**：Codex 审计设计 + 造出路线 B 全套桥制品 + a5 装 conda。**本轮改动文档及 purpose：**

- `plugin/bridge/route_b/`（新建整套）— purpose：路线 B 真机去风险制品。`fake_exe/oprunway_bridge_matmul.cpp`（假 exe，复刻 43 catlass 启动、两端 IO 换读/写框架约定 bin）、`optest_cases/matmul_ir.json`+`matmul_cases.json`（CatlassBasicMatmul 单 case，512³ fp32）、`golden/matmul_golden.py`（expect_func，fp32 累加）、`aclnn_op/CMakeLists.txt`（dummy，供 get_exe_name 抠 execute_matmul_op）、`run_derisk.sh`（build/stage/precision/perf 编排，DRY_RUN）、`README.md`。
- `canon/logbook/2026/07/d31ea446-….md`（新建 minute）— purpose：本会话 provenance（Codex 审计结论 + baseline 更正 + 方案 + 制品）。
- 决策：真机验证选 **a5 + 43_basic_matmul + 精度闭环+perf + 路线 B only**；a5 依赖用**用户态 miniconda**（用户指示「没有 conda 就装一个」，a5 直连 pypi 200）。

**要点**：① Codex 只读审计 `catlass-to-aclnn-bridge`（回源码 file:line）→ 2 阻断（路线 A 默认写共享 opp/vendors 违规；路线 B 无「谁编 exe」闭环）+ exe 名解析脆弱 + `-k` 命中模板符号不可预设；catlass 自带 `examples/advanced/basic_matmul_aclnn`（含 `extern "C"` 包装）恰是桥参考。② 用户更正 acceptance-contract 的性能「标杆」不必然是 GPU（默认 = NPU torch 未融合链；GPU 是 Task 3 对比），已 `bureau:note` 存档、建议加 `perf_baseline_source` 枚举。③ 回源码钉死路线 B 全部契约后落成制品。桥制品**未在真机编译/跑测——待用户 go**。

---

起草 **acc-casegen 首个组件产物 rule-catalog**（v1 手写 → 对抗评审 → v2）。**本轮改动文档及 purpose：**

- `plugin/skills/acc-casegen/references/rule-catalog.md`（新建 v2）— purpose：acc-casegen 核心 IP，规则库（11 原语 + 元规则 + 跨切面 dtype/layout/tiling + 组合规则），对任意算子查表生成用例。
- `doc/oprunway-rule-catalog-critique.md`（新建）— purpose：v1→v2 评审提炼（40 findings：覆盖漏洞/Ascend 硬件契约/数值机理错误）。
- minute — purpose：provenance（起 `plugin/skills/acc-casegen/` 骨架）。

**要点**：评审揭示 v1 漏 ~40% catlass 算子族（attention/conv/swiglu/sparse/routing）+ 数值 why 错（large_K/bf16/golden一路fp32）+ 缺 Ascend 契约（NZ 分形/splitK 原子加/tiling/workspace）；v2 全补，并加 `UNCOVERED_PRIMITIVE` 硬 guard。rule-catalog 是 canon「Primitive-to-case rule library」的实现（落 plugin/）。

---

转向**通用工作流**（用户明确「要通用、不只这一个算子」）：手写 cases.yaml → 对抗评审 → 提炼通用规则并正式化。**本轮改动文档及 purpose：**

- `canon/architecture/primitive-to-case-rule-library.md`（新建，proposed）— purpose：acc-casegen 核心 IP，原语→case 规则库 + 展开逻辑 + 元规则（跨算子）。
- `canon/architecture/generated-harness-responsibilities.md`（新建，proposed）— purpose：generated_harness 4 职责（bin-IO shim / layout 字节 / 数据注入 / 性能测量栈，跨仓+跨框架）。
- `canon/architecture/oprunway-component-breakdown.md` / `repo-adapter.md`（更新，proposed）— purpose：把 acc-casegen 挂规则库、仓适配器挂 harness 职责。
- `canon/decisions/0006-performance-timing-scope.md`（更新，proposed）— purpose：e2e 更正——AscendOpTest 能采 e2e（内建 `msprof --application`）、解析归我们 → device_e2e 可行。
- `doc/oprunway-task1-cases-critique.md`（已建）— purpose：对抗评审→通用规则提炼（规则库 + harness 4 职责的种子）。
- `doc/oprunway-design.md` §5/§7/§9 + `doc/oprunway-ascendoptest-probe.md`（更新）— purpose：同步两条通用能力 + e2e 更正。
- `reports/catlass/.../cases.yaml`（草稿夹具，含待修项）+ minute — purpose：首个验证夹具 + provenance。

**要点**：产品 = acc-casegen（跨算子生成器）+ repo-adapter/harness（跨仓跨框架跑通）；手写 case set 是**夹具非产品**。「生成用例」易、「让它真跑」难且通用。新页 proposed，tier 提升走 `bureau:review`。

## 2026-07-01

深挖 **AscendOpTest**（任务书精度实体 + 「性能也能用」）；用 workflow 4 维并行+对抗验证，**复用判定 = hybrid**。**本轮改动文档及 purpose：**

- `doc/oprunway-ascendoptest-probe.md`（新建）— purpose：AscendOpTest 深挖参照（精度阈值/判据、性能口径、catlass 桥两路、hybrid、待实测项）。
- `doc/oprunway-design.md` §5/§6/§7（更新）— purpose：§6 平台层=AscendOpTest 默认阈值(FP16 1e-3+0.1%坏点)；§7 补 1.2× 由 validator 算 + 同口径 caveat；§5 补 aclnn 桥。
- `canon/decisions/0008-reuse-ascendoptest.md`（新建，proposed）— purpose：Task2 精度+性能验收 hybrid 复用决策。
- `canon/architecture/ascendoptest-precision-thresholds.md`（新建，verified）— purpose：精度三层「平台层」实体（FP16 阈值+判据+复用 compare+自供 golden）。
- `canon/architecture/catlass-to-aclnn-bridge.md`（新建，proposed）— purpose：catlass 裸 kernel 接入 AscendOpTest 的两条桥（generated_harness 交付物）。
- `canon/decisions/0006-*`（更新，proposed）— purpose：补 kernel-only 确认 + 1.2× 同口径 caveat + 比值归 validator。
- `canon/architecture/repo-adapter.md`（更新，proposed）— purpose：generated_harness 补 aclnn 桥引用。
- `canon/_verify.json` / minute — purpose：精度页指纹 + provenance。

**规矩**：新页一律 proposed/verified，tier 提升只走 `bureau:review`（用户）；`catlass acceptance mechanics` 上轮被手改+自盖 reviewed，需补进 review 队列复核。

---

深挖 cannbot `catlass-op-generator` / `ops-direct-invoke`，把结论折进设计。**本轮改动的文档及各自 purpose：**

- `doc/oprunway-cannbot-catlass-reuse.md`（新建）— purpose：M1 实现 catlass Task 2 的「可复用资产」参照（generated_harness 调用壳配方、CMake 注入、`verify_cmake_config.py`、精度诊断分类法、编排范式），并标明哪些不能照搬。
- `doc/oprunway-design.md` §4/§5/§7（更新）— purpose：验收性能默认工具 msTuner→**msprof op**；§5 仓适配器补 generated_harness 现成配方。
- `canon/architecture/catlass-acceptance-mechanics.md`（canonical，精化，reviewed 07-01）— purpose：明确验收性能用 **msprof op**（profile 交付 kernel），msTuner 归为「调优工具、不用于验收」；口径仍 kernel-only。
- `canon/decisions/0006-performance-timing-scope.md`（proposed）— purpose：补 NPU 侧默认采集工具 = msprof op。
- `canon/architecture/repo-adapter.md`（proposed）— purpose：给 catlass `generated_harness` 补现成配方（借自 catlass-op-generator），点明「包住 PR 现成 kernel」vs cannbot「从 DESIGN 现写」的差异。
- `canon/logbook/2026/06/f0c36755….md`（追加 checkpoint）— purpose：记录本次深挖 + 折入决策的 provenance（cannbot 覆盖面、msprof op、generated_harness 配方、我们设计被验证更强）。

**要点**：只有 catlass/tilelang 有 cannbot 专属编排，其余 8 仓 greenfield；catlass-op-generator 本质是 generated_harness 生成器，给了现成执行骨架；我们的「JSON 证据 + validator + 人工 CP」比 cannbot「文本 summary + LLM 判定」强，不退回。

## 2026-06-30

0. 核实编排选型：claude-code-guide 查官方文档确认 Workflow 工具 ① 不能随 plugin 分发、② 要 opt-in 不能假定人人有、③ 不支持中途人工 CP（No mid-run user input）。结论不翻：成品走「skill/command 入口 + 子 agent fan-out + AskUserQuestion 卡 CP + validator 判定」混合架构，Workflow 工具仅作内部并行加速器（可用则用、否则降级）。更新 ADR 0004（理由换官方实锤）+ design §9。
1. 让 Codex(gpt-5.5) 评审了设计（只读），存全文 `doc/oprunway-codex-review.md`：方向认可，但点破最大隐患「验收口径未契约化 → 自动化外壳」。
2. 采纳 Codex 收敛，设计升 v3：**契约先行**（一条 case_id 串 任务书→PR→NPU→GPU→判定）；§3 schema 补 case_origin/spec_clause_ref/pr_change_ref/oracle_source/tolerance_policy_id/timing_policy_id；oracle 分层枚举。
3. 解开三个 parked 问题：**精度三层**（任务书>平台标准>catlass 内置 smoke，出三 pass 只看 acceptance，Q3 定）；**性能 timing_scope 必填 + 默认 kernel-only**（Q4 定）；**catlass 三模式 + 仓适配器接口**（Q2 定）。
4. 新增：**acc-common**（统一 schema+validator）共享组件、`acc-npu-run` 拆「适配器/判定器」、**判定归确定性 validator**（agent 不能宣告通过）、**Task3 状态机**（BLOCKED/FAILED/PASSED_WITH_RISK…）。
5. ADR 0002 重构（catlass = 首仓执行后端，非总规范；ops-test 保留为 smoke gate）；ADR 0003 补「接口稳定前不 external-sync」。
6. 路线图重排：M1 = 定契约 + 用真实任务书/PR 打穿（最优先）。

## 2026-06-29

1. init 工作区：定位为「NPU 算子验收」，确立三段式流水线（用例生成 ST → NPU 跑测 → NPU↔GPU 性能对比报告）。
2. 落地三份文档：`CLAUDE.md`、`doc/oprunway-design.md`、本简表。定先 catlass 打底、跑通再泛化；记下 11 个 gitcode 仓。
3. clone catlass 到 `repos/catlass` 并调研：`scripts/build.sh <example> [-DCATLASS_ARCH=3510]`；golden=CPU float32（`examples/common/golden/` 可复用）；性能用 msTuner 出 `task_duration(us)`（kernel-only，不含 H2D/D2H）。
4. 用户定调：**精度/性能验收不基于姊妹项目 ops-test 的「跑没跑崩」判定**，以 catlass 自身机制 + 任务书为准。
5. 调研 `cannbot-skills`（已 clone 到 `repos/`）：借鉴其精度标准分类（ops-precision-standard）、性能指标体系（ops-profiling）、验收纪律（CP 门禁+JSON 证据+开发≠评测）。
6. 调研 `awesome-ascend-skills`：只收 skills，有 external 自动同步机制；cannbot 就是「自维护仓 + external 同步」→ 定 OpRunway 走同款发布形态。
7. 把全部相关仓 clone 进 `repos/`（共 12 个、~604M）：catlass + cannbot + 其余 10 个算子仓（asc-devkit/ops-sparse/ops-blas/ops-cv/catccos/shmem/oam-tools/amct/hixl/cann-recipes-infer，均 `--depth 1`）。
8. 组件仍不建：等真实任务书 + catlass PR、敲定数据契约/口径后再实施。
