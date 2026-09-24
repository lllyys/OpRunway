# 任务包契约

装包（主流程 S5）产物的完整字段说明；消费方是开发者自测与 `repo-task-solver-accept`。

## 包内容十类

| 文件 | 内容 |
| --- | --- |
| `cases/index.json` | 逐 case 条目：canonical 全字段 + `npz` 路径 + `arrays` 清单 + `ratio_cpu`（残差复核阈值参数）+ `ratio_cpu_status`（`ok`/`prep_failed`） |
| `cases/<case_id>.npz` | `A64/A32`（potrs 另 `B64/B32`）+ `golden64/golden32`。实数 float64/float32，复数 complex128/complex64，字段名两域相同 |
| `gen_data.py` | 数据构造脚本副本；与 `canonical_cases.json` 配合可重新生成同批数据 |
| `canonical_cases.json` | 本算子的规范用例清单切片（含未生成 case 的登记） |
| `verify_accuracy.py` | 精度检查脚本副本（渲染件，输出不构成验收证据） |
| `verify_perf.py` | 性能对照脚本副本（同上） |
| `perf_baseline.json` | 竞品逐 case 参考耗时摘录；未匹配条目字段为空 |
| `sim_dut.py` | 模拟被测输出生成器，流程演练用 |
| `README.md` | 自测说明门面 |
| `manifest.json` | 环境版本、工具名与版本、逐文件 sha256 摘要 |

## 关键约定

- 降型一致：`A32 == A64.astype(单精度)`（B、golden 同理），装包自检逐 case 断言；
- 正定性由构造保证：精度用例的 SPD/HPD 矩阵在构造环节即正定——对角占优构造
  （`source: cu` 的用例）对角元 ≥ n、非对角幅值 ≤ 0.5，Gershgorin 圆盘给出特征值
  下界 `(n+1)/2`；随机底阵构造（`source: std` 的用例）`A = B·Bᴴ + n·I`，半正定项
  加正定平移，特征值下界 `n`。生成、判定与自测通路都不含运行时正定性检查，
  消费方不必自行验证；有意构造的非正定用例（构造时打破正定性，只用于 info
  输出对比）不受本条约束；
- **被测输出三键**：每 case 一个 `<case_id>.npz`，含 `out32`（结果）、`info`（整型
  状态）、`status`（`ok` 或失败标识）——验收与自测同用这份约定；
- **摘要分级**：`cases/`、`perf_baseline` 错配阻断对应结论；`verify_*.py` 与权威
  实现不一致只记告警；
- **包冻结**：交付后不改包。检查阈值若有后续裁定，由验收侧实现更新并另发说明，
  包内副本不动。
