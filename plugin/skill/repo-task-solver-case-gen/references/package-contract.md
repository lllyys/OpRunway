# 任务包契约

装包（主流程 S5）产物的完整字段说明；消费方是开发者自测与 `repo-task-solver-accept`。

## 包内容十类

| 文件 | 内容 |
| --- | --- |
| `cases/index.json` | 逐 case 条目：canonical 全字段 + `npz` 路径 + `arrays` 清单 + `ratio_cpu`（残差参考值）+ `ratio_cpu_status`（`ok`/`prep_failed`）；纯脚本包条目另记 `materialize: "gen"`，info 契约条目（`case_purpose: "info"`）另带 `k_expected`/`info_probe`/`base_case_id`（无 golden/ratio），批量条目另带 `sample_map` 槽位映射、逐内容 `ratio_cpu` k 值列表与 case 级 `ratio_cpu_mean` |
| `cases/<case_id>.npz` | `A64/A32`（potrs 另 `B64/B32`）+ `golden64/golden32`（golden 仅作自测参考，不参与判定）。实数 float64/float32，复数 complex128/complex64，字段名两域相同 |
| `gen_data.py` | 数据构造脚本副本；与 `canonical_cases.json` 配合可重新生成同批数据 |
| `canonical_cases.json` | 本算子的规范用例清单切片（含未生成 case 的登记） |
| `verify_accuracy.py` | 精度检查脚本副本（渲染件，输出不构成验收证据） |
| `verify_perf.py` | 性能对照脚本副本（同上） |
| `perf_baseline.json` | 竞品逐 case 参考耗时摘录；未匹配条目字段为空 |
| `sim_dut.py` | 模拟被测输出生成器，流程演练用 |
| `README.md` | 自测说明门面 |
| `manifest.json` | 环境版本、工具名与版本、逐文件 sha256 摘要 |

## 纯脚本形态（新装包一律如此：批量四算子与单矩阵六算子 v2）

纯脚本形态的包不携带任何数据数组：无 `cases/*.npz`（上表该行描述的是 gen_data
现场生成后的产物形状），`cases/index.json` 条目记 `materialize: "gen"`，其
`ratio_cpu` 为装包自检运行的参考值——批量为逐内容 k 值列表（A0 抽样，条目另带
`sample_map` 槽位映射，判定先比余槽 bit-wise 一致性、后代表槽逐内容残差判定）、单矩阵为
单个数值（均仅作参考，验收与自测侧判定时现场同法重算）。**精度判定为一段式直接
残差**：唯一判据 `ratio ≤ max(5·ratio_cpu, 3·ratio_cpu_mean)`（potri 为
`max(5·ratio_cpu, 0.1)`，不消费 mean），复数残差按复模一体判定（不拆实虚）；
golden 退出判定、仅作自测参考。**ratio_cpu_mean 固化**
（HT-4）：单矩阵包 index 顶层按算子存全部正定精度用例的算术平均 `{op: mean}`，批量包
按精度条目存 Σ(count_j·ratio_j)/batchSize 槽位加权均值（与任务书「batch 个矩阵均值」
数学等价）——判定时只读零重算，作上式第二支 `3·ratio_cpu_mean`；
info 契约用例与非正定矩阵不计入。**ratio_basis 基标记**（2026-10-08 换基）：index
顶层 `ratio_basis: "A32-f64"` 声明包内 `ratio_cpu` 与 `ratio_cpu_mean` 的残差基——
全族以实现实际输入（fp32/complex64）升 f64 计算，potrf 族 DPOT01 的 a 自此用 A32；
verify/accept 消费时据此辨新旧，旧包无此字段即 A64 基，旧固化值不可与新基混用。
**info 契约用例**（HT-8/9）：`case_purpose: "info"` 条目，单矩阵每算子 3 例（非正定/
奇异因子/非法参数）、批量每算子 1 例混合（非正定代表内容 + `bad_param_uplo` 探针），
只比被测 `info == k_expected`、不产 golden/ratio、与数值精度各出独立结论。数据由
开发者按包内 README「先造数后测」用 `gen_data.py` 现场生成；`canonical_cases.json`
为该算子的用例切片（单矩阵为 s1 子集、批量为二维代表子集，全量对账见其 `slice_note`
指向的冻结件；info 契约用例不入切片，由 `gen_data.py` 同规则现场派生）；`README.md`
用纯脚本模板（批量/单矩阵各一份）。上表其余文件同义入包，整包 KB 级。单矩阵六算子
此前交付的 v1 包是逐字节复用形态的历史实体，不再重装。

## 关键约定

- 降型一致：`A32 == A64.astype(单精度)`（B、golden 同理），装包自检逐 case 断言；
- 正定性由构造保证：精度用例的 SPD/HPD 矩阵在构造环节即正定——对角占优构造
  （`source: cu` 的用例）对角元 ≥ n、非对角幅值 ≤ 0.5，Gershgorin 圆盘给出特征值
  下界 `(n+1)/2`；随机底阵构造（`source: std` 的用例）`A = B·Bᴴ + n·I`，半正定项
  加正定平移，特征值下界 `n`。生成、判定与自测通路都不含运行时正定性检查，
  消费方不必自行验证；有意构造的非正定用例（info 契约用例，构造时打破正定性，只用于 info 契约
  判定）不受本条约束；
- **被测输出三键**：每 case 一个 `<case_id>.npz`，含 `out32`（结果）、`info`（整型
  状态）、`status`（`ok` 或失败标识）——验收与自测同用这份约定；
- **摘要分级**：`cases/`、`perf_baseline` 错配阻断对应结论；`verify_*.py` 与权威
  实现不一致只记告警；
- **包冻结**：交付后不改包。检查阈值若有后续裁定，由验收侧实现更新并另发说明，
  包内副本不动。
