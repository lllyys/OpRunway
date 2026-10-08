---
name: repo-task-solver-case-gen
description: 从 solver 算子任务目录（任务书、cases.json、竞品 bench 源码与性能结果）生成任务包，供开发者自测与验收侧同包消费。包内含规范用例清单、数据构造脚本、检查脚本副本与性能参考耗时表。当拿到 0923 类 solver 任务目录，需要为 Cholesky 十算子（spotrf/spotrs/spotri、cpotrf/cpotrs/cpotri 与批量四算子 spotrfBatched/spotrsBatched/cpotrfBatched/cpotrsBatched）生成任务包或用例数据时使用。
---

# repo-task-solver-case-gen

任务包：发给开发者辅助自测、验收侧同包消费的目录，内容为脚本与数据表。当前支持
Cholesky 十算子：实数 spotrf、spotrs、spotri，复数 cpotrf、cpotrs、cpotri，以及
批量四算子 spotrfBatched、spotrsBatched、cpotrfBatched、cpotrsBatched（后补包）。

新装的包（十算子全部）一律是**纯脚本形态**：不携带任何数据数组（无 `cases/*.npz`、
无 golden、无 ratio 数组），开发者按包内 README 的「先造数后测」流程用 `gen_data.py`
现场生成数据，检查脚本判定时自行重生成、不依赖生成目录。批量四算子走 **A0 抽样**：
每 case 只固化 `k = min(5, batch)` 个代表内容的槽位映射 `sample_map` 与逐内容 ratio
参考（数组不落盘，判定先比余槽 bit-wise 一致性、后代表槽逐内容三层）。除精度用例外
每算子另含 **info 契约用例**（`case_purpose: "info"`，单矩阵每算子 3 例、批量每算子
1 例混合）：只比被测 `info == k_expected`，与数值精度各出独立结论。发包侧固化
`ratio_cpu_mean`（单矩阵 index 顶层按算子存全部正定精度用例的算术平均；批量按 case
条目存 Σ(count_j·ratio_j)/batchSize 槽位加权均值，info 契约用例不计入）——判定时
只读零重算，作 potrf/potrs 残差阈值第二支。单矩阵六算子自 v2 起同此形态（此前交付的
v1 六包是逐字节复用形态的历史实体，维持原形不动）。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| 任务目录 | 单个算子族的发放目录 | 含 `<op>/cases.json` 与 `<op>/bench_result.json` | 由用户给出 |
| 输出根 | 产物写入位置 | 可写目录，不入 git | 由用户给出 |
| 算子集 | 装包哪些算子 | 仅作用于 S5；S2–S3 需同册三算子数据齐备 | 该册全部三个 |
| case 集合 | `s1` 或 `all` | `all` 受 S2 行容量约束 | `s1` |

## 前置检查

python3 与 numpy、scipy 可用；实际版本由装包阶段写入 manifest，不由人工登记。

## 主流程

按阶段顺序执行，命令中 `<输出根>` 替换为入口参数值：

| 阶段 | 命令 | 完成条件 |
| --- | --- | --- |
| S1 冻结规范清单 | `python3 scripts/freeze_canonical.py --task-dir <任务目录> --book real --out <输出根>/canonical_cases.json`（复数任务目录用 `--book complex`） | 退 0；单矩阵算子打印的 `dup_removed` 与 `bench_dup_entries` 全为 0，批量算子按去重对账核对（原始与去重条数、s1 六例逐条列出） |
| S2 生成数据与参考输出 | `python3 scripts/gen_data_cholesky.py --canonical <输出根>/canonical_cases.json --out <输出根>/staging --select s1` | 退 0（内建校验含单双精度降型一致；批量算子走 A0 抽样现场造，只产 index 记 `sample_map`，不落数组） |
| S3 补填 ratio_cpu | `python3 scripts/fill_ratio_cpu.py --cases <输出根>/staging/cases --out <输出根>/ratio` | 退 0，`ratio_cpu_report.json` 无 `prep_failed` 之外的异常项；批量条目按代表内容计算、index 落逐内容 k 值列表（A0 抽样） |
| S4 性能参考耗时表 | `python3 scripts/make_baseline.py --canonical <输出根>/canonical_cases.json --bench-dir <任务目录> --out <输出根>/perf` | 退 0；未匹配条目逐条列报（matched=false、耗时字段为空入包），非 std 未匹配的 case 标性能证据不足；精度数据生成不受 bench 缺失影响 |
| S5 装包 | `python3 scripts/build_package.py --canonical <输出根>/canonical_cases.json --staging <输出根>/staging <输出根>/ratio <输出根>/perf --out <输出根>/packages/<op> --selfcheck <输出根>/packages/<op>.selfcheck.json` | 自检清单每项通过；逐算子各执行一次；十算子均为纯脚本装包（零数组，index 条目记 `materialize: "gen"`；装包自检内建现场生成（含 info 契约派生）、golden 抽验与 ratio 回填及 `ratio_cpu_mean` 固化，S2/S3 的 staging 产物用于独立核对、不进包） |

## 检查条件

| 条件 | 表现 | 处理方式 |
| --- | --- | --- |
| `--select all` 未经容量核算 | 三算子全量写入文件约 132 GiB，超出常规磁盘预算 | 保持 `s1`；确需全量时先按 canonical 的 n 分布核算容量并取得使用者确认。装包不受此约束：纯脚本形态不装数组，包内切片只含 s1 子集，数据由开发者按「先造数后测」现场生成 |
| S1 单矩阵算子重复计数非 0 | 任务目录的 cases.json 或 bench_result.json 与既往批次结构不同 | 停止，报告差异，不修改脚本内的选择规则；批量算子去重非零是预期，按打印的对账条数逐一核对 |
| S3 出现 `prep_failed` | 该 case 前置分解失败，残差参考值缺失 | S3 独立运行时保留状态记录、不删除该 case；装包自检遇 `prep_failed` 即拒绝出包（构造性正定输入下不应发生，先查环境与数据） |
| 脚本非零退出 | 打印中含失败原因与所在 case | 按打印定位；输入不合规时退 2，修输入不改脚本 |

## 产物

`<输出根>/packages/<op>/` 即任务包（纯脚本形态，整包 KB 级），内容：
`cases/index.json`（用例清单，条目记 `materialize: "gen"`；单矩阵条目携带装包自检
回填的 `ratio_cpu` 参考单值，index 顶层另固化算子级 `ratio_cpu_mean` 算术平均；批量
条目携带 `sample_map` 槽位映射与逐内容 `ratio_cpu` k 值列表、条目级 `ratio_cpu_mean`
槽位加权均值；`case_purpose: "info"` 条目为 info 契约用例，带 `k_expected`、无
golden/ratio。参考值仅作参考；mean 为阈值第二支的固化消费值，判定只读零重算）、
`perf_baseline.json`（性能参考耗时）、`verify_accuracy.py` 与 `verify_perf.py`
（检查脚本副本，判定输入现场重生成，含 info 契约判定，输出不构成验收证据）、
`gen_data.py` 与
`canonical_cases.json`（数据构造脚本与本包用例切片：单矩阵 s1 子集 / 批量二维
代表子集）、`sim_dut.py`（流程演练用模拟被测，读「先造数后测」的 data 目录）、
`README.md`（自测说明）、`manifest.json`（环境版本与内容摘要）。包内无
`cases/*.npz`、无 ratio 数组。检查脚本的权威实现与判定在 `repo-task-solver-accept`。
包内每个文件的字段级说明见 [package-contract.md](references/package-contract.md)。

## 范围之外

harness 调用器生成、NPU 上执行、正式验收结论。
