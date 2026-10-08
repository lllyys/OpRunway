---
name: repo-task-solver-accept
description: 消费 solver 任务包与被测输出，按一段式 LAPACK 残差判定出具逐 case 数值结论与族级汇总报告。当需要对 solver 算子任务包执行验收复核，或开发者要求核对自测结论时使用；生成任务包改用 repo-task-solver-case-gen。
---

# repo-task-solver-accept

判定标准、判定执行与结论出具全部在本 skill 内完成；任务包内的检查脚本只是自测辅助件，
其输出不构成验收证据。精度判定为一段式：逐 case 直接算 LAPACK 残差（DPOT01/02/03，
输入用实现实际输入 A32/B32）对阈值判；包内 golden 数组不参与判定，降为自测参考件。
当前支持 Cholesky 十算子：实数 spotrf、spotrs、spotri，复数 cpotrf、cpotrs、cpotri
（复数残差取复模与共轭转置），以及批量四算子 spotrfBatched、spotrsBatched、
cpotrfBatched、cpotrsBatched（逐矩阵残差判定，case 数值结论 = 全部矩阵通过）。

批量算子三点差异，其余流程与单矩阵相同：

- 批量数值结论恒带 flag `T8`（判定聚合方案暂定）：数值结果可展示，正式精度项在
  标准侧确认前一律记证据不足。
- 被测 `info` 按接口角色分型：potrfBatched 族交付 `(batch,)` int32 infoArray
  （batch=1 也不写成标量），potrsBatched 族交付标量（仅报参数错）。
- 纯脚本形态包（批量四算子后补包与单矩阵六算子 v2 补发包，包内不携带数据数组）：
  包通路对其接口精度项如实记证据不足，数值展示走流式通道或包内检查脚本的现场
  重生成流程。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| 任务包目录 | `repo-task-solver-case-gen` 的装包产物 | 含 `cases/`、`manifest.json` | 由用户给出 |
| 被测输出目录 | 每 case 一个 `<case_id>.npz` | 含 `out32`、`info`、`status` 三键 | 由用户给出 |
| 报告路径 | 汇总报告写入位置 | 可写文件路径 | 任务包目录旁 `report.json` |

## 主流程

| 阶段 | 命令 | 完成条件 |
| --- | --- | --- |
| A1 备被测输出 | 真实被测输出由开发者工程产出；仅做流程演练时 `python3 scripts/sim_dut.py --package <任务包目录> --out <被测输出目录>` | 目录内每个目标 case 各一个 npz |
| A2 判定与汇总 | `python3 scripts/accept_run.py --package <任务包目录> --dut-out <被测输出目录> --report <报告路径>` | 退 0，报告写出 |
| A3 读报告 | 报告顶层五键：`operator`、`expectation`、`flags`、`versions`、`声明边界`；字段含义见 [verdict-mechanism.md](references/verdict-mechanism.md) | `expectation` 每项状态可解释（见检查条件） |

## 流式全量运行

不落盘通道：用例现场生成、判定后即弃，适合全量或大 n（实测双册 n≤1024 共 448 case
零数组文件、spotrf 全量 197 条含 n=8192 约 6 分钟）：

```bash
python3 scripts/stream_check.py --canonical <canonical_cases.json> \
  --gen-dir <repo-task-solver-case-gen 的 scripts 目录> --report stream_report.json
```

可选 `--ops`、`--max-n`、`--perturb`（负例演练）、`--dump <case_id> --dump-dir <目录>`
（调试单 case 写出数组）。当前被测通道为模拟件；真实被测经三键适配器接入。
批量算子同通道批维透传，另有两个批量选项：`--jobs N` 把逐矩阵判定按矩阵区间切成
多进程分块（判定只读共享数组，结果与串行逐位相同；`accept_run.py` 同名选项同义），
`--perturb-index i` 只扰动第 i 个矩阵（验证单矩阵超限时整 case FAIL 且报告指认序号）。

## 检查条件

| 条件 | 表现 | 处理方式 |
| --- | --- | --- |
| 数值 PASS 与正式结论的分界 | 每项含数值状态；`formal` 恒为 `PENDING_RULING` | 残差阈值语义待任务方正式裁定前，不出具正式通过 |
| 期望项缺证据 | 状态为证据不足，族级不判通过 | 补齐对应输入后重新运行 A2；不缩小期望集 |
| 包数据摘要与 manifest 不符 | 对应项被阻断并写明原因 | 重新取得完整任务包；不手工改 manifest |
| 检查脚本副本与权威实现不一致 | 报告 `flags` 记告警，判定不受影响 | 以本 skill 内实现为准；提醒包的提供方重新装包 |
| 被测输出 `status` 非 `ok` | 该 case 记执行失败，不参与数值统计 | 区别于数值 FAIL；先解决执行问题再重新运行 |
| 批量被测 `info` 形状不符 | potrfBatched 族需 `(batch,)` int32 infoArray（batch=1 不标量化），potrsBatched 族需标量 | 按证据问题记 error，不判精度；修被测输出格式后重跑 |
| 批量结论恒带 `T8` | 批量 case 的 `flags` 含 `T8`（判定聚合方案暂定） | 数值可展示；正式精度项记证据不足，待标准侧确认后收束 |

## 产物

`report.json`：逐期望项状态（数值 PASS、数值 FAIL、证据不足、待裁）、检查明细、
环境版本与声明边界。期望集覆盖七类：接口精度、性能项、bufferSize、内存证据、
batched、确定性、`info 契约`（报告字段原文）；本 skill 未支持的类别如实记证据不足。
batched 项按算子分型：批量算子的报告写明数值可展示、正式精度按 `T8` 记证据不足，
并附覆盖缺口声明（六例是二维代表子集，非正定、INF/NAN 等类别未覆盖）。

## 参考资料

- [verdict-mechanism.md](references/verdict-mechanism.md)——判定机制与报告字段含义（A3 读报告时用）
- [perf-collection.md](references/perf-collection.md)——性能耗时采集协议与 `verify_perf` 输入格式（开发者问性能自测怎么采时给出）

## 范围之外

真实 NPU 执行与采样、性能正式门禁（任务书定稿机制：NPU msprof 实测 ≤ GPU参考/0.35，
NPU 实测件待开发者提供）、batched
正式精度结论（`T8` 待标准侧收束）、gels、QR 与 LU 与特征值族。
