# OpRunway — NPU 算子验收 agent+skill 体系

输入 = **算子任务书（md 或链接）+ PR 链接** → agent 自动产 spec、跑测、出**裁决 + 中文报告**。三段式流水线：用例生成(ST) → NPU 精度+性能跑测 → NPU↔GPU 性能对比。

## 怎么用：在会话里对话，不碰脚本

装上本插件后，**在会话里对 `op-acceptance` agent 用自然语言说要验收什么**即可，例如：

> 帮我验收这个算子：任务书 `<md 路径或链接>`，PR `<链接>`。

agent 内部完成六步（取材 → 任务书→spec → 生成并验证 runner → NPU 跑测 → 失败解耦 → 报告）。**你全程只对话、看进展与最终报告——不需要、也不会被要求去跑任何脚本或命令。** 缺东西（任务书 / PR / 是否已开 NPU-VPN / mock 还是真机）它会问你。

- 缺 NPU/VPN → 到 mock 自检为止，如实告诉你「真机跑测待开 VPN」，不假装。
- 一份任务书含多个算子 → 自动拆成多份、逐个验收。

## 形态（统一 · 可移植）

**一个对话式 agent 为唯一入口**，内部用 skills（NL 判断）+ 确定性脚本（工具中立）：

| 层 | 内容 | 用户可见？ |
|---|---|---|
| **agent** `op-acceptance` | 对话入口 + 六步编排 | ✅ 唯一入口，只对话 |
| **skills** `acc-spec`(任务书→spec)、`acc-runner`(生成 runner) | NL 判断规则（`references/`）| 内部 |
| **scripts** `acc-common/*.py` | 取材 / 造用例 / 裁决 / 性能（确定性核心）| 内部（agent 幕后跑，**不暴露给用户**）|

**跨 CLI 统一**：脚本 + JSON 契约 + skill `references/` 工具中立、一份到处用；换运行时（Codex / Antigravity）只换 agent/skill 的注册薄壳，核心不动。判定脑子在 `acc-common/validator.py`（ADR 0007），不在 agent。

## 现状（诚实）

- **mock 端到端可用**（无需真机）；**真机跑测**需开 VPN + runner 验证。
- runner 生成当前闭环 = ops-<族> 仓·aclnn 两段式·opp 安装型（含非 experimental 子树）（catlass/非 aclnn/双实现待扩，见 doc/oprunway-batch6b-design.md）；「验证-才-信」是**纪律**非代码硬门（sidecar 待补）。
- 已端到端跑通管路的算子：IsClose / Sign / Equal / Neg。⚠ 其中经**真 NPU 验收裁决**的只有 IsClose / Sign；
  mock 通路自 C5（2026-07-22）起**不产验收裁决**（只产标 NON-ACCEPTANCE 的 `dev_run_summary.json`）。

> 设计/契约见 `../doc/oprunway-design.md`；改动流水见 `../doc/oprunway-changes-brief.md`；TODO 见 `../doc/oprunway-todo.md`。
