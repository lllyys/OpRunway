# Roll / Bernoulli / Remainder 统一实施计划

**日期**：2026-08-07
**状态**：已实施并完成 N0–N9 机械验收；N10 `PARTIALLY_VERIFIED`（2026-08-07 收口）
**取代关系**：本文取代 `oprunway-bernoulli-remainder-plan.md` 作为后续实施顺序的当前入口；旧文保留为历史裁定与 gap provenance，不删除、不改写。

**用例来源裁定**：三个算子的正式 caseset 与 golden 均由 OpRunway 根据任务书与被测事实自行生成。任务书附带 case/golden 和源码自测只作 coverage/reference evidence，不作为 caseset component，不决定 `case_target`，不消费其执行结果形成裁决。

**实施状态入口**：逐阶段机读状态、三算子单卡裁决、多卡 v4 等价复验、N0 三静态门与 N2 生成
C++/receipt 重投影见 `dev-doc/oprunway-roll-bernoulli-remainder-validation-2026-08-07.md` 及 ignored R/G/E
总账。N0–N9 的机械完成判据已闭合；N10 未宣称完成：Roll exact 在线 PR intake 已闭合，Bernoulli 缺 exact
在线身份，Remainder MR 4249 的 private fork head 无法形成 complete intake。三算子本地来源正式验收均已实测，
未完成的双来源部分继续以结构化 gap 保留。

## 0 · 目标与边界

目标是在同一套字段驱动、可机校的 workflow 中支持三份任务书与两种被测来源（在线 PR、本地 checkout），并最终分别得到可复核的精度证据、NPU msprof 性能证据和确定性裁决。具体算子只作见证，通用代码不得按算子名分支。

本计划不改变已有裁定：验收维度只有精度/性能；不做 GPU 性能比较；资源类不构成第三维；正式裁决只准 `cpp_extension`；任务书是权威，PR/op_def 是被测事实；本地不做 compute，测试和跑测均在 NPU 环境。

## 1 · 旧计划状态校正

| 旧批次 | 新状态 | 新计划处置 |
|---|---|---|
| P0-a/P0-b/P0-c | 只读核验已完成 | 不重做；证据继续引用 |
| B16 ND | 未实施，且 Roll 证明 standard 路径必须支持 | 纳入 N2，三算子共用 |
| B17 CANN 版本 | 未实施 | 纳入 N3 |
| C2→B10 身份隔离 | 原计划未完成；Roll 又暴露调用可信问题 | 与 output-written 一起前移到 N1/N4 |
| A0→A2 覆盖门 | 当前分支已有部分覆盖账本和 staged spec 硬化，不能按旧文从零重建 | N5 先做差量审计，仅补缺失字段/终态 |
| B11→B12 taskdoc dtype | 已有 taskdoc caseset 与 staged dtype authority 的基础，但未用三算子证明闭环 | N5 差量补齐并三算子见证 |
| B3/B2 多输入、广播、host scalar | 仍是 Remainder/Bernoulli 专属硬依赖 | 保留为 N6 |
| B4 dtype 分类 | complex64/uint32 transport 已补，四类失败语义仍需复核 | 纳入 N5，不重复建 dtype 表 |
| B5/B14 rank | rank 0 仍未完整关闭，Roll/Bernoulli共同需要 | 纳入 N7 |
| B6 非连续 | 未完整关闭，Roll/Bernoulli共同需要 | 纳入 N7 |
| B7/B9/B13 RNG oracle | Bernoulli 专属，仍有效 | 保留为 N8 |
| B8 aclnn_builtin baseline | 被现行 AGENTS §5.10 的全局 measure-only 口径取代；旧裁定仅作历史，不再实施 ratio baseline | N9 对三算子统一只做 msprof 绝对耗时；未比较条款进 gap |
| output-written gate | 旧计划遗漏；历史 Roll 已证为裁决可信性风险 | 新增 N1，成为所有正式见证的最早前置 |
| spec change gate / source provenance | 合并后已存在 | 作为每轮固定门使用，不重建 |

## 2 · 新批次

### N0 · 基线冻结与差量清单

- 固定当前 HEAD、旧两算子 gap/plan、Roll gap 文档和三份任务书摘要。
- 为每个算子列出 `R`（任务书必测）、`G`（生成规则可产）、`E`（runner 可执行）及来源冲突；正式 spec 显式写 `runner_form=cpp_extension`、来源形态、target-dir、由矩阵账本推导的 `case_target`、format、最低 CANN 版本和性能口径。
- 在线 URL 与本地 checkout 使用同一 spec 语义；只允许 provenance 字段不同。

**完成判据**：三个 spec 草案均通过 `validate_taskdoc_input.py`、`gen_cases.py <spec> --dry-run` 和 `spec_change_gate.py --check` 的对应静态门（不 build、不跑真机），且每个未决字段有受控 gap 记录，不生成裁决。

### N1 · cpp_extension 输出写入可信门（新 P0）

- 按 `oprunway-output-written-gate-handoff.md` 实现全 dtype 哨兵、`output_not_written`、bool/empty 显式 skip 和诊断元数据。
- 保持 validator 边界：未写入是 harness/执行错误，不是精度 mismatch。
- 每次修改后执行一次 `codex audit fix`；本批结束再由 Claude 同 session verify。

**完成判据**：以当前测试夹具构造确定性的 no-write uint8 标量，必须落 `output_not_written`；删除哨兵检查的 mutation 必红；正常写入正例仍 PASS；legacy caseset payload 不变。历史 Roll 假 PASS 只作问题 provenance，不作为完成所依赖的可复现输入。

### N2 · standard/extended 共用显式 tensor format

- `aclnn_tensor_format` 继续字段驱动；为 standard stage-2 提供与 extended 等价的显式 ND tensor conversion，不改变标准四参 ABI。
- Bernoulli、Remainder、Roll 分别由 header/preflight 决定 stage2_form，禁止手填迎合实现。

**完成判据**：三算子 ND fixture 的 manifest/生成 C++/receipt 均写明 `ACL_FORMAT_ND`；词表外值或不可达路径在生成期失败；默认旧 fixture 去除 producer 后字节不漂移。

### N3 · CANN 最低版本语义门

- spec 承载任务书最低版本；driver 记录原始版本与规范化结果；三级门比较 required vs measured。
- 无要求时显式记 `not_declared`；有要求时 unknown、不可解析、低版本全部 BLOCKED。

**完成判据**：8.4.9/8.5.0/8.5.1、带 suffix、unknown/垃圾字符串均有确定性正负 fixture；门中无算子名和固定 8.5.0。

### N4 · DUT 身份、构建与调用链闭环

- 完成旧 C2→B10：build receipt ↔ source facts、DUT/标杆 ELF 隔离、workspace/stage2 双符号定义者。
- Roll 额外核验 `aclnn_exclude` 下实际 vendor build 入口、产物 ELF 与符号；N1 命中后先做最小 root-cause，不直接归因 DUT。

**完成判据**：来源锚、tree digest、ELF 指纹、双符号定义者和实际加载对象逐字一致；污染、同源、缺符号、输出未写均在 precision/perf 前阻断。正式 build/acceptance receipt 必须消费 N2 已闭合的生成物；N2 未闭合时只准做标记为 development 的 root-cause build。

### N5 · 权威覆盖、自生成 caseset 与 dtype 分类差量补齐

- 不重建已有覆盖账本；先审计 A0–A2/B11–B12/B4 的现状，只补三算子实际暴露的断链。
- 为 Roll 新增 op-中立 `cyclic_shift/circular_index_remap` rule/profile；Bernoulli/Remainder 继续按任务书原语组合生成。所有输入和 golden 由 workflow 生成，附带材料仅用于提取有引用的 requirement/impact 场景。
- generated caseset 逐 case 对账 input/output dtype、shape、attrs compose；建立 requirement→case IDs mandatory ledger；`R ⊆ G∩E` 且每项要求有 case 或受控 gap，才能开始正式取证。
- Roll 正文 8 dtype与自测/op_def 的 bool、既有 conversion 实现的 int16/int64 分层记账；先解决“保持原类型”全集，不能由新 op_def 反推。对账结果必须分别写 `main_table_required` 与 `preservation_required/regression_extension`，逐 dtype 给来源和最终成员身份，禁止留给实施阶段临时选择。任务书随附 README 声称但实际缺失的 `case.json`、固定 golden 绝对路径只作参考材料 gap，不进入正式 intake。Bernoulli/Remainder 沿用旧文的 dtype 与目录/硬件冲突裁定。
- dtype 失败必须区分 API 不支持、harness 不支持、证据不完整、已确认 DUT 不支持。

**完成判据**：R/G/E 和每项来源进入机器账本；generated case 首/中/末 output dtype 漂移均失败；DEFERRED 不得变成 pass_with_gaps；改变附带 JSON 不改变正式 caseset，改变任务书要求或生成 profile 必须改变 spec/caseset 摘要并触发 spec-change 门。

### N6 · 多输入、host scalar、广播与 promote（Remainder/Bernoulli）

- 先贯通每输入独立 kind/dtype/shape、`aclScalar*`、seed/offset，再做广播和 promote。
- Remainder 按任务书 PyTorch 语义生成 dtype promote；Bernoulli 的 host scalar/RNG attrs 复用同一契约。

**完成判据**：调用计划、C++ schema、receipt 和 evidence 对同一参数身份逐字一致；广播覆盖账本非零；缺绑定/非法 promote 在执行前失败。

### N7 · rank 0、空 int_array 与非连续布局

- 数组类型由 spec/taskdoc compose 声明，而不是以“默认值非空”猜测；允许 `dims=[]`。
- shifts/dims 等关联属性以 atomic row/constraint group 生成，禁止独立笛卡尔制造非法配对；`dims=[]` 的单 shift 例外须保留来源解析记录。
- 真 rank 0 使用 `shape=()`；布局契约表达 stride/storage_offset/base storage，输入输出分账。
- Roll 覆盖 rank 0–8、空 dims、x 非连续；Bernoulli 覆盖任务书明写的 rank 0 与非连续；Remainder 不扩任务书未写布局要求。

**完成判据**：空 int_array 全链可达；`()` 被替换为 `(1,)` 必失败；非连续被静默 contiguous 必失败；每项均有 coverage ledger。

### N8 · Bernoulli 随机 oracle 前提

- 延续旧 B7→B9→B13：同机 NPU `torch_npu.Tensor.bernoulli_`、同 seed/offset、`prob∈(0,1)` exact 前提收据。
- 前提收据 `usable_for_verdict=false`；不一致只进入 root-cause，不能直接判 DUT fail。

**完成判据**：前提通过后才允许正式 Bernoulli 精度证据；字段缺失、设备不同或 RNG 消耗不一致均 BLOCKED。

### N9 · 性能证据

- Bernoulli/Remainder：按改动类别走 `measure_only/change_class_no_perf_comparison`，只记录同机 NPU msprof 绝对耗时；旧文的 `aclnn_builtin` ratio baseline 不再实施，任务书中未比较的性能条款按规则进入 gap。
- Roll：任务书无数值性能门，走 `measure_only/no_perf_requirement`；complex64 与旧 dtype 分档做 msprof，特别注意事项中的“不影响原 dtype 性能”在本轮按“未验收”进入 `task_pr_gaps`，不设等待条件，不能包装成达标。
- 所有数据均绑定 case、DUT provenance、SoC、CANN、kernel-only timing scope。

**完成判据**：缺 identity、profiler 采样或 timing scope 时无性能结论；Roll 不生成 ratio PASS；资源类不进入裁决。

### N10 · 三算子正式见证与双来源复核

执行顺序固定为最小可信见证 → per-op 全 caseset → 性能：

1. Roll：先历史假 PASS/int_array/ND 最小集，再跑自生成的完整矩阵、PR-impact supplement 和 msprof。
2. Remainder：按 `math/floor_mod` 取材，保留任务书目录冲突；在 A2/A3 真实构建，多输入/广播/promote 后跑全量。
3. Bernoulli：RNG 前提通过后跑正式精度，再做 msprof 绝对耗时采集（measure_only，不做 builtin ratio）。
4. 每个算子至少验证一次在线来源和一次本地来源的 intake/provenance；若代码字节不是同一来源，不要求 verdict 相同，只要求门语义相同。

目标环境有多张 NPU 时，正式 caseset 允许按 case identity 做确定性多卡分片：

- 每张卡使用显式 device id、独立进程、独立执行目录和独立 shard evidence；build/source/spec 工件只读共享，任何运行期输出不得共享写目录。多张同型号卡只能提高同一硬件目标的吞吐，不能替代 A2/A3/A5 三种产品覆盖。
- 分片前先在每张参与卡运行同一组最小 smoke，核对 SoC、CANN、DUT ELF、双符号定义者与输出写入门；任一卡环境身份不同或 smoke 异常时，隔离该卡并保留诊断，不把它混入汇总。
- shard manifest 记录 `shard_id/device_id/case_ids` 和 caseset/spec/provenance 摘要；case_ids 必须互斥且并集精确等于目标 caseset，缺失、重复、越界都 fail-closed。
- 精度与 msprof 可以跨卡并行，但同一个性能 case 的采样不得跨卡拼成一组统计；性能分档须保留 device identity，避免把卡间差异伪装成样本波动。
- 最终只由确定性汇总器按 case id 合并 shard 工件；agent 不手算总数或重判 pass/fail。

**完成判据**：单卡路径与多卡分片路径对同一固定 caseset 产生相同的 case 集合；Roll/Remainder 的逐 case 判定须一致。Bernoulli 只在 N8 前提成立且 execution identity/seed/offset 全同的受控复验中检查一致性，正式多卡分片仍逐卡保留判定与设备身份，不跨卡强求或投票改判。分片无重无漏且每份证据绑定实际 device。只有确定性脚本可写 verdict；gate passed 只表示证据完整；FAIL/BLOCKED/needs_review 原样报告。三算子全部完成后才形成统一验收总结。

## 3 · 依赖 DAG

```text
N0
 ├─ N1(output written) ─┐
 ├─ N2(ND) ────────────┼─ N4(identity/build) ─ N5(authority/generated/dtype) ─┬─ N6(multi-input) ─ Remainder
 └─ N3(CANN version) ──┘                                                   ├─ N7(rank0/empty attrs/layout) ─ Roll
                                                                           └─ N7 ─ N8(RNG) ─ Bernoulli

N4 + N5 + per-op functional prerequisites ─ N9(perf) ─ N10(full witnesses)
```

硬顺序：N1–N4 未闭合前不得做任何正式精度见证；N5 未闭合前不得做精度或性能采集、不得宣称任务书覆盖；N8 未闭合前不得做 Bernoulli 正式精度；N9 不得早于 DUT 身份闭环和正式 caseset 闭合。

## 4 · 迭代、审修与提交纪律

1. 每个批次采用“小改动 → NPU 环境定向测试 → NPU 环境相关回归 → `codex audit fix` 一次 → 修复/复验”的单循环；不把多个未验证结构改动堆成一轮。
2. 一个批次若修改 spec，先按 `spec_change_gate --update` 生成/验证收据；入口、出口两门都必须通过。
3. Claude audit-fix 用于批次或文档的独立复核；verify 必须复用同一 Claude session，最多三轮，剩余 finding 如实挂账。
4. commit 只在批次机械完成判据全部满足时做；不 push、不 merge，除非用户另行明示。
5. 每批更新 `oprunway-changes-brief.md`；实测数字只抄真实日志/工件。
6. 多卡并行只优化互相独立的执行阶段；build、spec/source binding、汇总裁决仍按依赖串行过门。首次启用多卡前先做单卡/多卡等价性回归，不能直接把并发当作可信加速。

## 5 · 停止条件

只有同时满足下列条件才停止实施循环：

- 在线任务书 URL 与本地任务书路径均可进入同一 intake；在线 PR 与本地 checkout 均可进入同一来源契约；调用方给什么就按什么取材，不推测来源。
- N1–N9 的机械完成判据全部通过，且对应 mutation/负例能证明门真实存在。
- Roll、Bernoulli、Remainder 各自完成 N10 的精度与性能实测；没有把未执行项包装为 PASS。
- 最终 deterministic acceptance/verdict、报告和 provenance 能从落盘工件独立复核。

若真机事实暴露新 gap，回到最早受影响批次更新计划和门，再继续循环；不得绕过门追求“先跑绿”。
