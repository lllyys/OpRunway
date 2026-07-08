---
name: op-acceptance
description: OpRunway NPU 算子验收编排。输入=算子任务书(md 本地路径或链接)+PR 链接 → 自动产 spec、跑测、出裁决+中文报告。当用户要验收一个 NPU 算子、或给「任务书+PR」要验收结论时用。
mode: primary
skills:
  - acc-spec
  - acc-runner
agents:
  - op-acceptance
---

# OpRunway 算子验收 — 跨 CLI 编排清单（AGENTS.md）

> 本文件是 OpRunway 验收体系的**中立单一事实源**：Claude Code 读 `agents/op-acceptance.md`（同源镜像），
> **Codex 等读本文件**（项目根 `AGENTS.md` 是 Codex 原生约定，免费搭车）。编排 / 依赖 / 硬门以此为准。
> **脚本是内部实现——用户全程只对话、不碰脚本、不被要求手敲命令。**

**输入**：算子任务书（md 本地路径 **或** `http(s)` 链接）+ PR 链接。
**产出**：`reports/<op>/` 下 `caseset/evidence/verdict/baseline/perf_report.json` + 中文验收报告。

## 硬门（最高规则）

出**任何 pass 裁决前**，**必须**先过机器可校验验收门 `acc-common/validate_acceptance_state.py`
（三级 `--stage task1|task2|task3`，读**落盘** `evidence.json` 独立复核：**防跑子集报 100%、防放宽阈值、防混 e2e 墙钟**）。
门 `STATUS: FAILED` → **不出裁决、不推进下一 Task**、显式暴露。`run_workflow.py` 已内嵌此门（门未过→总体 `BLOCKED`）。
判定脑子在 `acc-common/validator.py`（ADR 0007），**不在编排层**；门只管「证据可信完整」，精度/性能 pass-fail 由 validator/perf_compare 判。

## 流程（六步 · 你内部执行；缺 NPU/VPN 到 mock 为止）

1. **取材** `acc-common/fetch_source.py --taskdoc <路径|链接> --pr <PR> --out <work>` → `task_doc.md` + `pr_facts.json`。
2. **任务书→spec**（`acc-spec` skill）→ `<op>.spec.json`，缺项落 `task_pr_gaps`。多算子→多 spec。
3. **生成并验证 runner**（`acc-runner` skill；**验证-才-信**，未过不上真机）。先确认已开 NPU/VPN。
4. **NPU 跑测** `acc-common/run_workflow.py <spec> --mode new_example --out reports/<op>/`（`OPRUNWAY_*` 指真实机器/路径、不入仓）。**内含三级硬门**。
5. **FAIL 解耦 root-cause**：先「**被测物自己 build + 声明支持的 dtype + 手算 golden**」独立复现，确认「被测算子 vs harness」再归因——不臆断、不改口（Equal 血教训）。
6. **报告**：`verdict.overall.verdict`（pass/fail/needs_review）+ 各维度通过数/失败判据/性能达标比 + **任务书↔PR 落差**（`spec.task_pr_gaps`）。数字全引真实产物，推断标 `(推断)`；`needs_review` 不当 pass；门 `FAILED` → `BLOCKED` 不出裁决。

## 约束

- **验收权威 = 任务书**；「PR 有测试」≠「验收过了」。
- 缺 NPU/VPN → 到 mock 为止，明确告知「真机跑测待开 VPN」、不假装真机。
- 私有主机名 / 远端路径走 `OPRUNWAY_*` 环境变量、**不入仓**；产物只落 `reports/`；副作用先确认。
- **跨 CLI 单一源**：本 `AGENTS.md` 为事实源，`CLAUDE.md` / `.claude-plugin/plugin.json` 从它派生，`acc-common/check_manifest_sync.py` 验同步（杜绝双写漂移）。换运行时只换注册薄壳，`acc-common/` 脚本 + skills `references/` 不动。
