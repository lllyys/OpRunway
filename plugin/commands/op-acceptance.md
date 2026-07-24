---
name: op-acceptance
description: 跑一个 NPU 算子的验收流水线——输入=算子任务书(md 路径或链接)+PR 链接，自动产 spec→跑测→跑确定性脚本、逐字引用 acceptance.json 裁决并标来源→报告。
argument-hint: "<任务书 md路径或链接> <PR链接> [--mode new_example|mock]"
---

# /op-acceptance — 算子验收（人手动触发）

人触发版：把「任务书 + PR」交给 **`op-acceptance` agent** 跑完整验收。与 agent 同一流程，只是入口不同（agent 供别的 agent 自动调、本命令供人手动跑）。

**参数**：`$1`=任务书（md 本地路径或 http(s) 链接）、`$2`=PR 链接、可选 `--mode`（**默认 `new_example`，即真机**；2026-07-22 由 `mock` 翻正）。

⚠ **验收只有真机一条路**。`mock` 通路仍在（供测试与本地演示），但它的「NPU 输出」就是 golden 本身、
精度按构造必过 → C5（2026-07-22）起它**物理上不再产 `acceptance.json` / `verdict.json`**，
改产 `dev_run_summary.json` + `dev_precision_check.json`（均带 `evidence_grade="development"` +
`acceptance_note` 标 NON-ACCEPTANCE）。**不是「产了但不算数」，是压根不产。**

## 做什么

调起 **`op-acceptance` agent**（`agents/op-acceptance.md`，`mode:primary` 薄编排器）。它首响应先加载 **`acceptance-workflow` skill**，按其 **CP-A..E 状态机**推进——**先 CP-A 前置，再 CP-B..E**：

- **CP-A 前置**：primary 跑**确定性** `fetch_source.py` 取材 → **任务书↔PR 对应校验**（verify-spec-pr-correspondence，proposed·未 settle，载重前需核）→ 环境确认（NPU/VPN 开没开、目标机按任务书 `适配硬件` × op_def `AddConfig` 双源定）。组装 `correspondence.json` 时，issue/追踪号这类 **NL-read 字段显式标 `source=NL-read` + 出处（task_doc / PR title）**；status 判定靠 **`pr_facts.target_dir` 机器比对 + 用户确认**（`needs_user_confirmation` 由用户拍板），primary **不自行 NL judge 空任务、不把 NL 结论当事实落盘**。
  - **对应不成立（`mismatch`，由 `pr_facts.target_dir` 机器比对判定）→ 出「程序结论」（非 pass/fail）、不跑**；**疑似空任务/证据不足**（需 NL 判断的）→ 归 `needs_user_confirmation`、摆证据由用户拍板（primary 不自行 NL judge 空任务）；`confirmed` 才继续。
- **CP-B Task1 用例**（只关注 task1/caseset 自洽）：dispatch `acc-spec-extractor:extract_spec` → `<op>.spec.json` + `task_pr_gaps`；primary inline `gen_cases.py <spec> --dry-run`（plan-only 契约自检：预算区间 / dtype 分布 / 特殊场景覆盖 / id 唯一 / 种子确定）。
  ⚠ **能力边界**：dry-run **plan-only**：不调 `golden_fn`、不落 `.npy`、不产任何裁决；会加载执行 `golden.py` 取 `out_shape`（缺文件只记「未核」，文件在但坏了则当场抛）；**验不了**来源契约（那是 `check_golden.py`）/ validator 链 / 三级门，那些只有 CP-D 才验得到。
  dry-run 报错或账本异常 → `refine_spec`。
- **CP-C runner**（真机路径、需 NPU）：dispatch `acc-runner-dev:gen_runner`（先过 scope gate）→ `verify_runner`；按 acc-runner-dev 的 **runner 自检证据满足/不满足** 纪律（当前**非代码强制 sidecar 硬门、待补**）——未满足则停在 CP-C、不上真机。（acceptance 裁决只逐字引用 `validator.py` / `perf_compare.py` / `validate_acceptance_state.py` 产物，ADR 0007。）
- **CP-D 真机跑测**（一次原子）：dispatch `acc-verify-rootcause:run_npu` → `run_workflow.py --mode new_example`（Task2 精度 + Task3 性能 + 三级门一次成）→ `evidence.json` / `verdict.json` / `baseline.json`（有基线时）/ `perf_report.json` / `acceptance.json`。**Task3 性能**：基线来源=`spec.perf.baseline`（perf-baseline-by-reference-source，proposed·未 settle，载重前需核）；缺外部 GPU 标杆 → 路由 `BLOCKED_WAIT_GPU_BENCHMARK`，口径不可比 → `BLOCKED_INCOMPARABLE_TIMING_SCOPE`；**GPU external 对比层 consumer 侧已接入 pipeline，缺的是外部真实数据**。FAIL → primary 再 dispatch `acc-verify-rootcause:rootcause`（先解耦再归因）。
- **CP-E 报告**（primary）：逐字引用 `acceptance.json`/`verdict.json`/`perf_report.json` 裁决 + `task_pr_gaps` + 各维度出中文报告。

两种模式：

- **new_example**（**默认**，真机）：**先确认用户已开 NPU/VPN**；走全 CP-A..E；`OPRUNWAY_*` 环境变量指真实机器/路径（不写进仓）。**验收裁决只出自这条路。**
- **mock**（非默认，无需真机）：本地演示与管路自检用。**不产验收裁决**（见上 C5），产物一律标 NON-ACCEPTANCE。

## 性能对比（Task 3，待散文门）
- **GPU 标杆 consumer（T8）**：`run_workflow.py --gpu-baseline <外部 GPU 标杆 JSON>` 或 `spec.perf.baseline∈{gpu,gpu_external}` → 解析外部 GPU 标杆(按 case_id+完整输入签名对齐)出 NPU↔GPU 对比。缺标杆 → `BLOCKED_WAIT_GPU_BENCHMARK`（正规挂起、非 fail、绝不显 PASS）；双边 timing_scope 不一致 → `BLOCKED_INCOMPARABLE_TIMING_SCOPE`。真 GPU 数据待外部方给。
- **小 shape 例外（T6）**：任务书『<Nus 差 Nus→仿真图』条款 → 达标记 False + 出仿真图证据；**须先过 `gate_task3`**（图齐备+例外行↔图交叉一致+SVG sha 钉死）才 → `PASSED_WITH_RISK`（挂人工 CP，退出码 2）；**门未过 → `BLOCKED(验收门未过)`（exit 1）、非 PASSED_WITH_RISK**。

## 约束
- 全程中文；副作用（真机 clone/build/跑测）先确认；`needs_review` 不当 pass；验收门 `validate_acceptance_state.py` STATUS: FAILED → **不出 pass 裁决；仍由 run_workflow 写 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整）。
- 只认任务书为验收权威；缺 NPU/VPN 就明说「真机待开 VPN」，不假装跑了真机。
- 判定唯一归**确定性脚本链**（`validator.py` 精度 + `perf_compare.py` 性能 + `validate_acceptance_state.py` 三级完整性门 → 门控后写 `acceptance.json`，ADR 0007）；本命令与 agent **不自行判 pass/fail**，只逐字引用确定性产物的裁决并标来源。
