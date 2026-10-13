# {op} 任务包自测说明（LU/QR 批量算子·纯脚本包·A0 抽样）

本包供开发者自测，包内检查脚本的输出是自测参考，
不构成验收证据；正式验收结论由验收方使用其自带实现出具。

{scope_note}

本包为脚本形态，无 `cases/*.npz`、无 golden、有逐内容 `ratio_cpu` 参考值
（index 内逐内容 k 值列表，无顶层 `ratio_cpu_mean`——LU/QR 四册任务书 §3.2.2
逐算子为「相对线 + 绝对地板」单式，批量层再逐矩阵取大）。开发者可以先参考
本包内的 `ratio_cpu` 进行「先造数后测」：批量 case 走 **A0 抽样**，每 case 只固化
`k = min(5, batch)` 个代表内容矩阵的槽位映射（`sample_map`），数组不落盘——
执行器/DUT 挂钩与检查侧都按同一 seed **现场构造现场使用**（同环境逐位一致），
不读取、不依赖任何造数目录。

精度用例之外，本包另含 **{n_info} 例批量 info 契约用例**（`case_purpose == "info"`，
条目在 `cases/index.json`）：从中等规模精度用例派生，其中代表内容 0 与 1（k<2 时
仅内容 0）被构造为奇异（第 k 阶零主元），其余内容保持非奇异——两族均验证
infoArray 逐矩阵独立写入（期望 `infoArray[槽位] == k_expected[内容]`，LAPACK 约定：
奇异内容记首个零主元阶 k，非奇异内容记 0；k_expected 经 `sample_map` 展开即全批期望）。
info 契约用例只比 info，不进数值精度判定。

## 包内容

| 文件 | 用途 |
| --- | --- |
| `canonical_cases.json` | 本包用例的规范清单（本包收录的精度用例 {n_cases} 例；info 契约用例不入此清单，由 `gen_data.py` 同规则现场派生） |
| `cases/index.json` | 用例清单：参数、seed、`materialize: "gen"`、`sample_map` 槽位映射与逐内容 `ratio_cpu` 参考 k 值列表（精度判定只读包内冻结参考值，不现场重算替代）；含 {n_info} 例 `case_purpose: "info"` 的 info 契约用例（`k_expected` 入条目）。磁盘形态可为明文 `cases/index.json`、gzip `cases/index.json.gz` 或切分件 `cases/index.json.gz.partNNN`（单件 ≤ 10 MiB）。明文直接读取；完整 gzip 直接解压；切分件按名序拼接后解压 |
| `gen_data.py` | 数据构造脚本（代表内容构造、槽位映射派生与整批展开同用这一份） |
| `verify_accuracy.py` | 精度检查（A0 两层：先余槽 bit-wise 一致性，后代表槽逐内容一段式残差判定；判定输入现场重造）+ info 契约判定（{n_info} 例，只比 info） |
| `verify_perf.py` | 性能对照（与 GPU 参考耗时逐 case 比值，GPU 数据为 CUDA cuSolver 实测） |
| `perf_baseline.json` | 性能参考耗时（CUDA cuSolver 实测摘录） |
| `sim_dut.py` | 模拟被测输出生成器（形态统一保留；批量 A0 的流程演练见第 2 步说明） |
| `manifest.json` | 环境版本与内容摘要（请勿手工修改） |

输入填充为唯一函数 `fillRandom`：`A[i] = (rand()%10000)/100 - 50` → `U[-50,50)`，
复数每元素先 `.x` 后 `.y` 各一次独立 `rand()`；无对角占优构造。随机输入不保证非奇异，CPU 准备链失败时按包内
参考状态报告证据不足。有意构造的奇异用例只用于 info 输出检查。

## 前置条件

python3、numpy、scipy。版本见 `manifest.json` 的 `env`；实际加载的数值库版本与
线程数见包内 index 的 `reference_environment`。只匹配 NumPy/SciPy 版本不足以
保证逐位可复现。生成与检查全在 CPU 上进行，判定参考值始终读取包内冻结值。

## 自测步骤（现场构造，先造数后测，可选，如果测试数据和提供的参考数据存在较大出入可以提供相关说明）

1. 生成并核对用例索引（在包目录内执行；只产 `cases/index.json`，不落数组）：

   ```bash
   python3 gen_data.py --canonical canonical_cases.json --out selfcheck --select all
   ```

   产出的 `selfcheck/cases/index.json` 记录每 case 的 `sample_map`（内容 → 槽位
   映射，`rep_slot` 为该内容的代表槽），并含 {n_info} 例 info 契约用例
   （`k_expected` 入条目）。其中 `ratio_cpu` 及参考状态为空占位，不重新计算
   CPU 残差参考值；判定参考值始终读取任务包原有的冻结 index。

2. 接入被测：执行器/DUT 挂钩按 `sample_map` 用包内 `gen_data.py` 现场构造整批
   输入（k 个代表内容铺满 batch 个槽位），逐段喂入被测接口。构造方式（包目录内）：

   ```python
   import json, gen_data as g
   case = json.load(open("canonical_cases.json"))["cases"][0]
   contents = g.build_batched_contents(case)                     # k 个代表内容（含 golden）
   smap = g.derive_sample_map(case["seed"], case["batch"])       # 与 index 固化的 sample_map 逐项一致
   full = g.expand_sampled_rows(contents, smap, 0, case["batch"])  # 整批输入 (batch, n, ·)
   ```

   代表内容数组键随算子：

   - `*getriBatched`：DUT 输入为 `LU32/ipiv`（准备阶段已 `getrf` 的打包 LU 与
     1 基主元，不计入目标接口耗时）；`A64/A32` 保留原矩阵供参考；输出为逆；
   - `*matinvBatched`：DUT 输入仅 `A32`（n<32，接口内部完成 getrf+getri）；
     输出为逆。

   info 契约用例（`case_purpose == "info"`）的输入不同——奇异内容已就地改造，
   用条目自带 `k_expected` 构造：

   ```python
   import glob, gzip, json
   blob = b"".join(open(p, "rb").read() for p in sorted(glob.glob("cases/index.json.gz*")))
   entry = [c for c in json.loads(gzip.decompress(blob))["cases"]
            if c.get("case_purpose") == "info"][0]
   arrays = g.build_batched_info_arrays(entry)                   # 混合输入（含奇异内容，构造性自检 fail-closed）
   smap = g.derive_sample_map(entry["seed"], entry["batch"])
   full = g.expand_sampled_rows(arrays, smap, 0, entry["batch"])
   ```

   被测逐 case 产 `dut_out/<case_id>.npz`，三个键：

   - `out32`：`(batch, n, n)` 三维，batch=1 也不得压掉批维（逐矩阵连续存储，列主序
     `n×n`）；info 契约用例的 `out32` 不参与比对，但形状仍须合法（结构检查不豁免）；
   - `info`：`(batch,)` int32 infoArray（逐矩阵 LAPACK 约定，batch=1 写 `(1,)`，
     不得写成标量）；
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
   `out32` + info 逐位比对），后代表槽逐内容一段式 LAPACK 残差判定；序号域为
   内容下标 0..k-1，无逐元素直审层）。残差阈值逐矩阵 = max(5·`ratio_cpu_i`, 地板)，
   `*getriBatched` 地板 0.05、`*matinvBatched` 地板 0.5（CGET03 残差式
   `‖I−C·A‖₁/(n‖A‖₁‖C‖₁ε)`）；逐内容 `ratio_cpu_i` 从包内冻结 index 读取；
   info 契约用例只比 info——把 `k_expected` 经 `sample_map` 展开到全批槽位逐槽核对
   （任一槽失配即 FAIL，`fail_count` 计失配内容数、`first_fail_index` 报最小失配槽位）。
   报告给出 `fail_count`、`first_fail_index`、`worst_index` 与最差内容摘要；
   `--jobs N` 把逐内容判定切成多进程分块（结果与串行逐位相同）。
   合计行区分「精度 X + info 契约 Y」两类条数。
   退 0 = 全部数值通过；退 1 = 存在数值未通过或证据不足（缺被测输出、现场重造
   失败都记证据不足，读报告 summary 区分）；退 2 = 用例清单读不出或参数错——
   **退 2 时报告文件不落盘**，外层脚本不要无条件读报告。

4. 性能对照（可选，需你自测的逐 case 耗时 JSON）：

   ```bash
   python3 verify_perf.py --package . --dut-perf my_perf.json --report perf_report.json
   ```

   输出为逐 case 比值（被测 / 参考），正式性能达标判据按任务书执行
   （T_NPU ≤ T_GPU数据 / 0.35）。性能自测按任务书原规格 batch 整批调用——第 2 步的整批
   展开（`expand_sampled_rows`）即你的调用形状。

## 重新生成用例索引

```bash
python3 gen_data.py --canonical canonical_cases.json --out regen --select all
```

输入和槽位映射按冻结清单现场重造；逐内容 `ratio_cpu` 始终读取包内冻结 index，
不以重造结果替换。`selfcheck`/`regen` 目录供查看生成结果，不作为判定参考来源。

## 结论含义

- 数值通过 ≠ 正式验收通过：包内检查不构成正式验收结论，报告中 `formal` 字段
  恒为 `PENDING_RULING`（正式结论由验收方出具）；`flags` 恒空（数值结论不带条款标注）。
- 单侧未通过先查 `status` 与 `info`：执行失败与数值失败在报告中分开计；
  一致性失配（同内容槽位输出不逐位相等）按数值未通过计，`a0_mismatches`
  给出逐槽证据。
- info 契约未通过：infoArray 分型报逐槽失配证据（`diagnostics.
  info_mismatches`，含槽位号/内容号/期望/实际）；与数值精度是两个独立结论，
  互不替代。