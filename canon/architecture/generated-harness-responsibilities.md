---
title: generated_harness responsibilities
updated: 2026-07-02
status: canonical
reviewed: 2026-07-02
---

# generated_harness responsibilities

让「生成的用例」真跑起来，是**跨仓 + 跨框架**的通用价值（评审证明：难点不在生成用例，在让用例在「某仓 kernel × 某框架」上真跑）。`generated_harness`（[[Repo adapter interface and modes]] 的一个模式）必须干 4 件事，每件写明「保证什么」：

1. **bin-IO shim** — 把仓的 kernel（可能自造数据+进程内自校验，如 catlass example）改造成「按框架路径协议读输入 bin / 接 `--case_name --timestamp --output_shapes` / 把输出写到 `output_desc.data_path`」。**保证**：kernel 吃我们的用例、产物被框架 compare 读到。
2. **layout 字节契约** — 同一输入喂 golden 与喂 kernel 要**分两份造、禁止共用一次 reshape**：`X_logical`（喂 golden）按**逻辑形状**摆、numpy 按逻辑索引算参考；`X_bin`（喂 kernel）按算子**声明的 layout 摆物理字节**、设备直接读。layout 非 RowMajor 时两者排布不同（例：B 为 ColumnMajor 分组 → golden 用 `B_logical(G,K,N)`、kernel 用 per-group 转置后的字节）。**保证**：设备读到的是声明 layout、不是意外转置，杜绝「不崩却算错」的静默失败。
3. **数据注入** — 自己按分布 + 固定 seed 造 bin **预置**（框架 `os.path.exists` 检测存在即跳过其默认 uniform 生成），**同一份 bin 同源喂 kernel 和 golden**。**保证**：特殊值（负饱和/近常量/大幅值）可控、可复现、golden 同源。
4. **性能测量栈** — 两侧**同 `timing_scope`** 采集；基线参考实现按框架真实签名适配（如 `npu_grouped_matmul` 需 (G·M,K)+group_list、bias 独立 add、layer_norm normalized_shape=[K]/无 γβ）；warmup/iters/median 自实现；**比值判定归 validator**（[[ADR 0007 — Verdicts come from a deterministic validator]]，[[ADR 0006 — Compare performance at a matched timing scope]]）。**保证**：1.2× 可算、公平、可复现。
   - 端到端可行：AscendOpTest 内建 `msprof --application`（含 H2D/D2H）能采 e2e，只是自带 `get_prof.py` 只解析 kernel-only 的 OpBasicInfo.csv → device_e2e 由我们驱动 --application + 自解析 op_summary + 裁到算子窗口（避开 init/文件IO）。

[[catlass to aclnn bridge for AscendOpTest]] 是本抽象的**具体实例**（catlass × AscendOpTest）。规则种子见 `doc/oprunway-task1-cases-critique.md`。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
