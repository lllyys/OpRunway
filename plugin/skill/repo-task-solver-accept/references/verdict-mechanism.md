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

## LU/QR 十六接口（LQ-20/21/22）

CPU 参考链与被测输出均以算子实际输入的低精度数据为残差参考，运算统一升为
float64/complex128；不使用量化前的 A64/B64/C64。getrf、geqrf、gels 取
A32（gels 另取 B32）；getri/getriBatched 从实际 LU32 与 ipiv 升精度还原 A；
matinvBatched 取 A32；mqr 从实际 A32、tau32 升精度构造 Q，配合 C32；
gqr 对输出 Q 升精度后计算正交性残差。升精度保留低精度数值，不恢复量化前的值。

冻结的 ratio_cpu/ratio_opt_cpu 也必须按同一口径计算。旧 LU/QR 回填版本
`a32base-lqr1` 的部分支路仍用原始64位值，不能仅凭 `ratio_basis=A32-f64`
认定其同口径；使用前须按修正后的参考链重新冻结。LU/QR 精度条目须有
`reference_contract: "luqr-input32-v1"`，缺失时停止，不能只补标记。
现场验收不自动重算或替换冻结值。

判定卡独立成模块 `criteria/cards_luqr.py`（16 接口），经统一取卡分发层
`render_verify._spec_card` 与 Cholesky 卡汇合（`accepts(op)` 分流，Cholesky 零漂移）。

| 接口 | 残差计算对象 | 残差类型与阈值（r=ratio_cpu，rᵢ=逐内容参考值） |
| --- | --- | --- |
| sgetrf/cgetrf | 被测 LU 与 ipiv 逆序回放还原矩阵，与实际输入 A32 比较 | DGET01：max（5r，0.3 实 / 0.5 复） |
| sgetri/cgetri | 从输入 LU32 与 ipiv 还原 A，检查被测逆 C 的 I−C·A | DGET03：max(5r, 0.05) |
| sgetribatched/cgetribatched | 逐矩阵从输入 LU32 与 ipiv 还原 A，检查 I−C·A | DGET03：max(5rᵢ, 0.05) |
| smatinvbatched/cmatinvbatched | 逐矩阵以实际输入 A32 检查被测逆 C 的 I−C·A | DGET03：max(5rᵢ, 0.5) |
| sgeqrf/cgeqrf | 从被测反射子与 tau 构造完整 Q，检查 R−Qᴴ·A32 | DQRT01：max(5r, 0.1) |
| sorgqr/cungqr | 检查被测 Q 的 I−Qᴴ·Q；当前卡仅检查正交性／酉性 | DQRT02：max(5r, 1.5) |
| sormqr/cunmqr | 按 side/trans 以显式 Q 与 C32 相乘，与被测结果比较 | DQRT03：max(5r, 0.1) |
| sgels/cgels | 方阵检查 B32−A32·X；超定检查残差 R=B32−A32·X 的 Rᴴ·A32 | DQRT16：max(5r, 0.015)；DQRT17：10·ratio_opt_cpu；实／复同式 |

各残差按矩阵规模、范数和 EPS32=2^-24 归一化，计算时实际单精度输入与输出升到
float64／complex128，复数使用复模与共轭转置。golden 不参与判定，无混合容差或
matched_ratio 第二层；阈值所需 CPU 参考值从任务包冻结 index 读取。

gels 方阵读取 ratio_cpu，超定读取 ratio_opt_cpu，均要求有效的冻结参考值。
缺字段、非有限值或负值属于证据不足，不以现场计算的参考值补洞。

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
