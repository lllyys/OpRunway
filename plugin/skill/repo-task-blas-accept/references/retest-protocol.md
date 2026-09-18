# 性能复测与豁免协议

## Contents

- [术语与信任模型](#术语与信任模型)
- [复测轮记录](#复测轮记录)
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
不让整体裁决翻车。跑哪些 case 由用户点名，不做机械限制；复测沿用首轮的物理卡，
不支持换卡。

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
- `device_requested`（本轮请求值，不接受 `auto`）与 `device_resolved`（实际物理卡）。
  比较基准是首轮 JSON 的既有字段：首轮 `device`（原请求）为显式卡号时，两个字段都
  必须等于它；首轮 `device` 为 `auto` 时，两个字段必须等于首轮的 `device_resolved`，
  量具按首轮 auto 的同一机制执行（该物理卡经 `ASCEND_RT_VISIBLE_DEVICES` 映射为
  逻辑 0）。该映射由复测限定布尔参数 `--map-device` 触发：首轮为 `auto` 时复测必须
  带它（显式卡 K 映射为逻辑 0，`device_resolved` 记 K），首轮为显式卡号时不传，
  首轮模式传入属参数错误；是否传以 preflight 输出的 `needs_device_map` 为判据。
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
| 设备 | measure | 两个设备字段符合「复测轮记录」的设备规则 |
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
的布尔映射）、`device`（复测应使用的物理卡：首轮显式值，或首轮 auto 的
`device_resolved`；复测不接受 `auto`）、`needs_device_map`（测量轮是否要带
`--map-device`，判据见「复测轮记录」的设备条）、`device_note`（字符串说明）、
`expected_cases`（性能期望集大小，整数）、`warnings`（字符串数组）。首轮 JSON
不可读时 `device` 与 `needs_device_map` 为 null。

测量轮用 preflight 给出的轮号与 device 起量具，`needs_device_map` 为 true 时另加
`--map-device`：

```bash
cd <工作目录>/runtime && <python> verify_performance.py \
  --repo <工程目录> --soc <soc> --device <device> --run-id <id>-retest-<k> \
  --case <case_name> --skip-build --calls-per-case <calls_per_case> \
  [--warmup <N>] [--map-device]
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

可重演边界收窄为性能折叠可重演：同一组「首轮与各轮 JSON、运行时包 CSV 与规范化
基线、折叠规则版本」重跑折叠，得到相同的逐例有效状态、性能计数与性能结论；完整 A5
verdict 的重演还依赖 run-chain.md「A5 结论」列明的其余输入。
