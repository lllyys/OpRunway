# msprof kernel 性能协议

## Contents

- [适用范围](#适用范围)
- [执行序列](#执行序列)
- [warmup](#warmup)
- [执行成功证据与逐次判定](#执行成功证据与逐次判定)
- [进度反馈](#进度反馈)
- [op_summary 解析](#op_summary-解析)
- [统计与比对](#统计与比对)
- [结果与退出码](#结果与退出码)
- [证据保护](#证据保护)
- [复测与豁免](#复测与豁免)
- [验收消费规则](#验收消费规则)
- [已实测与待实测边界](#已实测与待实测边界)

## 适用范围

| 规则 | 规定 | 依据 |
| --- | --- | --- |
| 性能期望集 | 任务包 CSV 中 `TC_PF_` 前缀且能配到非空 `gpu_ms` 基线的行 | 依据：项目策略 |
| 无基线的行 | 不跑，只把用例名写进 JSON 的 `ignored_no_ref` | 依据：项目策略 |
| 进程隔离 | 每个 case 的每次采样都是独立进程 | 依据：项目策略 |
| 名字映射 | `--gtest_list_tests` 建 case_name 到完整名的映射 | 依据：项目策略 |
| 计时口径 | 只使用 msprof 的 kernel task duration | 依据：CANN 指南，待核 |
| 排除口径 | GTest 的 ms 含 host 准备与 golden，不作性能依据 | 依据：项目策略 |

`TC_PF_` 是 `case_name` 前缀，标记性能用例；其余 `TC_` 用例是精度用例。**期望集**是本轮
必须出结果的用例名集合，基线按**基线键**（`gpu_baseline.csv` 表头去掉 `id`、`gpu_ms`
后与 CSV 共有且非空的列）匹配。

## 执行序列

每次采样恰好执行下面这一条命令——仍是单命令：无第二步导出，默认不起预热进程
（`--warmup` 显式开启的预热在采样序列之外，见「warmup」）；`--gtest_output` 是 GTest
原生参数，结果文件由被测进程自己写出，不新起任何进程：

```
msprof --application="<bin> --gtest_filter=<name> --gtest_output=json:<case_dir>/r<N>.gtest.json" \
       --ai-core=on --task-time=on --output=<dir>
```

`<bin>` 是测试二进制，`<name>` 是名字映射得到的完整名，`<dir>` 是该例本次采样的独立
输出目录；`<case_dir>` 是该 case 采样产物目录的**绝对路径**，`N` 是采样序号，
`r<N>.gtest.json` 按次定址、不跨采样复用。`--ai-core=on --task-time=on` 是采集开关，
缺了在部分平台不产 op_summary。依据：项目策略；实测见「已实测与待实测边界」。

1. 每个期望用例默认只采样一次（`repeats=1`），`--repeats` 可覆盖采样次数；
   每次采样都是独立进程、独立输出目录。依据：项目策略。
2. `op_summary_*.csv` 由 `--application` 自动导出；该产物是否可信、逐次怎么裁，
   按「执行成功证据与逐次判定」执行，`NO_KERNEL` 以证据合格为前提。依据：项目策略。
3. 每次采到的 kernel 总时长除以 `--calls-per-case N`（默认 1）才记为本次样本。
   **`calls_per_case` 只计 harness 对被测 API 的调用次数，不计 kernel launch 数**：
   一次 API 调用可产生多个 launch（950 实测 coo2csr 每调用 2–4 个，随规模变化），
   样本 = 该用例全部保留 launch 的 duration 求和 ÷ `calls_per_case`，不得再除以
   `launches`（该字段 = 解析保留的 kernel 行数，定义见解析节）。
   N 是 harness 一条 GTest 用例调用被测接口的次数：按 README 契约写的新 harness 只调一次，
   填 1；固定先 warm-up 一次再调一次的旧 harness 填 2。依据：项目策略。
4. 原始目录默认保留，便于复核；`--keep-prof` 预留关闭策略。依据：项目策略。

协议里的「预热」要区分三处，不可混同：

- 外部 warmup 进程：采样前的裸 gtest 预热进程，由 `--warmup` 控制，默认 0 不起进程，
  语义与证据见「warmup」。旧口径的强制外部预热与 `FAIL(warmup)` 状态已废除。
- harness 内部的 warm-up 调用：写在 harness 源码里的额外 API 调用，计入
  `calls_per_case`（见第 3 条），与采样进程数无关。
- msprof 工具内部的预热行为：所测条件下未观察到系统性偏冷（见「已实测与待实测
  边界」）；内部机制未获证明。

msprof 查找顺序如下，均要求文件存在且可执行。依据：项目策略。

1. `--msprof` 显式覆盖。
2. PATH 中的 `msprof`。
3. `$ASCEND_TOOLKIT_HOME/tools/profiler/bin/msprof`。
4. `/usr/local/Ascend/ascend-toolkit/latest/tools/profiler/bin/msprof`。

全部找不到时退出 3，并写 `summary.reason=MSPROF_NOT_FOUND`。依据：项目策略。
这是环境失败，不进入任何性能采样。

## warmup

**warmup** 指采样前的外部预热。`--warmup N` 是量具参数，任何一轮（含首轮）都可显式
传入：默认 `0`，合法范围 0–100，越界属参数错误。N ≥1 时，每个 case 的采样序列开始前
起**一个**裸 gtest 预热进程（不带 msprof）：

```
<bin> --gtest_filter=<name> --gtest_repeat=N
```

跑完再进入采样；`--repeats` 大于 1 时也只在首次采样前预热一次。N=0 不起预热进程，即
默认口径。预热不产生样本，也不改变 `calls_per_case` 的除数。

证据与序列化：顶层 `warmup` 记本轮 N 值，逐例 `warmup_exit` 记预热进程退出码（整数）。
首轮未显式传 `--warmup` 时两个字段都不出现；首轮显式传入（含 N=0）时都出现；测量
复测轮（见「复测与豁免」）无条件写出两字段，未传按 N=0（豁免轮无这些字段）。拿不到退出码时 `warmup_exit` 为
null——未预热（N=0）、预热超时、启动异常三种情况都是 null，靠 `warmup` 的 N 值与逐例
`warnings` 的原因文本区分。

预热失败的去向只有两条：

- 非零退出与启动异常：记 warning，照常采样计分——裁决只源于采集轮证据链，预热进程
  起不来不构成对被测对象的判断。
- 预热超时（按 `--timeout`）：该例记 `TIMEOUT` 终态，不再采样，避免一例吃双倍超时；
  本轮其余 case 照常处理。

默认 N=0 的依据是 A3 机（机器别名，见「op_summary 解析」）的定案实测，
见「已实测与待实测边界」。

## 执行成功证据与逐次判定

**执行成功证据**是本次采样的 `r<N>.gtest.json`：它证明「本次采样中 GTest 测试执行完成」，
不证明「进程正常退出」——GTest 在 RUN_ALL_TESTS 返回时写出该文件，其后进程清理阶段的
失败不可见。接受此边界的理由：kernel 证据（op_summary）在测试体内已采完，清理段失败
不影响其有效性；不为捕获进程终态加 wrapper——加层违背最少机制。该 JSON 只作
执行成功证据，其中的耗时字段不作性能依据（计时口径仍只用 msprof）。依据：项目策略。

**证据合格**是正向条件，与精度侧 gtest JSON 解析同构：文件可读、可解析为 JSON 对象、
目标完整 gtest 名的记录存在、状态为已执行完成（非 SKIPPED/NOTRUN）、无 failure 记录。
读取、解析、结构校验的任何失败一律不合格，不允许对缺失字段按成功默认。依据：项目策略。

每次采样按下列顺序判定。既有 `problem` 分支优先级不变：采样进程超时记 `TIMEOUT`，
进程启动异常记 `CRASH`，都属证据不足（退出码 2）；CSV 与枚举等环境失败路径不变，
本节只替换旧「看 msprof 退出码定 CRASH」的判据。

1. `r<N>.gtest.json` 不合格（含缺失）→ `CRASH`，退出码 2。诊断措辞用「执行成功证据
   缺失/不合格」，不断言算子必然崩溃。
2. 证据合格但该次 msprof 退出码非 0 → `NO_KERNEL`，退出码 2：采集或分析未完整，产物
   不可信，**不计分**——即使 op_summary 存在也不读，防部分导出的 CSV 少算耗时抬高 ratio。
3. 证据合格、msprof 退 0、op_summary 缺失或无 kernel 行 → `NO_KERNEL`（既有证据不足
   路径，此时解析得 `launches==0`）。
4. 全部合格 → 计分。

任一次采样落入 1–3 即终止该 case 并按该状态裁决，不用其余成功样本掩盖失败。判定时把
repeat 序号、msprof 退出码与证据文件路径写进逐例 `warnings`（见「结果与退出码」）。
上游精度门（验收链 A3 阶段，见 run-chain.md）已挡住多数会崩溃的用例，性能轮的 `CRASH`
只暴露「精度通过但 profiling 下崩溃」的窄面。逐例状态里的 `MISSING`（点名期望用例
未能映射到 `--gtest_list_tests` 的完整名，枚举缺席、从未执行）与 `CRASH` 的分界：
前者没执行到，后者是执行成功证据缺失或不合格（含采样进程启动异常）。

## 进度反馈

量具把进度逐条打到 stderr 并即时 flush，共三种行。依据：项目策略。

- 开始一行：`性能采样:N 例,预估 ~X min`。N 是筛选后期望用例总数（含 MISSING），
  X = N × 单次预估耗时。
- 每例一行：`[k/N] <case> <status> <本例耗时s> 累计<m>m ETA<m>m`。计时用单调时钟，
  ETA（预计剩余时间）按已完成用例的平均耗时估算，首例完成前显示「待估」。
- 结束时打一行汇总。

A4 由 agent 直接启动量具进程，stderr 天然实时到终端，accept 不做转发。

## op_summary 解析

脚本把下列信息集中在表驱动常量中，换机型只改常量不改采集与裁决结构。
下表四项已在 A3 机用 sger 实测确认。**A3 机**是一台实测机器的别名
（ascend910_93，CANN 9.0.1），与验收链的 A3 精度阶段同名不同物；本文提机器一律写「A3 机」。

| 常量 | 当前值 | 依据 |
| --- | --- | --- |
| `OP_SUMMARY_GLOB` | `PROF_*/mindstudio_profiler_output/op_summary_*.csv` | 实测（自动导出产物） |
| task type 列 | `Task Type` | 实测（第 8 列） |
| duration 列 | `Task Duration(us)` | 实测（第 10 列） |
| kernel task 类型 | `AI_CORE/AI_VECTOR_CORE/MIX_AIC/MIX_AIV` | 实测（sger 出 AI_VECTOR_CORE） |

`parse_op_summary(dir)` 遍历匹配文件，只保留 kernel task 类型。每行的 duration 相加为
本次 `kernel_us`，保留行数记为本次 `launches`。**launches** 是解析后保留的 kernel 行数，
不等于 API 调用次数，也不作去重或换算依据，只作诊断记录。依据：项目策略；字段语义待核。

缺列、负 duration 或非数值 duration 都视为解析失败。
没有 kernel 行时记
`NO_KERNEL`，不能以 0 代替。依据：项目策略。

## 统计与比对

| 项 | 计算 | 依据 |
| --- | --- | --- |
| `samples` | 每次采样的 kernel duration 总和 ÷ `calls_per_case` | 依据：项目策略 |
| `kernel_us` | 所有 samples 的中位数 | 依据：项目策略 |
| `spread` | `(max(samples)-min(samples))/median` | 依据：项目策略 |
| `npu_ms` | `kernel_us/1000` | 依据：单位换算 |
| `ratio` | `gpu_ms/npu_ms` | 依据：项目策略 |
| PASS | `ratio >= 0.8` | 依据：项目策略 |

阈值固定 `0.8`，由 accept 渲染进脚本并写入 `manifest.json` 的 `threshold`；
它是项目策略，不是外部事实，任务包不能覆盖。

默认 `repeats=1` 时只有一个样本：中位数就是该值，`spread=0`。
spread=0 表示无样本间差异可算，不是稳定性证明。

GPU 基线按基线键匹配：整数文本按 int 比较，其他值按原字符串比较。期望集里每行都有基线，
所以逐例 `NO_REF` 正常不会出现；期望集为空（`TC_PF_` 全无基线，或被 `--case/--filter`
收窄成空）时 summary 记 `NO_REF`，退出 0。依据：项目策略。

基线里同一规范化键出现多行时**首行生效**：首行 `gpu_ms` 为空即按无基线处理
（该例计入 `ignored_no_ref`），后续重复行不覆盖首行。重复键不再报错，只记 warning
（进 `baseline_warnings`，见「结果与退出码」）；表头、非数值、≤0 这些逐行校验对所有行
照常执行。规范化基线文件保留全部行；所有读取点（量具与 accept 的 A2/A5 消费）按同一
规则选行——同键处处同选择。依据：项目策略。

基线 `timing_scope` 不是 `kernel` 时，每例 verdict 加 `(scope caveat)`。
summary 写
`scope_caveat=true`，但不改变基础状态或退出码。依据：项目契约。

## 结果与退出码

结果原子写入 `results/performance_<run_id>.json`。依据：项目策略。
临时文件成功关闭后才替换目标文件。

顶层记录 `calls_per_case`、`ignored_no_ref`（无基线被忽略的用例名）与 `baseline_warnings`
（基线告警文本数组，如「第 N 行键重复，首行生效」）。`baseline_warnings` 同时打到
stderr；本轮它只进性能 JSON 与 stderr，不进 A5 报告渲染。

每例除身份字段外，
还记录 `kernel_us`、`samples`、`launches`、`gpu_ms`、`ratio`、`spread`、`status`、`verdict`、
`warnings` 与诊断消息。逐例 `warnings` 是告警文本数组，与顶层 `baseline_warnings` 分开，
在逐次判定时写入，内容含 repeat 序号、msprof 退出码与执行成功证据路径。
这些字段共同保留原始样本、统计值和最终判定。依据：项目契约。

summary 记录计数、`status`、`timing_scope`、`threshold` 与 `scope_caveat`。
`status` 只取 `通过/不通过/NO_REF/证据不足`。依据：项目契约。

| 退出码 | 条件 | 依据 |
| --- | --- | --- |
| 0 | 无失败和证据缺口；期望集为空时 summary 为 NO_REF | 依据：项目策略 |
| 1 | 至少一个可比较用例 FAIL，且没有证据缺口 | 依据：项目策略 |
| 2 | 任一 NO_KERNEL、CRASH、TIMEOUT 或 MISSING | 依据：项目策略 |
| 3 | CSV、构建、二进制、列表、基线或 msprof 环境问题 | 依据：项目策略 |
| 4 | 起跑门失败：目标卡忙或查询失败（见 run-chain.md「A1 环境」）；复测下按中断轮处理，见 retest-protocol.md「启动与恢复」 | 依据：项目策略 |

证据不足优先于数值失败，因为存在未完成的期望用例。依据：项目策略。

## 证据保护

对象分三类，规则不同：

- **原始证据**：各轮结果 JSON 与各阶段目录内的采集产物，一经写出不可变。量具在建目录、
  编译、探卡之前先检查目标结果 JSON：已存在即拒绝退出（`RUN_ID_EXISTS`），不向该路径
  写任何内容。旧「环境失败时原子覆盖同名 JSON」的行为废除。
- **派生裁决**：verdict.json 与报告，随每次 A5 重跑覆盖——它记录输入清单与规则版本，
  随时可由原始证据重演。
- **归档副本**：产物目录 `intermediate/` 下的复制件，每次 A5 在临时目录整体生成后
  替换，不留 stale 文件。

失败落盘按三类区分：

1. **单例终态**：逐例 `CRASH/TIMEOUT/MISSING/NO_KERNEL` 是正常记录，量具继续处理
   其余 case，最终写出完整结果 JSON。
2. **首轮环境失败**：照常写出错误 JSON（起跑检查保证目标路径是新路径）。退出 3 的
   人工处置保留（见 run-chain.md「A4 性能」：删目录与 JSON 后同 id 重跑一次）——
   那是人工授权的显式删除，不是量具覆盖，只适用于首轮。
3. **量具异常终止与复测轮环境失败**：不写结果 JSON，阶段目录与日志留存，复测轮因此
   成为中断轮（定义见 retest-protocol.md「术语与信任模型」），不删目录重用轮号，
   失败就弃号换下一号，错误原因从日志与 stderr 读取。

## 复测与豁免

**首轮**（既有 A4 产出的 `performance_<id>.json`）之后，可绑定同一 run-id 追加
**复测轮**：**测量轮**重新采集点名 case，**豁免轮**宣布 case 退出裁决分母。复测轮的
记录 schema、轮次有效性、折叠规则、启动流程与 verdict 增量整体收在
[retest-protocol.md](retest-protocol.md)。与本协议的衔接点有三：复测轮 run-id 形如
`<id>-retest-<k>` 且不接受 `--out`；量具不读任何历史 JSON，折叠只在 A5 实现；量具在
首轮 JSON 顶层写出 `normalized_baseline_sha256` 与 `verifier_sha256` 两个绑定锚字段
（`threshold`、`calls_per_case`、`binary_sha256`、`csv_sha256` 首轮已有），供复测轮
校验「测的还是同一对象」。

## 验收消费规则

`accept.py verdict` 不重算 kernel 耗时或比值，但会重算 cases 状态计数，并校验期望集、
run-id、op、family、repo、soc、device、summary 与退出码，以及 A3/A4 两份 JSON 的
`csv_sha256`、`binary_sha256` 相同。期望集由 verdict 从运行时包的 CSV 与规范化基线重新算出，
与脚本口径一致。存在有效复测轮时，性能结论按 retest-protocol.md「折叠」的规则由首轮
与各轮合并得出。`NO_REF` 不能证明性能达标，总体结论为 `证据不足`。
`check.json` 的 `checks.perf.comparable_pf` 为 0 时不跑 A4、不要求性能 JSON，此时按
CSV 的 `TC_PF_` 行数裁决：无 `TC_PF_` 行记 `通过（无性能要求）`；有 `TC_PF_` 行而全
配不到基线记 `NO_REF`，总体 `证据不足`（判据见 run-chain.md「A5 结论」）。

## 已实测与待实测边界

早期在 A3 机用 sger 实测确认：`Task Type` 与 `Task Duration(us)` 列名、duration 单位为
微秒、向量算子出 `AI_VECTOR_CORE`；当时不带采集开关的 `--application` 不产 op_summary，
旧口径因此走「先采集、再 `--export=on` 导出」两步。两步口径只描述该机器该版本当时的
行为，已被现行单命令取代，不是现行指令。

后续证据推翻了两步口径的前提：A3 机复测坐实带采集开关后 `--application` 自动导出
op_summary，「自动导出 + 显式 `--export=on`」会在同一目录落两份逐字节相同的文件，
解析累加后 kernel_us 翻倍；ascend910B3 与 ascend950 的验收数据呈同型双份现象。
现行「单命令、无第二步导出」的口径由此而来。

A3 机（CANN 9.0.1）另有两项实测直证。其一：新命令形态跑 sger 三例，每例 op_summary
恰 1 份、kernel 行恰 1 行，自动导出成立。其二：msprof **不透传** application 失败——
application 退非 0 或收 SIGSEGV 后 msprof 仍退 0，只在 stderr 打 WARNING；中途 KILL 时
gtest JSON 缺失、op_summary 0 份，msprof 仍退 0。崩溃判定因此不看 msprof 退出码，
以执行成功证据为准。

外部预热对 kernel 测量的影响已定案实测（A3 机，CANN 9.0.1）：取两个最接近阈值、最有
望被预热改判的 FAIL 例，比较预热 0/1/10 次三种条件，TC_PF_1005 中位 4.64/4.90/4.72µs
（样本 13），TC_PF_1006 中位 4.75/4.56/4.75µs（样本 6）。条件间差异 ≤6% 且方向非
单调，低于同机 µs 级 kernel 的单点复现散布（同例跨进程单次采样差可达约 7%），预热
一次后 ratio 仍远低于 0.8。在所测机型与用例上未观察到预热改变裁决的效果，`--warmup`
默认 0 由此定案（这是决策，不是普适证明），预热仅作复测旋钮保留。

以下仍待在更多机型与算子上确认：

- 自动导出在新平台或新 CANN 上是否成立；不成立时无文件落 `NO_KERNEL`，不会假 PASS。
  依据：待实测。
- 逐次判定的直证状态（A3 机修订版复验）：判定 2（证据合格且 msprof 退出码非 0，
  不计分）与判定 4（计分）已端到端直证；判定 1（证据缺失或不合格）为函数级四形态直证，
  被测进程中途死的端到端形态待构造；判定 3（证据合格且无 op_summary）未新构造，
  沿用既有 NO_KERNEL 路径。依据：A3 机实测，未列形态待实测。
- msprof 内部预热机制未获证明；A3 机代表尺寸实测未观察到单次采样系统性偏冷
  （三尺寸各 5 次独立单采样，中位偏差 ≤1.7%，首例无台阶）。依据：A3 机实测，
  其他机型待实测。
- Cube 类算子（gemm/herk）的 `Task Type` 是否为 `AI_CORE` 或 `MIX_AIC/MIX_AIV`。依据：待实测。
- MIX task 是否在单份 op_summary 内产生重复计数的明细行。依据：待实测。
- 多设备或多 stream 时一个 case 是否产生多个 op_summary 文件。依据：待实测。
- ascend950（arch35）上文件名与列是否一致。依据：待实测。
