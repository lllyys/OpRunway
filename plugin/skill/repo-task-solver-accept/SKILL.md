---
name: repo-task-solver-accept
description: 构建并执行已接入的 solver 工程，保存运行证据，按任务包固化参考值与一段式 LAPACK 残差判定复核输出。当需要端到端验收、开发者同流程自测或失败证据重判时使用；未接入的交付接口明确停止，生成任务包改用 repo-task-solver-case-gen。
---

# repo-task-solver-accept

验收者从开发者交付源码自行构建、调用、判定并保存证据。开发者自测使用同一套
harness（构建、执行与取证工具），但自测报告不替代验收者本次运行的证据。
harness 独立分发，任务包保持设备无关。

判定覆盖 Cholesky 十算子：实数与复数的 potrf、potrs、potri，以及 potrfBatched、
potrsBatched。逐 case 直接计算 LAPACK 残差，输入取实现实际输入 A32/B32，复数按
复模与共轭转置计算。golden（参考输出）不参与判定。批量采用代表内容残差判定加
其余槽位逐字节一致性检查，全部矩阵满足条件才判 case 数值通过。

**执行支持与判定覆盖分别核对。** 当前已有 Host 指针接口的小规格真机通路；
Cholesky 的 Device 指针调用尚待真实交付头接入。缺接口时停在 A0，
不以模拟输出替代，不因存在判定卡就宣称十算子可端到端运行。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| 被测工程 | 实际交付的 ops-solver 源码 | 含公开头、构建入口和算子实现 | 用户提供 |
| 任务包目录 | case-gen 的装包产物 | 含 manifest、canonical 与已回填 index | 用户提供 |
| 生成脚本目录 | 同次分发的 case-gen scripts | 含 gen_data_cholesky.py | 同级 skill 目录 |
| 运行环境 | 目标设备、SOC、CANN 环境 | 已获构建与执行授权 | 探测实际环境，不从任务名猜测 |
| 工作与报告目录 | 本次运行的现场与结论 | 位于被测源码之外 | 用户给定的运行目录 |

## 主流程

| 阶段 | 动作 | 完成条件 |
| --- | --- | --- |
| A0 工程准入 | 按 [harness-run.md](references/harness-run.md) 核对实际交付接口、布局、内存归属、因子准备和目标环境 | 已有适配支持实际入口；未知签名或不支持的布局停止并报告缺项 |
| A1 构建与执行 | 验收者运行 build_dut.py 与 run_harness.py，命令见同一参考文档 | 本次源码、构建库和实际加载库有取证；输入现场构造，输出来自本次调用 |
| A2 判定与留证 | 编排器调用本 skill 的判据实现，读取包内固化参考值；失败件自动导出 | 执行、数值、确定性分别记结论，失败现场可独立重判 |
| A3 报告复核 | 失败件运行 run_harness.py --rejudge；族级期望含义见 [verdict-mechanism.md](references/verdict-mechanism.md) | 缺性能、内存、完整覆盖等证据时如实列缺口，局部成功不等于族级正式通过 |

## 已有输出复核

已有 `out32`（输出数组）、`info`（接口状态）、`status`（执行状态）三键 npz 的备用入口：

```bash
python3 scripts/accept_run.py --package <任务包目录> \
  --dut-out <被测输出目录> --report <报告路径>
```

该入口不补做构建与执行。纯脚本包没有冻结输入数组时，接口精度项记证据不足；
完整运行走 A0–A3。报告的 `operator`、`expectation`、`flags`、`versions` 和
`声明边界` 含义见 [verdict-mechanism.md](references/verdict-mechanism.md)。

## 模拟通路演练

模拟通道检查数据生成与判据连接，不构成真实执行证据：

```bash
python3 scripts/stream_check.py --canonical <canonical_cases.json> \
  --gen-dir <repo-task-solver-case-gen 的 scripts 目录> --report stream_report.json
```

筛选用 `--ops`、`--max-n`；负例用 `--perturb` 与批量 `--perturb-index`；
指定输出留盘用 `--dump <case_id> --dump-dir <目录>`。模拟通道现场计算的 CPU
参考值只用于演练，不能替代正式包的冻结参考值。批量 `--jobs N` 控制逐矩阵判定进程数。

## 检查条件

| 条件 | 表现 | 处理方式 |
| --- | --- | --- |
| 缺真实入口或适配未实现 | A0 停止，报告未接入 | 取得实际交付头、实现和调用契约后补适配，再做真机验证 |
| 包参考值基准不是 A32-f64，或缺固化参考值 | 拒绝判定 | A32-f64 指单精度实际输入升 f64 后计算残差。index 顶层 ratio_basis 必须为 A32-f64；索取完整新包，不现场重算替代 |
| 数值 PASS 与正式结论的分界 | formal 为 PENDING_RULING | 此状态表示未出具正式结论；补齐任务要求的全部证据与裁定后才谈正式通过 |
| 期望项缺证据 | 族级不判通过 | 补齐对应证据，不缩小期望集 |
| 包摘要与 manifest 不符 | 备用复核报告阻断对应项 | 重新取得完整包，不手工掩盖摘要差异 |
| 检查脚本副本与权威实现不同 | 备用复核 flags 告警 | 以 skill 实现为准，并重新装配自测副本 |
| 被测 status 非 ok | 执行失败，不参与数值统计 | 先解决执行问题再运行，区别于数值 FAIL |
| 批量 info 形状不符 | 证据错误 | potrfBatched 要求 (batch,) int32 数组，batch=1 也不标量化；potrsBatched 使用标量 |
| 确定性复跑不足规定轮数 | 确定性无结论 | 保留现场，不能将未完成当一致 |
| batched 期望项覆盖不全 | 与逐 case 数值结论分开记证据不足 | 补齐任务要求的覆盖类别，不能以代表子集替代 |

## 产物

harness 报告包含构建与执行取证、逐 case 判定、确定性、失败留证目录及统计。
备用入口的族级报告覆盖接口精度、性能、bufferSize、内存、批量覆盖、确定性、
info 契约七类期望。两种报告的结构不同，不能把 harness 的结构性 PASS 直接填成
族级数值 PASS。失败件包含实际输入输出、日志和原判定上下文，可独立重判。

## 参考资料

- [harness-run.md](references/harness-run.md)——A0–A3 的准入、命令、失败留证与边界
- [verdict-mechanism.md](references/verdict-mechanism.md)——族级期望与判定字段含义
- [perf-collection.md](references/perf-collection.md)——性能采集协议及 verify_perf 输入格式

## 范围之外

尚未交付的 Cholesky Device 接口、大规格完整执行验证、性能正式门禁、批量完整
覆盖，以及 gels、QR、LU 和特征值族数值判定。Host 求逆入口的结构性演练不扩大
这些判定范围。性能口径仍为任务书的 NPU msprof 实测 ≤ GPU 参考 / 0.35。
