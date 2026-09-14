# 性能跑测

## 目录

- 采集期间独占本现场
- 前提：精度先过
- 四种形态
- 先抽样，再跑
- 报告按规模档出加速比
- 性能子集抽样
- kind = none
- kind = builtin
- kind = cross_dtype
- 噪声

性能形态由任务书决定，写在 `facts.json` 的 `performance.kind` 里，
跑测侧照它执行，不自己选。

## 采集期间独占本现场

**性能采集期间本现场不并发第二个 NPU 任务**，ATK 那轮与外部量测都算。同一张卡上
重叠采样，两轮的数互相污染，而两边的退出码都是 0。`run_kit_perf.py` 自己持锁，
另起的量测件用命令包住：

```bash
python3 <skill>/scripts/perf_lock.py --stage stage --who "<这一轮跑的是什么>" --devices 0 -- \
    python3 <量测件>.py --cases <用例> -o <产物>
```

拿不到锁退 3 并打印持有者。判据与陈锁的处理见
[external-perf.md](external-perf.md)「现场级排他锁」。

**任务书判据表那批走外部量测，与自带件泛化集两批都跑**，不停下来问跑哪几批。
分工与结论口径见 [external-perf.md](external-perf.md)「两批的覆盖分工」。

## 前提：精度先过

**精度没通过就不评级性能。** 一个算错的算子跑得快没有意义，而且失败用例的
耗时本身也不可信。

`verdict.py` 按数据推 `performance.status`，不需要人判断：

| 情形 | 状态 |
| --- | --- |
| 精度未裁决 | `未执行(精度未裁决)` |
| 精度未过，没跑性能轮 | `未执行(精度未通过)` |
| 精度未过，已跑过性能轮 | `未评级(精度未通过，仅留参考数据)` |
| 精度过了，`kind` 是 `none` | `未评级(无基线)` |
| 精度过了，`kind` 是 `threshold` | `待人工判定(<criterion 原话>)` |
| 精度过了，有基线 | `已评级` |

最后一种之外都不出达标或劣化结论。已经采到的耗时不丢，报告里标明是参考数据。

## 四种形态

| `kind` | 任务书原话长什么样 | 跑什么 | 结论 |
| --- | --- | --- | --- |
| `none` | 「性能要求：无」 | 一轮采绝对耗时 | `未评级(无基线)` |
| `builtin` | 「不劣于 aclnnXxx 小算子拼接版本」 | 一条命令，脚本连跑两轮，轮 2 用 CANN 内置实现 | 逐用例比 Device 耗时 |
| `cross_dtype` | 「int8 不劣于 int32」 | 一轮，按 dtype 分组对比 | 每对各出一个结论 |
| `threshold` | 「达到 compute/memory bound 的 80%」 | 一轮，同 `none` | `待人工判定(<criterion>)`，只给实测耗时 |

四种各举一例：

| 算子 | 任务书 | `kind` |
| --- | --- | --- |
| roll | 性能要求：无 | `none` |
| median | 不劣于 aclnnMedian 小算子拼接版本 | `builtin` |
| indexfill | int16/int8/uint8 不劣于 int32，double 不劣于 INT64 | `cross_dtype` |
| huber_loss | 达到 compute bound 或 memory bound 的 80% | `threshold` |

**`threshold` 这一档跑测侧不代判。** 折算 roofline 要 FLOPs/字节数与本机型的算力、
带宽峰值，跑测侧手上一样都没有。它做的是把 `facts.json` 的 `criterion` 原话与实测
Device 耗时一起写进报告，总结论落在「精度通过·性能待定」，交给读报告的人判。
**不要因为判不了就把它改成 `none`** ——那是在报告里抹掉一条任务书要求。

## 先抽样，再跑

**性能轮不跑全量。** profiling 要对每条用例重复采样，200 条要二十多分钟，
而多出来的用例绝大多数落在已经采过的等价类里，不增加结论可信度。

用同一个分层抽样脚本抽 50 条（`cross_dtype` 时 50 对共 100 条）：

```bash
<python> <skill>/scripts/run_atk.py --mode performance \
    -c perf/cases.json --golden golden --facts facts.json -o performance.json
```

## 报告按规模档出加速比

三种形态的性能对比都按规模档分组，不只出一个总数：

```text
| 规模档 | 对数 | 被测中位数 | 基线中位数 | 加速比 | 结论 |
| small  | 12   | ... us    | ... us    | 1.01x | 不劣化 |
| large  |  6   | ... us    | ... us    | 0.77x | 劣化   |
| **合计** | 26 | ... us    | ... us    | 0.99x | 不劣化 |
```

**合计那行会骗人。** 上面这组数里合计判「不劣化」，而 `large` 档慢了 30%——
切分路径（UB 装不下时的循环切分、多核切分、尾核）出问题就是这个形态，
揉进一个中位数里看不见。**结论要逐档写，不要只抄合计。**

档位来自 `perf/manifest.json` 的 `bands`，是生成侧算好的。**这边不重算**——
阈值在两个 skill 各存一份，真机上出过两套不一致。缺这个文件时退回按 dtype
分组取中位数相除，报告里会写明做不了分档。

某档样本少于 3 条写 `unknown`，不出结论。`large` 抽不够是常事，那一档就是没验到。

## 性能子集抽样

抽样按 dtype × 规模档分层，先保证每层至少一条，再随机补齐到 50。
规模档按**字节数**分（`scalar` / `small` <32KB / `medium` <2MB / `large` ≥2MB），
与生成侧 `case-strategy.md`「规模档」同一套阈值。

脚本会打印抽到的规模档分布。**某一档少于 3 条时它报「抽不够」并给出全量里的条数**：

| 全量里有 | 含义 | 去向 |
| --- | --- | --- |
| 也少 | 源用例集就没有这一档 | 回生成侧调 `max_length` 与 `dim_values` 重新生成 |
| 够多 | 抽样没抽到 | `-n` 调大 |

**大多数情况是前者**——生成侧没显式构造规模档时，`large` 全量常常是 0 条，
调 `-n` 没有用。这时性能结论只覆盖小中张量，报告里要写明不覆盖多核切分路径。
`kind` 是 `cross_dtype` 时把 `-n` 调到 80——每个 dtype 要够分组比较。

**输出目录换、文件名不换。** `perf/cases.json` 而不是 `perf_cases.json`，
理由见 run-accuracy.md「golden 目录结构与文件名的耦合」：golden 的子目录名取自用例文件基名。

它跑的是同一套节点拓扑，只把 `--task` 换成 `performance_device`：

```bash
atk node --backend aclnn --devices 0 \
    node --backend cpu --task accuracy_load --output_path <golden> \
    task -c cases.json --task performance_device
```

`performance_device` 采的是 Device 侧耗时，不含 Host 侧调度开销，
是比较算子实现快慢的正确口径。`performance_e2e` 含调度，受环境噪声影响大，
不用它下结论。

耗时落在报告 `statistic` 表的 `pyaclnn_0_Device性能（us）` 与
`cpu_0_Device性能（us）` 两列，`run_atk.py` 解析成 `device_times`：

```json
{"device_times": {"0": {"aclnn": 12.34, "cpu": 890.1}}}
```

**不要拿 `cpu` 那列当性能基线。** CPU 与 NPU 不可比，它只是标杆节点的
副产物。三种形态的基线都不是它。

## kind = none

只采绝对耗时，报告里写中位数与区间，不给达标结论。
这不是偷懒——任务书没给基线，任何「快」或「慢」的结论都没有依据。

## kind = builtin

要两轮：轮 1 待验收实现，轮 2 CANN 内置的同名接口作基线。
**两轮必须用同一份 `perf/cases.json`**，否则耗时不可比——所以两轮由脚本
连着跑完，命令还是一条：

```bash
<python> <skill>/scripts/run_atk.py --mode performance \
    -c perf/cases.json --golden golden --facts facts.json -o performance.json
```

它读 `facts.json` 的 `kind`，是 `builtin` 就在轮 1 之后摘掉
`ATK_CUSTOM_OPP_PATH` 与 `ASCEND_CUSTOM_OPP_PATH`，让 pyaclnn 回落到装机目录的
`libopapi_*.so`，再用同一份用例跑轮 2，写 `performance_builtin.json`
（`verdict.py` 默认就读这个名字）。不用手工 unset，也不用重开 shell。

轮 1 成了而轮 2 挂了时，`performance.json` 已经落盘，只补跑轮 2：

```bash
<python> <skill>/scripts/run_atk.py --mode performance \
    -c perf/cases.json --golden golden --facts facts.json \
    --builtin-baseline -o performance_builtin.json
```

**`--builtin-baseline` 只用于这种补跑。** 正常路径不需要它。

`kind` 读不到（`facts.json` 缺失）或取值不在 `none`/`builtin`/`cross_dtype`
里时，脚本退 3 停在 A5，不猜形态——否则拼错一个字母的后果是基线轮静默不跑，
报告只是少一节，没人看得出来。

跑之前先确认内置实现真的有同名接口：

```bash
nm -D $ASCEND_OPP_PATH/../lib64/libopapi_nn.so | grep aclnnMedianGetWorkspaceSize
```

社区新算子常常没有同名内置接口，那这一轮跑不起来，`verdict.py` 会写
`unknown` 并说明原因。**不要拿别的接口凑基线。**

### 内置实现不支持新增 dtype 时

社区任务多半是「给已有算子扩展支持某几种 dtype」，那些新增 dtype 内置实现
按定义就不支持——**它们没有基线可比，比较只能在内置支持的 dtype 上做。**

`run_atk.py` 的 `--dtypes` 过滤本轮用例。过滤在两轮之前做，两轮吃的是同一份
过滤后的用例，不会比错批：

```bash
<python> <skill>/scripts/run_atk.py --mode performance -c perf/cases.json \
    --golden golden --facts facts.json --dtypes fp16,fp32,int32 -o performance.json
```

`--exclude-ids` 同理，用来剔掉内置实现跑不动的具体用例。
补跑轮 2 时要带上同一个 `--dtypes` 或 `--exclude-ids`，否则比的不是同一批。

报告里要写明比较只覆盖了哪几种 dtype，新增 dtype 的性能结论是 `unknown`。

### 内置实现挂死的定位

**基线轮卡住不动是常态，不是意外。** 内置实现往往正是因为有缺陷才有这个社区任务。

内置基线连撞三次、三次停在不同的 dtype 与 shape 上时，看着像随机。
把三条的参数并排一列，共同点立刻出来：

| 卡住的用例 | dtype | shape | dim | keepdim |
| --- | --- | --- | --- | --- |
| 第 1 次 | int8 | [64, 1, 15] | 1 | **True** |
| 第 2 次 | int64 | [32, 1] | 1 | **True** |
| 第 3 次 | int32 | [31] | −1 | **True** |

直接验：拿 3 条 `keepdim=False` 和 3 条 `keepdim=True` 各跑一轮基线，
前者秒回、后者第一条就不返回。结论是**内置 `aclnnMedian` 在 `keepdim=True`
时不返回**，与 dtype 和 shape 无关。

**定位方法就是这个：把卡住的几条参数并排，找共同项，再拿最小对照组直接验。**
不要逐条排除——那要跑几十轮。

拿到共同项后用它构造两轮共用的子集：

```bash
# 算出 keepdim=True 的 id
python -c "
import json
cases = json.load(open('cases.json'))
print(','.join(str(c['id']) for c in cases if c['inputs'][2]['range_values'] is True))
"
# 用这批 id 做排除
```

**这条不能算「待验收实现更快」。** 它是内置实现的缺陷，写进报告备注，
不进性能比值，被排除的那一类的性能结论是 `unknown`。

`verdict.py` 读到 `performance_builtin.json` 就自动逐用例配对算加速比，
不需要手工算。

**两轮必须串行跑，不能并发。** 两轮用同一份 `perf/cases.json`，ATK 的输出目录
也就同名（`atk_output/cases_<时间戳>/`），脚本靠修改时间取最新那份报告。
脚本自己跑的两轮本来就是一前一后；**不要在同一个工作目录里手工并起两条命令**，
后解析的那一轮会拿到对方的报告。

日志分开存：被测轮写 `evidence/performance.log`，基线轮写
`evidence/performance_builtin.log`。

## kind = cross_dtype

一轮就够，基线是同一个算子的另一种 dtype。
`facts.json` 的 `pairs` 给出要比的对：

```json
"pairs": [["int16", "int32"], ["int8", "int32"], ["uint8", "int32"], ["fp64", "int64"]]
```

对每一对，**逐对**算加速比（基线耗时 ÷ 被测耗时），再按规模档取中位数。

**shape 可比性由生成侧保证，不在这里补。** 不同 shape 的耗时差几个量级，
混在一起中位数没有意义，所以 `perf/cases.json` 的 cross_dtype 子集是**按 shape 成对**
抽的（用例包契约第 7 项），配对关系记在 `perf/manifest.json` 的 `pairs`（第 9 项）。
子集不成对时 `verdict.py` 照样打出「不劣化/劣化」，而那个结论没有意义。

一对里有一条没跑出耗时，**整对剔除**并写进「不覆盖的范围」——留着会让统计歪掉。

子集是不是成对，看生成侧有没有报过成对抽样；拿不准就在报告里标明这一条。
某一侧 dtype 的用例少于 3 条时该对写 `unknown`。

## 噪声

Device 耗时逐次波动通常在 5% 以内。判「劣化」要留余量：

| 差异 | 结论 |
| --- | --- |
| 被测 ≤ 基线 × 1.05（加速比 ≥ 0.952） | 不劣化 |
| 被测 > 基线 × 1.1 | 劣化 |
| 之间 | `unknown`，建议重跑一轮确认 |

跑性能轮时机器上不要跑别的 NPU 任务，`npu-smi info` 的 AICore 占用
应该接近 0。

## 外部性能结果

任务书的口径 ATK 表达不了时，倍率只能来自外部工具。判据、采样口径、
中止的判据与交换格式在 [external-perf.md](external-perf.md)。

## A4 性能

读 [run-performance.md](run-performance.md)。形态由 `facts.json` 的
`performance.kind` 决定，不由你选：

| kind | 跑什么 | 结论怎么写 |
| --- | --- | --- |
| `none` | 一轮 `performance_device` 采绝对耗时 | `未评级(无基线)` |
| `builtin` | 脚本连跑两轮，轮 2 摘掉自定义算子包测内置实现 | 逐用例比 Device 耗时中位数 |
| `cross_dtype` | 一轮内按 `pairs` 分组对比 | 每对各出一个结论 |
| `threshold` | 一轮，同 `none` | `待人工判定(<facts 的 criterion 原话>)` 并带上实测耗时。**这一档不代判** |

**三种形态都是这一条命令**，跑几轮由脚本读 `facts.json` 决定：

```bash
cd <现场>/work && source evidence/env.sh && <python> <skill>/scripts/run_atk.py \
    --mode performance --op <op> -c ../input/perf/cases.json \
    --golden ../input/golden --facts ../input/facts.json -o stage/performance.json
```

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | 该跑的轮次都跑完 | 进 A5 |
| 3 | `facts.json` 读不到，或 `kind` 不在四种里 | 修用例包的 `facts.json`，别改这里 |
| 2 | 某一轮没产出报告 | `kind=builtin` 时轮 1 已落盘，按屏幕提示只补跑轮 2 |

**`input/perf/cases.json` 由生成侧抽好，这里只消费**；没有它是旧版用例包，
回生成侧重跑 `gen_cases.py`，**不要在这里补抽样**。

## 任务书的口径 ATK 表达不了时，A4 是两步

先按 [run-performance.md](run-performance.md)「外部性能结果」
逐条比对任务书的性能章节与 ATK 的能力。**有一条对不上就走两步**：

| 步 | 做什么 | 产物 |
| --- | --- | --- |
| A4-1 | 上面那条命令，ATK 采绝对耗时 | `stage/performance.json` |
| A4-2 | 跑外部工具采任务书口径的数，再收编 | `stage/performance_external.json` |

```bash
cd <现场>/work && source evidence/env.sh && <python> <skill>/scripts/collect_perf.py \
    --from <外部工具的产出>.json -c ../input/cases.json \
    --facts ../input/facts.json -o stage/performance_external.json
```

外部工具就是任务方自带的性能脚本时，A4-2 的采数用
`run_kit_perf.py`（现查空闲卡分片并行、最多 4 张、按 `--help` 探测参数、Profiler 产物回落），
命令与退出码在 [kit-acceptance.md](kit-acceptance.md)「性能」。
**自带件路上 A4-1 整轮不跑**——任务书的口径 ATK 表达不了才走到这里，
ATK 那轮的绝对耗时与外部口径不可比，跑了只会在报告里多一组容易被相除的数。

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | 收编成功，逐条倍率已算好 | 进 A5，`verdict.py` 自己读 |
| 2 | 交换格式不合规，屏幕上逐条列出 | 回外部工具那侧改，**不要在这里换算或补字段** |
| 3 | 文件不存在或不是 JSON | 核路径 |

外部工具从哪来：

| 情形 | 用什么 |
| --- | --- |
| 用例包里有 `kit/`，且它带性能脚本 | **优先跑那份**，口径与任务方交付的基线表同源 |
| 自带件没有性能脚本，或它的口径不合任务书 | 照抄 `assets/perf_harness.py` 改 `build_call` 一个函数 |

**A4-2 缺了不拦，但结论会降级**：`verdict.py` 写「待人工判定」并注明没收到
外部结果。倍率型要求**不能拿 ATK 那轮的数去算**——两个口径的数相除没有意义。

**精度没通过就不评级性能。** 已跑过的耗时不丢，`verdict.py` 写成
`未评级(精度未通过，仅留参考数据)`。
