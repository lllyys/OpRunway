# 判定机制与结论含义

读报告（主流程 A3）时用于解释各字段；判定实现在 `criteria/`，本文只解释含义。

## 三层检查

| 层 | 内容 | 出处 |
| --- | --- | --- |
| 比对目标 | potrf F vs golden 直审（复数同，实/虚拆目标），还原 L·Lᵀ 对 A 降诊断（DPOT01 兜底层保留）；potrs 比解 X；potri 单目标 A⁻¹ 直比 golden（A·A⁻¹ 对 I 留 DPOT03 复核层）。主判基准统一 golden64 | 任务书 §3.2 第 2 条（HT-7 对调，issue A5 裁「按任务书」） |
| 混合容差 | 逐元素 \|actual−golden\| ≤ atol + rtol×\|golden\|；用例过 = matched_ratio ≥ 0.99 且 max_abs ≤ limit。复数实部、虚部各判且双侧同过 | 任务书 §3.2 第 3 条 |
| 残差复核 | 上层不满足不判死，改算 LAPACK 残差比（DPOT01/02/03，ε=2⁻²⁴）对阈值判定，作数值终审：potrf/potrs 阈值 max(5·ratio_cpu, 3·ratio_cpu_mean)（HT-3；ratio_cpu_mean 由包 index 顶层按算子预计算注入，缺 mean 走单支 5·ratio_cpu 兼容——超单支记证据不足不判 FAIL，待 v3 包）；potri 阈值 max(5·ratio_cpu, 0.1)（HT-5） | 0924 任务书 §3.2.2（issue A1/A3） |

设残差复核的原因：固定容差在正态大 σ 与高条件数下系统性误伤参考实现自身
（标准工程实测），残差复核既豁免误伤又保留「劣化于 CPU 参考 2 倍即不过」的约束。

## 报告字段

| 字段 | 取值与含义 |
| --- | --- |
| `numeric` | PASS/FAIL，数值判定结论 |
| `formal` | 恒 `PENDING_RULING`：阈值语义（任务书表与开源标准表取舍、`or 32 * ULP` 释义）待任务方明确，之前不出正式通过 |
| `flags` | 附加标记位，当前恒空：T3（两套第一层阈值分歧）已随 HT-14 收单套拆除，T4/T7 已摘 |
| `expectation[].status` | 数值 PASS / 数值 FAIL / 证据不足 / 待裁。证据不足 ≠ 失败：该项没有可判的输入（未生成 case、batched 无检查标准等） |
| 族级结论 | 期望集先于结果存在；任一必测项证据不足则族级不判通过，补齐后重新运行即可 |

## 与自测的关系

包内 `verify_accuracy.py` 是同一判定的渲染副本，供开发者自测；其输出不构成验收
证据。副本与本 skill 实现不一致时报告记告警，判定照常以本 skill 实现为准。
