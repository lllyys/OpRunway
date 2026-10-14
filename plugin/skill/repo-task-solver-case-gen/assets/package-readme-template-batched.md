# {op} 任务包自测说明（批量算子·纯脚本包·A0 抽样）

本包供开发者自测，包内检查脚本的输出是自测参考，
不构成验收证据；正式验收结论由验收方使用其自带实现出具。

本包不携带输入或 golden 数组。批量 case 使用 A0 抽样：每 case 构造
`k = min(5, batch)` 个代表内容，由固化的 `sample_map` 映射到整批槽位。
输入现场构造；判定按 case_id 读取包内固化的逐内容 `ratio_cpu`（CPU 残差参考值）、
状态与槽位加权 `ratio_cpu_mean`，不以本机重算值替代。

开发者和验收者可使用独立分发的共享 harness 构建、执行和留证；验收证据由验收者
自己运行产生。harness 不在任务包内，其当前接口支持范围以随工具的运行说明为准。

精度用例之外，本包另含 **{n_info} 例批量 info 契约混合用例**（
`case_purpose == "info"`，条目在 `cases/index.json`）：从中等规模精度用例派生，
其中 1~2 个代表内容矩阵被构造为非正定（其余保持正定）——`*potrfBatched` 族验证
infoArray 逐矩阵独立写入（期望 `infoArray[槽位] == k_expected[内容]`，LAPACK 约定：
非正定内容记首个非正定主子式阶 k，正定内容记 0；k_expected 经 `sample_map` 展开即
全批期望）；`*potrsBatched` 族的标量 info 仅报参数错（期望 `info == -1`，配 `uplo`
参数错探针）。info 契约用例只比 info，不进数值精度判定与精度均值。

## 包内容

| 文件 | 用途 |
| --- | --- |
| `canonical_cases.json` | 本包用例的规范清单（本算子精度用例全量清单 {n_cases} 例；info 契约用例不入此清单，由 `gen_data.py` 同规则现场派生） |
| `cases/index.json` | 用例清单：参数、seed、`materialize: "gen"`、`sample_map` 槽位映射与逐内容 `ratio_cpu` 参考 k 值列表（判定时读取包内固化值与状态）；精度条目另固化 case 级 `ratio_cpu_mean`（Σ(count_j·ratio_j)/batchSize 槽位加权均值，判定时直接读取包内固化值、零重算）；含 {n_info} 例 `case_purpose: "info"` 的 info 契约用例（`k_expected` 入条目）。**本文件以 gzip 压缩存储为 `index.json.gz`**（含 batch=1e6 级槽位映射，原文 250MB+），检查脚本与 harness 可直接读取 gzip；手工用标准 JSON 工具检查时可先执行 `gunzip cases/index.json.gz` |
| `gen_data.py` | 数据构造脚本（代表内容构造、槽位映射派生与整批展开同用这一份） |
| `verify_accuracy.py` | 精度检查（A0 两层：先余槽 bit-wise 一致性，后代表槽逐内容直接残差判定；判定输入现场重造）+ info 契约判定（{n_info} 例，只比 info） |
| `verify_perf.py` | 性能对照（与 GPU 参考耗时逐 case 比值，GPU 数据为 CUDA cuSolver 实测） |
| `perf_baseline.json` | 性能参考耗时（CUDA cuSolver 实测摘录） |
| `sim_dut.py` | 模拟被测输出生成器（形态统一保留；批量 A0 的流程演练见第 2 步说明） |
| `manifest.json` | 环境版本与内容摘要（请勿手工修改） |

精度用例的 A 矩阵在构造环节已保证正定（对角占优，或 `{gram_form} + n·I` 正定平移），
无需自行验证正定性；有意构造的非正定用例只用于 info 输出检查，不在此保证之内。

## 前置条件

python3、numpy、scipy（生成本包时的确切版本在 `manifest.json` 的 `env` 字段，
环境一致可获得逐位可复现的结果）。生成与检查全在 CPU 上进行。

## 自测步骤

1. 核对环境与参考值（在包目录内执行；只产 `cases/index.json`，不落数组）：

   ```bash
   python3 gen_data.py --canonical canonical_cases.json --out selfcheck --select all
   ```

   产出的 `selfcheck/cases/index.json` 记录每 case 的 `sample_map`（内容 → 槽位
   映射，`rep_slot` 为该内容的代表槽）与逐内容 `ratio_cpu` 参考 k 值列表，并含 {n_info}
   例 info 契约用例（`k_expected` 入条目）。缺判据支撑时脚本会提示跳过
   `ratio_cpu` 参考计算（`not_computed`）。该目录只用于检查输入构造；
   判定必须读取原包已回填的 index，不能拿此处未回填件替换。

2. 接入被测：执行器/DUT 挂钩按 `sample_map` 用包内 `gen_data.py` 现场构造整批
   输入（k 个代表内容铺满 batch 个槽位），逐段喂入被测接口。构造方式（包目录内）：

   ```python
   import json, gzip, pathlib, gen_data as g
   index = pathlib.Path("cases/index.json")
   with (index.open() if index.exists() else gzip.open(str(index) + ".gz", "rt")) as f:
       entries = json.load(f)["cases"]
   case = next(c for c in entries if c.get("case_purpose") != "info")
   contents = g.build_batched_contents(case)                     # k 个代表内容（A64/A32[/B64/B32]）
   smap = case["sample_map"]                                   # 消费包内固化映射
   full = g.expand_sampled_rows(contents, smap, 0, case["batch"])  # 整批输入 (batch, n, ·)
   ```

   info 契约用例（`case_purpose == "info"`）的输入不同——非正定内容已就地改造，
   用条目自带 `k_expected` 构造：

   ```python
   entry = next(c for c in entries if c.get("case_purpose") == "info")
   arrays = g.build_batched_info_arrays(entry)                   # 混合输入（含非正定内容，构造性自检 fail-closed）
   smap = entry["sample_map"]
   full = g.expand_sampled_rows(arrays, smap, 0, entry["batch"])
   ```

   被测逐 case 产 `dut_out/<case_id>.npz`，三个键：

   - `out32`：`(batch, n, cols)` 三维，batch=1 也不得压掉批维（potrfBatched
     逐矩阵存储侧半三角、另侧置 0；potrsBatched 整块解，nrhs=1）；info 契约
     用例的 `out32` 不参与比对，但形状仍须合法（结构检查不豁免）；
   - `info`：potrfBatched 族为 `(batch,)` int32 infoArray（逐矩阵 LAPACK 约定，
     batch=1 写 `(1,)`，不得写成标量）；potrsBatched 族为标量（仅报参数错）；
   - `status`：`ok` 或失败标识，非 `ok` 一律按执行失败计。

   **一致性契约（A0 判定第一层）**：`sample_map` 里同一内容的全部槽位，被测输出
   必须逐位相等（整批一次调用时同内容矩阵相同是接口语义的自然结果）——判定先比
   余槽 bit-wise 一致性，失配即数值未通过并报槽位号。

   流程演练：可对 `dut_out` 中个别槽位的输出人工注入扰动后复跑第 3 步检查，
   观察一致性失配与 info 契约的 FAIL 证据输出（槽位号/内容号/期望/实际）。

3. 精度检查（在包目录内执行；判定输入由脚本现场重造，不读任何造数目录）：

   ```bash
   python3 verify_accuracy.py --package . --dut-out dut_out --report self_report.json
   ```

   逐 case 两类判定：精度用例走 A0 两层（先余槽 bit-wise 一致性（同内容槽位
   `out32` + info 逐位比对），后代表槽逐内容直接计算 LAPACK 残差并对
   max(5·ratio_cpu, 3·ratio_cpu_mean) 判定（复数残差按复模一体判定，不拆实虚；
   序号域为内容下标 0..k-1））；golden 仅作自测参考，不参与判定。
   逐内容 `ratio_cpu`、状态和 `sample_map` 读取 index，`ratio_cpu_mean` 直接读取
   index 条目固化的槽位加权均值（发包侧预计算，零重算）；info 契约用例只比 info——potrfBatched 族把
   `k_expected` 经 `sample_map` 展开到全批槽位逐槽核对（任一槽失配即 FAIL，
   `fail_count` 计失配内容数、`first_fail_index` 报最小失配槽位），potrsBatched
   族标量直接比对。报告给出 `fail_count`、`first_fail_index`、`worst_index` 与
   最差内容摘要；`--jobs N` 把逐内容判定切成多进程分块（结果与串行逐位相同）。
   合计行区分「精度 X + info 契约 Y」两类条数。
   退 0 = 全部数值通过；退 1 = 存在数值未通过或证据不足（缺被测输出、现场重造
   失败都记证据不足，读报告 summary 区分）；退 2 = 用例清单或固化参考值不可用、参考值基准不符或参数错——
   **退 2 时报告文件不落盘**，外层脚本不要无条件读报告。

4. 性能对照（可选，需你自测的逐 case 耗时 JSON）：

   ```bash
   python3 verify_perf.py --package . --dut-perf my_perf.json --report perf_report.json
   ```

   输出为逐 case 比值（被测 / 参考），正式性能达标判据按任务书执行
   （T_NPU ≤ T_GPU数据 / 0.35）。性能自测按任务书原规格 batch 整批调用——第 2 步的整批
   展开（`expand_sampled_rows`）即你的调用形状。

## 输入构造诊断

```bash
python3 gen_data.py --canonical canonical_cases.json --out regen --select all
```

输入按固定 seed 现场构造，跨环境末位差异可以作为诊断记录。`selfcheck`/`regen`
目录不参与判定；检查侧使用原包冻结的参考值与映射，不把诊断值写回任务包。

## 结论含义

- 数值通过 ≠ 正式验收通过：包内检查不构成正式验收结论，报告中 `formal` 字段
  恒为 `PENDING_RULING`（正式结论由验收方出具）；`flags` 恒空（数值结论不带条款标注）。
- 单侧未通过先查 `status` 与 `info`：执行失败与数值失败在报告中分开计；
  一致性失配（同内容槽位输出不逐位相等）按数值未通过计，`a0_mismatches`
  给出逐槽证据。
- info 契约未通过：infoArray 分型报逐槽失配证据（`diagnostics.
  info_mismatches`，含槽位号/内容号/期望/实际），标量分型报 `expected`/`actual`；
  与数值精度是两个独立结论，互不替代。
