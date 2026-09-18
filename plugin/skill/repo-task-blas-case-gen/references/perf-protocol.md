# msprof kernel 性能协议

## 目录

- [适用范围](#适用范围)
- [期望集与调用次数](#期望集与调用次数)
- [执行序列](#执行序列)
- [warmup](#warmup)
- [执行成功证据与逐次判定](#执行成功证据与逐次判定)
- [进度反馈](#进度反馈)
- [op_summary 解析](#op_summary-解析)
- [统计与比对](#统计与比对)
- [逐例状态与 verdict](#逐例状态与-verdict)
- [结果与退出码](#结果与退出码)
- [证据保护](#证据保护)
- [复测与豁免](#复测与豁免)
- [已实测与待实测边界](#已实测与待实测边界)

## 适用范围

| 规则 | 规定 | 依据 |
| --- | --- | --- |
| 性能集 | 任务包 CSV 中能配到非空 `gpu_ms` 基线的 `TC_PF_` 行 | 依据：项目策略 |
| 进程隔离 | 每个 case 的每次采样都是独立进程 | 依据：项目策略 |
| 名字映射 | `--gtest_list_tests` 建 case_name 到完整名的映射 | 依据：项目策略 |
| 计时口径 | 只使用 msprof 的 kernel task duration | 依据：CANN 指南，待核 |
| 排除口径 | GTest 的 ms 含 host 准备与 golden，不作性能依据 | 依据：项目策略 |

`TC_PF_` 是性能用例块的前缀；四块命名见 [csv-and-blocks.md](csv-and-blocks.md) 的「四块」。

## 期望集与调用次数

两条规则决定哪些行进入采样、每次采样的时长怎么归一：

1. 性能期望集 = 任务包 `<op>_test.csv` 里能在 `gpu_baseline.csv` 配到非空 `gpu_ms` 的
   `TC_PF_` 行。配不到基线的 `TC_PF_` 行不跑，只把用例名计入结果 JSON 的
   `ignored_no_ref`。依据：项目策略。
2. `--calls-per-case N`（默认 `1`）是 harness 在一条 gtest 用例里调用被测接口的次数。
   msprof 采到的是整条用例的全部 kernel，每次采样的 kernel 总时长除以 N 才是单次调用耗时；
   N 写入结果 JSON 的 `calls_per_case`。README 契约要求一条用例只调一次，取默认值；
   旧 harness 固定预热一次时填 `2`。依据：项目策略。
   **`calls_per_case` 只计 harness 对被测 API 的调用次数，不计 kernel launch 数**：
   一次 API 调用可产生多个 launch（950 实测 coo2csr 每调用 2–4 个，随规模变化），
   样本 = 该用例全部保留 launch 的 duration 求和 ÷ `calls_per_case`，不得再除以
   `launches`（该字段 = 解析保留的 kernel 行数，定义见解析节）。

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
3. 原始目录默认保留，便于复核；`--keep-prof` 预留关闭策略。依据：项目策略。

协议里的「预热」要区分三处，不可混同：

- 外部 warmup 进程：采样前的裸 gtest 预热进程，由 `--warmup` 控制，默认 0 不起进程，
  语义与证据见「warmup」。旧口径的强制外部预热与 `FAIL(warmup)` 状态已废除。
- harness 内部的 warm-up 调用：写在 harness 源码里的额外 API 调用，计入
  `calls_per_case`（见「期望集与调用次数」），与采样进程数无关。
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

跑完再进入采样；`--repeats` 大于 1 时也只在首次采样前预热一次。N=0 不起预热进程。
预热不产生样本，也不改变 `calls_per_case` 的除数。

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
上游精度轮已挡住多数会崩溃的用例，性能轮的 `CRASH` 只暴露「精度通过但 profiling 下
崩溃」的窄面。`MISSING`（期望用例不在 `--gtest_list_tests`，见「逐例状态与
verdict」）与 `CRASH` 的分界：前者没执行到，后者执行成功证据缺失或不合格。

## 进度反馈

量具把进度逐条打到 stderr 并即时 flush，共三种行。依据：项目策略。

- 开始一行：`性能采样:N 例,预估 ~X min`。N 是筛选后期望用例总数（含 MISSING），
  X = N × 单次预估耗时。
- 每例一行：`[k/N] <case> <status> <本例耗时s> 累计<m>m ETA<m>m`。计时用单调时钟，
  ETA（预计剩余时间）按已完成用例的平均耗时估算，首例完成前显示「待估」。
- 结束时打一行汇总。

量具由调用方直接启动，stderr 天然实时到终端，无需额外转发。

## op_summary 解析

脚本把下列信息集中在表驱动常量中，换机型只改常量不改采集与裁决结构。
下表四项已在 A3 机用 sger 实测确认。**A3 机**是一台实测机器的别名
（ascend910_93，CANN 9.0.1），本文提机器一律写「A3 机」。

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
| `samples` | 每次采样的 kernel duration 总和除以 `--calls-per-case` | 依据：项目策略 |
| `kernel_us` | 所有 samples 的中位数 | 依据：项目策略 |
| `spread` | `(max(samples)-min(samples))/median` | 依据：项目策略 |
| `npu_ms` | `kernel_us/1000` | 依据：单位换算 |
| `ratio` | `gpu_ms/npu_ms` | 依据：项目策略；任务书可覆盖 |
| PASS | `ratio >= perf.threshold` | 依据：项目策略；任务书可覆盖 |

`perf.threshold` 默认 `0.8` 是项目策略，不是外部事实；FACTS 可按任务书覆盖。

默认 `repeats=1` 时只有一个样本：中位数就是该值，`spread=0`。
spread=0 表示无样本间差异可算，不是稳定性证明。

GPU 基线按 `PERF_KEY` 匹配：整数文本按 int 比较，其他值按原字符串比较。
没有匹配行或该行 `gpu_ms` 为空时记 `NO_REF`，只采集不评判。依据：项目策略。

基线里同一规范化键出现多行时**首行生效**：首行 `gpu_ms` 为空即按无基线处理
（记 `NO_REF`），后续重复行不覆盖首行。重复键不再报错，只记 warning
（进 `baseline_warnings`，见「结果与退出码」）；表头、非数值、≤0 这些逐行校验对所有行
照常执行。规范化基线文件保留全部行；所有读取点按同一规则选行——同键处处同选择。
依据：项目策略。

基线 `timing_scope` 不是 `kernel` 时，每例 verdict 加 `(scope caveat)`。
summary 写
`scope_caveat=true`，但不改变基础状态或退出码。依据：项目契约。

## 逐例状态与 verdict

每个用例先落一个 `status`，再由 `status` 派生对外的 `verdict`。`status` 只取下表七个值，
依据来自 `verify_performance.py`：

| status | 触发 | 计分归属 |
| --- | --- | --- |
| `PASS` | `ratio >= threshold`，用例可比较且达标 | 通过 |
| `FAIL` | `ratio < threshold`，用例可比较但不达标 | 数值失败 |
| `NO_REF` | 基线无匹配行或该行 `gpu_ms` 为空 | 只采集不评判 |
| `NO_KERNEL` | 证据合格但 msprof 退出码非 0（不计分），或 op_summary 缺失/无 kernel 行 | 证据不足 |
| `CRASH` | 执行成功证据缺失或不合格（见「执行成功证据与逐次判定」） | 证据不足 |
| `TIMEOUT` | 采样子进程超过超时 | 证据不足 |
| `MISSING` | 部署 CSV 里的期望用例不在 `--gtest_list_tests` | 证据不足 |

`verdict` 默认等于 `status`，只有一处不同：基线 `timing_scope` 不是 `kernel` 时
`verdict` 追加 `(scope caveat)`。
证据不足（`NO_KERNEL/CRASH/TIMEOUT/MISSING` 任一非零）优先于数值失败决定 summary 状态。

## 结果与退出码

结果原子写入 `results/performance_<run_id>.json`。依据：项目策略。
临时文件成功关闭后才替换目标文件。

顶层记录 `calls_per_case`、`ignored_no_ref` 与 `baseline_warnings`（基线告警文本数组，
如「第 N 行键重复，首行生效」）。`baseline_warnings` 同时打到 stderr；本轮它只进性能
JSON 与 stderr，不进验收报告渲染。

每例除身份字段外，还记录 `kernel_us`、`samples`、`launches`、`gpu_ms`、`ratio`、
`spread`、`status`、`verdict`、`warnings` 与诊断消息。逐例 `warnings` 是告警文本数组，
与顶层 `baseline_warnings` 分开，在逐次判定时写入，内容含 repeat 序号、msprof 退出码
与执行成功证据路径。
这些字段共同保留原始样本、统计值和最终判定。依据：项目契约。

summary 记录计数、`status`、`timing_scope`、`threshold` 与 `scope_caveat`。
`status` 只取 `通过/不通过/NO_REF/证据不足`。依据：项目契约。

| 退出码 | 条件 | 依据 |
| --- | --- | --- |
| 0 | 无失败和证据缺口；有 NO_REF 时 summary 为 NO_REF | 依据：项目策略 |
| 1 | 至少一个可比较用例 FAIL，且没有证据缺口 | 依据：项目策略 |
| 2 | 任一 NO_KERNEL、CRASH、TIMEOUT 或 MISSING | 依据：项目策略 |
| 3 | CSV、构建、二进制、列表、基线或 msprof 环境问题 | 依据：项目策略 |
| 4 | 起跑门失败：目标卡忙或查询失败，阻塞不换卡；复测下按中断轮处理，见「复测与豁免」 | 依据：项目策略 |

证据不足优先于数值失败，因为存在未完成的期望用例。依据：项目策略。

## 证据保护

对象分三类，规则不同：

- **原始证据**：各轮结果 JSON 与各阶段目录内的采集产物，一经写出不可变。量具在建目录、
  编译、探卡之前先检查目标结果 JSON：已存在即拒绝退出（`RUN_ID_EXISTS`），不向该路径
  写任何内容。旧「环境失败时原子覆盖同名 JSON」的行为废除。
- **派生裁决**：验收侧的 verdict 与报告，随每次重新裁决覆盖，随时可由原始证据重演。
- **归档副本**：验收产物目录下的复制件，每次裁决整体重建后替换。

失败落盘按三类区分：

1. **单例终态**：逐例 `CRASH/TIMEOUT/MISSING/NO_KERNEL` 是正常记录，量具继续处理
   其余 case，最终写出完整结果 JSON。
2. **首轮环境失败**：照常写出错误 JSON（起跑检查保证目标路径是新路径）。现行退出 3
   的人工处置保留（人工删除该轮阶段目录与结果 JSON 后同 id 重跑一次）——人工授权
   的显式删除，只适用于首轮。
3. **量具异常终止与复测轮环境失败**：不写结果 JSON，阶段目录与日志留存，复测轮因此
   成为中断轮（见「复测与豁免」），不删目录重用轮号，失败就弃号换下一号，错误原因从
   日志与 stderr 读取。

## 复测与豁免

**复测**是绑定同一 run-id 的后续轮次机制。**首轮**指量具以某 run-id 首次产出的
`performance_<id>.json`，`<id>` 称 **base run-id**；**复测轮**是绑定同一 base run-id
的一次后续声明，编号 1..N，分两种 kind：**测量轮**由量具对点名 case 重新采集出结果，
**豁免轮**由验收工具写出声明、宣布若干 case **豁免**（退出裁决分母），不做任何采集。
量具不读任何历史 JSON，跨轮合并（折叠）只在验收侧实现。信任模型是无主观恶意：下列
检查只为捕捉无意错误（跑错工程、换错基线、旧产物混入、工具缺陷），不防篡改。

### 复测轮记录

复测轮的结果文件名 `performance_<id>-retest-<k>.json`，k 为轮号（正整数），阶段目录
是 `results/<id>-retest-<k>/performance/`。轮序以文件名的 k 为准，JSON 内 `round` 与
k 不一致时记 warning，按文件名裁。复测轮不接受 `--out`。
结果 JSON 存在且可解析的复测轮是**完成轮**；阶段目录存在而结果 JSON 缺失的是
**中断轮**，占用轮号，不参与折叠。

两种 kind 共有的必填字段：`schema_version`（整数，本版 1）、`base_run_id`、`round`
（整数）、`kind`（`"measure"` 或 `"waive"`）、身份字段 `op/family/soc/repo`、
`started`/`finished`（该轮进程开始与结果写出前的时刻，ISO 格式）。

测量轮专有字段：

- `requested_cases`：用户点名清单，非空、无重复、每项 ∈ 性能期望集，未知点名在起跑前
  报参数错误。每个点名 case 在 `cases[]` 恰好一条记录，跑不出的用 `MISSING` 占位。逐例记录以 `name` 为用例名，其余字段与首轮相同
  （见「结果与退出码」），另加 `warmup_exit`（见「warmup」）。逐例状态合法集合是
  `PASS/FAIL/NO_KERNEL/CRASH/TIMEOUT/MISSING`——复测集全在性能期望集内，`NO_REF`
  出现即属工具缺陷。
- `warmup`：本轮预热次数 N（整数 ≥0）。
- `device_requested`（本轮请求值，不接受 `auto`）与 `device_resolved`（实际物理卡）。
  比较基准是首轮 JSON 的既有字段：首轮 `device`（原请求）为显式卡号时，两个字段都
  必须等于它；首轮 `device` 为 `auto` 时，两个字段必须等于首轮的 `device_resolved`
  （auto 模式下实际选定的物理卡），量具按首轮 auto 的同一机制执行（该物理卡经
  `ASCEND_RT_VISIBLE_DEVICES` 映射为逻辑 0）。映射由复测限定布尔参数 `--map-device`
  触发：首轮 `auto` 时复测必须带，显式卡号时不传，首轮模式传入属参数错误。
  不支持指定与首轮不同的物理卡。
- 绑定字段（跨轮不变量，必须与首轮 JSON 的对应字段一致）：`binary_sha256`、
  `csv_sha256`、`normalized_baseline_sha256`（规范化基线的哈希）、`threshold`、
  `calls_per_case`、`verifier_sha256`（量具脚本自身的 SHA-256）。
- `argv`：本轮量具进程的完整命令行（字符串数组，含脚本名），仅存证参考，不作机械
  判定输入。

逐例字段一致性：状态为 PASS/FAIL 的记录必须有数值 `ratio` 与 `kernel_us`；证据缺口
状态（`NO_KERNEL/CRASH/TIMEOUT/MISSING`）必须无 `ratio`。

豁免轮专有字段：`waivers[]`，每项 `{case, reason}`——case ∈ 性能期望集且轮内无重复，
reason 逐 case 必填非空。豁免轮不含设备、绑定与 `requested_cases` 字段，也不做任何
设备探测或采集。

首轮绑定锚：量具在首轮 JSON 顶层同样写出 `normalized_baseline_sha256` 与
`verifier_sha256`（其余四个绑定字段首轮已有）。绑定校验以首轮 JSON 里的值为锚，不以
当前 manifest 为锚——manifest 会被重新生成改写，不能替历史背书。首轮 JSON 缺这两个
字段（旧版量具产物）即判定不支持复测，拒绝且不做迁移。

### 轮次有效性

候选轮按文件名模式发现，不可解析的直接归**无效轮**（占号、告警、跳过）。可解析的
完成轮再做下表检查，任一不过即无效轮：跳过折叠，报告醒目列出轮号与原因并明说「该轮
未生效」，其余轮照常折叠，整体裁决不因此翻车。首轮不同：首轮缺失或退出 3 仍按现行
规则记证据不足。

| 检查 | 适用 kind | 判据 |
| --- | --- | --- |
| 结构 | 两者 | `schema_version` 认识、该 kind 必填字段齐全、逐例字段一致、逐例状态在合法集合内 |
| 身份 | 两者 | `base_run_id` 与本次 run-id 相同；op/family/soc/repo 与首轮一致 |
| 绑定 | measure | 六个绑定字段与首轮锚值一致（不一致说明测的不是同一对象，数值不可比） |
| 设备 | measure | 两个设备字段符合「复测轮记录」的设备规则 |
| 点名 | measure | `requested_cases` 合规，且每个点名 case 在 `cases[]` 恰好一条记录 |
| 豁免 | waive | `waivers[]` 合规 |

过检的完成轮是**有效轮**。轮号：完成轮与中断轮都占号，下一轮号 = 已占用最大值 +1，
空缺只记 warning。复测轮一次跑一个（串行是使用约定，非机械校验）。

### 折叠与裁决

折叠由验收侧执行：把首轮与全部有效轮合并为逐例**有效状态**，报告与总结论只认它。
逐例规则：

1. **豁免态**：取涉及该 case 的最后一个声明（按轮号序）——豁免轮列入即豁免，记
   `WAIVED`，退出裁决分母；更晚的有效测量轮点名该 case 即撤销豁免。撤销以「该 case
   被点名并出现在有效测量轮的记录中」为准：跑不出而记 `MISSING` 的占位记录同样构成
   撤销，不以测出数值为条件。首轮记录、中断轮与无效轮都不是声明。
2. **pass-once**：未豁免时，首轮加有效测量轮的测量历史中存在 PASS，有效状态即
   PASS，证据永久保留；无 PASS 但有 FAIL 即 FAIL；只有证据缺口时取最后一次有效测量
   的状态。
3. 汇总分母 = 性能期望集 − 豁免集；期望集非空而分母为空 → `证据不足`（豁免不能空转
   出通过）。分母内任一证据缺口 → `证据不足`，否则任一 FAIL → `不通过`，否则全
   PASS → `通过`。

已知代价：pass-once 使验收命题为「至少一次测得达标」，临界例多试可能靠波动通过，
验收报告必须展示每例尝试史。

## 已实测与待实测边界

早期在 A3 机用 sger 实测确认：`PROF_*/mindstudio_profiler_output/` 层级、`Task Type` 与
`Task Duration(us)` 列名、duration 单位为微秒、向量算子出 `AI_VECTOR_CORE`；当时不带
采集开关的 `--application` 不产 op_summary，旧口径因此走「先采集、再 `--export=on`
导出」两步。旧口径（外部 warm-up + 5 次采样）的整条流水线当时也在 A3 机跑通，
5 次中位数 spread 1.3–2.4% 且随尺寸单调。两步与 5 次的数据只描述该机器该版本当时的
行为，已被现行「单命令、单次采样」取代，不是现行指令。

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
