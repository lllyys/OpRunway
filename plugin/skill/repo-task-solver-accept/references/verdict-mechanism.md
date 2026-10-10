# 判定机制与结论含义

读报告（主流程 A3）时用于解释各字段；判定实现在 `criteria/`，本文只解释含义。

## 一段式残差判定

精度判定是单步（2026-10-08 用户裁定，去除生态精度标准层）：每个 accuracy 用例直接
算 LAPACK 残差比（DPOT01/02/03，ε=2⁻²⁴），对阈值判定即为数值结论。残差输入用实现
实际输入 A32/B32（内部升 f64；原 README 1.3 的 potrf「A 用 A64」口径废止），阈值：

- potrf/potrs：max(5·ratio_cpu, 3·ratio_cpu_mean)。ratio_cpu 是本 case 的 CPU 参考
  链残差（随包逐 case 固化），ratio_cpu_mean 是单矩阵同算子全部正定精度用例的算术
  平均（index 顶层），或批量本 case 的槽位加权均值（index 条目）。缺 mean 时走单支 5·ratio_cpu 兼容——过即 PASS，
  超出记证据不足不判 FAIL，待含 mean 的包复判。
- potri：max(5·ratio_cpu, 0.1)。
- 批量算子逐矩阵判定，配对该矩阵自己的 c_i=ratio_cpu[i]；case 数值结论 = 全部矩阵
  通过。A0 抽样包另有余槽 bit-wise 一致性核对与 infoArray 核对，失配即数值 FAIL
  并报槽位号。

fail-closed 边界：残差不可计算（被测含 NaN/Inf、全零输出触发零分母）→ 数值 FAIL，
`error` 指认原因；ratio_cpu 缺失或准备失败 → 证据不足（不判精度）。

原「逐元素混合容差 + 残差复核」两步结构已整体拆除：选残差作唯一判据，因为固定
容差在正态大 σ 与高条件数下系统性误伤参考实现自身（标准工程实测），残差既豁免
误伤又保留「劣化于 CPU 参考即不过」的约束。包内 golden32/golden64 不再参与判定，
降为自测参考件（包内文件与 schema 不变，供开发者比对参考）。

## 报告字段

| 字段 | 取值与含义 |
| --- | --- |
| `numeric` | PASS/FAIL，数值判定结论 |
| `residual` | 判定主体：`ratio`（本 case 残差）、`threshold`（阈值）、`formula`（阈值公式）、`ratio_cpu`/`ratio_cpu_mean`（阈值输入）、`eps`（归一基准 2^-24）、`pass`；`ran=false` 表示残差未运行（证据问题） |
| `formal` | 恒 `PENDING_RULING`：正式裁定待任务方出具，之前不出正式通过 |
| `flags` | 附加标记位，当前恒空 |
| `expectation[].status` | 数值 PASS / 数值 FAIL / 证据不足 / 待裁。证据不足 ≠ 失败：该项没有可判的输入（未生成 case、缺 ratio_cpu 基线等） |
| 族级结论 | 期望集先于结果存在；任一必测项证据不足则族级不判通过，补齐后重新运行即可 |

## 与自测的关系

包内 `verify_accuracy.py` 是同一判定的渲染副本，供开发者自测；其输出不构成验收
证据。副本与本 skill 实现不一致时报告记告警，判定照常以本 skill 实现为准。

## 固化参考值入口

任务包与共享 harness 入口按 case_id 读取冻结的 ratio_cpu、状态和对应均值；
批量同时读取 sample_map，不以现场重算值替代。入口要求完整冻结字段，缺失时拒绝
判定；上文底层判据对缺 mean 的兼容行为不意味着不完整任务包可以受理。

## 性能结论

性能入口见 [performance-acceptance.md](performance-acceptance.md)：逐 case 用
`msprof op` 采集目标 kernel，取单次调用算术平均，与冻结 GPU `avg_ms / 0.35` 比较。
`performance_verdict` 为 PASS、FAIL 或 INSUFFICIENT；漏采、缺基线或运行条件不符
保留 INSUFFICIENT，不从期望集删掉。`overall_acceptance=NOT_DETERMINED` 表示
性能结论不代替精度、确定性与总体验收结论。
旧 `perf.json` 参考比值仅展示（`required=false`），不进入族级必测统计；
正式性能门禁项仍参与统计，缺证据仍阻断。
