# P0 只读前置核验结果（2026-08-07）

**Run**: P0（plan.md §2），只读、不改代码
**模型**: `gpt-5.6-sol`，effort medium，sandbox read-only

## P0-a · format ND — RESULT=属实

未显式指定 format 时，rank 3/4/5 走 `torch_npu_rank_default` 映射成 NCL/NCHW/NCDHW，而非任务书要求
的 ND；`standard` 派发形态下显式传 `nd` 在**生成期**（codegen，非运行期 phase-2）直接抛
`CppExtensionCodegenError`；显式 ND 目前只在 `extended` stage-2 下实现。

证据：`cpp_extension_codegen.py:99-105`（rank→format 映射表）、`:250-252`（缺省来源
`default_unverified`）、`:379-423`（extended ND 转换器）、`:537-545`（standard 遇非默认 format 即拒）、
`taskdoc-to-spec.md:150`（spec 抽取规则同样确认该契约）。

## P0-b · CANN 8.5.0+ 版本门 — RESULT=属实

当前只把版本记成非空字符串，没有语义化版本比较，也不会因低于 8.5.0 阻断；全仓 `acc-common` 未见
packaging/semver 等版本比较库。

证据：`cpp_extension_driver.py:473`（原样读取 `ASCEND_TOOLKIT_VERSION`/`CANN_VERSION`，不解析）、
`:480`（唯一阻断条件是字面值 `"unknown"`）、`validate_acceptance_state.py:1029-1034`（只做非空检查）、
旁证 `test_validate_cpp_extension_receipt.py:156-186`（非语义化字符串 `"8.x"` 也能通过验收门）。

## P0-c · dtype 四类归因契约 — RESULT=未区分（需 B4 先补契约）

四类成因**没有**被区分为四套独立、机械可判的「状态字段 + 退出码 + 产物集合」：

| 成因 | 现状 | 关键证据 |
|---|---|---|
| **① API 层显式拒绝 dtype** | 未独立区分，混入普通执行失败 | `cpp_extension_driver.py:315/392/418`：失败只按阶段分类为 `phase=execute`/`error_kind=execution_failed`，不解析 ACL 错误码或 "unsupported dtype" 语义；`repo_adapter.py:389/503`：`evidence[].status="execution_failed"` 同时容纳「DUT 拒绝/kernel 崩/输入物化或读回失败」；`validator.py:1158/1175/988`：最终统一判 `功能=fail`、`精度=fail` → `verdict.overall.verdict="fail"`、`acceptance.state="FAILED_PRECISION"`、`exit_code=1`。**没有 `api_dtype_rejected` 这类受控状态** |
| **② harness/测试工具链限制** | 有独立挂账字段，但终态不唯一 | 已有 `task_pr_gaps[].kind="dtype_deferred"`（`validate_acceptance_state.py:61/254`）与 `DEFERRED_NP_BY_FORM`（`repo_adapter.py:158/172`）。但 gate 只禁 `verdict="pass"`，明确接受 `needs_review`/`fail`/`passed_with_risk`（`validate_acceptance_state.py:1352`，`test_validate_acceptance_state.py:2436`）——**harness 限制仍可合法落成 `fail`，违反"必须停在 blocked/needs_review"**。普通生成路径中 validator 本身不消费 `dtype_deferred` 产 `needs_review`；若给出 `pass`，三级门才把整轮变笼统的 `BLOCKED_EVIDENCE_INCOMPLETE`（`run_workflow.py:1008/325`），**不是 harness 专属状态**。若 dtype 连 deferred 白名单也不支持，`gen_cases` 直接抛 `ValueError`（`gen_cases.py:1845/1913`），此路径**没有结构化 verdict/acceptance** |
| **③ 证据不完整/needs_review** | 存在机械状态，但拆成两套语义 | 路径一：口径不确定 → `verdict="needs_review"`/`state="NEEDS_REVIEW"`/`exit_code=1`（`validator.py:958/998`，`run_workflow.py:1024`）。路径二：三级门证据缺失/覆盖不足 → `gate.passed=false`/`state="BLOCKED_EVIDENCE_INCOMPLETE"`/`exit_code=1`（`validate_acceptance_state.py:324`，`run_workflow.py:972/1008`）。**没有统一的"证据不完整"受控原因字段**；更关键的是 `evidence.status!="ok"` 的取证失败被 validator 直接算作①的功能/精度 fail，**仍与①混用** |
| **④ 已确认 DUT 能力缺失** | 有两个静态 finding kind，但未接通为独立"未通过"终态 | `task_pr_gaps[].kind="dtype_unsupported_by_op_def"` 被映射成 `verdict="passed_with_gaps"`/`exit_code=2`（`validator.py:1006`，`run_workflow.py:292/337`）——**不是"未通过"**。`kind="dtype_unsupported_on_target_hw"` 只在 `validate_acceptance_state.py` 的覆盖门里识别（`:184/204/1341`），**`validator.py` 完全没有这个 kind**（`test_validate_acceptance_state.py:2278` 明确记录"validator 尚未识别该 kind"），自然链会先产干净 `pass`，随后被 gate 改成笼统 `BLOCKED_EVIDENCE_INCOMPLETE`。真机 API 实际返回"不支持 dtype"时并不会自动生成上述 gap，而是落回①的 `execution_failed` → `FAILED_PRECISION`。**当前没有"真机错误码/日志 → 已确认 DUT 能力缺失"这条机械归因链** |

**结论**：只有若干局部词表，没有四类各自封闭的状态机。①与普通 DUT/harness 执行错误混合，
②允许 `fail`，④不是独立失败终态且 target-hardware kind 未接入 validator。

**⚠ 这正是 plan.md B4 完成判据里预期的分支**（"B4 先补齐 P0-c 标出的 Layer 1 契约缺类"）——
P0-c 不是新增缺口，是把 B4 该修的具体位置钉死了。B4 实施时直接从上表的四行开始改，不用重新分析。

完整原始 codex 输出（含全部中间 exec/rg 步骤）：本轮会话 workflow 产物，未另行归档；
本文件已包含定论与全部 file:line 证据，足够驱动 B4 实施。
