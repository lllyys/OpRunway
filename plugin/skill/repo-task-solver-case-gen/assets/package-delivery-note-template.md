# {delivery_group} 统一交付说明

（交付组装包步骤消费本模板：替换全部 {placeholder} 后随交付树落盘为
DELIVERY_NOTE.md；本模板不进任何单包，包内自测说明见 package-readme-template*。）

{delivery_note_preamble}

| 任务书 | 包 |
| --- | --- |
{taskbook_packages_table}

## 包的统一形态（{package_count} 包一致）

1. **纯脚本包（零数组数据）**：包内不带任何数据数组（单矩阵包 KB 级；批量包
   另含 A0 抽样槽位映射 sample_map——判定余槽一致性的必需数据，最大约 60 MB）。
   用例数据由包内脚本现场生成——先造数、后测试：

   ```bash
   python3 gen_data.py --canonical canonical_cases.json --out data --select all
   ```

   你的执行器从 `data/cases/*.npz` 读输入；`verify_accuracy.py` 判定时自行按同一
   配方重新生成输入与参考输出（同一环境下与你造的数逐位相同），不依赖你的
   `data` 目录。
2. **verify 双件**：`verify_accuracy.py` 验精度，`verify_perf.py` 验性能（判据取自
   包内 `perf_baseline.json`，采集按《性能自测采集说明》用 msprof op）。
3. **自测配套件**：`gen_data.py`、`sim_dut.py`（模拟被测输出，供接入自查）、
   `README.md`（接入步骤）。

## 固化口径（{package_count} 包一致，v3）

1. **ratio_cpu 参考值**：逐 case 由 CPU 同精度参考链预计算并固化在
   `cases/index.json`（字段 `ratio_cpu`），判定只消费、不重算。
2. **ratio_cpu_mean（A0 第二阈值支）**：批量包按槽位加权平均、单矩阵包按算术
   平均预计算，固化在 `cases/index.json` 顶层 `ratio_cpu_mean` 字段；阈值式
   max(5·ratio_cpu, 3·ratio_cpu_mean) 双支消费。极端情况下缺该键时判定自动走
   单支兼容口径（fail-closed 方向）。
3. **批量 A0 抽样**：批量包每 case 只固化 k=min(5,batch) 个代表内容矩阵与
   `sample_map`（槽位映射）；判定先核余槽 bit-wise 一致性、再对代表槽逐内容
   残差判定——「1~2 个矩阵非正定」按代表内容解释。
4. **info 契约用例**：每算子另含 case_purpose=`info` 的契约用例（无 golden，
   只比 info）：正定场景 info=0、非正定场景 info=第 k 阶（逐内容 k 值列表或
   标量，按接口分型）；被测 info 与期望不符即数值 FAIL。
5. **确定性**：任务书要求合法用例重复执行结果 bit-wise 一致；该证据由验收侧
   复跑出具，不随包交付。

## 批量四包的两点补充

1. **被测输出的 `info` 按接口区分**：potrfBatched 类为逐矩阵 int32 数组
   （shape=(batch,)，即 infoArray）；potrsBatched 类仍为标量（仅报参数错）。
   `out32` 为批维堆叠（potrf 类 (batch,n,n) 半三角布局，potrs 类 (batch,n,1)）。
2. **批量判定两层先后**：先余槽一致性（同输入不同输出本身即缺陷，FAIL 带槽位
   号证据），后代表槽逐内容直接计算 LAPACK ε 归一化残差并对
   max(5·ratio_cpu, 3·ratio_cpu_mean) 判定；报告给出失败内容计数与首个/最差失败序号。

## 结论口径（{package_count} 包一致）

- 数值通过 ≠ 正式验收通过（`formal` 恒 `PENDING_RULING`）；
- 复数算子残差按复模一体判定（不拆实虚）；
- 批量判定与单矩阵同口径出数值结论（批量数值结论已随判据定稿升正式）；
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
