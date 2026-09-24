# msopprof kernel 性能协议

正式性能验收的唯一权威：采集、解析、统计口径、失败判定与验收消费都以本文件为准。
复测轮与豁免的记录、有效性与折叠规则在 [retest-protocol.md](retest-protocol.md)。

## Contents

- [适用范围](#适用范围)
- [采集命令与产物](#采集命令与产物)
- [执行序列](#执行序列)
- [执行成功证据与逐次判定](#执行成功证据与逐次判定)
- [进度反馈](#进度反馈)
- [产物解析](#产物解析)
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
| 计时口径 | 本次采样命中的全部 kernel launch 的 `Task Duration(us)` 之和 | 依据：实测 |
| 排除口径 | GTest 的 ms 含 host 准备与 golden，不作性能依据 | 依据：项目策略 |

`TC_PF_` 是 `case_name` 前缀，标记性能用例；其余 `TC_` 用例是精度用例。**期望集**是本轮
必须出结果的用例名集合，基线按**基线键**（`gpu_baseline.csv` 表头去掉 `id`、`gpu_ms`
后与 CSV 共有且非空的列）匹配。msopprof 以重放方式量 kernel，读数是稳态耗时、不含首次
调用开销，与旧 msprof 口径不可比，差值见「已实测与待实测边界」。

## 采集命令与产物

每个 case 的每次采样恰好执行下面这一条命令，无第二步导出，无预热进程：

```
msopprof --output <采样目录> --aic-metrics=BasicInfo --launch-count <上限> \
         <被测二进制> --gtest_filter=<完整名> --gtest_output=json:<case_dir>/r<次序>.gtest.json
```

`<完整名>` 由名字映射得到，`<采样目录>` 是该例本次采样的独立输出目录，`<case_dir>` 是该
case 产物目录的**绝对路径**。四条约束进契约：

1. `--output` 与其余选项排在被测二进制之前，其后的参数一律透传给被测程序。放错位置报
   `output dir is not writable`，该报错文本与实际原因无关。
2. 不拼 `--application=`：该写法已废弃，被测程序及其参数作为 argv 尾原样传递。
3. 不传 `--ai-core` 与 `--task-time`：两者在新命令下是硬错误，工具退 255。
4. `--launch-count` 默认 512，允许区间 1 到 5000。超额指定不报错、不增加耗时与体积，
   采到的就是进程实际发生的全部 launch。

选 `--aic-metrics=BasicInfo` 而不用默认档的原因是耗时与文件数都更低，读数不变。

采集二进制按下列顺序查找，均要求文件存在且可执行。**CLI 参数名 `--msprof` 保留不改**，
只有查找目标是 `msopprof`。

1. `--msprof` 显式覆盖。
2. PATH 中的 `msopprof`。
3. `$ASCEND_TOOLKIT_HOME` 下的 `tools/msopprof/bin/msopprof` 与 `bin/msopprof`。
4. `/usr/local/Ascend/ascend-toolkit/latest/bin/msopprof`。

全部找不到时退出 3，写 `summary.reason=MSPROF_NOT_FOUND`，不进入任何性能采样。

**可执行不等于可用。** 自动发现到的二进制还要跑一次 `msopprof --help`：退 0 且帮助文本
里有 `--launch-count` 才算可用，否则退出 3 并写 `MSPROF_UNUSABLE`；显式传 `--msprof` 时
不探测。这道探测把「旧 CANN 上有同名文件而不支持本协议」报成版本问题，而不是让每一例都
表现成算子失败。

产物布局有两种，取决于**实际采到的 launch 数**，不取决于 `--launch-count` 的取值：

| 实际采到 | 路径 |
| --- | --- |
| 1 个 | `OPPROF_*/OpBasicInfo.csv` |
| 多个 | `OPPROF_*/<kernel 符号名>/<序号>/OpBasicInfo_<时间戳>.csv` |

glob 必须递归覆盖两种：`OPPROF_*/**/OpBasicInfo*.csv`。只写扁平那条会在多 launch 算子上
扫空，表现为每例 `NO_KERNEL`，与「算子没跑起来」长得一模一样。

`OpBasicInfo.csv` 恰九列，**没有 `Task Type` 列**，按 task 类型过滤 kernel 行的旧做法
整体作废：

```text
Op Name, Op Type, Task Duration(us), Block Dim, Mix Block Dim,
Device Id, Pid, Current Freq, Rated Freq
```

只有 `Task Duration(us)` 进判据。`Device Id` 是物理卡号，当前只作诊断、不参与核对。

## 执行序列

1. 每个期望用例默认只采样一次（`repeats=1`），`--repeats` 可覆盖；每次采样都是独立进程、
   独立输出目录。依据：项目策略。
2. `--calls-per-case` **只接受 1**。读数已是该次进程全部 launch 的求和，再除一次就是二次
   归一；字段保留是因为它同时是复测绑定锚、`evidence_id` 的哈希输入项与 `check.json` 的
   契约比对项。依据：项目策略。
3. 每次采样前检查一次可用空间，阈值按**本次采集上限**算：`--launch-count × 2.2 MB × 1.5`。
   不足即退出 3 并写 `DISK_SPACE`。依据：实测。
4. 采集目录不落 `/dev/shm`。这是容量约束与风险提示，真正的保证来自第 3 条。
5. 原始目录默认保留，便于复核；`--keep-prof` 预留关闭策略。依据：项目策略。

空间阈值按上限算，不按上一例的实际占用外推——上一例可能只有一个 launch，下一例可能有
一百个。峰值系数大于 1，是因为工具先复制目录再删原目录。按默认上限算出的 1 GB 是检查
阈值，不是实际占用：单 launch 用例的产物仍是 1.8 MB。

## 执行成功证据与逐次判定

**执行成功证据**是本次采样的 `r<N>.gtest.json`：它证明「本次采样中 GTest 测试执行完成」，
不证明「进程正常退出」——GTest 在 RUN_ALL_TESTS 返回时写出该文件，其后进程清理阶段的
失败不可见。接受此边界的理由是 kernel 证据在测试体内已采完，清理段失败不影响其有效性。
该 JSON 里的耗时字段不作性能依据。依据：项目策略。

**证据合格**是正向条件，与精度侧 gtest JSON 解析同构：文件可读、可解析为 JSON 对象、
目标完整 gtest 名的记录存在、状态为已执行完成（非 SKIPPED/NOTRUN）、无 failure 记录。
读取、解析、结构校验的任何失败一律不合格，不允许对缺失字段按成功默认。依据：项目策略。

每次采样按下列顺序判定，先到先定：

| 顺序 | 条件 | 去向 |
| --- | --- | --- |
| 1 | 采集进程超时，或启动异常 | 逐例 `TIMEOUT` / `CRASH`，退出码 2 |
| 2 | 工具退出码非零，或日志出现写盘失败标记 | **整轮中止**，退出码 3 |
| 3 | 执行成功证据缺失或不合格 | 逐例 `CRASH`，退出码 2 |
| 4 | 无文件、无数据行、缺列、duration 非有限正数 | 逐例 `NO_KERNEL`，退出码 2 |
| 5 | 采到的行数达到 `--launch-count` | 逐例 `NO_KERNEL`，退出码 2 |
| 6 | 以上都不成立 | 计分 |

**环境问题排在算子证据之前。** 第 2 条不恢复旧的「非零退出即 `NO_KERNEL`」：工具失败是
环境问题，记成算子问题会让排错方向完全相反。写盘失败的标记是 `Copy failed`、
`Failed to save`、`No space left` 三串之一，命中即判环境错误——磁盘满时工具仍退 0、只刷
WARN 且不产 CSV，不认这些串就会落到第 4 条；磁盘满还可能先让 gtest JSON 写不出而撞第 3
条，所以只查「文件不存在」不够。第 2 条整轮中止而不是逐例记状态：对着满盘继续跑二百例
只会产出二百个假 `NO_KERNEL`。

不存在的 gtest 用例落在第 3 条：证据 JSON 里没有该用例的记录，判 `CRASH` 而非 `NO_KERNEL`。
逐例 `MISSING`（期望用例未能映射到 `--gtest_list_tests` 的完整名，枚举缺席、从未执行）
与 `CRASH` 的分界是前者没执行到。任一次采样落入第 1 到第 5 条即终止该 case 并按该状态
裁决，不用其余成功样本掩盖失败；repeat 序号、采集工具退出码与证据文件路径写进逐例
`warnings`。

## 进度反馈

量具把进度逐条打到 stderr 并即时 flush，共三种行。依据：项目策略。

- 开始一行：`性能采样:N 例,预估 ~X min`。N 是筛选后期望用例总数（含 MISSING），
  X 按每例 6 秒乘采样次数估算，只作提示、不参与判定。
- 每例一行：`[k/N] <case> <status> <本例耗时s> 累计<m>m ETA<m>m`。计时用单调时钟，
  ETA（预计剩余时间）按已完成用例的平均耗时估算。
- 结束时打一行汇总。

A4 由 agent 直接启动量具进程，stderr 天然实时到终端，accept 不做转发。

## 产物解析

一次采样的读数是**递归 glob 命中的全部文件、全部数据行的 `Task Duration(us)` 之和**，
不再按 `calls_per_case` 做除法。命中的行数即该次进程实际发生的 kernel launch 数，记为
逐例 `launches`。多份文件、多个数据行是多 launch 算子的**正常形态**：cherk 每次 API 调用
发 6 个 kernel（1 个反交织、4 个 GEMM、1 个合并）。

计分要求四条同时成立：至少一份文件、至少一个数据行、`Task Duration(us)` 列存在、每个值
都是有限正数。任一不成立按上节第 4 条记 `NO_KERNEL`，不以 0 代替。

**采到的行数达到 `--launch-count` 时判截断，不计分。** 此时无法区分「恰好这么多」与
「被截断了」，而截断的后果是求和少算、ratio 虚高、假 PASS，是本协议唯一会把 FAIL 写成
PASS 的路径，所以取 fail-closed。工具撞上限后静默停止采集后续 kernel、不报失败：
gtest 成功、CSV 合法、求和得出一个偏小的数。

截断的措辞落在「当前采集口径不支持该用例的 launch 规模」，**不说成算子失败**。提高
`--launch-count` 能解决一部分，但产物体积随实际 launch 数线性增长，上限不能无脑拉满；
默认 512 是按已知反例留的余量，够不够由这条检查保证。

## 统计与比对

| 项 | 计算 | 依据 |
| --- | --- | --- |
| `samples` | 每次采样命中的全部 kernel launch 的 duration 之和 | 依据：项目策略 |
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

基线里同一规范化键出现多行时**首行生效**：首行 `gpu_ms` 为空即按无基线处理（该例计入
`ignored_no_ref`），后续重复行不覆盖首行，只记 warning 进 `baseline_warnings`；表头、
非数值、≤0 这些逐行校验对所有行照常执行。规范化基线文件保留全部行，所有读取点（量具与
accept 的 A2/A5 消费）按同一规则选行。依据：项目策略。

基线 `timing_scope` 不是 `kernel` 时，每例 verdict 加 `(scope caveat)`，summary 写
`scope_caveat=true`，基础状态与退出码不变。依据：项目契约。

## 结果与退出码

结果原子写入 `results/performance_<run_id>.json`，临时文件成功关闭后才替换目标文件。

顶层记录 `calls_per_case`、`ignored_no_ref`（无基线被忽略的用例名）与 `baseline_warnings`
（基线告警文本数组，如「第 N 行键重复，首行生效」）。`baseline_warnings` 同时打到
stderr，只进性能 JSON 与 stderr，不进 A5 报告渲染。顶层 `msprof` 字段记采集二进制的解析
结果，accept 从不读它。

每例除身份字段外，
还记录 `kernel_us`、`samples`、`launches`、`gpu_ms`、`ratio`、`spread`、`status`、`verdict`、
`warnings` 与诊断消息。逐例 `warnings` 与顶层 `baseline_warnings` 分开，在逐次判定时写入。
summary 记录计数、`status`、`timing_scope`、`threshold` 与 `scope_caveat`，`status` 只取
`通过/不通过/NO_REF/证据不足`。依据：项目契约。

| 退出码 | 条件 | 依据 |
| --- | --- | --- |
| 0 | 无失败和证据缺口；期望集为空时 summary 为 NO_REF | 依据：项目策略 |
| 1 | 至少一个可比较用例 FAIL，且没有证据缺口 | 依据：项目策略 |
| 2 | 任一 NO_KERNEL、CRASH、TIMEOUT 或 MISSING | 依据：项目策略 |
| 3 | CSV、构建、二进制、列表、基线或采集环境问题 | 依据：项目策略 |
| 4 | 起跑门失败：目标卡忙或查询失败（见 run-chain.md「A1 环境」） | 依据：项目策略 |

证据不足优先于数值失败，因为存在未完成的期望用例。依据：项目策略。

起跑门只查目标卡，换卡映射串里的占位卡不查，占位卡被占用的后果见
retest-protocol.md「换卡复测」；复测下起跑门失败按中断轮处理，见
retest-protocol.md「启动与恢复」。

**逐例状态词表封闭，恰七个值**：`PASS`、`FAIL`、`NO_REF`、`NO_KERNEL`、`CRASH`、
`TIMEOUT`、`MISSING`。采集环境失败不是新状态，它整轮中止并退出 3，`summary.reason` 取
`DISK_SPACE`、`DISK_WRITE_FAILED`、`PROFILER_FAILED`、`MSPROF_NOT_FOUND`、
`MSPROF_UNUSABLE` 之一，与 CSV、构建、基线等既有环境原因并列。往词表里加一个值是对外
契约扩张，折叠核会把不认识的状态判成无效轮。

## 证据保护

对象分三类，规则不同：

- **原始证据**：各轮结果 JSON 与各阶段目录内的采集产物，一经写出不可变。量具在建目录、
  编译、探卡之前先检查目标结果 JSON：已存在即拒绝退出（`RUN_ID_EXISTS`），不向该路径
  写任何内容。阶段目录里已写入的采集产物同属此类，不自动清理。
- **派生裁决**：verdict.json 与报告，随每次 A5 重跑覆盖——它记录输入清单与规则版本，
  随时可由原始证据重演。
- **归档副本**：产物目录 `intermediate/` 下的复制件，每次 A5 在临时目录整体生成后替换。

失败落盘按三类区分：

1. **单例终态**：逐例 `CRASH/TIMEOUT/MISSING/NO_KERNEL` 是正常记录，量具继续处理
   其余 case，最终写出完整结果 JSON。
2. **首轮环境失败**：照常写出错误 JSON（起跑检查保证目标路径是新路径）。退出 3 的人工
   处置保留（见 run-chain.md「A4 性能」：删目录与 JSON 后同 id 重跑一次），那是人工授权
   的显式删除，只适用于首轮。
3. **量具异常终止与复测轮环境失败**：不写结果 JSON，阶段目录与日志留存，复测轮因此成为
   中断轮（定义见 retest-protocol.md「术语与信任模型」），不删目录重用轮号，失败就弃号
   换下一号，错误原因从日志与 stderr 读取。

## 复测与豁免

**首轮**（既有 A4 产出的 `performance_<id>.json`）之后，可绑定同一 run-id 追加**复测轮**：
**测量轮**重新采集点名 case，**豁免轮**宣布 case 退出裁决分母。记录 schema、轮次有效性、
折叠规则、启动流程与 verdict 增量整体收在 [retest-protocol.md](retest-protocol.md)。
与本协议的衔接点有四：

- 复测轮 run-id 形如 `<id>-retest-<k>`，且不接受 `--out`。
- 量具不读任何历史 JSON，折叠只在 A5 实现。
- 量具在首轮 JSON 顶层写出 `normalized_baseline_sha256` 与 `verifier_sha256` 两个绑定锚
  （`threshold`、`calls_per_case`、`binary_sha256`、`csv_sha256` 首轮已有），供复测轮校验
  「测的还是同一对象」。
- 复测轮可改用别的物理卡，量具按 `--compiled-device` 与 `--map-device` 重映射并写出
  `device_compiled`；A5 只核对这些自报字段，不独立核对采集落点。

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

下列事实在 A3 机实测确认。**A3 机**是一台实测机器的别名（ascend910_93，CANN 9.0.1），
与验收链的 A3 精度阶段同名不同物；本文提机器一律写「A3 机」。

- 退出码不透传被测程序失败，工具自身参数或初始化失败才退非零；崩溃判定因此只看执行
  成功证据。
- 单 launch 用例耗时：`--aic-metrics=BasicInfo` 档 4.6s，默认档 6.2s；产物约 2.2 MB
  每 launch。
- 预热无效：`--warm-up` 取 0 / 5 / 50 得 26.70 / 27.34 / 27.16 us，噪声级差异，重放模式
  本身绕开首次调用惩罚。预热旋钮因此整体撤除。
- 磁盘满时工具仍退 0，只在日志刷 `Copy failed` 且不产 CSV（容器 `/dev/shm` 仅 64 M）。

### 口径变更记录

采集后端从 msprof 换成 msopprof 是**口径变更，不是等价替换**。同机同用例双跑，三例都是
单 launch 用例，与 launch 计数无关：

| 用例 | msprof | msopprof | 比值 | 绝对差 |
| --- | --- | --- | --- | --- |
| TC_PF_1001 | 23.78 us | 15.30 us | 0.64x | 8.48 us |
| TC_PF_1002 | 45.40 us | 35.72 us | 0.79x | 9.68 us |
| TC_PF_1003 | 68.50 us | 57.26 us | 0.84x | 11.24 us |

**新后端系统性读低。** 绝对差基本恒定在 8.5 到 11 us，是固定的单次调用开销被重放绕过的
特征，规模越小受影响越大。msprof 量的是含首次调用惩罚的单次冷调用，msopprof 量的是重放
稳态。NPU 侧读数变小会让 ratio 变好，卡在阈值 `0.8` 附近的用例可能从 FAIL 翻成 PASS。

哪一种更接近 GPU 基线的口径**无法判定**：基线的 `perf.meta` 六个键当前全是 `unspecified`，
其中就包括 `warmup`。所以只能说换了口径，**不能宣称换后端更准**，历史数值与当前数值
不可直接比较。

跨后端折叠的洞由既有绑定锚堵住，不需要新增防护。`verifier_sha256` 是量具脚本自身的哈希：
换后端就是换脚本，哈希随之变化，拿新量具给旧首轮追加测量复测会被判成无效轮，要复测就用
新量具重跑首轮。**旧首轮加已有的旧复测轮仍正常折叠**——绑定锚比较的是复测轮与它自己的
首轮，不是与当前量具。

### 待实测

- 多 launch 算子的端到端正确性。cherk 源码上每次调用 6 个 kernel，是嵌套布局与求和的
  正例；ctpmv 与 sger 的单 launch 结果是阴性证据，不能替代。依据：待实测。
- 截断路径。用 `batchCount` 较大的 `gemm_strided_batched` 用例，或人为把 `--launch-count`
  调到小于实际 launch 数，确认 fail-closed 触发且措辞落在采集口径上。依据：待实测。
- 空间阈值的峰值系数 1.5 是否够用，以及峰值与最终目录大小的差。依据：待实测。
- 量具在 `sparse_frame` 剖面下共用同一份模板，换后端后的端到端行为。依据：待实测。
- 新平台或新 CANN 上产物布局与九列列名是否一致；不一致时无文件落 `NO_KERNEL`，
  不会假 PASS。依据：待实测。
- 被测进程中途死的端到端形态：计分与环境失败两条路径已端到端直证，「执行成功证据缺失」
  仅有函数级直证。依据：待实测。
