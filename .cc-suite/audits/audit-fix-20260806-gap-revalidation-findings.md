# Audit Findings — gap 清单对 96edacd 基线的重核

**Run**: audit-fix 20260806 gap-revalidation
**Scope**: `dev-doc/oprunway-bernoulli-remainder-gap-todo.md`（G1–G13 全量）
**基线**: 原文对 `739a691`，本次重核对 **`96edacd`**（+30 commit，含 PR #15）
**审法**: 逐条判据回到代码核验（非通用代码审计）
**Status values**: open | fixed | not-fixed | partial

## A · 原有 gap 的存废（逐条到代码核过）

| # | gap | 新基线上的状态 | 证据 | Status |
|---|---|---|---|---|
| A1 | G1 内存轴 | **仍完全成立** | `acc-common/` 无任何 memory 模块；`gpu_baseline_contract.json` 内存字段计数 = 0 | open |
| A2 | G2 随机判定 | **仍成立** | `acceptance_predicate.kind = [pointwise, equivalence_relation]`、`compare = [exact, isclose]`，词表一字未变 | open |
| A3 | G3 混合 kind/dtype 入参 | **仍成立** | 「常规构造路径下所有输入同形」仍在 `gen_cases.py:2091/2340/2363`；`gen_cases` 与 `cpp_extension_codegen` 均无 `host_scalar`/`aclScalar` | open |
| A4 | G4 float64 | **仍成立**，且**新增跨层张力**（见 B2） | `repo_adapter.py` 全文无 `float64` | open |
| A5 | G5 广播哨兵 | **仍成立**，**行号位移** | `gen_cases.py:1939`（原 1419），代码逐字未变 | open |
| A6 | G6 promote | **仍成立** | 同 A3，一 case 一 dtype 未变 | open |
| A7 | G7 非连续 | **仍成立** | `ascontiguousarray` 9 处；全文无 stride/storage_offset 的造例或落盘表达 | open |
| A8 | G8 rank 池 | **仍成立**，**行号位移** | `_REG_SHAPES`(`:2031`，原 1507) 最高 4 维；`_EXT_RANK_SHAPES`(`:2039`，原 1515) 到 5 维 | open |
| A9 | G9 任务书门缺资源类口径 | **仍成立** | 契约仍 18 项，id 列表逐字未变；全文无「内存/资源」相关条目 | open |
| A10 | G10 provenance | **仍成立** | `dut_source` 仍两条通路，`ANCHOR_FIELD` = `pr_head_sha` / `local_root_digest` | open |
| A11 | G11 前半（baseline 选型） | **仍成立** | `perf.baseline` 词表仍 `tbe\|gpu_external\|torch_npu\|aclnn_builtin` | open |
| A12 | **G11 后半（DUT 侧符号来源隔离）** | **已被 PR #15 做掉** | `cpp_extension_adapter.py:525`「符号来源包 ↔ vendor ELF 必须同源」；`cpp_extension_driver.py:200`；**且被 `validate_acceptance_state.py:1036-1051` 强制对账**——正是方案 C2 的完成判据 | **fixed** |
| A13 | G12 dtype 三源冲突 | **仍成立** | 判据来自任务书/header/op_def，与本仓代码无关 | open |
| A14 | G13 候选项 | **仍待核** | 未见针对 `offset%4`/`prob` 边界/属性绑定的定向覆盖 | open |

## B · 本次新发现（原清单没有）

| # | 发现 | 严重度 | 证据 | Status |
|---|---|---|---|---|
| B1 | **`self_test_case/` 口径冲突**：`taskdoc_caseset.py` docstring 明写它「是任务书的一部分，因此是**验收权威**（AGENTS.md 5.8）」，并已建接口映射 IR 接进主链；而文档 §0 按用户当时指示把它**排除**。两者相反、未 settle | **Critical** | `taskdoc_caseset.py:1-24` | open（交用户裁定，不自行消解） |
| B2 | **跨层 dtype 能力面不一致**：`taskdoc_caseset.CANONICAL_DTYPES` **含 `float64`/`double`**，而 `repo_adapter.SUPPORTED_NP_BY_FORM` 三个 form 都没有 → intake 层认得、执行层跑不了。**在哪一层 fail-closed 未核** | High | 两处词表对比 | open |
| B3 | **G1 的「有无承接终态」已有先例**：`blocked_golden_unavailable`（`run_workflow.py:1019`，含单测）——方案 A1-0 的核验范围因此缩小，不再是完全未知 | Medium | `run_workflow.py:1019`、`test_precision_policy.py:682/700` | open |
| B4 | **golden 方法族词表已扩**：`GOLDEN_METHOD_KIND` 与 `RUNNABLE_METHOD_KINDS` 同步新增 `opencv_cpu`（`precision_policy.py:861/863`）——说明「扩词表」这条路 19 已在走，但仍是源码里的硬编码元组 | Low | 同上 | open |
| B5 | **新增 `measure_only` 模式**：`spec_schema_template.jsonc:356` 注明该模式下 `perf.baseline` **必须缺席**——G11/B8 的 baseline 落地要把这条算进去 | Low | 同上 | open |
| B6 | 文档内 6 处 `file:line` 因 PR #15 位移，未更新会指错代码 | Medium | G5/G8/准入形态三处已确认位移 | open |

## C · 结论

**13 条 gap 里 12 条仍然成立**，只有 G11 的后半（DUT 符号来源隔离）被 PR #15 做掉。
**五条最硬的（G1 内存轴、G2 随机判定、G3 混合入参、G4 fp64、G5 广播）一条没动。**

最重要的不是 gap 的存废，是 **B1**：`self_test_case` 的口径在本仓内部已经分叉，
而它直接决定 G2/G3/G13 的判据基础。**这条必须先 settle，其余讨论才有意义。**

---

## D · Verify 结果（codex 独立复核，`gpt-5.6-sol` / medium，读代码核对）

12 条声称里 **11 条 CONFIRMED、1 条 WRONG**：

| # | 判定 | 说明 |
|---|---|---|
| 1–10, 12 | CONFIRMED | 逐条给出 file:line 佐证 |
| **11** | **WRONG** | 我把「DUT 符号来源隔离已做掉」判过满。已做的是路径/环境/来源锚绑定；**未做**：defining-ELF 证明（只有 `hasattr`）、两段式只核了一个符号、DUT↔baseline 排他 |

### verify 推翻/修正的我方结论

| 我的原结论 | 订正后 |
|---|---|
| G11 后半「已被 PR #15 做掉」 | **部分完成**；C2 不能删，收窄到补 defining-ELF 证明 |
| G10「provenance 降级」 | **判错**：`source_provenance.py` 明确 `local_source`→`local_snapshot` 是 `complete` 档、不需授权、无降级挂账 |
| 「12/13 条仍成立」单一计数 | **不准**：必须按 `case_source` 分 `generated` / `taskdoc` 两栏计；taskdoc 路已绕开 G5/G8 与 G3 的 shape 维 |

### verify 新找出、我复核确认的 gap（原清单没有）

| 新 gap | 严重度 | 证据 |
|---|---|---|
| **G17** cpp_extension 的 baseline 侧从不排除 DUT vendor 根 | **Critical（可达 fail-open，在唯一准入形态上）** | `perf_msprof.py:2635` `dut_lib` 对 cpp_extension 恒 `None` → `:2680` 的 `exclude_dut_vendor_root` 永不生效。**已本地复核代码形状属实**，运行时后果待实测 |
| **G14** taskdoc 只对首个 case 做 output dtype 对账 | High（fail-open） | `taskdoc_caseset.py:828-836/526-535/1201-1209`；`gen_cases.py:1049-1061/1242-1257/3568-3571` |
| **G15** intake↔执行 dtype 能力面分叉（不只 fp64，还有 bool/uint16/32/64） | Medium | `taskdoc_caseset.py:61-77` vs `gen_cases.py:218-220` |
| **G16** taskdoc 路显式不支持多输出契约 | Low（有意缺口） | `gen_cases.py:1225-1231` |

**全部 4 条已写进 gap 清单。** G11/G10/计数口径三处订正也已落文档。

## E · 结论

- **Result: PARTIAL** —— 重核本身完成且已订正，但暴露出的 G17 是**新的、更严重的**问题，未修（也不该在本轮修，它是代码缺陷不是文档缺陷）。
- 待用户裁定的仍是 §0 的 `self_test_case` 口径冲突（Critical），它决定 G2/G3/G13 的判据基础。
