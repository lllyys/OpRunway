# CSV 生成与四块规则

## 目录

- [轴派生](#轴派生)
- [行物化](#行物化)
- [四块](#四块)
- [确定性 pairwise](#确定性-pairwise)
- [report](#report)
- [包级校验](#包级校验)

`gen_csv.py` 只依赖 Python 3.8 以上标准库。
开发者可在没有本 skill 的机器上
运行 `python3 gen_csv.py`，在脚本目录重生 `<op>_test.csv`。

## 轴派生

`build_axes(facts)` 按 params 声明顺序返回轴。单值轴作为常量，不参与
pairwise 配对。

| 参数 | 轴 | 值 |
| --- | --- | --- |
| op enum | `<name>` | `values` 声明顺序 |
| dtype/compute enum | 无独立轴 | 由 profile.assign 决定 |
| 存在 profiles | `profile` | profile 名，插在首个 dtype/compute enum 处 |
| matrix 维度 | `<name>` | 2^n±1 边界阶梯，见 case-strategy.md |
| 纯 vector/int_array 长度 | `<name>` | 同上加大值 `100003` |
| batch count | `<name>` | `1,2,5` |
| ld/stride | `<name>` | `min,pad` |
| inc | `<name>` | `1,3` |
| 标量 | `<name>` | 声明的 values，否则是实数或复数标量 tiers |
| 无 producer 的输入 buffer | `<name>_fill` | fill 词表 |
| 有 conditioning 的 matrix | `<name>_matrix_type` | conditioning 列表 |
| 输入 fixed_vector | `<name>` | samples 的下标 |

`cases.dim_tiers`、`vec_dim_tiers`、`batch_tiers`、`inc_tiers` 与 `fill_tiers`
可覆盖对应默认轴。
`vec_dim_tiers` 超过 8 个值时，按量级抽 6–8 档；两个长轴会让
pairwise 在约束下退化成接近全叉积，增加任务包与真机时长。

## 行物化

轴值按以下规则变成 CSV 列：

- dim、inc 和 batch 直接写整数。
- ld 的 `min` 是 `max(1, rows)`，`pad` 是 `rows + 5`。
- stride 的 `min` 是 `ld * cols`，`pad` 是 `ld * cols + 7`。
- 实标量写十进制数，复标量拆成 `_re` 和 `_im`。
- profile.assign 把 dtype/compute enum 短记号写入对应列。
- ED 的 set 可覆盖 dtype/compute enum；该行不要求组合属于某个 profile。
- fixed_vector 根据轴中的 sample 下标展开为 `<name>0..`。
- null 列默认为 `0`，batch pattern 默认为 `UNIFORM`。
- edge 中 null 列的 bool 规范化为 `0/1`，其他 `set` 值不转换。
- `expect_result` 默认为 `ACLBLAS_STATUS_SUCCESS`。
- `random_seed` 是 `20260000 + 全局行号`，行号从 1 开始。

非 ED 行必须满足全部 constraints，且 footprint 不超过上限。footprint
是各 buffer 的 dtype 字节数乘元素数和 batch 数之和：

- matrix 元素数是 `ld * cols`。
- vector 元素数是 `1 + (len - 1) * abs(inc)`。
- fixed_vector 和 int_array 元素数是 `len`。

## 四块

生成的用例分四块，每块的 `case_name` 以 `TC_<块>_<编号>` 命名，块名即前缀：
**L0**（冒烟，最小尺寸的全枚举组合）、**PW**（pairwise 组合）、**ED**（edge 边界）、
**PF**（性能）。因此 `TC_PF_` 前缀即性能用例，非 `TC_PF_` 前缀即精度用例。

| 块 | 生成规则 | case_name |
| --- | --- | --- |
| L0 | op enum 全组合 × profile × `4,8`，其他轴取首值 | `TC_L0_%03d` |
| PW | 确定性 pairwise，每行通过合法性核 | `TC_PW_%03d` |
| ED | 基准行套用每个 edge_cases.set | `TC_ED_%03d` |
| PF | 物化 perf.rows，可选附加方阵 sweep | `TC_PF_%03d` |

L0 的所有普通 dim 同时取 `4` 或 `8`，batch 取 `2`。无 op enum 时，
每个 profile 仍生成两行。

ED 基准行的普通 dim 取 `16`，batch 取 `2`，ld/stride 取 `min`。改动 dim
后，未在 set 中指定的 ld/stride 会重算。
description 等于 edge 的 name。

PF 先物化每个 perf.row。`sweep=True` 时再按
`64,128,256,512,1024,2048,4096` 附加方阵，超预算后停止。

PF 与 PW 即使出现相同的性能键也都保留。PW 证明组合覆盖，PF 承载独立性能期望集、
基线键与 msprof 入口。
合并会让精度过滤或性能证据缺行。

## 确定性 pairwise

轴和取值均保持声明顺序。算法先构造所有轴对与值对，再重复以最早的
未覆盖对作种子。其余轴优先选能覆盖最多未覆盖对的值，平局选先声明的值。

候选行不合法时按轴顺序回溯，每个种子最多试 2000 个完整候选。穷举完整空间仍找不到
合法行才记为 `pairs_infeasible`；到达搜索预算却未判定的直接失败（`GeneratorError`），
不冒充不可行。实现只以整数索引排序，不依赖 set 迭代顺序。

## report

`generate(facts)["report"]` 含以下字段：

| 字段 | 含义 |
| --- | --- |
| `axes` | 每个轴的名称与取值数 |
| `pairs_total` | 所有必覆盖值对数 |
| `pairs_covered` | 已被合法 PW 行覆盖的值对数 |
| `pairs_infeasible` | 穷举证明无合法行的值对明细 |
| `rows_dropped` | constraints 或 footprint 丢弃的候选数 |
| `blocks` | L0/PW/ED/PF 各块行数 |

## 包级校验

CSV 存在时，`package.py check` 执行以下机械门。核心是一条通用规则：**五个派生文件必须与
从同一 FACTS 重新渲染的结果逐字节一致**——派生物对可信渲染器的任何字节偏离都被抓住；生成器
输出的语义正确性另由 schema 与生成器内的几条不变量保证，不在此重复逐文件校验。

- `gen_csv.py` 通用代码区 SHA-256 与模板一致；FACTS 区只允许字面量赋值。
- 同一 FACTS 两次 `generate` 结果一致。
- `<op>_test.csv`、`README.md`、`verify_accuracy.py`、`verify_performance.py`、
  `gpu_baseline.csv` 各自与从 FACTS 重新渲染的结果逐字节一致。
- generate 输出的 header 与 package.py 投影一致；L0 至少一行，存在可变轴时 PW 至少一行。

全部通过后打印覆盖数、`infeasible` 与 `rows_dropped`。
