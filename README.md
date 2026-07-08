# OpRunway

**NPU 算子「验收（acceptance）」workspace。** 把下面这条三段式验收流水线，做成可复用的 workflow / agents / skills：

```
任务书 + PR ──①用例生成(ST)──▶ 测试用例集 ──②NPU跑测──▶ NPU 精度对比 + 性能数据
                                  │                                   │
                                  └──③ 同一份用例喂 GPU 标杆 ──────────┘
                                          ───▶ NPU↔GPU 性能对比报告
```

- **Task 1 · 用例生成(ST)**：PR + 算子任务书 → 覆盖功能/精度/性能的机读+人读用例集（整条流水线的脊柱）。
- **Task 2 · NPU 跑测**：用同一套用例，在算子工程上跑出 NPU 精度对比 + 性能数据。
- **Task 3 · 性能对比**：同一套用例喂 GPU 标杆 → NPU↔GPU 性能对比报告。

## 现状

**主干施工完毕 + 真机端到端验证通过**，但还不是「能对任意算子一键验收」的成品——见 [`doc/oprunway-todo.md`](doc/oprunway-todo.md)。

- **架构**：三层可移植设计。Layer 0 六份 JSON 契约 · Layer 1 确定性脚本（工具中立的「脑子」）· Layer 2 per-tool 薄壳（编排）。Stage 间只传 JSON。
- **已真机验证**：三个结构互不相同的算子（IsClose 二元/bool、Sign 一元/数值、Equal 二元/bool）在真昇腾 NPU 上跑通，**裁决全部正确**——精度 = 真 NPU 输出 vs numpy golden，性能 = msprof 真 kernel-only vs 真内置 TBE 基线，总体门同时卡精度+性能。
- **加一个算子**：agent 自动产 `spec`（acc-spec）+ `runner`（acc-runner）；`gen_cases` 的 golden 目前仍是一处手工注册（待自动化）。用户侧无感——只需在会话里给任务书 + PR。

## 怎么用：在会话里对话（不跑脚本）

装上插件后，**在支持的 agent CLI 会话里，对 `op-acceptance` agent 用自然语言说要验收什么**：

> 帮我验收这个算子：任务书 `<md 路径或链接>`，PR `<链接>`。

agent 内部完成全部六步（取材 → 任务书→spec → 生成并验证 runner → NPU 跑测 → 失败解耦 → 报告）。**你只对话、看进展与最终报告——不需要、也不会被要求跑任何脚本或命令。** 缺东西（任务书 / PR / 是否已开 NPU-VPN / mock 还是真机）它会**用对话问你**。

- 缺 NPU/VPN → 到 mock 自检为止，如实告诉你「真机跑测待开 VPN」，不假装。
- 真机跑测的机器/路径经 `OPRUNWAY_*` 环境变量注入（agent 内部用、不写进仓、不需你手敲）。
- 平台/精度/性能口径由 agent **从算子任务书推**（不猜）。

> 内部实现（确定性脚本 `acc-common/*.py`、spec/runner 生成、判定 `validator.py`）是 agent 幕后的事，**不作为用法暴露给用户**。开发/契约细节见 `doc/oprunway-design.md`。

## 目录

```
plugin/     agent+skill 体系（acc-common 脚本 + skills + agents + commands + .claude-plugin/manifest）
bridge/     route-B catlass 去风险（已从 plugin 移出，非验收体系组件）
doc/        设计与流水线（oprunway-design.md）、改动简表、TODO
canon/      bureau 决策/ADR（durable 知识，capture→compile→review 三态）
spec/       算子 spec 笔记
repos/      被测/参考算子仓（外部克隆，.gitignore 不入库）
```

## 约定（继承 cann-ops-test）

零硬编码（仓名/路径/SOC/阈值运行时探测或询问）· 零持久化配置（产物落 CWD 下 `reports/`）· 全程中文 · 副作用先确认 · 跑测多层判定 · 不凭空捏造（推断项显式标注）。

> 详细设计见 `doc/oprunway-design.md`；改动流水见 `doc/oprunway-changes-brief.md`；待办见 `doc/oprunway-todo.md`。
