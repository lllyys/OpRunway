---
name: repo-task-solver-case-gen
description: 从 solver 任务书、cases.json 和竞品 bench 结果生成开发者自测与验收共用的任务包。用于 Cholesky、LU/QR 任务包的生成或参考值冻结，输出规范用例、数据构造与检查脚本、CPU 残差参考和 GPU 性能基线。
---

# repo-task-solver-case-gen

任务包供开发者自测和验收侧同包消费；检查脚本的权威实现由 `repo-task-solver-accept` 提供。
精度与性能参考分别冻结，缺 GPU 耗时不阻断精度数据生成，也不能据此判性能通过。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| 任务目录 | 单个算子族的发放目录 | 含 `<op>/cases.json` 与 `<op>/bench_result.json` | 由用户给出 |
| 输出根 | 产物写入位置 | 可写目录，不入 git | 由用户给出 |
| 算子集 | 装包哪些算子 | 仅作用于 S5；S2–S3 需同册数据齐备（Cholesky 册三算子、LU/QR 册四接口） | 该册全部 |
| staging case 集合 | S2 的 `--select s1|all` | `all` 需先核算数组容量 | `s1` |
| 交付范围 | S5 的 `--scope s1|full` | 与 staging 集合独立；full 包含全量规范用例 | `full` |

## 前置检查

Python 3、NumPy、SciPy、threadpoolctl 必须可用。冻结参考值时记录实际加载的数值库版本与线程数；无法识别时停止，补齐环境后重试。

## 主流程

A0 抽样指每个批量 case 仅生成 `min(5,batch)` 个代表内容，再按冻结槽位映射展开。

| 任务族 | `<册>` 取值 | S2 生成器 |
| --- | --- | --- |
| Cholesky 十接口 | `real`、`complex`、`batched` | `gen_data_cholesky.py` |
| LU/QR 十六接口 | `lu_real`、`lu_complex`、`qr_real`、`qr_complex` | `gen_data_luqr.py` |

Cholesky 支持实/复 potrf、potrs、potri 及 potrfBatched、potrsBatched。
LU/QR 支持实/复 getrf、getri、getribatched、matinvbatched、geqrf、gqr、mqr、gels，
其中实数 gqr/mqr 为 sorgqr/sormqr，复数为 cungqr/cunmqr；不含 larft/getrs。

按阶段顺序执行，命令中 `<输出根>` 替换为入口参数值：

| 阶段 | 命令 | 完成条件 |
| --- | --- | --- |
| S1 冻结规范清单 | `python3 scripts/freeze_canonical.py --task-dir <任务目录> --book <册> --out <输出根>/canonical_cases.json` | 退 0；单矩阵算子打印的 `dup_removed` 与 `bench_dup_entries` 全为 0，批量算子按去重对账核对（原始与去重条数、s1 六例逐条列出） |
| S2 生成数据与参考输出 | Cholesky：`python3 scripts/gen_data_cholesky.py --canonical <输出根>/canonical_cases.json --out <输出根>/staging --select s1`；LU/QR：`python3 scripts/gen_data_luqr.py --canonical <输出根>/canonical_cases.json --out <输出根>/staging --select s1` | 退 0（内建校验含单双精度降型一致；批量算子走 A0 抽样现场造，只产 index 记 `sample_map`，不落数组；LU/QR 的 getrf 不产 golden） |
| S3 补填 ratio_cpu | `python3 scripts/fill_ratio_cpu.py --cases <输出根>/staging/cases --out <输出根>/ratio` | 退 0，`ratio_cpu_report.json` 无 `prep_failed` 之外的异常项；批量条目按代表内容计算、index 落逐内容 k 值列表（A0 抽样） |
| S4 性能参考耗时表 | `python3 scripts/make_baseline.py --canonical <输出根>/canonical_cases.json --bench-dir <任务目录> --out <输出根>/perf` | 退 0；未匹配条目逐条列报（matched=false、耗时字段为空入包），非 std 未匹配的 case 标性能证据不足；精度数据生成不受 bench 缺失影响 |
| S5 装包 | `python3 scripts/build_package.py --canonical <输出根>/canonical_cases.json --staging <输出根>/staging <输出根>/ratio <输出根>/perf --out <输出根>/packages/<op> --scope <交付范围> --selfcheck <输出根>/packages/<op>.selfcheck.json` | 自检清单每项通过；逐算子各执行一次；纯脚本装包，执行下述正负例门与 info id 对账；S2/S3 staging 用于核对，不进包 |

S5 自检的末项是正负例门：临时目录内用包内 `gen_data.py`、`sim_dut.py`、
`verify_accuracy.py` 执行一轮判定链，门自己那份 case 清单上每个 id 恰一条判定行、
正例全部 PASS、负例全部 FAIL 才出包。负例的 FAIL 还要求判定行的 `error` 为空：
判定链自己出错（残差基线不可用、内部异常）同样产 FAIL，那是没有证据，不是判据
抓住了偏差。

## 检查条件

| 条件 | 表现 | 处理方式 |
| --- | --- | --- |
| `--select all` 未经容量核算 | 数组量随形状、dtype、batch 和用例数增加；不能以纯脚本包体积估算 staging 容量 | 保持 `s1`；确需全量时先按 canonical 的 n 分布核算容量并取得使用者确认。装包不受此约束：纯脚本形态不装数组，包内切片由 --scope s1/full 决定，数据由开发者按「先造数后测」现场生成 |
| S1 单矩阵算子重复计数非 0 | 任务目录的 cases.json 或 bench_result.json 与既往批次结构不同 | 停止，报告差异，不修改脚本内的选择规则；批量算子去重非零是预期，按打印的对账条数逐一核对 |
| S3 出现 `prep_failed` | 该 case 前置分解失败，残差参考值缺失 | S3 独立运行时保留状态记录、不删除该 case；装包自检遇 `prep_failed` 即拒绝出包（先核对该族输入构造与环境，不删除失败用例） |
| 负例门不过 | 装包退 3，打印指明哪条断言、实际退出码与实际结论；门自身执行出错（超时、包内脚本起不来、报告 JSON 损坏）同样退 3 并打印「执行边界异常」 | 按打印定位包内判定链（verify 副本、sim_dut、数据重生成）或环境，修好再装；不放宽门的断言 |
| info id 对账不符 | 装包退 3，打印两侧集合的差集 | index 的 info 用例 id 与包内 `verify_accuracy.py` 将派生的集合必须相等，否则 verify 要的 case 没有被测输出；两侧派生基座都由切片顶层 `package_scope` 定，按差集定位是哪一侧取错了基座 |
| 脚本非零退出 | 打印中含失败原因与所在 case | 按打印定位；输入不合规时退 2，修输入不改脚本 |

S5 装包及契约错误排查时读取 [任务包契约](references/package-contract.md)。

## 产物

交付目录为 `<输出根>/packages/<op>/`。新包只含脚本和冻结数据表，不含数组；
装包自检报告保存在包外的 `<op>.selfcheck.json`。交付时报告包路径、自检结果和
未匹配 GPU 基线的 case 清单。

## 范围之外

harness 调用器生成、NPU 上执行、正式验收结论。
