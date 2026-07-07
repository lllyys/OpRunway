---
title: Primitive-to-case rule library
updated: 2026-07-02
status: canonical
reviewed: 2026-07-02
---

# Primitive-to-case rule library

acc-casegen 的**核心知识库**——让「生成用例」跨算子通用。算子 = **原语**的组合；每类原语挂一套必测 case 模式。生成 = 目录 + 展开逻辑 + 元规则。这是 acc-casegen 的可复用 IP，不绑任何具体算子（LayerNormGroupedMatmulBiasSilu 只是首个夹具）。

**① 目录（每类原语 → case 模式，实现为 `references/rule-catalog.md`）**
- **归约**（LayerNorm/Softmax/RMSNorm/Reduce）：归约维=1（var=0 分支）、大归约维（fp16 平方和溢出，测方差是否 fp32）、行常量/近常量（var≈0 放大误差）、ε 鲁棒。
- **matmul/分组/batch**：单轴非对齐（M/K/N 各一，可定位）、大 K 累加（fp16 精度）、batch 1/多、M/N=1、**变长分组/空 group(M=0)**（若语义为 group_list 必测）。
- **有界激活**（SiLU/GELU/Sigmoid）：两端饱和 + ≥1 条**强制输出走相对误差分支（|out|≥1）**（否则多数点落 abs 分支「假容易」）、拐点区。
- **elementwise/bias**：广播、极值、大正/大负。

**② 展开逻辑**（实现为 acc-casegen 的 SKILL.md）：解析任务书 → 识别算子 = 哪些原语的组合 → 每原语拉 case 模式 → 按 shape 符号（如 G/M/K/N）实例化 → 合并去重 → 加元规则。

**③ 元规则（所有 case 共用）**：golden = 各原语 numpy 参考按公式拼、**默认 fp32 中间→降输出 dtype**（对齐 Ascend Cube fp32 累加），**但按算子实际累加 / 任务书参考精度调整**——敏感归约（方差/大 K/抵消）可能需 fp64、累加非 fp32 的算子按其实际精度；**动态/分组语义先从 host 接口锁定**（错则所有精度用例废）；每条 case 标注「走 [[AscendOpTest precision thresholds]] 的哪个 compare 分支 / 数据来源(框架 or 预置) / 任务书条款 / PR 改动」。

是 [[OpRunway component breakdown]] 中 `acc-casegen` 的核心，产出吃 [[Acceptance contract and evidence chain]] 的契约。规则种子来自首个夹具的对抗评审（见 `doc/oprunway-task1-cases-critique.md`）。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]，[[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（golden「fp32 中间」软化为默认 + 按算子/任务书调整）
