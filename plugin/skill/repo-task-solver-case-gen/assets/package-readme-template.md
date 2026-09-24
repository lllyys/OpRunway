# {op} 任务包自测说明

本包供开发者自测与验收侧同包消费。包内检查脚本的输出是自测参考，
不构成验收证据；验收判定由 `repo-task-solver-accept` 用其自带实现出具。

## 包内容

| 文件 | 用途 |
| --- | --- |
| `cases/index.json` | 用例清单：参数、seed、ratio_cpu 参考值、npz 路径 |
| `cases/<case_id>.npz` | 冻结输入与参考输出（`A64/A32[/B64/B32]/golden64/golden32`） |
| `gen_data.py` + `canonical_cases.json` | 数据构造脚本与本包用例的规范清单，可重新生成同一批数据 |
| `verify_accuracy.py` | 精度检查（三层：直审 + LAPACK 残差复核） |
| `verify_perf.py` | 性能对照（与竞品参考耗时逐 case 比值） |
| `perf_baseline.json` | 性能参考耗时（竞品实测摘录） |
| `sim_dut.py` | 模拟被测输出生成器，仅用于流程演练 |
| `manifest.json` | 环境版本与内容摘要（请勿手工修改） |

精度用例的 A 矩阵在构造环节已保证正定（对角占优，或 `B·Bᴴ + n·I` 正定平移），
无需自行验证正定性；有意构造的非正定用例只用于 info 输出检查，不在此保证之内。

## 前置条件

python3、numpy、scipy（生成本包时的确切版本在 `manifest.json` 的 `env` 字段，
环境一致可获得逐位可复现的结果）。生成与检查全在 CPU 上进行。

## 自测步骤

1. 准备被测输出：你的工程对每个 case 产一个 `dut_out/<case_id>.npz`，
   含 `out32`（计算结果）、`info`（整型状态）、`status`（`ok` 或失败标识）。
   `out32` 与包内 `golden32` 同 dtype 同布局（potrf/potri 存储侧半三角、另侧置 0；
   potrs 整块解）；`info` 按 LAPACK 约定（0=成功，正值=第 k 阶主子式非正定）；
   `status` 非 `ok` 一律按执行失败计。只想演练流程时用模拟件代替：

   ```bash
   python3 sim_dut.py --package . --out dut_out
   ```

   负例演练加 `--perturb scale`（复数包可用 `--perturb conj`）。

2. 精度检查：

   ```bash
   python3 verify_accuracy.py --package . --dut-out dut_out --report self_report.json
   ```

   目标 case = `cases/index.json` 的全部条目。退 0 = 全部数值通过；
   退 1 = 存在数值未通过或证据不足（缺被测输出目录、缺单个 npz 都记证据不足，
   读报告 summary 区分）；退 2 = 用例清单读不出或参数错——**退 2 时报告文件
   不落盘**，外层脚本不要无条件读报告。

3. 性能对照（可选，需你自测的逐 case 耗时 JSON）：

   ```bash
   python3 verify_perf.py --package . --dut-perf my_perf.json --report perf_report.json
   ```

   输出为逐 case 比值（被测 / 参考），仅作参考；正式性能达标按任务书的
   T_A100 机制另行执行。

## 重新生成数据

```bash
python3 gen_data.py --canonical canonical_cases.json --out regen --select all
```

同一 seed 与依赖版本下数组内容可复现；跨环境重生成可能有末位差异，
以包内冻结的 npz 为准。

## 结论含义

- 数值通过 ≠ 正式验收通过：精度阈值语义待任务方最终确认，报告中
  `formal` 字段恒为 `PENDING_RULING`；`flags` 里的 `T3`/`T4`/`T7` 是待确认的
  阈值条款编号，不影响本次数值结论。
- 单侧未通过先查 `status` 与 `info`：执行失败与数值失败在报告中分开计。
