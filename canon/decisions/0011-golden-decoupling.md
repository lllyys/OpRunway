---
title: ADR 0011 — Golden is decoupled from the engine and loaded per operator
updated: 2026-07-20
status: proposed
---

# ADR 0011 — Golden is decoupled from the engine and loaded per operator

**决策（用户 2026-07-20 逐条拍定）**：精度 **golden 值**（拿来和 NPU 输出逐点比的「正确答案」）从引擎的硬编码
`GOLDEN` 表（`gen_cases.py:148`，4 算子）改为**按算子从用户侧加载**——引擎**零算子 golden**，第 5 个算子不再在
`gen_cases:562` 崩。**只管精度 golden 值**（≠ 精度标准 ascendoptest/opbase ≠ 性能基线）。

六条：
1. **表 → 加载器**：按 op 名加载 `golden_fn` + 元数据 `GOLDEN_SOURCE`/`GOLDEN_PROVENANCE`；加载不到 / 缺元数据 / source 非枚举 → **fail-closed**。
2. **归属**：golden 落用户 CWD `<ops_root>/<op>/`（同 runner）、`gen_cases` 里 op-中立加载器读。**补 [[ADR 0002 — Acceptance grounded in catlass and the spec]] 未定的「golden 归属」洞**（选 per-op 输入产物落用户 CWD、非 repo_adapter 层）。
3. **公式来源分级**（最高律令 [[Golden and precision standard come only from the task-doc-specified method]]）：仓自带 / PR 参考 > 任务书期望 > `analytical_ref`（agent 自写、**末位 + 标可信度 + 人核**）；任务书方法不支持 → fail-closed。防 [[Primitive-to-case rule library]] 之理（真值口径一错则所有精度用例作废，golden 错亦然）。
4. **后端**：golden 恒 CPU、**按算子 torch>numpy 定档记录**、选定库必装 fail-closed、不运行时偷换、确定性保住。**更新 [[Golden is fixed to torch on CPU for determinism]]**。
5. **oracle_source loader 侧接线**：承 Q9/Q7（[[oracle_source is a hardcoded constant not a recorded fact]] 已止血），加载时把该算子 `GOLDEN_SOURCE` 写进每条 case、门继续校。
6. **形态**：`golden.py` 动态 import（执行 = 用户 / 生成的 Python、性质同 runner.cpp、同信任级；golden 由 acc-spec / acc-runner-dev 从任务书生成、非任意上传）+ **执行边界文档化**。

是 [[Runner is an output of the engine not a component]] 的 golden 侧对应——两刀齐了引擎才真 op-中立。对齐
[[OpRunway component breakdown]]（核心不放算子件）与 [[ADR 0009 — One generalized workflow with per-repo adapters]]。

**Consequences.** 引擎真 op-中立（能接任意算子、「加算子 = spec + golden + runner 三件皆用户侧」成立）；
[[Golden is fixed to torch on CPU for determinism]] 被决策 4 更新；代码工作项（`gen_cases` 加载器 + `golden.py` 契约 +
oracle_source loader 接线 + acc-spec/acc-runner-dev 产 golden 纪律 + 安全边界文档 + 单测改 fixture）**另开 PR，代码未实施**。

**详细设计 / 取舍 / 候选** 见 `doc/oprunway-golden-decoupling-adr.md`。

**tier 说明**：留 `proposed`，待 `bureau:review`。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-20：golden 去引擎化 ADR 拍定）
