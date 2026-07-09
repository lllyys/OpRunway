---
name: op-acceptance
description: 跑一个 NPU 算子的验收流水线——输入=算子任务书(md 路径或链接)+PR 链接，自动产 spec→跑测→裁决→报告。
argument-hint: "<任务书 md路径或链接> <PR链接> [--mode mock|new_example]"
---

# /op-acceptance — 算子验收（人手动触发）

人触发版：把「任务书 + PR」交给 **`op-acceptance` agent** 跑完整验收。与 agent 同一流程，只是入口不同（agent 供别的 agent 自动调、本命令供人手动跑）。

**参数**：`$1`=任务书（md 本地路径或 http(s) 链接）、`$2`=PR 链接、可选 `--mode mock|new_example`（默认 mock）。

## 做什么

调起 **`op-acceptance` agent**（`agents/op-acceptance.md`），按其六步跑：
① `fetch_source.py` 取材 → ② **`acc-spec` skill** 出 `<op>.spec.json` → ③ **`acc-runner` skill** 生成+验证 runner（new_example 模式、需 NPU）→ ④ `run_workflow.py` 跑测裁决 → ⑤ FAIL 解耦 root-cause → ⑥ 中文报告。

- **mock**（默认，无需真机）：到 spec + numpy-golden 自检裁决为止，验证流水线自洽。
- **new_example**（真机）：**先确认用户已开 NPU/VPN**；`OPRUNWAY_*` 环境变量指真实机器/路径（不写进仓）。

## 性能对比（Task 3，待散文门）
- **GPU 标杆 consumer（T8）**：`run_workflow.py --gpu-baseline <外部 GPU 标杆 JSON>` 或 `spec.perf.baseline∈{gpu,gpu_external}` → 解析外部 GPU 标杆(按 case_id+完整输入签名对齐)出 NPU↔GPU 对比。缺标杆 → `BLOCKED_WAIT_GPU_BENCHMARK`（正规挂起、非 fail、绝不显 PASS）；双边 timing_scope 不一致 → `BLOCKED_INCOMPARABLE_TIMING_SCOPE`。真 GPU 数据待外部方给。
- **小 shape 例外（T6）**：任务书『<Nus 差 Nus→仿真图』条款 → 达标记 False + 出仿真图证据 → `PASSED_WITH_RISK`（挂人工 CP，退出码 2）。

## 约束
- 全程中文；副作用（真机 clone/build/跑测）先确认；`needs_review` 不当 pass。
- 只认任务书为验收权威；缺 NPU/VPN 就明说「真机待开 VPN」，不假装跑了真机。
- 判定在 `validator.py`，本命令只搬 JSON + 出报告。
