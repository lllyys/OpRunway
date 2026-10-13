# 任务包契约

装包（主流程 S5）产物的完整字段说明；消费方是开发者自测与 `repo-task-solver-accept`。

## 包内容

| 文件 | 内容 |
| --- | --- |
| `cases/index.json` | 逐 case 条目：canonical 全字段 + `npz` 路径 + `arrays` 清单 + `ratio_cpu`（残差参考值）+ `ratio_cpu_status`（`ok`/`prep_failed`）；纯脚本包条目另记 `materialize: "gen"`，info 契约条目（`case_purpose: "info"`）另带 `k_expected`/`info_probe`/`base_case_id`（无 golden/ratio），批量条目另带 `sample_map` 槽位映射、逐内容 `ratio_cpu` k 值列表；Cholesky 另带 case 级 `ratio_cpu_mean` |
| `cases/<case_id>.npz` | Cholesky：`A64/A32`（potrs 另 `B64/B32`）+ `golden64/golden32`。LU/QR：自 `A64/A32` 起——getrf 仅 `A64/A32`（无 golden，判定侧还原 L·U）；getri 另 `LU64/LU32`+`ipiv`+golden；gels 另 `B64/B32`+golden；getriBatched 另 `LU64/LU32`+`ipiv`（DUT 输入打包 LU）+golden；matinvBatched 仅 `A`+golden；geqrf 另 `tau64/tau32`（golden 即打包 AF）；*gqr 以打包 AF 为 `A64/A32`+`tau64/tau32`+golden(Q)；*mqr 另 `C64/C32`+`q64`+`tau64/tau32`+golden。实数 float64/float32，复数 complex128/complex64，字段名两域相同（golden 仅作自测参考，不参与判定） |
| `gen_data.py` | 数据构造脚本副本；与 `canonical_cases.json` 配合可重新生成同批数据 |
| `canonical_cases.json` | 本算子的规范用例清单切片（含未生成 case 的登记） |
| `verify_accuracy.py` | 精度检查脚本副本（渲染件，输出不构成验收证据） |
| `verify_perf.py` | 性能对照脚本副本（同上） |
| `perf_baseline.json` | 竞品逐 case 参考耗时摘录；未匹配条目字段为空 |
| `sim_dut.py` | 模拟被测输出生成器，流程演练用 |
| `README.md` | 自测说明门面 |
| `manifest.json` | 环境版本、工具名与版本、逐文件 sha256 摘要 |

## 纯脚本形态

LU/QR 十六接口同样一律纯脚本形态（`--book lu_real|lu_complex|qr_real|qr_complex`），
`gen_data.py` 即 `gen_data_luqr.py` 副本，字段面见上表 npz 行与「关键约定」的 LU/QR 段。

纯脚本形态的包不携带任何数据数组：无 `cases/*.npz`（上表该行描述的是 gen_data
现场生成后的产物形状），`cases/index.json` 条目记 `materialize: "gen"`，其
`ratio_cpu` 为装包自检运行的参考值——批量为逐内容 k 值列表（A0 抽样，条目另带
`sample_map` 槽位映射，判定先比余槽 bit-wise 一致性、后代表槽逐内容残差判定）、单矩阵为
单个数值。验收与自测侧均按 case_id 消费包内冻结值及其状态，不以现场重算值替代；
缺字段或参考值不完整时停止判定。**Cholesky 精度判定为一段式直接
残差**：判据 `ratio ≤ max(5·ratio_cpu, 3·ratio_cpu_mean)`（potri 为
`max(5·ratio_cpu, 0.1)`，不消费 mean），复数残差按复模一体判定（不拆实虚）；
golden 退出判定、仅作自测参考。**ratio_cpu_mean 固化**
：单矩阵包 index 顶层按算子存全部正定精度用例的算术平均 `{op: mean}`，批量包
按精度条目存 Σ(count_j·ratio_j)/batchSize 槽位加权均值（与任务书「batch 个矩阵均值」
数学等价）——判定时只读零重算，作上式第二支 `3·ratio_cpu_mean`；
info 契约用例与非正定矩阵不计入。**ratio_basis 基标记**：index
顶层 `ratio_basis: "A32-f64"` 声明包内 `ratio_cpu` 与 `ratio_cpu_mean` 的残差基——
全族以实现实际输入（float32/complex64）升 float64/complex128 计算，potrf 族 DPOT01 的 a 自此用 A32；
verify/accept 消费时据此辨新旧，旧包无此字段即 A64 基，旧固化值不可与新基混用。
LU/QR 自 `fill_ratio_cpu` 的 `a32base-lqr2` 起，CPU 参考残差统一使用实际
A32/B32/C32 升精度；mqr 的 Q 从实际 A32/tau32 升精度重建。LU/QR 精度条目还须有
`reference_contract: "luqr-input32-v1"`；生产端只在重算参考值后写入，消费端缺失即停止。
旧 lqr1 即使带 ratio_basis，也不能据此认定同口径，须重新冻结，不能只补标记。
info 条目不消费数值参考，不要求此标记；Cholesky 保持原有基标记契约。

LU/QR 不使用 ratio_cpu_mean，按对应任务书残差及阈值消费 ratio_cpu；
gels 超定使用 ratio_opt_cpu。gqr 按任务书检查正交性，不增加 Q 与输入的关联性精度门。

装包 index 顶层 reference_environment、独立回填 index 的 ratio_cpu_fill.env
记录冻结时 NumPy、SciPy、Python、机器架构、
threadpoolctl 识别的实际加载数值库路径、版本、线程数及线程环境变量。
该记录用于追溯参考值环境，不要求消费环境相同，也不允许消费端以重算值替换冻结值。

**info 契约用例**：`case_purpose: "info"` 条目，单矩阵每算子至多 3 例、批量每算子 1 例。
Cholesky 覆盖非正定、奇异因子或非法 uplo；LU 覆盖奇异矩阵或因子；QR 使用 bad_param 探针。
只比被测 `info == k_expected`、不产 golden/ratio、与数值精度各出独立结论。数据由
开发者按包内 README「先造数后测」用 `gen_data.py` 现场生成；`canonical_cases.json`
为该算子的用例切片（由 `package_scope=s1/full` 决定代表子集或全量；
info 契约用例不入切片，由 `gen_data.py` 同规则现场派生）；`README.md`
用纯脚本模板（批量/单矩阵各一份）。上表其余文件同义入包。包体积取决于用例和批量映射规模，不固定为 KB 级。

## gels 冻结参考值

sgels/cgels 方阵使用冻结 ratio_cpu，超定使用冻结 ratio_opt_cpu。
验收与包内检查脚本只读冻结参考值；缺失或无效时停止判定，不现场重算替代。
参考值口径与版本要求同上节。

## 关键约定

- 降型一致：`A32 == A64.astype(单精度)`（B、golden 同理），装包自检逐 case 断言；
- 正定性由构造保证（**仅 Cholesky**）：精度用例的 SPD/HPD 矩阵在构造环节即正定——对角占优构造
  （`source: cu` 的用例）对角元 ≥ n、非对角幅值 ≤ 0.5，Gershgorin 圆盘给出特征值
  下界 `(n+1)/2`；随机底阵构造（`source: std` 的用例）`A = B·Bᴴ + n·I`，半正定项
  加正定平移，特征值下界 `n`。生成、判定与自测通路都不含运行时正定性检查，
  消费方不必自行验证；有意构造的非正定用例（info 契约用例，构造时打破正定性，只用于 info 契约
  判定）不受本条约束；
- **LU/QR 输入构造**（lq-plan §2.1，转录自 0923 四册 bench 的唯一 `fillRandom`）：元素
  `rand()%10000/100 - 50`（U[-50,50)，无对角占优/无 Hermitian 构造；复数每元素先 `.x`
  后 `.y` 各一次独立 rand）。以 FP64 为母本、`A32` 由 `A64` 降型（装包自检断言一致）；
  getrf/geqrf 因子非唯一，判定侧按任务书 §3.2.1 条 6 走还原比对（无 golden 直比）；
- **被测输出三键**：每 case 一个 `<case_id>.npz`，含 `out32`（结果）、`info`（整型
  状态）、`status`（`ok` 或失败标识）——验收与自测同用这份约定；LU/QR 的 getrf 另带
  `ipiv`（1 基主元）、geqrf 另带 `tau`（反射子系数，长度 min(m,n)），由判定侧透传消费；
- **摘要分级**：`cases/`、`perf_baseline` 错配阻断对应结论；`verify_*.py` 与权威
  实现不一致只记告警；
- **包冻结**：交付后不改包。检查阈值若有后续裁定，由验收侧实现更新并另发说明，
  包内副本不动。

## 共享执行工具

任务包保持设备无关。构建、调用和失败取证使用独立分发的 solver-accept harness，
开发者自测与验收者使用同一流程，但验收证据由验收者本次运行产生。harness 不进入
任务包。包内 index 可为明文 `cases/index.json`；超 10 MiB 时按 gzip 落
`cases/index.json.gz`，仍超限则切分为 `cases/index.json.gz.partNNN`（单件 ≤ 10 MiB，
按名序拼接即完整 gzip 流）。manifest 中 `cases/index.json` 的摘要恒针对解压后的
JSON 内容（与磁盘形态无关），压缩/切分不改变用例或数值参考值。

## 试生成范围

试运行裁取输入清单时，在 canonical 顶层保留 `trial_scope` 对象，记录筛选范围；
`max_dimension` 可记录矩阵维度上限。装包将试生成声明写入 README 与 slice_note，
不改变用例或参考值。`package_scope=full` 仅表示完整消费传入清单，不证明原任务全覆盖。
