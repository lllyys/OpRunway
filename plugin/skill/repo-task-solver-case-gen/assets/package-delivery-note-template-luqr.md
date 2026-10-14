# {delivery_group} 统一交付说明

（交付组装包步骤消费本模板：替换全部 {placeholder} 后随交付树落盘为
DELIVERY_NOTE.md；本模板不进任何单包，包内自测说明见 package-readme-template-luqr*。）

{delivery_note_preamble}

| 任务书 | 包 |
| --- | --- |
{taskbook_packages_table}

## 包的统一形态（{package_count} 包一致）

1. **纯脚本包（零数组数据）**：包内不带任何数据数组（单矩阵包 KB 级；批量包
   另含 A0 抽样槽位映射 sample_map——判定余槽一致性的必需数据）。用例数据由包内
   脚本现场生成——先造数、后测试：

   ```bash
   python3 gen_data.py --canonical canonical_cases.json --out data --select all
   ```

   你的执行器从 `data/cases/*.npz` 读输入；`verify_accuracy.py` 判定时自行按同一
   配方重新生成输入与参考输出（同一环境下与你造的数逐位相同），不依赖你的
   `data` 目录。
2. **verify 双件**：`verify_accuracy.py` 验精度，`verify_perf.py` 验性能（判据取自
   包内 `perf_baseline.json` 的 GPU 参考耗时，采集按《性能自测采集说明》用
   `msprof op`）。
3. **自测配套件**：`gen_data.py`、`sim_dut.py`（模拟被测输出，供接入自查）、
   `README.md`（接入步骤）。

## 固化口径（{package_count} 包一致，lq-v1）

1. **ratio_cpu 参考值（唯一阈值支）**：逐 case 由 CPU 同精度参考链预计算并固化在
   `cases/index.json`（字段 `ratio_cpu`），判定只消费、不重算。**LU/QR 四册任务书
   §3.2.2 逐算子为「相对线 × ratio + 绝对地板」单式，无均值第二支**——index 顶层
   不写 `ratio_cpu_mean`。
2. **逐算子阈值（任务书 §3.2.2）**：{threshold_table}
3. **批量 A0 抽样**：批量包每 case 只固化 k=min(5,batch) 个代表内容矩阵与
   `sample_map`（槽位映射）；判定先核余槽 bit-wise 一致性、再对代表槽逐内容
   一段式 LAPACK 残差判定（LU 求逆类「奇异矩阵」按代表内容解释）。
4. **info 契约用例**：单矩阵算子（`getrf`/`getri`/QR 四算子）每算子 3 例；
   批量算子（`getriBatched`/`matinvBatched`）每算子 1 例（逐内容 k 值列表）。
   `*getrf`/`*getri` 构造含零主元的奇异输入，期望 `info` = 第 k 阶零主元（正值）；
   `getriBatched`/`matinvBatched` 期望为逐内容 k 值列表；QR 四算子构造非法枚举
   参数输入，期望 `info == -1`。约定不符即数值 FAIL（info 契约与数值精度各出独立结论）。
5. **确定性**：任务书要求合法用例重复执行结果 bit-wise 一致；该证据由验收侧
   复跑出具，不随包交付。

## 已知边界（交付即声明）

- 数值通过 ≠ 正式验收通过（`formal` 恒 `PENDING_RULING`）；复数算子实部、虚部
  分别判定。
- **性能证据缺口**：两个 `getriBatched` 册 GPU 基线不完整，且 `getriBatched` 全量、
  `matinvBatched` 2 条、`sgels`/`cgels` 各 137 条 bench 条目无 `perf`（无 T_GPU数据）
  → 这些 case 的性能门禁 `T_NPU ≤ T_GPU数据 / 0.35` **不可评**，按「缺 perf 标性能
  证据不足，不 gate」处置（待任务侧补跑或裁定，U-8/U-9）。
- **入参生成口径**：包内输入按任务书附 bench `.cu` 的 `fillRandom`（`U[-50,50)`，
  无对角占优；复数 `.x`/`.y` 各自独立）构造，与任务书 §3.5 文字规则（`[-5,5]` 均匀
  / 正态 + 对角占优）不一致；本批未补 §3.5 的 std 用例（待任务侧裁决，U-11）。
- 残差超阈判不通过时，可举证算子实现无 bug 并分析误差产生的原因（申诉通道，
  见任务书 3.2.2 各节注文）。

## 提醒

大规格用例现场生成与判定耗时以分钟计，属预期。性能测试按任务书 §3.3 协议自采
（批量接口按任务书原规格 batch 调用，不做拆分）。

## 包指纹对账

| 包 | manifest sha256（前 16） | 文件数 | 来源自检 |
| --- | --- | --- | --- |
{fingerprint_table}

逐包指纹与文件数以 DELIVERY_LEDGER.md 对账表为准；组装后由验收侧复核。