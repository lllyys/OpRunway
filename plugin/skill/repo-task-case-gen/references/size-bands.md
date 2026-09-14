# 规模档：字节数，不是元素数

S2 构造规模覆盖时读这份；S3 判读覆盖量具的输出时也读它。
四条覆盖轴的另外三条在 [case-strategy.md](case-strategy.md)。

同样 65536 个元素，int8 是 64 KB，fp32 是 256 KB，落在算子搬运能力的不同区间。

| 档 | 字节 | 覆盖什么 | 目标占比 |
| --- | --- | --- | --- |
| `small` | < 32 KB | 单核单次搬运装得下 | 40% |
| `medium` | 32 KB – 2 MB | UB 装不下，要循环切分 | 30% |
| `large` | ≥ 2 MB | 多核切分、尾核处理 | 30% |

`check_coverage.py` 报实际占比，但**只在 `large` 为 0 时提示**——那时性能子集
一条都填不进去。别的偏离不提示，全量本来就不追这个配比（见下「全量不追」）。

#### `max_length` 是字节预算，不是元素数——但有五个 dtype 不守这个口径

ATK 文档写的是「最大元素数量」，但代码先按 dtype 宽度除了一次
（`get_max_number_ele`，`parameter_tensor.py:41-51`），所以**大多数 dtype 的字节
上限都等于 `max_length`**。那张宽度表只列了三组：

```python
if dtype in ["int16", "fp16"]:                 return int(length / 2)
elif dtype in ["fp32", "int32"]:               return int(length / 4)
elif dtype in ["fp64", "int64", "complex64"]:  return int(length / 8)
else:                                          return length      # 兜底当 1 字节
```

**表里没有的 dtype 全掉进 `else`，按 1 字节记账。** 宽度真是 1 字节的
（int8 / uint8 / bool / fp8）恰好正确，其余五个拿到成倍的预算：

| dtype | 真实宽度 | 元素上限 | 字节上限 | 相对 `max_length` |
| --- | ---: | ---: | ---: | ---: |
| int8 / uint8 / bool / fp8 | 1 | `max_length` | `max_length` | 1× ✓ |
| fp16 / int16 | 2 | `max_length`/2 | `max_length` | 1× ✓ |
| fp32 / int32 | 4 | `max_length`/4 | `max_length` | 1× ✓ |
| fp64 / int64 / complex64 | 8 | `max_length`/8 | `max_length` | 1× ✓ |
| **bf16 / uint16** | 2 | `max_length` | 2 × `max_length` | **2×** |
| **uint32** | 4 | `max_length` | 4 × `max_length` | **4×** |
| **uint64** | 8 | `max_length` | 8 × `max_length` | **8×** |
| **complex128** | 16 | `max_length` | 16 × `max_length` | **16×** |

`max_length: 4194304` 下，一条 `uint32` 用例最大 16 MB、`complex128` 最大 64 MB。
**dtype 列表里出现这五个中的任何一个，就在约束器里按真实宽度给那几种 dtype 的
元素数自己封顶**，口径与「输出比输入大的算子要给输出封顶」那节同一套：

```python
OVERSHOOT = {"bf16": 2, "uint16": 2, "uint32": 4, "uint64": 8, "complex128": 16}
if x.dtype in OVERSHOOT:                     # 其余 dtype ATK 自己算得对，不要碰
    cap = MAX_LENGTH // OVERSHOOT[x.dtype]   # MAX_LENGTH 取 YAML 里那个 max_length
    # 按 cap 重写 shape，写法见「高秩不靠抽样」那段的现算轴长
```

不封的后果落在 golden 体积与 S4 的 600 秒超时，判据同样是 `du -sh golden/`。

上表在 A3 + atk 26.8.8 上逐个调 `get_max_number_ele` 复核过，五行全对得上。
同一台机器上跑 200 条（8 dtype × 25）抽样验证：一条 `complex128` 用例抽到
33 292 800 字节，是 `max_length` 的 7.9 倍——随机抽样够不到理论上限，
但已经证明这条路是通的。

早前用 `max_length: 2097152` 跑的那轮实测：

| dtype | 元素上限 | 最大字节 |
| --- | --- | --- |
| int8 / uint8 / bool | 2097152 | 2097151 |
| fp32 / int32 | 524288 | 2097148 |
| **bf16** | 2097152 | **4194302** |

**取值规则：`max_length` = large 门槛 × 2。** 具体数值在 `skeleton.yaml`。
判据是 `>=` 拒绝，填成门槛本身会让 large 差几个字节永远不可达，
只有 bf16 靠上面那个例外漏进来——实测全量 200 条里 large 只有 8 条且全是 bf16，
翻到 4194304 后变成 53 条覆盖全部 10 个 dtype。

#### 用 size_distributions 控配比

ATK 原生支持按规模配比生成，**单位是 MB**（`SIZE_UNIT` 是 1 MB 的比特数，
`atk/case_generator/generator/processor.py:317`）。写在 YAML 顶层：

配比 40 / 30 / 30，**具体写法在 `skeleton.yaml`**。

它对每条用例重抽 shape 直到落进配额档，最多重抽 1000 次，填不满会在
`atk.log` 里写 `Unable to satisfy the size restriction`。

**配了它，`shape_distributions` 就整个不执行**——两者互斥，ATK 二选一
（`atk/case_generator/generator/base_generator.py:50-51`）。

**光配它还不够**，`max_length` 与 `dim_values` 得让目标档在数学上可达：

| 卡住的地方 | 表现 | 修法 |
| --- | --- | --- |
| `max_length` 太小 | 填成 large 门槛本身，各 dtype 都差几个字节 | 提到门槛 × 2，见 `skeleton.yaml` |
| `dim_values` 最大值太小 | **秩 1 结构上到不了 large** | 补到 `skeleton.yaml` 那份的上限 `2^20` |

三处要一起调，只调 `size_distributions` 到不了目标档。

#### 全量不追 large 占比，性能子集才追

追全量 30% large 意味着几十条数 MB 的张量：golden 涨到几百 MB，冻结时间也上去。
而 tiling 边界缺陷在 `2^n±1` 的小张量上就能暴露，大张量对**精度**轮没有额外收益。

`gen_cases.py` 抽性能子集时**先按规模档配额再按 dtype 补齐**，全量里有十几条
large 就够填满子集的 30%。实测代价：

| `max_length` | 全量 large | 子集 large | large 覆盖几个 dtype |
| --- | --- | --- | --- |
| 2097152（填成门槛本身） | 8 / 200 | 8 / 50 | **1**，只有 bf16 |
| **4194304（门槛 × 2）** | 53 / 200 | 15 / 50 | **10 / 10** |

其余配置不变，出处见 `CLAUDE.md` 的真机事实表。

#### 跨 dtype 比性能时子集要成对

`facts.json` 的 `performance.kind = cross_dtype` 对应任务书里「A 的性能对比 B
劣化 x% 之内」这类要求。跑测侧只跑一轮性能，然后按输入张量的 dtype 分组，拿两组
的中位数相除——**所以两组必须跑同一批 shape**。

按规模档随机抽出来的两组 shape 分布不一样，比值里就混着 shape 效应；而同一 dtype
内不同 shape 的耗时差几个量级，几个百分点的阈值早被淹掉。靠随机抽样也撞不出同
shape 的两条。

`gen_cases.py` 因此在 `kind=cross_dtype` 时改成成对抽：按被测 dtype 抽 `--perf-number`
条，每条**复制一份、只把 dtype 换成基线 dtype、shape 与 attr 全不动**，两条一起进子集。
**这时 `--perf-number` 数的是对数**，默认 50 对、落盘 100 条——两组各要 50 个样本，
中位数才和非成对形态下 50 条的统计量对齐。`pairs` 有多组时 50 对按组均分。
配对关系与档位落进 `perf/manifest.json`：跑测侧靠它逐对算加速比、按规模档分组，
并在一对缺一条时整对剔除。**只在内存里成对是不够的**——写盘成扁平列表之后那边
只能按 dtype 分两堆取中位数相除，出不了分档结论，也验不出配对被破坏。

镜像件同时并入全量 `cases.json`，`freeze_golden.py` 照常给它们冻 golden——所以跑测
侧不需要做任何 dtype 转换，它拿到的就是两条正常用例。

**转换不能放到跑测侧做。** aclnn 的 `out` 是第一段接口的入参，ATK 从 golden 的
`output_info.json` 取它的 shape 与 dtype；跑测侧临时把用例改成另一个 dtype，golden
还是原 dtype 的，`out` 会按原 dtype 申请，对不上。

#### 形状上限

生态算子精度标准规定：单轴落在 `[1, 2^20]`，单张张量总元素数不超过 `2^31`。

| 项 | 上限 | 现状 |
| --- | --- | --- |
| 单维 | `2^20` | **正卡在边上**：`dim_values` 最大值就是 1048576 = 2^20 |
| 总元素 | `2^31` | 不 binding：`max_length: 4194304` 下最多 2^22 个元素，差 512 倍 |

**`dim_values` 不能再往上加数。** 要更大的张量靠加维，不靠加长单轴——
int8 想到 4 MB 用 `[4, 1048576]`，不要写 `[4194304]`。

**`extra_numbers` 生成的上边界用例不受 `max_length` 约束**，它把某一轴硬编码成
`2^31+1`。要么在约束器里把该轴夹回 `2^20`，要么填 `extra_numbers: 0` 关掉。

#### 输出比输入大的算子要给输出封顶

`size_distributions`、`max_length`、`dim_values` 约束的**全是输入**。
输出规模由算子语义决定，YAML 里没有任何入口管得住它——上采样、pad、repeat、
broadcast 这类算子，输入档位完全合法，输出可以大出一两个量级。

失控的后果落在 golden：`freeze_golden.py` 把每条用例的输出整个存盘，
体积 ≈ Σ（输出元素数 × dtype 字节宽）。一条 2 MB 的 `large` 输入放大 4 倍就是
8 MB，几十条就是几百 MB，先撞上的是 S4 那个 600 秒超时。

**这类算子在约束器里给输出封顶，用和输入同一套字节口径**——`max_length` 是输入的
字节预算，给输出用同一个数，按当前用例的 dtype 现算元素数：

```python
width = {"fp32": 4, "fp16": 2, "bf16": 2, "uint8": 1}[x.dtype]
out_cap = MAX_LENGTH // width           # MAX_LENGTH 取 YAML 里那个 max_length
out_elems = min(desired_out_elems, out_cap)
```

**不要写成一个固定的元素数上限。** 元素数常数与规模档自相矛盾：`large` 门槛是 2 MB
字节，uint8 要 2^21 个元素才够，一个 `1 << 20` 的常数会让窄 dtype 的输出永远到不了
large——正好压掉重点 dtype 那一档，与「规模档按字节算」那节打架。

判据在 S4：`du -sh golden/` 明显超出「200 条 198 MB」那个量级（`skeleton.yaml` 那套
数值的实测值），就是输出没封住，回 S2 调小预算。**不要靠调小 `dtype_numbers` 换体积**，
那会同时削掉覆盖面。

S4 超时上限 600 秒，**生成侧全程预算 20 分钟**。超时先查这批用例的张量体积，
不要调超时。
**不要为了跑得快砍掉 `large` 档**——那等于宣布不测多核切分。
