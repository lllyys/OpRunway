# OpRunway 插件 · workflow v0

NPU 算子验收流水线（任务书 + PR → 用例 → NPU 跑测 → NPU↔基线性能对比）。遵循**三层架构（约束 A 跨运行时可移植）**，见 `doc/oprunway-workflow-design.md`。

## 结构

```
plugin/
├── acc-common/                 # Layer 0 契约 + Layer 1 确定性脚本（核心脑子、工具中立）
│   ├── specs/<op>.spec.json    #   任务书解析产物（spec.json）
│   ├── gen_cases.py            #   Task 1: spec → caseset (+ 真 golden)
│   ├── repo_adapter.py         #   Task 2: caseset → evidence（mock / new_example）
│   ├── validator.py            #   Task 2: evidence → verdict（ADR 0007 唯一裁决源）
│   ├── perf_compare.py         #   Task 3: evidence + baseline → perf_report
│   └── run_workflow.py         #   顶层驱动：串 Task 1→2→3（本地版）
├── skills/                     # Layer 2 薄壳（Claude Code）：驱动 Layer 1、不含脑子
│   ├── acc-casegen/ acc-npu-run/ acc-perf-compare/
├── agents/                     # task-doc-parse（唯一必需 NL 步）、eval
├── commands/op-acceptance.md   # 顶层编排命令
└── bridge/                     # catlass→aclnn 桥（generated_harness，另线）
```

**stage 间只传 JSON/数据文件。** 换运行时（Codex/Antigravity）只换 `skills/`+`agents/`+`commands/` 薄壳，`acc-common/` 零改。

## 跑（v0 · mock NPU，无需真机）

```bash
cd plugin/acc-common
python3 run_workflow.py specs/isclose.spec.json --out reports/isclose
# 注入缺陷看 fail：           --defect isclose_000
```

产物：`caseset.json / evidence.json / verdict.json / baseline.json / perf_report.json` + `work/<case>/{x1,x2,golden,out}.npy`。

## v0 真 vs 桩

| 件 | 状态 |
|---|---|
| gen_cases / validator / perf_compare | **真**（本地逻辑，真 golden + 真判定） |
| repo_adapter `mock` | **真链路**（kernel 输出用 golden 顶替） |
| repo_adapter `new_example` | **桩**（真机 build/run PR 工程，需 NPU + VPN，之后填） |
| 覆盖算子 | 仅 IsClose（示范）；扩算子 = 加 `specs/<op>.spec.json` + golden 分发 |

## 下一步

- 真机跑测（`new_example`）：需 ascend-a5/a3，届时接 build/run/golden/perf。
- 扩 golden 分发（Sign/SPMV…）、rule-catalog 泛化策略、精度多口径（MERE·MARE / np.isclose / ATK）。
- Layer 2 薄壳补全 + `.claude-plugin/plugin.json` 清单。
