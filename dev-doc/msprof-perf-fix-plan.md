# msprof 性能通路修复实施计划(v3 交接版,已过 Codex 七维评审并吸收)

**状态:2026-09-13 Mr.0 已批准;wave 0(本文件落盘)已完成;wave 1 起未执行。**

> 本文档自包含,面向新 session / 新分支执行。执行者不需要本 plan 之外的对话上下文。
> Codex 评审结论 NEEDS REVISION 的全部 P1/P2 已吸收进本版;评审线程
> `01a09da2-7bdf-7351-ab6b-f2f7ca20ab89`(可 `/cc-suite:continue` 追问,非必需)。

## 0. 交接引导(新 session 先做)

1. 分支:从 `feature/sparse-r1` 切新分支(建议 `fix/msprof-perf-pipeline`),在 worktree
   `OpRunway/.claude/worktrees/oprunway-sparse-r1` 或新 worktree 执行。
2. 落盘本 plan:全文存入 `dev-doc/msprof-perf-fix-plan.md`(仓规:开发记录只进 dev-doc)。
3. 过门(每个会改 skill 文件的 session/subagent 各一次):
   Read `.claude/hooks/skill-best-practices.md` → `Skill(skill-creator)`。否则写入被 hook 拦。
4. 背景材料(按需读):`dev-doc/msprof-perf-issues-summary.md`(缺陷 A1–A8 与定案)、
   `dev-doc/msprof-op-summary-double-count.md`(A1 根因)。
5. 真机:凭 `.oprunway/real-machine.env`(先读 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`,保护根只读);
   一切构建/测试/验收在远程 NPU 容器内,本机只编辑与 Git(仓规 AGENTS.md §3)。

## 1. 背景与定案(Mr.0 已裁,不再重开)

msprof 性能通路(BLAS 链)已确认缺陷:A1 双份计数假 FAIL、A2 默认采集参数不足致 NO_KERNEL、
A3 基线重复键整程崩退 3、A7 全程 ~3.5h。定案:

| 定案 | 消掉 |
|---|---|
| 每 case 单次采样、免 warmup、不批量(200 例=200 次 msprof) | A7(~6×,→~30min) |
| 不跑显式 `--export`,只靠 `--application` 自动导出 | A1(源头) |
| 补 `--ai-core=on --task-time=on` | A2 |
| 基线重复键容忍(统一首行生效+warning),不再崩 | A3 |
| 不留运行时兜底(无 md5 去重/launches 门);缺数自然落 NO_KERNEL | Mr.0 裁 |
| 逐 case 进度反馈 | Mr.0 增 |
| 独立「产物目录」参数(A5 产物可指到工作目录外) | Mr.0 增 |

**硬边界**:只改 `plugin/skill/repo-task-blas-case-gen/` 与 `plugin/skill/repo-task-blas-accept/`
(加 dev-doc 记录);其他 skill、plugin 根、仓根规则不碰。
**改动主体**:模板 `repo-task-blas-case-gen/assets/template/verify_performance.py` 是权威源,
`assets/example/{sasum,cherk}/verify_performance.py` 是渲染产物(重渲染,不手改);
编排 `repo-task-blas-accept/scripts/accept.py`。

## 2. 冻结契约(并行实施前的跨文件语义,先钉死)

C1 采集命令(每 case 恰一次):
```
msprof --application="<bin> --gtest_filter=<name>" --ai-core=on --task-time=on --output=<dir>
```
无第二步 export、无外部 warmup、默认 repeats=1(保留 `--repeats` 覆盖口)。
op_summary 由 `--application` 自动导出;无文件 → launches==0 → NO_KERNEL(既有路径,不加新门)。

C2 基线重复键规则(**唯一不变量:同键处处同选择**):所有读取点(模板 `_load_gpu_baseline`
与 accept.py `_load_baseline`,含 A2/A5 消费)统一「按规范化键,首行生效——首行 gpu_ms 为空即
NO_REF,后续重复行不覆盖」。每行既有校验(表头/非数值/≤0)对所有行照常执行,重复键不再 raise,
记 warning。规范化基线文件保留全部行,只统一选择规则(改动面最小)。

C3 warning 字段:量具 payload 增 `baseline_warnings: [str]`(如「第 N 行键重复,首行生效」),
同时打到 stderr。本轮只进 performance JSON + stderr,不改 accept 报告渲染(可读性遗留另记)。

C4 进度反馈(量具 stderr,逐条 flush):
- 开始:`性能采样:N 例,预估 ~X min`(N=筛选后 expected_rows 总数,含 MISSING;X=N×单次预估)。
- 每例一行:`[k/N] <case> <status> <本例耗时s> 累计<m>m ETA<m>m`(单调时钟;ETA 按已完成均值,
  首例前显示「待估」)。
- 结束:汇总一行。A4 由 agent 直接起量具进程,stderr 天然实时到终端,无需 accept 转发。

C5 产物目录寻址不变量(**输出位置不得影响证据来源**):`--out` 只决定写入位置;
`_contract_summary` 的 check.json 证据源钉死为工作目录(cwd)一处,归档只复制这份。
产物目录只辖 A5 三类产物(report/、intermediate/、repro/);runtime 与 profiling 数据仍在工作目录。

C6 单样本统计退化:samples/median/spread 字段结构不动;repeats=1 时 median=该值、spread=0,
协议明写「spread=0 表示无样本间差异可算,不是稳定性证明」。

C7 术语(冷读要求,协议文本首次出现处就地定义):A3 三义消歧(缺陷编号/验收阶段/机器别名)、
launches(解析保留的 kernel 行数,≠API 调用次数,不作去重依据)、三种预热区分(已删的外部
warmup 进程 / harness 内部调用 / msprof 工具内部行为——后者来源=用户裁定,真机核实中)。

## 3. 工作分解

### W1 模板 verify_performance.py(S1 独占)
1. 删 warmup 块(:622-634)。**语义为有条件迁移**:崩溃 case 的观察对象变为 msprof 进程退出码,
   msprof 是否透传 application 失败属真机核实项(V-3);协议按「条件成立→CRASH/退 2」表述,
   并注明上游 A3 精度门已挡住多数崩溃 case(性能轮只暴露"精度过但 profiling 下崩"的窄面)。
2. `REPEATS = 5 → 1`(:175),`--repeats` 保留(:785)。
3. 采集命令(:636-644)加 `--ai-core=on --task-time=on`。
4. 删显式 export 块(**:657-671 整块**,含 export 退非 0 分支与其 return;删后成功采集直进解析)。
5. `_load_gpu_baseline`(:471-509,raise 在 :497):按 C2/C3 改。
6. 进度反馈按 C4,主循环(:933 附近)落点。
7. 清理两步导出/5 次/warmup 相关注释与 docstring。

### W2 契约文档(S2 独占,四份+概念清扫)
- `repo-task-blas-accept/references/perf-protocol.md`(权威):采样节(:30-33)、命令节(:34-37)、
  :45 repeats 默认、:83 中位数(C6)、:19 进程隔离句去 warmup、:131 附近导出前提句;
  删 FAIL(warmup);增:C2 重复键规则、C3 字段、C4 进度说明、C7 术语定义;
  历史实测句加版本/机器限定,不与现行规则并列成矛盾指令。
- `repo-task-blas-case-gen/references/perf-protocol.md`(旧版):同节同文收敛,并移植权威版
  「不得再除以 launches」条款(:38-42)。
- `repo-task-blas-accept/references/run-chain.md`:A4 参数(repeats 默认 5 → 1,:247 附近)、
  A5 `--out` 语义与三类产物布局(C5)。
- `repo-task-blas-accept/SKILL.md`:入口参数表加「产物目录」(可选,缺省 `<工作目录>/verdict`);
  A5 命令 `--out <产物目录>`;「结论报告」路径改真实布局 `report/report.md`、
  `intermediate/verdict.json`(顺修 :163 现存一层之差);注明产物目录只辖 A5。
- 清扫法:两 skill 内 grep `warmup|repeats|export|去重|5 次` 逐处核对,防漏(Codex F5)。
- **不动**:0.8 阈值、退出码表、ratio 定义、「证据不足优先」。两份协议「阈值可否覆盖」的
  既有分叉不在本轮,单列 todo。

### W3 accept.py(S3 独占)
1. `_load_baseline`(:245 附近)按 C2 统一首行生效(A2 :743 与 A5 :1560 消费点核对)。
2. C5 证据寻址:`_contract_summary`(:1163)去 out_dir.parent 搜索,钉死工作目录;
   归档(:1381)只复制该份,消除同名覆盖。
3. grep `warmup|repeats|spread|FAIL(warmup)` 消费面核对(交叉校验 :1120-1128 预计不受影响,验证)。
4. ~~进度流式转发~~(撤销:1451 是写 rerun.sh 的字符串,accept 不执行量具)。

### W4 收尾配套(wave 2 主会话)
- 重渲染 example 两份;交叉一致性核对(渲染 diff 仅预期块、C1-C7 与四份文档互证)。
- `dev-doc/oprunway-changes-brief.md` 顶部追摘要;todo 对应条目**只标「已实现,待真机」**,
  关闭须等 wave 4(Codex F16)。
- 不写单测(上游纪律:不做 TDD、不为脚本补单测;验证=真机);不跑本地 pytest(测试属远端)。
- `repo-task-blas-accept/CLAUDE.md`「真机验证过的事实」表:wave 4 后按实测更新
  (warmup+5 次流水线行、spread 行),复验前不预填。

## 4. 执行结构

```
wave 0(串行):交接引导 §0;契约 §2 随 plan 批准即冻结
wave 1(并行):S1=W1 ∥ S2=W2 ∥ S3=W3 ∥ V0=真机前提穿刺(见 §5,无需改码,授权后即可跑)
wave 2(串行):W4 集成收尾(主会话)
wave 3(串行):Codex checkpoint(改裁决口径+对外契约,无条件;codex-review.md 七维;
              若 wave 4 结果再改裁决/契约,重新触发)
wave 4(串行):真机全量回归(§5 V-4/V-5)→ 更新事实表与 todo 关闭
```
- S1/S2/S3 文件零交集(模板 .py / 四份 .md / accept.py);语义交集已被 §2 冻结消除。
- 每个 subagent 任务书含:过门前置(§0.3)+ 硬边界(§1)+ 所辖契约条款原文。
- V0 与 S1-S3 并行:穿刺用现有二进制手跑新命令形态,不依赖新代码。

## 5. 真机验证矩阵(需 Mr.0 授权机器;逐项记录机型/CANN/msprof 版本与命令)

| # | 验什么 | 怎么验 | 通过判据 |
|---|---|---|---|
| V-1(V0) | 自动导出产 op_summary 且恰一份 | 新命令形态跑 2-3 个 case,数 `PROF_*/mindstudio_profiler_output/op_summary_*.csv` | 每 case 恰 1 份;文件内 kernel 行数与预期调用一致(单文件内无重复行) |
| V-2(V0) | 单次不偏冷 | 同卡同 calls_per_case,代表尺寸(大/中/小)各做 ≥5 次独立单采样,对照同卡 aclrtEvent(或去污染后的历史中位数);偏差=\|single−ref\|/ref | 分布无系统性偏慢;中位偏差 ≤10%;首次 vs 后续无显著台阶 |
| V-3(V0) | msprof 是否透传 application 失败 | 故意跑一个必挂的 filter | 非 0 退出 → CRASH 路径成立;若透传不成立,回 plan 补执行成功证据的设计再实施 W1.1 |
| V-4 | 全链回归 | cgeru(真值 aclrtEvent 200/200 PASS)走 A1→A5 全链(正式口径用 isolated-acceptance) | 200/200 PASS、0 NO_KERNEL、总时长 ~30-40min、进度行实时可见、A5 报告/退出码一致 |
| V-5 | 变更面专项 | ①重复键基线(空值在首/在后两种排列)小包跑 A2/A4/A5;②产物目录指工作目录外(父级预置同名 check.json);③无 summary 场景(如错误 SoC) | ①三阶段同一期望集、warning 可见;②verdict 不受输出位置影响、无覆盖;③NO_KERNEL/证据不足,不误判 FAIL |

## 6. 风险与止损

- 回滚单位=**整候选版本**(代码+协议+渲染产物一起),不做逐项回退——恢复 export 会重新引入
  A1,恢复 repeats 不恢复 warmup,半回退必致代码与契约失配(Codex F2)。V0 任一不过:停在
  wave 1,修订 plan,不带病进 wave 2。
- 残余风险(如实陈述):「msprof 内部预热」在 `--application` 模式无公开文档背书(公开预热参数
  属 `msprof op`),V-2 是唯一证据来源;未测平台(新 CANN/SoC)如实标待验,缺数自然落
  NO_KERNEL/证据不足,不会假 PASS。
- 语义迁移(FAIL(warmup)→CRASH)为有条件结论,以 V-3 为准;评审专核。
- 局部证据不得表述为算子正式通过;commit 不加 AI 署名(仓规)。

## 7. Codex 评审记录(2026-09-13,gpt-6-astra/xhigh,NEEDS REVISION→已吸收)

P1:基线规则两处不一致(→C2/W3.1)、W6 证据寻址非零改动(→C5/W3.2)、回滚说法不成立(→§6)、
删 warmup 语义无条件化(→W1.1/V-3)、真机覆盖不足(→§5)、前提核验太晚(→V0 提前)。
P2:文档清扫漏项(→W2)、进度层级找错(→W3.4 撤)、export 块行号 657-671(→W1.4)、
重复键校验顺序/warning 接口(→C2/C3)、进度计量语义(→C4)、V-2 判据不可执行(→已定量)、
todo 提前关闭(→W4)。七维低分项:冷读 2/5(→C7 术语条款)、复杂度 3/5(→C2/C5 两条不变量)。

## 附录 A. V0 实测记录与 W1.1 修订(2026-09-14,状态:V-3 已实测,修订待 Codex 评审+Mr.0 裁定)

### A.1 V0 环境与 V-1 结果

环境:ascend-a3 机(Mr.0 逐项授权 V-1/V-2/V-3 后改授权至 A3 全套),容器 `oprunway_prov`,
Ascend910(910_93/arch22),CANN 9.0.1,msprof `/usr/local/Ascend/cann-9.0.1/bin/msprof`,device 1。
载具:sger_test(oprw-sasum-20260825a 既有 Release 构建)+ ctpmv arch22(20260911 FAIL 现场同源码,
本轮新构建)。工作目录 `/home/l30066237/oprw-msprof-v0/`。

V-1(自动导出)**通过**:新命令形态(C1,含 `--ai-core=on --task-time=on`,无显式 export)跑
sger TC_PF_001/002/003(1024×1024、512×2048、256×256),每例 msprof 退 0、
`PROF_*/mindstudio_profiler_output/op_summary_*.csv` **恰 1 份、kernel 行恰 1 行**,无重复行。

### A.2 V-3 结果:透传不成立(触发计划预设分支)

| application 形态 | profiling 数据 | msprof 退出码 | 备注 |
|---|---|---|---|
| 正常跑 NPU case,退 0 | 有 | 0 | V-1 三例 |
| 跑 NPU case 后 `exit 7` | 有 | **0** | 仅 stderr WARNING,且把 7 按 errno 误译为 "Argument list too long" |
| 跑 NPU case 后 SIGSEGV | 有 | **0** | 同上,误译 "Resource temporarily unavailable" |
| 失败且无 NPU 业务 | 无 | 255 | 「找不到 profiling 数据」的分析失败,非透传 |
| 跑 NPU 中途被 KILL | 无 | **0** | op_summary 0 份 |

结论:CANN 9.0.1/910_93 下 msprof **不透传** application 失败;只要分析流程完成即退 0。
W1.1 原表述「崩溃观察对象=msprof 退出码」不可实施,按计划 V-3 条款「回 plan 补执行成功
证据的设计再实施 W1.1」。

### A.3 W1.1 修订 v2:执行成功证据 = gtest JSON 结果文件
(v1 经 Codex 评审 NEEDS REVISION,F1-F12 已按七维逐项吸收,见 A.5)

C1 修订(唯一命令改动:application 串追加一个 gtest 原生参数,仍单命令、无新进程、无第二步;
参数由既有 `shlex.join` 构造,JSON 路径用**绝对路径**且**按采样定址**,F4/F7):

```
msprof --application="<bin> --gtest_filter=<name> --gtest_output=json:<case目录绝对路径>/r<N>.gtest.json" \
       --ai-core=on --task-time=on --output=<dir>
```

**证明边界(F5,先钉死)**:`r<N>.gtest.json` 是「本次采样中 GTest 测试完成证据」,不是
「进程正常退出证据」——gtest 在 RUN_ALL_TESTS 返回时写出该文件,其后进程清理阶段的失败
不可见。接受此边界的理由:kernel 证据(op_summary)在测试体内已采完,清理段失败不影响
其有效性;为捕获终态再加 wrapper 脚本层被 v1 评估否决(机制+1,违背最少机制)。

**JSON 合格定义(F3/F9,正向条件,与精度侧 `_gtest_records` 同构)**:本次采样的
`r<N>.gtest.json` 可读、可解析为 JSON 对象,且其中**目标完整 gtest 名**的记录存在、
状态为已执行完成(非 SKIPPED/NOTRUN)、无 failure 记录。读取、解析、结构校验的**任何**
失败一律不合格——不允许对缺失字段按成功默认。

判定顺序(每次采样;**只替换旧「msprof 退出码→CRASH/export 分支」段,既有 `problem`
分支(TIMEOUT/启动异常)优先级不变,CSV/枚举等环境失败路径不变,F2**):

1. `r<N>.gtest.json` 不合格(含缺失)→ **CRASH**,退 2。诊断措辞为「执行成功证据缺失/不合格」,
   不断言算子必然崩溃(F8)。
2. JSON 合格但该次 msprof 退出码非 0 → **NO_KERNEL**,退 2(采集或分析未完整,产物不可信,
   **不计分**——即使 op_summary 存在也不读,防部分导出的 CSV 少算耗时抬高 ratio,F6)。
3. JSON 合格、msprof 退 0、op_summary 缺失或无 kernel 行 → **NO_KERNEL**(既有路径)。
4. 全合格 → 计分。任一次采样落入 1-3 即终止该 case 并按该状态裁决,不用其余成功样本
   掩盖失败(F4)。

warning 接口(F10):逐例 record 增 `warnings: [str]`(与顶层 `baseline_warnings` 分开),
文本含 repeat 序号、msprof 退出码、证据文件路径;在判定时记录。协议同步定义该字段。

### A.4 验证矩阵与放行顺序(F11/F12)

实测直证(sger/910_93,手工新形态):①正常形态 JSON 落盘(tests=1, failures=0)且
op_summary 恰 1 份;②中途 KILL 则 JSON 缺失、op_summary 0 份、msprof 仍退 0;
③filter 不匹配时 gtest 也退 0、JSON 写出但 tests=0 且 testsuites 为空——只看 failures==0
会假 PASS,目标 gtest 名核对(F9)是判定 1 的必要条件,已直证。
**尚未直证**:失败 JSON、合格 JSON+无 summary、合格 JSON+msprof 非 0 三形态——FAIL JSON 的
结构解析有精度侧 `_gtest_records` 先例,其余以 S1 补丁落地后的**修订版 V-3 复验**补齐
(小包真机跑通判定 1-4 各至少一例,可构造:filter 不匹配→判定 1;删 PROF 目录重放→不适用,
以 timeout 杀 msprof 子过程或断 device 构造 2/3,做不出的形态如实记录为待 V-5)。

放行顺序:冻结本附录判据与接口 → S1(模板)/S2(协议)补丁并行 → 修订版 V-3 复验 +
V-2(单次不偏冷,进行中:ctpmv Release 构建后按 §5 V-2 判据跑)→ 全部通过才进 wave 2
集成;任一不过回本附录修订。V-2 结果与适用平台随测随记于 A.6。

### A.5 v1→v2 修订对照(Codex 评审 2026-09-14,gpt-6-astra/xhigh,NEEDS REVISION)

P1:F3 统一「无成功证据」默认(→合格定义)、F4 按采样定址(→r<N>.gtest.json+循环内构造)、
F5 证明边界(→部分吸收:明确边界,不加终态机制)、F6 msprof 非 0 不计分(→判定 2 升门)、
F9 合格=目标名执行完成(→合格定义)、F11/F12 验证矩阵与放行顺序(→A.4)。
P2:F1 协议 NO_KERNEL 前提同步(→S2 补丁范围)、F2 只替换退出码段(→判定顺序前置声明)、
F7 shlex+绝对路径、F8 能力前提(精度侧同一二进制已用 gtest JSON)与诊断措辞、F10 warnings
接口冻结。全部吸收,无拒项;F5 拒了「补终态证据」的可选方案,理由=最少机制。

### A.6 影响面与 V-2 记录

影响面:模板 verify_performance.py(S1 补丁:判定 1-4、r<N>.gtest.json、warnings 字段)、
两份 perf-protocol(S2 补丁:C1 命令行、CRASH 节、NO_KERNEL 前提「以执行证据合格为先」、
warnings 字段、术语「执行成功证据」就地定义)+ troubleshooting.md 与 README 模板对应句
(已在 wave 2 先行清扫的部分不含 CRASH 判据,补丁时一并核对);accept.py 不涉及
(A5 不复核 gtest JSON,正确性由模板保证——评审确认此边界成立)。渲染产物随 W4 重渲染。

V-2 记录(2026-09-14,**通过**):ctpmv arch22 **Release** 构建(CMAKE_BUILD_TYPE=Release 经
env 注入 build.sh——该脚本不处理构建型态,默认 Debug,brief 已记 Debug 乘数 31-34x,
本轮先中招后纠正),ascend-a3/910_93/CANN 9.0.1/device 1,新命令形态(C1+gtest_output)。
三代表尺寸各 5 次独立单采样(Task Duration 求和,µs):

| case | n | 5 次样本 | 中位 | 参照(910_93 单份) | 偏差 |
|---|---|---|---|---|---|
| TC_PF_1001 | 512 | 23.26/23.58/23.68/22.54/23.12 | 23.26 | 22.88 | 1.7% |
| TC_PF_1002 | 1024 | 45.10/43.76/45.16/44.92/46.36 | 45.10 | 45.38 | 0.6% |
| TC_PF_1003 | 2048 | 66.66/68.00/68.34/69.76/68.62 | 68.34 | 68.24 | 0.1% |

判据全过:中位偏差 ≤10%(实际 ≤1.7%);首例均在分布内,无冷启动台阶(1003 首例还是最小值);
每次采样 kernel 行恰 1(无重复行,与 V-1 互证)。A8 两个待验前提(msprof 单次内部行为不偏冷、
自动导出产 op_summary)至此均获真机直证。参照值来源:msprof-op-summary-double-count.md
的 a3 实测单 launch 表(Release 口径)。

### A.7 修订版 V-3 复验记录(2026-09-14,S1 补丁后,**通过**,V0 整体放行)

载具:S1 补丁后模板按 sger 值手工渲染(token 替换与 package.py 同法,py_compile 过),
`oprw-msprof-v0/v3r/`,sger_test(Release 既有构建),910_93/CANN 9.0.1/device 1。

| 判定线 | 形态 | 结果 |
|---|---|---|
| 判定 4 计分 | 端到端 3 例(基线取 V-1 实测值) | 3/3 PASS,ratio 1.00-1.02,退 0;launches=**[1]**(旧协议同环境为 [2,…]),spread=0 |
| 判定 1 证据不合格 | 函数级 `_gtest_evidence` 四形态 | 正确名 (True);tests=0、文件缺失、非目标名均 (False, 具体原因) |
| 判定 2 msprof 非 0 | 端到端(--msprof wrapper 采集正常后退 1) | NO_KERNEL 不计分退 2;warnings 含 r 序号/退出码/证据路径 |
| 判定 3 | 未新构造 | 既有路径未触碰(S1 diff 确认),A2 历史与 V-1 反证 |
| C2/C3 | 端到端(基线插重复键行 9.9) | stderr「第 N 行性能键重复,首行生效」,首行生效(未被 9.9 覆盖),payload `baseline_warnings` 就位 |
| C4 | 端到端 | 开始/每例([k/N] case status 耗时 累计 ETA)/结束三段 stderr 实时 |
| gtest 完成标记 | 真机 JSON 直证 | status="RUN"、result="COMPLETED" 同时命中 S1 双字面量;完整名=suite+"."+test 与 filter 一致 |

既有守卫未被扰动的旁证:假 case 被更早的 MISSING 门拦(枚举校验,证据不足退 2,不起 msprof);
重复 run-id 被 RUN_ID_EXISTS 拦(退 3)。单例耗时 ~6-7s,200 例外推 ~20-23min(A7 兑现)。
判定 1 端到端形态(msprof 起跑后 gtest 死)与判定 3 新构造留 V-5 专项,不阻塞 wave 2。

### A.8 wave 3 checkpoint 记录(2026-09-14,gpt-6-astra/xhigh,FIX_NEEDED→已修复复验)

评审对象=工作树全部改动。结论:判定顺序/C1/C2/C5/warnings/渲染主体全部成立;
FIX_NEEDED 3 P1+3 P2,无 P0,全部采纳修复:

- F1(P1)`_gtest_evidence` 完成标记曾写成二选一(`and` 拒绝)→改 status/result 双必要,
  缺失或未知一律不合格(fail-closed;旧版 gtest 无 result 字段时显式 CRASH,方向安全)。
- F2(P1)`failures or []` 放行畸形值、`failures=1` 抛 TypeError→字段存在时必须为数组,
  畸形即不合格;缺失仍视为无失败(gtest 正常省略)。
- F3(P1)README 模板末尾「两步实测」残句、readme-contract.md「预热与重复由量具负责」
  ——两处计划外清扫漏项,已改现行口径并重渲染。
- F4/F5/F6(P2)README 与 troubleshooting 的 NO_KERNEL 前提、两协议直证状态分形态标注
  (判定 2/4 端到端、判定 1 函数级、判定 3 未新构造;V-2 措辞限定「未观察到偏冷」而非
  「机制已证明」)、launches/ETA 首用短释。

修后复验:py_compile 过;example 重渲染零漂移;真机 `_gtest_evidence` 七形态全过
(正常/tests=0/缺文件/缺 result/failures 为 dict/int/非空列表);行长全绿。
Codex 另澄清两个 HEAD 既有分叉,非本轮回归、不修、记 todo:①accept 对非数值基线记 None
vs 模板拒绝非数值与非正数(校验严格度差异);②case-gen 协议 NO_REF「只采集不评判」旧句
与实际期望集过滤(无基线不跑)不一致。

### A.9 wave 4 部署裁定与载具适配(2026-09-14,Mr.0 授权)

Mr.0 裁定:V-5 授权 @ A3;**V-4 载具由 cgeru 改为 ctpmv @ A3**(cgeru 材料后到,
`20260910性能fail/` 含 950 源码树与任务包,但 cgeru 仅 arch35——若跑归 A5,另请授权)。

V-4 口径说明(如实):
- ctpmv 任务包(`community_task…/test_cases/` 六件)的 gen_csv.py 是任务方自由脚本,
  非本仓 FACTS 结构,A2 的 FACTS 机械门过不了(当年验收用的 `package_fixed` 未回传);
  故 V-4 执行 **A4 性能轮 200 例全量**(量具=新模板按 manifest 值渲染,CSV 与 0911 现场
  sha 逐字节同源 79f3…),覆盖 plan V-4 判据的 200/200、0 NO_KERNEL、时长、进度四条;
  **A5 一致性判据由 V-5 的 sasum 小包全链(A1→A5)覆盖**。isolated-acceptance 无头正式
  口径本轮未走,留正式验收通路(不据此宣称算子正式通过)。
- 载具适配(测试材料,不改 skill):任务方基线枚举为短形(UPPER/N),包 CSV 为全形
  (ACLBLAS_UPPER/ACLBLAS_OP_N),键规范化(既有行为)不做枚举映射→0 匹配;
  已把基线枚举展开为全形重跑。任务方真基线自带 3 处重复键(第 15/87/96 行)——
  旧协议在此直接 raise 崩退 3,新协议首行生效+warning 继续跑,A3 缺陷修复获真实现场直证。

V-5 设计:sasum(example 现行六件,A3 新构建 sasum_test,卡 0 与 V-4 卡 1 物理隔离):
①双排列复合基线(n=1 空在首→NO_REF;n=2 值在首空在后→计分;n=3/4 填值;余 196 行留空
控制期望集)跑 A1→A5 全链,验三阶段同一期望集+warning 可见;②verdict `--out` 外指+
父级预置同名 check.json,验 C5;③msprof wrapper 删 summary 构造判定 3 端到端直证。
sger-pkg 旧包(3 PF、旧代通用代码区)过不了现行 A2 机械门,弃用;基线值为构造值
(999/5.0),V-5 验的是选择规则与链路一致性,不代表 sasum 性能真值。

V-5 载具二次调整(执行中发现):example sasum 配的主干 `sasum_test` 不是 CSV 驱动
harness(`--gtest_list_tests` 被无视直接跑 demo)——README 契约的 harness 属任务方开发件,
主干树没有。改为 **sger FACTS 升级包**:旧 sger FACTS 区(声明式,合规)移植进现行模板
gen_csv,perf.rows 按现行 schema 补至 200 行唯一键,`package.py render` 全机械门通过;
200-PF CSV 部署到 `test/ger/sger/arch22/`(原 3-PF CSV 已备份,事毕还原);基线双排列同
前设计,键改 (1024,1024)/(512,2048)/(256,256),有值行取 V-1 实测(ratio≈1)。
另:sasum 链首跑时卡 0 有驻留进程,量具 `NPU_GATE_BUSY` 正确拒绝起跑(退 4)——
空闲门 fail-closed 又一真实现场直证。

### A.10 V-4 结果(ctpmv 200 例全量 @ A3,2026-09-14,**通过**)

`summary: 不通过,PASS=195,FAIL=5,NO_REF=0,证据不足=0`,退 1;总时长 **23.4min**
(旧协议同载具实测 ~3.5h,~9×;判据 30-40min 达标);进度行 [k/200] 全程实时;
**200 例 launches 全部 [1]**(0911 同 CSV 同现场为全 [2,…]),A1 零复发;0 NO_KERNEL。

5 例 FAIL 全落在 TC_PF_1005/1006/1007/1008/1010(n≤32 最小尺寸),ratio 0.468-0.721——
与 0911 去重重算后的已知窄面(TC_PF_1005-1010,ratio 0.45-0.80,Mr.0 已裁「待 aclrtEvent
复核」)完全同型,非本轮回归;实测 195 PASS 优于当年推算 192(0.8 线附近边界例的正常抖动)。
本结果为修复回归证据,不构成 ctpmv 算子的正式验收结论(正式口径走 isolated-acceptance)。

### A.11 V-5 结果与 wave 4 收官(2026-09-14,**三项全过**)

载具=sger FACTS 升级包(A.9),A3/910_93/device 1,`--ops=sger --device=1` 重建
(此前 sasum 构建覆盖了 built_tests.list,A2 `NOT_BUILT` 正确拦截过一轮)。

**V-5①(重复键双排列,A1→A5 全链)通过**:A2 退 0(双排列 warning「第 2/4 数据行键重复,
首行生效」,NO_REF 198 → 期望集 2);A3 精度 172/172 PASS;A4「通过」PASS=2 退 0
(warning 同规则再现;kernel 570.4/101.6µs 与 V-1 的 571.7/98.6 交叉一致);A5「结论:通过」
退 0,report/intermediate/repro 三类布局正确。三阶段同一期望集、warning 三处可见,判据全中。
(A5 首跑曾因主会话打包漏带 accept `assets/` 崩,补传后过——部署遗漏,非代码缺陷。)

**V-5②(产物目录外指)通过**:`--out` 指工作目录外,父级预置假 check.json——结论仍「通过」
且与①一致;假件未被读、未被覆盖;归档 check.json 为工作目录真件。C5 端到端直证。

**V-5③(无 summary→判定 3)通过**:wrapper 在真 msprof 退 0 后删 op_summary → 量具判
NO_KERNEL 退 2,不误判 FAIL,warnings 含 r 序号/退出码 0/证据路径。判定 3 端到端直证补齐;
另一次 wrapper 笔误意外直证了「msprof 未启动→JSON 缺失→CRASH」的判定 1 端到端形态。
四条判定线至此**全部端到端直证**(1:v5t3+函数级七形态;2:v3r6;3:v5t4;4:v3r2/V-4/V-5①)。

附带的既有守卫真实现场直证:NPU_GATE_BUSY(卡 0 驻留进程,量具拒跑退 4)、NOT_BUILT
(清单不含目标算子退 2)、PACKAGE_INVALID(AppleDouble 垃圾文件被数为第二个 CSV,退 2)、
MISSING(枚举校验退 2)、RUN_ID_EXISTS(退 3)——全部 fail-closed 正确。

历史闭环:旧事实表「sger 5 次中位 613/1139/197µs」恰为本轮单份实测 306/571/99µs 的 2×,
旧实测本身即 A1 双份计数口径,已在两 skill CLAUDE.md 事实表作废重写(W4 条款履行)。

侵入清理:sger 部署 CSV 已还原 3-PF 原版,sasum 部署 CSV 已删,假 check.json 已删;
`oprw-msprof-v0/` 全部穿刺与回归产物保留作证据;built_tests.list 现为 sger(构建副产物区,
与 build 内有效二进制一致)。**wave 4 完成;todo 三条按此关闭,遗留项见 todo。**

补测(2026-09-14):`--repeats 3` 路径真机回归过(中位/spread/逐采样证据定址均正确)。
**Mr.0 裁定:一个 case 只测一次**——`--repeats` 仅为保留的覆盖口,任何流程不使用。
至此改动区全部活路径均有真机直证;未测项仅余未改动分支(TIMEOUT/problem、A5 不通过传导)
与平台泛化(950/910B3/新 CANN,协议已列待实测,缺数落 NO_KERNEL 不假 PASS)。
