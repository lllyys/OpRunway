---
name: repo-task-solver-case-gen
description: 从 solver 算子任务目录（任务书、cases.json、竞品 bench 源码与性能结果）生成任务包，供开发者自测与验收侧同包消费。包内含规范用例清单、数据构造脚本、检查脚本副本与性能参考耗时表。当拿到 0923 类 solver 任务目录，需要为 Cholesky 六算子（spotrf/spotrs/spotri 与 cpotrf/cpotrs/cpotri）生成任务包或用例数据时使用。
---

# repo-task-solver-case-gen

任务包：发给开发者辅助自测、验收侧同包消费的目录，内容为脚本与数据表。当前支持
Cholesky 六算子：实数 spotrf、spotrs、spotri 与复数 cpotrf、cpotrs、cpotri。

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
| S1 冻结规范清单 | `python3 scripts/freeze_canonical.py --task-dir <任务目录> --book real --out <输出根>/canonical_cases.json`（复数任务目录用 `--book complex`） | 退 0，且打印的 `dup_removed` 与 `bench_dup_entries` 全为 0 |
| S2 生成数据与参考输出 | `python3 scripts/gen_data_cholesky.py --canonical <输出根>/canonical_cases.json --out <输出根>/staging --select s1` | 退 0（内建校验含单双精度降型一致） |
| S3 补填 ratio_cpu | `python3 scripts/fill_ratio_cpu.py --cases <输出根>/staging/cases --out <输出根>/ratio` | 退 0，`ratio_cpu_report.json` 无 `prep_failed` 之外的异常项 |
| S4 性能参考耗时表 | `python3 scripts/make_baseline.py --canonical <输出根>/canonical_cases.json --bench-dir <任务目录> --out <输出根>/perf` | 退 0；未匹配条目逐条列报（matched=false、耗时字段为空入包），非 std 未匹配的 case 标性能证据不足；精度数据生成不受 bench 缺失影响 |
| S5 装包 | `python3 scripts/build_package.py --canonical <输出根>/canonical_cases.json --staging <输出根>/staging <输出根>/ratio <输出根>/perf --out <输出根>/packages/<op> --selfcheck <输出根>/packages/<op>.selfcheck.json` | 自检清单每项通过；逐算子各执行一次 |

## 检查条件

| 条件 | 表现 | 处理方式 |
| --- | --- | --- |
| `--select all` 未经容量核算 | 三算子全量写入文件约 132 GiB，超出常规磁盘预算 | 保持 `s1`；确需全量时先按 canonical 的 n 分布核算容量并取得使用者确认 |
| S1 打印的重复计数非 0 | 任务目录的 cases.json 或 bench_result.json 与既往批次结构不同 | 停止，报告差异，不修改脚本内的选择规则 |
| S3 出现 `prep_failed` | 该 case 前置分解失败，残差参考值缺失 | 保留状态入包，验收侧按证据不足处理，不删除该 case |
| 脚本非零退出 | 打印中含失败原因与所在 case | 按打印定位；输入不合规时退 2，修输入不改脚本 |

## 产物

`<输出根>/packages/<op>/` 即任务包，内容：`cases/`（npz 数据与 `index.json`，
含 ratio_cpu 参考值）、`perf_baseline.json`（性能参考耗时）、`verify_accuracy.py`
与 `verify_perf.py`（检查脚本副本，输出不构成验收证据）、`gen_data.py` 与
`canonical_cases.json`（数据构造脚本与本包用例清单）、`sim_dut.py`（流程演练用
模拟被测）、`README.md`（自测说明）、`manifest.json`（环境版本与内容摘要）。
检查脚本的权威实现与判定在 `repo-task-solver-accept`。
包内每个文件的字段级说明见 [package-contract.md](references/package-contract.md)。

## 范围之外

harness 调用器生成、NPU 上执行、batched 检查、正式验收结论。
