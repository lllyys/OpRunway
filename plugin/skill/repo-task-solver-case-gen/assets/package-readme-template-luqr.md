# {op} 任务包自测说明（LU/QR 单算子·纯脚本包）

本包供开发者自测，包内检查脚本的输出是自测参考，
不构成验收证据；正式验收结论由验收方使用其自带实现出具。

本包为脚本形态，无 `cases/*.npz`、无 golden、有逐 case `ratio_cpu` 参考值
（index 内，无顶层 `ratio_cpu_mean`——LU/QR 四册任务书 §3.2.2 逐算子为
「相对线 + 绝对地板」单式，无均值第二支）。开发者可以先参考本包内的 `ratio_cpu`
进行「先造数后测」：先在你的环境用包内脚本造数，再执行被测，最后检查。
检查脚本当前判定时会**自行现场重生成**同一输入并计算参考值（同环境逐位一致），
不读取、不依赖你的造数目录。

精度用例之外，本包另含 **info 契约用例**（index 条目 `case_purpose: "info"`）：
每算子 {n_info} 例。`*getrf`/`*getri` 构造含零主元的奇异输入（`*getrf` 把
`U(k,k)` 置 0；`*getri` 把输入 LU 的 `U(k,k)` 置 0），验证被测 `info` 是否等于
条目的 `k_expected`（正值，第 k 阶零主元）；QR 四算子构造非法枚举参数输入，
期望 `info == -1`。info 契约用例只比 info，不做残差比对、不进精度结论
（info 契约与数值精度各出独立结论）。

## 包内容

| 文件 | 用途 |
| --- | --- |
| `canonical_cases.json` | 本包用例的规范清单（本算子精度用例全量清单 {n_cases} 例） |
| `cases/index.json` | 用例清单：参数、seed、`materialize: "gen"`（包内无数组）、逐 case `ratio_cpu` 参考值（CPU 参考实现的残差比，精度判定只读包内冻结参考值，不现场重算替代）；`case_purpose: "info"` 条目为 info 契约用例（无 golden，不带 ratio_cpu，带 `k_expected`） |
| `gen_data.py` | 数据构造脚本（第 0 步造数与检查侧现场重生成同用这一份） |
| `verify_accuracy.py` | 精度检查（一段式 LAPACK 残差判定；判定输入现场重生成） |
| `verify_perf.py` | 性能对照（与 GPU 参考耗时逐 case 比值，GPU 数据为 CUDA cuSolver 实测） |
| `perf_baseline.json` | 性能参考耗时（CUDA cuSolver 实测摘录） |
| `sim_dut.py` | 模拟被测输出生成器，仅用于流程演练 |
| `manifest.json` | 环境版本与内容摘要（请勿手工修改） |

## 前置条件

python3、numpy、scipy（生成本包时的确切版本在 `manifest.json` 的 `env` 字段，
环境一致可获得逐位可复现的结果）。生成与检查全在 CPU 上进行。

输入填充为唯一函数 `fillRandom`（任务书附 bench `.cu` 口径）：
`A[i] = (rand()%10000)/100 - 50` → `U[-50,50)`，复数每元素先 `.x` 后 `.y` 各一次
独立 `rand()`；无对角占优、无 Hermitian 构造。

## 自测步骤（先造数后测，可选，如果测试数据和提供的参考数据存在较大出入可以提供相关说明）

1. 第 0 步造数（在包目录内执行；`--select all` 即物化清单里的全部 {n_cases} 个
   精度用例，并一并派生 {n_info} 个 info 契约用例）：

   ```bash
   python3 gen_data.py --canonical canonical_cases.json --out data --select all
   ```

   精度用例的输入与参考输出落 `data/cases/*.npz`，数组键随算子而定：

   - `*getrf`：`A64/A32`（无 golden——分解因子非唯一，主判走还原 `L·U+ipiv` vs `A`）；
   - `*getri`：`A64/A32/LU64/LU32/ipiv/golden64/golden32`（DUT 输入为打包 LU +
     1 基 `ipiv`，`golden` 为逆，唯一；主判走 `‖I−C·A‖₁` 型 S/CGET03）；
   - `*geqrf`：`A64/A32/golden64/golden32/tau64/tau32`（`golden` 为打包反射子 AF，
     非唯一，主判走还原 `Q·R` vs `A`）；
   - `*gqr`（orgqr/ungqr）：`A64/A32/tau64/tau32/golden64/golden32`（`A` 为打包反射子
     输入，`golden` 为 Q）；
   - `*mqr`（ormqr/unmqr）：`A64/A32/tau64/tau32/C64/C32/q64/q32/golden64/golden32`
     （`q` 为显式正交/酉矩阵，供残差兜底）；
   - `*gels`：`A64/A32/B64/B32/golden64/golden32`（`golden` 为最小二乘解 X，前 n 行）。

   矩阵形状随算子：`*getrf/*geqrf` 为 `(m, n)`，`*getri` 为 `(n,n)`，`*gels` 左端
   `(m,n)`、右端 `(m,nrhs)`，`*gqr` 输入反射子 `(m,n)` 输出 Q `(m,n)`，
   `*mqr` 反射子 `(nq,k)`、`C` 为 `(m,n)`。info 契约用例的 npz 只有输入数组
   （无 golden，构造时已当场自检 `info == k_expected`）。造数阶段不产 `ratio_cpu`
   参考值；检查脚本从任务包原有 index 读取冻结值，不影响造数与自测。

2. 准备被测输出：你的执行器从 `data/cases/*.npz` 读输入，逐 case 产
   `dut_out/<case_id>.npz`，三个公共键及算子附加输出：

   - `out32`：计算结果，与输入同精度的结果数组；`*getrf`/`*geqrf` 输出打包
     因子矩阵 `(m,n)`（附加输出要求见下），`*getri` 为逆 `(n,n)`，
     `*gqr` 为 Q `(m,n)`，`*mqr` 为 `op(Q)·C`（或 `C·op(Q)`）`(m,n)`，`*gels` 为解
     `(n,nrhs)`；
   - `info`：整型标量，按 LAPACK 约定（0=成功，`*getrf`/`*getri` 正值=第 k 阶零主元）；
   - `status`：`ok` 或失败标识，非 `ok` 一律按执行失败计。

   精度用例的附加输出：`*getrf` 必须带 `ipiv`（1 基整型主元，长度 min(m,n)）；
   `*geqrf` 必须带 `tau`（被测反射子系数，长度 min(m,n)，dtype 与输出一致）。
   判定侧使用这些输出还原矩阵或构造 Q。

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
   info 项单独出结论，与精度项分开计数）。精度判定为**一段式 LAPACK 残差**：
   每个精度用例直接算残差比 `ratio = 残差 / (分母·ε)`，对阈值判，无逐元素直审层。
   残差式与阈值逐算子（S/CGET01·03、S/CQRT01·02·03·16·17）：
   `*getrf` 还原 `L·U+ipiv` vs `A`，max(5·`ratio_cpu`, 实 0.3 / 复 0.5)；
   `*getri` `‖I−C·A‖₁/(n‖A‖₁‖C‖₁ε)`，max(5·`ratio_cpu`, 0.05)；
   `*geqrf` 还原 `Q·R` vs `A` 与 `*mqr` 同式 max(5·`ratio_cpu`, 0.1)；
   `*gqr` max(5·`ratio_cpu`, 1.5)；
   `*gels` 方阵 max(5·`ratio_cpu`, 0.015)、超定 10·`ratio_opt_cpu`（无绝对地板）。
   gels 方阵读取冻结 `ratio_cpu`，超定读取冻结 `ratio_opt_cpu`；
   缺失或无效时停止判定，不能现场重算替代。
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
- info 契约未通过：`*getrf`/`*getri` 报 `expected`（k_expected）/`actual`（被测
  info）；QR 四算子期望恒为 -1，与数值精度是两个独立结论，互不替代。