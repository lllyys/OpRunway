# {op} 任务包自测说明（纯脚本包）

本包供开发者自测，包内检查脚本的输出是自测参考，
不构成验收证据；正式验收结论由验收方使用其自带实现出具。

本包为脚本形态，无 `cases/*.npz`、无 golden、有
`ratio_cpu` /`ratio_cpu_mean`参考值数组。开发者可以直接参考本包内的`ratio_cpu`和`ratio_cpu_mean`,数据来源也可以参考下面说明进行「先造数后测」：先在你的环境用包内脚本造数，再执行被测，最后
检查。检查脚本当前判定时会**自行现场重生成**同一输入并计算参考值（同环境逐位一致），
不读取、不依赖你的造数目录。

除精度用例外，包内另含 **info 契约用例**（index 条目 `case_purpose: "info"`）：
每算子 {n_info} 例，构造非正定 / 奇异因子 / 非法参数输入，只检查被测 `info` 是否
等于条目的 `k_expected`——不做残差比对、不进精度结论（info 契约与数值精度各出
独立结论）。

## 包内容

| 文件 | 用途 |
| --- | --- |
| `canonical_cases.json` | 本包用例的规范清单（本算子精度用例全量清单 {n_cases} 例） |
| `cases/index.json` | 用例清单：参数、seed、`materialize: "gen"`（包内无数组）、逐 case `ratio_cpu` 参考值（CPU 参考实现的残差比，发布参考；精度判定时以检查脚本现场重算值为准，同环境逐位一致）与顶层 `ratio_cpu_mean` 固化均值（全部精度用例 `ratio_cpu` 的算术平均，判定时直接读取包内固化值、零重算）；`case_purpose: "info"` 条目为 info 契约用例（无 golden，不带 ratio_cpu，带 `k_expected`） |
| `gen_data.py` | 数据构造脚本（第 0 步造数与检查侧现场重生成同用这一份） |
| `verify_accuracy.py` | 精度检查（直接计算 LAPACK 残差并对阈值判定；判定输入现场重生成） |
| `verify_perf.py` | 性能对照（与 GPU 参考耗时逐 case 比值，GPU 数据为 CUDA cuSolver 实测） |
| `perf_baseline.json` | 性能参考耗时（CUDA cuSolver 实测摘录） |
| `sim_dut.py` | 模拟被测输出生成器，仅用于流程演练 |
| `manifest.json` | 环境版本与内容摘要（请勿手工修改） |

## 前置条件

python3、numpy、scipy（生成本包时的确切版本在 `manifest.json` 的 `env` 字段，
环境一致可获得逐位可复现的结果）。生成与检查全在 CPU 上进行。

## 自测步骤（先造数后测，可选，如果测试数据和提供的参考数据存在较大出入可以提供相关说明）

1. 第 0 步造数（在包目录内执行；`--select all` 即物化清单里的全部 {n_cases} 个
   精度用例，并一并派生 {n_info} 个 info 契约用例）：

   ```bash
   python3 gen_data.py --canonical canonical_cases.json --out data --select all
   ```

   精度用例的输入与参考输出落 `data/cases/*.npz`（`A64/A32[/B64/B32]/golden64/
   golden32`，矩阵形状 `(n, n)`，右端 `(n, nrhs)`；golden 仅作自测参考，不参与
   判定）；info 契约用例的 npz 只有输入数组（无 golden，构造时已当场自检
   `info == k_expected`）。造数阶段不产 `ratio_cpu` 参考值——检查脚本判定时会
   现场重算，不影响造数与自测。

2. 准备被测输出：你的执行器从 `data/cases/*.npz` 读输入，逐 case 产
   `dut_out/<case_id>.npz`，三个键：

   - `out32`：计算结果，与 `golden32` 同 dtype 同布局（potrf/potri 存储侧半三角、
     另侧置 0；potrs 整块解）；
   - `info`：整型标量，按 LAPACK 约定（0=成功，正值=第 k 阶主子式非正定）；
   - `status`：`ok` 或失败标识，非 `ok` 一律按执行失败计。

   info 契约用例同样产三键（`out32` 不参与比对，`info` 即判定对象）。只想演练
   流程时用模拟件代替（读第 0 步的 data 目录）：

   ```bash
   python3 sim_dut.py --package data --out dut_out
   ```

   负例演练加 `--perturb scale`（复数包可用 `--perturb conj`）——info 契约用例的
   负例会产错误 `info`（非 `k_expected`），可验证检查侧把契约项判 FAIL。

3. 精度检查（在包目录内执行；判定输入由脚本现场重生成，不读 `data` 目录）：

   ```bash
   python3 verify_accuracy.py --package . --dut-out dut_out --report self_report.json
   ```

   目标 case = `canonical_cases.json` 的全部条目，另按同一规则现场派生
   {n_info} 个 info 契约用例（合计行区分「精度 X + info 契约 Y」两类条数，
   info 项单独出结论，与精度项分开计数）。verify 直接计算 LAPACK 残差并对
   max(5·ratio_cpu, 3·ratio_cpu_mean) 判定（potri 为绝对线 0.1，即
   max(5·ratio_cpu, 0.1)；复数残差按复模一体判定，不拆实虚）：逐 case
   `ratio_cpu` 以检查脚本现场重算值为准，`ratio_cpu_mean` 直接读取 index 顶层
   固化均值（发包侧预计算，零重算）。golden 仅作自测参考，不参与判定。
   退 0 = 全部数值通过；退 1 = 存在数值未通过或证据不足（缺被测输出、现场重生
   成失败都记证据不足，读报告 summary 区分）；退 2 = 用例清单读不出或参数错——
   **退 2 时报告文件不落盘**，外层脚本不要无条件读报告。

4. 性能对照（可选，需你自测的逐 case 耗时 JSON）：

   ```bash
   python3 verify_perf.py --package . --dut-perf my_perf.json --report perf_report.json
   ```

   输出为逐 case 比值（被测 / 参考），正式性能达标判据按任务书执行
   （T_NPU ≤ T_GPU数据 / 0.35）。

## 重新生成数据

```bash
python3 gen_data.py --canonical canonical_cases.json --out regen --select all
```

同一 seed 与依赖版本下数组内容可复现。本包无冻结 npz：检查侧以判定时的现场
重生成为准，你的 `data` 目录只喂执行器与模拟件，不参与判定。

## 结论含义

- 数值通过 ≠ 正式验收通过：包内检查不构成正式验收结论，报告中 `formal` 字段
  恒为 `PENDING_RULING`（正式结论由验收方出具）；`flags` 恒空（数值结论不带条款标注）。
- 单侧未通过先查 `status` 与 `info`：执行失败与数值失败在报告中分开计。
