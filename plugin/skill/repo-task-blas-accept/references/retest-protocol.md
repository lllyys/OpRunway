# 性能复测与豁免协议

## Contents

- [术语与信任模型](#术语与信任模型)
- [复测轮记录](#复测轮记录)
- [换卡复测](#换卡复测)
- [轮次有效性](#轮次有效性)
- [折叠](#折叠)
- [启动与恢复](#启动与恢复)
- [verdict 与报告增量](#verdict-与报告增量)

本协议只改性能侧：精度量具、精度侧 `-rerun` 复跑与首轮闭合要求（见 run-chain.md
「A5 结论」的集合闭合）都不变。证据写入的不可变规则见 perf-protocol.md「证据保护」，
warmup 语义见 perf-protocol.md「warmup」，性能期望集定义见 perf-protocol.md
「适用范围」。

## 术语与信任模型

| 术语 | 定义 |
| --- | --- |
| 首轮 | 既有 A4 产出的 `performance_<id>.json`；`<id>` 即 base run-id |
| base run-id | 首轮的 run-id，复测轮以 `base_run_id` 字段回指它，两者严格同义 |
| 复测轮 | 绑定同一 base run-id 的一次后续声明，编号 1..N，分测量轮与豁免轮 |
| 测量轮 | 由性能量具执行、对点名 case 重新采集并出结果的复测轮 |
| 豁免轮 | 由 accept 写出的声明记录，宣布若干 case 豁免，不做任何采集 |
| 豁免 | 该 case 退出裁决分母，逐例有效状态记 `WAIVED` |
| 完成轮 | 结果 JSON 存在于约定路径且可解析的复测轮 |
| 阶段目录 | 一轮的独占产物目录 `runtime/results/<run_id>/performance/`；复测轮 run_id 即 `<id>-retest-<k>`，k 即轮号 |
| 中断轮 | 阶段目录存在而结果 JSON 缺失的复测轮；占用轮号，不参与折叠 |
| 有效轮 / 无效轮 | 完成轮过/不过「轮次有效性」检查为有效/无效；结果文件存在但不可解析的轮也是无效轮 |
| 折叠 | A5 把首轮与全部有效轮合并为逐例有效状态的机械规则（见「折叠」） |
| 有效状态 | 折叠后每个 case 的最终状态，报告与总结论只认它 |
| 代表记录 | 有效状态对应的那一轮的逐例记录，报告主表展示它的数值 |
| pass-once | 折叠规则：测量历史中存在 PASS，未豁免时有效状态即 PASS |

信任模型是无主观恶意：本协议的检查只为捕捉无意错误——跑错工程、换错基线、旧产物
混入、工具缺陷——不提供防篡改保证，也不为此增加机制。问题轮次跳过并醒目告警，
不让整体裁决翻车。跑哪些 case 由用户点名，不做机械限制；跑在哪张卡上默认沿用首轮，
也可以指定别的物理卡（见「换卡复测」）。

## 复测轮记录

文件名 `performance_<id>-retest-<k>.json`，k 为轮号（正整数）。轮序以文件名的 k 为准，
JSON 内 `round` 与 k 不一致时记 warning，按文件名裁。复测轮不接受 `--out`：结果一律落
`runtime/results/performance_<id>-retest-<k>.json`。

两种 kind 共有的必填字段：`schema_version`（整数，本版 1）、`base_run_id`、`round`
（整数）、`kind`（`"measure"` 或 `"waive"`）、身份字段 `op/family/soc/repo`、
`started`/`finished`（该轮进程开始与结果写出前的时刻，ISO 格式）。

测量轮专有字段：

- `requested_cases`：用户点名清单，非空、无重复、每项 ∈ 性能期望集，未知点名在起跑前
  报参数错误（不建目录不探卡）。每个点名 case 在 `cases[]` 恰好一条记录，跑不出的用
  `MISSING` 占位，不缺不多不重复。逐例记录以 `name` 为用例名，其余字段与首轮相同
  （见 perf-protocol.md「结果与退出码」），另加 `warmup_exit`。逐例状态合法集合是
  `PASS/FAIL/NO_KERNEL/CRASH/TIMEOUT/MISSING`——复测集全在性能期望集内，`NO_REF`
  出现即属工具缺陷。
- `warmup`：本轮预热次数 N（整数 ≥0）。
- `device_requested`（本轮请求的物理卡，不接受 `auto`）与 `device_resolved`（实际
  执行卡）：两者都是显式卡号，允许不等于首轮的卡（见「换卡复测」）。用默认卡时是否带
  复测限定布尔参数 `--map-device`，以 preflight 的 `needs_device_map` 为判据——首轮为
  `auto` 时必须带它（目标卡 K 经 `ASCEND_RT_VISIBLE_DEVICES` 映射为逻辑 0，
  `device_resolved` 记 K），首轮为显式卡号时不传；首轮模式传入属参数错误。
- `device_compiled`（整数，置位 `--map-device` 的轮写出，同卡显式卡号轮没有这个字段）：
  被测二进制编译期 `TEST_DEVICE_ID` 的进程内逻辑卡号，取值与校验见「换卡复测」。
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
当前 manifest 为锚——manifest 会被重跑 A2 改写，不能替历史背书。首轮 JSON 缺这两个
字段（旧版量具产物）即判定该工作目录不支持复测：拒绝复测并提示，不做迁移。

## 换卡复测

复测轮默认沿用首轮那张物理卡，也可以点名换一张。跨卡可比性有一次 A3 机（CANN 9.0.1）
实测：同一用例在 card0 与重映射到的 card6 各采 3 次，中位数差 2.04%，低于该次实验定的
3% 比较线（这条线是那次实验的判据，与性能 PASS 阈值 `ratio ≥ 0.8` 无关）。
其他机型与用例上的量级未经验证。

本轮跑在哪张卡上由三个量共同定：

| 量 | 含义 | 来源 |
| --- | --- | --- |
| 默认卡 | 不换卡时用的物理卡 | preflight 的 `device`：首轮 `device` 的显式值，或首轮 `auto` 实际选中的 `device_resolved` |
| 目标卡 | 本轮实际要跑的物理卡 | 用户点名，量具的 `--device` |
| 编译逻辑卡号 | 被测二进制编译期 `TEST_DEVICE_ID` 的值 | preflight 的 `device_compiled`，按 harness profile 推导 |

编译逻辑卡号按 profile 的绑卡模式推导，不按「显式即编译」想当然。profile 是 A1 探测
写进 `runtime/manifest.json` 的 `harness_profile`（见 run-chain.md「A1 环境」）：

| profile | 绑卡模式 | `device_compiled` |
| --- | --- | --- |
| `blas` | 编译期定卡：`build.sh --device` 把卡号写进 `TEST_DEVICE_ID` | 首轮 `auto` → 0（auto 定卡协议按 `--device=0` 构建）；首轮显式卡号 M → M |
| `sparse_frame` | 运行时定卡：`TEST_DEVICE_ID` 恒 0，物理卡一律经运行时映射选 | 恒 0 |
| 未登记的 profile | 推不出 | 无值，`can_switch_device` 为 false |

推不出编译逻辑卡号时不猜卡号：preflight 的 `can_switch_device` 为 false，该工作目录
只能按默认卡复测——**推不出只挡换卡，不挡同卡复测**。

`can_switch_device` 只回答一件事：编译逻辑卡号推不推得出、在不在上界内。上界是 7——
重映射串共 `device_compiled`+1 位互不相同的真实物理卡，物理卡域 0-7 凑不出第 9 张，
推导值超界即 false。

**占位卡的可用性不在 `can_switch_device` 的检查范围内，由用户保证。** 占位卡从 0 起
依次取、**跳过目标卡**，共取 `device_compiled` 张——所以目标卡小于 `device_compiled`
时会顺延用到卡 `device_compiled`，占位集合不是固定的 0 到 `device_compiled`-1。
被测二进制不会用到这些卡，但它们**既要存在、也要空闲**：

- 卡数不足或占位卡被别的进程占用时，换卡轮整轮失败，改用同卡复测，或换一张目标卡，
  使它对应的那组占位卡全空闲。
- 起跑门只查目标卡忙闲，不查占位卡，所以这类失败在起跑时不报，要到采集才暴露。
- 症状（A3 机实测，CANN 9.0.1）：编译卡号 3、目标卡 2，占位因此顺延成 0、1、3
  （跳过目标卡 2），串 `0,1,3,2`；占位里的卡 3 被别的进程独占时整轮 `CRASH` 且不产生
  PROF 目录——**算子本身算对了**（gtest 内部 PASSED、MERE/MARE 均 0），但 msprof 报
  `Operation not permitted` 拿不到 profiling 数据。换成目标卡 4 后占位回到 0、1、2
  （串 `0,1,2,4`）、三张全空，同一轮正常出数。看到「CRASH 且无 PROF 目录」先查占位卡，
  不要先怀疑算子。

换卡轮在「启动与恢复」那条量具命令上改三处——`--device` 改填目标卡，固定加
`--map-device`，另加 `--compiled-device`：

```bash
  --device <目标卡> --map-device --compiled-device <device_compiled>
```

重映射串是 `ASCEND_RT_VISIBLE_DEVICES` 的取值：逗号分隔的物理卡号列表，第 i 项映射为
进程内逻辑卡 i；量具按「目标卡落在第 `device_compiled` 位」构造它。该轮 JSON 的
`device_requested` 与 `device_resolved` 都记目标物理卡，`device_compiled` 记编译
逻辑卡号。

A5 判一个测量轮是不是换卡轮，**只看本轮 `device_resolved` 与默认卡是否相等**，不看
`device_compiled` 字段在不在——带 `--map-device` 的同卡复测也会写出该字段，拿字段
存在性当判据两头都错：缺字段的换卡轮会漏网，带字段的同卡轮会被误判。据此分两路：

- 换卡轮（`device_resolved` 不等于默认卡，或默认卡本身推不出）：编译逻辑卡号必须
  推得出，`device_compiled` 必须在且等于推导值，否则该轮无效。
- 同卡轮：一律放行，`device_compiled` 只作记录，推不出编译逻辑卡号也不影响。

另有一项 **PROF 落点核对**，对每个测量轮都做：msprof 在每次采集的输出目录下建
`PROF_*/device_<物理卡>/`，目录名里的卡号直证该次采集落到的卡，必须与
`device_resolved` 一致，不一致即无效轮。核对范围限定为该轮实际产生的目录——某例因
`TIMEOUT/MISSING/CRASH/NO_KERNEL` 等合法单例终态没产生 PROF 目录时，缺目录不改判该轮
无效；一个落点目录都没有而该轮有 PASS/FAIL 计分用例时记一条「未完成设备核对」的
告警，同样不改判（数值证据在 JSON 里，产物目录可能被清理过）。

身份与绑定校验不因换卡放松：换的只是卡，测的仍须是同一个二进制与同一份基线。

## 轮次有效性

候选轮按文件名模式 `performance_<id>-retest-<k>.json` 发现。文件不可解析 → 直接归
无效轮（占号、告警、跳过）。可解析的完成轮再做下表检查，任一不过 → 该轮为无效轮：
跳过折叠，报告醒目列出轮号与原因并明说「该轮未生效」，其余轮照常折叠，整体裁决不因此
翻车。首轮不同：首轮缺失或退出 3 仍按现行规则记证据不足。

| 检查 | 适用 kind | 判据 |
| --- | --- | --- |
| 结构 | 两者 | `schema_version` 认识、该 kind 必填字段齐全、逐例字段一致、逐例状态在合法集合内 |
| 身份 | 两者 | `base_run_id` 与本次 run-id 相同；op/family/soc/repo 与首轮一致 |
| 绑定 | measure | 六个绑定字段与首轮锚值一致（不一致说明测的不是同一对象，数值不可比） |
| 设备 | measure | 两个设备字段是显式卡号；换卡轮的 `device_compiled` 在且等于推导值；PROF 落点与 `device_resolved` 一致（判据见「换卡复测」） |
| 点名 | measure | `requested_cases` 合规，且每个点名 case 在 `cases[]` 恰好一条记录 |
| 豁免 | waive | `waivers[]` 合规 |

轮号占用：完成轮与中断轮都占号，下一轮号 = 已占用轮号最大值 +1；轮号空缺只记
warning，不影响折叠。复测轮一次跑一个（串行是使用约定，不是机械校验）。

## 折叠

折叠只在 accept（A5）一处实现，量具不读任何历史 JSON。折叠对象 = 首轮 + 全部有效轮。
逐例先算两个独立量：

1. **豁免态**：取涉及该 case 的最后一个声明（按轮号序）——豁免轮列入即豁免，更晚的
   有效测量轮点名该 case 即撤销豁免。撤销以「该 case 被点名并出现在有效测量轮的记录
   中」为准：点名即再测，跑不出而记 `MISSING` 的占位记录同样构成撤销，不以测出数值
   为条件。首轮记录、中断轮与无效轮都不是声明。
2. **测量历史**：首轮记录加全部有效测量轮中该 case 的记录，按轮序排列（首轮视为
   第 0 位）。每条记录永久保留。

有效状态按序判定：

1. 豁免态成立 → `WAIVED`，退出裁决分母。代表记录 = 使豁免态成立的那个豁免轮（数值栏
   留空，展示豁免理由）；另以该例最近一次测量记录（若有）作参考值标注，参考轮与代表轮
   是两个独立字段，不混用。
2. 测量历史中存在 PASS → `PASS`，代表记录取最早 PASS 的那轮（pass-once：该 PASS
   证据永久保留）。
3. 无 PASS 但存在 FAIL → `FAIL`，代表记录取 ratio 最大的那轮，同 ratio 取轮号小者。
4. 只有证据缺口（`NO_KERNEL/CRASH/TIMEOUT/MISSING`）→ 有效状态与代表记录都取该
   case 最后一次有效测量。

汇总作用于全部有效状态：

- 待裁集合 = 性能期望集 − 豁免集；分母 = 待裁集合的大小。
- 性能期望集非空而待裁集合为空 → 性能 `证据不足`（豁免不能空转出通过）。
- 待裁集合内：任一有效状态为证据缺口 → `证据不足`；否则任一 FAIL → `不通过`；否则
  全 PASS → `通过`。退出码与状态词沿用 perf-protocol.md「结果与退出码」。
- 「无性能要求」与 NO_REF 的分叉按 run-chain.md「A5 结论」执行：CSV 无 `TC_PF_` 行
  才是 `通过（无性能要求）`；有 `TC_PF_` 行而全配不到基线是 `NO_REF`，总体
  `证据不足`。

判定示例（「测」= 有效测量轮结果）：

| # | 事件序列 | 有效状态（代表轮） |
| --- | --- | --- |
| 1 | 首轮 FAIL | FAIL（首轮） |
| 2 | 首轮 FAIL → 测 PASS | PASS（该测量轮） |
| 3 | 首轮 FAIL(0.72) → 测 FAIL(0.75) | FAIL（后轮，ratio 大） |
| 4 | 首轮 FAIL → 测 CRASH | FAIL（首轮；规则 3 先于规则 4） |
| 5 | 首轮 NO_KERNEL → 测 FAIL | FAIL（测量轮） |
| 6 | 首轮 NO_KERNEL → 测 TIMEOUT | TIMEOUT（末次测量） |
| 7 | 首轮 PASS → 豁免 | WAIVED（PASS 证据保留展示） |
| 8 | 首轮 FAIL → 豁免 → 测 FAIL | FAIL（豁免被撤销） |
| 9 | 首轮 PASS → 豁免 → 测 FAIL | PASS（撤销豁免后最早 PASS 恢复） |
| 10 | 首轮 FAIL → 豁免，此后无测量 | WAIVED |
| 11 | 首轮 FAIL → 测 PASS → 测 FAIL | PASS（最早 PASS 轮） |
| 12 | 首轮 FAIL → 豁免 → 中断轮 | WAIVED（中断轮不撤销豁免） |
| 13 | 首轮 FAIL → 无效轮里测出 PASS | FAIL（无效轮被跳过并告警，不进历史） |

已知代价（协议明示）：pass-once 使验收命题为「至少一次测得达标」，临界例多试可能靠
波动通过；报告必须展示每例尝试史。

## 启动与恢复

两种入口的最小输入——其余一律从盘上恢复（`check.json`、`runtime/manifest.json`、
`runtime/results/` 文件名、`<产物目录>/intermediate/verdict.json`）：

- 测量复测：工作目录 + 点名 case + 可选 `--warmup`。
- 豁免：工作目录 + 豁免 case + 逐例理由。

起任何复测轮前必须先跑机械 preflight，不可跳过：

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py retest-preflight --run-id <id>
```

它读首轮 JSON 与轮次盘点，stdout 输出一个 JSON：成功退 0，拒绝退 2。字段：`run_id`
（字符串，= base run-id）、
`supported`（能否复测，false 即拒绝，`refusal_reasons` 给原因）、`next_round`
（下一轮号 = 已占用轮号最大值 +1）、`occupied_rounds`（轮号整数数组）、
`interrupted_rounds`（轮号整数数组）、`invalid_rounds`（对象数组，每项含 `round`
轮号与 `reasons` 原因数组）、`anchor_fields`（两个扩展锚字段各自是否在首轮 JSON 里
的布尔映射）、`device`（默认卡，即不换卡时该用的物理卡：首轮显式值，或首轮 auto 的
`device_resolved`；复测不接受 `auto`）、`device_compiled`（编译逻辑卡号，整数或
null）、`can_switch_device`（布尔，推不出编译逻辑卡号或超上界时 false，此时只能按
默认卡复测；占位卡可用性不在它的检查范围内，见「换卡复测」）、
`needs_device_map`（用默认卡时是否要带 `--map-device`；换卡轮一律要带）、
`device_note`（字符串说明）、`expected_cases`（性能期望集大小，整数）、
`warnings`（字符串数组）。首轮 JSON 不可读时 `device`、`device_compiled` 与
`needs_device_map` 为 null。

测量轮用 preflight 给出的轮号与 device 起量具，`needs_device_map` 为 true 时另加
`--map-device`；换卡时按「换卡复测」改那三处：

```bash
cd <工作目录>/runtime && <python> verify_performance.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id>-retest-<k> \
  --case <case_name> --skip-build --calls-per-case <calls_per_case> \
  [--warmup <N>] [--map-device] [--compiled-device <device_compiled>]
```

豁免轮由 accept 写出，`--waive` 取 case 与理由两个参数、每个 case 一组、可重复：

```bash
cd <工作目录> && <python> <skill>/scripts/accept.py waive \
  --run-id <id> --waive <case> <理由> [--waive <case> <理由> …]
```

waive 写盘为独占创建：目标轮结果文件已存在即拒绝，不向该路径写任何内容（口径同
perf-protocol.md「证据保护」）。

量具异常终止或环境失败的复测轮不写结果 JSON：留阶段目录并把原因追记进其中的
`fail.log`，成为中断轮占号。起跑门失败（目标卡忙或查询失败，见 perf-protocol.md
「结果与退出码」的退出码 4）同样按中断轮处理；恢复 = 处理占卡后重新跑
retest-preflight 取下一轮号，不删目录、不复用轮号。每个复测轮跑完必须重跑 A5
（命令见 run-chain.md「A5 结论」），报告与 verdict 才折入该轮；不重跑 A5 的复测轮
不构成最终报告。

恢复分支（对话式启动、零上下文时按序处理）：

- `runtime/results/` 下 base run-id 多候选：列出全部候选问用户。
- 产物目录在工作目录外且找不到：要求用户显式给出。
- 中断轮涉及的 case 从其阶段目录与日志读取；不可得时不阻塞，由用户重新点名。

## verdict 与报告增量

存在有效轮时，`verdict.json` 的 `performance` 段增加 `retest` 键：

| 字段 | 内容 |
| --- | --- |
| `fold_protocol_version` | 折叠规则版本，整数，本版 1（独立于记录的 `schema_version`） |
| `inputs` | 本次折叠消费的首轮与各轮 JSON 路径 + SHA-256（记录性质，供重演核对） |
| `interrupted_rounds` | 中断轮号清单 |
| `invalid_rounds` | 无效轮号及原因清单，措辞含「该轮未生效」 |
| `waived` | 豁免清单与理由 |
| `pass_on_retest` | 首轮非 PASS 而当前有效状态为 PASS 的 case 数 |

报告（A5 重跑后的当前投影）在性能节逐例展示：首轮状态与数值、各轮摘要（轮号/kind/
device_resolved/warmup/状态/ratio）、有效状态与代表轮（`WAIVED` 例另列参考轮）、
逐例的有效复测测量次数（有效测量轮中含该例的次数，不含首轮；证据缺口轮也计入——
它计「测过几次」，不是「测出数值几次」）。汇总处列：有效复测轮数（有效轮总数，含
豁免轮，不含首轮、中断轮与无效轮）、中断轮号、无效轮号及原因、豁免清单与理由、
`PASS(复测)` 计数。豁免与复测条目不受报告的 30 条截断限制，必须完整展示。

条件输出：无任何复测产物痕迹（无轮次结果文件、无阶段目录）的工作目录，A5 的裁决
字段、逐例状态、计数、退出码与现行完全一致，报告与 verdict.json 不出现复测段落；
只有中断轮或无效轮、无有效轮时，裁决同样与现行一致，但诊断区照列中断/无效轮清单——
失败的尝试不允许从最终报告消失。

可重演边界收窄为性能折叠可重演：同一组输入重跑折叠，得到相同的逐例有效状态、
性能计数与性能结论。这组输入有五件，比轮次 JSON 本身多出两件：

- 首轮与各轮结果 JSON。
- 运行时包 CSV 与规范化基线（性能期望集由这两件重算）。
- 折叠规则版本。
- `runtime/manifest.json` 的 `harness_profile`——编译逻辑卡号按它推导，profile
  变了，换卡轮的有效性跟着变。
- 各复测轮的阶段目录内容——PROF 落点核对读的是目录。**同一份轮次 JSON，落点目录
  里有错卡时该轮无效，目录缺失时该轮有效**，所以阶段目录被清理过的工作目录重演不出
  原判。本协议不为此加签名或哈希机制：信任模型是无主观恶意，记下这条边界即可。

完整 A5 verdict 的重演还依赖 run-chain.md「A5 结论」列明的其余输入。
