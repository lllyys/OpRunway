# 性能跑测

性能形态由任务书决定，写在 `facts.json` 的 `performance.kind` 里，
跑测侧照它执行，不自己选。

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
| 精度过了，有基线 | `已评级` |

最后一种之外都不出达标或劣化结论。已经采到的耗时不丢，报告里标明是参考数据。

## 三种形态

| `kind` | 任务书原话长什么样 | 跑什么 | 结论 |
| --- | --- | --- | --- |
| `none` | 「性能要求：无」 | 一轮采绝对耗时 | `未评级(无基线)` |
| `builtin` | 「不劣于 aclnnXxx 小算子拼接版本」 | 两轮，第二轮用 CANN 内置实现 | 逐用例比 Device 耗时 |
| `cross_dtype` | 「int8 不劣于 int32」 | 一轮，按 dtype 分组对比 | 每对各出一个结论 |

三个目标算子刚好各占一种：

| 算子 | 任务书 | `kind` |
| --- | --- | --- |
| roll | 性能要求：无 | `none` |
| median | 不劣于 aclnnMedian 小算子拼接版本 | `builtin` |
| indexfill | int16/int8/uint8 不劣于 int32，double 不劣于 INT64 | `cross_dtype` |

## 先抽样，再跑

**性能轮不跑全量。** profiling 要对每条用例重复采样，200 条要二十多分钟，
而多出来的用例绝大多数落在已经采过的等价类里，不增加结论可信度。

用同一个分层抽样脚本抽 50 条：

```bash
<python> <skill>/scripts/sample_smoke.py -i cases.json -o perf -n 50
<python> <skill>/scripts/run_atk.py --mode performance \
    -c perf/cases.json --golden golden --facts facts.json -o performance.json
```

抽样按 dtype × shape 档位分层，先保证每层至少一条，再随机补齐到 50。
`kind` 是 `cross_dtype` 时把 `-n` 调到 80——每个 dtype 要够分组比较。

**输出目录换、文件名不换。** `perf/cases.json` 而不是 `perf_cases.json`，
理由与冒烟一样：golden 的子目录名取自用例文件基名。

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

要两轮，第二轮跑 CANN 内置的同名接口作基线。**两轮必须用同一份
`perf/cases.json`**，否则耗时不可比。

```bash
# 轮 1：待验收实现
<python> <skill>/scripts/run_atk.py --mode performance \
    -c perf/cases.json --golden golden --facts facts.json -o performance.json

# 轮 2：CANN 内置实现
<python> <skill>/scripts/run_atk.py --mode performance \
    -c perf/cases.json --golden golden --facts facts.json \
    --builtin-baseline -o performance_builtin.json
```

`--builtin-baseline` 会在子进程里摘掉 `ATK_CUSTOM_OPP_PATH` 与
`ASCEND_CUSTOM_OPP_PATH`，让 pyaclnn 回落到装机目录的 `libopapi_*.so`。
不用手工 unset，也不用重开 shell。

跑之前先确认内置实现真的有同名接口：

```bash
nm -D $ASCEND_OPP_PATH/../lib64/libopapi_nn.so | grep aclnnMedianGetWorkspaceSize
```

社区新算子常常没有同名内置接口，那这一轮跑不起来，`verdict.py` 会写
`unknown` 并说明原因。**不要拿别的接口凑基线。**

### 内置实现不支持新增 dtype 时

社区任务多半是「给已有算子扩展支持某几种 dtype」，那些新增 dtype 内置实现
按定义就不支持——**它们没有基线可比，比较只能在内置支持的 dtype 上做。**

用 `--dtypes` 过滤出一份两轮共用的子集：

```bash
<python> <skill>/scripts/sample_smoke.py -i cases.json -o perfbase -n 40 \
    --dtypes fp16,fp32,bf16,int32,int64
```

然后两轮都用 `perfbase/cases.json`。报告里要写明比较只覆盖了哪几种 dtype，
新增 dtype 的性能结论是 `unknown`。

### 内置实现挂死时怎么定位

**基线轮卡住不动是常态，不是意外。** 内置实现往往正是因为有缺陷才有这个社区任务。

真机上 median 撞了三次，三次都停在不同的 dtype 与 shape 上，看着像随机。
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
<python> <skill>/scripts/sample_smoke.py -i cases.json -o perfbase -n 40 \
    --dtypes fp16,fp32,bf16,int32,int64 --exclude-ids "<上面的输出>"
```

**这条不能算「待验收实现更快」。** 它是内置实现的缺陷，写进报告备注，
不进性能比值，被排除的那一类的性能结论是 `unknown`。

`verdict.py` 读到 `performance_builtin.json` 就自动逐用例配对比中位数，
不需要手工算。

**两轮必须串行跑，不能并发。** 两轮用同一份 `perf/cases.json`，ATK 的输出目录
也就同名（`atk_output/cases_<时间戳>/`），脚本靠修改时间取最新那份报告。
并发跑会让后解析的那一轮拿到对方的报告。

日志分开存：被测轮写 `evidence/performance.log`，基线轮写
`evidence/performance_builtin.log`。

## kind = cross_dtype

一轮就够，基线是同一个算子的另一种 dtype。
`facts.json` 的 `pairs` 给出要比的对：

```json
"pairs": [["int16", "int32"], ["int8", "int32"], ["uint8", "int32"], ["fp64", "int64"]]
```

对每一对，取两侧 dtype 各自用例的 Device 耗时中位数比较。

**只在 shape 可比的用例之间比。** 不同 shape 的耗时差几个量级，
混在一起中位数没有意义。按 shape 元素总数分档后再比，
档内用例太少（少于 3 条）就写 `unknown`。

## 噪声

Device 耗时逐次波动通常在 5% 以内。判「劣化」要留余量：

| 差异 | 结论 |
| --- | --- |
| 被测 ≤ 基线 × 1.05 | 不劣化 |
| 被测 > 基线 × 1.1 | 劣化 |
| 之间 | `unknown`，建议重跑一轮确认 |

跑性能轮时机器上不要跑别的 NPU 任务，`npu-smi info` 的 AICore 占用
应该接近 0。
