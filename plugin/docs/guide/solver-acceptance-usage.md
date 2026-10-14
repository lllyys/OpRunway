# Solver 验收 skill 使用指南

使用 `repo-task-solver-accept` 从开发者交付源码构建算子库，按任务包生成输入，
通过公开接口调用算子，分别检查精度、确定性和性能。`harness` 指 skill 提供的
C++ 调用程序及其构建、运行工具。

本指南面向发起验收的人；运行规则以 [SKILL.md](../../skill/repo-task-solver-accept/SKILL.md)
及其引用文件为准。生成任务包使用配套的 `repo-task-solver-case-gen`。

## 前置条件

准备以下材料，并在发起验收时提供实际路径：

| 材料 | 内容 |
| --- | --- |
| 两个 skill | 同一版本的 `repo-task-solver-accept` 和 `repo-task-solver-case-gen`；保留各自的 `scripts/`、`references/` 及其他配套目录 |
| 开发者源码 | 算子实现、公开头文件和构建入口；当前构建工具按 `ops-solver` 工程的 `build.sh` 与库目录约定工作 |
| 适配模块 | 工程内 `test/<op>/<op>_adapter.cpp`，实现固定协议，与开发者自测共用 |
| 任务包 | `canonical_cases.json`、`manifest.json`、`cases/index.json` 或 `cases/index.json.gz`；性能验收还需 `perf_baseline.json` |
| 目标环境 | 可用的目标 NPU、实际 SOC、设备号、CANN 环境、编译工具以及 Python、NumPy、SciPy；性能采集需要 `msprof op` |
| 运行目录 | 位于被测源码之外，用于保存构建信息、实际输入输出、日志及报告；性能采集目录必须是新目录 |
| 性能 kernel 清单 | 各算子或各用例实际执行的 kernel 符号、CSV 名称和每次接口调用的执行次数 |

`index` 中的 CPU 残差参考值用于精度判定，`perf_baseline.json` 中的 GPU 耗时用于性能判定。
任务包必须完整，精度参考值基准须为 `A32-f64`，即从实际单精度输入升精度后计算残差。
缺失或不匹配时取得修复后的任务包，不在验收现场临时重算参考值替换。

适配模块只负责接口调用与资源管理：核对参数顺序、数据类型、矩阵布局、Host/Device
指针归属、workspace 大小单位及 `info` 形态。适配模块不生成输入、不提供标准答案或
通过判定，也不包含 `main`。具体接口见[固定适配协议](../../skill/repo-task-solver-accept/references/adapter-contract.md)。

当前适配编译一次服务一个算子。验收多个算子时，分别提供对应任务包、适配源和运行目录。

## 用法

让执行助手读取验收 skill，并提供一次运行所需的材料。以下文本可替换实际值后使用：

```text
请使用 /tools/repo-task-solver-accept/SKILL.md 验收本次交付。

算子：spotrf
开发者源码：/delivery/ops-solver
适配源码：/delivery/ops-solver/test/spotrf/spotrf_adapter.cpp
任务包：/tasks/spotrf
配套生成 skill：/tools/repo-task-solver-case-gen
目标环境：<运行机器或容器、实际设备型号>
SOC：<现场确认的 SOC>
设备号：0
CANN 环境脚本：<实际 set_env.sh 路径>
实际接口布局：<row_major 或 column_major>
性能 kernel 清单：/tasks/spotrf-kernel-map.json
本次运行目录：/runs/spotrf-001

允许在上述目标环境中构建和执行本次交付。
先核对公开接口、适配模块、任务包及目标环境，再从源码构建真实算子库。
完整运行任务包用例，执行精度检查、5 轮确定性复跑和性能验收。
性能必须使用 msprof op，按均值匹配包内 GPU 基线并汇总结论。
分别报告执行、精度、确定性、性能和未覆盖项，保存失败现场。
接口未接入、基线缺失或证据不足时，明确报告缺项。
```

`spotrf` 是交付材料的填写示例，需要对应开发者适配完成后才能运行。
目标型号、SOC 和布局填写实际值；当前任务性能门要求的 `950PR`、`ascend950` 和
Device 列主序接口，不能用其他设备或接口的数据代替。

若先检查工程接入，可明确要求只跑少量用例。子集结果用于排错；完成全量运行后再核对
完整覆盖。直接操作脚本时，构建、执行和重判命令见
[harness 运行协议](../../skill/repo-task-solver-accept/references/harness-run.md)，
新交付使用其中的固定适配模块入口，并明确传入实际布局。

## 验收流程

| 阶段 | 执行动作 | 应得到的结果 |
| --- | --- | --- |
| 1. 核对材料 | 检查公开头、适配源码、任务包、目标环境、布局与指针归属 | 确认可调用，或列出具体缺项 |
| 2. 构建 | 验收者从交付源码构建 `libops_solver.so`，再编译公共调用程序与适配模块 | 本次构建记录、源码与库身份 |
| 3. 精度与确定性 | 按包内用例生成输入，调用真实算子，取回输出、`info` 和执行状态；恢复输入后复跑 | 逐用例精度结果、完成轮数、一致性结果及失败现场 |
| 4. 性能 | 按 kernel 清单逐用例运行 `msprof op`，匹配冻结 GPU 基线并判断阈值 | 逐用例耗时、比值、性能结论及缺项 |
| 5. 汇总 | 复核失败、未测用例和任务要求的其他证据 | 分项报告及剩余工作 |

精度检查由 skill 的判据实现独立执行，读取任务包冻结的 CPU 参考值；不运行开发者
自测判定代码。需要前置 Cholesky 因子的普通求解、求逆用例，由 skill 从实际输入独立准备。
确定性检查比较多轮实际输出与 `info` 的字节；未完成规定轮数不判一致。

## 性能判据

性能流程是：**逐用例采集 → 匹配 GPU 基线 → 按任务阈值判断 → 汇总结论**。

- 采集使用 `msprof op`，当前工具预热 5 次、采集 30 次接口调用对应的目标 kernel。
- 判定使用算术平均值；多 kernel 或多次 launch 按一次接口调用的全部目标 kernel 耗时求和。
- 计时范围为 `kernel_only`，不包含 Host 分配、数据搬运和输入准备；前置分解必须与目标调用分开。
- 当前任务阈值为 `NPU 平均耗时 <= GPU avg_ms / 0.35`，等价于 `GPU/NPU >= 0.35`。
- 原始 GPU 数据来自 `bench_result.json`，验收读取任务包冻结的 `perf_baseline.json`，按 `case_id` 和 `bench_key` 匹配。

提供的 kernel 清单必须列全实际目标 kernel；符号、CSV 的 `Op Name` 和每次调用次数
需要从真实实现核对。清单格式和采集命令见
[性能验收流程](../../skill/repo-task-solver-accept/references/performance-acceptance.md)。

性能全集为任务包的普通用例，不包含 `info` 契约例。部分 `source=std` 精度补充例
没有 GPU 基线，即使执行成功也会保留为证据不足。

缺少匹配基线或采集证据的用例记为 `INSUFFICIENT`。只要存在超限用例，性能汇总为
`FAIL`；没有超限但有缺项时为 `INSUFFICIENT`；完整非空用例集全部达标才为 `PASS`。
GPU 原始数据更新不自动改变已发任务包，基线更新需要重新冻结、更新包摘要并组织对应证据。

## 产物与结果

| 产物或字段 | 含义 |
| --- | --- |
| `build.json` | 本次源码构建、环境与库产物记录 |
| `report.json` | harness 的执行、结构、数值和确定性结果及统计 |
| `evidence/` | 失败现场；也可指定保留成功用例 |
| `performance-measurements.json` | 性能采集记录、kernel 清单及原始 CSV 索引 |
| `performance-report.json` | 逐用例与汇总性能结论 |
| `structural=PASS` | 输出格式、有限性等结构检查通过，不等于精度通过 |
| `numeric=NOT_JUDGED` | 未进行该算子的数值判定 |
| `formal=PENDING_RULING` | 尚未形成正式结论 |
| `overall_acceptance=NOT_DETERMINED` | 性能报告未决定总体验收结果 |

阅读报告时先看执行是否成功，再看精度、确定性与性能，最后检查是否还有未测用例及
任务要求的 bufferSize、内存、批量覆盖等证据。某一分项通过或命令退出码为 0，
都不能代替完整验收结论。

失败重判使用保存的证据目录；性能重判保留整个采集目录及原始 CSV，不能只复制汇总 JSON。
重判入口分别见前述 harness 运行协议和性能验收流程。

## 开发者样例

[cmatinv 样例说明](../../skill/repo-task-solver-accept/assets/adapter-sample/README.md)
介绍开发者如何共用适配模块完成精度与性能自测。通过 skill 内的
`export_cmatinv_sample.py` 导出完整独立包，不能只复制 `assets/adapter-sample/`
目录；导出过程还会附带固定协议头和公共调用程序。

开发者样例不包含 skill。样例自测通过后，验收者仍从交付源码独立构建并运行任务包。
样例自己的精度结果，与共享 harness 的 `cmatinv` 连接演练中
`numeric=NOT_JUDGED` 属于不同入口，不互相替代。

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 提示 `awaiting_delivery` 或接口未接入 | 检查是否提供该算子的固定适配模块，核对公开接口；按固定适配入口运行 |
| 描述、布局或指针类型不匹配 | 修正交付适配与运行参数，不能通过改标签掩盖接口差异 |
| 包摘要或 CPU 参考值检查失败 | 取得同次分发的完整任务包，保留原始失败信息 |
| 执行失败、`info` 形状错误或复跑不一致 | 查看该用例的执行日志、实际输出和失败现场，修复后重新运行 |
| 性能 `INSUFFICIENT` | 查看逐例原因，补齐 GPU 基线、目标环境、kernel 清单或完整采样 |
| 只跑了调试子集 | 使用完整任务包重新执行，并核对覆盖范围 |

当前数值判据覆盖 Cholesky 十算子；具体 Device 适配、大规格执行与 950 目标完整验证仍需
按交付逐项完成。新数学算子需要独立的判据接入，仅实现适配模块不足以获得精度结论。
