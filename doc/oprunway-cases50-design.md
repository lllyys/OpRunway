# 精度用例按 opbase §1 生成 + 阈值走 ascendoptest + 精度门前置 fail-fast + 性能同输入 —— 实现蓝图

> 已定需求（用户 2026-07-15）。权威源：`cann/opbase` `docs/zh/ops_precision_standard/experimental_standard.md`
> （pin `f69d4e4e3f2626ddd37855a8d05063a1764ac4c9`）。本蓝图给实现，供对抗评审「先审地基」。

## 决策更新 v2（2026-07-15 · 评审 REVISE 后）

评审（ultracode 4 lens + 汇总，run `wf_bc5287c2`）判 **REVISE**，抓 12 条必修。用户就 4 个改交付物的岔口定：
1. **空 Tensor → 现在就实现支持**：改 validator（numel=0→na 非 fail）+ adapter（numel=0 优雅处理、空入空出不 raise 不跑 kernel、排除 perf 对齐）+ runner 空契约；空 Tensor 只挂「功能」维（无精度可判/无 kernel 可 profile）。
2. **覆盖强度 → 白名单强制必覆盖组合 + 其余采样**：关键联合（每重点 dtype × {NaN, 大 shape, 每 attr 取值}）另立白名单强制纳入，其余 1-wise 采样；caseset 导出实际达成覆盖强度 + 被丢组合类清单（可审计）。承认 50 封顶下 §1.1「100% 正交联合」不可达。
3. **dtype 策略 → fp16/fp32/bf16 重点覆盖；其他 dtype（算子若支持）每种 1-2 条主流场景**。**bf16 本轮扩 runner 代码**（用户 2026-07-15 定）：runner.cpp 加 bf16 分支 + `repo_adapter._NP` 加 bf16。→ 本轮**代码可跑集**（生成/收发/mock）= {fp16, fp32, bf16}；⚠ **真机已验收集**仍 = {fp16, fp32}——**bf16 真机验收阻塞在 op-build 环境**（见文末实现进度），isclose spec 里 bf16 现 deferred。int32/int16 等仍 Track C、未接则 deferred。
4. **perf → 判全部 + 退化 case 标 trivial-met**：perf 测/比全部相同输入；numel≤阈值退化 case 标 trivial-met（不失败/不崩/报告标注），trivial-met 做成贯穿 perf_compare + gate_task3 + run_workflow + GPU 对齐的一等公民。

**我直接在蓝图修的实现层必修（不改交付物，评审 must-fix #3/#4/#5/#7/#8/#10/#11/#12）**：
- 精度全过门**不越权重判**——改「仅 overall==PASS 时的跨产物一致性断言（PASS 则 counts.fail==0 且 uncertain==0）」，fail/needs_review/passed_with_risk 一律放行交 run_workflow overall 表达。
- fail-fast **不 early-return**——先跑 gate_task1/task2 + runner_source 分支，仅当两支不触发且 prec∉{pass,passed_with_risk} 才跳 Task3 计算、合成 skipped perf summary，复用主路 overall/state/exit。
- **per-case 独立种子** `seed=hash(case_id)⊕SEED`——数据只依赖稳定 id，与选择/顺序/target 全解耦（补「同 id 不同 target 字节一致」回归）。
- **id_kind 独立命名空间**（empty/scalar/bnd_lo/bnd_hi/inf/ninf/nan/grid…）——特殊值编进 id_kind 不靠 shape；`_shape_tag(())→scalar0d`。
- §1.4 均一输出/非对齐 NaN **豁免**现有「必混 True/False」(L388) + `_assert_equal_nan_effective`——作用域收窄到常规 pair 构造；NaN 场景走 equal_nan 语义比较（对齐位判等）或不纳入 exact_mismatch。
- **format 轴**：elementwise 仅 ND → 蓝图显式声明退化 + 依据（op_def/example），不默默省。
- **attr 作真正交轴**：attr_matrix 仅供每 attr 取值集、算法笛卡尔展开（不再当最终 case 列表坍缩到单代表点）。
- **case_target 校验**：int 且 ≥ max(1, S)（S=特殊场景强制下限）；0/负/非整 fail-fast（堵零用例空跑冒充验收）；acc-spec 问用户前 dry-run 拿 [S, pool_max] 区间呈现。
- **正交网格 CAP** 防组合爆炸；caseset 导出 `pool_max/requested_target/emitted/dropped_combo_classes`。
- **数量门软化**：pool<target → PASS + note（不硬 BLOCKED），忠 §1.1「不设下限」；仅「精度 case 数==0」或重点 dtype 未覆盖 → BLOCKED。
- **确定性 normal(μ,σ) 构造器**（现 gen_cases 只有 uniform）；50/50 值域仅约束基准输入、比较类第二输入由 pair 主导（明记）。

> ✅ **Pending（设计决策）已收口**：bf16 本轮扩 runner 代码（用户定）。设计拍定、进入实现。⚠ 注：bf16 **真机验收本身未收口**——见文末实现进度（op-build 环境阻塞、kernel 支持未证实）。

## 实现分层执行顺序

- **Layer A · gen_cases §1 生成核心**（Python）：dtype 分层（key fp16/fp32/bf16 重点 + 其他 1-2）× shape 阶梯(2ᵏ/2ᵏ−1,1~8维,≤2³¹,CAP) × 值域(uniform+normal 确定性) × attr 正交笛卡尔；白名单必覆盖组合 + 1-wise 采样 + 预算封顶；§1.4 特殊场景(空→功能only / 标量[1] / 边界 / inf·nan) + id_kind 命名空间；per-case 独立种子；caseset 导出 pool_max/requested/emitted/dropped + coverage_strength；perf tag（非空皆带性能、退化下游 trivial-met）。
- **Layer B · 流水线判定**（Python）：run_workflow fail-fast(不 early-return) + validate 精度一致性断言(非重判)+软数量门 + perf_compare trivial-met + validator numel=0→na。
- **Layer C · adapter + runner**（Python + C++ · a3）：repo_adapter numel=0 优雅 + _NP 加 bf16 + msprof 规模化；runner.cpp bf16 分支 + 空契约。
- **Layer D · acc-spec agent**：case_target AskUserQuestion + dry-run 拿 [S,pool_max] 呈现。
- 贯穿：测试 + codex 门 + a3 真 torch/真机验 + bureau capture。

## 实现进度（2026-07-15，随做随记）

**✅ Layer A（gen_cases §1 生成核心）—— 完成 + a3 真 torch 验证**
- fork 落地、我 review 通过：白名单防关键联合被丢、per-case 种子（数据解耦 target）、id_kind 独立命名空间、
  空 case `compare=na`、断言对 inf/nan 收窄、导出账本（pool_max/emitted/dropped/coverage_strength）。
- dry-run（本机无 torch）：isclose target=50 → emitted=50、确定性、种子解耦 target。

**✅ Layer B（流水线判定）—— 完成 + a3 真 torch mock e2e 全绿**
- `precision_policy.compute_metrics` EXACT 分支加 NaN 对齐相等（bf16/int Neg 的 `neg(NaN)=NaN` 不再假 fail）。
- `validator`：空 Tensor(`compare=na`)短路判 na + 防伪造 na（校真空 Tensor）；`_dims_contract` 加 `allow_na`。
- `perf_compare`：trivial-met（退化 case numel<阈值 达标免测，perf 达标由大 shape 主导）；`_case_numel` helper。
- `run_workflow`：精度门前置 + fail-fast（**不 early-return**，精度非全过→跳 Task3、不加 task3 门、复用主 overall）。
- `validate_acceptance_state`：三门加 na/trivial 豁免 + 防伪造复核（gate_task1/2 校真空、gate_task3 据 caseset numel 核 trivial）。
- **a3 验证（真 torch 2.13.0+cpu）**：① isclose fp32/fp16 mock e2e → 50 用例、精度 pass、perf 42/42、**三门 PASSED、总体 PASS、exit 0**；
  ② 注入 1 精度缺陷 → **精度 fail → 跳过性能 → FAIL(精度)、exit 1**（fail-fast 正确）；
  ③ isclose fp32/fp16/**bf16** mock e2e → 50 用例三 dtype 均衡(17/17/16)、bf16 codec+torch golden 通、总体 PASS。

**⏳ 剩余**
- **Layer C 真机**（代码 + a3 真 NPU）：`repo_adapter._NP` 加 bf16 + `new_example` 空 Tensor 别 raise（runner 已处理 numel=0）+ 空 case 真机 evidence；`runner.cpp × 3` 加 bf16 dispatch（`ACL_BF16` + uint16 storage，一行/文件）。
  **✅ Layer C 代码完成 + 真机 bf16 验收通过（2026-07-16）**：`repo_adapter._NP` 加 bf16 + `new_example` 空 Tensor 放行 + 数值 storage-aware readback（bf16 uint16→fp32）+ 修 evidence 段 per-case dtn；`runner.cpp × 3` 加 `ACL_BF16` dispatch + **isclose runner 补第二道 manifest 解析处 dtype 关卡**（line 152，原只补了 RunCase dispatch、bf16 被解析处先拦）。
  真机跑抓修的 bug：① `new_example` 对 `x{j}.npy`（gen_cases 已存**物理** storage）又调 `materialize_input`（期望逻辑 fp32）= 二次 encode，bf16 一开就撞 → 改**直写物理字节**；② isclose runner 第二道 dtype 关卡漏补 bf16。
  **真机 blocker 已解（非环境坏、是脚本 bug）**：`build.sh --ops=isclose` 失败根因＝run_on_npu.sh 每次 fresh 都重建 op（op 名/experimental 路径对 isclose 不适用——isclose 在 `math/is_close/` 非 experimental）且会 `rm -rf $OPP` 毁掉现成 opp。**修 run_on_npu.sh：用户态 opp（route B）已建则复用、跳 op 重建、只建 runner_exe**。
  **真机解封 + bf16 实测（a3 真 NPU，复用 opp）**：完整 3-dtype 50 用例 **Task2 pass、50/50、0 fail**（fp32/fp16 回归+bf16 精度全过 vs torch golden）、**三门全 PASSED**、perf 46/47（1 真实略慢）、总体 NEEDS_REVIEW（插件样例 runner→挂人核）。
  ⚠ **provenance 洞（codex 门坐实）**：这次跑**复用了 prior 建的 opp、未从当前 op 源 provenance-clean 重建**（run_on_npu.sh OPHASH 用 `experimental/math/$OP`、对 isclose 路径不存在=恒定空 hash、未绑源；复用只查目录在否）→ 无法自动排除 stale opp 假通过、复用为 **dev-grade**。故按「证据 provenance 绑定、防 stale 假通过」标准，**bf16 实测虽全过、仍留 deferred、不转 tested**。**follow-up**：正确源路径算 OPHASH + opp 独立 provenance stamp + 缺则 fail-closed + 从当前源重建、再转 tested。int32 仍 Track C。
- **✅ Layer D 已落地**：acc-spec 三入口（SKILL/taskdoc/schema）补 `case_target` 交互（AskUserQuestion 问用户、默认 50）；gen_cases `--dry-run` 补 **`forced_total`(=强制下限 S)** + [S,pool_max] 区间行，使区间可真取（原只打 forced_special、非真 S，codex 散文门坐实并修）。
- **✅ 测试重整已完成**：fork 重整 + 我修 codex 4 项后 **274 单测全绿**（a3 真 torch）。**✅ codex 门**（源码 + 散文各一轮）全修。
- **剩余（非阻塞）**：其余 spec（sign/equal/neg）可补 `case_target`（现走默认 50）；真机验收（op-build 阻塞，见上）；GPU 标杆真数据（外部阻塞）。
- **当前未 commit（本会话末步）**；本机 py3.14 无 torch → torch 相关测试在 a3 跑。

## 0. 决策回顾（原始蓝图 —— ⚠ **部分被 v2 + 评审覆盖，以 v2/实现进度为准**）

> ⚠ 本节是**动手前的原始蓝图**，多处已被上方「决策更新 v2」与评审改写。**尤其**：下文数量门「pool<target → BLOCKED」「不 clone 凑数、数量门 BLOCKED 报用户」等**已改为 v2「软化 PASS+note、仅零用例/重点 dtype 缺失才 BLOCKED」**；attr/覆盖/空Tensor 等亦以 v2 为准。读时以 v2 + 文末实现进度为现行。

| 项 | 定稿 |
|---|---|
| 生成规则 | 采纳 opbase **§1**（§0：仅浮点计算类；整型/搬运类另定 → 遇到先停确认） |
| 数量 | **以用户为准**：`spec.precision.case_target`，默认 50，acc-spec agent 运行时问用户（覆盖 §1.1「不设下限」） |
| 阈值 | **§2 不用**，走 ascendoptest（现有 `precision_policy` 快照，**零改动**） |
| golden | torch(CPU) 固定后端（已建） |
| 跑测 | 单一用例集、NPU 跑一次，每条同出精度 + 性能(kernel-only us) |
| 精度门 | 前置 + fail-fast（**跑完再判**）：任一挂 → FAILED_PRECISION、跳过 Task3、提前结束、非零退出 |
| 性能 | 在**全部相同输入**上判（用户明示） |

## 1. §1 覆盖-预算 生成算法（gen_cases._plan 重写）

**目标**：产 `case_target` 条用例，覆盖优先、确定性（SEED，无随机选择）、每条有 torch golden。

**轴（§1.1/§1.2/§1.3）**：
- `D` = spec 被测 dtype 集（`self_param.dtype`，应为**可跑集**；deferred 的 bf16/int32 不在此、归 `dtype_deferred`，不入必过集）。
- `SH` = shape 阶梯（§1.2）：维度 1~8；每维值 ∈ {2ᵏ, 2ᵏ−1}（k 使值落 [1, 2²⁰]）；总元素 ≤ 2³¹。确定性有序表，覆盖 1D/2D/高维、含 2ᵏ 与 2ᵏ−1 两味。
- `V` = 值域（§1.2）：50% 均匀 [-5,5] + 50% 正态(μ∈[-5,5], σ∈[0.1,2])；叠加算子专属 pair 构造（比较类 near/far，复用现有 `_make_*`）。
- `A` = attr 组合（§1.3）：有 `attr_matrix` 用之；否则标量走等价类代表值、布尔 T/F、枚举全值（IsClose：`equal_nan` T/F + rtol/atol 代表类）。

**特殊场景（§1.4，强制、不与常规正交）**：每 dtype 覆盖 —— 空 Tensor（某维=0）、标量 Tensor `[1]`、边界（下：全维=1；上：某维取最大）、INF/-INF/NAN 遍历（每 dtype 每种值不同 shape）。

**预算分配（确定性）**：
1. 先产**全部特殊场景**用例（§1.4，强制覆盖），计 S 条。
2. 产常规正交网格 `D × SH × V × A`（确定性序），计 R 条。池 = S + R。
3. `if case_target ≥ 池`：全出（**不 clone 凑数**，忠于 §1.1；数量门会因 < target 而 BLOCKED，如实报给用户）。
4. `if case_target < 池`：**分层确定性采样**到 target —— 必含全部特殊场景；常规网格按「每 dtype × 每 shape 类 × 每值域 × 每 attr 至少一条」轮转取，稳定序（sorted key + 索引，无 rng 选择）。

**tag**：每条 `dims = ["功能","精度","性能"]`（validator 判精度 + perf_compare 测性能，同输入）。
**确定性**：值填充用 SEED rng；**选择不带 rng**。

## 2. spec schema

- 加 `spec.precision.case_target`（int，默认 50）。旧 spec 无此字段 → 默认 50（向后兼容）。
- 样例 specs（isclose/sign/equal/neg）补 `precision.case_target: 50`。
- `self_param.dtype` 保持「可跑集」；deferred 走 `dtype_deferred`（已有 Q7 语义）。

## 3. run_workflow：精度全过闸前置 + fail-fast

在 Task2（validator 出 verdict）后、Task3 前插入：

```
verdict = validator.validate(...)
prec = verdict["overall"]["verdict"]
if prec != "pass" and prec != "passed_with_risk":   # 任一精度挂/needs_review → 不进性能
    overall = "FAIL(精度)"  (或 needs_review 分支)
    # 跳过 Task3、跳过 perf_compare
    落 acceptance.json（perf_status="skipped_precision_gate"、note「精度未全过、性能未跑」）
    return 非零退出
# 全过 → 照旧 Task3 perf_compare（在全部相同输入上）
```

- fail-fast 粒度 = 跑完再判（真机精度+性能已一次测完；此处「跳过」= 跳过 Task3 对比/报告）。
- 精度全过定义：所有精度 case verdict == pass（passed_with_risk 另议：任务书宽于平台底线时仍算过、走人工 CP——保留现有语义）。

## 4. validate_acceptance_state：加两道门（task2）

- **精度全过门**：evidence 里任一精度 case verdict ≠ pass（或 caseset↔evidence 精度不全 pass）→ FAILED。
- **精度数量门**：精度 case 数（真实 cases 里带「精度」tag 的）≥ `spec.precision.case_target` → 否则 BLOCKED（补「跑子集报全」数量维）。读同一 `case_target` 字段。

## 5. 波及文件（ripple）

| 文件 | 改动 |
|---|---|
| `gen_cases.py` | `_plan` 重写为 §1 覆盖-预算；每条 tag 三维；读 `case_target`；特殊场景；值域 50/50 |
| `repo_adapter.py` | `perf_ids` 自动变全集（每条带「性能」）→ **真机对 50 条都跑 msprof**（成本↑，用户接受）。核 remote 编排能扛 50 条 |
| `perf_compare.py` | `perf_ids` 自动全集；**须抗退化 case**（空 Tensor/标量 numel≤阈值 → perf 标 trivial、不 NaN、不误 fail） |
| `validator.py` | 每条带「精度」→ 判全部（现逻辑即可，无需改选择） |
| `run_workflow.py` | 插精度全过闸 + fail-fast + 跳 Task3 分支 |
| `validate_acceptance_state.py` | 加精度全过门 + 数量门 |
| `acc-spec` skill/agent | AskUserQuestion 问 case_target、写入 spec |
| 测试 | gen_cases 覆盖/预算/确定性、门两道、run_workflow fail-fast、perf 退化 case |

## 6. 已知风险点（评审重点）

1. **退化 case 的 perf**：空 Tensor/标量[1] 的 kernel us ≈ 0，ratio 可能 NaN/除零 → perf_compare 须显式抗（标 trivial-met、不 fail、不崩）。用户要「perf 判全部」，故不能简单剔除、要优雅纳入。
2. **预算 < 池 的分层采样**：要保证覆盖代表性（每 dtype/shape类/值域/attr 至少一条）且确定性；采样算法别引入 rng、别偏斜。
3. **池 < case_target**：不 clone 凑数（忠 §1.1），如实少出 + 数量门 BLOCKED 报用户。别为凑 50 灌水。
4. **真机 50 条 msprof 成本 + 编排**：`perfcases_list.txt` 现只写 perf_ids；变全集后 remote 编排/超时/拉回要能扛。
5. **精度全过闸与 passed_with_risk**：任务书宽于平台底线的 risk case 算不算「过」→ 保留现有「acceptance 过即放行、risk 走人工 CP」，不因新闸误挡。
6. **边界值域 × golden**：NaN/±0/Inf 输入喂 torch golden 要确定（正好补 Q9 codex#6 边界覆盖）；IsClose 的 equal_nan 与 NaN 段联动别产假覆盖（现有 `_assert_equal_nan_effective` 保留）。
7. **dtype 可跑集 vs dtype_required**：生成只在可跑集（fp32/fp16）铺 target；dtype_required 全集（含 deferred）仍归 Q7 覆盖门，别混。
